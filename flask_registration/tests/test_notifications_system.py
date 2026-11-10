"""Notifications step 4: a warning or a suspension always reaches the user,
in the app and by email (verified address), regardless of the settings
matrix. The email goes out after the warning or suspension is committed."""
import json
from datetime import timedelta

import pytest

from app import notifications
from app.issues import to_local
from app.models import Notification, Suspension, User, Warning
from tests.test_moderator import _client, _req, _user
from tests.test_user_moderation import _drop_moderation_rows


@pytest.fixture(autouse=True)
def _clean_session(db):
    db.session.expunge_all()
    yield


@pytest.fixture
def mails(monkeypatch):
    sent = []
    import app.mail as mail
    monkeypatch.setattr(mail, 'send_email', lambda to, subject, html: sent.append((to, subject, html)) or True)
    return sent


def _rows(user, category):
    from app import db
    db.session.expire_all()
    return Notification.query.filter_by(user_id=user.id, category=category).all()


def _everything_off(db, user):
    user.notif_prefs = json.dumps({k: {'app': False, 'email': False} for k in notifications.CATEGORY_KEYS})
    user.summary_frequency = 'off'
    db.session.commit()


def test_warning_always_reaches_the_user(app, db, mails):
    target, mod = _user(db), _user(db, 'moderator')
    _everything_off(db, target)              # the matrix does not apply
    try:
        r = _req(_client(app, mod), 'POST', f'/moderator/gebruiker/{target.id}/waarschuwing',
                 data={'message': 'Blijf op de inhoud, niet op de persoon.'})
        assert r.status_code == 302
        w = Warning.query.filter_by(user_id=target.id).one()
        rows = _rows(target, 'waarschuwing')
        assert len(rows) == 1 and rows[0].in_app is True and rows[0].warning_id == w.id
        assert rows[0].emailed_at is not None
        assert [(to, subject) for to, subject, _ in mails] == [(target.email, 'Je hebt een waarschuwing gekregen')]
        html = mails[0][2]
        assert 'Blijf op de inhoud, niet op de persoon.' in html
        assert 'spelregels van het forum' in html and 'http://localhost/spelregels' in html
        assert 'altijd verstuurd' in html

        # In the app: on /meldingen, and counted in the header.
        assert notifications.unread_count(db.session.get(User, target.id)) == 1
        page = _req(_client(app, target), 'GET', '/meldingen').get_data(as_text=True)
        assert 'Een moderator heeft je een waarschuwing gestuurd: “Blijf op de inhoud, niet op de persoon.”' in page
        assert 'href="/spelregels"' in page
    finally:
        _drop_moderation_rows(target, mod)


def test_suspension_always_reaches_the_user_in_their_language(app, db, mails):
    target, senior = _user(db), _user(db, 'senior_moderator')
    _everything_off(db, target)
    target.lang = 'en'
    db.session.commit()
    try:
        r = _req(_client(app, senior), 'POST', f'/moderator/gebruiker/{target.id}/schorsen',
                 data={'duur': '7', 'reason': 'Herhaald persoonlijk aanvallen.'})
        assert r.status_code == 302
        s = Suspension.query.filter_by(user_id=target.id).one()
        until = to_local(s.until).strftime('%d-%m-%Y %H:%M')
        rows = _rows(target, 'schorsing')
        assert len(rows) == 1 and rows[0].in_app is True and rows[0].suspension_id == s.id
        assert [(to, subject) for to, subject, _ in mails] == [(target.email, 'You have been suspended')]
        html = mails[0][2]
        assert f'You are suspended until {until}. Reason: Herhaald persoonlijk aanvallen.' in html
        assert 'you can still read, but not contribute or vote' in html
        assert notifications.notification_line(rows[0], 'nl')[0] == \
            f'Je bent geschorst tot {until}. Reden: Herhaald persoonlijk aanvallen.'

        # The suspended banner still shows, next to the unread count; /meldingen lists it.
        forum = _req(_client(app, target), 'GET', '/forum').get_data(as_text=True)
        assert 'gu-suspended-banner' in forum and 'Je hebt 1 nieuwe melding.' in forum
        page = _req(_client(app, target), 'GET', '/meldingen').get_data(as_text=True)
        assert f'Je bent geschorst tot {until}.' in page
    finally:
        db.session.get(User, target.id).suspended_until = None
        db.session.commit()
        _drop_moderation_rows(target, senior)


def test_unverified_user_gets_it_in_the_app_only(app, db, mails):
    target, mod = _user(db), _user(db, 'moderator')
    target.verified = False
    db.session.commit()
    try:
        _req(_client(app, mod), 'POST', f'/moderator/gebruiker/{target.id}/waarschuwing',
             data={'message': 'Let op je toon.'})
        rows = _rows(target, 'waarschuwing')
        assert len(rows) == 1 and rows[0].in_app is True and rows[0].emailed_at is None
        assert mails == []
    finally:
        _drop_moderation_rows(target, mod)


def test_system_categories_are_not_in_the_matrix(app, db):
    assert not set(notifications.SYSTEM_CATEGORIES) & set(notifications.CATEGORY_KEYS)
    page = _req(_client(app, _user(db)), 'GET', '/profiel/meldingen').get_data(as_text=True)
    assert 'name="waarschuwing_app"' not in page and 'name="schorsing_email"' not in page
    with pytest.raises(ValueError):
        notifications.notify_system(_user(db), 'reactie')


def test_mail_goes_out_after_the_commit(app, db, monkeypatch):
    """When the mail is sent, the warning and the notification are already committed."""
    target, mod = _user(db), _user(db, 'moderator')
    seen = []

    def _send(to, subject, html):
        from app import db as _db
        # A separate connection sees only committed rows.
        with _db.engine.connect() as conn:
            from sqlalchemy import text
            seen.append((conn.execute(text('SELECT COUNT(*) FROM user_warnings WHERE user_id = :u'),
                                      {'u': target.id}).scalar(),
                         conn.execute(text("SELECT COUNT(*) FROM notifications WHERE user_id = :u "
                                           "AND category = 'waarschuwing'"), {'u': target.id}).scalar()))
        return True
    import app.mail as mail
    monkeypatch.setattr(mail, 'send_email', _send)
    try:
        _req(_client(app, mod), 'POST', f'/moderator/gebruiker/{target.id}/waarschuwing', data={'message': 'Na de commit.'})
        assert seen == [(1, 1)]
    finally:
        _drop_moderation_rows(target, mod)
