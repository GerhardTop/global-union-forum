"""
Tests voor safe_next() (app/utils.py) en de twee routes die een next-waarde
volgen: de vertaalvoorkeur (POST-veld next) en login (?next=). Externe of
protocol-relatieve bestemmingen moeten op een veilige interne plek uitkomen,
interne paden werken gewoon.
"""
import pytest
from flask import g

from app.utils import safe_next

EXTERNAL = [
    'https://evil.example',
    'http://evil.example/forum',
    '//evil.example',
    '///evil.example',
    '/\\evil.example',
    '\\\\evil.example',
    'javascript:alert(1)',
    'evil.example',
    '/forum\n//evil.example',
    ' /forum',
    '',
    None,
]
INTERNAL = ['/forum/3', '/forum/3#post-12', '/profile?tab=taal', '/']


class TestSafeNext:
    @pytest.mark.parametrize('target', EXTERNAL)
    def test_rejects_external_and_tricky_targets(self, target):
        assert safe_next(target, '/veilig') == '/veilig'

    @pytest.mark.parametrize('target', INTERNAL)
    def test_keeps_internal_paths(self, target):
        assert safe_next(target, '/veilig') == target


def _logged_in(app, user):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user.id)
        sess['_fresh'] = True
    return client


def _post(client, path, **kw):
    # Gedeelde app_context in conftest: current_user zit anders gecached in g.
    g.pop('_login_user', None)
    return client.post(path, **kw)


class TestVertaalvoorkeurRedirect:
    @pytest.mark.parametrize('target', ['https://evil.example/x', '//evil.example', '/\\evil.example'])
    def test_external_next_falls_back_to_profile(self, app, verified_user, target):
        resp = _post(_logged_in(app, verified_user), '/profiel/vertaalvoorkeur',
                     data={'auto_translate': '1', 'next': target})
        assert resp.status_code == 302
        assert resp.headers['Location'] == '/profile'

    def test_internal_next_is_followed(self, app, verified_user):
        resp = _post(_logged_in(app, verified_user), '/profiel/vertaalvoorkeur',
                     data={'auto_translate': '1', 'next': '/forum/3'})
        assert resp.status_code == 302
        assert resp.headers['Location'] == '/forum/3'

    def test_missing_next_goes_to_profile_as_before(self, app, verified_user):
        resp = _post(_logged_in(app, verified_user), '/profiel/vertaalvoorkeur',
                     data={'auto_translate': '1'})
        assert resp.headers['Location'] == '/profile'


class TestLoginRedirect:
    def _login(self, client, next_value):
        g.pop('_login_user', None)
        return client.post('/login', query_string={'next': next_value},
                           data={'email': 'vera@example.com', 'password': 'Sterk1!ww'})

    @pytest.mark.parametrize('target', ['https://evil.example', '///evil.example', '/\\evil.example'])
    def test_external_next_goes_home(self, client, verified_user, target):
        resp = self._login(client, target)
        assert resp.status_code == 302
        assert resp.headers['Location'] == '/'

    def test_internal_next_is_followed(self, client, verified_user):
        resp = self._login(client, '/forum/3')
        assert resp.status_code == 302
        assert resp.headers['Location'] == '/forum/3'
