"""Archiving an issue (delete flow step 1, Toegang.dc.html): an admin archives
or restores an issue from the cockpit's Beheer menu. An archived issue is
closed, drops out of the public lists and stays reachable by its URL."""
import uuid

import pytest

from app.models import Issue
from tests.test_delete_issue import _cleanup, _client, _issue_full, _req, _user


@pytest.fixture(autouse=True)
def _clean_session(db):
    db.session.expunge_all()
    yield


def _archivable(db):
    admin, author = _user(db, admin=True), _user(db)
    issue, _fact, _prop = _issue_full(db, author)
    issue.title = f'Archieftest {uuid.uuid4().hex[:8]}'
    db.session.commit()
    return admin, author, issue


def test_archive_hides_from_public_list_and_unarchive_restores(app, db):
    admin, author, issue = _archivable(db)
    issue_id, title = issue.id, issue.title
    try:
        c = _client(app, admin)
        assert title in _req(c, 'GET', '/gesprekken').get_data(as_text=True)

        r = _req(c, 'POST', f'/vraagstuk/{issue_id}/archiveren')
        assert r.status_code == 302
        issue = db.session.get(Issue, issue_id)
        db.session.refresh(issue)
        assert issue.archived_at is not None and issue.is_archived and issue.is_closed
        assert issue.thread.is_closed  # no new replies in the discussion either

        # Gone from the public list (also for a participant), reachable by URL.
        assert title not in _req(_client(app, author), 'GET', '/gesprekken').get_data(as_text=True)
        page = _req(c, 'GET', f'/vraagstuk/{issue_id}').get_data(as_text=True)
        assert 'Gearchiveerd' in page and 'Terug uit archief' in page
        assert f'/vraagstuk/{issue_id}/terug-uit-archief' in page

        r = _req(c, 'POST', f'/vraagstuk/{issue_id}/terug-uit-archief')
        assert r.status_code == 302
        db.session.refresh(issue)
        assert issue.archived_at is None and issue.is_closed  # stays closed until reopened
        assert title in _req(c, 'GET', '/gesprekken').get_data(as_text=True)
    finally:
        _cleanup(issue_id)


def test_archive_and_unarchive_are_admin_only(app, db):
    _admin, author, issue = _archivable(db)
    issue_id = issue.id
    try:
        mod = _user(db, 'moderator')
        for user in (author, mod):
            c = _client(app, user)
            assert _req(c, 'POST', f'/vraagstuk/{issue_id}/archiveren').status_code == 403
            assert _req(c, 'POST', f'/vraagstuk/{issue_id}/terug-uit-archief').status_code == 403
        assert db.session.get(Issue, issue_id).archived_at is None
    finally:
        _cleanup(issue_id)


def test_beheer_menu_shows_admin_items(app, db):
    admin, author, issue = _archivable(db)
    issue_id = issue.id
    try:
        page = _req(_client(app, admin), 'GET', f'/vraagstuk/{issue_id}').get_data(as_text=True)
        menu = page[page.index('<details class="gu-beheer" id="beheer">'):]
        menu = menu[:menu.index('</details>')]
        assert 'Beheer' in menu and 'Archiveren' in menu and f'/vraagstuk/{issue_id}/archiveren' in menu
        # Delete rights step 3: deleting moved to the manage screen; the menu links there.
        assert 'Vraagstuk verwijderen' not in menu and f'/vraagstuk/{issue_id}/verwijderen' not in menu
        assert 'gu-beheer__item--danger' not in menu
        assert f'href="/moderator/vraagstuk/{issue_id}/beheer"' in menu and 'Vraagstukbeheer' in menu
        assert page.count('id="beheer"') == 1 and 'beheer-admin' not in page

        # A participant who is not the initiator sees no Beheer menu.
        other = _user(db)
        plain = _req(_client(app, other), 'GET', f'/vraagstuk/{issue_id}').get_data(as_text=True)
        assert 'class="gu-beheer"' not in plain and 'Archiveren' not in plain
    finally:
        _cleanup(issue_id)
