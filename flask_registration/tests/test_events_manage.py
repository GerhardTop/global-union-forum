"""
Events step 1c (docs/events.md, K3, K4, K5, K7, K13): making and managing an
event. The form "New event" (/events/nieuw), the event settings
(/e/<code>/instellingen) with the order of the issues, the issue form with
"Which event?", and the label of an event issue read from the event. All
only with EVENTS on; with it off nothing changes.

Shared conftest app (CSRF off); every test makes its own data with unique
texts. make_user removes the users with their issues and events.
"""
import html
import uuid
from datetime import datetime

import pytest

from app import content_i18n, content_jobs, db as _db, events, issues
from app.models import ContentTranslation, Event, Issue
from tests.test_delete_issue import _clean_session  # noqa: F401  (autouse fixture)
from tests.test_issue_polish import _client, _req, make_user  # noqa: F401  (fixture)


def _uid():
    return uuid.uuid4().hex[:8]


@pytest.fixture
def on(app, monkeypatch):
    monkeypatch.setitem(app.config, 'EVENTS', True)


@pytest.fixture
def scheduled(monkeypatch):
    """content_jobs.schedule, recorded (the background start is off in tests anyway)."""
    calls = []
    real = content_jobs.schedule
    monkeypatch.setattr(content_jobs, 'schedule', lambda obj: (calls.append(obj), real(obj)))
    return calls


def _form(**over):
    data = {'name': f'Avond {_uid()}', 'note': '', 'format': 'location', 'location': 'Forum, Groningen',
            'online_url': '', 'event_day': '2027-01-15', 'event_from': '19:00', 'event_to': '22:00',
            'everyone_votes': '1'}
    data.update(over)
    return data


def _event(user, **over):
    values, errors = events.parse_form(_FakeForm(_form(**over)))
    assert not errors, errors
    event = events.create(values, user, lang='nl')
    _db.session.commit()
    return event


class _FakeForm(dict):
    def getlist(self, key):
        value = self.get(key)
        return value if isinstance(value, list) else ([] if value is None else [value])


def _issue_in(event, user, title=None):
    issue = issues.create({'title': title or f'Vraag {_uid()}', 'intro': 'Uitleg.', 'kind': 'event',
                           'event_id': event.id}, user, lang='nl')
    _db.session.commit()
    return issue


def _page(client, path):
    resp = _req(client, 'GET', path)
    assert resp.status_code == 200, (path, resp.status_code)
    return html.unescape(resp.get_data(as_text=True))


# ── With EVENTS off nothing changes ──────────────────────────────────────

def test_with_events_off_the_routes_are_404_and_the_forms_unchanged(app, make_user):
    user = make_user()
    event = _event(user)
    client = _client(app, user)
    assert _req(client, 'GET', '/events/nieuw').status_code == 404
    assert _req(client, 'POST', '/events/nieuw', data=_form()).status_code == 404
    assert _req(client, 'GET', f'/e/{event.code}/instellingen').status_code == 404
    assert _req(client, 'POST', f'/e/{event.code}/volgorde', data={'issue_id': '1', 'direction': 'up'}).status_code == 404
    overview = _page(client, '/gesprekken')
    assert 'Nieuw event' not in overview and '/events/nieuw' not in overview
    form = _page(client, '/gesprekken/nieuw')
    assert 'name="event_name"' in form and 'name="event_location"' in form and 'name="event_id"' not in form
    assert 'Bij welk event?' not in form


# ── Making an event ──────────────────────────────────────────────────────

def test_new_event_form_follows_the_artboard(app, make_user, on):
    page = _page(_client(app, make_user()), '/events/nieuw')
    page = page[page.index('<main'):]
    marks = ['Denk mee', 'Nieuw event', 'Een event is een bijeenkomst op vaste dagen en tijden',
             'Naam van het event', 'Toelichting', 'Waar vindt het event plaats?', 'Op een locatie',
             'Op een locatie en online', 'Locatie', 'Link naar het videogesprek of de livestream', 'Dagen',
             'Online bijdragen open tussen de eerste en laatste dag', 'Deelnemers mogen vraagstukken toevoegen',
             'Stemrecht', 'Iedereen mag stemmen', 'Zichtbaarheid',
             'Dit event is alleen zichtbaar voor de deelnemers aan dit event. Je nodigt ze uit met de link of de QR-code.',
             'Na het opslaan krijg je een eigen link naar het event en een QR-code', 'Event maken']
    found = [page.index(m) for m in marks]
    assert found == sorted(found)
    assert '(na 3 kwaliteitsbijdragen)' in page
    # Defaults: everyone may vote on, participants may add issues off.
    assert 'name="everyone_votes" value="1" checked' in page
    assert 'name="open_for_issues" value="1">' in page
    assert 'name="format" value="location" checked' in page


def test_making_a_valid_event(app, make_user, on, scheduled):
    user = make_user()
    data = _form(format='hybrid', online_url='https://meet.example.org/abc', note='Een avond.', open_for_issues='1',
                 event_day=['2027-01-15', '2027-01-16'], event_from=['19:00', '10:00'], event_to=['22:00', '12:00'])
    resp = _req(_client(app, user), 'POST', '/events/nieuw', data=data)
    event = Event.query.filter_by(name=data['name']).one()
    assert resp.status_code == 303 and resp.location.endswith(f'/e/{event.code}')   # step 1d: the event page
    assert len(event.code) == 8 and set(event.code) <= set(events.CODE_ALPHABET)
    assert (event.lang, event.created_by_id, event.format) == ('nl', user.id, 'hybrid')
    assert (event.location, event.online_url, event.note) == ('Forum, Groningen', 'https://meet.example.org/abc', 'Een avond.')
    assert event.everyone_votes is True and event.open_for_issues is True and event.online_between is False
    assert len(event.days) == 2 and event.shared_at is None
    assert event in scheduled


def test_codes_not_labels_and_the_unused_field_is_empty(app, make_user, on):
    user = make_user()
    online = _form(format='online', location='Vergeten zaal', online_url='https://x.example.org')
    _req(_client(app, user), 'POST', '/events/nieuw', data=online)
    event = Event.query.filter_by(name=online['name']).one()
    assert event.format == 'online' and event.location is None
    at_location = _form(online_url='https://x.example.org')
    _req(_client(app, user), 'POST', '/events/nieuw', data=at_location)
    event = Event.query.filter_by(name=at_location['name']).one()
    assert event.format == 'location' and event.online_url is None


@pytest.mark.parametrize('over,message', [
    ({'name': ''}, 'Geef het event een naam van hoogstens 200 tekens.'),
    ({'name': 'x' * 201}, 'Geef het event een naam van hoogstens 200 tekens.'),
    ({'event_day': ''}, 'Vul minstens één dag in.'),
    ({'location': ''}, 'Vul de locatie in, van hoogstens 200 tekens.'),
    ({'format': 'hybrid', 'location': ''}, 'Vul de locatie in, van hoogstens 200 tekens.'),
    ({'format': 'online', 'online_url': 'ftp://x.example.org'}, 'Vul een link in die begint met http:// of https://.'),
    ({'format': 'online', 'online_url': 'meet.example.org'}, 'Vul een link in die begint met http:// of https://.'),
    ({'format': 'zaal'}, 'Kies waar het event plaatsvindt.'),
])
def test_errors_per_field_keep_what_was_filled_in(app, make_user, on, over, message):
    client = _client(app, make_user())
    data = _form(note=f'Bewaard {_uid()}', **over)
    before = Event.query.count()
    resp = _req(client, 'POST', '/events/nieuw', data=data)
    assert resp.status_code == 303 and resp.location.endswith('/events/nieuw')
    assert Event.query.count() == before
    page = _page(client, '/events/nieuw')
    assert message in page and data['note'] in page
    # PRG: the second GET is empty again.
    assert message not in _page(client, '/events/nieuw')


def test_without_online_url_an_online_event_is_fine(app, make_user, on):
    user = make_user()
    data = _form(format='online', location='')
    assert _req(_client(app, user), 'POST', '/events/nieuw', data=data).status_code == 303
    assert Event.query.filter_by(name=data['name']).one().online_url is None


# ── Rights ───────────────────────────────────────────────────────────────

def test_visitor_goes_to_login(app, on):
    resp = _req(_client(app), 'GET', '/events/nieuw')
    assert resp.status_code == 302 and '/login' in resp.location


def test_without_the_conduct_rules_first_the_rules(app, db, make_user, on):
    user = make_user()
    user.rules_accepted_at = None
    db.session.commit()
    client = _client(app, user)
    resp = _req(client, 'GET', '/events/nieuw')
    assert resp.status_code == 302 and 'spelregels' in resp.location
    data = _form()
    _req(client, 'POST', '/events/nieuw', data=data)
    assert Event.query.filter_by(name=data['name']).count() == 0


def test_suspended_is_refused(app, db, make_user, on):
    from datetime import timedelta
    user = make_user()
    user.suspended_until = datetime.utcnow() + timedelta(days=3)
    db.session.commit()
    client = _client(app, user)
    resp = _req(client, 'GET', '/events/nieuw')
    assert resp.status_code == 302 and 'spelregels' not in resp.location
    data = _form()
    _req(client, 'POST', '/events/nieuw', data=data)
    assert Event.query.filter_by(name=data['name']).count() == 0
    assert not events.can_create(user)


def test_settings_for_organiser_and_moderator_not_for_another_member(app, make_user, on):
    organiser, moderator, other = make_user(), make_user('moderator'), make_user()
    event = _event(organiser)
    path = f'/e/{event.code}/instellingen'
    assert _req(_client(app, organiser), 'GET', path).status_code == 200
    assert _req(_client(app, moderator), 'GET', path).status_code == 200
    assert _req(_client(app, other), 'GET', path).status_code == 403
    assert _req(_client(app, other), 'POST', path, data=_form()).status_code == 403
    # The code is case and space insensitive; an unknown one is a 404.
    assert _req(_client(app, organiser), 'GET', f'/e/{event.code.lower()}/instellingen').status_code == 200
    assert _req(_client(app, organiser), 'GET', '/e/AAAAAAAA/instellingen').status_code == 404


def test_a_former_account_manages_nothing(app, db, make_user, on):
    organiser = make_user()
    event = _event(organiser)
    assert events.can_manage(event, organiser)
    organiser.deleted_at = datetime.utcnow()
    assert not events.can_manage(event, organiser) and not events.can_add_issue(event, organiser)
    organiser.deleted_at = None
    db.session.commit()


# ── The settings ─────────────────────────────────────────────────────────

def test_settings_show_the_saved_values_and_save_changes(app, make_user, on, scheduled):
    organiser = make_user()
    event = _event(organiser, note='Eerste toelichting.')
    content_i18n.set_translation(event, 'name', 'en', 'An evening')
    _db.session.commit()
    client = _client(app, organiser)
    page = _page(client, f'/e/{event.code}/instellingen')
    assert f'value="{event.name}"' in page and 'Eerste toelichting.' in page and 'value="2027-01-15"' in page
    assert 'Denk mee' in page and 'Instellingen' in page and 'Opslaan' in page
    new_name = f'Nieuwe naam {_uid()}'
    data = _form(name=new_name, note='Eerste toelichting.', format='online', location='', open_for_issues='1')
    resp = _req(client, 'POST', f'/e/{event.code}/instellingen', data=data)
    assert resp.status_code == 303
    _db.session.expire_all()
    event = _db.session.get(Event, event.id)
    assert (event.name, event.format, event.location, event.open_for_issues) == (new_name, 'online', None, True)
    assert ContentTranslation.query.filter_by(object_type='events', object_id=event.id, field='name').count() == 0
    assert event in scheduled


def test_a_locked_everyone_votes_cannot_change(app, make_user, on):
    organiser = make_user()
    event = _event(organiser)
    event.everyone_votes = False
    event.everyone_votes_locked_at = datetime.utcnow()
    _db.session.commit()
    client = _client(app, organiser)
    page = _page(client, f'/e/{event.code}/instellingen')
    assert 'name="everyone_votes" value="1" disabled' in page
    assert 'Vastgelegd sinds het stemmen begon.' in page
    _req(client, 'POST', f'/e/{event.code}/instellingen', data=_form(name=event.name, everyone_votes='1'))
    _db.session.expire_all()
    assert _db.session.get(Event, event.id).everyone_votes is False


def test_not_locked_everyone_votes_can_change(app, make_user, on):
    organiser = make_user()
    event = _event(organiser)
    client = _client(app, organiser)
    assert 'Vastgelegd sinds het stemmen begon.' not in _page(client, f'/e/{event.code}/instellingen')
    data = _form(name=event.name)
    data.pop('everyone_votes')
    _req(client, 'POST', f'/e/{event.code}/instellingen', data=data)
    _db.session.expire_all()
    assert _db.session.get(Event, event.id).everyone_votes is False


def test_order_up_and_down(app, make_user, on):
    organiser = make_user()
    event = _event(organiser)
    first, second, third = (_issue_in(event, organiser) for _ in range(3))
    ids = [first.id, second.id, third.id]
    client = _client(app, organiser)
    page = _page(client, f'/e/{event.code}/instellingen')
    assert 'Vraagstukken van dit event' in page and f'aria-label="Omhoog: {first.title}"' in page
    assert f'/gesprekken/nieuw?event={event.code}' in page and 'Nieuw vraagstuk bij dit event' in page

    def order():
        _db.session.expire_all()
        rows = Issue.query.filter_by(event_id=event.id).order_by(Issue.event_position, Issue.id).all()
        assert [i.event_position for i in rows] == [1, 2, 3]
        return [i.id for i in rows]

    def move(issue_id, direction):
        resp = _req(client, 'POST', f'/e/{event.code}/volgorde', data={'issue_id': issue_id, 'direction': direction})
        assert resp.status_code == 303

    assert order() == ids
    move(ids[0], 'up')            # the top edge does nothing
    assert order() == ids
    move(ids[2], 'down')          # the bottom edge does nothing
    assert order() == ids
    move(ids[0], 'down')
    assert order() == [ids[1], ids[0], ids[2]]
    move(ids[2], 'up')
    assert order() == [ids[1], ids[2], ids[0]]
    other = make_user()
    assert _req(_client(app, other), 'POST', f'/e/{event.code}/volgorde',
                data={'issue_id': ids[0], 'direction': 'up'}).status_code == 403


# ── The issue form with "Which event?" ──────────────────────────────────

def _new_issue(client, event_id, path='/gesprekken/nieuw'):
    title = f'Vraag {_uid()}'
    resp = _req(client, 'POST', path, data={'title': title, 'intro': 'Uitleg.', 'kind': 'event',
                                            'event_id': str(event_id)})
    return resp, Issue.query.filter_by(title=title).first()


def test_the_issue_form_chooses_an_event(app, make_user, on):
    user = make_user()
    event = _event(user)
    client = _client(app, user)
    form = _page(client, '/gesprekken/nieuw')
    assert 'Bij welk event?' in form and f'<option value="{event.id}"' in form and event.name in form
    assert 'name="event_name"' not in form and 'name="event_location"' not in form
    assert '<input type="date" name="event_day"' not in form
    first = _issue_in(event, user)
    resp, issue = _new_issue(client, event.id)
    assert resp.status_code == 302 and issue is not None
    assert (issue.kind, issue.event_id, issue.event_position) == ('event', event.id, first.event_position + 1)
    assert issue.event_days == [] and issue.event_name is None and issue.event_location is None
    assert Event.query.filter_by(created_by_id=user.id).count() == 1   # attach_legacy made nothing


def test_another_members_event_only_with_open_for_issues(app, make_user, on):
    organiser, member = make_user(), make_user()
    event = _event(organiser)
    client = _client(app, member)
    # Not open: not in the list (not even with ?event=), and a forged event_id is refused.
    form = _page(client, f'/gesprekken/nieuw?event={event.code}')
    assert f'<option value="{event.id}"' not in form
    assert 'Je hebt nog geen event. Maak eerst een event.' in form and '/events/nieuw' in form
    resp, issue = _new_issue(client, event.id)
    assert issue is None and resp.status_code == 303
    assert 'Kies een event.' in _page(client, '/gesprekken/nieuw')
    # Open for issues: with ?event= it is chosen already.
    event.open_for_issues = True
    _db.session.commit()
    form = _page(client, f'/gesprekken/nieuw?event={event.code}')
    assert f'<option value="{event.id}" selected' in form
    assert 'name="kind" value="event" checked' in form
    resp, issue = _new_issue(client, event.id, f'/gesprekken/nieuw?event={event.code}')
    assert issue is not None and issue.event_id == event.id
    # Without ?event= the open event of someone else is not offered.
    assert f'<option value="{event.id}"' not in _page(client, '/gesprekken/nieuw')
    # Archived: nobody adds to it.
    event.archived_at = datetime.utcnow()
    _db.session.commit()
    assert not events.can_add_issue(event, organiser)
    resp, issue = _new_issue(_client(app, organiser), event.id)
    assert issue is None


def test_with_events_off_the_issue_form_keeps_its_event_fields(app, make_user):
    user = make_user()
    client = _client(app, user)
    title = f'Vraag {_uid()}'
    resp = _req(client, 'POST', '/gesprekken/nieuw', data={
        'title': title, 'intro': 'Uitleg.', 'kind': 'event', 'event_name': 'Oude avond', 'event_location': 'Zaal',
        'event_day': '2027-02-01', 'event_from': '19:00', 'event_to': '21:00'})
    assert resp.status_code == 302
    issue = Issue.query.filter_by(title=title).one()
    assert issue.event_name == 'Oude avond' and issue.event is not None and issue.event.name == 'Oude avond'


# ── The label of an event issue reads the event ─────────────────────────

def test_the_label_and_the_cockpit_read_the_event(app, make_user, on):
    user = make_user()
    event = _event(user, location='Buurthuis De Kroon')
    issue = _issue_in(event, user)
    events.share(event, user)   # step 1d: only a shared event is in the overview
    _db.session.commit()
    overview = _page(_client(app, user), '/gesprekken')
    row = overview.split(issue.title)[0].rsplit('<li', 1)[1]
    assert event.name in row and 'Buurthuis De Kroon' in row and '15 jan 2027 19:00' in row
    cockpit = _page(_client(app, user), f'/vraagstuk/{issue.id}')
    assert event.name in cockpit and 'vrijdag 15 januari 2027, 19:00' in cockpit
    overview_en = _page(_client(app, user, 'en'), '/gesprekken')
    assert 'Nieuw event' not in overview_en and 'New event' in overview_en


# ── The new texts in every language ──────────────────────────────────────

TEXTS = {
    'nl': ('Nieuw event', 'Iedereen mag stemmen', 'Waar vindt het event plaats?'),
    'en': ('New event', 'Everyone may vote', 'Where does the event take place?'),
    'de': ('Neue Veranstaltung', 'Alle dürfen abstimmen', 'Wo findet die Veranstaltung statt?'),
    'es': ('Nuevo evento', 'Todos pueden votar', '¿Dónde tiene lugar el evento?'),
    'fr': ('Nouvel événement', 'Tout le monde peut voter', "Où a lieu l'événement ?"),
}


@pytest.mark.parametrize('lang', sorted(TEXTS))
def test_the_form_in_every_language(app, make_user, on, lang):
    user = make_user()
    event = _event(user)
    client = _client(app, user, lang)
    page = _page(client, '/events/nieuw')
    for text in TEXTS[lang]:
        assert text in page, (lang, text)
    if lang != 'nl':
        for dutch in ('Iedereen mag stemmen', 'Zichtbaarheid', 'Deelnemers mogen', 'Event maken'):
            assert dutch not in page, (lang, dutch)
        settings = _page(client, f'/e/{event.code}/instellingen')
        assert 'Vraagstukken van dit event' not in settings and TEXTS[lang][1] in settings
