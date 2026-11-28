"""
Events step 1e (docs/events.md, K9, K10; docs/design/event-pagina.html): the
bar "You are at" on the event page and on every page of an issue of an event
that is not shared, the current event in the session (Have your say in the
header leads to it, /e/verlaten forgets it), no breadcrumb from Have your say
inside such an event, and no event block in the cockpit. All only with EVENTS
on; with it off nothing changes.

Shared conftest app (CSRF off); every test makes its own data with unique
texts. make_user removes the users with their issues and events.
"""
import html
import re
from datetime import datetime

import pytest

from app import db as _db, events, issues
from app.models import IssueProposal
from tests.test_delete_issue import _clean_session  # noqa: F401  (autouse fixture)
from tests.test_events_manage import _event, _issue_in, _uid
from tests.test_events_page import _queries
from tests.test_issue_polish import _client, _fact, _req, make_user  # noqa: F401  (fixture)

BAR = 'class="gu-eventbar" role="region" aria-label="Je bent bij een event"'


@pytest.fixture
def on(app, monkeypatch):
    monkeypatch.setitem(app.config, 'EVENTS', True)


def _get(client, path):
    resp = _req(client, 'GET', path)
    assert resp.status_code == 200, (path, resp.status_code)
    return html.unescape(resp.get_data(as_text=True))


def _menu_link(page):
    """The href of "Denk mee" in the header."""
    nav = page.split('id="siteNav"')[1]
    return re.search(r'<a href="([^"]+)"\s*(?:class="is-active")?\s*>Denk mee</a>', nav).group(1)


def _menu_active(page):
    nav = page.split('id="siteNav"')[1]
    return bool(re.search(r'class="is-active"\s*>Denk mee</a>', nav))


def _world(organiser):
    event = _event(organiser)
    issue = _issue_in(event, organiser)
    proposal = IssueProposal(issue_id=issue.id, statement=f'S{_uid()}', created_by_id=organiser.id)
    _db.session.add(proposal)
    _db.session.commit()
    return event, issue, proposal


def _pages(issue, proposal):
    return [f'/vraagstuk/{issue.id}', f'/vraagstuk/{issue.id}?vergroot=feiten',
            f'/vraagstuk/{issue.id}/voorstel/{proposal.id}', f'/vraagstuk/{issue.id}/voorstellen/nieuw']


# ── The bar ──────────────────────────────────────────────────────────────

def test_the_bar_on_the_event_page_and_the_issue_pages(app, db, make_user, on):
    organiser = make_user()
    event, issue, proposal = _world(organiser)
    client = _client(app, organiser)
    page = _get(client, f'/e/{event.code}')
    assert BAR in page
    bar = page.split('class="gu-eventbar"')[1].split('</div>\n</div>')[0]
    assert f'Je bent bij: <strong class="gu-eventbar__name">' in bar and event.name in bar
    assert f'<a href="/e/{event.code}">Terug naar het event</a>' in bar
    assert '<a href="/e/verlaten" class="gu-eventbar__out">Bekijk de vraagstukken buiten dit event' in bar
    assert '<path d="M13 6l6 6-6 6"/>' in bar
    # The link at the bottom of the event page leaves too.
    assert 'href="/e/verlaten" class="gu-eventpage__out"' in page
    for path in _pages(issue, proposal):
        assert BAR in _get(client, path), path


def test_the_bar_on_the_chart_page(app, db, make_user, on):
    from app.models import FACT_KIND, Post
    from tests.test_proposal_queries import _chart_facts
    organiser = make_user()
    event, issue, proposal = _world(organiser)
    _chart_facts(issue, [proposal])
    _db.session.commit()
    fact = Post.query.filter_by(thread_id=issue.thread_id, kind=FACT_KIND, fact_type='cijfer').first()
    page = _get(_client(app, organiser), f'/vraagstuk/{issue.id}/cijfer/{fact.id}')
    assert BAR in page and f'<a href="/e/{event.code}">Terug naar het event</a>' in page


def test_no_bar_for_a_shared_event_an_issue_for_everyone_or_with_events_off(app, db, make_user, on, monkeypatch):
    organiser = make_user()
    event, issue, proposal = _world(organiser)
    online = issues.create({'title': f'Online {_uid()}', 'intro': 'Uitleg.', 'kind': 'online'}, organiser, lang='nl')
    _db.session.commit()
    client = _client(app, organiser)
    assert 'gu-eventbar' not in _get(client, f'/vraagstuk/{online.id}')
    events.share(event, organiser)
    _db.session.commit()
    for path in [f'/e/{event.code}'] + _pages(issue, proposal):
        page = _get(_client(app, organiser), path)
        assert 'gu-eventbar' not in page, path
    monkeypatch.setitem(app.config, 'EVENTS', False)
    assert 'gu-eventbar' not in _get(_client(app, organiser), f'/vraagstuk/{issue.id}')


# ── The session ──────────────────────────────────────────────────────────

def test_the_session_points_have_your_say_to_the_event(app, db, make_user, on):
    organiser = make_user()
    event, issue, _proposal = _world(organiser)
    visitor = _client(app)
    assert _menu_link(_get(visitor, '/gesprekken')) == '/gesprekken'
    page = _get(visitor, f'/e/{event.code}')
    assert _menu_link(page) == f'/e/{event.code}' and _menu_active(page)
    page = _get(visitor, '/faq')
    assert _menu_link(page) == f'/e/{event.code}' and not _menu_active(page)
    page = _get(visitor, f'/vraagstuk/{issue.id}')
    assert _menu_link(page) == f'/e/{event.code}' and _menu_active(page)
    # Leaving: the menu points to the overview again, the access to the issues stays.
    resp = _req(visitor, 'GET', '/e/verlaten')
    assert resp.status_code == 302 and resp.location.endswith('/gesprekken')
    page = _get(visitor, '/gesprekken')
    assert _menu_link(page) == '/gesprekken'
    assert 'Dit vraagstuk hoort bij een event' not in _get(visitor, f'/vraagstuk/{issue.id}')
    with visitor.session_transaction() as sess:
        assert event.id in sess['event_access']


def test_an_issue_of_another_event_switches_the_current_event(app, db, make_user, on):
    organiser = make_user()
    first, _i1, _p1 = _world(organiser)
    second, issue2, _p2 = _world(organiser)
    client = _client(app, organiser)
    _get(client, f'/e/{first.code}')
    page = _get(client, f'/vraagstuk/{issue2.id}')
    assert _menu_link(page) == f'/e/{second.code}'
    with client.session_transaction() as sess:
        assert sess['current_event'] == second.id


@pytest.mark.parametrize('how', ['shared', 'archived', 'deleted'])
def test_a_shared_archived_or_deleted_event_drops_out(app, db, make_user, on, how):
    organiser = make_user()
    event, issue, _proposal = _world(organiser)
    client = _client(app, organiser)
    _get(client, f'/e/{event.code}')
    if how == 'shared':
        events.share(event, organiser)
    elif how == 'archived':
        event.archived_at = datetime.utcnow()
    else:
        issues.purge(issue)
    _db.session.commit()
    page = _get(client, '/faq')
    assert _menu_link(page) == '/gesprekken'
    with client.session_transaction() as sess:
        assert 'current_event' not in sess


def test_verlaten_is_no_event_code(app, make_user, on):
    assert events.by_code('verlaten') is None
    resp = _req(_client(app), 'GET', '/e/verlaten')
    assert resp.status_code == 302 and resp.location.endswith('/gesprekken')


def test_with_events_off_nothing_of_the_session(app, db, make_user):
    organiser = make_user()
    event, issue, _proposal = _world(organiser)
    client = _client(app, organiser)
    _get(client, f'/vraagstuk/{issue.id}')
    with client.session_transaction() as sess:
        assert 'current_event' not in sess
    assert _menu_link(_get(client, '/faq')) == '/gesprekken'
    assert _req(client, 'GET', '/e/verlaten').status_code == 404


# ── Breadcrumb and cockpit ───────────────────────────────────────────────

def test_no_breadcrumb_inside_an_event_and_the_label_links_to_it(app, db, make_user, on):
    organiser = make_user()
    event, issue, proposal = _world(organiser)
    client = _client(app, organiser)
    page = _get(client, f'/e/{event.code}')
    assert 'gu-issue-crumbs' not in page and 'gu-backlink' not in page
    cockpit = _get(client, f'/vraagstuk/{issue.id}')
    assert 'class="gu-backlink"' not in cockpit
    label = cockpit.split('gu-cockpit__eventlabel')[1].split('</p>')[0]
    assert f'<a href="/e/{event.code}" class="gu-issue-kind__event">' in label and event.name in label
    assert 'gu-cockpit__event"' not in cockpit and 'gu-cockpit__eventname' not in cockpit
    proposal_page = _get(client, f'/vraagstuk/{issue.id}/voorstel/{proposal.id}')
    crumbs = proposal_page.split('class="gu-pv__crumbs"')[1].split('</nav>')[0]
    assert 'Terug naar het vraagstuk' in crumbs and '>Denk mee</a>' not in crumbs
    new = _get(client, f'/vraagstuk/{issue.id}/voorstellen/nieuw')
    crumbs = new.split('class="gu-forum__crumbs gu-issue-crumbs"')[1].split('</nav>')[0]
    assert '>Denk mee</a>' not in crumbs and issue.title in crumbs


def test_a_shared_event_keeps_the_breadcrumb_without_event_block(app, db, make_user, on):
    organiser = make_user()
    event, issue, proposal = _world(organiser)
    events.share(event, organiser)
    _db.session.commit()
    client = _client(app, organiser)
    cockpit = _get(client, f'/vraagstuk/{issue.id}')
    assert 'class="gu-backlink"' in cockpit and 'gu-eventbar' not in cockpit
    assert f'<a href="/e/{event.code}" class="gu-issue-kind__event">' in cockpit
    assert 'gu-cockpit__eventname' not in cockpit
    crumbs = _get(client, f'/vraagstuk/{issue.id}/voorstel/{proposal.id}').split('class="gu-pv__crumbs"')[1]
    assert '>Denk mee</a>' in crumbs.split('</nav>')[0]


def test_with_events_off_the_cockpit_is_as_before(app, db, make_user):
    organiser = make_user()
    issue = issues.create({'title': f'Event {_uid()}', 'intro': 'Uitleg.', 'kind': 'event', 'event_name': 'Oude avond',
                           'event_note': 'Toelichting van de avond.'}, organiser, lang='nl')
    _db.session.commit()
    cockpit = _get(_client(app, organiser), f'/vraagstuk/{issue.id}')
    assert 'class="gu-backlink"' in cockpit and 'gu-cockpit__eventname' in cockpit
    assert 'gu-cockpit__eventlabel' not in cockpit and 'gu-eventbar' not in cockpit


# ── Queries ──────────────────────────────────────────────────────────────

def test_at_most_one_query_more_on_an_issue_page(app, db, make_user, monkeypatch):
    """The cockpit and the proposal page of an issue of an event that is not
    shared, with EVENTS on and off: the bar costs at most the one query that
    loads the event (with its days); the name's translation shares the
    issue's query."""
    from app.models import User
    uid = make_user().id
    organiser = _db.session.get(User, uid)
    event, issue, proposal = _world(organiser)
    paths = {'cockpit': f'/vraagstuk/{issue.id}', 'proposal': f'/vraagstuk/{issue.id}/voorstel/{proposal.id}'}
    counts = {}
    for switch in (False, True):
        monkeypatch.setitem(app.config, 'EVENTS', switch)
        for name, path in paths.items():
            _queries(_client(app, _db.session.get(User, uid)), path)   # warm: the first visit marks reads
            counts[(name, switch)] = _queries(_client(app, _db.session.get(User, uid)), path)
    for name in paths:
        assert counts[(name, True)] - counts[(name, False)] <= 1, counts
