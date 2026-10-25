"""
Stored language preference per user (users.lang).

Covers: registration stores the current language; a language switch by a
logged-in user updates it (and leaves auto_translate to its own rule); login
sets the session language from users.lang, also on a new device; without a
stored preference the session language is kept.

Shared conftest app (CSRF off).
"""
import uuid

import pytest

from app.models import User
from tests.test_forum_proposals import _client, _req, make_user  # noqa: F401
from tests.test_forum_conduct import _fresh_rate_limits  # noqa: F401


@pytest.fixture(autouse=True)
def no_mail(monkeypatch):
    import app.mail as mail_module
    monkeypatch.setattr(mail_module, 'send_email', lambda *a, **k: True)


def _user(db, user_id):
    db.session.expire_all()
    return db.session.get(User, user_id)


def _session_lang(client):
    with client.session_transaction() as s:
        return s.get('lang')


class TestRegistration:
    @pytest.mark.parametrize('lang', ['nl', 'en'])
    def test_registration_stores_current_language(self, app, db, lang):
        name = f'r_{uuid.uuid4().hex[:6]}'
        data = {'username': name, 'email': f'{name}@example.com', 'first_name': '', 'last_name': '',
                'linkedin_url': '', 'password': 'ValidPassword123!', 'password_confirm': 'ValidPassword123!',
                'accept_rules': '1'}
        _req(_client(app, lang=lang), 'POST', '/aanmelden', data=data)
        user = User.query.filter_by(username=name).one()
        assert user.lang == lang


class TestSwitch:
    def test_switch_updates_stored_language(self, app, db, make_user):
        anna = make_user()
        assert anna.lang is None
        client = _client(app, anna, 'nl')
        _req(client, 'GET', '/lang/en')
        assert _user(db, anna.id).lang == 'en' and _session_lang(client) == 'en'
        _req(client, 'GET', '/lang/nl')
        assert _user(db, anna.id).lang == 'nl' and _session_lang(client) == 'nl'
        _req(client, 'GET', '/lang/de')  # unknown language: no change
        assert _user(db, anna.id).lang == 'nl'

    def test_anonymous_switch_changes_only_the_session(self, app, db):
        client = _client(app, None, 'nl')
        _req(client, 'GET', '/lang/en')
        assert _session_lang(client) == 'en'


class TestLogin:
    def _login(self, client, user):
        return _req(client, 'POST', '/login', data={'email': user.email, 'password': 'Sterk1!ww'})

    def test_login_opens_in_stored_language_on_new_device(self, app, db, make_user):
        anna = make_user()
        anna.lang = 'en'
        db.session.commit()
        client = _client(app, None, 'nl')  # a new device, browsing in Dutch
        assert self._login(client, anna).status_code == 302
        assert _session_lang(client) == 'en'
        page = _req(client, 'GET', '/forum').get_data(as_text=True)
        assert 'lang="en"' in page

    def test_login_without_preference_keeps_session_language(self, app, db, make_user):
        bram = make_user()
        client = _client(app, None, 'en')
        assert self._login(client, bram).status_code == 302
        assert _session_lang(client) == 'en'
        assert _user(db, bram.id).lang is None
