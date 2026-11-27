"""
Herontwerp, bouwstap 3: feiten en onderzoek.

Een feit is een Post met kind 'feit', een richting en een bron, in het
gesprek van een vraagstuk (of van een voorstel). Deze module voegt toe:

- Betwisten: een reden uit een vaste lijst plus een optionele toelichting
  (FactDispute). Komt in de moderatorweergave; het feit blijft gewoon staan.
  Per gebruiker per feit één openstaand bezwaar.
- De feitenlijst van een vraagstuk: filteren op richting en sorteren
  (kwaliteit eerst, nieuwste).

Polijstronde P2: het "nuttig"-signaal is vervangen door het AI-
kwaliteitsmerk van een feit (QualityAward.fact_mark, zie app/quality.py).
De tabel fact_useful_votes blijft staan maar wordt niet meer gebruikt; bij
het opruimen gaan oude rijen nog wel weg. "Kwaliteit eerst": eerst feiten
met het merk, dan feiten met een bron die een link is, daarna de nieuwste.
Een feit kan media zijn (fact_type 'media': nieuws, podcast, video).
"""
from datetime import datetime

from app import db
from app.models import FACT_DIRECTIONS, FACT_DISPUTE_REASONS, FactDispute, FactUsefulVote, Issue, QualityAward

ORDERS = ('kwaliteit', 'nieuwste')
DEFAULT_ORDER = 'kwaliteit'
NOTE_MAX = 500


# ── Waar hoort een feit bij ──────────────────────────────────────────────

def issue_for_thread(thread_id):
    return Issue.query.filter_by(thread_id=thread_id).first() if thread_id is not None else None


def issue_phase_open(issue, now=None):
    """Feiten toevoegen bij een vraagstuk zonder voorstel: in de feitenfase en
    vóór de deadline. Alleen lezen; de fase-engine verandert hier niets."""
    if issue is None or issue.is_closed or issue.phase != 'feiten':
        return False
    from app import issues
    if not issues.is_open_now(issue, now):  # polijstronde P4: het platform is dicht
        return False
    return issue.phase_deadline is None or (now or datetime.utcnow()) < issue.phase_deadline


# ── Betwisten ────────────────────────────────────────────────────────────

def can_dispute(fact, user):
    return (getattr(user, 'is_authenticated', False) and user.can_contribute()
            and fact.user_id != user.id)


def parse_dispute(form):
    """(reden, toelichting, fouten) uit het betwistformulier."""
    reason = form.get('reason') or ''
    note = (form.get('note') or '').strip()
    errors = {}
    if reason not in FACT_DISPUTE_REASONS:
        errors['reason'] = True
    if len(note) > NOTE_MAX:
        errors['note'] = True
    return reason, note or None, errors


def has_open_dispute(fact, user):
    return FactDispute.query.filter_by(fact_post_id=fact.id, user_id=user.id, status='open').first() is not None


def dispute(fact, user, reason, note=None, lang=None):
    """Een nieuw bezwaar. Commit niet zelf. lang: de taal van de toelichting
    (op verzoek vertaalbaar voor de moderator)."""
    row = FactDispute(fact_post_id=fact.id, user_id=user.id, reason=reason, note=note, status='open', lang=lang)
    db.session.add(row)
    # Notifications step 3a-1: the fact's author hears it is challenged, senior
    # moderators that there is a challenged fact. Never the disputer.
    from app import notifications
    from app.models import User
    if fact.user_id is not None:
        notifications.notify(db.session.get(User, fact.user_id), 'feit', post_id=fact.id,
                             thread_id=fact.thread_id, actor_id=user.id)
    notifications.emit('betwist', notifications.senior_moderators(), post_id=fact.id, thread_id=fact.thread_id,
                       actor_id=user.id)
    return row


def can_resolve(row, user):
    """Een moderator, niet bij een feit van zichzelf (zoals bij de handhaving)."""
    return (getattr(user, 'is_authenticated', False) and user.can_moderate()
            and row.fact is not None and row.fact.user_id != user.id)


def resolve(row, user, now=None):
    """Commit niet zelf. The fact's author hears the dispute was resolved
    (a 'feit' notification pointing at this dispute)."""
    row.status = 'resolved'
    row.resolved_by_id = user.id
    row.resolved_at = now or datetime.utcnow()
    fact = row.fact
    if fact is not None and fact.user_id is not None:
        from app import notifications
        from app.models import User
        notifications.notify(db.session.get(User, fact.user_id), 'feit', post_id=fact.id,
                             thread_id=fact.thread_id, actor_id=user.id, dispute_id=row.id)


def my_disputed(fact_ids, user):
    fact_ids = list(fact_ids)
    if not fact_ids or not getattr(user, 'is_authenticated', False):
        return set()
    return {fid for (fid,) in db.session.query(FactDispute.fact_post_id)
            .filter(FactDispute.fact_post_id.in_(fact_ids), FactDispute.user_id == user.id,
                    FactDispute.status == 'open')}


def open_disputes():
    return FactDispute.query.filter_by(status='open').order_by(FactDispute.created_at, FactDispute.id).all()


def fact_url(fact):
    """Where a fact is: on its issue's page with the facts enlarged and the
    fact in view (page consolidation step 4), otherwise in the facts block of
    the conversation (a proposal without an issue)."""
    from flask import url_for
    issue = issue_for_thread(fact.thread_id)
    if issue is not None:
        return url_for('issues.show', issue_id=issue.id, vergroot='feiten') + f'#post-{fact.id}'
    return url_for('forum.forum_thread', thread_id=fact.thread_id) + f'#post-{fact.id}'


# ── Lijst en sorteren ────────────────────────────────────────────────────

def marked_ids(fact_ids):
    """Feiten met het kwaliteitsmerk (polijstronde P2)."""
    fact_ids = list(fact_ids)
    if not fact_ids:
        return set()
    return {pid for (pid,) in db.session.query(QualityAward.post_id)
            .filter(QualityAward.post_id.in_(fact_ids), QualityAward.fact_mark.is_(True))}


def sort_facts(facts, order, marked=frozenset()):
    """Sorteer een lijst feiten: kwaliteit eerst (merk, dan een link als
    bron, dan de nieuwste) of alleen de nieuwste."""
    newest = lambda f: (f.created_at or datetime.min, f.id)  # noqa: E731
    if order == 'nieuwste':
        return sorted(facts, key=newest, reverse=True)
    return sorted(facts, key=lambda f: (f.id in marked, bool(f.fact_source_is_url)) + newest(f), reverse=True)


def listing(facts, direction=None, order=DEFAULT_ORDER):
    """De gefilterde en gesorteerde feiten."""
    if direction in FACT_DIRECTIONS:
        facts = [f for f in facts if f.fact_direction == direction]
    order = order if order in ORDERS else DEFAULT_ORDER
    return sort_facts(facts, order, marked_ids(f.id for f in facts))


# ── Opruimen ─────────────────────────────────────────────────────────────

def delete_for_posts(post_ids):
    """Vóór het verwijderen van bijdragen: (oude) nuttig-rijen en bezwaren bij die feiten weg."""
    post_ids = list(post_ids)
    if post_ids:
        from app import issue_proposals
        issue_proposals.delete_links_for_facts(post_ids)  # stap 5: koppelingen aan voorstellen
        FactUsefulVote.query.filter(FactUsefulVote.fact_post_id.in_(post_ids)).delete(synchronize_session='evaluate')
        from app import content_i18n
        # Background figures: the snapshots, then the charts of these facts.
        from app.models import ChartSnapshot, FactChart
        chart_rows = (FactChart.query.filter(FactChart.post_id.in_(post_ids))
                      .with_entities(FactChart.id, FactChart.issue_id, FactChart.slug, FactChart.source).all())
        chart_ids = [row.id for row in chart_rows]
        if chart_ids:
            from app import background_figures
            for row in chart_rows:   # the memory: a removed chart does not come back
                background_figures.block_slug(row.issue_id, background_figures.memory_key(row.source, row.slug))
            snapshots = ChartSnapshot.query.filter(ChartSnapshot.chart_id.in_(chart_ids))
            content_i18n.delete_for('chart_snapshots', [row.id for row in snapshots.with_entities(ChartSnapshot.id)])
            snapshots.delete(synchronize_session='evaluate')
            content_i18n.delete_for('fact_charts', chart_ids)
            FactChart.query.filter(FactChart.id.in_(chart_ids)).delete(synchronize_session='evaluate')
        disputes = FactDispute.query.filter(FactDispute.fact_post_id.in_(post_ids))
        content_i18n.delete_for('fact_disputes', [row.id for row in disputes.with_entities(FactDispute.id)])
        disputes.delete(synchronize_session='evaluate')


def detach_user(user_id):
    """Vóór het verwijderen van een gebruiker: zijn (oude) nuttig-signalen weg,
    zijn naam bij bezwaren los (het bezwaar zelf blijft voor de moderator)."""
    FactUsefulVote.query.filter_by(user_id=user_id).delete(synchronize_session='evaluate')
    FactDispute.query.filter_by(user_id=user_id).update({FactDispute.user_id: None}, synchronize_session=False)
    FactDispute.query.filter_by(resolved_by_id=user_id).update({FactDispute.resolved_by_id: None},
                                                               synchronize_session=False)
