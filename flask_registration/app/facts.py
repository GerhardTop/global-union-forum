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
from app.models import (FACT_DIRECTIONS, FACT_DISPUTE_REASONS, FACT_KIND, FactDispute, FactUsefulVote, Issue,
                        Post, QualityAward)

ORDERS = ('kwaliteit', 'nieuwste')
DEFAULT_ORDER = 'kwaliteit'
PAGE_SIZE = 15  # polijststap P7: ongeveer 15, de rest in stappen
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


def dispute(fact, user, reason, note=None):
    """Een nieuw bezwaar. Commit niet zelf."""
    row = FactDispute(fact_post_id=fact.id, user_id=user.id, reason=reason, note=note, status='open')
    db.session.add(row)
    return row


def can_resolve(row, user):
    """Een moderator, niet bij een feit van zichzelf (zoals bij de handhaving)."""
    return (getattr(user, 'is_authenticated', False) and user.can_moderate()
            and row.fact is not None and row.fact.user_id != user.id)


def resolve(row, user, now=None):
    row.status = 'resolved'
    row.resolved_by_id = user.id
    row.resolved_at = now or datetime.utcnow()


def disputed_ids(fact_ids):
    """Feiten met een openstaand bezwaar (het label voor moderators)."""
    fact_ids = list(fact_ids)
    if not fact_ids:
        return set()
    return {fid for (fid,) in db.session.query(FactDispute.fact_post_id)
            .filter(FactDispute.fact_post_id.in_(fact_ids), FactDispute.status == 'open').distinct()}


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
    """Waar een feit staat: op de feitenpagina van zijn vraagstuk, anders in
    het feitenblok van het gesprek (een voorstel zonder vraagstuk)."""
    from flask import url_for
    issue = issue_for_thread(fact.thread_id)
    if issue is not None:
        return url_for('issues.facts', issue_id=issue.id) + f'#post-{fact.id}'
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


def thread_facts(thread_id):
    return Post.query.filter_by(thread_id=thread_id, kind=FACT_KIND).order_by(Post.created_at, Post.id).all()


# ── Opruimen ─────────────────────────────────────────────────────────────

def delete_for_posts(post_ids):
    """Vóór het verwijderen van bijdragen: (oude) nuttig-rijen en bezwaren bij die feiten weg."""
    post_ids = list(post_ids)
    if post_ids:
        from app import issue_proposals
        issue_proposals.delete_links_for_facts(post_ids)  # stap 5: koppelingen aan voorstellen
        FactUsefulVote.query.filter(FactUsefulVote.fact_post_id.in_(post_ids)).delete(synchronize_session='evaluate')
        FactDispute.query.filter(FactDispute.fact_post_id.in_(post_ids)).delete(synchronize_session='evaluate')


def detach_user(user_id):
    """Vóór het verwijderen van een gebruiker: zijn (oude) nuttig-signalen weg,
    zijn naam bij bezwaren los (het bezwaar zelf blijft voor de moderator)."""
    FactUsefulVote.query.filter_by(user_id=user_id).delete(synchronize_session='evaluate')
    FactDispute.query.filter_by(user_id=user_id).update({FactDispute.user_id: None}, synchronize_session=False)
    FactDispute.query.filter_by(resolved_by_id=user_id).update({FactDispute.resolved_by_id: None},
                                                               synchronize_session=False)
