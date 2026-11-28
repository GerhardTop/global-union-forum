"""
Events (docs/events.md): a gathering with its own space, one or more issues.

Step 1b: the data model, the code of the link, and the transition from the
event issues of before (the event data on the issue itself). Until the form
"New event" exists (step 1c), the issue form stays the source:
attach_legacy(issue) gives every event issue without an event an event with
the same data and the defaults of K12. The switch EVENTS (enabled) changes
nothing yet; it is there for steps 1c to 1f.
"""
import logging
import secrets
from datetime import datetime

from flask import current_app

from app import db

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)

# K2: 8 characters without the ones that look alike (no 0, O, 1, I, L).
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8
NAME_MAX = 200
# The phases in which voting has begun (K7: the setting is then locked).
_VOTING_BEGUN = ('stemmen', 'samenvatten', 'vervolg')


def enabled(app=None):
    return bool((app or current_app).config.get('EVENTS'))


def new_code():
    """A code nobody can guess, unique among the events."""
    from app.models import Event
    while True:
        code = ''.join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        if not db.session.query(Event.id).filter_by(code=code).first():
            return code


def normalize_code(text):
    """What someone typed as an event code: upper case, without spaces and
    hyphens (step 1d)."""
    return ''.join(ch for ch in (text or '').upper() if not ch.isspace() and ch != '-')


def attach_legacy(issue, now=None):
    """Give an event issue without an event its own event, with the data of
    the issue and the defaults of K12: everyone may vote off, shared, locked
    when voting already began. Copies the days. Does not commit; returns the
    event (None when there is nothing to do)."""
    from app.models import Event, EventDay
    if issue.kind != 'event' or issue.event_id is not None:
        return None
    now = now or datetime.utcnow()
    voting_begun = issue.is_closed or issue.phase in _VOTING_BEGUN
    event = Event(
        code=new_code(),
        name=(issue.event_name or issue.title or '')[:NAME_MAX],
        note=issue.event_note,
        location=issue.event_location,
        format='location' if issue.event_location else 'online',
        online_url=None,
        online_between=bool(issue.event_online_between),
        everyone_votes=False,
        everyone_votes_locked_at=now if voting_begun else None,
        open_for_issues=False,
        shared_at=issue.created_at or now,
        shared_by_id=None,
        created_by_id=issue.created_by_id,
        created_at=issue.created_at or now,
        lang=issue.lang,
    )
    for day in issue.event_days:
        event.days.append(EventDay(starts_at=day.starts_at, ends_at=day.ends_at))
    db.session.add(event)
    db.session.flush()
    issue.event_id = event.id
    issue.event_position = 1
    return event


def attach_all_legacy():
    """attach_legacy for every event issue without an event, by id; commits
    once. Returns the number of events made (a second call makes none)."""
    from app.models import Issue
    todo = Issue.query.filter(Issue.kind == 'event', Issue.event_id.is_(None)).order_by(Issue.id).all()
    made = sum(1 for issue in todo if attach_legacy(issue) is not None)
    db.session.commit()
    return made


def delete_empty(event_ids):
    """Delete the events among `event_ids` that no issue belongs to any more,
    with their days and translations (a bulk delete). Does not commit."""
    from app import content_i18n
    from app.models import Event, EventDay, Issue
    event_ids = {i for i in event_ids if i is not None}
    if not event_ids:
        return
    used = {i for (i,) in db.session.query(Issue.event_id).filter(Issue.event_id.in_(event_ids))}
    empty = list(event_ids - used)
    if not empty:
        return
    EventDay.query.filter(EventDay.event_id.in_(empty)).delete(synchronize_session=False)
    content_i18n.delete_for('events', empty)
    Event.query.filter(Event.id.in_(empty)).delete(synchronize_session=False)


# ── Rights (step 1c, K4) ─────────────────────────────────────────────────

def _active(user):
    return (user is not None and getattr(user, 'is_authenticated', False)
            and not getattr(user, 'is_deleted', False))


def can_create(user):
    """Anyone who may contribute (logged in, conduct rules accepted, not suspended)."""
    return _active(user) and user.can_contribute()


def can_manage(event, user):
    """The organiser, with moderators as the fallback. A former account
    manages nothing."""
    if event is None or not _active(user):
        return False
    return event.created_by_id == user.id or user.can_moderate()


def can_add_issue(event, user):
    """Who may add an issue to the event: whoever manages it, or, with
    "Participants may add issues" on, anyone who may contribute. Nobody for
    an archived event."""
    if event is None or event.archived_at is not None:
        return False
    return can_manage(event, user) or (bool(event.open_for_issues) and can_create(user))


def votes_locked(event):
    """K7: "Everyone may vote" is fixed once voting began (set in step 1f;
    now only on migrated events)."""
    return event is not None and event.everyone_votes_locked_at is not None


def by_code(code):
    """The event with this code (normalised), or None."""
    from app.models import Event
    code = normalize_code(code)
    if len(code) != CODE_LENGTH:
        return None
    return Event.query.filter_by(code=code).first()


# ── The form "New event" and the settings (step 1c, K13) ─────────────────

LOCATION_MAX = 200
URL_MAX = 500
FORM_FIELDS = ('name', 'note', 'format', 'location', 'online_url', 'online_between', 'open_for_issues',
               'everyone_votes')
FORM_LISTS = ('event_day', 'event_from', 'event_to')


def _is_web_link(url):
    """Only http:// and https://, with a host after it and no spaces."""
    low = url.lower()
    rest = low.split('://', 1)[1] if '://' in low else ''
    return low.split('://', 1)[0] in ('http', 'https') and bool(rest) and not any(ch.isspace() for ch in url)


def parse_form(form):
    """The fields of the form: (values, errors). Codes, not labels: the
    format is stored as location, online or hybrid."""
    from app import issues
    from app.models import EVENT_FORMATS
    values, errors = {}, {}
    name = (form.get('name') or '').strip()
    if not name or len(name) > NAME_MAX:
        errors['name'] = 'invalid'
    values['name'] = name[:NAME_MAX]
    values['note'] = (form.get('note') or '').strip() or None
    fmt = form.get('format')
    if fmt not in EVENT_FORMATS:
        errors['format'] = 'invalid'
    values['format'] = fmt if fmt in EVENT_FORMATS else 'location'
    location = (form.get('location') or '').strip()
    url = (form.get('online_url') or '').strip()
    if fmt in ('location', 'hybrid'):
        if not location or len(location) > LOCATION_MAX:
            errors['location'] = 'invalid'
        values['location'] = location[:LOCATION_MAX] or None
    else:
        values['location'] = None
    if fmt in ('online', 'hybrid'):
        if url and (len(url) > URL_MAX or not _is_web_link(url)):
            errors['online_url'] = 'invalid'
        values['online_url'] = url[:URL_MAX] or None
    else:
        values['online_url'] = None
    values['days'] = issues.parse_event_days(form, errors)
    values['online_between'] = form.get('online_between') == '1'
    values['open_for_issues'] = form.get('open_for_issues') == '1'
    values['everyone_votes'] = form.get('everyone_votes') == '1'
    return values, errors


def _set_days(event, days):
    from app.models import EventDay
    event.days = [EventDay(starts_at=begin, ends_at=end) for begin, end in days]


def create(values, user, lang=None):
    """A new event of `user`, with a fresh code; translated into the other
    languages after the commit. Does not commit."""
    from app import content_jobs, i18n
    from app.models import Event
    values = dict(values)
    days = values.pop('days', None) or []
    event = Event(code=new_code(), lang=lang or i18n.current(), created_by_id=user.id,
                  created_at=datetime.utcnow(), **values)
    _set_days(event, days)
    db.session.add(event)
    db.session.flush()
    content_jobs.schedule(event)
    return event


def update(event, values):
    """Save the settings. A locked "Everyone may vote" stays as it is, also
    with a forged POST. A new name or note: translations again. Does not commit."""
    from app import content_i18n, content_jobs
    values = dict(values)
    days = values.pop('days', None)
    if votes_locked(event):
        values.pop('everyone_votes', None)
    changed = [field for field in ('name', 'note') if field in values and values[field] != getattr(event, field)]
    for key, value in values.items():
        setattr(event, key, value)
    if days is not None:
        _set_days(event, days)
    if changed:
        for field in changed:
            content_i18n.invalidate(event, field)
        content_jobs.schedule(event)


def renumber(event):
    """event_position 1 to n, in the current order."""
    for position, issue in enumerate(_ordered(event), start=1):
        issue.event_position = position


def _ordered(event):
    return sorted(event.issues, key=lambda i: (i.event_position is None, i.event_position or 0, i.id))


def move(event, issue_id, direction):
    """Move an issue one place up or down; the ends do nothing. Does not commit."""
    issues = _ordered(event)
    index = next((n for n, issue in enumerate(issues) if issue.id == issue_id), None)
    if index is None:
        return False
    other = index - 1 if direction == 'up' else index + 1 if direction == 'down' else None
    if other is not None and 0 <= other < len(issues):
        issues[index], issues[other] = issues[other], issues[index]
    for position, issue in enumerate(issues, start=1):
        issue.event_position = position
    return True


def next_position(event):
    """The place of a new issue: at the bottom of the list."""
    from app.models import Issue
    highest = db.session.query(db.func.max(Issue.event_position)).filter(Issue.event_id == event.id).scalar()
    return (highest or 0) + 1


def choices_for(user, code=None):
    """The events an issue of `user` can be added to in the issue form: own
    events that are not archived, and the event of ?event=<code> when user
    may add to it (open_for_issues, or managing it)."""
    from app.models import Event
    if not _active(user):
        return []
    own = Event.query.filter(Event.created_by_id == user.id, Event.archived_at.is_(None)) \
        .order_by(Event.created_at.desc(), Event.id.desc()).all()
    extra = by_code(code) if code else None
    if extra is not None and extra not in own and can_add_issue(extra, user):
        own.insert(0, extra)
    return [event for event in own if can_add_issue(event, user)]


# ── Step 1d: the event page, access, sharing, Your events ────────────────

ACCESS_KEY = 'event_access'
ACCESS_MAX = 20


def grant(event, sess=None):
    """Opening /e/<code> gives this session access to the issues of the
    event (K2b); at most ACCESS_MAX events, the oldest drop off."""
    from flask import session
    sess = session if sess is None else sess
    ids = [i for i in (sess.get(ACCESS_KEY) or []) if i != event.id]
    ids.append(event.id)
    sess[ACCESS_KEY] = ids[-ACCESS_MAX:]


def has_access(event, sess=None):
    from flask import session
    sess = session if sess is None else sess
    return event.id in (sess.get(ACCESS_KEY) or [])


def is_shared(event):
    return event is not None and event.shared_at is not None


def can_view(issue, user, sess=None):
    """K2b: may this reader open the issue (or anything under it)? Yes without
    an event, for a shared event, or with EVENTS off. Otherwise: the link of
    the event was opened in this session, or the reader took part in or
    follows the issue, or manages the event."""
    if issue is None or issue.event_id is None or not enabled():
        return True
    event = issue.event
    if event is None or is_shared(event):
        return True
    if has_access(event, sess) or can_manage(event, user):
        return True
    if not _active(user):
        return False
    from app import issue_lifecycle
    return user.id in issue_lifecycle.participants(issue) or issue_lifecycle.is_following(issue, user)


def refuse():
    """Abort with 404 and the message of K2b (an issue of an event that is
    not shared, without access)."""
    from flask import abort, make_response, render_template, request
    from flask_babel import gettext
    # TRANSLATORS: shown instead of an issue of an event that is not shared, to someone without its link.
    message = gettext('This issue belongs to an event. Open the link of the event to take part.')
    if request.is_json or request.accept_mimetypes.best == 'application/json':
        abort(make_response({'error': 'event', 'message': message}, 404))
    abort(make_response(render_template('events/notice.html', message=message), 404))


def check_view(issue, user):
    """can_view, else refuse(); marks the page noindex for an event that is
    not shared. Step 1e: such an event becomes the current event of the
    session, with the bar on the page. The event is loaded with its days in
    one query (the only extra query of an issue page)."""
    if issue is not None and issue.event_id is not None and enabled():
        from sqlalchemy.orm import joinedload
        from sqlalchemy.orm.attributes import set_committed_value
        from app.models import Event
        event = db.session.query(Event).options(joinedload(Event.days)) \
            .filter(Event.id == issue.event_id).one_or_none()
        set_committed_value(issue, 'event', event)   # no lazy load later, no change to flush
    if not can_view(issue, user):
        refuse()
    if noindex(issue):
        mark_noindex()
        enter(issue.event)


def mark_noindex():
    """meta robots noindex on this response (base.html), per request."""
    from flask import request
    request.environ['guf.noindex'] = True


def noindex(issue):
    """Pages of an issue of an event that is not shared stay out of search engines."""
    return enabled() and issue is not None and issue.event_id is not None and not is_shared(issue.event)


def listed_filter(query):
    """The one filter for every public list of issues (K10): no issues of an
    event that is not shared, or archived. Only with EVENTS on."""
    from app.models import Event, Issue
    if not enabled():
        return query
    listed = db.session.query(Event.id).filter(Event.shared_at.isnot(None), Event.archived_at.is_(None))
    return query.filter(db.or_(Issue.event_id.is_(None), Issue.event_id.in_(listed)))


def hidden_thread_ids():
    """The conversations of issues that are not listed (for /forum)."""
    from app.models import Event, Issue
    if not enabled():
        return set()
    hidden = db.session.query(Event.id).filter(db.or_(Event.shared_at.is_(None), Event.archived_at.isnot(None)))
    return {t for (t,) in db.session.query(Issue.thread_id).filter(Issue.event_id.in_(hidden),
                                                                   Issue.thread_id.isnot(None))}


def open_now(event, now=None):
    """Is the platform open for contributions to this event now: during a
    day, between the first and the last day when that is on; without days always."""
    now = now or datetime.utcnow()
    days = list(event.days)
    if not days:
        return True
    if event.online_between and days[0].starts_at <= now <= days[-1].ends_at:
        return True
    return any(day.starts_at <= now <= day.ends_at for day in days)


def first_start(event):
    return event.days[0].starts_at if event.days else None


def last_end(event):
    return max(day.ends_at for day in event.days) if event.days else None


def state(event, issues=None, now=None):
    """'planned', 'running' or 'finished' (derived, never stored). Planned:
    the first day has not begun. Finished: the last day is over, or without
    days every issue is finished. Otherwise running."""
    from app import issues as issues_mod
    now = now or datetime.utcnow()
    if event.days:
        if now < first_start(event):
            return 'planned'
        if now > last_end(event):
            return 'finished'
        return 'running'
    rows = event.issues if issues is None else issues
    if rows and all(issues_mod.is_finished(i) for i in rows):
        return 'finished'
    return 'running'


def can_share(event, user):
    """Sharing and undoing it: only the organiser (no moderator)."""
    return _active(user) and event.created_by_id == user.id and event.archived_at is None


def share_card(event, user, now=None):
    """The card "The event is over" for the organiser: not shared, not
    dismissed, the last day over."""
    now = now or datetime.utcnow()
    return (can_share(event, user) and event.shared_at is None and event.share_dismissed_at is None
            and event.days and now > last_end(event))


def _log(event, user, action):
    from app import issues
    for issue in event.issues:
        issues.log_action(user, action, 'event', event.id, snapshot=event.name, issue_id=issue.id)


def share(event, user, now=None):
    event.shared_at = now or datetime.utcnow()
    event.shared_by_id = user.id
    _log(event, user, 'share_event')


def unshare(event, user):
    event.shared_at = None
    event.shared_by_id = None
    _log(event, user, 'unshare_event')


def dismiss_share(event, now=None):
    event.share_dismissed_at = now or datetime.utcnow()


def renew_code(event, user):
    """Nieuwe link maken (K2): a new code; the old one is not kept, so it
    gives the message for an unknown code."""
    event.code = new_code()
    _log(event, user, 'event_new_link')
    return event.code


def where(event):
    """Where, short, in the reader's language: the location, ", and online"
    for hybrid, "online" for online. The location itself is not translated."""
    from flask_babel import gettext
    if event.format == 'online':
        # TRANSLATORS: where an online event takes place, in a short label ("Event · 9 Jan 2027 14:00 · online").
        return gettext('online')
    if event.format == 'hybrid':
        # TRANSLATORS: where a hybrid event takes place: the location, then "and online" ("Forum, Groningen, and online").
        return gettext('%(place)s, and online', place=event.location or '')
    return event.location or ''


def issue_rows(issues_list):
    """[(issue, proposals, participants)] for a list of issues, in a fixed
    number of queries (as issues.overview)."""
    from app import issues
    return issues.with_counts(issues_list)


def your_events(user, sess=None, now=None):
    """Your events (K10) for the overview: planned and running events you
    take part in (logged in and took part or follow, or opened the link in
    this session) and the events you organise that are not shared. Returns
    [(event, organiser, was_there, state, n_issues)], in a fixed number of
    queries."""
    from flask import session
    from sqlalchemy.orm import selectinload
    from app.models import (Event, Issue, IssueFollow, IssueProposal, IssueProposalSupport, IssueProposalVote,
                            Post)
    sess = session if sess is None else sess
    seen = set(sess.get(ACCESS_KEY) or [])
    took_part, own = set(), set()
    if _active(user):
        uid = user.id
        posts = db.session.query(Post.thread_id).filter(Post.user_id == uid)
        proposals = db.session.query(IssueProposal.issue_id).filter(IssueProposal.created_by_id == uid)
        supports = db.session.query(IssueProposal.issue_id).join(
            IssueProposalSupport, IssueProposalSupport.proposal_id == IssueProposal.id).filter(
            IssueProposalSupport.user_id == uid)
        votes = db.session.query(IssueProposal.issue_id).join(
            IssueProposalVote, IssueProposalVote.proposal_id == IssueProposal.id).filter(
            IssueProposalVote.user_id == uid)
        follows = db.session.query(IssueFollow.issue_id).filter(IssueFollow.user_id == uid)
        took_part = {e for (e,) in db.session.query(Issue.event_id).filter(
            Issue.event_id.isnot(None),
            db.or_(Issue.created_by_id == uid, Issue.thread_id.in_(posts), Issue.id.in_(proposals),
                   Issue.id.in_(supports), Issue.id.in_(votes), Issue.id.in_(follows))).distinct()}
        own = {e for (e,) in db.session.query(Event.id).filter(
            Event.created_by_id == uid, Event.shared_at.is_(None), Event.archived_at.is_(None))}
    ids = (seen | took_part | own)
    if not ids:
        return []
    found = Event.query.options(selectinload(Event.days), selectinload(Event.issues)) \
        .filter(Event.id.in_(ids), Event.archived_at.is_(None)).all()
    from app import content_i18n
    content_i18n.preload(found, 'name')
    out = []
    for event in found:
        organiser = event.id in own
        st = state(event, now=now)
        if not organiser and st == 'finished':
            continue
        out.append((event, organiser, event.id in seen or organiser, st, len(event.issues)))
    out.sort(key=lambda row: (first_start(row[0]) is None, first_start(row[0]) or datetime.max, -row[0].id))
    return out


def qr(url, kind):
    """The QR code of `url` made here (segno, no network): SVG or PNG bytes."""
    import io
    import segno
    code = segno.make(url, error='m')
    out = io.BytesIO()
    if kind == 'svg':
        code.save(out, kind='svg', scale=6, border=2, xmldecl=False, svgns=True)
    else:
        code.save(out, kind='png', scale=10, border=2)
    return out.getvalue()


# ── Step 1e: the current event of the session and the bar (K9) ──────────

CURRENT_KEY = 'current_event'
_BAR = 'guf.event_bar'
_CURRENT = 'guf.current_event'


def enter(event):
    """The event becomes the current one of this session and gets the bar on
    this page. Not for a shared or archived event (K10): no bar, no session."""
    from flask import request, session
    if not enabled() or event is None or is_shared(event) or event.archived_at is not None:
        return
    session[CURRENT_KEY] = event.id
    request.environ[_BAR] = event
    request.environ[_CURRENT] = event


def leave():
    """"See the issues outside this event": the session forgets the current
    event; the access of K2b (event_access) stays."""
    from flask import session
    session.pop(CURRENT_KEY, None)


def current_event():
    """The current event of the session, only when it exists, is not shared
    and not archived; otherwise None (and the key goes). Once per request."""
    from flask import has_request_context, request, session
    if not enabled() or not has_request_context():
        return None
    if _CURRENT in request.environ:
        return request.environ[_CURRENT]
    event = None
    event_id = session.get(CURRENT_KEY)
    if event_id is not None:
        from app.models import Event
        event = db.session.get(Event, event_id)
        if event is None or is_shared(event) or event.archived_at is not None:
            session.pop(CURRENT_KEY, None)
            event = None
    request.environ[_CURRENT] = event
    return event


def bar_event():
    """The event of the bar on this page, or None."""
    from flask import has_request_context, request
    if not has_request_context():
        return None
    return request.environ.get(_BAR)


def inside(issue):
    """An issue of an event that is not shared, with EVENTS on: no breadcrumb
    from Have your say; the bar takes its place."""
    return (enabled() and issue is not None and issue.event_id is not None
            and issue.event is not None and not is_shared(issue.event))
