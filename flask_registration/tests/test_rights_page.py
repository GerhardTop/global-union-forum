"""Delete rights step 4: the admin "Rechten" page at /moderator/rechten.

Two on/off switches backed by the step-1 AppSetting keys; saving them changes
the permission outcome. Admin only (403 for everyone else). The word "Rechten"
on the manage screen is a link for an admin and plain text for a moderator.
"""
import re

import pytest

from app import issues
from app.models import AppSetting, ModerationAction
from tests.test_delete_issue import _clean_session, _cleanup, _client, _req, _user  # noqa: F401  (autouse fixture)
from tests.test_delete_rights import MOD_KEY, SENIOR_KEY, _issue, _proposal

PATH = '/moderator/rechten'


@pytest.fixture(autouse=True)
def _defaults(db):
    def reset():
        AppSetting.query.filter(AppSetting.key.in_([SENIOR_KEY, MOD_KEY])).delete(synchronize_session=False)
        ModerationAction.query.delete(synchronize_session=False)
        db.session.commit()
    reset()
    yield
    reset()


@pytest.fixture
def world(db):
    w = {'admin': _user(db, admin=True), 'senior': _user(db, 'senior_moderator'),
         'mod': _user(db, 'moderator'), 'contributor': _user(db)}
    w['issue'] = _issue(db, w['mod'], 'Vraagstuk voor rechten')
    w['proposal'] = _proposal(db, w['issue'], w['contributor'], 'Een voorstel.')
    issue_id = w['issue'].id
    yield w
    db.session.rollback()
    _cleanup(issue_id)


def _html(app, user, path=PATH, lang='nl'):
    return re.sub(r'\s+', ' ', _req(_client(app, user, lang), 'GET', path).get_data(as_text=True))


def _switch(page, key):
    """The checkbox input of one switch."""
    return re.search(rf'<input type="checkbox"[^>]*name="{re.escape(key)}"[^>]*>', page).group(0)


def _save(app, user, **on):
    data = {key: '1' for key, value in on.items() if value}
    return _req(_client(app, user), 'POST', PATH, data=data, follow_redirects=True)


def test_admin_sees_both_switches_with_the_stored_state(app, db, world):
    page = _html(app, world['admin'])
    assert '<h1 class="gu-admin__title">Rechten</h1>' in page
    assert 'Senior moderator mag ook vraagstukken verwijderen' in page
    assert 'Moderator mag voorstellen onder het eigen vraagstuk verwijderen' in page
    assert 'vraagstukken verwijderen blijft dan bij de admin.' in page
    assert 'dan kan een moderator die voorstellen alleen verbergen, niet verwijderen.' in page
    # The defaults: senior issues off, moderator proposals on.
    assert 'checked' not in _switch(page, SENIOR_KEY) and 'role="switch"' in _switch(page, SENIOR_KEY)
    assert 'checked' in _switch(page, MOD_KEY)
    # Stored values are shown.
    issues.set_delete_setting(SENIOR_KEY, True)
    issues.set_delete_setting(MOD_KEY, False)
    db.session.commit()
    page = _html(app, world['admin'])
    assert 'checked' in _switch(page, SENIOR_KEY) and 'checked' not in _switch(page, MOD_KEY)
    en = _html(app, world['admin'], lang='en')
    for text in ('<h1 class="gu-admin__title">Rights</h1>', 'Senior moderators may also delete issues',
                 'Moderators may delete proposals under their own issue'):
        assert text in en, text


def test_saving_flips_the_settings_and_changes_the_permissions(app, db, world):
    w = world
    # Defaults: a senior may not delete another's issue; the moderator may delete proposals under their own.
    assert not issues.can_delete_issue(w['senior'], w['issue'])
    assert issues.can_delete_proposal(w['mod'], w['proposal'])
    resp = _save(app, w['admin'], **{SENIOR_KEY: True, MOD_KEY: False})
    assert resp.status_code == 200 and 'Rechten opgeslagen.' in resp.get_data(as_text=True)
    db.session.expire_all()
    assert issues.delete_setting(SENIOR_KEY) is True and issues.delete_setting(MOD_KEY) is False
    assert db.session.get(AppSetting, SENIOR_KEY).updated_by_id == w['admin'].id
    assert issues.can_delete_issue(w['senior'], w['issue'])
    assert not issues.can_delete_proposal(w['mod'], w['proposal'])
    assert issues.can_hide_proposal(w['mod'], w['proposal'])  # hiding stays
    page = _html(app, w['admin'])
    assert 'checked' in _switch(page, SENIOR_KEY) and 'checked' not in _switch(page, MOD_KEY)
    # And back.
    _save(app, w['admin'], **{SENIOR_KEY: False, MOD_KEY: True})
    db.session.expire_all()
    assert not issues.can_delete_issue(w['senior'], w['issue'])
    assert issues.can_delete_proposal(w['mod'], w['proposal'])
    # The English confirmation.
    c = _client(app, w['admin'], 'en')
    resp = _req(c, 'POST', PATH, data={MOD_KEY: '1'}, follow_redirects=True)
    assert 'Rights saved.' in resp.get_data(as_text=True)


def test_non_admins_get_403(app, db, world):
    w = world
    for who in ('senior', 'mod', 'contributor'):
        assert _req(_client(app, w[who]), 'GET', PATH).status_code == 403, who
        assert _req(_client(app, w[who]), 'POST', PATH, data={SENIOR_KEY: '1'}).status_code == 403, who
    db.session.expire_all()
    assert issues.delete_setting(SENIOR_KEY) is False  # nothing changed
    assert app.test_client().get(PATH).status_code != 200  # not signed in: no page


def test_rechten_link_on_the_manage_screen(app, db, world):
    w = world
    manage = f'/moderator/vraagstuk/{w["issue"].id}/beheer'
    link = f'<a href="{PATH}" class="gu-mgmt__rightslink">'
    admin = _html(app, w['admin'], manage)
    assert f'Jij stelt deze rechten in bij {link}Rechten</a>.' in admin
    assert f'You set these rights under {link}Rights</a>.' in _html(app, w['admin'], manage, 'en')
    for who in ('mod', 'senior'):
        page = _html(app, w[who], manage)
        assert 'Een admin stelt deze rechten in bij Rechten.' in page and PATH not in page, who
    # Reachable in the moderator area and from the admin dashboard, for an admin only.
    assert f'href="{PATH}"' in _html(app, w['admin'], '/moderator')
    assert f'href="{PATH}"' not in _html(app, w['senior'], '/moderator')
    assert f'href="{PATH}"' in _html(app, w['admin'], '/admin')
