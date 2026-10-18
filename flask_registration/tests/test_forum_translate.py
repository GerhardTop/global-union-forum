"""
Tests voor het vertalen van een los forumbericht (POST /forum/translate/<id>,
app/routes/forum.py:forum_translate): succes en cache, de CSRF-afhandeling,
en de rate limit (per gebruiker, alleen echte API-aanroepen tellen mee, een
429 komt bij fetch als JSON terug).

De Anthropic-client wordt vervangen door een nep-client: geen echte
API-aanroepen. Hergebruikt de app-fixture met CSRF en rate limiting aan uit
test_forum_reply_security.py.
"""
import re
from types import SimpleNamespace

import pytest
from flask import g

from app import db
from app.models import Post, Thread
from tests.test_forum_reply_security import _client_for, _make_user, app  # noqa: F401

XHR = {'X-Requested-With': 'XMLHttpRequest'}


@pytest.fixture
def fake_anthropic(monkeypatch):
    calls = []

    class FakeMessages:
        def create(self, **kw):
            calls.append(kw)
            text = kw['messages'][0]['content'].split('\n\n', 1)[1]
            return SimpleNamespace(content=[SimpleNamespace(text=f'[vertaald] {text}')])

    class FakeClient:
        def __init__(self, api_key=None):
            self.messages = FakeMessages()

    import anthropic
    monkeypatch.setattr(anthropic, 'Anthropic', FakeClient)
    return calls


@pytest.fixture
def setup(app):  # noqa: F811
    t = Thread(eyebrow_nl='T', eyebrow_en='T', title_prefix_nl='Vertalen', title_prefix_en='Translate')
    db.session.add(t)
    db.session.flush()
    posts = [Post(thread_id=t.id, author_name='Bram', author_username='bram',
                  body=f'English post number {i}', source_lang='en') for i in range(32)]
    db.session.add_all(posts)
    db.session.commit()
    return {'thread': t, 'posts': posts, 'anna': _make_user('anna'), 'bram': _make_user('bram')}


def _req(client, method, path, **kw):
    # Gedeelde app_context in deze fixture: g wissen zoals een echte server doet.
    for key in ('_login_user', 'csrf_token', 'translation_api_called'):
        g.pop(key, None)
    return client.open(path, method=method, **kw)


def _nl_client(app, user, thread):  # noqa: F811
    client = _client_for(app, user)
    with client.session_transaction() as sess:
        sess['lang'] = 'nl'
    html = _req(client, 'GET', f'/forum/{thread.id}').get_data(as_text=True)
    token = re.search(r'var csrfToken = "([^"]+)";', html).group(1)
    return client, token, html


def _translate(client, post, token, headers=XHR):
    data = {} if token is None else {'csrf_token': token}
    return _req(client, 'POST', f'/forum/translate/{post.id}', data=data, headers=headers)


class TestTranslateSuccess:
    def test_translates_and_caches(self, app, setup, fake_anthropic):  # noqa: F811
        post = setup['posts'][0]
        client, token, _ = _nl_client(app, setup['anna'], setup['thread'])
        resp = _translate(client, post, token)
        assert resp.status_code == 200
        assert resp.get_json() == {'translation': '[vertaald] English post number 0'}
        assert len(fake_anthropic) == 1 and 'Dutch' in fake_anthropic[0]['messages'][0]['content']
        db.session.expire_all()
        assert db.session.get(Post, post.id).body_nl == '[vertaald] English post number 0'
        # Tweede keer uit de cache: geen nieuwe API-aanroep.
        assert _translate(client, post, token).get_json()['translation'] == '[vertaald] English post number 0'
        assert len(fake_anthropic) == 1

    def test_page_renders_button_and_fetch_sends_token_and_xhr_header(self, app, setup):  # noqa: F811
        _, token, html = _nl_client(app, setup['anna'], setup['thread'])
        assert 'class="gu-post__action gu-post__action--ghost gu-post__translate-btn"' in html
        assert "trFd.append('csrf_token', csrfToken);" in html
        assert "headers: { 'X-Requested-With': 'XMLHttpRequest' }" in html
        # De vertaling wordt als platte tekst ingevoegd (geen ruwe HTML).
        assert 'el.textContent = p;' in html


class TestTranslateCsrf:
    def test_missing_token_is_rejected(self, app, setup, fake_anthropic):  # noqa: F811
        client, _, _ = _nl_client(app, setup['anna'], setup['thread'])
        assert _translate(client, setup['posts'][0], None).status_code == 400
        assert fake_anthropic == []

    def test_invalid_token_is_rejected(self, app, setup, fake_anthropic):  # noqa: F811
        client, _, _ = _nl_client(app, setup['anna'], setup['thread'])
        assert _translate(client, setup['posts'][0], 'niet-het-echte-token').status_code == 400
        assert fake_anthropic == []

    def test_anonymous_is_sent_to_login(self, app, setup, fake_anthropic):  # noqa: F811
        resp = _req(app.test_client(), 'POST', f'/forum/translate/{setup["posts"][0].id}')
        assert resp.status_code == 302 and '/login' in resp.headers['Location']
        assert fake_anthropic == []


class TestTranslateRateLimit:
    def test_cached_translations_do_not_use_up_the_limit(self, app, setup, fake_anthropic):  # noqa: F811
        post = setup['posts'][0]
        client, token, _ = _nl_client(app, setup['anna'], setup['thread'])
        for _ in range(40):
            assert _translate(client, post, token).status_code == 200
        assert len(fake_anthropic) == 1

    def test_limit_counts_api_calls_per_user_and_answers_json(self, app, setup, fake_anthropic):  # noqa: F811
        posts = setup['posts']
        anna, token, _ = _nl_client(app, setup['anna'], setup['thread'])
        for p in posts[:30]:
            assert _translate(anna, p, token).status_code == 200
        resp = _translate(anna, posts[30], token)
        assert resp.status_code == 429
        assert resp.get_json()['error'] == 'rate_limited'
        assert len(fake_anthropic) == 30
        # Een andere gebruiker (zelfde IP, zoals achter de proxy van Render)
        # kan gewoon verder vertalen.
        bram, bram_token, _ = _nl_client(app, setup['bram'], setup['thread'])
        assert _translate(bram, posts[31], bram_token).status_code == 200
