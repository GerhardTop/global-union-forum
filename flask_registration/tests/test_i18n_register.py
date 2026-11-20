"""Multilingual phase 0, step 5: the language register.

config.py holds the register (LANGUAGES), the switched-on languages
(ENABLED_LANGUAGES) and the default language; app/i18n.py is the one place
that reads them. German is on (phase 1, a machine translation); these tests
switch French on for a moment, with a minimal French catalog in a temporary
folder (never in app/translations), and show that switching a language on
needs no other code change.
"""
import importlib

import pytest
from babel.messages.catalog import Catalog
from babel.messages.mofile import write_mo
from flask import session
from flask_babel import force_locale, get_locale, refresh

import config
from app import digest, i18n, notifications
from app.models import Notification, User
from tests.test_issue_polish import _client, _req, make_user  # noqa: F401  (fixture)

FRENCH = {
    'functioning democracies': 'de démocraties qui fonctionnent',
    'Your daily summary': 'Ton résumé quotidien',
    'News about your proposal': 'Du nouveau sur ta proposition',
}


@pytest.fixture
def french(app, monkeypatch, tmp_path):
    """French switched on, with a tiny catalog in tmp_path."""
    catalog = Catalog(locale='fr')
    for msgid, msgstr in FRENCH.items():
        catalog.add(msgid, msgstr)
    folder = tmp_path / 'fr' / 'LC_MESSAGES'
    folder.mkdir(parents=True)
    with open(folder / 'messages.mo', 'wb') as f:
        write_mo(f, catalog)
    babel = app.extensions['babel']
    monkeypatch.setattr(config, 'ENABLED_LANGUAGES', ['nl', 'en', 'de', 'fr'])
    monkeypatch.setattr(babel, 'translation_directories', babel.translation_directories + [str(tmp_path)])
    cache = babel.instance.domain_instance.cache
    cache.pop(('fr', 'messages'), None)
    yield
    cache.pop(('fr', 'messages'), None)
    refresh()


# ── The helpers ────────────────────────────────────────────────────────────

def test_register_helpers():
    assert i18n.enabled_codes() == ['nl', 'en', 'de']
    assert [lang['code'] for lang in i18n.enabled_languages()] == ['nl', 'en', 'de']
    assert i18n.is_enabled('nl') and i18n.is_enabled('en') and i18n.is_enabled('de')
    assert not i18n.is_enabled('fr')
    assert not i18n.is_enabled(None)
    assert i18n.language_name('de') == 'Deutsch' and i18n.language_name('fr') == 'Français'
    assert i18n.text_direction('es') == 'ltr' and i18n.text_direction('xx') == 'ltr'
    assert i18n.normalize('en') == 'en'
    assert i18n.normalize('fr') == 'nl' and i18n.normalize(None) == 'nl' and i18n.normalize('') == 'nl'
    assert i18n.auto_translate_default('en') and not i18n.auto_translate_default('nl')
    assert i18n.is_reviewed('nl') and i18n.is_reviewed('en')
    assert not i18n.is_reviewed('de') and not i18n.is_reviewed('xx')
    assert not i18n.auto_translate_default('fr')  # not enabled: the default language


def test_every_enabled_language_is_in_the_register():
    codes = {lang['code'] for lang in config.LANGUAGES}
    assert set(config.ENABLED_LANGUAGES) <= codes
    assert config.DEFAULT_LANGUAGE in config.ENABLED_LANGUAGES
    assert config.SOURCE_LANGUAGE in codes
    assert all(lang['dir'] in ('ltr', 'rtl') and lang['name'] for lang in config.LANGUAGES)
    assert all(isinstance(lang['reviewed'], bool) for lang in config.LANGUAGES)


def test_a_disabled_language_is_ignored(app):
    """/lang/fr does nothing while French is off; the browser's French falls back."""
    client = app.test_client()
    _req(client, 'GET', '/lang/fr')
    with client.session_transaction() as s:
        assert 'lang' not in s
    page = _req(client, 'GET', '/', headers={'Accept-Language': 'fr'}).get_data(as_text=True)
    assert '<html lang="nl" dir="ltr">' in page


def test_the_switcher_shows_the_enabled_languages(app):
    page = _req(_client(app, None, 'en'), 'GET', '/').get_data(as_text=True)
    assert '<html lang="en" dir="ltr">' in page
    for code in ('nl', 'en', 'de'):
        assert f'lang="{code}" hreflang="{code}"' in page
    assert 'hreflang="fr"' not in page


# ── German (on, machine translation) ───────────────────────────────────────

def test_german_is_on(app):
    client = app.test_client()
    resp = _req(client, 'GET', '/lang/de')
    assert resp.status_code == 302
    with client.session_transaction() as s:
        assert s['lang'] == 'de'
    page = _req(client, 'GET', '/').get_data(as_text=True)
    assert '<html lang="de" dir="ltr">' in page
    assert 'funktionierender Demokratien' in page
    # Header (nav and end dropdown) and footer each list the three enabled languages.
    for code in ('nl', 'en', 'de'):
        assert page.count(f'hreflang="{code}"') == 3, code
    assert 'hreflang="es"' not in page and 'hreflang="fr"' not in page
    assert 'is-active" lang="de" hreflang="de"' in page


def test_the_footer_says_when_a_page_is_machine_translated(app):
    """Only in a language the register marks as not reviewed (de), in that language."""
    note = {'de': 'Diese Seite wurde maschinell übersetzt.'}
    for lang in ('nl', 'en', 'de'):
        page = _req(_client(app, None, lang), 'GET', '/').get_data(as_text=True)
        refresh()
        assert ('gu-footer__machine' in page) == (lang == 'de'), lang
        if lang in note:
            assert note[lang] in page


def test_get_locale_with_german_on(app):
    with app.test_request_context('/', headers={'Accept-Language': 'de-DE,de;q=0.9,en;q=0.5'}):
        refresh()
        assert str(get_locale()) == 'de'
    with app.test_request_context('/', headers={'Accept-Language': 'fr'}):
        refresh()
        assert str(get_locale()) == 'nl'
    with app.test_request_context('/'):
        session['lang'] = 'de'
        refresh()
        assert str(get_locale()) == 'de'
    refresh()


def test_switching_french_on_needs_no_code_change(app, french):
    client = app.test_client()
    _req(client, 'GET', '/lang/fr')
    page = _req(client, 'GET', '/').get_data(as_text=True)
    assert '<html lang="fr" dir="ltr">' in page
    assert 'de démocraties qui fonctionnent' in page
    for code in ('nl', 'en', 'de', 'fr'):
        assert page.count(f'hreflang="{code}"') == 3, code


def test_a_member_choosing_german(app, db, make_user):
    user = make_user()
    client = _client(app, user, 'nl')
    _req(client, 'GET', '/lang/de')
    db.session.expire_all()
    stored = db.session.get(User, user.id)
    assert stored.lang == 'de'
    assert stored.auto_translate is True  # not the default language: translate by default
    page = _req(client, 'GET', '/profile').get_data(as_text=True)
    assert '<html lang="de" dir="ltr">' in page
    assert '>Deutsch</a>' in page and '>Nederlands</a>' in page and '>English</a>' in page


def test_mail_to_a_german_member(app, db, make_user):
    """Notification, digest and password reset mails render in German, with du."""
    auth_module = importlib.import_module('app.routes.auth')  # the module, not the blueprint
    user = make_user()
    user.lang = 'de'
    user.first_name = 'Anna'
    assert digest.lang_for(user) == 'de'
    n = Notification(user_id=user.id, category='voorstel', event_key='issueproposal:0')
    with app.test_request_context('/'), force_locale('nl'):
        subject, _html = notifications.email_for(user, n)
        digest_subject, digest_html = digest._email([], digest.lang_for(user))
    assert subject == 'Neues zu deinem Vorschlag'
    assert digest_subject == 'Deine tägliche Zusammenfassung'
    assert 'In deinem Profil kannst du die E-Mail abschalten' in digest_html
    sent = []
    original = auth_module.send_email
    auth_module.send_email = lambda to, subj, html: sent.append((subj, html)) or True
    try:
        with app.test_request_context('/'), force_locale('nl'):
            auth_module._send_reset_email(user, 'https://example.org/reset/x', 'de')
    finally:
        auth_module.send_email = original
    (reset_subject, reset_html), = sent
    assert reset_subject == 'Global Union Forum: Passwort zurücksetzen'
    assert 'Hallo Anna,' in reset_html and 'Dieser Link ist 1 Stunde gültig.' in reset_html


def test_mail_to_a_member_falls_back_when_the_language_is_off(app, db, make_user):
    user = make_user()
    user.lang = 'fr'
    assert digest.lang_for(user) == 'nl'


def test_language_name_in_another_language():
    assert i18n.language_name_in('en', 'nl') == 'Engels'
    assert i18n.language_name_in('nl', 'en') == 'Dutch'
    assert i18n.language_name_in('de', 'de') == 'Deutsch'
    assert i18n.language_name_in('en', 'xx') == 'EN'   # unknown locale: the code
