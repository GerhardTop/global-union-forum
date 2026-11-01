"""
Herontwerp, bouwstap 1: het vraagstuk (Issue) als container voor voorstellen.

Een vraagstuk is een vraag met een eerste bericht, online of als event, met
een planning per fase (in dagen) en de stemregels. Voorstellen hangen er via
proposals.issue_id onder.

- Backfill: elk bestaand voorstel zonder vraagstuk krijgt een eigen
  vraagstuk, met de stelling als vraag, het openingsbericht als eerste
  bericht en de fase en datums van het voorstel. Idempotent: alleen
  voorstellen met issue_id NULL, dus veilig bij elke start.
- Een nieuw voorstel via het oude startformulier krijgt meteen zo'n
  vraagstuk (wrap), zodat elk voorstel er een heeft.
- Een nieuw vraagstuk via /gesprekken/nieuw start in de feitenfase, met
  een eigen gesprek (het eerste bericht als openingsbericht).
- Stap 2: issues.thread_id wijst naar het gesprek met de gedeelde
  discussie. Bij een ingepakt voorstel is dat het gesprek van het voorstel;
  de backfill vult het ook voor vraagstukken uit stap 1.

De fase-automatiek (de tik) draait in deze stap nog op het voorstel; de
fasevelden van een vraagstuk worden hier gezet en getoond, niet bijgewerkt.
"""
import math
from datetime import date, datetime, timedelta

from app import db
from app.models import ISSUE_KINDS, Issue, Post, Proposal, Thread

# Grenzen voor het startformulier.
DAYS_MIN, DAYS_MAX = 1, 365
SUPPORT_MIN, SUPPORT_MAX = 1, 1000
THRESHOLD_MIN, THRESHOLD_MAX = 50, 99
TITLE_MAX, LOCATION_MAX = 300, 200
DAY_FIELDS = (('days_facts', 21), ('days_adjust', 5), ('days_vote', 7))


# ── Backfill en het inpakken van een voorstel ────────────────────────────

def _thread_title(thread, lang):
    suffix = 'en' if lang == 'en' else 'nl'
    accent = getattr(thread, f'title_accent_{suffix}')
    return ''.join((getattr(thread, f'title_prefix_{suffix}') or '',
                    f' {accent}' if accent else '',
                    getattr(thread, f'title_suffix_{suffix}') or '')).strip()


def _days(minutes, default):
    return max(1, math.ceil(minutes / (24 * 60))) if minutes else default


def wrap(proposal):
    """Maak een eigen vraagstuk voor een voorstel en koppel het. Doet niets
    als het voorstel al een vraagstuk heeft. Commit niet zelf."""
    if proposal.issue_id is not None or proposal.issue is not None:
        return proposal.issue
    thread = proposal.thread
    op = (Post.query.filter_by(thread_id=thread.id, is_op=True).order_by(Post.id).first()
          if thread is not None and thread.id is not None else None)
    lang = op.source_lang if op is not None else None
    phase_start, phase_end = proposal.window(proposal.phase)
    issue = Issue(
        title=(_thread_title(thread, lang) if thread is not None else '')[:TITLE_MAX] or '?',
        intro=(op.body or (op.body_en if lang == 'en' else op.body_nl)) if op is not None else None,
        kind='online' if proposal.kind == 'publiek' else 'event',
        voting_deadline=proposal.stemmen_end.date() if proposal.stemmen_end else None,
        days_facts=_days(proposal.feiten_minutes, 21),
        days_adjust=_days(proposal.bijstellen_minutes, 5),
        days_vote=_days(proposal.stemmen_minutes, 7),
        proposal_threshold_pct=proposal.threshold_pct,
        show_interim=bool(proposal.show_live_tally),
        phase=proposal.phase,
        phase_started_at=proposal.phase_changed_at or phase_start or proposal.started_at,
        phase_deadline=None if proposal.phase == 'vervolg' else phase_end,
        created_by_id=proposal.host_id,
        created_at=proposal.started_at or proposal.created_at or datetime.utcnow(),
        lang=lang,
        is_closed=bool(thread.is_closed) if thread is not None else False,
        thread=thread,
    )
    db.session.add(issue)
    proposal.issue = issue
    return issue


def _open_thread(issue, user):
    """Een eigen gesprek voor een vraagstuk, met het eerste bericht als
    openingsbericht. Geeft het openingsbericht terug. Commit niet zelf."""
    thread = Thread(eyebrow_nl='Vraagstuk', eyebrow_en='Issue',
                    title_prefix_nl=issue.title, title_prefix_en=issue.title, is_closed=bool(issue.is_closed))
    db.session.add(thread)
    db.session.flush()
    issue.thread = thread
    op = Post(thread_id=thread.id, user_id=user.id,
              author_name=f'{user.first_name} {user.last_name}' if user.first_name else '',
              author_username=user.username, author_role='member', body=issue.intro or issue.title,
              source_lang=issue.lang or 'nl', is_op=True)
    db.session.add(op)
    return op


def backfill():
    """Elk voorstel zonder vraagstuk in een eigen vraagstuk, en elk vraagstuk
    zonder gesprek een gesprek: dat van zijn (eerste) voorstel, anders een
    eigen gesprek. Idempotent. Geeft het aantal aangepaste rijen."""
    loose = Proposal.query.filter(Proposal.issue_id.is_(None)).order_by(Proposal.id).all()
    for proposal in loose:
        wrap(proposal)
    db.session.flush()
    threadless = Issue.query.filter(Issue.thread_id.is_(None)).order_by(Issue.id).all()
    changed = len(loose)
    for issue in threadless:
        first = min(issue.proposals, key=lambda p: p.id, default=None)
        if first is not None:
            issue.thread_id = first.thread_id
        elif issue.created_by is not None:
            _open_thread(issue, issue.created_by)
        else:
            continue  # zonder maker geen openingsbericht; blijft zonder discussie
        changed += 1
    if changed:
        db.session.commit()
    return changed


def detach_threads(thread_ids):
    """Vóór het verwijderen van gesprekken: vraagstukken laten los. Commit niet zelf."""
    if thread_ids:
        Issue.query.filter(Issue.thread_id.in_(thread_ids)).update({Issue.thread_id: None},
                                                                     synchronize_session=False)


def delete_wrappers(issue_ids):
    """Na het verwijderen van voorstellen: hun vraagstuk weg als er geen
    voorstel meer onder hangt. Commit niet zelf."""
    issue_ids = {i for i in issue_ids if i is not None}
    if not issue_ids:
        return
    still_used = {i for (i,) in db.session.query(Proposal.issue_id).filter(Proposal.issue_id.in_(issue_ids))}
    # Stap 4: een vraagstuk met eigen voorstellen blijft ook staan.
    from app.models import IssueProposal
    still_used |= {i for (i,) in db.session.query(IssueProposal.issue_id).filter(IssueProposal.issue_id.in_(issue_ids))}
    empty = issue_ids - still_used
    if empty:
        Issue.query.filter(Issue.id.in_(empty)).delete(synchronize_session=False)


# ── Een nieuw vraagstuk ──────────────────────────────────────────────────

def _int(raw, low, high, default):
    raw = (raw or '').strip()
    if not raw:
        return default
    if not raw.isdigit() or not low <= int(raw) <= high:
        return None
    return int(raw)


def parse_form(form):
    """De velden uit het startformulier: (waarden, fouten)."""
    values, errors = {}, {}
    values['title'] = (form.get('title') or '').strip()
    values['intro'] = (form.get('intro') or '').strip()
    if not values['title'] or len(values['title']) > TITLE_MAX:
        errors['title'] = 'invalid'
    if not values['intro']:
        errors['intro'] = 'invalid'
    kind = form.get('kind')
    if kind not in ISSUE_KINDS:
        errors['kind'] = 'invalid'
    values['kind'] = kind

    values['event_location'] = (form.get('event_location') or '').strip()[:LOCATION_MAX] or None
    values['event_date'] = None
    values['voting_deadline'] = None
    if kind == 'event':
        try:
            values['event_date'] = datetime.strptime(form.get('event_date') or '', '%Y-%m-%dT%H:%M')
        except ValueError:
            errors['event_date'] = 'invalid'
        raw_deadline = (form.get('voting_deadline') or '').strip()
        if raw_deadline:
            try:
                values['voting_deadline'] = date.fromisoformat(raw_deadline)
            except ValueError:
                errors['voting_deadline'] = 'invalid'
    else:
        values['event_location'] = None

    for name, default in DAY_FIELDS:
        values[name] = _int(form.get(name), DAYS_MIN, DAYS_MAX, default)
        if values[name] is None:
            errors[name] = 'invalid'
    values['support_threshold'] = _int(form.get('support_threshold'), SUPPORT_MIN, SUPPORT_MAX, 5)
    if values['support_threshold'] is None:
        errors['support_threshold'] = 'invalid'
    values['proposal_threshold_pct'] = _int(form.get('proposal_threshold_pct'), THRESHOLD_MIN, THRESHOLD_MAX, 50)
    if values['proposal_threshold_pct'] is None:
        errors['proposal_threshold_pct'] = 'invalid'
    values['show_interim'] = form.get('show_interim') == '1'
    values['final_vote'] = form.get('final_vote') == '1'
    return values, errors


def create(values, user, lang=None, now=None):
    """Een nieuw vraagstuk in de feitenfase. Online: de stemdeadline volgt
    uit de planning. Commit niet zelf."""
    now = now or datetime.utcnow()
    values = {**dict(DAY_FIELDS), **values}  # de planning is nodig vóór de flush
    issue = Issue(created_by_id=user.id, created_at=now, lang=lang, phase='feiten', phase_started_at=now,
                  **values)
    issue.phase_deadline = now + timedelta(days=issue.days_facts)
    if issue.kind == 'online':
        issue.voting_deadline = issue.plan()['stemmen'][1].date()
    db.session.add(issue)
    _open_thread(issue, user)
    return issue


def opening_post(issue):
    if issue.thread_id is None:
        return None
    return Post.query.filter_by(thread_id=issue.thread_id, is_op=True).order_by(Post.id).first()


# ── De cockpit (stap 2): alleen tonen ────────────────────────────────────

def remaining(issue, now=None):
    """(aantal, eenheid) tot de deadline van de huidige fase: dagen, uren of
    minuten; ('verlopen', None) voorbij de deadline; None zonder deadline."""
    if issue.phase_deadline is None:
        return None
    left = issue.phase_deadline - (now or datetime.utcnow())
    if left.total_seconds() <= 0:
        return 0, 'over'
    if left >= timedelta(days=1):
        return left.days, 'days'
    if left >= timedelta(hours=1):
        return int(left.total_seconds() // 3600), 'hours'
    return max(1, int(left.total_seconds() // 60)), 'minutes'


def voting_right(user):
    """Jouw stemrecht voor de rechterkolom: de bestaande verdiende status en
    de voortgang naar stemrecht (aantal keurmerken tegen de drempel)."""
    if not getattr(user, 'is_authenticated', False):
        return None
    from app import status
    count, _topics = status.quality_counts(user.id)
    needed = status.get_thresholds()['voter_min']
    return {'status': user.effective_status, 'can_vote': user.can_vote(), 'count': count, 'needed': needed,
            'pct': 100 if user.can_vote() else min(100, round(100 * count / needed)) if needed else 100}


# ── Het overzicht achter "Denk mee" (polijstronde P1) ────────────────────

OVERVIEW_FILTERS = ('alle', 'online', 'event', 'lopend', 'afgerond')


def is_finished(issue):
    """Afgerond: gesloten, of in de vervolgfase (het beraad is voorbij)."""
    return bool(issue.is_closed) or issue.phase == 'vervolg'


def overview(which='alle'):
    """[(vraagstuk, aantal voorstellen, aantal deelnemers)], nieuwste eerst."""
    from app import issue_lifecycle
    from app.models import IssueProposal
    query = Issue.query
    if which in ISSUE_KINDS:
        query = query.filter(Issue.kind == which)
    found = query.order_by(Issue.created_at.desc(), Issue.id.desc()).all()
    if which == 'lopend':
        found = [i for i in found if not is_finished(i)]
    elif which == 'afgerond':
        found = [i for i in found if is_finished(i)]
    counts = {}
    if found:
        counts = dict(db.session.query(IssueProposal.issue_id, db.func.count(IssueProposal.id))
                      .filter(IssueProposal.issue_id.in_([i.id for i in found]))
                      .group_by(IssueProposal.issue_id).all())
    return [(i, counts.get(i.id, 0) + len(i.proposals), len(issue_lifecycle.participants(i))) for i in found]
