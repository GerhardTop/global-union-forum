"""Notifications step 1: the settings page (docs/design/notificaties.html)
and the preferences model. No notifications are produced yet."""
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
