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
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app import db
from app.models import ISSUE_KINDS, Issue, Post, Proposal, Thread

# Grenzen voor het startformulier.
DAYS_MIN, DAYS_MAX = 1, 365
SUPPORT_MIN, SUPPORT_MAX = 0, 1000  # P4: 0 = meteen op de stemlijst
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
        from app import issue_lifecycle
        from app.models import IssueEventDay
        issue_lifecycle.delete_follows(issue_ids=empty)  # polijstronde P3
        IssueEventDay.query.filter(IssueEventDay.issue_id.in_(empty)).delete(synchronize_session=False)
        Issue.query.filter(Issue.id.in_(empty)).delete(synchronize_session=False)


# ── Een nieuw vraagstuk ──────────────────────────────────────────────────
# Polijstronde P4: de tijden in het formulier zijn Nederlandse tijd
# (LOCAL_TZ); opgeslagen wordt UTC, zoals de rest van de app (utcnow).
# Online: per fase een begindatum met een tijd; feiten en discussie
# standaard om 00:00, de volgende fasen standaard om 24:00 (het einde van
# die dag). Event: een naam, een toelichting, één of meer dagen met een
# van-tot-tijd en de keuze of online bijdragen tussen de eerste en de
# laatste dag openstaan; de initiatiefnemer verzet de fasen zelf.

LOCAL_TZ = ZoneInfo('Europe/Amsterdam')
EVENT_DAYS_MAX = 14
NEW_FACTS_MAX = 10
NAME_MAX = 200
# (veld, standaardtijd) per moment van de online planning.
SCHEDULE_FIELDS = (('starts_at', '00:00'), ('adjust_starts_at', '24:00'), ('vote_starts_at', '24:00'),
                   ('vote_ends_at', '24:00'))


def to_utc(local):
    """Een naïeve Nederlandse tijd naar een naïeve UTC-tijd."""
    return local.replace(tzinfo=LOCAL_TZ).astimezone(timezone.utc).replace(tzinfo=None)


def to_local(moment):
    """Een naïeve UTC-tijd naar een naïeve Nederlandse tijd (voor de weergave)."""
    if moment is None:
        return None
    if not isinstance(moment, datetime):
        return moment
    return moment.replace(tzinfo=timezone.utc).astimezone(LOCAL_TZ).replace(tzinfo=None)


def _int(raw, low, high, default):
    raw = (raw or '').strip()
    if not raw:
        return default
    if not raw.isdigit() or not low <= int(raw) <= high:
        return None
    return int(raw)


def _moment(day, time, default_time):
    """Een datum (JJJJ-MM-DD) en een tijd (UU:MM, leeg is de standaard; 24:00
    is het einde van de dag) als Nederlandse tijd, of None als ongeldig."""
    try:
        day = date.fromisoformat((day or '').strip())
    except ValueError:
        return None
    time = (time or '').strip() or default_time
    if time == '24:00':
        return datetime.combine(day, datetime.min.time()) + timedelta(days=1)
    try:
        return datetime.combine(day, datetime.strptime(time, '%H:%M').time())
    except ValueError:
        return None


def default_schedule(today=None):
    """De standaardplanning voor het formulier: {veld: (datum, tijd)}, met de
    standaard doorlooptijden (21, 5 en 7 dagen) vanaf vandaag 00:00."""
    today = today or to_local(datetime.utcnow()).date()
    days = dict(DAY_FIELDS)
    adjust = today + timedelta(days=days['days_facts'] - 1)
    vote = adjust + timedelta(days=days['days_adjust'])
    end = vote + timedelta(days=days['days_vote'])
    return {'starts_at': (today.isoformat(), '00:00'), 'adjust_starts_at': (adjust.isoformat(), ''),
            'vote_starts_at': (vote.isoformat(), ''), 'vote_ends_at': (end.isoformat(), '')}


def _days_between(start, end):
    return max(DAYS_MIN, min(DAYS_MAX, math.ceil((end - start).total_seconds() / 86400)))


def parse_event_days(form, errors):
    """De dagen van een event: [(begin, eind)] in UTC, gesorteerd."""
    days = []
    dates, starts, ends = form.getlist('event_day'), form.getlist('event_from'), form.getlist('event_to')
    for i, raw in enumerate(dates[:EVENT_DAYS_MAX]):
        if not (raw or '').strip():
            continue
        begin = _moment(raw, starts[i] if i < len(starts) else '', '09:00')
        end = _moment(raw, ends[i] if i < len(ends) else '', '17:00')
        if begin is None or end is None or end <= begin:
            errors['event_days'] = 'invalid'
            continue
        days.append((to_utc(begin), to_utc(end)))
    if not days and 'event_days' not in errors:
        errors['event_days'] = 'missing'
    return sorted(days)


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
    values['event_date'] = values['voting_deadline'] = None
    values['event_name'] = values['event_note'] = None
    values['event_online_between'] = None
    values['event_days'] = []
    for name, _default in SCHEDULE_FIELDS:
        values[name] = None
    days = dict(DAY_FIELDS)
    if kind == 'event':
        values['event_name'] = (form.get('event_name') or '').strip()[:NAME_MAX] or None
        if not values['event_name']:
            errors['event_name'] = 'invalid'
        values['event_note'] = (form.get('event_note') or '').strip() or None
        values['event_online_between'] = form.get('event_online_between') == '1'
        values['event_days'] = parse_event_days(form, errors)
        if values['event_days']:
            values['event_date'] = values['event_days'][0][0]
    elif kind == 'online':
        values['event_location'] = None
        moments = []
        for name, default_time in SCHEDULE_FIELDS:
            local = _moment(form.get(f'{name}_date'), form.get(f'{name}_time'), default_time)
            if local is None:
                errors[name] = 'invalid'
            moments.append(local)
        if 'starts_at' not in errors:
            # Elke fase begint na de vorige; het stemmen sluit na zijn begin.
            for (name, _t), prev, cur in zip(SCHEDULE_FIELDS[1:], moments, moments[1:]):
                if prev is not None and cur is not None and cur <= prev:
                    errors[name] = 'order'
        if not errors.keys() & {n for n, _t in SCHEDULE_FIELDS}:
            utc = [to_utc(m) for m in moments]
            if utc[-1] <= datetime.utcnow():
                errors['vote_ends_at'] = 'past'
            for (name, _t), moment in zip(SCHEDULE_FIELDS, utc):
                values[name] = moment
            days = {'days_facts': _days_between(utc[0], utc[1]), 'days_adjust': _days_between(utc[1], utc[2]),
                    'days_vote': _days_between(utc[2], utc[3])}
    values.update(days)
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
    """Een nieuw vraagstuk in de feitenfase. Online: met een planning in
    momenten begint de feitenfase op het geplande begin en loopt tot het
    begin van het bijstellen; zonder (tests, oude aanroepen) vanaf nu met de
    dagen. Het stemmen sluit aan het eind van de planning. Commit niet zelf."""
    from app.models import IssueEventDay
    now = now or datetime.utcnow()
    values = {**dict(DAY_FIELDS), **values}  # de planning is nodig vóór de flush
    event_days = values.pop('event_days', None) or []
    issue = Issue(created_by_id=user.id, created_at=now, lang=lang, phase='feiten', **values)
    if issue.kind == 'online' and issue.starts_at is not None:
        issue.phase_started_at = issue.starts_at
        issue.phase_deadline = issue.adjust_starts_at
        issue.voting_deadline = to_local(issue.vote_ends_at - timedelta(seconds=1)).date()
    else:
        issue.phase_started_at = now
        issue.phase_deadline = now + timedelta(days=issue.days_facts) if issue.kind == 'online' else None
        if issue.kind == 'online':
            issue.voting_deadline = issue.plan()['stemmen'][1].date()
    db.session.add(issue)
    for begin, end in event_days:
        issue.event_days.append(IssueEventDay(starts_at=begin, ends_at=end))
    _open_thread(issue, user)
    return issue


def is_open_now(issue, now=None):
    """Polijstronde P4: is het platform nu open voor bijdragen bij dit
    vraagstuk? Online: vanaf het geplande begin. Event: tijdens een dag van
    het event, en tussen de eerste en de laatste dag als dat aanstaat.
    Zonder planning of dagen (vraagstukken van vóór P4) altijd."""
    now = now or datetime.utcnow()
    if issue.kind == 'online':
        return issue.starts_at is None or now >= issue.starts_at
    days = list(issue.event_days) if issue.id is not None else []
    if not days:
        return True
    if issue.event_online_between and days[0].starts_at <= now <= days[-1].ends_at:
        return True
    return any(day.starts_at <= now <= day.ends_at for day in days)


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


# ── Verwijderen (fase 3) ─────────────────────────────────────────────────

def purge(issue):
    """Verwijder een heel vraagstuk definitief: de discussie (het gesprek en de
    bijdragen, reacties op voorstellen inbegrepen), alle voorstellen met hun
    versies, steun, stemmen, feitkoppelingen en meldingen, het oude voorstel dat
    dit gesprek deelt, de volgers en de eventdagen. Onomkeerbaar. Commit zelf.

    Dezelfde foreign-key-volgorde als de demo-reset (demo_voorbeeld._remove_issue
    roept deze functie aan), zodat beide niet uit elkaar groeien."""
    from app import quality, issue_lifecycle, proposals
    from app.models import (IssueProposal, IssueProposalVersion, IssueProposalSupport,
                            IssueProposalVote, IssueProposalFact, ProposalReport,
                            PostLike, IssueEventDay, Proposal)
    thread_id = issue.thread_id
    prop_ids = [p.id for p in IssueProposal.query.filter_by(issue_id=issue.id)]
    post_ids = [p.id for p in Post.query.filter_by(thread_id=thread_id)] if thread_id else []
    # Eerst alles wat naar de berichten verwijst, dan de berichten, dan de voorstellen.
    if post_ids:
        quality.delete_for_posts(post_ids)  # merk, moderatie, feitsignalen, meldingen
        PostLike.query.filter(PostLike.post_id.in_(post_ids)).delete(synchronize_session=False)
        Post.query.filter(Post.parent_id.in_(post_ids)).update({Post.parent_id: None}, synchronize_session=False)
        Post.query.filter(Post.id.in_(post_ids)).delete(synchronize_session=False)
    if prop_ids:
        for model in (IssueProposalVote, IssueProposalSupport, IssueProposalFact, IssueProposalVersion, ProposalReport):
            model.query.filter(model.proposal_id.in_(prop_ids)).delete(synchronize_session=False)
        IssueProposal.query.filter(IssueProposal.id.in_(prop_ids)).delete(synchronize_session=False)
    issue_lifecycle.delete_follows(issue_ids=[issue.id])
    IssueEventDay.query.filter_by(issue_id=issue.id).delete(synchronize_session=False)
    # Een oud voorstel met een eigen gesprek wordt alleen losgekoppeld; het oude
    # voorstel dat dit gesprek deelt, gaat via delete_for_threads mee weg.
    Proposal.query.filter(Proposal.issue_id == issue.id, Proposal.thread_id != thread_id) \
        .update({Proposal.issue_id: None}, synchronize_session=False)
    issue_id = issue.id
    issue.thread_id = None
    db.session.flush()
    if thread_id:
        proposals.delete_for_threads([thread_id])
    Issue.query.filter_by(id=issue_id).delete(synchronize_session=False)
    if thread_id:
        Thread.query.filter_by(id=thread_id).delete(synchronize_session=False)
    db.session.commit()


def purge_proposal(proposal):
    """Verwijder een los voorstel definitief: de reacties erop (bijdragen met
    issue_proposal_id), zijn versies, steun, stemmen, feitkoppelingen en
    meldingen. Het vraagstuk blijft staan. Commit zelf."""
    from app import quality
    from app.models import (IssueProposal, IssueProposalVersion, IssueProposalSupport,
                            IssueProposalVote, IssueProposalFact, ProposalReport, PostLike)
    pid = proposal.id
    reply_ids = [p.id for p in Post.query.filter_by(issue_proposal_id=pid)]
    if reply_ids:
        quality.delete_for_posts(reply_ids)
        PostLike.query.filter(PostLike.post_id.in_(reply_ids)).delete(synchronize_session=False)
        Post.query.filter(Post.parent_id.in_(reply_ids)).update({Post.parent_id: None}, synchronize_session=False)
        Post.query.filter(Post.id.in_(reply_ids)).delete(synchronize_session=False)
    for model in (IssueProposalVote, IssueProposalSupport, IssueProposalFact, IssueProposalVersion, ProposalReport):
        model.query.filter(model.proposal_id == pid).delete(synchronize_session=False)
    IssueProposal.query.filter(IssueProposal.id == pid).delete(synchronize_session=False)
    db.session.commit()
