"""Multilingual phase 0, step 5: the language register.

config.py holds the register (LANGUAGES), the switched-on languages
(ENABLED_LANGUAGES) and the default language; app/i18n.py is the one place
that reads them. These tests switch German on for a moment, with a minimal
German catalog in a temporary folder (never in app/translations), and show
that switching a language on needs no other code change.
"""
import pytest
from babel.messages.catalog import Catalog
from babel.messages.mofile import write_mo
from flask import session
from flask_babel import force_locale, get_locale, refresh

import config
from app import digest, i18n, notifications
from app.models import Notification, User
from tests.test_issue_polish import _client, _req, make_user  # noqa: F401  (fixture)

GERMAN = {
    'functioning democracies': 'funktionierender Demokratien',
    'Your daily summary': 'Deine tägliche Zusammenfassung',
    'News about your proposal': 'Neuigkeiten zu deinem Vorschlag',
}


@pytest.fixture
def german(app, monkeypatch, tmp_path):
    """German switched on, with a tiny catalog in tmp_path."""
    catalog = Catalog(locale='de')
    for msgid, msgstr in GERMAN.items():
        catalog.add(msgid, msgstr)
    folder = tmp_path / 'de' / 'LC_MESSAGES'
    folder.mkdir(parents=True)
    with open(folder / 'messages.mo', 'wb') as f:
        write_mo(f, catalog)
    babel = app.extensions['babel']
    monkeypatch.setattr(config, 'ENABLED_LANGUAGES', ['nl', 'en', 'de'])
    monkeypatch.setattr(babel, 'translation_directories', babel.translation_directories + [str(tmp_path)])
    cache = babel.instance.domain_instance.cache
    cache.pop(('de', 'messages'), None)
    yield
    cache.pop(('de', 'messages'), None)
    refresh()


# ── The helpers ────────────────────────────────────────────────────────────

def test_register_helpers():
    assert i18n.enabled_codes() == ['nl', 'en']
    assert [lang['code'] for lang in i18n.enabled_languages()] == ['nl', 'en']
    assert i18n.is_enabled('nl') and i18n.is_enabled('en') and not i18n.is_enabled('de')
    assert not i18n.is_enabled(None)
    assert i18n.language_name('de') == 'Deutsch' and i18n.language_name('fr') == 'Français'
    assert i18n.text_direction('es') == 'ltr' and i18n.text_direction('xx') == 'ltr'
    assert i18n.normalize('en') == 'en'
    assert i18n.normalize('fr') == 'nl' and i18n.normalize(None) == 'nl' and i18n.normalize('') == 'nl'
    assert i18n.auto_translate_default('en') and not i18n.auto_translate_default('nl')
    assert not i18n.auto_translate_default('fr')  # not enabled: the default language


def test_every_enabled_language_is_in_the_register():
    codes = {lang['code'] for lang in config.LANGUAGES}
    assert set(config.ENABLED_LANGUAGES) <= codes
    assert config.DEFAULT_LANGUAGE in config.ENABLED_LANGUAGES
    assert config.SOURCE_LANGUAGE in codes
    assert all(lang['dir'] in ('ltr', 'rtl') and lang['name'] for lang in config.LANGUAGES)


def test_a_disabled_language_is_ignored(app):
    """/lang/de does nothing while German is off; the browser's German falls back."""
    client = app.test_client()
    _req(client, 'GET', '/lang/de')
    with client.session_transaction() as s:
        assert 'lang' not in s
    page = _req(client, 'GET', '/', headers={'Accept-Language': 'de'}).get_data(as_text=True)
    assert '<html lang="nl" dir="ltr">' in page


def test_the_switcher_shows_the_enabled_languages(app):
    page = _req(_client(app, None, 'en'), 'GET', '/').get_data(as_text=True)
    assert '<html lang="en" dir="ltr">' in page
    for code in ('nl', 'en'):
        assert f'lang="{code}" hreflang="{code}"' in page
    assert 'hreflang="de"' not in page


# ── German switched on ─────────────────────────────────────────────────────

def test_switching_german_on(app, german):
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


def test_get_locale_with_german_on(app, german):
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


def test_a_member_choosing_german(app, db, make_user, german):
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


def test_mail_to_a_german_member(app, db, make_user, german):
    user = make_user()
    user.lang = 'de'
    assert digest.lang_for(user) == 'de'
    n = Notification(user_id=user.id, category='voorstel', event_key='issueproposal:0')
    with app.test_request_context('/'), force_locale('nl'):
        subject, _html = notifications.email_for(user, n)
        digest_subject, _html = digest._email([], digest.lang_for(user))
    assert subject == 'Neuigkeiten zu deinem Vorschlag'
    assert digest_subject == 'Deine tägliche Zusammenfassung'


def test_mail_to_a_german_member_falls_back_when_german_is_off(app, db, make_user):
    user = make_user()
    user.lang = 'de'
    assert digest.lang_for(user) == 'nl'


def test_language_name_in_another_language():
    assert i18n.language_name_in('en', 'nl') == 'Engels'
    assert i18n.language_name_in('nl', 'en') == 'Dutch'
    assert i18n.language_name_in('de', 'de') == 'Deutsch'
    assert i18n.language_name_in('en', 'xx') == 'EN'   # unknown locale: the code
