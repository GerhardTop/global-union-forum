"""
CSRF op de overige forum-acties (app/routes/forum.py, _require_csrf) en de
vertaalvoorkeur (app/routes/profile.py): per actie een 400 zonder of met
een ongeldig token, en met een geldig token werkt de actie zoals voorheen.
Plus: elk formulier dat zo'n actie aanroept, rendert het token.

Hergebruikt de app-fixture met CSRF aan en de helpers uit
test_forum_reply_security.py.
"""
import re

import pytest

from app import db
from app.models import Post, PostLike, Thread, User
from tests.test_forum_reply_security import _client_for, _make_user, _req, _token, app  # noqa: F401


@pytest.fixture
def setup(app):  # noqa: F811
    anna = _make_user('anna')
    bram = _make_user('bram')
    mod = _make_user('moddy')
    mod.is_moderator = True
    t = Thread(eyebrow_nl='Test', eyebrow_en='Test', title_prefix_nl='Acties', title_prefix_en='Actions')
    db.session.add(t)
    db.session.flush()
    op = Post(thread_id=t.id, user_id=anna.id, author_name='Anna Test', author_username='anna',
              body='Opening van Anna', is_op=True)
    db.session.add(op)
    db.session.flush()
    reply = Post(thread_id=t.id, user_id=anna.id, parent_id=op.id, author_name='Anna Test',
                 author_username='anna', body='Reactie van Anna')
    bram_post = Post(thread_id=t.id, user_id=bram.id, parent_id=op.id, author_name='Bram Test',
                     author_username='bram', body='Reply from Bram', source_lang='en')
    # Engels bericht, zodat bij een NL-lezer de vertaal(voorkeur)knoppen verschijnen.
    db.session.add_all([reply, bram_post])
    db.session.commit()
    return {'anna': anna, 'bram': bram, 'mod': mod, 'thread': t, 'op': op,
            'reply': reply, 'bram_post': bram_post}


def _post(client, path, data=None, token=None):
    data = dict(data or {})
    if token is not None:
        data['csrf_token'] = token
    return _req(client, 'POST', path, data=data)


# Elke actie: (pad, data, wie, controle-functie na een geslaagde actie).
def _actions(s):
    t, reply, bram_post = s['thread'], s['reply'], s['bram_post']
    return {
        'verwijderen': (f'/forum/post/{reply.id}/verwijderen', {}, 'anna',
                        lambda: db.session.get(Post, reply.id).body == '[bericht verwijderd]'),
        'bewerken': (f'/forum/post/{reply.id}/bewerken', {'body': 'Nieuwe tekst'}, 'anna',
                     lambda: db.session.get(Post, reply.id).body == 'Nieuwe tekst'),
        'liken': (f'/forum/like/{bram_post.id}', {}, 'anna',
                  lambda: PostLike.query.filter_by(post_id=bram_post.id).count() == 1),
        'sluiten': (f'/forum/{t.id}/close', {}, 'mod',
                    lambda: db.session.get(Thread, t.id).is_closed is True),
        'draad_starten': ('/forum/nieuw', {'title': 'Nieuwe draad', 'body': 'Eerste bericht'}, 'anna',
                          lambda: Thread.query.filter_by(title_prefix_nl='Nieuwe draad').count() == 1),
        'draad_bewerken': (f'/forum/{t.id}/bewerken', {'title': 'Andere titel', 'body': 'Andere opening'}, 'anna',
                           lambda: db.session.get(Thread, t.id).title_prefix_nl == 'Andere titel'),
        'vertaalvoorkeur': ('/profiel/vertaalvoorkeur', {'auto_translate': '1'}, 'anna',
                            lambda: db.session.get(User, s['anna'].id).auto_translate is True),
    }


ACTIONS = ['verwijderen', 'bewerken', 'liken', 'sluiten', 'draad_starten', 'draad_bewerken', 'vertaalvoorkeur']


@pytest.mark.parametrize('name', ACTIONS)
def test_action_without_token_is_rejected(setup, app, name):  # noqa: F811
    path, data, who, done = _actions(setup)[name]
    client = _client_for(app, setup[who])
    resp = _post(client, path, data)
    assert resp.status_code == 400
    db.session.expire_all()
    assert not done()


@pytest.mark.parametrize('name', ACTIONS)
def test_action_with_invalid_token_is_rejected(setup, app, name):  # noqa: F811
    path, data, who, done = _actions(setup)[name]
    client = _client_for(app, setup[who])
    _token(client, setup['thread'])  # sessie heeft een echt token
    resp = _post(client, path, data, token='niet-het-echte-token')
    assert resp.status_code == 400
    db.session.expire_all()
    assert not done()


@pytest.mark.parametrize('name', ACTIONS)
def test_action_with_valid_token_works(setup, app, name):  # noqa: F811
    path, data, who, done = _actions(setup)[name]
    client = _client_for(app, setup[who])
    resp = _post(client, path, data, token=_token(client, setup['thread']))
    assert resp.status_code in (200, 302)
    db.session.expire_all()
    assert done()


def test_unlike_and_reopen_with_valid_token(setup, app):  # noqa: F811
    t, bram_post = setup['thread'], setup['bram_post']
    anna = _client_for(app, setup['anna'])
    token = _token(anna, t)
    assert _post(anna, f'/forum/like/{bram_post.id}', token=token).get_json()['liked'] is True
    assert _post(anna, f'/forum/like/{bram_post.id}', token=token).get_json()['liked'] is False

    mod = _client_for(app, setup['mod'])
    token = _token(mod, t)
    _post(mod, f'/forum/{t.id}/close', token=token)
    assert _post(mod, f'/forum/{t.id}/reopen').status_code == 400
    db.session.expire_all()
    assert db.session.get(Thread, t.id).is_closed is True
    _post(mod, f'/forum/{t.id}/reopen', token=token)
    db.session.expire_all()
    assert db.session.get(Thread, t.id).is_closed is False


def test_permissions_still_checked_before_csrf(setup, app):  # noqa: F811
    # Een gewoon lid krijgt nog steeds 403 bij sluiten, ook zonder token.
    bram = _client_for(app, setup['bram'])
    assert _post(bram, f'/forum/{setup["thread"].id}/close').status_code == 403


def _forms_with(html, action):
    return re.findall(r'<form[^>]*action="%s"[^>]*>(.*?)</form>' % re.escape(action), html, re.S)


def test_every_action_form_renders_a_token(setup, app):  # noqa: F811
    t, reply = setup['thread'], setup['reply']
    mod = _client_for(app, setup['mod'])
    page = _req(mod, 'GET', f'/forum/{t.id}').get_data(as_text=True)
    forms = _forms_with(page, f'/forum/{t.id}/close')
    assert forms and all('name="csrf_token"' in f for f in forms)

    anna = _client_for(app, setup['anna'])
    page = _req(anna, 'GET', f'/forum/{t.id}').get_data(as_text=True)
    for action in (f'/forum/post/{reply.id}/verwijderen', '/profiel/vertaalvoorkeur'):
        forms = _forms_with(page, action)
        assert forms and all('name="csrf_token"' in f for f in forms), action
    # Token voor de fetch-acties (liken, bewerken) staat in het script.
    assert re.search(r'var csrfToken = "[^"]+";', page)

    for path, action in (('/forum/nieuw', '/forum/nieuw'),
                         (f'/forum/{t.id}/bewerken', f'/forum/{t.id}/bewerken'),
                         ('/profile', '/profiel/vertaalvoorkeur')):
        html = _req(anna, 'GET', path).get_data(as_text=True)
        forms = _forms_with(html, action)
        assert forms and all('name="csrf_token"' in f for f in forms), path

    _post(mod, f'/forum/{t.id}/close', token=_token(mod, t))
    for path in ('/forum/gesloten', '/moderator'):
        html = _req(mod, 'GET', path).get_data(as_text=True)
        forms = _forms_with(html, f'/forum/{t.id}/reopen')
        assert forms and all('name="csrf_token"' in f for f in forms), path
