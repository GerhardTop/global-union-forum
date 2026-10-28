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
- Een nieuw vraagstuk via /gesprekken/nieuw start in de feitenfase.

De fase-automatiek (de tik) draait in deze stap nog op het voorstel; de
fasevelden van een vraagstuk worden hier gezet en getoond, niet bijgewerkt.
"""
import math
from datetime import date, datetime, timedelta

from app import db
from app.models import ISSUE_KINDS, Issue, Post, Proposal

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
    )
    db.session.add(issue)
    proposal.issue = issue
    return issue


def backfill():
    """Elk voorstel zonder vraagstuk in een eigen vraagstuk. Idempotent."""
    loose = Proposal.query.filter(Proposal.issue_id.is_(None)).order_by(Proposal.id).all()
    for proposal in loose:
        wrap(proposal)
    if loose:
        db.session.commit()
    return len(loose)


def delete_wrappers(issue_ids):
    """Na het verwijderen van voorstellen: hun vraagstuk weg als er geen
    voorstel meer onder hangt. Commit niet zelf."""
    issue_ids = {i for i in issue_ids if i is not None}
    if not issue_ids:
        return
    still_used = {i for (i,) in db.session.query(Proposal.issue_id).filter(Proposal.issue_id.in_(issue_ids))}
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
    return issue
