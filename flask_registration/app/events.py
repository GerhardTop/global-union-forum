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
