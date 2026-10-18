"""
Tests voor de beveiliging van de forum-reactie-stroom (POST /forum/<id>,
app/routes/forum.py:forum_thread): CSRF-validatie via ForumReplyForm en de
twee gelaagde rate limits (5/minuut per gebruiker, 100/uur per IP).

Eigen app-fixture (zoals test_mail_global_ratelimit.py): CSRF en rate
limiting staan hier AAN, in tegenstelling tot de gedeelde conftest-app.
De app_context blijft de hele test open, dus _req() wist vóór elk request
de in `g` gecachte current_user en het CSRF-token (zie de Babel/g-test-gotcha).
"""
import os
import re
import sys

import pytest
from flask import g

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import config as config_module
from app import bcrypt, create_app, db
from app.models import Post, Thread, User


@pytest.fixture
def app():
    missing = object()
    orig = {k: config_module.Config.__dict__.get(k, missing)
            for k in ('SQLALCHEMY_DATABASE_URI', 'RATELIMIT_ENABLED', 'WTF_CSRF_ENABLED', 'TESTING')}
    config_module.Config.SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    config_module.Config.TESTING = True
    config_module.Config.WTF_CSRF_ENABLED = True
    config_module.Config.RATELIMIT_ENABLED = True
    try:
        flask_app = create_app()
        with flask_app.app_context():
            yield flask_app
            db.session.remove()
            db.drop_all()
    finally:
        for k, v in orig.items():
            if v is missing:
                if k in config_module.Config.__dict__:
                    delattr(config_module.Config, k)
            else:
                setattr(config_module.Config, k, v)


def _make_user(username):
    u = User(username=username, first_name=username.capitalize(), last_name='Test',
             email=f'{username}@example.com',
             password_hash=bcrypt.generate_password_hash('Sterk1!ww').decode('utf-8'),
             verified=True)
    u.accept_rules()  # bestaande bijdrager (gegrandfatherd), zie forum fase A
    db.session.add(u)
    db.session.commit()
    return u


@pytest.fixture
def thread(app):
    t = Thread(eyebrow_nl='Test', eyebrow_en='Test',
               title_prefix_nl='Beveiliging', title_prefix_en='Security')
    db.session.add(t)
    db.session.flush()
    op = Post(thread_id=t.id, author_name='Op', author_username='op_user',
              body='Openingsbericht', is_op=True)
    db.session.add(op)
    db.session.commit()
    return t


def _client_for(app, user):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user.id)
        sess['_fresh'] = True
    return client


def _req(client, method, path, ip='192.0.2.10', **kw):
    # Flask-Login en Flask-WTF cachen current_user en het CSRF-token in `g`.
    g.pop('_login_user', None)
    g.pop('csrf_token', None)
    return client.open(path, method=method, environ_base={'REMOTE_ADDR': ip}, **kw)


def _token(client, thread, ip='192.0.2.10'):
    html = _req(client, 'GET', f'/forum/{thread.id}', ip=ip).get_data(as_text=True)
    return re.search(r'name="csrf_token" type="hidden" value="([^"]+)"', html).group(1)


def _reply(client, thread, body, token, ip='192.0.2.10'):
    data = {'body': body}
    if token is not None:
        data['csrf_token'] = token
    return _req(client, 'POST', f'/forum/{thread.id}', ip=ip, data=data)


def _count(body):
    return Post.query.filter_by(body=body).count()


class TestCsrf:
    def test_every_reply_form_carries_a_token(self, app, thread):
        client = _client_for(app, _make_user('anna'))
        html = _req(client, 'GET', f'/forum/{thread.id}').get_data(as_text=True)
        forms = re.findall(r'<form[^>]*action="/forum/%d"[^>]*>(.*?)</form>' % thread.id, html, re.S)
        # Composer + inline reactieformulier onder het openingsbericht.
        assert len(forms) == 2
        assert all('name="csrf_token"' in f for f in forms)
        # Geen dubbele id="csrf_token" over de vele formulieren heen.
        assert 'id="csrf_token"' not in ''.join(forms)

    def test_reply_without_token_is_rejected(self, app, thread):
        client = _client_for(app, _make_user('anna'))
        resp = _reply(client, thread, 'zonder token', token=None)
        assert resp.status_code == 400
        assert _count('zonder token') == 0

    def test_reply_with_invalid_token_is_rejected(self, app, thread):
        client = _client_for(app, _make_user('anna'))
        _token(client, thread)  # sessie heeft nu een echt token
        resp = _reply(client, thread, 'vals token', token='niet-het-echte-token')
        assert resp.status_code == 400
        assert _count('vals token') == 0

    def test_nested_reply_without_token_is_rejected(self, app, thread):
        client = _client_for(app, _make_user('anna'))
        op = Post.query.filter_by(thread_id=thread.id, is_op=True).one()
        resp = _req(client, 'POST', f'/forum/{thread.id}',
                    data={'body': 'genest zonder token', 'parent_id': op.id})
        assert resp.status_code == 400
        assert _count('genest zonder token') == 0

    def test_reply_with_valid_token_still_works(self, app, thread):
        client = _client_for(app, _make_user('anna'))
        resp = _reply(client, thread, 'met token', token=_token(client, thread))
        assert resp.status_code == 302
        assert _count('met token') == 1


class TestRateLimit:
    def test_sixth_reply_within_a_minute_is_blocked(self, app, thread):
        client = _client_for(app, _make_user('anna'))
        token = _token(client, thread)
        for i in range(5):
            assert _reply(client, thread, f'snel {i}', token).status_code == 302
        resp = _reply(client, thread, 'snel 5', token)
        # De 429-handler stuurt terug naar de thread (composer) met een melding.
        assert resp.status_code == 302
        assert resp.headers['Location'].endswith(f'/forum/{thread.id}#composer')
        assert _count('snel 5') == 0
        assert Post.query.filter(Post.body.like('snel %')).count() == 5
        page = _req(client, 'GET', f'/forum/{thread.id}').get_data(as_text=True)
        assert 'Te veel pogingen' in page

    def test_limit_is_per_user(self, app, thread):
        anna = _client_for(app, _make_user('anna'))
        token = _token(anna, thread)
        for i in range(6):
            _reply(anna, thread, f'anna {i}', token)
        bram = _client_for(app, _make_user('bram'))
        resp = _reply(bram, thread, 'bram mag nog', _token(bram, thread))
        assert resp.status_code == 302 and _count('bram mag nog') == 1

    def test_blocked_requests_do_not_consume_per_ip_budget(self, app, thread):
        from app import limiter as _limiter
        ip = '198.51.100.7'
        client = _client_for(app, _make_user('anna'))
        token = _token(client, thread, ip=ip)
        for i in range(9):
            _reply(client, thread, f'bot {i}', token, ip=ip)
        assert Post.query.filter(Post.body.like('bot %')).count() == 5
        # Alleen de 5 toegestane verzoeken tellen mee in de per-IP-laag; de 4
        # die al op de per-gebruiker-limiet stukliepen niet.
        assert _limiter._storage.get(f'LIMITER/{ip}/forum.forum_thread/100/1/hour') == 5

    def test_anonymous_post_and_page_views_are_not_limited(self, app, thread):
        client = app.test_client()
        for _ in range(8):
            assert _req(client, 'GET', f'/forum/{thread.id}').status_code == 200
            # Anonieme POST doet niets (zoals voorheen) en raakt de limiter niet.
            assert _req(client, 'POST', f'/forum/{thread.id}', data={'body': 'anoniem'}).status_code == 200
        assert _count('anoniem') == 0
