"""Notifications step 1: the settings page (docs/design/notificaties.html)
and the preferences model (events: tests/test_notifications_events.py)."""
import json

from app.models import User
from app.notifications import effective_prefs, summary_frequency
from tests.test_moderator import _client, _req, _user


def test_defaults_and_effective_prefs(app, db):
    user = _user(db)
    prefs = effective_prefs(user)
    assert prefs['reactie'] == {'app': True, 'email': False}
    assert prefs['voorstel'] == {'app': True, 'email': True}
    assert prefs['uitslag'] == {'app': True, 'email': True}
    assert summary_frequency(user) == 'weekly'
    user.notif_prefs = json.dumps({'reactie': {'email': True}})
    assert effective_prefs(user)['reactie'] == {'app': True, 'email': True}


def test_page_shows_moderation_section_by_role(app, db):
    plain, mod, senior = _user(db), _user(db, 'moderator'), _user(db, 'senior_moderator')
    page = _req(_client(app, plain), 'GET', '/profiel/meldingen').get_data(as_text=True)
    assert 'Jouw deelname' in page and 'Reactie op je bijdrage' in page
    assert 'Moderatie' not in page and 'name="melding_app"' not in page
    assert 'Samenvatting per e-mail' in page and 'value="weekly" checked' in page

    page = _req(_client(app, mod), 'GET', '/profiel/meldingen').get_data(as_text=True)
    assert 'voor moderators' in page and 'name="melding_app"' in page
    assert 'name="bezwaar_app"' not in page and 'name="betwist_app"' not in page

    page = _req(_client(app, senior), 'GET', '/profiel/meldingen').get_data(as_text=True)
    assert 'name="bezwaar_app"' in page and 'name="betwist_email"' in page and 'Senior moderators' in page

    profile = _req(_client(app, plain), 'GET', '/profile').get_data(as_text=True)
    assert 'href="/profiel/meldingen"' in profile


def test_saving_persists_prefs_and_ignores_unentitled_categories(app, db):
    plain = _user(db)
    c = _client(app, plain)
    r = _req(c, 'POST', '/profiel/meldingen', data={
        'reactie_app': '1', 'reactie_email': '1', 'voorstel_app': '1',   # voorstel email off
        'melding_app': '1', 'melding_email': '1',                        # not a moderator: ignored
        'summary_frequency': 'daily'})
    assert r.status_code == 302 and r.headers['Location'].endswith('/profiel/meldingen')
    db.session.expire_all()
    user = db.session.get(User, plain.id)
    stored = json.loads(user.notif_prefs)
    assert stored['reactie'] == {'app': True, 'email': True}
    assert stored['voorstel'] == {'app': True, 'email': False}
    assert stored['gevolgd'] == {'app': False, 'email': False}
    assert 'melding' not in stored and user.summary_frequency == 'daily'
    page = _req(c, 'GET', '/profiel/meldingen').get_data(as_text=True)
    assert 'Bewaard' in page and 'value="daily" checked' in page

    # An unknown frequency is ignored.
    _req(c, 'POST', '/profiel/meldingen', data={'summary_frequency': 'hourly'})
    db.session.expire_all()
    assert db.session.get(User, plain.id).summary_frequency == 'daily'

    mod = _user(db, 'moderator')
    _req(_client(app, mod), 'POST', '/profiel/meldingen', data={'melding_email': '1', 'bezwaar_email': '1'})
    db.session.expire_all()
    stored = json.loads(db.session.get(User, mod.id).notif_prefs)
    assert stored['melding'] == {'app': False, 'email': True} and 'bezwaar' not in stored


# ── Step 2: the summary frequency drives the digest ─────────────────────

def test_null_frequency_falls_back_to_old_toggle(app, db):
    user = _user(db)
    user.summary_frequency = None
    user.digest_email = False
    assert summary_frequency(user) == 'off'
    user.digest_email = True
    assert summary_frequency(user) == 'weekly'
    user.digest_email = None
    assert summary_frequency(user) == 'weekly'
    user.summary_frequency = 'daily'
    user.digest_email = False
    assert summary_frequency(user) == 'daily'      # an explicit choice wins
    db.session.rollback()


def _digest_user(db, freq, monkeypatch, mails):
    """A user for whom build() always has something, with email captured."""
    from app import digest
    import app.mail
    user = _user(db)
    user.summary_frequency = freq
    user.last_digest_at = None
    db.session.commit()
    monkeypatch.setattr(digest, 'build', lambda u, now, participants=None: [{'thread_id': 0, 'changes': []}])
    monkeypatch.setattr(app.mail, 'send_email', lambda to, subject, html: mails.append((to, subject)) or True)
    return user


def test_digest_off_daily_weekly(app, db, monkeypatch):
    from datetime import datetime, timedelta
    from app import digest
    from app.models import Digest
    mails = []
    base = datetime(2026, 11, 10, 9, 0)
    with app.test_request_context():
        off = _digest_user(db, 'off', monkeypatch, mails)
        assert digest.send_for(off, base) is None
        assert Digest.query.filter_by(user_id=off.id).count() == 0 and not mails

        daily = _digest_user(db, 'daily', monkeypatch, mails)
        assert digest.send_for(daily, base) is not None
        assert digest.send_for(daily, base + timedelta(hours=3)) is None        # same local day
        assert digest.send_for(daily, base + timedelta(days=1)) is not None     # next day
        assert Digest.query.filter_by(user_id=daily.id).count() == 2
        assert [s for to, s in mails if to == daily.email] == ['Je dagelijkse samenvatting'] * 2

        weekly = _digest_user(db, 'weekly', monkeypatch, mails)
        assert digest.send_for(weekly, base) is not None
        assert digest.send_for(weekly, base + timedelta(days=1)) is None
        assert digest.send_for(weekly, base + timedelta(days=6, hours=23)) is None
        assert digest.send_for(weekly, base + timedelta(days=7, minutes=1)) is not None
        assert Digest.query.filter_by(user_id=weekly.id).count() == 2
        assert [s for to, s in mails if to == weekly.email] == ['Je wekelijkse samenvatting'] * 2
    for u in (off, daily, weekly):
        Digest.query.filter_by(user_id=u.id).delete()
    db.session.commit()
