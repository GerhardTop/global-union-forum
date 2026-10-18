"""
Tests voor geneste reacties in het forum (app/routes/forum.py, forum_thread()
en forum/_post.html): reageren op een reactie, de boom over meerdere niveaus,
de dieptegrens die platslaat met een "in antwoord op"-label, de inklapknop,
en dat bestaande reacties van één niveau nog werken.

Let op: de sessie-brede app-fixture uit conftest.py houdt één app_context
open, dus ook één `g` voor alle requests. Flask-Babel (locale) en Flask-Login
(current_user) cachen daarin; _req() wist die caches vóór elk request, zodat
elk request zich gedraagt als op een echte server.
"""
from datetime import datetime, timedelta
from html.parser import HTMLParser

import pytest
from flask import g
from flask_babel import refresh

from app import bcrypt
from app.models import Post, Thread, User
from app.routes.forum import MAX_REPLY_INDENT


# ── Helpers ──────────────────────────────────────────────────────────────

class _Tree(HTMLParser):
    """Verzamelt per element-id de ids van alle omhullende elementen, plus
    de class- en overige attributen, zodat we nesting kunnen controleren."""

    VOID = {'input', 'img', 'br', 'hr', 'meta', 'link', 'source', 'wbr', 'area', 'col', 'embed', 'track'}

    def __init__(self):
        super().__init__()
        self.stack = []          # (tag, id)
        self.ancestors = {}      # id -> [omhullende ids, buitenste eerst]
        self.attrs = {}          # id -> dict(attrs)
        self.elements = []       # (tag, attrs, omhullende ids) voor elk element

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        el_id = a.get('id')
        self.elements.append((tag, a, [i for _, i in self.stack if i]))
        if el_id:
            self.ancestors[el_id] = [i for _, i in self.stack if i]
            self.attrs[el_id] = a
        if tag not in self.VOID:
            self.stack.append((tag, el_id))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break


def _parse(html):
    t = _Tree()
    t.feed(html)
    return t


def _find(tree, tag, cls):
    return [(a, anc) for t, a, anc in tree.elements
            if t == tag and cls in (a.get('class') or '').split()]


def _req(client, method, path, **kw):
    g.pop('_login_user', None)
    refresh()
    return client.open(path, method=method, **kw)


def _get(client, path, lang):
    _req(client, 'GET', f'/lang/{lang}')
    resp = _req(client, 'GET', path)
    assert resp.status_code == 200, resp.status_code
    return resp.get_data(as_text=True)


@pytest.fixture
def forum_users(db):
    users = []
    for uname, first in (('anna_nest', 'Anna'), ('bram_nest', 'Bram')):
        u = User(username=uname, first_name=first, last_name='Test',
                 email=f'{uname}@example.com',
                 password_hash=bcrypt.generate_password_hash('Sterk1!ww').decode('utf-8'),
                 verified=True)
        db.session.add(u)
        users.append(u)
    db.session.commit()
    yield users
    for u in users:
        for p in Post.query.filter_by(user_id=u.id):
            p.user_id = None
        db.session.delete(u)
    db.session.commit()


def _delete_thread(db, t):
    # Via de ORM (niet bulk), zodat de sessie geen verouderde objecten houdt
    # waarvan SQLite de ids daarna hergebruikt.
    for p in Post.query.filter_by(thread_id=t.id):
        db.session.delete(p)
    db.session.delete(t)
    db.session.commit()


@pytest.fixture
def thread(db):
    t = Thread(eyebrow_nl='Test', eyebrow_en='Test',
               title_prefix_nl='Geneste test', title_prefix_en='Nested test')
    db.session.add(t)
    db.session.commit()
    yield t
    _delete_thread(db, t)


def _chain(db, thread, users, length):
    """Root (OP) plus een keten van `length` reacties, elk op de vorige,
    afwisselend door Anna en Bram. Geeft de posts terug, index = diepte."""
    start = datetime.utcnow() - timedelta(hours=length + 1)
    posts, parent = [], None
    for depth in range(length + 1):
        u = users[depth % 2]
        p = Post(thread_id=thread.id, user_id=u.id, parent_id=parent.id if parent else None,
                 author_name=f'{u.first_name} Test', author_username=u.username,
                 body=f'bericht op diepte {depth}', source_lang='nl',
                 is_op=(depth == 0), created_at=start + timedelta(hours=depth))
        db.session.add(p)
        db.session.flush()
        posts.append(p)
        parent = p
    db.session.commit()
    return posts


def _login(app, user):
    # Rechtstreeks via de sessie i.p.v. POST /login: de login-route heeft een
    # rate limit die over de hele testsessie meetelt.
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user.id)
        sess['_fresh'] = True
    return client


# ── Tests ────────────────────────────────────────────────────────────────

class TestReplyToReply:
    def test_reply_to_a_reply_is_saved_with_parent(self, app, db, forum_users, thread):
        root, reply = _chain(db, thread, forum_users, 1)
        client = _login(app, forum_users[0])
        resp = _req(client, 'POST', f'/forum/{thread.id}',
                    data={'body': 'antwoord op de reactie', 'parent_id': reply.id})
        assert resp.status_code == 302
        new = Post.query.filter_by(thread_id=thread.id, body='antwoord op de reactie').one()
        assert new.parent_id == reply.id
        assert new.parent.parent_id == root.id
        assert resp.headers['Location'].endswith(f'#post-{new.id}')

    def test_parent_from_other_thread_still_rejected(self, app, db, forum_users, thread):
        other = Thread(eyebrow_nl='x', eyebrow_en='x', title_prefix_nl='x', title_prefix_en='x')
        db.session.add(other)
        db.session.commit()
        foreign = _chain(db, other, forum_users, 0)[0]
        client = _login(app, forum_users[0])
        _req(client, 'POST', f'/forum/{thread.id}', data={'body': 'kapot', 'parent_id': foreign.id})
        assert Post.query.filter_by(body='kapot').count() == 0
        _delete_thread(db, other)

    def test_reply_button_available_beyond_old_two_level_limit(self, app, db, forum_users, thread):
        posts = _chain(db, thread, forum_users, 3)
        # posts[3] is van Bram (diepte 3); Anna mag erop reageren.
        client = _login(app, forum_users[0])
        html = _req(client, 'GET', f'/forum/{thread.id}').get_data(as_text=True)
        assert f'id="reply-form-{posts[3].id}"' in html


class TestTreeRendering:
    @pytest.mark.parametrize('lang', ['nl', 'en'])
    def test_tree_nests_multiple_levels(self, client, db, forum_users, thread, lang):
        posts = _chain(db, thread, forum_users, MAX_REPLY_INDENT)
        tree = _parse(_get(client, f'/forum/{thread.id}', lang))
        for depth in range(1, MAX_REPLY_INDENT + 1):
            container = f'replies-{posts[depth - 1].id}'
            assert f'gu-thread__replies--d{depth}' in tree.attrs[container]['class']
            assert container in tree.ancestors[f'post-{posts[depth].id}']
        # Binnen de grens geen platgeslagen label op desktop.
        assert 'gu-post__inreply--flat' not in _get(client, f'/forum/{thread.id}', lang)

    def test_siblings_are_oldest_first(self, client, db, forum_users, thread):
        root = _chain(db, thread, forum_users, 0)[0]
        now = datetime.utcnow()
        newer = Post(thread_id=thread.id, parent_id=root.id, author_name='Nieuw', author_username='n',
                     body='jongste', created_at=now)
        older = Post(thread_id=thread.id, parent_id=root.id, author_name='Oud', author_username='o',
                     body='oudste', created_at=now - timedelta(minutes=30))
        db.session.add_all([newer, older])
        db.session.commit()
        html = _get(client, f'/forum/{thread.id}', 'nl')
        assert html.index(f'id="post-{older.id}"') < html.index(f'id="post-{newer.id}"')


class TestDepthLimit:
    @pytest.mark.parametrize('lang,label', [('nl', 'in antwoord op'), ('en', 'in reply to')])
    def test_beyond_limit_is_flattened_with_label(self, client, db, forum_users, thread, lang, label):
        posts = _chain(db, thread, forum_users, MAX_REPLY_INDENT + 2)
        html = _get(client, f'/forum/{thread.id}', lang)
        tree = _parse(html)
        last_indent = posts[MAX_REPLY_INDENT]
        flat_container = f'replies-{last_indent.id}'
        assert 'gu-thread__replies--flat' in tree.attrs[flat_container]['class']
        assert f'gu-thread__replies--d{MAX_REPLY_INDENT + 1}' not in html
        for deeper in posts[MAX_REPLY_INDENT + 1:]:
            anc = tree.ancestors[f'post-{deeper.id}']
            assert flat_container in anc
            # Niet verder genest dan het platte blok: geen ander replies-blok
            # binnen het platte blok.
            assert not [a for a in anc[anc.index(flat_container) + 1:] if a.startswith('replies-')]
            parent = posts[posts.index(deeper) - 1]
            links = [a for a, anc in _find(tree, 'a', 'gu-post__inreply')
                     if f'post-{deeper.id}' in anc]
            assert len(links) == 1
            assert links[0]['href'] == f'#post-{parent.id}'
            assert 'gu-post__inreply--flat' in links[0]['class']
            assert f'{label} {parent.author_name}' in html

    def test_mobile_label_present_from_depth_three(self, client, db, forum_users, thread):
        # Op diepte 3 en 4 springt mobiel niet meer in; het label staat er dan
        # al (alleen via CSS zichtbaar op mobiel), zonder de --flat-modifier.
        posts = _chain(db, thread, forum_users, 3)
        tree = _parse(_get(client, f'/forum/{thread.id}', 'nl'))
        links = _find(tree, 'a', 'gu-post__inreply')
        assert [(a['href'], a['class']) for a, _ in links] == [(f'#post-{posts[2].id}', 'gu-post__inreply')]
        assert f'post-{posts[3].id}' in links[0][1]


class TestCollapse:
    @pytest.mark.parametrize('lang,hide,show', [
        ('nl', '3 reacties verbergen', '3 reacties tonen'),
        ('en', 'Hide 3 replies', 'Show 3 replies'),
    ])
    def test_collapse_toggle_present_and_expanded(self, client, db, forum_users, thread, lang, hide, show):
        posts = _chain(db, thread, forum_users, 3)
        tree = _parse(_get(client, f'/forum/{thread.id}', lang))
        btns = {a['data-post-id']: a for a, _ in _find(tree, 'button', 'gu-post__collapse-btn')}
        root_btn = btns[str(posts[0].id)]
        assert root_btn['aria-controls'] == f'replies-{posts[0].id}'
        assert root_btn['aria-expanded'] == 'true'
        assert root_btn['data-label-hide'] == hide
        assert root_btn['data-label-show'] == show
        # Het blok is standaard uitgeklapt.
        assert 'hidden' not in tree.attrs[f'replies-{posts[0].id}']
        # Elk bericht met kinderen heeft een knop, het diepste (geen kinderen) niet.
        assert set(btns) == {str(p.id) for p in posts[:3]}

    def test_flat_posts_with_children_get_ancestor_based_toggle(self, client, db, forum_users, thread):
        posts = _chain(db, thread, forum_users, MAX_REPLY_INDENT + 2)
        tree = _parse(_get(client, f'/forum/{thread.id}', 'nl'))
        flat_parent = posts[MAX_REPLY_INDENT + 1]
        flat_child = posts[MAX_REPLY_INDENT + 2]
        btns = {a['data-post-id']: a for a, _ in _find(tree, 'button', 'gu-post__collapse-btn')}
        # Plat bericht met kind: knop zonder doelblok, werkt via data-ancestors.
        assert 'data-target' not in btns[str(flat_parent.id)]
        assert btns[str(flat_parent.id)]['data-label-hide'] == '1 reactie verbergen'
        # Elk plat item: zijn data-ancestors, gekoppeld aan het eerstvolgende
        # <article> (het bericht in dat item).
        ancestors_of, pending = {}, None
        for tag, a, _ in tree.elements:
            if tag == 'div' and 'gu-thread__flatitem' in (a.get('class') or '').split():
                pending = a['data-ancestors'].split()
            elif tag == 'article' and pending is not None:
                ancestors_of[a['id']], pending = pending, None
        assert ancestors_of[f'post-{flat_parent.id}'] == []
        assert ancestors_of[f'post-{flat_child.id}'] == [str(flat_parent.id)]


class TestOneLevelStillWorks:
    @pytest.mark.parametrize('lang', ['nl', 'en'])
    def test_existing_one_level_replies_and_top_level_posts(self, client, db, forum_users, thread, lang):
        root, reply = _chain(db, thread, forum_users, 1)
        top_level = Post(thread_id=thread.id, user_id=forum_users[0].id, parent_id=None,
                         author_name='Anna Test', author_username='anna_nest',
                         body='reactie via de onderste composer', source_lang='nl')
        db.session.add(top_level)
        db.session.commit()
        tree = _parse(_get(client, f'/forum/{thread.id}', lang))
        assert f'replies-{root.id}' in tree.ancestors[f'post-{reply.id}']
        assert not [a for a in tree.ancestors[f'post-{top_level.id}'] if a.startswith('replies-')]

    def test_demo_threads_render_in_both_languages(self, client):
        for lang in ('nl', 'en'):
            html = _get(client, '/forum', lang)
            for tid in {int(x) for x in __import__('re').findall(r'href="/forum/(\d+)"', html)}:
                _get(client, f'/forum/{tid}', lang)
