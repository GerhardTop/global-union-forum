"""
Events step 1b (docs/events.md): the data model, the transition of the event
issues of before (events.attach_legacy / attach_all_legacy, K12),
is_open_now via the event, the code of the link, the translation of name and
note (content_i18n, content_jobs) and delete_empty.

Shared conftest app; every test makes its own data with unique texts and
removes what it committed.
"""
import os
import uuid
from datetime import datetime, timedelta

import pytest

import config
from app import content_i18n, content_jobs, events, issues
from app.models import ContentTranslation, Event, EventDay, Issue, IssueEventDay
from tests.test_delete_issue import _clean_session, _cleanup  # noqa: F401  (autouse fixture)
from tests.test_issue_polish import _client, _req, make_user  # noqa: F401  (fixture)


def _uid():
    return uuid.uuid4().hex[:8]


def _legacy(db, user, phase='feiten', closed=False, days=(), location=None, name=None,
            note=None, between=None, kind='event'):
    """An issue as before step 1b: the event data on the issue, no event."""
    issue = Issue(title=f'Legacy {_uid()}', intro='Uitleg.', kind=kind, phase=phase, is_closed=closed,
                  created_by_id=user.id, created_at=datetime(2026, 5, 1, 12, 0), lang='nl',
                  event_name=name, event_note=note, event_location=location, event_online_between=between)
    db.session.add(issue)
    for begin, end in days:
        issue.event_days.append(IssueEventDay(starts_at=begin, ends_at=end))
    db.session.commit()
    assert issue.event_id is None
    return issue


def _event_issue(db, user, **values):
    issue = issues.create({'title': f'Event {_uid()}', 'intro': 'Uitleg.', 'kind': 'event',
                           'event_name': f'Avond {_uid()}', **values}, user, lang='nl')
    db.session.commit()
    return issue


DAY1 = (datetime(2026, 12, 12, 18, 0), datetime(2026, 12, 12, 21, 0))
DAY2 = (datetime(2026, 12, 14, 18, 0), datetime(2026, 12, 14, 21, 0))


# ── The transition (K12) ─────────────────────────────────────────────────

def test_attach_all_legacy_makes_one_event_per_event_issue(app, db, make_user):
    user = make_user()
    name, note = f'Avond {_uid()}', f'Toelichting {_uid()}'
    plain = _legacy(db, user, days=(DAY1, DAY2), location='Forum, Groningen', name=name, note=note, between=True)
    voting = _legacy(db, user, phase='stemmen')
    closed = _legacy(db, user, closed=True)
    online = _legacy(db, user, kind='online')
    mine = (plain, voting, closed, online)
    try:
        made = events.attach_all_legacy()
        assert made >= 3
        for issue in mine:
            db.session.refresh(issue)
        assert online.event_id is None
        event = plain.event
        assert (event.name, event.note, event.location, event.format) == (name, note, 'Forum, Groningen', 'location')
        assert event.online_between is True and event.online_url is None
        assert event.everyone_votes is False and event.open_for_issues is False
        assert event.everyone_votes_locked_at is None
        assert event.shared_at == plain.created_at and event.shared_by_id is None
        assert event.created_by_id == user.id and event.created_at == plain.created_at and event.lang == 'nl'
        assert len(event.code) == events.CODE_LENGTH
        assert [(d.starts_at, d.ends_at) for d in event.days] == [DAY1, DAY2]
        assert plain.event_position == 1 and event.issues == [plain]
        # Without event_name the title; without a location online; voting begun: locked.
        assert voting.event.name == voting.title and voting.event.format == 'online'
        assert voting.event.days == []
        assert voting.event.everyone_votes_locked_at is not None
        assert closed.event.everyone_votes_locked_at is not None
        before = Event.query.count()
        assert events.attach_all_legacy() == 0 and Event.query.count() == before
        # Afterwards: kind is 'event' exactly when event_id is set.
        assert Issue.query.filter(Issue.kind == 'event', Issue.event_id.is_(None)).count() == 0
        assert Issue.query.filter(Issue.kind != 'event', Issue.event_id.isnot(None)).count() == 0
    finally:
        for issue in mine:
            _cleanup(issue.id)


def test_the_name_is_cut_at_200(app, db, make_user):
    issue = _legacy(db, make_user(), name='x' * 200)
    issue.title = 'y' * 250
    issue.event_name = None
    db.session.commit()
    try:
        event = events.attach_legacy(issue)
        db.session.commit()
        assert event.name == 'y' * 200
        assert events.attach_legacy(issue) is None
    finally:
        _cleanup(issue.id)


# ── is_open_now, the same as before the transition ──────────────────────

@pytest.mark.parametrize('days,between', [((), None), ((DAY1, DAY2), False), ((DAY1, DAY2), True),
                                          ((DAY1,), True)])
def test_is_open_now_is_the_same_after_the_transition(app, db, make_user, days, between):
    issue = _legacy(db, make_user(), days=days, between=between)
    moments = [datetime(2026, 12, 1), DAY1[0] + timedelta(hours=1), datetime(2026, 12, 13, 12, 0),
               DAY2[0] + timedelta(minutes=5), datetime(2026, 12, 20)]
    try:
        before = [issues.is_open_now(issue, now) for now in moments]
        events.attach_legacy(issue)
        db.session.commit()
        db.session.refresh(issue)
        assert issue.event is not None
        # The event decides now: the old days on the issue no longer count.
        issue.event_online_between = not between
        assert [issues.is_open_now(issue, now) for now in moments] == before
    finally:
        db.session.rollback()
        _cleanup(issue.id)


def test_is_open_now_reads_the_days_of_the_event(app, db, make_user):
    issue = _event_issue(db, make_user(), event_days=[DAY1])
    try:
        assert issues.is_open_now(issue, DAY1[0] + timedelta(hours=1))
        issue.event.days[0].starts_at = datetime(2027, 1, 1, 18, 0)
        issue.event.days[0].ends_at = datetime(2027, 1, 1, 21, 0)
        db.session.flush()
        assert not issues.is_open_now(issue, DAY1[0] + timedelta(hours=1))
    finally:
        db.session.rollback()
        _cleanup(issue.id)


# ── issues.create ────────────────────────────────────────────────────────

def test_create_gives_an_event_issue_its_event_right_away(app, db, make_user):
    user = make_user()
    event_issue = _event_issue(db, user, event_days=[DAY1], event_location='Buurthuis')
    online = issues.create({'title': f'Online {_uid()}', 'intro': 'Uitleg.', 'kind': 'online'}, user, lang='nl')
    db.session.commit()
    try:
        event = event_issue.event
        assert event is not None and event.name == event_issue.event_name and event.location == 'Buurthuis'
        assert [(d.starts_at, d.ends_at) for d in event.days] == [DAY1]
        assert event_issue.event_position == 1 and event.everyone_votes is False
        # The old data on the issue is still filled (the display reads it until 1d and 1e).
        assert [(d.starts_at, d.ends_at) for d in event_issue.event_days] == [DAY1]
        assert online.event_id is None
    finally:
        _cleanup(event_issue.id)
        _cleanup(online.id)


# ── The code ─────────────────────────────────────────────────────────────

def test_new_code_uses_the_alphabet_and_is_unique(app, db):
    assert not set('0O1IL') & set(events.CODE_ALPHABET)
    codes = [events.new_code() for _ in range(200)]
    assert all(len(c) == 8 and set(c) <= set(events.CODE_ALPHABET) for c in codes)
    assert len(set(codes)) == len(codes)


def test_new_code_tries_again_on_a_collision(app, db, make_user, monkeypatch):
    issue = _event_issue(db, make_user())
    taken = issue.event.code
    picks = iter(taken + 'ABCDEFGH')
    monkeypatch.setattr(events.secrets, 'choice', lambda alphabet: next(picks))
    try:
        assert events.new_code() == 'ABCDEFGH'
    finally:
        monkeypatch.undo()
        _cleanup(issue.id)


def test_normalize_code():
    assert events.normalize_code(' k7m2-qxpa ') == 'K7M2QXPA'
    assert events.normalize_code('k7m2 qxpa') == 'K7M2QXPA'
    assert events.normalize_code(None) == ''


def test_the_switch_is_off_by_default(app, monkeypatch):
    assert config.Config.EVENTS is (os.environ.get('EVENTS', '') == '1')
    monkeypatch.setitem(app.config, 'EVENTS', False)
    assert events.enabled(app) is False
    monkeypatch.setitem(app.config, 'EVENTS', True)
    assert events.enabled(app) is True


# ── Translation of name and note ─────────────────────────────────────────

def test_name_and_note_are_translated_content(app, db, make_user):
    model, fields = content_jobs._models()['events']
    assert model is Event and fields == ('name', 'note')
    assert 'events' in content_jobs.BACKLOG_KINDS and 'events' in content_jobs.backlog()
    issue = _event_issue(db, make_user(), event_note=f'Toelichting {_uid()}')
    event = issue.event
    try:
        assert content_i18n.source_lang(event) == 'nl'
        assert content_i18n.field_kind(event, 'name') == 'short'
        assert content_i18n.field_kind(event, 'note') == 'text'
        others = [lang for lang in config.ENABLED_LANGUAGES if lang != 'nl']
        assert {(field, lang) for _t, field, lang in content_jobs.missing(event)} == \
            {(field, lang) for field in ('name', 'note') for lang in others}
        content_i18n.set_translation(event, 'name', 'en', 'An evening')
        db.session.commit()
        assert ContentTranslation.query.filter_by(object_type='events', object_id=event.id).count() == 1
    finally:
        event_id = event.id
        _cleanup(issue.id)
    assert db.session.get(Event, event_id) is None
    assert ContentTranslation.query.filter_by(object_type='events', object_id=event_id).count() == 0
    assert 'events' not in content_i18n.orphans()


def test_an_orm_delete_of_an_event_takes_its_translations(app, db, make_user):
    event = Event(code=events.new_code(), name=f'Los {_uid()}', lang='nl')
    db.session.add(event)
    db.session.commit()
    content_i18n.set_translation(event, 'name', 'en', 'Loose')
    db.session.commit()
    event_id = event.id
    db.session.delete(event)
    db.session.commit()
    assert ContentTranslation.query.filter_by(object_type='events', object_id=event_id).count() == 0


# ── delete_empty ─────────────────────────────────────────────────────────

def test_purging_the_only_issue_removes_the_event(app, db, make_user):
    issue = _event_issue(db, make_user(), event_days=[DAY1, DAY2])
    event_id = issue.event_id
    _cleanup(issue.id)
    assert db.session.get(Event, event_id) is None
    assert EventDay.query.filter_by(event_id=event_id).count() == 0


def test_an_event_with_two_issues_stays_after_one_goes(app, db, make_user):
    user = make_user()
    first = _event_issue(db, user)
    event_id, first_id = first.event_id, first.id
    second = Issue(title=f'Tweede {_uid()}', intro='Uitleg.', kind='event', created_by_id=user.id, lang='nl',
                   event_id=event_id, event_position=2)
    db.session.add(second)
    db.session.commit()
    second_id = second.id
    try:
        assert [i.id for i in db.session.get(Event, event_id).issues] == [first_id, second_id]
        _cleanup(first_id)
        assert db.session.get(Event, event_id) is not None
    finally:
        _cleanup(first_id)
        _cleanup(second_id)
    assert db.session.get(Event, event_id) is None


def test_delete_wrappers_takes_an_empty_event_along(app, db, make_user):
    issue = _event_issue(db, make_user())
    event_id, issue_id = issue.event_id, issue.id
    try:
        issues.delete_wrappers([issue_id])
        db.session.commit()
        assert db.session.get(Event, event_id) is None
    finally:
        _cleanup(issue_id)


# ── /admin/vertalingen: the row Events ───────────────────────────────────

LABELS = {'nl': 'Events', 'en': 'Events', 'de': 'Veranstaltungen', 'es': 'Eventos', 'fr': 'Événements'}


@pytest.mark.parametrize('lang', sorted(LABELS))
def test_the_backlog_page_has_a_row_events(app, db, make_user, lang):
    page = _req(_client(app, make_user(admin=True), lang), 'GET', '/admin/vertalingen').get_data(as_text=True)
    row = page.split('data-backlog="events-')[0].rsplit('<tr', 1)[1]
    assert LABELS[lang] in row
