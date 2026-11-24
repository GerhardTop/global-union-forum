"""The language choice: the visitor fallback (English when the browser asks
for no enabled language), the language cookie that keeps a choice beyond the
session (/lang/<code> and log in set it), the order in get_locale (session,
cookie, browser, fallback), and the switcher in the header for visitors and
members.

Shared conftest app (CSRF off).
"""
import re

import pytest

import config
from app import i18n
from app.models import User
from tests.test_forum_conduct import _fresh_rate_limits  # noqa: F401
from tests.test_forum_proposals import _client, _req, make_user  # noqa: F401


@pytest.fixture(autouse=True)
def no_mail(monkeypatch):
    import app.mail as mail_module
    monkeypatch.setattr(mail_module, 'send_email', lambda *a, **k: True)


def _page_lang(resp):
    m = re.search(r'<html lang="([a-z]+)"', resp.get_data(as_text=True))
    assert m, 'no <html lang>'
    return m.group(1)


def _bare(app):
    """A new browser: no session, no cookie."""
    return app.test_client()


def _expire_session(client):
    client.delete_cookie('session')


def _lang_cookie_header(resp):
    headers = [h for h in resp.headers.getlist('Set-Cookie') if h.startswith(f'{i18n.LANG_COOKIE}=')]
    assert len(headers) == 1, resp.headers.getlist('Set-Cookie')
    return headers[0]


def test_the_register_has_a_visitor_fallback_and_keeps_the_default():
    assert config.VISITOR_FALLBACK_LANGUAGE == 'en' and i18n.visitor_fallback_language() == 'en'
    assert config.DEFAULT_LANGUAGE == 'nl' and i18n.default_language() == 'nl'
    assert i18n.is_enabled(config.VISITOR_FALLBACK_LANGUAGE)


@pytest.mark.parametrize('accept,expected', [
    ('it-IT,it;q=0.9', 'en'),          # no enabled language: the visitor fallback, not nl
    (None, 'en'),                      # no header at all: the same
    ('de-DE,de;q=0.9,en;q=0.5', 'de'),
    ('it,fr;q=0.8', 'fr'),             # the best enabled match
    ('nl-BE', 'nl'),
])
def test_a_visitor_without_a_choice_gets_the_browser_language_or_english(app, db, accept, expected):
    headers = {'Accept-Language': accept} if accept else {}
    assert _page_lang(_bare(app).get('/', headers=headers)) == expected


def test_choosing_a_language_sets_the_cookie(app, db):
    client = _bare(app)
    resp = client.get('/lang/de')
    header = _lang_cookie_header(resp)
    assert header.startswith('lang=de;')
    for part in ('Max-Age=31536000', 'Path=/', 'SameSite=Lax', 'HttpOnly'):
        assert part in header, header
    assert 'Secure' not in header                    # only in production (https)
    assert client.get_cookie('lang').value == 'de'
    other = client.get('/lang/it')
    assert not any(h.startswith('lang=') for h in other.headers.getlist('Set-Cookie'))
    assert client.get_cookie('lang').value == 'de'   # a language that is not enabled changes nothing


def test_the_cookie_is_secure_in_production(app, db, monkeypatch):
    monkeypatch.setitem(app.config, 'LANG_COOKIE_SECURE', True)
    assert 'Secure' in _lang_cookie_header(_bare(app).get('/lang/fr'))


def test_a_chosen_language_survives_the_end_of_the_session(app, db):
    client = _bare(app)
    client.get('/lang/es')
    _expire_session(client)
    assert client.get_cookie('session') is None
    # The browser asks for Italian (no match) or German: the cookie wins over both.
    assert _page_lang(client.get('/', headers={'Accept-Language': 'it'})) == 'es'
    assert _page_lang(client.get('/', headers={'Accept-Language': 'de'})) == 'es'


def test_the_session_wins_over_the_cookie(app, db):
    client = _bare(app)
    client.set_cookie('lang', 'fr')
    with client.session_transaction() as s:
        s['lang'] = 'de'
    assert _page_lang(client.get('/')) == 'de'
    client.set_cookie('lang', 'xx')                  # a broken cookie is ignored
    _expire_session(client)
    assert _page_lang(client.get('/', headers={'Accept-Language': 'nl'})) == 'nl'


class TestLogin:
    def _login(self, client, user):
        return _req(client, 'POST', '/login', data={'email': user.email, 'password': 'Sterk1!ww'})

    def test_log_in_sets_the_members_language_and_the_cookie(self, app, db, make_user):
        anna = make_user()
        anna.lang = 'fr'
        db.session.commit()
        client = _bare(app)
        client.get('/lang/de')                       # chosen as a visitor on this device
        resp = self._login(client, anna)
        assert resp.status_code == 302
        assert _lang_cookie_header(resp).startswith('lang=fr;')
        assert client.get_cookie('lang').value == 'fr'
        assert _page_lang(_req(client, 'GET', '/forum')) == 'fr'
        # After the session ends (and logged out), the device still opens in French.
        _expire_session(client)
        assert _page_lang(client.get('/', headers={'Accept-Language': 'de'})) == 'fr'

    def test_log_in_without_a_stored_language_keeps_the_choice(self, app, db, make_user):
        bram = make_user()
        client = _bare(app)
        client.get('/lang/es')
        _expire_session(client)                      # the choice lives on in the cookie only
        resp = self._login(client, bram)
        assert resp.status_code == 302
        assert client.get_cookie('lang').value == 'es'
        assert _page_lang(_req(client, 'GET', '/forum')) == 'es'
        db.session.expire_all()
        assert db.session.get(User, bram.id).lang is None

    def test_a_member_switching_language_updates_the_cookie(self, app, db, make_user):
        anna = make_user()
        client = _client(app, anna, 'nl')
        _req(client, 'GET', '/lang/de')
        assert client.get_cookie('lang').value == 'de'
        db.session.expire_all()
        assert db.session.get(User, anna.id).lang == 'de'


def _switchers(page):
    """The desktop dropdown and the one in the mobile menu, with their links."""
    return {
        'desktop': 'id="langDd"' in page,
        'mobile': 'class="gu-header__nav-lang"' in page,
        'links': sorted(set(re.findall(r'href="/lang/([a-z]+)"', page))),
    }


def test_the_switcher_is_in_the_header_for_visitors_and_members(app, db, make_user):
    enabled = sorted(i18n.enabled_codes())
    visitor = _switchers(_bare(app).get('/').get_data(as_text=True))
    assert visitor == {'desktop': True, 'mobile': True, 'links': enabled}
    anna = make_user()
    page = _req(_client(app, anna, 'nl'), 'GET', '/').get_data(as_text=True)
    assert _switchers(page) == {'desktop': True, 'mobile': True, 'links': enabled}
    assert 'id="avatarBtn"' in page
    # Next to the avatar: the dropdown comes right before it in the header's end.
    end = page[page.index('class="gu-header__end"'):]
    assert end.index('id="langDd"') < end.index('id="avatarWrap"')


def test_the_privacy_page_lists_the_language_cookie(app, db):
    for code, snippet in [('nl', 'onthoudt je taalkeuze een jaar lang'),
                          ('en', 'remembers your language choice for one year'),
                          ('de', 'speichert deine Sprachwahl ein Jahr lang'),
                          ('es', 'recuerda el idioma que elegiste durante un año'),
                          ('fr', 'mémorise votre choix de langue pendant un an')]:
        page = _req(_client(app, None, code), 'GET', '/privacy').get_data(as_text=True)
        assert '<code>lang</code>' in page and snippet in page, code
