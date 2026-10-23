"""
Tests voor de gelaagde reactiestructuur in het forum (app/routes/forum.py:
_render_thread, forum_branch, post_url; forum/thread.html): reageren op een
reactie, nesten tot niveau 4 (openingsbericht = niveau 1), de doorlink naar
een eigen weergave die weer bij 1 telt, in- en uitklappen met tellingen van
alleen zichtbare berichten, geen rol- of statusbadges bij de auteur, en het
medaille-keurmerk.

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
from app.models import QualityAward
from app.routes.forum import MAX_LEVEL, post_url


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
        u.accept_rules()  # bestaande bijdrager (gegrandfatherd), zie forum fase A
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

def _node_level(tree, post):
    cls = tree.attrs[f'node-{post.id}']['class']
    return int(cls.split('gu-node--l')[1].split()[0])


class TestReplyToReply:
    def test_reply_to_a_reply_is_saved_with_parent(self, app, db, forum_users, thread):
        root, reply = _chain(db, thread, forum_users, 1)
        client = _login(app, forum_users[0])
        resp = _req(client, 'POST', f'/forum/{thread.id}',
                    data={'body': 'antwoord op de reactie', 'parent_id': reply.id})
        assert resp.status_code == 302
        new = Post.query.filter_by(thread_id=thread.id, body='antwoord op de reactie').one()
        assert new.parent_id == reply.id and new.parent.parent_id == root.id
        assert resp.headers['Location'].endswith(f'/forum/{thread.id}#post-{new.id}')

    def test_parent_from_other_thread_still_rejected(self, app, db, forum_users, thread):
        other = Thread(eyebrow_nl='x', eyebrow_en='x', title_prefix_nl='x', title_prefix_en='x')
        db.session.add(other)
        db.session.commit()
        foreign = _chain(db, other, forum_users, 0)[0]
        client = _login(app, forum_users[0])
        _req(client, 'POST', f'/forum/{thread.id}', data={'body': 'kapot', 'parent_id': foreign.id})
        assert Post.query.filter_by(body='kapot').count() == 0
        _delete_thread(db, other)

    def test_reply_beyond_level_four_redirects_to_branch_view(self, app, db, forum_users, thread):
        posts = _chain(db, thread, forum_users, 3)  # niveaus 1 tot 4
        client = _login(app, forum_users[0])  # posts[3] is van Bram
        html = _req(client, 'GET', f'/forum/{thread.id}').get_data(as_text=True)
        assert f'id="reply-form-{posts[3].id}"' in html
        resp = _req(client, 'POST', f'/forum/{thread.id}', data={'body': 'niveau vijf', 'parent_id': posts[3].id})
        new = Post.query.filter_by(body='niveau vijf').one()
        assert resp.headers['Location'].endswith(f'/forum/{thread.id}/tak/{posts[3].id}#post-{new.id}')


class TestNesting:
    @pytest.mark.parametrize('lang', ['nl', 'en'])
    def test_levels_one_to_four_nested(self, client, db, forum_users, thread, lang):
        posts = _chain(db, thread, forum_users, MAX_LEVEL - 1)
        tree = _parse(_get(client, f'/forum/{thread.id}', lang))
        for level, p in enumerate(posts, start=1):
            assert _node_level(tree, p) == level
            if level > 1:
                assert f'replies-{posts[level - 2].id}' in tree.ancestors[f'post-{p.id}']

    def test_top_level_composer_replies_sit_under_opening_post(self, client, db, forum_users, thread):
        op, reply = _chain(db, thread, forum_users, 1)
        top = Post(thread_id=thread.id, user_id=forum_users[0].id, parent_id=None,
                   author_name='', author_username='anna_nest', body='via de composer', source_lang='nl')
        db.session.add(top)
        db.session.commit()
        tree = _parse(_get(client, f'/forum/{thread.id}', 'nl'))
        assert _node_level(tree, op) == 1
        assert _node_level(tree, top) == 2 and _node_level(tree, reply) == 2
        assert f'replies-{op.id}' in tree.ancestors[f'post-{top.id}']

    def test_siblings_oldest_first(self, client, db, forum_users, thread):
        root = _chain(db, thread, forum_users, 0)[0]
        now = datetime.utcnow()
        newer = Post(thread_id=thread.id, parent_id=root.id, author_name='', author_username='n',
                     body='jongste', created_at=now)
        older = Post(thread_id=thread.id, parent_id=root.id, author_name='', author_username='o',
                     body='oudste', created_at=now - timedelta(minutes=30))
        db.session.add_all([newer, older])
        db.session.commit()
        html = _get(client, f'/forum/{thread.id}', 'nl')
        assert html.index(f'id="post-{older.id}"') < html.index(f'id="post-{newer.id}"')

    def test_demo_threads_render_in_both_languages(self, client):
        import re
        for lang in ('nl', 'en'):
            html = _get(client, '/forum', lang)
            for tid in {int(x) for x in re.findall(r'href="/forum/(\d+)"', html)}:
                _get(client, f'/forum/{tid}', lang)


class TestDepthLimitAndBranch:
    @pytest.mark.parametrize('lang,label', [('nl', 'Verder in dit gesprek (3 reacties)'),
                                            ('en', 'Continue this conversation (3 replies)')])
    def test_no_indent_beyond_level_four_link_instead(self, client, db, forum_users, thread, lang, label):
        posts = _chain(db, thread, forum_users, 6)  # niveaus 1 tot 7
        html = _get(client, f'/forum/{thread.id}', lang)
        tree = _parse(html)
        assert max(_node_level(tree, p) for p in posts[:4]) == 4
        for deeper in posts[4:]:
            assert f'id="post-{deeper.id}"' not in html
        links = _find(tree, 'a', 'gu-node__more')
        assert [a['href'] for a, _ in links] == [f'/forum/{thread.id}/tak/{posts[3].id}']
        assert f'node-{posts[3].id}' in links[0][1] and label in html

    def test_branch_view_restarts_at_one_with_same_rules(self, client, db, forum_users, thread):
        posts = _chain(db, thread, forum_users, 9)  # niveaus 1 tot 10
        tree = _parse(_get(client, f'/forum/{thread.id}/tak/{posts[3].id}', 'nl'))
        assert [_node_level(tree, p) for p in posts[3:7]] == [1, 2, 3, 4]
        assert f'post-{posts[7].id}' not in tree.ancestors and f'post-{posts[2].id}' not in tree.ancestors
        links = [a['href'] for a, _ in _find(tree, 'a', 'gu-node__more')]
        assert links == [f'/forum/{thread.id}/tak/{posts[6].id}']
        deeper = _parse(_get(client, f'/forum/{thread.id}/tak/{posts[6].id}', 'nl'))
        assert [_node_level(deeper, p) for p in posts[6:10]] == [1, 2, 3, 4]

    def test_post_url_points_to_the_right_view(self, client, db, forum_users, thread):
        posts = _chain(db, thread, forum_users, 9)
        t = thread.id
        assert post_url(posts[0]).endswith(f'/forum/{t}#post-{posts[0].id}')
        assert post_url(posts[3]).endswith(f'/forum/{t}#post-{posts[3].id}')
        for i in (4, 5, 6):
            assert post_url(posts[i]).endswith(f'/forum/{t}/tak/{posts[3].id}#post-{posts[i].id}')
        for i in (7, 8, 9):
            assert post_url(posts[i]).endswith(f'/forum/{t}/tak/{posts[6].id}#post-{posts[i].id}')

    def test_post_without_quality_mark_counts_and_shows(self, client, db, forum_users, thread):
        """Kwaliteit is alleen een beloning: een bijdrage zonder keurmerk telt
        gewoon mee en staat gewoon in de (doorlink)weergave."""
        posts = _chain(db, thread, forum_users, 6)
        before = _get(client, f'/forum/{thread.id}', 'nl')
        db.session.add(QualityAward(post_id=posts[5].id, source='ai', status='afgewezen', post_lang='nl'))
        db.session.commit()
        try:
            html = _get(client, f'/forum/{thread.id}', 'nl')
            for marker in ('Verder in dit gesprek (3 reacties)', f'id="collapsed-{posts[0].id}" data-count="6"'):
                assert marker in before and marker in html
            branch = _get(client, f'/forum/{thread.id}/tak/{posts[3].id}', 'nl')
            assert posts[5].body in branch and 'verborgen wegens onvoldoende kwaliteit' not in branch
        finally:
            db.session.delete(QualityAward.query.filter_by(post_id=posts[5].id).one())
            db.session.commit()


class TestCollapse:
    @pytest.mark.parametrize('lang,collapse,hidden', [
        ('nl', 'Tak inklappen (2 reacties)', '2 reacties verborgen'),
        ('en', 'Collapse branch (2 replies)', '2 replies hidden'),
    ])
    def test_toggle_line_and_hidden_note(self, client, db, forum_users, thread, lang, collapse, hidden):
        posts = _chain(db, thread, forum_users, 2)
        tree = _parse(_get(client, f'/forum/{thread.id}', lang))
        toggles = {a['data-node']: a for a, _ in _find(tree, 'button', 'gu-node__toggle')}
        t = toggles[str(posts[0].id)]
        assert t['aria-expanded'] == 'true' and t['aria-controls'] == f'replies-{posts[0].id}'
        assert t['aria-label'] == collapse
        assert set(toggles) == {str(posts[0].id), str(posts[1].id)}  # het blad heeft geen knopje
        lines = {a['data-node'] for a, _ in _find(tree, 'button', 'gu-node__line')}
        assert lines == set(toggles)
        note = tree.attrs[f'collapsed-{posts[0].id}']
        assert 'hidden' in note and note['data-count'] == '2'
        html = _get(client, f'/forum/{thread.id}', lang)
        assert hidden in html

    def test_singular_collapsed_note(self, client, db, forum_users, thread):
        posts = _chain(db, thread, forum_users, 3)  # posts[2] (niveau 3) heeft precies 1 reactie
        html = _get(client, f'/forum/{thread.id}', 'nl')
        assert f'id="collapsed-{posts[2].id}" data-count="1" hidden>1 reactie verborgen<' in html

    def test_no_toggle_on_level_four_link_singular(self, client, db, forum_users, thread):
        posts = _chain(db, thread, forum_users, 4)  # posts[3] (niveau 4) heeft 1 reactie
        html = _get(client, f'/forum/{thread.id}', 'nl')
        toggles = {a['data-node'] for a, _ in _find(_parse(html), 'button', 'gu-node__toggle')}
        assert str(posts[3].id) not in toggles and 'Verder in dit gesprek (1 reactie)' in html


class TestAuthorLine:
    def test_no_role_status_badge_or_age(self, client, db, forum_users, thread):
        p = Post(thread_id=thread.id, user_id=forum_users[0].id, author_name='', author_username='anna_nest',
                 author_role='founder', author_badge_nl='Handelsrecht · Universiteit Leiden', author_age=52,
                 body='met rollen', source_lang='nl', is_op=True)
        q = Post(thread_id=thread.id, user_id=forum_users[1].id, author_name='', author_username='bram_nest',
                 author_role='expert', body='expert', source_lang='nl')
        db.session.add_all([p, q])
        db.session.commit()
        html = _get(client, f'/forum/{thread.id}', 'nl')
        main = html.split('<main', 1)[1].split('</main>')[0]
        for gone in ('gu-post__role', 'Initiatiefnemer', '>Expert<', 'Handelsrecht', 'gu-post__age',
                     'gu-status-badge', 'gu-post__badge'):
            assert gone not in main, gone
        assert 'gu-post__time' in main and '@anna_nest' in main


class TestMedal:
    def test_medal_links_to_discussion_with_explanation(self, client, db, forum_users, thread):
        p = _chain(db, thread, forum_users, 1)[1]
        db.session.add(QualityAward(post_id=p.id, source='ai', status='toegekend', criterion='K1',
                                    ai_criterion='K1', post_lang='nl', explanation_nl='Nieuw.'))
        db.session.commit()
        html = _get(client, f'/forum/{thread.id}', 'nl')
        tree = _parse(html)
        marks = [(a, anc) for a, anc in _find(tree, 'a', 'gu-quality__mark') if f'post-{p.id}' in anc]
        assert len(marks) == 1
        a = marks[0][0]
        title = 'Kwaliteitskeurmerk: deze bijdrage voldoet aan de kwaliteitscriteria. Klik voor de onderbouwing.'
        assert a['href'] == f'/forum/kwaliteit/{p.id}' and a['title'] == title and a['aria-label'] == title
        assert 'gu-quality__medal' in html and 'M6 0.5 L11.5 6' not in html  # geen diamant meer
        db.session.delete(QualityAward.query.filter_by(post_id=p.id).one())
        db.session.commit()
