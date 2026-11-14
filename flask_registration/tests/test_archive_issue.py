"""Archiving an issue (delete flow step 1, Toegang.dc.html): an admin archives
or restores an issue from the manage screen (/moderator/vraagstuk/<id>/beheer);
the issue page only has the initiator's "Fasen" menu. An archived issue is
closed, drops out of the public lists and stays reachable by its URL."""
import uuid

import pytest

from app.models import Issue
from tests.test_delete_issue import _cleanup, _client, _forget_deleted, _issue_full, _req, _user


@pytest.fixture(autouse=True)
def _clean_session(db):
    db.session.expunge_all()
    yield
    # Purged rows must not linger in the identity map (SQLite reuses their ids).
    db.session.rollback()
    _forget_deleted()


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
        assert 'Gearchiveerd' in page and _menu(page) is None  # the archived banner, no menu
        manage = _req(c, 'GET', f'/moderator/vraagstuk/{issue_id}/beheer').get_data(as_text=True)
        assert 'Terug uit archief' in manage and f'/vraagstuk/{issue_id}/terug-uit-archief' in manage

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


def _menu(page):
    if '<details class="gu-beheer" id="beheer">' not in page:
        return None
    menu = page[page.index('<details class="gu-beheer" id="beheer">'):]
    return menu[:menu.index('</details>')]


def test_issue_page_has_no_archive_or_manage_link(app, db):
    admin, author, issue = _archivable(db)
    issue_id = issue.id
    try:
        for user in (admin, _user(db, 'senior_moderator'), _user(db)):
            for lang in ('nl', 'en'):
                page = _req(_client(app, user, lang), 'GET', f'/vraagstuk/{issue_id}').get_data(as_text=True)
                assert f'/vraagstuk/{issue_id}/archiveren' not in page and f'/vraagstuk/{issue_id}/terug-uit-archief' not in page
                assert 'Archiveren' not in page and 'Vraagstukbeheer' not in page and 'Manage issue' not in page
                assert f'href="/moderator/vraagstuk/{issue_id}/beheer"' not in page
                # An admin without a phase action sees no menu here at all.
                assert _menu(page) is None, (user.username, lang)
    finally:
        _cleanup(issue_id)


def test_phases_menu_only_with_phase_actions(app, db):
    from app import issue_lifecycle as lifecycle
    from tests.test_delete_rights import _issue
    author = _user(db)
    issue = _issue(db, author, f'Fasentest {uuid.uuid4().hex[:8]}')
    issue_id = issue.id
    try:
        assert lifecycle.can_step(issue, author)
        for lang, label, other in (('nl', 'Fasen', 'Beheer'), ('en', 'Phases', 'Manage')):
            menu = _menu(_req(_client(app, author, lang), 'GET', f'/vraagstuk/{issue_id}').get_data(as_text=True))
            assert menu is not None and f'{label}\n' in menu.split('</summary>')[0], lang
            assert other not in menu.split('</summary>')[0], lang
            assert f'action="/vraagstuk/{issue_id}/volgende-fase"' in menu, lang
            assert 'archiveren' not in menu and '/moderator/' not in menu, lang
        # The gear icon is gone; the caret stays.
        summary = menu.split('</summary>')[0]
        assert 'circle cx="12" cy="12" r="3"' not in summary and 'gu-beheer__caret' in summary
    finally:
        _cleanup(issue_id)


def test_archive_still_on_the_manage_screen(app, db):
    admin, _author, issue = _archivable(db)
    issue_id = issue.id
    try:
        c = _client(app, admin)
        manage = _req(c, 'GET', f'/moderator/vraagstuk/{issue_id}/beheer').get_data(as_text=True)
        assert 'Archiveren' in manage and f'action="/vraagstuk/{issue_id}/archiveren"' in manage
        assert _req(c, 'POST', f'/vraagstuk/{issue_id}/archiveren').status_code == 302
        assert db.session.get(Issue, issue_id).is_archived
        assert 'Restore from archive' in _req(_client(app, admin, 'en'), 'GET', f'/moderator/vraagstuk/{issue_id}/beheer').get_data(as_text=True)
    finally:
        _cleanup(issue_id)
