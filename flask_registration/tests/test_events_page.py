"""
Events step 1d (docs/events.md, K2, K2b, K10): the event page /e/<code> with
the organiser's strip (link, code, QR code, a new link), access to the issues
of an event that is not shared, sharing after the last day, and Have your
say with Your events, the event code, For everyone and From events. All only
with EVENTS on; with it off the existing tests and page_snapshot hold.

Shared conftest app (CSRF off); every test makes its own data with unique
texts. make_user removes the users with their issues and events.
"""
import html
import re
from datetime import datetime, timedelta

import pytest

from app import db as _db, events, issue_lifecycle, issues
from app.models import Event, Issue, IssueFollow, IssueProposal, ModerationAction
from tests.test_delete_issue import _clean_session  # noqa: F401  (autouse fixture)
from tests.test_events_manage import _event, _issue_in, _uid
from tests.test_issue_polish import _client, _req, make_user  # noqa: F401  (fixture)
from tests.test_proposal_queries import _count_queries


@pytest.fixture
def on(app, monkeypatch):
    monkeypatch.setitem(app.config, 'EVENTS', True)


def _get(client, path):
    resp = _req(client, 'GET', path)
    return resp.status_code, html.unescape(resp.get_data(as_text=True))


def _day(days_from_now, hours=3):
    start = datetime.utcnow().replace(microsecond=0) + timedelta(days=days_from_now)
    return start.strftime('%Y-%m-%d')


def _past_event(user, **over):
    """An event whose only day lies in the past (the share card applies)."""
    event = _event(user, **over)
    event.days[0].starts_at = datetime.utcnow() - timedelta(days=3, hours=3)
    event.days[0].ends_at = datetime.utcnow() - timedelta(days=3)
    _db.session.commit()
    return event


# ── The page ─────────────────────────────────────────────────────────────

def test_page_for_a_visitor(app, make_user, on):
    organiser = make_user()
    event = _event(organiser, format='hybrid', online_url='https://meet.example.org/x', note=f'Toelichting {_uid()}')
    issue = _issue_in(event, organiser)
    status, page = _get(_client(app), f'/e/{event.code}')
    assert status == 200
    main = page[page.index('<main'):]
    marks = ['Event', event.name, 'Vrijdag 15 januari 2027, 19:00', 'Forum, Groningen, en online',
             'Meedoen via het videogesprek', f'Organisator: {organiser.public_name}', event.note,
             'Vraagstukken van dit event', issue.title, 'ingebracht door', '0 voorstellen',
             'Hier staan alleen de vraagstukken van dit event.', 'Bekijk de vraagstukken buiten dit event']
    found = [main.index(m) for m in marks]
    assert found == sorted(found)
    assert 'Vrijdag 15 januari 2027, 19:00 tot 22:00' in main
    assert 'href="https://meet.example.org/x" target="_blank" rel="noopener nofollow"' in main
    assert 'Alleen zichtbaar voor jou als organisator' not in main and 'Instellingen' not in main
    assert 'Nieuw vraagstuk bij dit event' not in main and '/qr.svg' not in main
    assert '<meta name="robots" content="noindex">' in page
    assert 'stemrecht' not in main.lower()       # K8: no message about the right to vote


def test_page_without_issues_and_a_closed_platform(app, make_user, on):
    event = _event(make_user(), event_day=_day(10))
    _status, page = _get(_client(app), f'/e/{event.code}')
    assert 'Er zijn nog geen vraagstukken bij dit event.' in page
    assert 'Het platform is nu niet open voor bijdragen. Zie de dagen van het event.' in page


def test_the_strip_only_for_whoever_manages(app, make_user, on):
    organiser, moderator, member = make_user(), make_user('moderator'), make_user()
    event = _event(organiser)
    for user in (organiser, moderator):
        _status, page = _get(_client(app, user), f'/e/{event.code}')
        assert 'Alleen zichtbaar voor jou als organisator' in page
        assert f'value="http://localhost/e/{event.code}"' in page and 'readonly' in page
        assert 'aria-label="Link kopiëren" title="Link kopiëren"' in page and 'data-copied="Link gekopieerd"' in page
        assert 'aria-live="polite"' in page
        assert 'Deel de link of de QR-code met de deelnemers' in page
        assert f'Eventcode: <strong>{event.code}</strong>' in page and '(om in te typen bij Denk mee)' in page
        assert f'/e/{event.code}/nieuwe-link' in page and 'Nieuwe link maken' in page
        assert 'Na afloop kies je of je het event deelt met alle deelnemers van Global Union.' in page
        assert 'Iedereen mag stemmen: aan.' in page and 'Wijzigen' in page
        assert f'src="/e/{event.code}/qr.svg"' in page and 'alt="QR-code van de link naar dit event"' in page
        assert 'Scan om mee te doen' in page and 'aria-label="QR-code kopiëren"' in page
        assert f'href="/e/{event.code}/qr.png" download=' in page and 'aria-label="QR-code downloaden"' in page
        assert 'Nieuw vraagstuk bij dit event' in page and f'/e/{event.code}/instellingen' in page
    _status, page = _get(_client(app, member), f'/e/{event.code}')
    assert 'Alleen zichtbaar voor jou als organisator' not in page and 'Nieuw vraagstuk bij dit event' not in page
    event.open_for_issues = True
    _db.session.commit()
    _status, page = _get(_client(app, member), f'/e/{event.code}')
    assert 'Nieuw vraagstuk bij dit event' in page and 'Instellingen' not in page


def test_unknown_code_and_a_lower_case_code(app, make_user, on):
    status, page = _get(_client(app), '/e/AAAAAAAA')
    assert status == 404 and 'Deze link werkt niet meer. Vraag de organisator om de nieuwe link.' in page
    assert 'href="/gesprekken"' in page
    assert _get(_client(app), '/e/kort')[0] == 404
    event = _event(make_user())
    resp = _req(_client(app), 'GET', f'/e/{event.code.lower()}')
    assert resp.status_code == 302 and resp.location.endswith(f'/e/{event.code}')


def test_making_an_event_leads_to_its_page(app, make_user, on):
    from tests.test_events_manage import _form
    data = _form()
    resp = _req(_client(app, make_user()), 'POST', '/events/nieuw', data=data)
    event = Event.query.filter_by(name=data['name']).one()
    assert resp.location.endswith(f'/e/{event.code}')


def test_an_archived_event_has_no_buttons(app, make_user, on):
    organiser = make_user()
    event = _event(organiser)
    event.archived_at = datetime.utcnow()
    _db.session.commit()
    _status, page = _get(_client(app, organiser), f'/e/{event.code}')
    assert 'Gearchiveerd' in page and 'Dit event is gearchiveerd' in page
    assert 'Alleen zichtbaar voor jou als organisator' not in page and 'Nieuw vraagstuk bij dit event' not in page


# ── The QR code ──────────────────────────────────────────────────────────

def test_qr_codes_only_for_whoever_manages(app, make_user, on):
    organiser, member = make_user(), make_user()
    event = _event(organiser)
    client = _client(app, organiser)
    svg = _req(client, 'GET', f'/e/{event.code}/qr.svg')
    assert svg.status_code == 200 and svg.mimetype == 'image/svg+xml' and b'<svg' in svg.data
    assert 'private' in svg.headers['Cache-Control']
    png = _req(client, 'GET', f'/e/{event.code}/qr.png')
    assert png.status_code == 200 and png.mimetype == 'image/png' and png.data.startswith(b'\x89PNG')
    for other in (_client(app, member), _client(app)):
        assert _req(other, 'GET', f'/e/{event.code}/qr.svg').status_code == 403
        assert _req(other, 'GET', f'/e/{event.code}/qr.png').status_code == 403


def test_the_qr_code_holds_the_link_and_is_made_here(app, make_user, on, monkeypatch):
    import segno
    made = []
    real = segno.make
    monkeypatch.setattr(segno, 'make', lambda content, **kw: (made.append(content), real(content, **kw))[1])
    organiser = make_user()
    event = _event(organiser)
    _req(_client(app, organiser), 'GET', f'/e/{event.code}/qr.png')
    assert made == [f'http://localhost/e/{event.code}']


# ── A new link ───────────────────────────────────────────────────────────

def test_a_new_link_makes_the_old_one_stop(app, make_user, on):
    organiser, member = make_user(), make_user()
    event = _event(organiser)
    issue = _issue_in(event, organiser)
    old = event.code
    client = _client(app, organiser)
    _status, page = _get(client, f'/e/{old}/nieuwe-link')
    assert 'De oude link en QR-code werken dan niet meer. Ook geprinte QR-codes niet.' in page
    assert 'Nieuwe link maken' in page and 'Annuleren' in page
    assert _req(_client(app, member), 'POST', f'/e/{old}/nieuwe-link').status_code == 403
    resp = _req(client, 'POST', f'/e/{old}/nieuwe-link')
    _db.session.expire_all()
    new = _db.session.get(Event, event.id).code
    assert new != old and resp.status_code == 303 and resp.location.endswith(f'/e/{new}')
    status, page = _get(_client(app), f'/e/{old}')
    assert status == 404 and 'Deze link werkt niet meer' in page
    assert ModerationAction.query.filter_by(action='event_new_link', target_id=event.id, issue_id=issue.id).count() == 1


# ── Access to the issues of an event that is not shared (K2b) ────────────

def _routes(issue, proposal):
    base = f'/vraagstuk/{issue.id}'
    return [('GET', base), ('GET', base + '?vergroot=voorstellen'), ('GET', base + '/voorstellen'),
            ('GET', base + '/feiten'), ('GET', f'{base}/voorstel/{proposal.id}'),
            ('GET', base + '/voorstellen/nieuw'), ('POST', f'{base}/voorstel/{proposal.id}/steunen'),
            ('POST', base + '/volgen'), ('GET', f'/forum/{issue.thread_id}'),
            ('POST', f'/forum/{issue.thread_id}/feiten')]


def test_access_per_route(app, make_user, on):
    organiser, member, follower = make_user(), make_user(), make_user()
    event = _event(organiser)
    issue = _issue_in(event, organiser)
    proposal = IssueProposal(issue_id=issue.id, statement=f'S{_uid()}', created_by_id=organiser.id)
    _db.session.add(proposal)
    _db.session.add(IssueFollow(issue_id=issue.id, user_id=follower.id))
    _db.session.commit()
    message = 'Dit vraagstuk hoort bij een event. Open de link van het event om mee te doen.'
    for method, path in _routes(issue, proposal):
        for client in (_client(app), _client(app, member)):
            resp = _req(client, method, path)
            if resp.status_code == 302:   # a route behind login, for a visitor: the login page first
                assert '/login' in resp.location and client is not None
                continue
            assert resp.status_code == 404, (method, path, resp.status_code)
            assert message in html.unescape(resp.get_data(as_text=True)), (method, path)
        resp = _req(_client(app, member), method, path)
        assert resp.status_code == 404, (method, path, resp.status_code)
    # The JSON route of a post in the conversation too.
    op = issues.opening_post(issue)
    assert _req(_client(app, member), 'POST', f'/forum/translate/{op.id}').status_code == 404
    # With the link opened in this session, as a participant, a follower, the organiser, a moderator.
    visitor = _client(app)
    _req(visitor, 'GET', f'/e/{event.code}')
    allowed = [visitor, _client(app, follower), _client(app, organiser), _client(app, make_user('moderator'))]
    for client in allowed:
        status, page = _get(client, f'/vraagstuk/{issue.id}')
        assert status == 200 and message not in page
        assert '<meta name="robots" content="noindex">' in page
    # Shared: open for everyone, no noindex.
    events.share(event, organiser)
    _db.session.commit()
    status, page = _get(_client(app), f'/vraagstuk/{issue.id}')
    assert status == 200 and '<meta name="robots" content="noindex">' not in page


def test_with_events_off_nothing_is_closed(app, make_user):
    organiser = make_user()
    event = _event(organiser)
    issue = _issue_in(event, organiser)
    status, page = _get(_client(app), f'/vraagstuk/{issue.id}')
    assert status == 200 and 'noindex' not in page
    assert _get(_client(app), f'/e/{event.code}')[0] == 404
    assert issue.title in _get(_client(app), '/gesprekken')[1]


def test_access_keeps_at_most_twenty_events(app, on):
    sess = {}
    made = [type('E', (), {'id': n})() for n in range(1, 26)]
    for event in made:
        events.grant(event, sess)
    assert sess['event_access'] == list(range(6, 26))
    events.grant(made[10], sess)
    assert sess['event_access'][-1] == 11 and len(sess['event_access']) == 20


def test_the_conversations_of_unshared_events_are_not_in_the_forum_list(app, make_user, on):
    organiser = make_user()
    event = _event(organiser)
    issue = _issue_in(event, organiser)
    assert f'/forum/{issue.thread_id}"' not in _get(_client(app), '/forum')[1]
    events.share(event, organiser)
    _db.session.commit()
    assert events.hidden_thread_ids().isdisjoint({issue.thread_id})


# ── Sharing (K10) ────────────────────────────────────────────────────────

def test_the_share_card_only_after_the_last_day_and_only_for_the_organiser(app, make_user, on):
    organiser, moderator = make_user(), make_user('moderator')
    future = _event(organiser)
    assert 'Het event is afgelopen' not in _get(_client(app, organiser), f'/e/{future.code}')[1]
    event = _past_event(organiser)
    issue = _issue_in(event, organiser)
    _status, page = _get(_client(app, organiser), f'/e/{event.code}')
    assert 'Het event is afgelopen. Wil je het delen met alle deelnemers van Global Union?' in page
    assert 'De vraagstukken van het event komen dan in het overzicht Denk mee' in page
    assert 'Eén vraagstuk is nog niet afgerond (Voorstellen maken). Na het delen kan iedereen daar meedoen.' in page
    assert 'Delen met alle deelnemers' in page and 'Nu niet' in page
    assert 'Het event is afgelopen' not in _get(_client(app, moderator), f'/e/{event.code}')[1]
    assert _req(_client(app, moderator), 'POST', f'/e/{event.code}/delen', data={'action': 'share'}).status_code == 403
    issue.phase = 'vervolg'
    _db.session.commit()
    _status, page = _get(_client(app, organiser), f'/e/{event.code}')
    assert 'nog niet afgerond' not in page


def test_two_unfinished_issues_in_the_plural(app, make_user, on):
    organiser = make_user()
    event = _past_event(organiser)
    _issue_in(event, organiser)
    second = _issue_in(event, organiser)
    second.phase = 'stemmen'
    _db.session.commit()
    page = _get(_client(app, organiser), f'/e/{event.code}')[1]
    assert '2 vraagstukken zijn nog niet afgerond (Voorstellen maken, Stemmen).' in page


def test_share_not_now_and_undo(app, make_user, on):
    organiser = make_user()
    event = _past_event(organiser)
    first, second = _issue_in(event, organiser), _issue_in(event, organiser)
    client = _client(app, organiser)
    assert first.title not in _get(client, '/gesprekken')[1]
    # Not now: the card goes, the event stays unshared.
    _req(client, 'POST', f'/e/{event.code}/delen', data={'action': 'later'})
    _db.session.expire_all()
    assert _db.session.get(Event, event.id).share_dismissed_at is not None
    assert 'Het event is afgelopen' not in _get(client, f'/e/{event.code}')[1]
    # Share from the settings.
    settings = _get(client, f'/e/{event.code}/instellingen')[1]
    assert 'Delen met alle deelnemers' in settings and 'form="shareForm"' in settings
    shared_before = ModerationAction.query.filter_by(action='share_event').count()
    resp = _req(client, 'POST', f'/e/{event.code}/delen', data={'action': 'share', 'back': 'settings'})
    assert resp.status_code == 303 and resp.location.endswith('/instellingen')
    _db.session.expire_all()
    event = _db.session.get(Event, event.id)
    assert event.shared_at is not None and event.shared_by_id == organiser.id
    rows = ModerationAction.query.filter_by(action='share_event').order_by(ModerationAction.id).all()[shared_before:]
    assert sorted(r.issue_id for r in rows) == sorted([first.id, second.id])
    assert {r.target_id for r in rows} == {event.id}
    overview = _get(_client(app), '/gesprekken')[1]
    assert first.title in overview and second.title in overview
    page = _get(client, f'/e/{event.code}')[1]
    assert 'Dit event is gedeeld met alle deelnemers van Global Union.' in page
    assert 'Na afloop kies je' not in page and '<meta name="robots"' not in page
    assert 'Delen ongedaan maken' in _get(client, f'/e/{event.code}/instellingen')[1]
    # Undo: only the organiser.
    moderator = make_user('moderator')
    assert _req(_client(app, moderator), 'POST', f'/e/{event.code}/delen', data={'action': 'unshare'}).status_code == 403
    _req(client, 'POST', f'/e/{event.code}/delen', data={'action': 'unshare'})
    _db.session.expire_all()
    assert _db.session.get(Event, event.id).shared_at is None
    assert ModerationAction.query.filter_by(action='unshare_event', issue_id=first.id).count() >= 1
    assert first.title not in _get(_client(app), '/gesprekken')[1]


# ── Have your say ────────────────────────────────────────────────────────

def test_listed_filter(app, make_user, on):
    organiser = make_user()
    event = _event(organiser)
    issue = _issue_in(event, organiser)
    online = issues.create({'title': f'Online {_uid()}', 'intro': 'Uitleg.', 'kind': 'online'}, organiser, lang='nl')
    _db.session.commit()

    def listed():
        return {i.id for i in events.listed_filter(Issue.query).all()}
    assert online.id in listed() and issue.id not in listed()
    events.share(event, organiser)
    _db.session.commit()
    assert issue.id in listed()
    event.archived_at = datetime.utcnow()
    _db.session.commit()
    assert issue.id not in listed()


def test_have_your_say_labels_and_filters(app, make_user, on):
    organiser = make_user()
    event = _event(organiser)
    issue = _issue_in(event, organiser)
    online = issues.create({'title': f'Online {_uid()}', 'intro': 'Uitleg.', 'kind': 'online'}, organiser, lang='nl')
    events.share(event, organiser)
    _db.session.commit()
    page = _get(_client(app), '/gesprekken')[1]
    nav = page.split('aria-label="Vraagstukken filteren"')[1].split('</nav>')[0]
    assert [re.sub(r'\s+', ' ', t).strip() for t in re.findall(r'>([^<]+)</a>', nav)] == \
        ['Alle', 'Voor iedereen', 'Van events', 'Lopend', 'Afgerond']
    row = page.split(online.title)[0].rsplit('<li', 1)[1]
    assert 'Voor iedereen' in row and '>Online' not in row
    row = page.split(issue.title)[0].rsplit('<li', 1)[1]
    assert f'<a href="/e/{event.code}" class="gu-issue-kind__event">' in row
    assert event.name in row.split('gu-issue-kind__event')[1].split('</a>')[0]
    assert '15 jan 2027 19:00, Forum, Groningen' in row
    # No link inside a link: the row is no anchor, the title is its own link.
    assert '<a class="gu-threadlist__link' not in row
    assert f'href="/vraagstuk/{issue.id}"' in page.split(issue.title)[0].rsplit('<li', 1)[1]
    assert issue.title in _get(_client(app), '/gesprekken?filter=event')[1]
    assert issue.title not in _get(_client(app), '/gesprekken?filter=online')[1]
    assert online.title in _get(_client(app), '/gesprekken?filter=online')[1]


def test_have_your_say_with_events_off_is_unchanged(app, make_user):
    page = _get(_client(app), '/gesprekken')[1]
    assert 'Voor iedereen' not in page and 'Heb je een eventcode?' not in page and 'Jouw events' not in page


def test_your_events(app, make_user, on):
    organiser, member, stranger = make_user(), make_user(), make_user()
    own = _event(organiser, name=f'Eigen {_uid()}')
    other = _event(stranger, name=f'Ander {_uid()}', event_day=_day(20))
    followed = _event(stranger, name=f'Gevolgd {_uid()}')
    _issue_in(own, organiser)
    _issue_in(other, stranger)
    issue = _issue_in(followed, stranger)
    _db.session.add(IssueFollow(issue_id=issue.id, user_id=member.id))
    finished = _past_event(stranger, name=f'Voorbij {_uid()}')
    finished_issue = _issue_in(finished, stranger)
    _db.session.add(IssueFollow(issue_id=finished_issue.id, user_id=member.id))
    _db.session.commit()
    # The organiser: the own event with the pill, back to the event.
    page = _get(_client(app, organiser), '/gesprekken')[1]
    block = page.split('id="yourEvents"')[1].split('</section>')[0]
    assert own.name in block and 'Organisator' in block and 'Terug naar het event' in block
    assert 'Gepland' in block and '1 vraagstuk' in block
    assert 'Events waar je aan meedoet of die je organiseert. Alleen jij ziet dit blok.' in page
    # A member: the followed event (not opened here yet), the one opened by link, not the finished one.
    client = _client(app, member)
    _req(client, 'GET', f'/e/{other.code}')
    page = _get(client, '/gesprekken')[1]
    block = page.split('id="yourEvents"')[1].split('</section>')[0]
    assert followed.name in block and other.name in block and finished.name not in block and own.name not in block
    assert 'Organisator' not in block
    item = block.split(followed.name)[1].split('</li>')[0]
    assert 'Naar het event' in item
    item = block.split(other.name)[1].split('</li>')[0]
    assert 'Terug naar het event' in item
    # Without events no block; the code field is there for everyone.
    page = _get(_client(app), '/gesprekken')[1]
    assert 'id="yourEvents"' not in page and 'Heb je een eventcode?' in page
    assert 'placeholder="Bijvoorbeeld K7M2QXPA"' in page and 'Naar het event' in page


def test_the_state_of_an_event(app, make_user, on):
    user = make_user()
    planned = _event(user, event_day=_day(5))
    assert events.state(planned) == 'planned'
    over = _past_event(user)
    assert events.state(over) == 'finished'
    over.days[0].ends_at = datetime.utcnow() + timedelta(hours=1)
    assert events.state(over) == 'running'
    no_days = _event(user)
    no_days.days = []
    issue = _issue_in(no_days, user)
    assert events.state(no_days) == 'running'
    issue.phase = 'vervolg'
    assert events.state(no_days) == 'finished'
    _db.session.rollback()


def test_the_event_code_field(app, make_user, on):
    event = _event(make_user())
    code = event.code
    client = _client(app)
    resp = _req(client, 'GET', f'/e?code= {code[:4].lower()}-{code[4:].lower()} ')
    assert resp.status_code == 302 and resp.location.endswith(f'/e/{code}')
    resp = _req(client, 'GET', '/e?code=ZZZZ2222')
    assert resp.status_code == 302 and resp.location.endswith('/gesprekken#eventcode')
    page = _get(client, '/gesprekken')[1]
    assert 'Deze eventcode kennen we niet. Controleer de code of vraag de organisator.' in page


def test_the_event_code_field_has_a_rate_limit(app, on):
    client = _client(app)
    answers = [_req(client, 'GET', '/e?code=ZZZZ2222') for _ in range(11)]
    assert all(r.location.endswith('/gesprekken#eventcode') for r in answers[:10])
    # The eleventh: the limiter's message, back to Have your say.
    assert answers[10].status_code == 302 and answers[10].location.endswith('/gesprekken')
    assert 'Te veel pogingen' in _get(client, '/gesprekken')[1]


# ── A fixed number of queries ────────────────────────────────────────────

def _queries(client, path):
    _db.session.commit()
    _db.session.expire_all()
    _db.session.remove()
    with _count_queries() as statements:
        resp = _req(client, 'GET', path)
    assert resp.status_code == 200
    return len(statements)


def test_the_event_page_has_a_fixed_number_of_queries(app, make_user, on):
    from app.models import User
    uid = make_user().id
    counts = []
    for size in (1, 6):
        organiser = _db.session.get(User, uid)
        event = _event(organiser)
        for _ in range(size):
            issue = _issue_in(event, organiser)
            _db.session.add(IssueProposal(issue_id=issue.id, statement=f'S{_uid()}', created_by_id=make_user().id))
        _db.session.commit()
        counts.append(_queries(_client(app, organiser), f'/e/{event.code}'))
    assert counts[0] == counts[1], counts


def test_have_your_say_has_a_fixed_number_of_queries(app, make_user, on):
    from app.models import User
    uid = make_user().id
    counts = []
    for size in (1, 6):
        organiser = _db.session.get(User, uid)
        event = _event(organiser)
        for _ in range(size):
            issue = _issue_in(event, organiser)
            _db.session.add(IssueProposal(issue_id=issue.id, statement=f'S{_uid()}', created_by_id=make_user().id))
        events.share(event, organiser)
        _db.session.commit()
        counts.append(_queries(_client(app, organiser), '/gesprekken'))
    assert counts[0] == counts[1], counts


def test_participant_counts_match_participants(app, make_user, on):
    organiser, other = make_user(), make_user()
    event = _event(organiser)
    a, b = _issue_in(event, organiser), _issue_in(event, other)
    _db.session.add(IssueProposal(issue_id=a.id, statement=f'S{_uid()}', created_by_id=other.id))
    _db.session.commit()
    counts = issue_lifecycle.participant_counts([a, b])
    assert counts == {a.id: len(issue_lifecycle.participants(a)), b.id: len(issue_lifecycle.participants(b))}
    assert counts[a.id] == 2
