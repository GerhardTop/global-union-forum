"""
Fase D: handhaving van de gedragsregels.

De AI geeft in dezelfde aanroep als het kwaliteitsoordeel ook een
handhavingsoordeel (zie quality.judge_post). Dit module voert het uit en
bevat de handelingen van moderators:

- geen overtreding: niets.
- duidelijke overtreding: de bijdrage gaat uit het gesprek naar de openbare
  achtergrondpagina (/achtergrond), met de regel en de uitleg. Niets wordt
  verwijderd; de tekst wordt als momentopname bewaard.
- spam of grof misbruik: volledig verborgen, niet op de achtergrondpagina.
- twijfel: niets verbergen, alleen gemarkeerd voor een moderator.

De auteur en moderators blijven een verplaatste of verborgen bijdrage zien,
gemarkeerd met de reden. De auteur kan binnen zeven dagen één keer bezwaar
maken; een senior moderator (of admin) beslist, niet degene die het besluit
nam en niet de auteur.
"""
from datetime import datetime

from flask_babel import gettext

from app import db
from app.models import (CONDUCT_RULES, REMOVING_ACTIONS, ConductCase, ConductObjection, Post)

# Van het AI-schema naar de interne regelnaam.
AI_RULES = {'respect': 'respect', 'on_topic': 'onderwerp'}


def rule_label(rule):
    """De naam van een gedragsregel in de taal van de lezer (catalogus)."""
    if rule == 'respect':
        return gettext('About the idea, not the person')
    if rule == 'onderwerp':
        return gettext('On topic')
    return gettext('Spam or abuse')


# ── Zichtbaarheid ────────────────────────────────────────────────────────

def is_privileged(post, user):
    """De auteur en moderators zien een verplaatste of verborgen bijdrage nog."""
    if not getattr(user, 'is_authenticated', False):
        return False
    return post.user_id == user.id or user.can_moderate()


def removed_for(post, case, user):
    """Is deze bijdrage voor deze lezer uit het gesprek (verplaatst of verborgen)?"""
    return case is not None and case.removes_from_thread and not is_privileged(post, user)


def is_removed_for(post, user):
    return removed_for(post, post.conduct_case, user)


def cases_for(post_ids):
    post_ids = list(post_ids)
    if not post_ids:
        return {}
    return {c.post_id: c for c in ConductCase.query.filter(ConductCase.post_id.in_(post_ids))}


def removed_post_ids(user, thread_ids=None):
    """Ids van bijdragen die voor deze lezer uit het gesprek zijn."""
    q = (db.session.query(ConductCase.post_id, Post.user_id, Post.thread_id, Post.is_op)
         .join(Post, ConductCase.post_id == Post.id)
         .filter(ConductCase.action.in_(REMOVING_ACTIONS)))
    if thread_ids is not None:
        q = q.filter(Post.thread_id.in_(list(thread_ids)))
    if getattr(user, 'is_authenticated', False) and user.can_moderate():
        return set(), set()
    uid = user.id if getattr(user, 'is_authenticated', False) else None
    removed, hidden_threads = set(), set()
    for post_id, author_id, thread_id, is_op in q:
        if author_id is not None and author_id == uid:
            continue
        removed.add(post_id)
        if is_op:
            hidden_threads.add(thread_id)
    return removed, hidden_threads


# ── Handelingen ──────────────────────────────────────────────────────────

def _case(post):
    case = post.conduct_case
    if case is None:
        case = ConductCase(post_id=post.id, action='twijfel')
        db.session.add(case)
        post.conduct_case = case
    return case


def _decide_open_objection(case, decision, actor, now, note=None):
    obj = case.open_objection
    if obj is not None:
        obj.decision, obj.decided_by_id, obj.decided_at = decision, getattr(actor, 'id', None), now
        if note:
            obj.decision_note = note


def move(post, rule, explanation, lang, actor=None):
    """Verplaats een bijdrage naar de achtergrondpagina (actor None = de AI)."""
    if rule not in CONDUCT_RULES:
        raise ValueError(f'onbekende gedragsregel: {rule}')
    now = datetime.utcnow()
    case = _case(post)
    case.action, case.rule = 'verplaatst', rule
    case.source = 'ai' if actor is None else 'moderator'
    case.explanation, case.explanation_lang = explanation, lang
    case.body_snapshot = post.body
    case.on_background = True
    case.acted_at, case.acted_by_id = now, getattr(actor, 'id', None)
    case.confirmed_at = case.confirmed_by_id = None
    case.restored_at = case.restored_by_id = None
    return case


def hide(post, explanation, lang, actor=None):
    """Verberg spam of grof misbruik volledig (niet op de achtergrondpagina)."""
    now = datetime.utcnow()
    case = _case(post)
    case.action, case.rule = 'verborgen', None
    case.source = 'ai' if actor is None else 'moderator'
    case.explanation, case.explanation_lang = explanation, lang
    case.acted_at, case.acted_by_id = now, getattr(actor, 'id', None)
    case.confirmed_at = case.confirmed_by_id = None
    case.restored_at = case.restored_by_id = None
    return case


def flag_doubt(post, rule, explanation, lang):
    """Twijfel: niets verbergen, alleen markeren voor een moderator."""
    case = _case(post)
    case.action = 'twijfel'
    case.rule = rule if rule in CONDUCT_RULES else None
    case.source = 'ai'
    case.explanation, case.explanation_lang = explanation, lang
    return case


def restore(case, actor, note=None):
    """Terug in het gesprek. Een verplaatste bijdrage blijft op de
    achtergrondpagina staan, met de status teruggeplaatst."""
    now = datetime.utcnow()
    _decide_open_objection(case, 'terugplaatsen', actor, now, note)
    case.action = 'teruggeplaatst'
    case.restored_by_id, case.restored_at = actor.id, now
    return case


def release(case, actor):
    """Twijfel opgelost: geen overtreding, de bijdrage blijft gewoon staan."""
    case.action = 'vrijgegeven'
    case.confirmed_by_id, case.confirmed_at = actor.id, datetime.utcnow()
    return case


def confirm(case, actor, note=None):
    """Besluit gehandhaafd na een bezwaar."""
    now = datetime.utcnow()
    _decide_open_objection(case, 'handhaven', actor, now, note)
    case.confirmed_by_id, case.confirmed_at = actor.id, now
    return case


def can_decide_objection(case, user):
    """Bij voorkeur een senior moderator: senior moderators en de admin, niet
    de auteur en niet de moderator die het besluit nam."""
    return (getattr(user, 'is_authenticated', False) and user.can_manage_rules()
            and case.post.user_id != user.id and case.acted_by_id != user.id)


def object_to(case, author, text):
    obj = ConductObjection(case_id=case.id, author_id=author.id, text=text)
    db.session.add(obj)
    return obj


# ── Het AI-oordeel uitvoeren ─────────────────────────────────────────────

def apply_ai_verdict(post, verdict, ai_rule, explanation, lang):
    """Voer het handhavingsoordeel van de AI uit. Terughoudend: een
    overtreding zonder herkenbare regel wordt twijfel."""
    rule = AI_RULES.get(ai_rule)
    if verdict == 'violation':
        return move(post, rule, explanation, lang) if rule else flag_doubt(post, None, explanation, lang)
    if verdict == 'spam':
        return hide(post, explanation, lang)
    if verdict == 'doubt':
        return flag_doubt(post, rule, explanation, lang)
    return None


# ── Achtergrondpagina ────────────────────────────────────────────────────

def background_cases():
    """Verplaatste (en later teruggeplaatste) bijdragen, nieuwste eerst.
    Nooit spam of misbruik, nooit open twijfel, en niet als de auteur zijn
    bijdrage zelf verwijderde."""
    return (ConductCase.query.join(Post, ConductCase.post_id == Post.id)
            .filter(ConductCase.on_background.is_(True),
                    ConductCase.action.in_(('verplaatst', 'teruggeplaatst')),
                    Post.body != '[bericht verwijderd]')
            .order_by(ConductCase.acted_at.desc(), ConductCase.id.desc()).all())
