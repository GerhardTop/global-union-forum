"""
Herontwerp, bouwstap 7: de levenscyclus van een vraagstuk.

Fasen: feiten, bijstellen, stemmen, vervolg (PROPOSAL_PHASES).

- Online: de uurtaak (digest.tick, POST /intern/tik) zet een vraagstuk naar
  de volgende fase zodra de deadline van de huidige fase is verstreken. Bij
  elke overgang worden phase, phase_started_at en phase_deadline opnieuw
  gezet uit days_facts, days_adjust en days_vote. Een overgang op de
  deadline begint op die deadline (de planning blijft kloppen, ook als een
  tik wordt gemist); zo nodig schuiven meerdere fasen in één tik door.
- De feitenfase sluit ook eerder: na een stilte van N dagen (standaard 5,
  instelbaar door de admin) zonder nieuw keurmerk in het gesprek van het
  vraagstuk. De stilte telt vanaf het begin van de feitenfase of het laatste
  keurmerk, wat later is.
- Event: geen automatiek en geen deadlines. De initiatiefnemer of een
  moderator zet de fasen met de hand door ("naar de volgende fase"); die
  actie bestaat ook bij online, om een fase vroegtijdig af te sluiten.
- Uit de vervolgfase gaat niets automatisch verder. Daar schrijft de
  initiatiefnemer of een moderator de vervolgtekst, en kan het vraagstuk
  (ook als het gesloten is) opnieuw worden opengesteld: terug naar de
  feitenfase, met een nieuwe planning.
- Een vraagstuk dat een oud voorstel inpakt (Proposal, een voorstel is een
  gesprek) heeft geen eigen automatiek en geen handmatige overgangen: zijn
  fase volgt die van het oude voorstel, dat zijn eigen tik heeft. Zo lopen
  ze niet uit elkaar en komen er geen dubbele meldingen.

Meldingen: digest_items levert per vraagstuk de belangrijke veranderingen
voor de bestaande dagelijkse samenvatting (app/digest.py); de ontvangers zijn
de initiatiefnemer en wie heeft bijgedragen (participants).
"""
from datetime import datetime, timedelta

from sqlalchemy import func, or_

from app import db
from app.models import (AppSetting, Issue, IssueProposal, IssueProposalSupport, IssueProposalVote, Post,
                        QualityAward)

SILENCE_KEY = 'issue_facts_silence_days'
DEFAULT_SILENCE_DAYS = 5
SILENCE_MIN, SILENCE_MAX = 1, 365
NEXT_PHASE = {'feiten': 'bijstellen', 'bijstellen': 'stemmen', 'stemmen': 'vervolg'}
CLOSING_SOON = timedelta(hours=24)


# ── Instelling: de stilte-drempel van de feitenfase ──────────────────────

def get_silence_days():
    row = db.session.get(AppSetting, SILENCE_KEY)
    return row.value if row is not None else DEFAULT_SILENCE_DAYS


def set_silence_days(days, user_id=None):
    row = db.session.get(AppSetting, SILENCE_KEY)
    if row is None:
        db.session.add(AppSetting(key=SILENCE_KEY, value=int(days), updated_by_id=user_id))
    else:
        row.value, row.updated_by_id = int(days), user_id
    db.session.commit()


# ── Fasen ────────────────────────────────────────────────────────────────

def is_wrapper(issue):
    """Pakt dit vraagstuk een oud voorstel in (stap 1)?"""
    return bool(issue.proposals)


def _deadline(issue, phase, start):
    if issue.kind != 'online':
        return None  # een event heeft geen vaste datums
    days = issue.days(phase)
    return start + timedelta(days=days) if days else None


def set_phase(issue, phase, start):
    """Zet de fase met een nieuw begin en een nieuwe deadline. Commit niet zelf."""
    issue.phase = phase
    issue.phase_started_at = start
    issue.phase_deadline = _deadline(issue, phase, start)
    if phase == 'stemmen' and issue.phase_deadline is not None:
        issue.voting_deadline = issue.phase_deadline.date()


def last_quality_at(issue):
    """Het moment van het laatste keurmerk in het gesprek van het vraagstuk."""
    if issue.thread_id is None:
        return None
    return (db.session.query(func.max(func.coalesce(QualityAward.decided_at, QualityAward.created_at)))
            .join(Post, QualityAward.post_id == Post.id)
            .filter(Post.thread_id == issue.thread_id, QualityAward.status == 'toegekend').scalar())


def silence_end(issue):
    """Wanneer de feitenfase door stilte sluit, of None."""
    if issue.phase != 'feiten' or issue.phase_started_at is None:
        return None
    anchor = max(m for m in (issue.phase_started_at, last_quality_at(issue)) if m is not None)
    return anchor + timedelta(days=get_silence_days())


def due(issue, now=None):
    """(volgende fase, begin) als een online vraagstuk nu moet doorschuiven, anders None."""
    now = now or datetime.utcnow()
    if issue.kind != 'online' or issue.is_closed or issue.phase not in NEXT_PHASE or is_wrapper(issue):
        return None
    candidates = []
    if issue.phase_deadline is not None and now >= issue.phase_deadline:
        candidates.append(issue.phase_deadline)
    quiet = silence_end(issue)
    if quiet is not None and now >= quiet:
        candidates.append(quiet)
    if not candidates:
        return None
    return NEXT_PHASE[issue.phase], min(candidates)


def advance(issue, now=None):
    """Schuif een online vraagstuk door zolang er een overgang verschuldigd is.
    Geeft True als de fase veranderde. Commit niet zelf."""
    now = now or datetime.utcnow()
    moved = False
    for _guard in range(len(NEXT_PHASE)):
        step = due(issue, now)
        if step is None:
            break
        set_phase(issue, *step)
        moved = True
    return moved


def sync_wrapper(issue):
    """Een ingepakt oud voorstel: de fase van het vraagstuk volgt die van het
    voorstel (alleen lezen aan de kant van het voorstel). Commit niet zelf."""
    proposal = min(issue.proposals, key=lambda p: p.id)
    if issue.phase == proposal.phase:
        return False
    start, end = proposal.window(proposal.phase)
    issue.phase = proposal.phase
    issue.phase_started_at = proposal.phase_changed_at or start or proposal.started_at
    issue.phase_deadline = None if proposal.phase == 'vervolg' else end
    return True


def advance_due(now=None):
    """De uurtaak: alle open vraagstukken langs. Geeft het aantal dat veranderde."""
    now = now or datetime.utcnow()
    moved = 0
    for issue in Issue.query.filter(Issue.is_closed.is_(False), Issue.phase != 'vervolg').all():
        changed = sync_wrapper(issue) if is_wrapper(issue) else advance(issue, now)
        moved += int(changed)
    if moved:
        db.session.commit()
    return moved


# ── Handmatig: doorzetten, vervolgtekst, opnieuw openstellen ─────────────

def _is_host(issue, user):
    return getattr(user, 'is_authenticated', False) and (issue.created_by_id == user.id or user.can_moderate())


def can_step(issue, user):
    return (_is_host(issue, user) and not is_wrapper(issue) and not issue.is_closed
            and issue.phase in NEXT_PHASE)


def step_forward(issue, now=None):
    """Naar de volgende fase (event, of een online fase vroegtijdig afsluiten)."""
    set_phase(issue, NEXT_PHASE[issue.phase], now or datetime.utcnow())


def can_edit_followup(issue, user):
    return _is_host(issue, user) and issue.phase == 'vervolg'


def set_followup(issue, text, now=None):
    issue.followup_text = text or None
    issue.followup_at = now or datetime.utcnow()


def can_reopen(issue, user):
    return _is_host(issue, user) and not is_wrapper(issue) and (issue.is_closed or issue.phase == 'vervolg')


def reopen(issue, now=None):
    """Terug naar de discussie: de feitenfase met een nieuwe planning. De
    voorstellen, steun en stemmen blijven bewaard. Commit niet zelf."""
    issue.is_closed = False
    if issue.thread is not None:
        issue.thread.is_closed = False
    set_phase(issue, 'feiten', now or datetime.utcnow())


# ── Meldingen voor de dagelijkse samenvatting ────────────────────────────

def participants(issue):
    """De initiatiefnemer en wie heeft bijgedragen: berichten en feiten in het
    gesprek, voorstellen, steun en stemmen."""
    ids = {issue.created_by_id}
    if issue.thread_id is not None:
        ids |= {u for (u,) in db.session.query(Post.user_id).filter(Post.thread_id == issue.thread_id).distinct()}
    proposal_ids = db.session.query(IssueProposal.id).filter(IssueProposal.issue_id == issue.id)
    ids |= {u for (u,) in db.session.query(IssueProposal.created_by_id).filter(IssueProposal.issue_id == issue.id)}
    ids |= {u for (u,) in db.session.query(IssueProposalSupport.user_id)
            .filter(IssueProposalSupport.proposal_id.in_(proposal_ids))}
    ids |= {u for (u,) in db.session.query(IssueProposalVote.user_id)
            .filter(IssueProposalVote.proposal_id.in_(proposal_ids))}
    ids.discard(None)
    return ids


def notifiable_issues():
    """Vraagstukken met eigen meldingen (de ingepakte volgen het oude voorstel)."""
    return [i for i in Issue.query.all() if not is_wrapper(i)]


def issues_for_user(user_id):
    return [i for i in notifiable_issues() if user_id in participants(i)]


def _iso(moment):
    return moment.isoformat() if moment else None


def changes(issue, since, now=None):
    """De belangrijke veranderingen van een vraagstuk sinds `since`."""
    from app import issue_proposals as ip
    now = now or datetime.utcnow()
    out = []
    started = issue.phase_started_at
    if started is not None and since < started <= now:
        if issue.phase == 'feiten':
            if issue.created_at is None or started - issue.created_at > timedelta(minutes=1):
                out.append({'type': 'issue_heropend'})
        elif issue.phase == 'bijstellen':
            out.append({'type': 'issue_bijstellen'})
        elif issue.phase == 'stemmen':
            out.append({'type': 'stemmen_open', 'until': _iso(issue.phase_deadline)})
        elif issue.phase == 'vervolg':
            results = []
            for proposal, count in ip.listing(issue, order='stemlijst'):
                if ip.on_list(proposal, count):
                    res = ip.result(proposal)
                    results.append({'statement': proposal.statement, 'adopted': res['adopted'], 'pct': res['pct_for']})
            out.append({'type': 'issue_uitslag', 'results': results})
    if (issue.phase == 'stemmen' and not issue.is_closed and issue.phase_deadline
            and now < issue.phase_deadline <= now + CLOSING_SOON):
        out.append({'type': 'sluit_binnenkort', 'until': _iso(issue.phase_deadline)})
    final = ip.final_proposal(issue)
    if final is not None:
        end = ip.final_voting_end(final)
        if final.created_at and since < final.created_at <= now:
            out.append({'type': 'ultiem_open', 'statement': final.statement, 'until': _iso(end)})
        if since < end <= now:
            res = ip.result(final)
            out.append({'type': 'ultiem_uitslag', 'statement': final.statement, 'adopted': res['adopted'],
                        'pct': res['pct_for']})
    if issue.followup_at is not None and since < issue.followup_at <= now and issue.followup_text:
        out.append({'type': 'issue_vervolgtekst'})
    return out
