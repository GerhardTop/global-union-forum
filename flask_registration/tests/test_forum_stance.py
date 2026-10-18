"""
Forum fase B: eens/oneens per bijdrage (POST /forum/stance/<id>, vervangt de
like). Dekt: eens, wisselen, intrekken met één rij per gebruiker per
bijdrage; niet-ingelogd ziet alleen de tellingen; niet op je eigen bijdrage;
alleen bijdragers; CSRF; de rate limit; de weergave in NL en EN.

Hergebruikt de app-fixture met CSRF en rate limiting aan uit
test_forum_reply_security.py.
"""
import re

import pytest
from flask import g

from app import db
from app.models import Post, PostLike, Thread, User
from tests.test_forum_reply_security import _client_for, _make_user, app  # noqa: F401

XHR = {'X-Requested-With': 'XMLHttpRequest'}


@pytest.fixture
def setup(app):  # noqa: F811
    anna, bram, carla = _make_user('anna'), _make_user('bram'), _make_user('carla')
    t = Thread(eyebrow_nl='T', eyebrow_en='T', title_prefix_nl='Signaal', title_prefix_en='Signal')
    db.session.add(t)
    db.session.flush()
    post = Post(thread_id=t.id, user_id=bram.id, author_name='', author_username='bram',
                body='Een stelling van Bram', source_lang='nl')
    db.session.add(post)
    db.session.commit()
    return {'thread': t, 'post': post, 'anna': anna, 'bram': bram, 'carla': carla}


def _req(client, method, path, **kw):
    g.pop('_login_user', None)
    g.pop('csrf_token', None)
    return client.open(path, method=method, **kw)


def _token(client, thread):
    html = _req(client, 'GET', f'/forum/{thread.id}').get_data(as_text=True)
    return re.search(r'var csrfToken = "([^"]+)";', html).group(1)


def _vote(client, post, stance, token):
    data = {'stance': stance}
    if token is not None:
        data['csrf_token'] = token
    return _req(client, 'POST', f'/forum/stance/{post.id}', data=data, headers=XHR)


def _rows(post):
    return PostLike.query.filter_by(post_id=post.id).all()


class TestStanceFlow:
    def test_agree_switch_withdraw_keeps_one_row(self, app, setup):  # noqa: F811
        post = setup['post']
        anna = _client_for(app, setup['anna'])
        token = _token(anna, setup['thread'])

        assert _vote(anna, post, 'eens', token).get_json() == {'eens': 1, 'oneens': 0, 'mine': 'eens'}
        assert [(r.user_id, r.stance) for r in _rows(post)] == [(setup['anna'].id, 'eens')]

        assert _vote(anna, post, 'oneens', token).get_json() == {'eens': 0, 'oneens': 1, 'mine': 'oneens'}
        db.session.expire_all()
        assert [(r.user_id, r.stance) for r in _rows(post)] == [(setup['anna'].id, 'oneens')]

        assert _vote(anna, post, 'oneens', token).get_json() == {'eens': 0, 'oneens': 0, 'mine': None}
        assert _rows(post) == []

    def test_counts_combine_several_users(self, app, setup):  # noqa: F811
        post = setup['post']
        for name, stance in (('anna', 'eens'), ('carla', 'oneens')):
            client = _client_for(app, setup[name])
            _vote(client, post, stance, _token(client, setup['thread']))
        anna = _client_for(app, setup['anna'])
        assert _vote(anna, post, 'oneens', _token(anna, setup['thread'])).get_json() == \
            {'eens': 0, 'oneens': 2, 'mine': 'oneens'}
        assert len(_rows(post)) == 2

    def test_invalid_stance_is_rejected(self, app, setup):  # noqa: F811
        anna = _client_for(app, setup['anna'])
        assert _vote(anna, setup['post'], 'misschien', _token(anna, setup['thread'])).status_code == 400
        assert _rows(setup['post']) == []


class TestWhoMayVote:
    def test_anonymous_cannot_vote_but_sees_counts(self, app, setup):  # noqa: F811
        post = setup['post']
        db.session.add(PostLike(post_id=post.id, user_id=setup['anna'].id, stance='eens'))
        db.session.commit()
        client = app.test_client()
        resp = _req(client, 'POST', f'/forum/stance/{post.id}', data={'stance': 'oneens'})
        assert resp.status_code == 302 and '/login' in resp.headers['Location']
        html = _req(client, 'GET', f'/forum/{setup["thread"].id}').get_data(as_text=True)
        group = re.search(r'<div class="gu-stance" data-post-id="%d".*?</div>' % post.id, html, re.S).group(0)
        assert re.search(r'Eens</span>\s*<span class="gu-stance__count">1<', group)
        assert re.search(r'Oneens</span>\s*<span class="gu-stance__count">0<', group)
        assert group.count('disabled') == 2

    def test_not_on_own_post(self, app, setup):  # noqa: F811
        bram = _client_for(app, setup['bram'])
        resp = _vote(bram, setup['post'], 'eens', _token(bram, setup['thread']))
        assert resp.status_code == 400 and _rows(setup['post']) == []
        html = _req(bram, 'GET', f'/forum/{setup["thread"].id}').get_data(as_text=True)
        group = re.search(r'<div class="gu-stance" data-post-id="%d".*?</div>' % setup['post'].id, html, re.S).group(0)
        assert group.count('disabled') == 2

    def test_user_without_rules_acceptance_cannot_vote(self, app, setup):  # noqa: F811
        new = User(username='nieuw', email='nieuw@example.com', password_hash='x', verified=True)
        db.session.add(new)
        db.session.commit()
        client = _client_for(app, new)
        assert _vote(client, setup['post'], 'eens', _token(client, setup['thread'])).status_code == 403
        assert _rows(setup['post']) == []


class TestCsrfAndRateLimit:
    def test_missing_token_is_rejected(self, app, setup):  # noqa: F811
        anna = _client_for(app, setup['anna'])
        _token(anna, setup['thread'])
        assert _vote(anna, setup['post'], 'eens', None).status_code == 400
        assert _rows(setup['post']) == []

    def test_invalid_token_is_rejected(self, app, setup):  # noqa: F811
        anna = _client_for(app, setup['anna'])
        _token(anna, setup['thread'])
        assert _vote(anna, setup['post'], 'eens', 'niet-het-echte-token').status_code == 400
        assert _rows(setup['post']) == []

    def test_rate_limit_per_user(self, app, setup):  # noqa: F811
        anna = _client_for(app, setup['anna'])
        token = _token(anna, setup['thread'])
        for i in range(30):
            assert _vote(anna, setup['post'], 'eens' if i % 2 == 0 else 'oneens', token).status_code == 200
        resp = _vote(anna, setup['post'], 'eens', token)
        assert resp.status_code == 429 and resp.get_json()['error'] == 'rate_limited'
        # Een andere gebruiker heeft zijn eigen budget.
        carla = _client_for(app, setup['carla'])
        assert _vote(carla, setup['post'], 'eens', _token(carla, setup['thread'])).status_code == 200


class TestRendering:
    @pytest.mark.parametrize('lang,agree,disagree', [('nl', 'Eens', 'Oneens'), ('en', 'Agree', 'Disagree')])
    def test_own_choice_marked_and_labels(self, app, setup, lang, agree, disagree):  # noqa: F811
        db.session.add(PostLike(post_id=setup['post'].id, user_id=setup['anna'].id, stance='oneens'))
        db.session.commit()
        anna = _client_for(app, setup['anna'])
        with anna.session_transaction() as sess:
            sess['lang'] = lang
        html = _req(anna, 'GET', f'/forum/{setup["thread"].id}').get_data(as_text=True)
        group = re.search(r'<div class="gu-stance" data-post-id="%d".*?</div>' % setup['post'].id, html, re.S).group(0)
        assert f'>{agree}</span>' in group and f'>{disagree}</span>' in group
        assert re.search(r'gu-stance__btn--oneens is-mine"\s*data-stance="oneens" aria-pressed="true"', group)
        assert re.search(r'gu-stance__btn--eens"\s*data-stance="eens" aria-pressed="false"', group)
        assert 'disabled' not in group
        assert 'gu-post__like' not in html
