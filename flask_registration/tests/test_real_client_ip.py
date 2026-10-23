"""
Echt client-IP achter Cloudflare (real_client_ip in app/utils.py).

De oude tests stuurden geen X-Forwarded-For mee en zagen daardoor niet dat
de per-IP limiet op het Cloudflare-edge-IP telde. Hier sturen we een
realistische proxyketen mee: bezoeker -> Cloudflare -> Render -> app.
"""
import os
import sys

import pytest

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import config as config_module
from app import create_app, db, limiter, oauth
from app.utils import real_client_ip

SECRET = 'test-origin-secret'
VISITOR = '203.0.113.7'
OTHER_VISITOR = '198.51.100.23'
CF_EDGE = '172.70.1.1'
RENDER_PROXY = '10.0.0.5'


@pytest.fixture
def app():
    orig_db_uri = config_module.Config.SQLALCHEMY_DATABASE_URI
    orig_ratelimit_enabled = getattr(config_module.Config, 'RATELIMIT_ENABLED', True)
    config_module.Config.SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    config_module.Config.TESTING = True
    config_module.Config.WTF_CSRF_ENABLED = False
    config_module.Config.RATELIMIT_ENABLED = True
    try:
        flask_app = create_app()
        with flask_app.app_context():
            limiter.reset()
            yield flask_app
            db.session.remove()
            db.drop_all()
    finally:
        config_module.Config.SQLALCHEMY_DATABASE_URI = orig_db_uri
        config_module.Config.RATELIMIT_ENABLED = orig_ratelimit_enabled


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def with_secret(monkeypatch):
    monkeypatch.setenv('ORIGIN_VERIFY_SECRET', SECRET)


@pytest.fixture
def without_secret(monkeypatch):
    monkeypatch.delenv('ORIGIN_VERIFY_SECRET', raising=False)


def _key(app, headers):
    """De sleutel die de helper geeft voor een verzoek dat vanaf de Render-
    proxy binnenkomt, na ProxyFix (zoals in productie)."""
    seen = {}

    def probe(environ, start_response):
        from flask import request
        with app.request_context(environ):
            seen['ip'] = real_client_ip()
            seen['remote_addr'] = request.remote_addr
        start_response('204 No Content', [])
        return [b'']

    from werkzeug.test import EnvironBuilder
    from werkzeug.middleware.proxy_fix import ProxyFix
    environ = EnvironBuilder(path='/', headers=headers,
                             environ_base={'REMOTE_ADDR': RENDER_PROXY}).get_environ()
    ProxyFix(probe, x_for=0, x_proto=1, x_host=1)(environ, lambda *a: None)
    return seen


def _chain(visitor, verify=None, cf_ip=None, forged_xff=None):
    xff = ', '.join(x for x in (forged_xff, visitor, CF_EDGE) if x)
    headers = {'X-Forwarded-For': xff, 'X-Forwarded-Proto': 'https'}
    if cf_ip:
        headers['CF-Connecting-IP'] = cf_ip
    if verify is not None:
        headers['X-GU-Origin-Verify'] = verify
    return headers


class TestHelperWithSecret:
    def test_verified_cloudflare_request_uses_cf_ip(self, app, with_secret):
        seen = _key(app, _chain(VISITOR, verify=SECRET, cf_ip=VISITOR, forged_xff='6.6.6.6'))
        assert seen['ip'] == VISITOR

    def test_missing_verify_header_ignores_forged_cf_ip(self, app, with_secret):
        seen = _key(app, {'CF-Connecting-IP': '6.6.6.6', 'X-Forwarded-For': '6.6.6.6'})
        assert seen['ip'] == RENDER_PROXY

    def test_wrong_verify_header_ignores_forged_cf_ip(self, app, with_secret):
        seen = _key(app, _chain('6.6.6.6', verify='raden', cf_ip='6.6.6.6'))
        assert seen['ip'] == RENDER_PROXY

    def test_verified_but_no_cf_ip_uses_socket(self, app, with_secret):
        seen = _key(app, _chain(VISITOR, verify=SECRET))
        assert seen['ip'] == RENDER_PROXY


class TestHelperWithoutSecret:
    def test_cf_ip_used_when_present(self, app, without_secret):
        seen = _key(app, _chain(VISITOR, cf_ip=VISITOR, forged_xff='6.6.6.6'))
        assert seen['ip'] == VISITOR

    def test_socket_address_without_cf_ip(self, app, without_secret):
        seen = _key(app, _chain(VISITOR, forged_xff='6.6.6.6'))
        assert seen['ip'] == RENDER_PROXY

    def test_invalid_cf_ip_falls_back_to_socket(self, app, without_secret):
        seen = _key(app, {'CF-Connecting-IP': 'geen-ip'})
        assert seen['ip'] == RENDER_PROXY


def test_proxyfix_no_longer_rewrites_remote_addr_from_xff(app, without_secret):
    seen = _key(app, _chain(VISITOR, forged_xff='6.6.6.6'))
    assert seen['remote_addr'] == RENDER_PROXY


def test_app_proxyfix_keeps_proto_but_not_for(app):
    pf = app.wsgi_app
    assert (pf.x_for, pf.x_proto, pf.x_host) == (0, 1, 1)


# ── Via de echte limiter: aparte tellers per bezoeker ──────────────────────

# De 429-handler stuurt browsers door met een melding; met XHR krijgen we de
# echte statuscode terug.
XHR = {'X-Requested-With': 'XMLHttpRequest'}


def _login(client, headers):
    return client.post('/login', data={'email': 'niemand@example.com', 'password': 'fout'},
                       headers={**headers, **XHR}, environ_overrides={'REMOTE_ADDR': RENDER_PROXY})


class TestSeparateCounters:
    def test_two_visitors_do_not_block_each_other(self, client, with_secret):
        a = _chain(VISITOR, verify=SECRET, cf_ip=VISITOR)
        b = _chain(OTHER_VISITOR, verify=SECRET, cf_ip=OTHER_VISITOR)
        for _ in range(5):
            assert _login(client, a).status_code != 429
        assert _login(client, a).status_code == 429
        assert _login(client, b).status_code != 429

    def test_two_visitors_separate_without_secret(self, client, without_secret):
        for _ in range(5):
            assert _login(client, _chain(VISITOR, cf_ip=VISITOR)).status_code != 429
        assert _login(client, _chain(VISITOR, cf_ip=VISITOR)).status_code == 429
        assert _login(client, _chain(OTHER_VISITOR, cf_ip=OTHER_VISITOR)).status_code != 429

    def test_forged_cf_ip_does_not_bypass_limit(self, client, with_secret):
        """Direct op de origin: elke keer een ander CF-Connecting-IP helpt niet,
        alles telt op het socket-adres."""
        for i in range(5):
            assert _login(client, {'CF-Connecting-IP': f'6.6.6.{i}'}).status_code != 429
        assert _login(client, {'CF-Connecting-IP': '6.6.6.99'}).status_code == 429


class TestOAuthRoutes:
    @pytest.fixture(autouse=True)
    def _no_google(self, monkeypatch):
        from flask import redirect
        monkeypatch.setattr(oauth.google, 'authorize_redirect',
                            lambda *a, **k: redirect('https://accounts.example/'))

    def _hit(self, client, headers):
        return client.get('/auth/google', headers={**headers, **XHR},
                          environ_overrides={'REMOTE_ADDR': RENDER_PROXY})

    def test_forged_left_xff_no_longer_bypasses(self, client, with_secret):
        for i in range(20):
            assert self._hit(client, {'X-Forwarded-For': f'6.6.6.{i}, {CF_EDGE}'}).status_code != 429
        assert self._hit(client, {'X-Forwarded-For': f'6.6.6.99, {CF_EDGE}'}).status_code == 429

    def test_oauth_counts_per_visitor(self, client, with_secret):
        a = _chain(VISITOR, verify=SECRET, cf_ip=VISITOR)
        for _ in range(20):
            assert self._hit(client, a).status_code != 429
        assert self._hit(client, a).status_code == 429
        assert self._hit(client, _chain(OTHER_VISITOR, verify=SECRET,
                                        cf_ip=OTHER_VISITOR)).status_code != 429
