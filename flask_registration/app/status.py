"""
Forum fase C2: verdiende status op basis van kwaliteit.

Een kwaliteitsbijdrage is een eigen bijdrage (hoofdbericht of reactie) met
een kwaliteitsoordeel dat NU de status 'toegekend' heeft. Een ingetrokken
oordeel telt dus niet. "Onderwerpen" zijn de gesprekken (threads) waarin die
bijdragen staan: de thread is in dit forum het onderwerp (gedragsregel "Bij
het onderwerp" = "reageer op de vraag van het gesprek"); er bestaat geen
ander onderwerpveld dat gesprekken van gebruikers onderscheidt.

Ladder: reader, contributor (>= 1), voter (>= voter_min), moderator
(> moderator_min en >= moderator_min_topics onderwerpen), senior
(> senior_min). De drempels stelt de admin in (AppSetting); de defaults zijn
alleen de beginwaarde.

Promotie gaat alleen omhoog: herberekenen verlaagt de opgeslagen status
nooit (ook niet na een ingetrokken oordeel of hogere drempels); omlaag is
een handmatige actie. De verdiende status geeft geen moderatiemacht en raakt
de handmatige rol (is_admin, is_moderator, role) niet.
"""
from datetime import datetime, timedelta

from sqlalchemy import func

from app import db
from app.models import (STATUS_LADDER, AppSetting, Post, QualityAward, QualityPromotion,
                        User, status_rank)

DEFAULT_THRESHOLDS = {
    'voter_min': 3,
    'moderator_min': 10,
    'moderator_min_topics': 3,
    'senior_min': 100,
}

THRESHOLD_LABELS = {
    'voter_min': {'nl': 'Stemmer: minimaal aantal kwaliteitsbijdragen',
                  'en': 'Voter: minimum number of quality contributions'},
    'moderator_min': {'nl': 'Moderator: meer dan dit aantal kwaliteitsbijdragen',
                      'en': 'Moderator: more than this number of quality contributions'},
    'moderator_min_topics': {'nl': 'Moderator: verdeeld over minimaal dit aantal onderwerpen',
                             'en': 'Moderator: spread over at least this number of topics'},
    'senior_min': {'nl': 'Senior: meer dan dit aantal kwaliteitsbijdragen',
                   'en': 'Senior: more than this number of quality contributions'},
}

STATUS_LABELS = {
    'reader': {'nl': 'Lezer', 'en': 'Reader'},
    'contributor': {'nl': 'Bijdrager', 'en': 'Contributor'},
    'voter': {'nl': 'Stemmer', 'en': 'Voter'},
    'moderator': {'nl': 'Moderator', 'en': 'Moderator'},
    'senior': {'nl': 'Senior', 'en': 'Senior'},
}


# ── Drempels ─────────────────────────────────────────────────────────────

def get_thresholds():
    """De huidige drempels: opgeslagen waarden, anders de defaults."""
    stored = {s.key: s.value for s in AppSetting.query.filter(AppSetting.key.in_(DEFAULT_THRESHOLDS))}
    return {k: stored.get(k, v) for k, v in DEFAULT_THRESHOLDS.items()}


def set_thresholds(values, user_id=None):
    """Sla drempels op (gelden vanaf nu; geen herberekening van iedereen)."""
    for key, value in values.items():
        if key not in DEFAULT_THRESHOLDS:
            raise ValueError(f'onbekende drempel: {key}')
        row = db.session.get(AppSetting, key)
        if row is None:
            db.session.add(AppSetting(key=key, value=int(value), updated_by_id=user_id))
        else:
            row.value, row.updated_by_id = int(value), user_id
    db.session.commit()


# ── Tellen en niveau bepalen ─────────────────────────────────────────────

def quality_counts(user_id):
    """(aantal toegekende kwaliteitsbijdragen, aantal onderwerpen) van een gebruiker.
    Een bijdrage die wegens de gedragsregels uit het gesprek is (fase D), telt niet."""
    from app.models import REMOVING_ACTIONS, ConductCase
    removed = db.session.query(ConductCase.post_id).filter(ConductCase.action.in_(REMOVING_ACTIONS))
    row = (db.session.query(func.count(func.distinct(Post.id)), func.count(func.distinct(Post.thread_id)))
           .join(QualityAward, QualityAward.post_id == Post.id)
           .filter(Post.user_id == user_id, QualityAward.status == 'toegekend',
                   Post.id.notin_(removed)).one())
    return int(row[0] or 0), int(row[1] or 0)


def level_for(count, topics, thresholds):
    """Het hoogste niveau waarvoor deze aantallen voldoen."""
    level = 'reader'
    if count >= 1:
        level = 'contributor'
    if count >= thresholds['voter_min']:
        level = 'voter'
    if count > thresholds['moderator_min'] and topics >= thresholds['moderator_min_topics']:
        level = 'moderator'
    if count > thresholds['senior_min']:
        level = 'senior'
    return level


def recalc_user(user_id):
    """Herbereken de verdiende status. Alleen omhoog; elk nieuw bereikt
    niveau krijgt een regel in het promotielog. Geeft de (eventueel nieuwe)
    status terug. Commit zelf."""
    if user_id is None:
        return None
    user = db.session.get(User, user_id)
    if user is None:
        return None
    count, topics = quality_counts(user_id)
    computed = level_for(count, topics, get_thresholds())
    current = user.earned_status
    if status_rank(computed) <= status_rank(current):
        return current
    now = datetime.utcnow()
    for level in STATUS_LADDER[status_rank(current) + 1: status_rank(computed) + 1]:
        db.session.add(QualityPromotion(user_id=user_id, level=level, promoted_at=now))
    user.quality_status = computed
    db.session.commit()
    return computed


def recalc_for_post(post):
    """Na het schrijven of wijzigen van een kwaliteitsoordeel: de auteur."""
    if post is not None and post.user_id is not None:
        return recalc_user(post.user_id)
    return None


# ── Eenmalige melding bij een promotie ───────────────────────────────────

def pending_promotion(user):
    """De recentste ongeziene promotie naar voter of hoger, of None."""
    if not getattr(user, 'is_authenticated', False):
        return None
    return (QualityPromotion.query
            .filter(QualityPromotion.user_id == user.id, QualityPromotion.seen_at.is_(None),
                    QualityPromotion.level.in_(STATUS_LADDER[status_rank('voter'):]))
            .order_by(QualityPromotion.promoted_at.desc(), QualityPromotion.id.desc()).first())


def mark_promotions_seen(user_id):
    QualityPromotion.query.filter_by(user_id=user_id, seen_at=None).update(
        {QualityPromotion.seen_at: datetime.utcnow()}, synchronize_session=False)
    db.session.commit()


# ── Taal-monitor (tegen stijl-bias van de AI) ────────────────────────────

PERIODS = {'7': 7, '30': 30, 'alles': None}


def language_monitor(period='30'):
    """Per taal (source_lang van de bijdrage): hoeveel bijdragen de AI
    beoordeelde, hoeveel hij toekende (zijn eigen oordeel, ai_criterion, los
    van latere aanpassingen door moderators) en het percentage. Plus het
    verschil nl min en in procentpunten (None als een van beide geen data heeft)."""
    q = (db.session.query(Post.source_lang, func.count(QualityAward.id),
                          func.sum(db.case((QualityAward.ai_criterion.isnot(None), 1), else_=0)))
         .join(Post, QualityAward.post_id == Post.id)
         .filter(QualityAward.source == 'ai'))
    days = PERIODS.get(period, PERIODS['30'])
    if days is not None:
        q = q.filter(QualityAward.created_at >= datetime.utcnow() - timedelta(days=days))
    rows = {lang: (int(n), int(awarded or 0)) for lang, n, awarded in q.group_by(Post.source_lang).all()}
    result = {}
    for lang in ('nl', 'en'):
        judged, awarded = rows.get(lang, (0, 0))
        result[lang] = {'judged': judged, 'awarded': awarded,
                        'pct': round(100.0 * awarded / judged, 1) if judged else None}
    nl, en = result['nl']['pct'], result['en']['pct']
    result['difference'] = round(nl - en, 1) if nl is not None and en is not None else None
    return result


def monitor_sample(lang=None, limit=50):
    """De recentste AI-oordelen (voor handmatige controle), filterbaar op taal."""
    q = (QualityAward.query.join(Post, QualityAward.post_id == Post.id)
         .filter(QualityAward.source == 'ai'))
    if lang in ('nl', 'en'):
        q = q.filter(Post.source_lang == lang)
    return q.order_by(QualityAward.created_at.desc(), QualityAward.id.desc()).limit(limit).all()
