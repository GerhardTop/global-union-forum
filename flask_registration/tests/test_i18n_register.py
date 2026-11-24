"""Multilingual phase 0, step 5: the language register.

config.py holds the register (LANGUAGES), the switched-on languages
(ENABLED_LANGUAGES) and the default language; app/i18n.py is the one place
that reads them. German, Spanish and French are on (phase 1, machine
translations); these tests add Italian to the register and switch it on for a
moment, with a minimal Italian catalog in a temporary folder (never in
app/translations), and show that switching a language on needs no other code
change. Italian, not in the register, is also the example of a language that
is not enabled.
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

ITALIAN = {
    'functioning democracies': 'democrazie funzionanti',
    'Your daily summary': 'Il tuo riepilogo giornaliero',
    'News about your proposal': 'Novità sulla tua proposta',
}


FRENCH_NOTE = 'Cette page a été traduite automatiquement.'


@pytest.fixture
def italian(app, monkeypatch, tmp_path):
    """Italian in the register and switched on, with a tiny catalog in tmp_path."""
    catalog = Catalog(locale='it')
    for msgid, msgstr in ITALIAN.items():
        catalog.add(msgid, msgstr)
    folder = tmp_path / 'it' / 'LC_MESSAGES'
    folder.mkdir(parents=True)
    with open(folder / 'messages.mo', 'wb') as f:
        write_mo(f, catalog)
    babel = app.extensions['babel']
    monkeypatch.setattr(config, 'LANGUAGES', config.LANGUAGES + [
        {"code": "it", "name": "Italiano", "english": "Italian", "dir": "ltr", "reviewed": False}])
    monkeypatch.setattr(config, 'ENABLED_LANGUAGES', ['nl', 'en', 'de', 'es', 'fr', 'it'])
    monkeypatch.setattr(babel, 'translation_directories', babel.translation_directories + [str(tmp_path)])
    cache = babel.instance.domain_instance.cache
    cache.pop(('it', 'messages'), None)
    yield
    cache.pop(('it', 'messages'), None)
    refresh()


# ── The helpers ────────────────────────────────────────────────────────────

def test_register_helpers():
    assert i18n.enabled_codes() == ['nl', 'en', 'de', 'es', 'fr']
    assert [lang['code'] for lang in i18n.enabled_languages()] == ['nl', 'en', 'de', 'es', 'fr']
    assert all(i18n.is_enabled(code) for code in ('nl', 'en', 'de', 'es', 'fr'))
    assert not i18n.is_enabled('it')
    assert not i18n.is_enabled(None)
    assert i18n.language_name('de') == 'Deutsch' and i18n.language_name('fr') == 'Français'
    assert i18n.text_direction('es') == 'ltr' and i18n.text_direction('xx') == 'ltr'
    assert i18n.normalize('en') == 'en'
    assert i18n.normalize('it') == 'nl' and i18n.normalize(None) == 'nl' and i18n.normalize('') == 'nl'
    assert i18n.auto_translate_default('en') and not i18n.auto_translate_default('nl')
    assert i18n.is_reviewed('nl') and i18n.is_reviewed('en')
    assert not any(i18n.is_reviewed(code) for code in ('de', 'es', 'fr', 'xx'))
    assert not i18n.auto_translate_default('it')  # not enabled: the default language


def test_every_enabled_language_is_in_the_register():
    codes = {lang['code'] for lang in config.LANGUAGES}
    assert set(config.ENABLED_LANGUAGES) <= codes
    assert config.DEFAULT_LANGUAGE in config.ENABLED_LANGUAGES
    assert config.SOURCE_LANGUAGE in codes
    assert all(lang['dir'] in ('ltr', 'rtl') and lang['name'] for lang in config.LANGUAGES)
    assert all(isinstance(lang['reviewed'], bool) for lang in config.LANGUAGES)


def test_a_disabled_language_is_ignored(app):
    """/lang/it does nothing while Italian is off; the browser's Italian falls back
    to the visitor fallback (English)."""
    client = app.test_client()
    _req(client, 'GET', '/lang/it')
    with client.session_transaction() as s:
        assert 'lang' not in s
    page = _req(client, 'GET', '/', headers={'Accept-Language': 'it'}).get_data(as_text=True)
    assert '<html lang="en" dir="ltr">' in page


def test_the_switcher_shows_the_enabled_languages(app):
    page = _req(_client(app, None, 'en'), 'GET', '/').get_data(as_text=True)
    assert '<html lang="en" dir="ltr">' in page
    for code in ('nl', 'en', 'de', 'es', 'fr'):
        assert f'lang="{code}" hreflang="{code}"' in page
    assert 'hreflang="it"' not in page


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
    # Header (nav and end dropdown) and footer each list the five enabled languages.
    for code in ('nl', 'en', 'de', 'es', 'fr'):
        assert page.count(f'hreflang="{code}"') == 3, code
    assert 'hreflang="it"' not in page
    assert 'is-active" lang="de" hreflang="de"' in page


def test_the_footer_says_when_a_page_is_machine_translated(app):
    """Only in a language the register marks as not reviewed (de, es, fr), in that language."""
    note = {'de': 'Diese Seite wurde maschinell übersetzt.', 'es': 'Esta página se tradujo automáticamente.',
            'fr': FRENCH_NOTE}
    for lang in ('nl', 'en', 'de', 'es', 'fr'):
        page = _req(_client(app, None, lang), 'GET', '/').get_data(as_text=True)
        refresh()
        assert ('gu-footer__machine' in page) == (lang in note), lang
        if lang in note:
            assert note[lang] in page


def test_get_locale_with_german_on(app):
    with app.test_request_context('/', headers={'Accept-Language': 'de-DE,de;q=0.9,en;q=0.5'}):
        refresh()
        assert str(get_locale()) == 'de'
    with app.test_request_context('/', headers={'Accept-Language': 'it'}):
        refresh()
        assert str(get_locale()) == 'en'   # VISITOR_FALLBACK_LANGUAGE, not nl
    with app.test_request_context('/'):
        session['lang'] = 'de'
        refresh()
        assert str(get_locale()) == 'de'
    refresh()


def test_switching_italian_on_needs_no_code_change(app, italian):
    client = app.test_client()
    _req(client, 'GET', '/lang/it')
    page = _req(client, 'GET', '/').get_data(as_text=True)
    assert '<html lang="it" dir="ltr">' in page
    assert 'democrazie funzionanti' in page
    for code in ('nl', 'en', 'de', 'es', 'fr', 'it'):
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


def _mails(app, user, lang):
    """(notification subject, digest subject, digest html, reset subject, reset html) for `user` in `lang`."""
    auth_module = importlib.import_module('app.routes.auth')  # the module, not the blueprint
    user.lang = lang
    user.first_name = 'Anna'
    assert digest.lang_for(user) == lang
    n = Notification(user_id=user.id, category='voorstel', event_key='issueproposal:0')
    with app.test_request_context('/'), force_locale('nl'):
        subject, _html = notifications.email_for(user, n)
        digest_subject, digest_html = digest._email([], digest.lang_for(user))
    sent = []
    original = auth_module.send_email
    auth_module.send_email = lambda to, subj, html: sent.append((subj, html)) or True
    try:
        with app.test_request_context('/'), force_locale('nl'):
            auth_module._send_reset_email(user, 'https://example.org/reset/x', lang)
    finally:
        auth_module.send_email = original
    (reset_subject, reset_html), = sent
    return subject, digest_subject, digest_html, reset_subject, reset_html


def test_mail_to_a_german_member(app, db, make_user):
    """Notification, digest and password reset mails render in German, with du."""
    subject, digest_subject, digest_html, reset_subject, reset_html = _mails(app, make_user(), 'de')
    assert subject == 'Neues zu deinem Vorschlag'
    assert digest_subject == 'Deine tägliche Zusammenfassung'
    assert 'In deinem Profil kannst du die E-Mail abschalten' in digest_html
    assert reset_subject == 'Global Union Forum: Passwort zurücksetzen'
    assert 'Hallo Anna,' in reset_html and 'Dieser Link ist 1 Stunde gültig.' in reset_html


def test_mail_to_a_spanish_member(app, db, make_user):
    """Notification, digest and password reset mails render in Spanish, with tú."""
    subject, digest_subject, digest_html, reset_subject, reset_html = _mails(app, make_user(), 'es')
    assert subject == 'Novedades sobre tu propuesta'
    assert digest_subject == 'Tu resumen diario'
    assert 'Puedes desactivar el correo en tu perfil' in digest_html
    assert reset_subject == 'Global Union Forum: restablece tu contraseña'
    assert 'Hola, Anna:' in reset_html and 'Este enlace caduca en 1 hora.' in reset_html


def test_mail_to_a_french_member(app, db, make_user):
    """Notification, digest and password reset mails render in French, with vous."""
    subject, digest_subject, digest_html, reset_subject, reset_html = _mails(app, make_user(), 'fr')
    assert subject == 'Du nouveau sur votre proposition'
    assert digest_subject == 'Votre résumé quotidien'
    assert 'Vous pouvez désactiver cet e-mail dans votre profil' in digest_html
    assert reset_subject == 'Global Union Forum\u00a0: réinitialisez votre mot de passe'
    assert 'Bonjour Anna,' in reset_html and 'Ce lien expire dans 1 heure.' in reset_html


def test_mail_to_a_member_falls_back_when_the_language_is_off(app, db, make_user):
    user = make_user()
    user.lang = 'it'
    assert digest.lang_for(user) == 'nl'


def test_language_name_in_another_language():
    assert i18n.language_name_in('en', 'nl') == 'Engels'
    assert i18n.language_name_in('nl', 'en') == 'Dutch'
    assert i18n.language_name_in('de', 'de') == 'Deutsch'
    assert i18n.language_name_in('en', 'xx') == 'EN'   # unknown locale: the code
