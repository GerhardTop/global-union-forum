"""
Herontwerp, bouwstap 4: voorstellen als onderdeel van een vraagstuk.

Een voorstel (IssueProposal) hoort bij één vraagstuk, met een stelling (één
zin), een toelichting en een inbrenger. Het heeft geen eigen gesprek: de
gedeelde discussie is die van het vraagstuk. Meerdere voorstellen per
vraagstuk.

- Voorstellen doen en steunen: alleen in de feitenfase van het vraagstuk en
  niet als het gesloten is. Aan het eind van de feitenfase ligt de
  stemlijst dus vast; de bijstelfase is alleen voor het bijstellen van
  bestaande voorstellen. Steunen kan één keer per persoon, niet het eigen
  voorstel, en alleen zolang het voorstel nog steun verzamelt.
- Status: "verzamelt steun (x van n)" tot de steundrempel van het vraagstuk
  (support_threshold) is gehaald, daarna "op de stemlijst".
- Bijstellen: de inbrenger, in de bijstelfase van het vraagstuk;
  polijstronde P5: vaker dan eens, met elke versie bewaard
  (IssueProposalVersion) en reacties gekoppeld aan de versie waarop ze slaan.

Stap 5, feiten koppelen: iedereen die mag bijdragen koppelt feiten uit de
gedeelde lijst van het vraagstuk aan een voorstel, ook feiten die ertegen
pleiten (de richting blijft zichtbaar). Ontkoppelen: wie het koppelde of een
moderator. Beide in de feitenfase en de bijstelfase, niet als het vraagstuk
gesloten is. Koppelen, niet kopiëren: het feit blijft één Post.

Stap 6, stemmen en uitslag:
- Een gewoon voorstel op de stemlijst krijgt stemmen (voor, tegen,
  onthouding) van stemgerechtigden (User.can_vote) zolang het vraagstuk in
  de stemfase is en niet gesloten. Eén stem per persoon, te wijzigen zolang
  het stemmen loopt. De uitslag (na de stemfase): aangenomen als het
  percentage voor van de geldige stemmen (voor plus tegen) groter is dan
  proposal_threshold_pct; onthoudingen tellen mee in de opkomst, niet in de
  uitslag. De tussenstand is zichtbaar tijdens het stemmen als show_interim
  aanstaat, anders pas na afloop.
- Het samenvattende voorstel (als final_vote aanstaat): een IssueProposal met
  is_final, gemaakt door de initiatiefnemer in de eigen fase samenvatten
  (polijstronde P6; daarvoor in de vervolgfase), één per
  vraagstuk, zonder steunronde. De stemming erover loopt days_vote dagen
  vanaf het aanmaken, met dezelfde stemregels en drempel. Daarbij kan de
  initiatiefnemer de vraag en de toelichting van het vraagstuk aanpassen.

De fase van het vraagstuk wordt hier alleen gelezen; de fase-engine en de
tik veranderen niet (stap 6/7). Het oude voorstel (Proposal, een voorstel is
een gesprek) blijft ernaast werken tot stap 7.
"""
from datetime import datetime, timedelta

from sqlalchemy import func, or_

from app import db
from types import SimpleNamespace

from app.models import (FACT_KIND, VOTE_CHOICES, IssueProposal, IssueProposalFact,
                        IssueProposalSupport, IssueProposalVersion, IssueProposalVote, Post)

STATEMENT_MAX = 300
EXPLANATION_MAX = 5000
# Voorstellen doen en steunen: alleen in de feitenfase (de stemlijst ligt daarna vast).
OPEN_PHASES = ('feiten',)
FILTERS = ('alle', 'stemlijst', 'steun')
ORDERS = ('steun', 'nieuwste', 'stemlijst')
DEFAULT_ORDER = 'steun'
# Feiten koppelen en ontkoppelen: in de feitenfase en de bijstelfase.
LINK_PHASES = ('feiten', 'bijstellen')


def _is_contributor(user):
    return getattr(user, 'is_authenticated', False) and user.can_contribute()


def _open(issue):
    from app import issues
    return not issue.is_closed and issue.phase in OPEN_PHASES and issues.is_open_now(issue)


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
    """Commit niet zelf. Versie 1 komt erbij (polijstronde P5)."""
    proposal = IssueProposal(issue_id=issue.id, statement=statement, explanation=explanation,
                             created_by_id=user.id, lang=lang, created_at=datetime.utcnow())
    proposal.version_rows.append(IssueProposalVersion(number=1, statement=statement, explanation=explanation,
                                                      created_at=proposal.created_at))
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
    """Op de stemlijst: genoeg steun. Het samenvattende voorstel staat er altijd op."""
    return bool(proposal.is_final) or count >= proposal.issue.support_threshold


def status(proposal, count):
    """(sleutel, info) voor het statuslabel; de template maakt er tekst van.
    Gewoon voorstel: 'steun' (aantal, drempel) in de feitenfase, daarna
    'niet_op_lijst' (aantal, drempel) of 'stemlijst'; na het stemmen de
    uitslag 'aangenomen' of 'verworpen' (met de uitslag). Samenvattend voorstel:
    'ultiem_stemmen' (einddatum) tijdens de stemming, daarna de uitslag."""
    if on_list(proposal, count):
        if voting_over(proposal):
            res = result(proposal)
            return ('aangenomen' if res['adopted'] else 'verworpen'), res
        if proposal.is_final:
            return 'ultiem_stemmen', final_voting_end(proposal)
        return 'stemlijst', None
    if proposal.issue.phase == 'feiten' and not proposal.issue.is_closed:
        return 'steun', (count, proposal.issue.support_threshold)
    return 'niet_op_lijst', (count, proposal.issue.support_threshold)


def can_support(proposal, user, count, supported=False):
    return (not proposal.is_final and _is_contributor(user) and proposal.created_by_id != user.id and not supported
            and _open(proposal.issue) and not on_list(proposal, count))


def support(proposal, user):
    """Commit niet zelf."""
    db.session.add(IssueProposalSupport(proposal_id=proposal.id, user_id=user.id))


# ── Bijstellen en versies (polijstronde P5) ──────────────────────────────

def can_revise(proposal, user):
    """De inbrenger, in de bijstelfase, zo vaak als nodig."""
    issue = proposal.issue
    return (not proposal.is_final and getattr(user, 'is_authenticated', False) and proposal.created_by_id == user.id
            and not issue.is_closed and issue.phase == 'bijstellen')


def versions(proposal):
    """De versies, oudste eerst: de rijen, of voor een voorstel van vóór P5
    afgeleid (het origineel en, na de ene bijstelling, de huidige tekst)."""
    if proposal.version_rows:
        return list(proposal.version_rows)
    first = SimpleNamespace(number=1, created_at=proposal.created_at,
                            statement=proposal.original_statement or proposal.statement,
                            explanation=(proposal.original_explanation if proposal.revised_at
                                         else proposal.explanation))
    if not proposal.revised_at:
        return [first]
    return [first, SimpleNamespace(number=2, created_at=proposal.revised_at, statement=proposal.statement,
                                   explanation=proposal.explanation)]


def current_version(proposal):
    return len(versions(proposal))


def revise(proposal, statement, explanation, now=None):
    """Een nieuwe versie; alle eerdere blijven bewaard. Commit niet zelf."""
    now = now or datetime.utcnow()
    if not proposal.version_rows:  # een voorstel van vóór P5: eerst de afgeleide versies vastleggen
        for version in versions(proposal):
            proposal.version_rows.append(IssueProposalVersion(
                number=version.number, statement=version.statement, explanation=version.explanation,
                created_at=version.created_at))
    if proposal.original_statement is None:
        proposal.original_statement = proposal.statement
        proposal.original_explanation = proposal.explanation
    proposal.statement, proposal.explanation = statement, explanation
    proposal.revised_at = now
    proposal.version_rows.append(IssueProposalVersion(number=len(proposal.version_rows) + 1, statement=statement,
                                                      explanation=explanation, created_at=now))


# ── Reacties bij een voorstel (polijstronde P5) ──────────────────────────

def reactions(proposal):
    """De reacties die bij dit voorstel zijn geplaatst (in de gedeelde
    discussie van het vraagstuk), oudste eerst."""
    return (Post.query.filter_by(issue_proposal_id=proposal.id)
            .order_by(Post.created_at, Post.id).all())


def reactions_for(proposal_ids):
    """Alle reacties bij deze voorstellen in één query (voor de tellers in de lijsten)."""
    return Post.query.filter(Post.issue_proposal_id.in_(list(proposal_ids))).all()


def proposals_by_id(ids):
    ids = {i for i in ids if i is not None}
    return {p.id: p for p in IssueProposal.query.filter(IssueProposal.id.in_(ids))} if ids else {}


# ── Feiten koppelen (stap 5) ─────────────────────────────────────────────

def _link_open(issue):
    return not issue.is_closed and issue.phase in LINK_PHASES


def can_link(proposal, user):
    return not proposal.is_final and _is_contributor(user) and _link_open(proposal.issue)


def can_unlink(link, user):
    return (getattr(user, 'is_authenticated', False) and _link_open(link.proposal.issue)
            and (link.linked_by_id == user.id or user.can_moderate()))


def visible_issue_facts(issue, user):
    """De feiten van het vraagstuk die deze lezer ziet (een verplaatst of
    verborgen feit is weg voor anderen, zoals in het gesprek), oudste eerst."""
    from app import conduct
    if issue.thread_id is None:
        return []
    facts = (Post.query.filter_by(thread_id=issue.thread_id, kind=FACT_KIND)
             .order_by(Post.created_at, Post.id).all())
    cases = conduct.cases_for([f.id for f in facts])
    return [f for f in facts if f.id not in cases or not conduct.removed_for(f, cases[f.id], user)]


def linked(proposal, user):
    """[(koppeling, feit)] die deze lezer ziet, in de volgorde van koppelen."""
    visible = {f.id for f in visible_issue_facts(proposal.issue, user)}
    links = sorted(proposal.fact_links, key=lambda link: (link.created_at or datetime.min, link.id))
    return [(link, link.fact) for link in links if link.fact_post_id in visible]


def link(proposal, fact, user):
    """Koppel een feit van hetzelfde vraagstuk. Geeft de koppeling, of None
    als het geen feit van dit vraagstuk is of al gekoppeld is. Commit niet zelf."""
    if fact is None or not fact.is_fact or fact.thread_id != proposal.issue.thread_id:
        return None
    if IssueProposalFact.query.filter_by(proposal_id=proposal.id, fact_post_id=fact.id).first() is not None:
        return None
    row = IssueProposalFact(proposal_id=proposal.id, fact_post_id=fact.id, linked_by_id=user.id)
    db.session.add(row)
    return row


def link_ids(proposal, fact_ids, user):
    """Koppel een lijst feit-id's (uit een formulier); alleen feiten die deze
    lezer ziet. Geeft het aantal nieuwe koppelingen. Commit niet zelf."""
    allowed = {f.id: f for f in visible_issue_facts(proposal.issue, user)}
    made = 0
    for fid in dict.fromkeys(fact_ids):
        if fid in allowed and link(proposal, allowed[fid], user) is not None:
            db.session.flush()
            made += 1
    return made


def unlink(row):
    db.session.delete(row)


# ── Stemmen en uitslag (stap 6) ──────────────────────────────────────────

def _phase_index(issue, phase):
    """De plaats van een fase in de fasen van dit vraagstuk (P6: vier of vijf)."""
    return issue.phases.index(phase) if phase in issue.phases else 0


def final_voting_end(proposal):
    return (proposal.created_at or datetime.utcnow()) + timedelta(days=proposal.issue.days_vote or 7)


def voting_open(proposal, count=None, now=None):
    """Kan er nu over dit voorstel gestemd worden?"""
    issue = proposal.issue
    if issue.is_closed:
        return False
    if proposal.is_final:
        # Polijstronde P6: de stemming over het samenvattende voorstel hoort bij de fase samenvatten.
        return issue.phase == 'samenvatten' and (now or datetime.utcnow()) < final_voting_end(proposal)
    if count is None:
        count = support_counts([proposal.id]).get(proposal.id, 0)
    from app import issues
    return issue.phase == 'stemmen' and on_list(proposal, count) and issues.is_open_now(issue, now)


def voting_over(proposal, now=None):
    """Is de stemming voorbij (dan staat de uitslag vast)?"""
    issue = proposal.issue
    if proposal.is_final:
        return (issue.is_closed or issue.phase == 'vervolg'
                or (now or datetime.utcnow()) >= final_voting_end(proposal))
    stemmen = _phase_index(issue, 'stemmen')
    here = _phase_index(issue, issue.phase)
    return here > stemmen or (issue.is_closed and here == stemmen)


def can_vote(proposal, user, count=None):
    return getattr(user, 'is_authenticated', False) and user.can_vote() and voting_open(proposal, count)


def cast_vote(proposal, user, choice):
    """Nieuwe stem of de keuze wijzigen. Commit niet zelf."""
    if choice not in VOTE_CHOICES:
        raise ValueError(choice)
    row = IssueProposalVote.query.filter_by(proposal_id=proposal.id, user_id=user.id).first()
    if row is None:
        db.session.add(IssueProposalVote(proposal_id=proposal.id, user_id=user.id, choice=choice))
    else:
        row.choice = choice


def my_vote(proposal, user):
    if not getattr(user, 'is_authenticated', False):
        return None
    row = IssueProposalVote.query.filter_by(proposal_id=proposal.id, user_id=user.id).first()
    return row.choice if row is not None else None


def tally(proposal):
    counts = dict.fromkeys(VOTE_CHOICES, 0)
    for choice, n in (db.session.query(IssueProposalVote.choice, func.count(IssueProposalVote.id))
                      .filter(IssueProposalVote.proposal_id == proposal.id).group_by(IssueProposalVote.choice)):
        counts[choice] = int(n)
    return counts


def result(proposal, counts=None):
    """De uitslag: aantallen, percentage voor en tegen van de geldige stemmen
    (None zonder geldige stemmen) en of het voorstel is aangenomen."""
    from app.proposals import for_pct
    counts = counts or tally(proposal)
    pct = for_pct(counts['voor'], counts['tegen'])
    threshold = proposal.issue.proposal_threshold_pct
    return {'counts': counts, 'total': sum(counts.values()), 'pct_for': pct,
            'pct_against': None if pct is None else round(100 - pct, 1),
            'threshold': threshold, 'adopted': pct is not None and pct > threshold}


def show_tally(proposal, count=None):
    """De aantallen zijn zichtbaar na afloop, en tijdens het stemmen alleen
    als de tussenstand (show_interim) aanstaat."""
    return voting_over(proposal) or (bool(proposal.issue.show_interim) and voting_open(proposal, count))


# ── Het samenvattende voorstel (stap 6) ────────────────────────────────────────

def final_proposal(issue):
    return IssueProposal.query.filter_by(issue_id=issue.id, is_final=True).first()


def can_make_final(issue, user):
    return (getattr(user, 'is_authenticated', False) and bool(issue.final_vote) and not issue.is_closed
            and issue.phase == 'samenvatten' and issue.created_by_id == user.id and final_proposal(issue) is None)


def create_final(issue, user, statement, explanation=None, lang=None, title=None, intro=None):
    """Het samenvattende voorstel; eventueel met een aangepaste vraag en toelichting
    van het vraagstuk. Commit niet zelf."""
    if title:
        issue.title = title
        thread = issue.thread
        if thread is not None and thread.proposal is None:  # het gesprek van een oud voorstel niet hernoemen
            thread.title_prefix_nl = thread.title_prefix_en = title
            thread.title_accent_nl = thread.title_accent_en = None
            thread.title_suffix_nl = thread.title_suffix_en = ''
    if intro is not None:
        issue.intro = intro or issue.intro
    proposal = IssueProposal(issue_id=issue.id, statement=statement, explanation=explanation,
                             created_by_id=user.id, lang=lang, is_final=True)
    db.session.add(proposal)
    return proposal


# ── Lijst ────────────────────────────────────────────────────────────────

def regular_query(issue):
    """De gewone voorstellen van een vraagstuk (zonder het samenvattende)."""
    return IssueProposal.query.filter(IssueProposal.issue_id == issue.id,
                                      or_(IssueProposal.is_final.is_(None), IssueProposal.is_final.is_(False)))


def listing(issue, which='alle', order=DEFAULT_ORDER):
    """[(voorstel, aantal steunbetuigingen)], gefilterd en gesorteerd."""
    proposals = regular_query(issue).all()
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
    # Stemmen blijven meetellen, zonder naam: de uitslag mag niet veranderen.
    IssueProposalVote.query.filter_by(user_id=user_id).update({IssueProposalVote.user_id: None},
                                                               synchronize_session=False)
    IssueProposal.query.filter_by(created_by_id=user_id).update({IssueProposal.created_by_id: None},
                                                                 synchronize_session=False)
    IssueProposalFact.query.filter_by(linked_by_id=user_id).update({IssueProposalFact.linked_by_id: None},
                                                                   synchronize_session=False)
    from app.models import ProposalReport
    ProposalReport.query.filter_by(reporter_id=user_id).delete(synchronize_session='evaluate')
    ProposalReport.query.filter_by(decided_by_id=user_id).update({ProposalReport.decided_by_id: None},
                                                                 synchronize_session=False)


def delete_links_for_facts(fact_ids):
    """Vóór het verwijderen van feiten: hun koppelingen weg."""
    fact_ids = list(fact_ids)
    if fact_ids:
        IssueProposalFact.query.filter(IssueProposalFact.fact_post_id.in_(fact_ids)).delete(
            synchronize_session='evaluate')
