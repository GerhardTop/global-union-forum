"""Multilingual phase 0, step 4: Python texts through gettext.

- Module-level labels are lazy: read inside force_locale they follow the
  language (a plain gettext at import would freeze the default language).
- Stored values stay codes; only the display is translated.
- Mails are rendered in the recipient's language: force_locale(user.lang),
  Dutch when it is empty; an invitation in the inviter's language at the
  moment of sending. Each mail kind is checked with an NL and an EN recipient.
"""
from datetime import datetime, timedelta

import pytest
from flask_babel import force_locale

from app import digest, notifications, quality, reports, status
from app.models import Notification, time_ago
from tests.test_issue_polish import _client, _req, make_user  # noqa: F401  (fixture)


def _both(app, fn):
    out = {}
    for lang in ('nl', 'en'):
        with app.test_request_context('/'), force_locale(lang):
            out[lang] = str(fn())
    return out


# ── 1. Lazy module-level labels ────────────────────────────────────────────

@pytest.mark.parametrize('fn, nl, en', [
    (lambda: status.STATUS_LABELS['reader'], 'Lezer', 'Reader'),
    (lambda: status.STATUS_LABELS['voter'], 'Stemmer', 'Voter'),
    (lambda: status.THRESHOLD_LABELS['voter_min'], 'Stemmer: minimaal aantal kwaliteitsbijdragen',
     'Voter: minimum number of quality contributions'),
    (lambda: quality.CRITERIA_LABELS['K1'], 'Nieuw argument', 'New argument'),
    (lambda: notifications.CATEGORIES[0][2], 'Reactie op je bijdrage', 'A reply to your contribution'),
    (lambda: notifications._SUBJECTS['voorstel'], 'Nieuws over je voorstel', 'News about your proposal'),
    (lambda: notifications._SYSTEM_NOTES['schorsing'], 'Tot dan kun je blijven lezen, maar niet bijdragen of stemmen.',
     'Until then you can still read, but not contribute or vote.'),
    (lambda: notifications._PHASES['stemmen'], 'Stemmen', 'Voting'),
    (lambda: reports.wait_bucket(datetime.utcnow() - timedelta(days=3))['label'], '3 dagen', '3 days'),
    (lambda: time_ago(datetime.utcnow() - timedelta(days=1, hours=1)), '1 dag geleden', '1 day ago'),
    (lambda: time_ago(datetime.utcnow() - timedelta(hours=2, minutes=5)), '2 uur geleden', '2 hours ago'),
    (lambda: time_ago(datetime.utcnow()), 'zojuist', 'just now'),
])
def test_module_level_labels_follow_the_language(app, fn, nl, en):
    got = _both(app, fn)
    assert got == {'nl': nl, 'en': en}
    assert got['nl'] != got['en'] or nl == en


def test_stored_values_stay_codes(app):
    assert set(status.STATUS_LABELS) == {'reader', 'contributor', 'voter', 'moderator', 'senior'}
    assert set(quality.CRITERIA_LABELS) == {'K1', 'K2', 'K3'}
    assert notifications.CATEGORY_KEYS == ('reactie', 'voorstel', 'gevolgd', 'uitslag', 'feit', 'melding',
                                           'bezwaar', 'betwist')
    assert {section for _k, section, *_r in notifications.CATEGORIES} == {'deelname', 'moderatie'}
    with app.test_request_context('/'):
        assert set(reports.wait_bucket(datetime.utcnow())) == {'label', 'sev'}


# ── 3. Mails in the recipient's language ───────────────────────────────────

@pytest.fixture
def outbox(monkeypatch):
    sent = []

    def fake(to, subject, html):
        sent.append({'to': to, 'subject': subject, 'html': html})
        return True
    import sys
    from app import mail
    auth, social = sys.modules['app.routes.auth'], sys.modules['app.routes.social']
    monkeypatch.setattr(auth, 'send_email', fake)
    monkeypatch.setattr(social, 'send_email', fake)
    monkeypatch.setattr(mail, 'send_email', fake)
    return sent


def _user(make_user, lang):
    u = make_user()
    u.lang = lang
    u.first_name = 'Anna'
    return u


@pytest.mark.parametrize('lang, subject, line', [
    ('nl', 'Wachtwoord opnieuw instellen — Global Union Forum', 'Deze link verloopt na 1 uur.'),
    ('en', 'Reset your password — Global Union Forum', 'This link expires in 1 hour.'),
])
def test_reset_email(app, db, make_user, outbox, lang, subject, line):
    from app.routes.auth import _send_reset_email
    user = _user(make_user, lang)
    with app.test_request_context('/'):
        assert _send_reset_email(user, 'https://example.org/reset', lang)
    assert outbox[-1]['subject'] == subject and line in outbox[-1]['html']
    assert ('Hoi Anna,' if lang == 'nl' else 'Hi Anna,') in outbox[-1]['html']


@pytest.mark.parametrize('lang, subject, line', [
    ('nl', 'Bevestig je e-mailadres — Global Union Forum', 'Deze link verloopt na 24 uur.'),
    ('en', 'Confirm your email — Global Union Forum', 'This link expires in 24 hours.'),
])
def test_verify_email(app, db, make_user, outbox, lang, subject, line):
    from app.utils import _send_verify_email
    user = _user(make_user, lang)
    with app.test_request_context('/'):
        _send_verify_email(user, lang, 'secret')
    assert outbox[-1]['subject'] == subject and line in outbox[-1]['html']


@pytest.mark.parametrize('lang, subject, line', [
    ('nl', 'Uitnodiging: doe mee aan het Global Union Forum gesprek', 'Maak gratis een account aan:'),
    ('en', 'Invitation: join the Global Union Forum conversation', 'Create a free account here:'),
])
def test_invitation_in_the_inviters_language(app, db, make_user, outbox, lang, subject, line):
    inviter = make_user()
    resp = _req(_client(app, inviter, lang), 'POST', '/uitnodiging',
                data={'invite_email': 'vriend@example.com', 'invite_message': 'Kijk even.'})
    assert resp.status_code in (302, 303)
    mail = outbox[-1]
    assert mail['to'] == 'vriend@example.com' and mail['subject'] == subject and line in mail['html']
    assert ('Persoonlijk bericht van' if lang == 'nl' else 'Personal message from') in mail['html']


@pytest.mark.parametrize('lang, subject, line, button', [
    ('nl', 'Nieuws over je voorstel', 'Je krijgt deze e-mail omdat je hiervoor e-mail hebt aangezet.', 'Bekijken'),
    ('en', 'News about your proposal', 'You receive this email because you turned on email for this.', 'View'),
    (None, 'Nieuws over je voorstel', 'Je krijgt deze e-mail omdat je hiervoor e-mail hebt aangezet.', 'Bekijken'),
])
def test_notification_email_in_the_recipients_language(app, db, make_user, lang, subject, line, button):
    """user.lang decides; empty means Dutch. The request itself is in the other language."""
    user = _user(make_user, lang)
    n = Notification(user_id=user.id, category='voorstel', event_key='issueproposal:0')
    with app.test_request_context('/'), force_locale('en' if lang != 'en' else 'nl'):
        got_subject, html = notifications.email_for(user, n)
    assert got_subject == subject and line in html and f'>{button}</a>' in html
    expected_line = ('Je voorstel bij een voorstel heeft genoeg steun en staat op de stemlijst.' if lang != 'en'
                     else 'Your proposal on a proposal has enough support and is on the voting list.')
    assert expected_line in html


@pytest.mark.parametrize('lang, subject', [('nl', 'Je dagelijkse samenvatting'), ('en', 'Your daily summary')])
def test_digest_email_in_the_recipients_language(app, db, make_user, lang, subject):
    user = _user(make_user, lang)
    with app.test_request_context('/'), force_locale('en' if lang == 'nl' else 'nl'):
        got_subject, _html = digest._email([], digest.lang_for(user))
    assert got_subject == subject


def test_digest_language_falls_back_to_dutch(make_user):
    user = make_user()
    user.lang = None
    assert digest.lang_for(user) == 'nl'
