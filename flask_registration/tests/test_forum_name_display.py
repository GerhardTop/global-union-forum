"""
Naamweergave in het forum: standaard het pseudoniem, de echte naam (en
LinkedIn) alleen als de auteur dat zelf kiest (User.show_real_name), altijd
volgens de HUIDIGE instelling van de auteur.

Dekt: Post.public_author_name / public_author_initial / public_linkedin_url,
de weergave op de threadpagina (naam, avatar-initiaal, LinkedIn, "in
antwoord op"), wisselen van de instelling, een verwijderde auteur, de
profielroute /profiel/naamweergave (bevestiging vereist bij aanzetten), de
profielpagina, en de migratie (bestaande gebruikers pseudoniem, demo's naam).

Gedeelde conftest-app (CSRF uit): _req() wist vóór elk request de in `g`
gecachte current_user en Babel-locale (zie de Babel/g-test-gotcha).
"""
import re
import uuid

import pytest
from flask import g
from flask_babel import refresh
from sqlalchemy import text

from app import bcrypt
from app.models import Post, Thread, User
from app.routes.forum import DEMO_USER_EMAILS

LINKEDIN = 'https://www.linkedin.com/in/anna-de-vries'


@pytest.fixture
def make_user(db):
    created = []

    def _make(first='Anna', last='de Vries', linkedin=LINKEDIN, show=False):
        name = f'u_{uuid.uuid4().hex[:8]}'
        u = User(username=name, first_name=first, last_name=last, email=f'{name}@example.com',
                 password_hash=bcrypt.generate_password_hash('Sterk1!ww').decode('utf-8'),
                 verified=True, linkedin_url=linkedin, show_real_name=show)
        u.accept_rules()
        db.session.add(u)
        db.session.commit()
        created.append(u.id)
        return u

    yield _make
    for uid in created:
        for p in Post.query.filter_by(user_id=uid):
            p.user_id = None
        u = db.session.get(User, uid)
        if u:
            db.session.delete(u)
    db.session.commit()


@pytest.fixture
def thread(db):
    t = Thread(eyebrow_nl='T', eyebrow_en='T', title_prefix_nl='Namen', title_prefix_en='Names')
    db.session.add(t)
    db.session.commit()
    yield t
    for p in Post.query.filter_by(thread_id=t.id):
        db.session.delete(p)
    db.session.delete(t)
    db.session.commit()


def _post(db, thread, user, body, parent=None):
    p = Post(thread_id=thread.id, user_id=user.id, parent_id=parent.id if parent else None,
             author_name=user.real_name or '', author_username=user.username, body=body, source_lang='nl')
    db.session.add(p)
    db.session.commit()
    return p


def _client(app, user=None, lang='nl'):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['lang'] = lang
        if user is not None:
            sess['_user_id'] = str(user.id)
            sess['_fresh'] = True
    return client


def _req(client, method, path, **kw):
    g.pop('_login_user', None)
    refresh()
    return client.open(path, method=method, **kw)


def _article(html, post):
    """De HTML van één bericht (tot het volgende <article>)."""
    start = html.index(f'id="post-{post.id}"')
    nxt = html.find('<article', start)
    return html[start:nxt if nxt != -1 else len(html)]


# ── Helpers op het model ─────────────────────────────────────────────────

class TestModelHelpers:
    def test_default_is_pseudonym(self, db, make_user, thread):
        p = _post(db, thread, make_user(), 'x')
        assert p.public_author_name() == '@' + p.author.username
        assert p.public_linkedin_url() is None
        assert p.public_author_initial() == p.author.username[0].upper()

    def test_real_name_when_chosen(self, db, make_user, thread):
        p = _post(db, thread, make_user(show=True), 'x')
        assert p.public_author_name() == 'Anna de Vries'
        assert p.public_linkedin_url() == LINKEDIN
        assert p.public_author_initial() == 'A'

    def test_real_name_without_linkedin(self, db, make_user, thread):
        p = _post(db, thread, make_user(show=True, linkedin=None), 'x')
        assert p.public_author_name() == 'Anna de Vries' and p.public_linkedin_url() is None

    def test_chosen_but_no_name_falls_back_to_pseudonym(self, db, make_user, thread):
        p = _post(db, thread, make_user(first=None, last=None, show=True), 'x')
        assert p.public_author_name() == '@' + p.author.username
        assert p.public_linkedin_url() is None

    def test_deleted_author_falls_back_to_stored_pseudonym(self, db, make_user, thread):
        user = make_user(show=True)
        p = _post(db, thread, user, 'x')
        stored = p.author_username
        p.user_id = None
        db.session.delete(user)
        db.session.commit()
        db.session.expire_all()
        p = db.session.get(Post, p.id)
        assert p.author is None
        assert p.public_author_name() == '@' + stored
        assert p.public_linkedin_url() is None


# ── Weergave op de threadpagina ──────────────────────────────────────────

class TestThreadPage:
    def test_pseudonym_hides_name_and_linkedin(self, app, db, make_user, thread):
        anna = make_user()
        p = _post(db, thread, anna, 'mijn bericht')
        part = _article(_req(_client(app), 'GET', f'/forum/{thread.id}').get_data(as_text=True), p)
        assert '@' + anna.username in part
        assert 'Anna de Vries' not in part and 'Anna' not in part
        assert LINKEDIN not in part and 'gu-post__linkedin' not in part
        assert f'<div class="gu-post__avatar">{anna.username[0].upper()}</div>' in part

    def test_real_name_shows_name_and_linkedin(self, app, db, make_user, thread):
        anna = make_user(show=True)
        p = _post(db, thread, anna, 'mijn bericht')
        part = _article(_req(_client(app), 'GET', f'/forum/{thread.id}').get_data(as_text=True), p)
        assert 'Anna de Vries' in part and LINKEDIN in part
        assert '<div class="gu-post__avatar">A</div>' in part

    def test_real_name_without_linkedin_shows_no_link(self, app, db, make_user, thread):
        p = _post(db, thread, make_user(show=True, linkedin=None), 'mijn bericht')
        part = _article(_req(_client(app), 'GET', f'/forum/{thread.id}').get_data(as_text=True), p)
        assert 'Anna de Vries' in part and 'gu-post__linkedin' not in part

    def test_switching_updates_existing_posts(self, app, db, make_user, thread):
        anna = make_user()
        posts = [_post(db, thread, anna, f'bericht {i}') for i in range(2)]
        html = _req(_client(app), 'GET', f'/forum/{thread.id}').get_data(as_text=True)
        assert all('Anna de Vries' not in _article(html, p) for p in posts)

        anna.show_real_name = True
        db.session.commit()
        html = _req(_client(app), 'GET', f'/forum/{thread.id}').get_data(as_text=True)
        assert all('Anna de Vries' in _article(html, p) and LINKEDIN in _article(html, p) for p in posts)

        anna.show_real_name = False
        db.session.commit()
        html = _req(_client(app), 'GET', f'/forum/{thread.id}').get_data(as_text=True)
        assert all('Anna de Vries' not in _article(html, p) and LINKEDIN not in _article(html, p) for p in posts)

    def test_in_reply_to_label_follows_choice(self, app, db, make_user, thread):
        anna, bram = make_user(), make_user(first='Bram', last='Bakker')
        chain = [_post(db, thread, anna, 'root')]
        for i in range(3):
            chain.append(_post(db, thread, bram if i % 2 == 0 else anna, f'r{i}', parent=chain[-1]))
        # chain[3] (van Bram) staat op diepte 3 en antwoordt op chain[2] van
        # Anna: daar staat het label met de naam van Anna.
        assert chain[2].author.id == anna.id
        html = _req(_client(app), 'GET', f'/forum/{thread.id}').get_data(as_text=True)
        assert f'in antwoord op @{anna.username}' in html
        anna.show_real_name = True
        db.session.commit()
        html = _req(_client(app), 'GET', f'/forum/{thread.id}').get_data(as_text=True)
        assert 'in antwoord op Anna de Vries' in html

    def test_demo_posts_keep_their_demo_names(self, app):
        html = _req(_client(app), 'GET', '/forum/1').get_data(as_text=True)
        assert '(demo)' in html


# ── Profiel: de keuze maken ──────────────────────────────────────────────

class TestProfileChoice:
    XHR = {'X-Requested-With': 'XMLHttpRequest'}

    def test_enabling_requires_confirmation(self, app, db, make_user):
        anna = make_user()
        client = _client(app, anna)
        resp = _req(client, 'POST', '/profiel/naamweergave', data={'show_real_name': '1'}, headers=self.XHR)
        assert resp.status_code == 422 and resp.get_json()['error'] == 'not_confirmed'
        db.session.expire_all()
        assert db.session.get(User, anna.id).show_real_name is False

        resp = _req(client, 'POST', '/profiel/naamweergave', data={'show_real_name': '1', 'confirm': '1'},
                    headers=self.XHR)
        assert resp.get_json() == {'ok': True, 'show_real_name': True}
        db.session.expire_all()
        assert db.session.get(User, anna.id).show_real_name is True

    def test_switching_back_is_direct(self, app, db, make_user):
        anna = make_user(show=True)
        resp = _req(_client(app, anna), 'POST', '/profiel/naamweergave', data={'show_real_name': '0'},
                    headers=self.XHR)
        assert resp.get_json() == {'ok': True, 'show_real_name': False}
        db.session.expire_all()
        assert db.session.get(User, anna.id).show_real_name is False

    def test_no_real_name_cannot_enable(self, app, db, make_user):
        nameless = make_user(first=None, last=None)
        resp = _req(_client(app, nameless), 'POST', '/profiel/naamweergave',
                    data={'show_real_name': '1', 'confirm': '1'}, headers=self.XHR)
        assert resp.status_code == 422 and resp.get_json()['error'] == 'no_name'

    def test_without_js_form_post_redirects_back(self, app, db, make_user):
        anna = make_user()
        client = _client(app, anna)
        resp = _req(client, 'POST', '/profiel/naamweergave', data={'show_real_name': '1'})
        assert resp.status_code == 303 and resp.headers['Location'].endswith('/profile#guNameDisplay')
        page = _req(client, 'GET', '/profile').get_data(as_text=True)
        assert 'Bevestig eerst dat je echte naam zichtbaar mag worden.' in page
        resp = _req(client, 'POST', '/profiel/naamweergave', data={'show_real_name': '1', 'confirm': '1'})
        assert resp.status_code == 302
        db.session.expire_all()
        assert db.session.get(User, anna.id).show_real_name is True

    def test_anonymous_cannot_change(self, app):
        resp = _req(_client(app), 'POST', '/profiel/naamweergave', data={'show_real_name': '1', 'confirm': '1'})
        assert resp.status_code == 302 and '/login' in resp.headers['Location']

    @pytest.mark.parametrize('lang,question,pseudo', [
        ('nl', 'Hoe verschijn je in het forum?', 'Pseudoniem'),
        ('en', 'How do you appear in the forum?', 'Pseudonym'),
    ])
    def test_profile_page_shows_choice(self, app, make_user, lang, question, pseudo):
        anna = make_user()
        page = _req(_client(app, anna, lang), 'GET', '/profile').get_data(as_text=True)
        assert question in page and f'{pseudo} (@{anna.username})' in page
        assert 'id="guRealNameModal"' in page
        # Pseudoniem: LinkedIn-blok verborgen, de uitleg zichtbaar.
        assert re.search(r'id="guLinkedInSection" hidden', page)
        assert re.search(r'id="guLinkedInPseudoNote" class="gu-namechoice__hint">', page)

    def test_profile_page_real_name_mode_shows_linkedin(self, app, make_user):
        page = _req(_client(app, make_user(show=True)), 'GET', '/profile').get_data(as_text=True)
        assert re.search(r'id="guLinkedInSection">', page)
        assert 'Je LinkedIn-profiel staat publiek bij je naam in het forum.' in page

    def test_nameless_user_sees_disabled_real_name_option(self, app, make_user):
        page = _req(_client(app, make_user(first=None, last=None)), 'GET', '/profile').get_data(as_text=True)
        assert 'value="1" disabled' in page
        assert 'Er is geen echte naam bekend bij je account.' in page


# ── Migratie ─────────────────────────────────────────────────────────────

class TestMigration:
    def test_existing_users_pseudonym_demo_accounts_real_name(self, app, db, make_user):
        from app import _migrate_columns
        anna = make_user(show=True)  # wordt door de migratie (kolom opnieuw) pseudoniem
        db.session.close()
        with db.engine.connect() as conn:
            conn.execute(text('ALTER TABLE users DROP COLUMN show_real_name'))
            conn.commit()
        db.engine.dispose()  # SQLite: verse verbindingen na DROP COLUMN
        _migrate_columns()
        rows = dict(db.session.execute(text('SELECT email, show_real_name FROM users')).all())
        assert not rows[anna.email]
        for email in DEMO_USER_EMAILS.values():
            assert rows[email]
