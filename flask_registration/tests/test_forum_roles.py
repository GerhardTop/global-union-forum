"""
Forum fase A: rollen (reader/contributor/voter/moderator/senior_moderator,
admin erboven) en de spelregels (gedragsregels en kwaliteitsrichtlijnen).

Dekt: de permissie-helpers per rol, set_role/accept_rules, de eenmalige
backfill in _migrate_roles(), dat bestaande bijdragers en moderators niets
merken, dat een nieuwe registratie pas na akkoord kan bijdragen (via
/aanmelden, /kies-gebruikersnaam en het vangnet op /spelregels), het
handmatig zetten van een rol in het adminscherm, en dat de betrokken
pagina's in NL en EN renderen.

Gedeelde conftest-app (CSRF uit): _req() wist vóór elk request de in `g`
gecachte current_user en Babel-locale (zie de Babel/g-test-gotcha).
"""
import uuid
from datetime import datetime

import pytest
from flask import g
from flask_babel import refresh
from sqlalchemy import text

from app import bcrypt
from app.models import ROLES, AnonymousUser, Post, Thread, User


# ── Helpers ──────────────────────────────────────────────────────────────

def _user(role=None, accepted=True, is_admin=False, is_moderator=None, **kw):
    """In-memory User (niet opgeslagen), voor de pure helper-tests."""
    u = User(username='x', email='x@example.com', password_hash='x', is_admin=is_admin,
             is_moderator=bool(is_moderator), **kw)
    u.role = role
    u.rules_accepted_at = datetime.utcnow() if accepted else None
    return u


@pytest.fixture
def make_user(db):
    created = []

    def _make(role='contributor', accepted=True, is_admin=False, username=None, password='Sterk1!ww'):
        name = username or f'u_{uuid.uuid4().hex[:8]}'
        u = User(username=name, first_name=name.capitalize(), last_name='Test',
                 email=f'{name}@example.com',
                 password_hash=bcrypt.generate_password_hash(password).decode('utf-8'),
                 verified=True, is_admin=is_admin, is_moderator=is_admin)
        if role:
            u.set_role(role)
            if is_admin:
                u.is_moderator = True
        if accepted:
            u.rules_accepted_at = datetime.utcnow()
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
    t = Thread(eyebrow_nl='Test', eyebrow_en='Test', title_prefix_nl='Rollen', title_prefix_en='Roles')
    db.session.add(t)
    db.session.flush()
    op = Post(thread_id=t.id, author_name='Op', author_username='op_user', body='Opening', is_op=True)
    db.session.add(op)
    db.session.commit()
    yield t
    for p in Post.query.filter_by(thread_id=t.id):
        db.session.delete(p)
    db.session.delete(t)
    db.session.commit()


def _client_for(app, user=None):
    client = app.test_client()
    if user is not None:
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user.id)
            sess['_fresh'] = True
    return client


def _req(client, method, path, **kw):
    g.pop('_login_user', None)
    refresh()
    return client.open(path, method=method, **kw)


def _get(client, path, lang='nl'):
    _req(client, 'GET', f'/lang/{lang}')
    resp = _req(client, 'GET', path)
    return resp


# ── 1. Permissie-helpers per rol ─────────────────────────────────────────

class TestPermissionHelpers:
    #                         contribute vote   moderate manage_rules
    CASES = {
        'reader (anoniem)':   (False, False, False, False),
        'geen akkoord':       (False, False, False, False),
        'contributor':        (True,  False, False, False),
        'voter':              (True,  True,  False, False),
        'moderator':          (True,  True,  True,  False),
        'senior_moderator':   (True,  True,  True,  True),
        'admin':              (True,  True,  True,  True),
    }

    @staticmethod
    def _subject(name):
        if name == 'reader (anoniem)':
            return AnonymousUser()
        if name == 'geen akkoord':
            return _user(role=None, accepted=False)
        if name == 'admin':
            return _user(role='moderator', is_admin=True, is_moderator=True)
        return _user(role=name, is_moderator=name in ('moderator', 'senior_moderator'))

    @pytest.mark.parametrize('name', list(CASES))
    def test_helpers_per_role(self, name):
        u = self._subject(name)
        assert (u.can_contribute(), u.can_vote(), u.can_moderate(), u.can_manage_rules()) == self.CASES[name]

    def test_role_without_acceptance_cannot_contribute(self):
        u = _user(role='contributor', accepted=False)
        assert not u.can_contribute() and not u.can_vote()

    def test_admin_contributes_even_without_role_or_acceptance(self):
        u = _user(role=None, accepted=False, is_admin=True)
        assert u.can_contribute() and u.can_moderate() and u.can_manage_rules()

    def test_legacy_is_moderator_flag_still_moderates(self):
        assert _user(role='contributor', is_moderator=True).can_moderate()

    def test_set_role_keeps_is_moderator_in_sync(self):
        u = _user(role='contributor')
        u.set_role('senior_moderator')
        assert u.is_moderator is True
        u.set_role('voter')
        assert u.is_moderator is False and not u.can_moderate()
        with pytest.raises(ValueError):
            u.set_role('admin')

    def test_accept_rules_makes_contributor_and_keeps_higher_role(self):
        new = _user(role=None, accepted=False)
        new.accept_rules()
        assert new.role == 'contributor' and new.rules_accepted_at is not None and new.can_contribute()
        mod = _user(role='moderator', accepted=False, is_moderator=True)
        mod.accept_rules()
        assert mod.role == 'moderator'

    def test_ladder_order(self):
        assert ROLES == ('contributor', 'voter', 'moderator', 'senior_moderator')


# ── 2. Migratie en backfill ──────────────────────────────────────────────

class TestMigrationBackfill:
    def test_backfill_runs_once_when_columns_are_added(self, app, db):
        # Via de volledige _migrate_columns() (zoals bij het opstarten), zodat
        # ook de volgorde ten opzichte van de andere migraties getest wordt:
        # een ORM-query op User vóór _migrate_roles zou hier crashen.
        uids = [
            db.session.execute(text(
                "INSERT INTO users (username, email, password_hash, is_admin, is_moderator, verified) "
                f"VALUES ('mig_{n}', 'mig_{n}@example.com', 'x', 0, {mod}, 1)"
            )).lastrowid
            for n, mod in (('mod', 1), ('gewoon', 0))
        ]
        db.session.commit()
        db.session.close()
        # Oud schema nabootsen: de twee kolommen bestonden nog niet.
        with db.engine.connect() as conn:
            conn.execute(text('ALTER TABLE users DROP COLUMN role'))
            conn.execute(text('ALTER TABLE users DROP COLUMN rules_accepted_at'))
            conn.commit()
        # SQLite-eigenaardigheid: een andere verbinding in de pool kent na een
        # DROP COLUMN het oude schema nog. Verse verbindingen voor de migratie.
        db.engine.dispose()
        from app import _migrate_columns
        try:
            _migrate_columns()
            rows = {r[0]: r for r in db.session.execute(text(
                'SELECT id, role, rules_accepted_at FROM users WHERE id IN (:a, :b)'), {'a': uids[0], 'b': uids[1]})}
            assert rows[uids[0]][1] == 'moderator' and rows[uids[0]][2] is not None
            assert rows[uids[1]][1] == 'contributor' and rows[uids[1]][2] is not None

            # Een nieuwe registratie daarna (role NULL) wordt bij een volgende
            # start NIET alsnog goedgekeurd.
            db.session.execute(text("UPDATE users SET role = NULL, rules_accepted_at = NULL WHERE id = :b"),
                               {'b': uids[1]})
            db.session.commit()
            _migrate_columns()
            row = db.session.execute(text('SELECT role, rules_accepted_at FROM users WHERE id = :b'),
                                     {'b': uids[1]}).one()
            assert row == (None, None)
        finally:
            db.session.execute(text('DELETE FROM users WHERE id IN (:a, :b)'), {'a': uids[0], 'b': uids[1]})
            db.session.commit()


# ── 3. Bestaande gebruikers merken niets ─────────────────────────────────

class TestExistingUsersUnchanged:
    def test_existing_contributor_can_post_and_reply(self, app, make_user, thread):
        anna = make_user('contributor')
        client = _client_for(app, anna)
        op = Post.query.filter_by(thread_id=thread.id, is_op=True).one()
        resp = _req(client, 'POST', f'/forum/{thread.id}', data={'body': 'hoofdniveau'})
        assert resp.status_code == 302
        resp = _req(client, 'POST', f'/forum/{thread.id}', data={'body': 'antwoord', 'parent_id': op.id})
        assert resp.status_code == 302
        assert Post.query.filter_by(thread_id=thread.id, user_id=anna.id).count() == 2
        resp = _req(client, 'POST', '/forum/nieuw', data={'title': 'Draad van Anna', 'body': 'Eerste bericht'})
        assert resp.status_code == 302
        new_thread = Thread.query.filter_by(title_prefix_nl='Draad van Anna').one()
        for p in Post.query.filter_by(thread_id=new_thread.id):
            app.extensions['sqlalchemy'].session.delete(p)
        app.extensions['sqlalchemy'].session.delete(new_thread)
        app.extensions['sqlalchemy'].session.commit()

    @pytest.mark.parametrize('role', ['moderator', 'senior_moderator'])
    def test_moderator_can_still_moderate(self, app, db, make_user, thread, role):
        mod = make_user(role)
        client = _client_for(app, mod)
        assert _req(client, 'POST', f'/forum/{thread.id}/close').status_code == 302
        db.session.expire_all()
        assert db.session.get(Thread, thread.id).is_closed is True
        assert _req(client, 'POST', f'/forum/{thread.id}/reopen').status_code == 302
        assert _req(client, 'GET', '/moderator').status_code == 200

    def test_contributor_cannot_moderate(self, app, make_user, thread):
        client = _client_for(app, make_user('contributor'))
        assert _req(client, 'POST', f'/forum/{thread.id}/close').status_code == 403
        assert _req(client, 'GET', '/moderator').status_code == 403

    def test_admin_unchanged(self, app, db, make_user, thread):
        admin = make_user('moderator', is_admin=True)
        client = _client_for(app, admin)
        assert _req(client, 'GET', '/admin').status_code == 200
        assert _req(client, 'POST', f'/forum/{thread.id}', data={'body': 'admin reageert'}).status_code == 302
        assert Post.query.filter_by(body='admin reageert').count() == 1
        assert _req(client, 'POST', f'/forum/{thread.id}/close').status_code == 302


# ── 4. Nieuwe registratie: eerst de spelregels ───────────────────────────

class TestNewUsersMustAcceptRules:
    def test_unaccepted_user_cannot_contribute_until_accepting(self, app, db, make_user, thread):
        new = make_user(role=None, accepted=False)
        client = _client_for(app, new)

        resp = _req(client, 'POST', f'/forum/{thread.id}', data={'body': 'te vroeg'})
        assert resp.status_code == 302 and '/spelregels' in resp.headers['Location']
        assert Post.query.filter_by(body='te vroeg').count() == 0
        resp = _req(client, 'GET', '/forum/nieuw')
        assert resp.status_code == 302 and '/spelregels' in resp.headers['Location']

        page = _get(client, f'/forum/{thread.id}').get_data(as_text=True)
        assert 'id="composer"' not in page and 'Accepteer de gedragsregels om mee te doen</a>.' in page
        assert 'class="gu-post__action gu-post__reply-btn"' not in page

        rules = _get(client, '/spelregels').get_data(as_text=True)
        assert 'Ik ga akkoord met de gedragsregels' in rules
        resp = _req(client, 'POST', '/spelregels/accepteren', data={'next': f'/forum/{thread.id}'})
        assert resp.status_code == 302 and resp.headers['Location'] == f'/forum/{thread.id}'
        db.session.expire_all()
        fresh = db.session.get(User, new.id)
        assert fresh.role == 'contributor' and fresh.rules_accepted_at is not None

        resp = _req(client, 'POST', f'/forum/{thread.id}', data={'body': 'nu wel'})
        assert resp.status_code == 302 and Post.query.filter_by(body='nu wel').count() == 1

    def _register(self, client, accept):
        data = {'username': f'reg_{uuid.uuid4().hex[:6]}', 'email': f'{uuid.uuid4().hex[:8]}@example.com',
                'password': 'ValidPassword123!', 'password_confirm': 'ValidPassword123!'}
        if accept:
            data['accept_rules'] = '1'
        return data, _req(client, 'POST', '/aanmelden', data=data)

    def test_registration_without_acceptance_is_refused(self, app, db):
        client = _client_for(app)
        data, resp = self._register(client, accept=False)
        assert resp.status_code == 303
        assert User.query.filter_by(email=data['email']).count() == 0
        page = _req(client, 'GET', '/aanmelden').get_data(as_text=True)
        assert 'Ga akkoord met de gedragsregels om verder te gaan.' in page
        assert "getElementById('rulesField').scrollIntoView" in page
        # Ingevulde gegevens blijven staan (PRG), het vinkje staat nog uit.
        assert data['username'] in page

    def test_registration_with_acceptance_makes_contributor(self, app, db):
        data, resp = self._register(_client_for(app), accept=True)
        user = User.query.filter_by(email=data['email']).one()
        try:
            assert user.role == 'contributor' and user.rules_accepted_at is not None
            assert user.can_contribute()
        finally:
            db.session.delete(user)
            db.session.commit()

    def test_google_user_accepts_on_username_step(self, app, db, make_user):
        google_user = make_user(role=None, accepted=False, username='user_abc123')
        client = _client_for(app, google_user)
        page = _get(client, '/kies-gebruikersnaam').get_data(as_text=True)
        assert 'name="accept_rules"' in page

        _req(client, 'POST', '/kies-gebruikersnaam', data={'username': 'gekozen_naam'})
        db.session.expire_all()
        assert db.session.get(User, google_user.id).username == 'user_abc123'

        _req(client, 'POST', '/kies-gebruikersnaam', data={'username': 'gekozen_naam', 'accept_rules': '1'})
        db.session.expire_all()
        saved = db.session.get(User, google_user.id)
        assert saved.username == 'gekozen_naam' and saved.can_contribute()

    def test_grandfathered_placeholder_user_sees_no_checkbox(self, app, make_user):
        old = make_user('contributor', username='user_old999')
        page = _get(_client_for(app, old), '/kies-gebruikersnaam').get_data(as_text=True)
        assert 'name="accept_rules"' not in page


# ── 5. Admin zet de rol handmatig ────────────────────────────────────────

class TestAdminSetsRole:
    @pytest.mark.parametrize('role,expect_mod', [
        ('contributor', False), ('voter', False), ('moderator', True), ('senior_moderator', True),
    ])
    def test_admin_sets_each_role(self, app, db, make_user, role, expect_mod):
        admin = make_user('moderator', is_admin=True)
        target = make_user('contributor')
        resp = _req(_client_for(app, admin), 'POST', f'/admin/gebruiker/{target.id}/rol', data={'role': role})
        assert resp.get_json() == {'ok': True, 'role': role}
        db.session.expire_all()
        t = db.session.get(User, target.id)
        assert t.role == role and t.is_moderator is expect_mod and t.can_moderate() is expect_mod

    def test_legacy_value_and_invalid_role(self, app, db, make_user):
        admin = make_user('moderator', is_admin=True)
        target = make_user('moderator')
        client = _client_for(app, admin)
        _req(client, 'POST', f'/admin/gebruiker/{target.id}/rol', data={'role': 'gebruiker'})
        db.session.expire_all()
        assert db.session.get(User, target.id).role == 'contributor'
        assert _req(client, 'POST', f'/admin/gebruiker/{target.id}/rol', data={'role': 'koning'}).status_code == 400

    def test_non_admin_cannot_set_roles(self, app, make_user):
        senior = make_user('senior_moderator')
        target = make_user('contributor')
        resp = _req(_client_for(app, senior), 'POST', f'/admin/gebruiker/{target.id}/rol', data={'role': 'voter'})
        assert resp.status_code == 403


# ── 6. Pagina's renderen in NL en EN ─────────────────────────────────────

class TestPagesRender:
    @pytest.mark.parametrize('lang,expect', [('nl', 'Over het idee, niet de persoon'),
                                             ('en', 'About the idea, not the person')])
    def test_spelregels_public(self, app, lang, expect):
        html = _get(_client_for(app), '/spelregels', lang).get_data(as_text=True)
        for anchor in ('gedrag-1', 'gedrag-2', 'gedrag-3', 'kwaliteit-1', 'kwaliteit-2'):
            assert f'id="{anchor}"' in html
        assert '>R1<' not in html and expect in html

    @pytest.mark.parametrize('lang,label', [('nl', 'Ik ga akkoord met de gedragsregels'),
                                            ('en', 'I accept the conduct rules')])
    def test_registration_shows_checkbox(self, app, lang, label):
        html = _get(_client_for(app), '/aanmelden', lang).get_data(as_text=True)
        assert 'name="accept_rules"' in html and label in html

    @pytest.mark.parametrize('lang', ['nl', 'en'])
    def test_thread_and_admin_render(self, app, make_user, thread, lang):
        admin = make_user('moderator', is_admin=True)
        make_user(role=None, accepted=False)
        client = _client_for(app, admin)
        assert _get(client, f'/forum/{thread.id}', lang).status_code == 200
        html = _get(client, '/admin', lang).get_data(as_text=True)
        label = 'Senior moderator'
        assert label in html
        assert ('Nog geen akkoord op de gedragsregels' if lang == 'nl' else 'No conduct rules acceptance yet') in html
        assert ('Gedragsregels nog niet geaccepteerd' if lang == 'nl' else 'Conduct rules not accepted yet') in html
