"""
Events step 1f (docs/events.md, K6, K7, K8; docs/design/event-stemblok.html):
"Everyone may vote". The right to vote on one place (events.has_vote_right),
used by the vote, the vote block and the cockpit; fixed at the first vote of
the event; one sentence each in two FAQ answers. All only with EVENTS on;
with it off exactly user.can_vote().

Shared conftest app (CSRF off); every test makes its own data with unique
texts. make_user removes the users with their issues and events.
"""
import html
from datetime import datetime, timedelta

import pytest

from app import db as _db, events, issue_lifecycle, issue_proposals as ip, issues
from app.models import IssueProposalSupport, IssueProposalVote
from tests.test_delete_issue import _clean_session  # noqa: F401  (autouse fixture)
from tests.test_events_manage import _event, _form, _issue_in, _uid
from tests.test_issue_polish import _client, _req, make_user  # noqa: F401  (fixture)

NO_RIGHT = 'Je hebt nog geen stemrecht'


@pytest.fixture
def on(app, monkeypatch):
    monkeypatch.setitem(app.config, 'EVENTS', True)


def _voting(organiser, everyone=True):
    """An event with one issue in the voting phase and one proposal on the list."""
    event = _event(organiser)
    event.everyone_votes = everyone
    event.days = []   # no days: the platform is open now
    issue = _issue_in(event, organiser)
    issue.support_threshold = 1
    proposal = ip.create(issue, organiser, f'Stelling {_uid()}')
    _db.session.flush()
    ip.support(proposal, organiser)
    issue.phase = 'stemmen'
    _db.session.commit()
    return event, issue, proposal


def _member(client_event, app, user):
    """A client for `user` that opened the link of the event (access, K2b)."""
    client = _client(app, user)
    _req(client, 'GET', f'/e/{client_event.code}')
    return client


def _page(client, path):
    resp = _req(client, 'GET', path)
    assert resp.status_code == 200, (path, resp.status_code)
    return html.unescape(resp.get_data(as_text=True))


# ── has_vote_right ───────────────────────────────────────────────────────

def test_has_vote_right(app, db, make_user, on, monkeypatch):
    from app.models import AnonymousUser
    organiser, newcomer, voter = make_user(), make_user(), make_user('voter')
    event, issue, _proposal = _voting(organiser)
    online = issues.create({'title': f'Online {_uid()}', 'intro': 'Uitleg.', 'kind': 'online'}, organiser, lang='nl')
    _db.session.commit()
    assert events.has_vote_right(issue, AnonymousUser()) is False
    assert events.has_vote_right(issue, voter) is True and events.has_vote_right(online, voter) is True
    assert events.has_vote_right(issue, newcomer) is True
    assert events.has_vote_right(online, newcomer) is False
    newcomer.suspended_until = datetime.utcnow() + timedelta(days=2)
    assert events.has_vote_right(issue, newcomer) is False
    newcomer.suspended_until = None
    newcomer.deleted_at = datetime.utcnow()
    assert events.has_vote_right(issue, newcomer) is False
    newcomer.deleted_at = None
    newcomer.rules_accepted_at = None
    assert events.has_vote_right(issue, newcomer) is False
    db.session.rollback()
    event.everyone_votes = False
    assert events.has_vote_right(issue, newcomer) is False
    monkeypatch.setitem(app.config, 'EVENTS', False)
    event.everyone_votes = True
    for user in (newcomer, voter, organiser):
        assert events.has_vote_right(issue, user) == user.can_vote()
    db.session.rollback()


# ── Voting through the route ─────────────────────────────────────────────

def test_a_new_account_votes_with_everyone_may_vote(app, db, make_user, on):
    organiser, newcomer = make_user(), make_user()
    event, issue, proposal = _voting(organiser)
    client = _member(event, app, newcomer)
    path = f'/vraagstuk/{issue.id}/voorstel/{proposal.id}'
    page = _page(client, path)
    assert 'name="choice"' in page and NO_RIGHT not in page
    # Artboard A: no line or message about the setting.
    block = page.split('id="stemming"')[1].split('</section>')[0]
    assert 'Iedereen mag stemmen' not in block and 'organisator' not in block.lower()
    resp = _req(client, 'POST', path + '/stemmen', data={'choice': 'voor'})
    assert resp.status_code == 302
    assert IssueProposalVote.query.filter_by(proposal_id=proposal.id, user_id=newcomer.id).one().choice == 'voor'
    assert ip.tally(proposal)['voor'] >= 1


def test_without_everyone_may_vote_the_existing_text(app, db, make_user, on):
    organiser, newcomer = make_user(), make_user()
    event, issue, proposal = _voting(organiser, everyone=False)
    client = _member(event, app, newcomer)
    path = f'/vraagstuk/{issue.id}/voorstel/{proposal.id}'
    page = _page(client, path)
    assert NO_RIGHT in page and 'name="choice"' not in page
    assert _req(client, 'POST', path + '/stemmen', data={'choice': 'voor'}).status_code == 403
    assert IssueProposalVote.query.filter_by(proposal_id=proposal.id, user_id=newcomer.id).count() == 0


def test_with_events_off_a_new_account_cannot_vote(app, db, make_user):
    organiser, newcomer = make_user(), make_user()
    event, issue, proposal = _voting(organiser)
    path = f'/vraagstuk/{issue.id}/voorstel/{proposal.id}'
    client = _client(app, newcomer)
    assert NO_RIGHT in _page(client, path)
    assert _req(client, 'POST', path + '/stemmen', data={'choice': 'voor'}).status_code == 403


def test_support_and_proposals_work_as_before(app, db, make_user, on):
    organiser, newcomer = make_user(), make_user()
    event = _event(organiser)
    event.everyone_votes = False
    event.days = []   # no days: the platform is open now
    issue = _issue_in(event, organiser)
    proposal = ip.create(issue, organiser, f'Stelling {_uid()}')
    _db.session.commit()
    client = _member(event, app, newcomer)
    statement = f'Voorstel {_uid()}'
    resp = _req(client, 'POST', f'/vraagstuk/{issue.id}/voorstellen/nieuw', data={'statement': statement})
    assert resp.status_code in (302, 303)
    assert any(p.statement == statement for p in issue.issue_proposals)
    _req(client, 'POST', f'/vraagstuk/{issue.id}/voorstel/{proposal.id}/steunen')
    assert IssueProposalSupport.query.filter_by(proposal_id=proposal.id, user_id=newcomer.id).count() == 1


# ── The cockpit: progress, and the profile keeps the earned status ──────

def test_the_cockpit_counts_the_event_right_the_profile_does_not(app, db, make_user, on):
    organiser, newcomer = make_user(), make_user()
    event, issue, _proposal = _voting(organiser)
    right = issues.voting_right(newcomer, issue)
    assert right['can_vote'] is True and right['pct'] == 100 and right['status'] == newcomer.effective_status
    assert issues.voting_right(newcomer)['can_vote'] is False
    client = _member(event, app, newcomer)
    cockpit = _page(client, f'/vraagstuk/{issue.id}')
    assert 'id="rightsTitle"' not in cockpit          # as for a voter: no progress card
    profile = _page(client, '/profile')
    assert '· stemrecht' not in profile and 'Op weg naar stemrecht' in profile


# ── Fixed at the first vote (K7) ─────────────────────────────────────────

def test_voting_fixes_the_setting_once(app, db, make_user, on):
    organiser = make_user()
    event = _event(organiser)
    first, second = _issue_in(event, organiser), _issue_in(event, organiser)
    assert event.everyone_votes_locked_at is None
    start = datetime(2027, 1, 15, 20, 0)
    issue_lifecycle.set_phase(first, 'stemmen', start)
    _db.session.commit()
    assert event.everyone_votes_locked_at == start
    issue_lifecycle.set_phase(second, 'stemmen', start + timedelta(hours=1))
    first.phase = 'vervolg'
    issue_lifecycle.reopen(first, start + timedelta(days=1))
    _db.session.commit()
    assert event.everyone_votes_locked_at == start
    # The settings refuse a change now, also with a forged POST.
    client = _client(app, organiser)
    page = _page(client, f'/e/{event.code}/instellingen')
    assert 'name="everyone_votes" value="1" checked disabled' in page and 'Vastgelegd sinds het stemmen begon.' in page
    data = _form(name=event.name)
    data.pop('everyone_votes')
    _req(client, 'POST', f'/e/{event.code}/instellingen', data=data)
    _db.session.expire_all()
    assert _db.session.get(type(event), event.id).everyone_votes is True


def test_straight_to_the_summary_vote_fixes_it_too(app, db, make_user, on):
    organiser = make_user()
    event = _event(organiser)
    issue = _issue_in(event, organiser)
    issue_lifecycle.set_phase(issue, 'samenvatten', datetime(2027, 1, 15, 20, 0))
    assert event.everyone_votes_locked_at == datetime(2027, 1, 15, 20, 0)
    _db.session.rollback()


def test_with_events_off_nothing_is_fixed(app, db, make_user):
    organiser = make_user()
    event = _event(organiser)
    issue = _issue_in(event, organiser)
    issue_lifecycle.set_phase(issue, 'stemmen', datetime(2027, 1, 15, 20, 0))
    assert event.everyone_votes_locked_at is None
    _db.session.rollback()


# ── FAQ (K8) ─────────────────────────────────────────────────────────────

FAQ = {
    'nl': ('Bij een event kan de organisator iedere deelnemer stemrecht geven voor de vraagstukken van dat event.',
           "Bij een event met Iedereen mag stemmen kan ook een nieuw account stemmen; zo'n event is alleen zichtbaar"),
    'en': ('At an event the organiser can give every participant the right to vote',
           'At an event with Everyone may vote a new account can vote too'),
    'de': ('Bei einer Veranstaltung können die Veranstaltenden allen Teilnehmenden Stimmrecht',
           'Bei einer Veranstaltung mit Alle dürfen abstimmen kann auch ein neues Konto abstimmen'),
    'es': ('En un evento, la persona organizadora puede dar a cada participante derecho a voto',
           'En un evento con Todos pueden votar también puede votar una cuenta nueva'),
    'fr': ("Lors d'un événement, la personne qui organise peut donner à chaque participant le droit de vote",
           "Lors d'un événement avec Tout le monde peut voter, un nouveau compte peut aussi voter"),
}


@pytest.mark.parametrize('lang', sorted(FAQ))
def test_the_faq_sentences_only_with_events(app, monkeypatch, lang):
    monkeypatch.setitem(app.config, 'EVENTS', False)
    off = _page(_client(app, None, lang), '/faq')
    monkeypatch.setitem(app.config, 'EVENTS', True)
    page = _page(_client(app, None, lang), '/faq')
    vote_answer = page.split('id="faq-3-5"')[1].split('</details>')[0]
    assert FAQ[lang][0] in vote_answer
    assert FAQ[lang][1] in page.split('id="faq-3-7"')[1].split('</details>')[0]
    for sentence in FAQ[lang]:
        assert sentence not in off
