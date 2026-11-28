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
