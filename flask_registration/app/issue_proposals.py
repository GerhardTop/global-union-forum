"""
Herontwerp, bouwstap 4: voorstellen als onderdeel van een vraagstuk.

Een voorstel (IssueProposal) hoort bij één vraagstuk, met een stelling (één
zin), een toelichting en een inbrenger. Het heeft geen eigen gesprek: de
gedeelde discussie is die van het vraagstuk. Meerdere voorstellen per
vraagstuk.

- Voorstellen doen en steunen: zolang het vraagstuk in de feitenfase of de
  bijstelfase is en niet gesloten. Steunen kan één keer per persoon, niet
  het eigen voorstel, en alleen zolang het voorstel nog steun verzamelt.
- Status: "verzamelt steun (x van n)" tot de steundrempel van het vraagstuk
  (support_threshold) is gehaald, daarna "op de stemlijst".
- Bijstellen: de inbrenger, in de bijstelfase van het vraagstuk, één keer;
  de oorspronkelijke stelling en toelichting blijven bewaard.

De fase van het vraagstuk wordt hier alleen gelezen; de fase-engine en de
tik veranderen niet (stap 6/7). Het oude voorstel (Proposal, een voorstel is
een gesprek) blijft ernaast werken tot stap 7.
"""
from datetime import datetime

from sqlalchemy import func

from app import db
from app.models import IssueProposal, IssueProposalSupport

STATEMENT_MAX = 300
EXPLANATION_MAX = 5000
OPEN_PHASES = ('feiten', 'bijstellen')
FILTERS = ('alle', 'stemlijst', 'steun')
ORDERS = ('steun', 'nieuwste', 'stemlijst')
DEFAULT_ORDER = 'steun'


def _is_contributor(user):
    return getattr(user, 'is_authenticated', False) and user.can_contribute()


def _open(issue):
    return not issue.is_closed and issue.phase in OPEN_PHASES


# ── Voorstel doen ────────────────────────────────────────────────────────

def can_propose(issue, user):
    return _is_contributor(user) and _open(issue)


def parse_form(form):
    """(stelling, toelichting, fouten) uit het voorstelformulier."""
    statement = ' '.join((form.get('statement') or '').split())
    explanation = (form.get('explanation') or '').strip()
    errors = {}
    if not statement or len(statement) > STATEMENT_MAX:
        errors['statement'] = True
    if len(explanation) > EXPLANATION_MAX:
        errors['explanation'] = True
    return statement, explanation or None, errors


def create(issue, user, statement, explanation=None, lang=None):
    """Commit niet zelf."""
    proposal = IssueProposal(issue_id=issue.id, statement=statement, explanation=explanation,
                             created_by_id=user.id, lang=lang)
    db.session.add(proposal)
    return proposal


# ── Steun en status ──────────────────────────────────────────────────────

def support_counts(proposal_ids):
    proposal_ids = list(proposal_ids)
    if not proposal_ids:
        return {}
    rows = (db.session.query(IssueProposalSupport.proposal_id, func.count(IssueProposalSupport.id))
            .filter(IssueProposalSupport.proposal_id.in_(proposal_ids))
            .group_by(IssueProposalSupport.proposal_id))
    return {pid: int(n) for pid, n in rows}


def my_supports(proposal_ids, user):
    proposal_ids = list(proposal_ids)
    if not proposal_ids or not getattr(user, 'is_authenticated', False):
        return set()
    return {pid for (pid,) in db.session.query(IssueProposalSupport.proposal_id)
            .filter(IssueProposalSupport.proposal_id.in_(proposal_ids), IssueProposalSupport.user_id == user.id)}


def on_list(proposal, count):
    return count >= proposal.issue.support_threshold


def status(proposal, count):
    """('stemlijst', None) of ('steun', (aantal, drempel)); de template maakt er tekst van."""
    if on_list(proposal, count):
        return 'stemlijst', None
    return 'steun', (count, proposal.issue.support_threshold)


def can_support(proposal, user, count, supported=False):
    return (_is_contributor(user) and proposal.created_by_id != user.id and not supported
            and _open(proposal.issue) and not on_list(proposal, count))


def support(proposal, user):
    """Commit niet zelf."""
    db.session.add(IssueProposalSupport(proposal_id=proposal.id, user_id=user.id))


# ── Bijstellen ───────────────────────────────────────────────────────────

def can_revise(proposal, user):
    issue = proposal.issue
    return (getattr(user, 'is_authenticated', False) and proposal.created_by_id == user.id
            and proposal.revised_at is None and not issue.is_closed and issue.phase == 'bijstellen')


def revise(proposal, statement, explanation, now=None):
    """Eén keer verfijnen; de oorspronkelijke versie blijft bewaard. Commit niet zelf."""
    proposal.original_statement = proposal.statement
    proposal.original_explanation = proposal.explanation
    proposal.statement, proposal.explanation = statement, explanation
    proposal.revised_at = now or datetime.utcnow()


# ── Lijst ────────────────────────────────────────────────────────────────

def listing(issue, which='alle', order=DEFAULT_ORDER):
    """[(voorstel, aantal steunbetuigingen)], gefilterd en gesorteerd."""
    proposals = IssueProposal.query.filter_by(issue_id=issue.id).all()
    counts = support_counts(p.id for p in proposals)
    rows = [(p, counts.get(p.id, 0)) for p in proposals]
    if which == 'stemlijst':
        rows = [r for r in rows if on_list(*r)]
    elif which == 'steun':
        rows = [r for r in rows if not on_list(*r)]
    newest = lambda r: (r[0].created_at or datetime.min, r[0].id)  # noqa: E731
    if order == 'nieuwste':
        key = newest
    elif order == 'stemlijst':
        key = lambda r: (on_list(*r), r[1]) + newest(r)  # noqa: E731
    else:
        key = lambda r: (r[1],) + newest(r)  # noqa: E731
    return sorted(rows, key=key, reverse=True)


# ── Opruimen ─────────────────────────────────────────────────────────────

def detach_user(user_id):
    """Vóór het verwijderen van een gebruiker: zijn steun weg, zijn naam bij
    zijn voorstellen los (het voorstel blijft bij het vraagstuk)."""
    IssueProposalSupport.query.filter_by(user_id=user_id).delete(synchronize_session='evaluate')
    IssueProposal.query.filter_by(created_by_id=user_id).update({IssueProposal.created_by_id: None},
                                                                 synchronize_session=False)
