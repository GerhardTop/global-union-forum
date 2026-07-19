"""
Test-suite voor het pseudoniem-model (fase 1): username-validatie,
registratieflow met username, /kies-gebruikersnaam, Google-OAuth-placeholder,
en de template-fallbacks die crashes bij een lege naam moeten voorkomen.
"""

import pytest
import os
import sys

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import config as config_module
from app import create_app, db, bcrypt
from app.models import User, Thread, Post
from app.utils import is_username_valid_format, is_username_blacklisted, is_username_available


@pytest.fixture
def app():
    orig_db_uri = config_module.Config.SQLALCHEMY_DATABASE_URI
    config_module.Config.SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    config_module.Config.TESTING = True
    config_module.Config.WTF_CSRF_ENABLED = False
    config_module.Config.RATELIMIT_ENABLED = False

    try:
        flask_app = create_app()
        with flask_app.app_context():
            yield flask_app
            db.session.remove()
            db.drop_all()
    finally:
        config_module.Config.SQLALCHEMY_DATABASE_URI = orig_db_uri


@pytest.fixture
def client(app):
    return app.test_client()


class TestUsernameValidationHelpers:
    """Unit tests voor de herbruikbare validatiefuncties in app/utils.py."""

    def test_valid_formats_accepted(self, app):
        with app.app_context():
            assert is_username_valid_format('abc')
            assert is_username_valid_format('sara_dv')
            assert is_username_valid_format('sara-dv-2')
            assert is_username_valid_format('a' * 20)

    def test_too_short_rejected(self, app):
        with app.app_context():
            assert not is_username_valid_format('ab')
            assert not is_username_valid_format('')

    def test_too_long_rejected(self, app):
        with app.app_context():
            assert not is_username_valid_format('a' * 21)

    def test_invalid_characters_rejected(self, app):
        with app.app_context():
            assert not is_username_valid_format('sara dv')
            assert not is_username_valid_format('sara@dv')
            assert not is_username_valid_format('sara.dv')
            assert not is_username_valid_format('sara!')

    @pytest.mark.parametrize("name", [
        "admin", "Admin", "ADMIN", "administrator", "moderator",
        "Moderator", "beheerder", "mod", "MOD",
    ])
    def test_blacklist_case_insensitive(self, app, name):
        with app.app_context():
            assert is_username_blacklisted(name)

    def test_blacklist_no_substring_match(self, app):
        """Alleen exacte match — 'adminfacts' en 'mymod' zijn geen treffers."""
        with app.app_context():
            assert not is_username_blacklisted('adminfacts')
            assert not is_username_blacklisted('mymod')
            assert not is_username_blacklisted('superadmin')

    def test_availability_case_insensitive_collision(self, app):
        with app.app_context():
            user = User(username='Sara_DV', first_name='Sara', last_name='DV',
                        email='sara@example.com', password_hash='hash', verified=True)
            db.session.add(user)
            db.session.commit()

            assert not is_username_available('sara_dv')
            assert not is_username_available('SARA_DV')
            assert is_username_available('sara_dv', exclude_user_id=user.id)
            assert is_username_available('someone_else')


class TestRegistrationUsername:
    """Registratieflow (/aanmelden) met username: happy path + elke foutcategorie."""

    def test_registration_requires_username(self, client, app):
        response = client.post('/aanmelden', data={
            'email': 'noname@example.com',
            'password': 'ValidPassword123!',
            'password_confirm': 'ValidPassword123!',
            'linkedin_url': 'https://www.linkedin.com/in/noname',
        }, follow_redirects=True)
        assert response.status_code == 200
        with app.app_context():
            assert User.query.filter_by(email='noname@example.com').first() is None

    def test_registration_without_first_last_name_succeeds(self, client, app):
        """Kernpunt van fase 1: voornaam/achternaam zijn nu echt optioneel."""
        response = client.post('/aanmelden', data={
            'username': 'pseudonym1',
            'email': 'pseudo@example.com',
            'password': 'ValidPassword123!',
            'password_confirm': 'ValidPassword123!',
            'linkedin_url': 'https://www.linkedin.com/in/pseudo',
        }, follow_redirects=True)
        assert response.status_code == 200
        with app.app_context():
            user = User.query.filter_by(email='pseudo@example.com').first()
            assert user is not None
            assert user.username == 'pseudonym1'
            assert user.first_name is None
            assert user.last_name is None

    def test_registration_blacklisted_username_rejected(self, client, app):
        response = client.post('/aanmelden', data={
            'username': 'admin',
            'email': 'wannabe@example.com',
            'password': 'ValidPassword123!',
            'password_confirm': 'ValidPassword123!',
            'linkedin_url': 'https://www.linkedin.com/in/wannabe',
        }, follow_redirects=True)
        assert response.status_code == 200
        with app.app_context():
            assert User.query.filter_by(email='wannabe@example.com').first() is None

    def test_registration_duplicate_username_case_insensitive_rejected(self, client, app):
        with app.app_context():
            existing = User(username='taken_name', first_name='A', last_name='B',
                            email='first@example.com', password_hash='hash', verified=True)
            db.session.add(existing)
            db.session.commit()

        response = client.post('/aanmelden', data={
            'username': 'Taken_Name',
            'email': 'second@example.com',
            'password': 'ValidPassword123!',
            'password_confirm': 'ValidPassword123!',
            'linkedin_url': 'https://www.linkedin.com/in/second',
        }, follow_redirects=True)
        assert response.status_code == 200
        with app.app_context():
            assert User.query.filter_by(email='second@example.com').first() is None

    def test_registration_first_name_without_last_name_rejected(self, client, app):
        """Voornaam/achternaam-koppel: één zonder de ander is fout."""
        response = client.post('/aanmelden', data={
            'username': 'halfname1',
            'first_name': 'Sara',
            'email': 'halfname@example.com',
            'password': 'ValidPassword123!',
            'password_confirm': 'ValidPassword123!',
            'linkedin_url': 'https://www.linkedin.com/in/halfname',
        }, follow_redirects=True)
        assert response.status_code == 200
        with app.app_context():
            assert User.query.filter_by(email='halfname@example.com').first() is None

    def test_username_check_endpoint_all_outcomes(self, client, app):
        with app.app_context():
            existing = User(username='existing_user', first_name='A', last_name='B',
                            email='existing@example.com', password_hash='hash', verified=True)
            db.session.add(existing)
            db.session.commit()

        r_format = client.post('/aanmelden/username-check', data={'username': 'ab'})
        assert r_format.get_json() == {'ok': False, 'reason': 'format'}

        r_blacklist = client.post('/aanmelden/username-check', data={'username': 'moderator'})
        assert r_blacklist.get_json() == {'ok': False, 'reason': 'blacklisted'}

        r_taken = client.post('/aanmelden/username-check', data={'username': 'Existing_User'})
        assert r_taken.get_json() == {'ok': False, 'reason': 'taken'}

        r_ok = client.post('/aanmelden/username-check', data={'username': 'brand_new_name'})
        assert r_ok.get_json() == {'ok': True}


class TestKiesGebruikersnaam:
    """/kies-gebruikersnaam: verplichte flow voor placeholder-usernames."""

    def _login(self, client, email, password):
        return client.post('/login', data={'email': email, 'password': password},
                           follow_redirects=True)

    def test_placeholder_user_redirected_to_kies_gebruikersnaam(self, client, app):
        with app.app_context():
            user = User(username='user_999', first_name=None, last_name=None,
                        email='placeholder@example.com',
                        password_hash=bcrypt.generate_password_hash('Password123!').decode('utf-8'),
                        verified=True)
            db.session.add(user)
            db.session.commit()

        self._login(client, 'placeholder@example.com', 'Password123!')
        response = client.get('/', follow_redirects=False)
        assert response.status_code == 302
        assert '/kies-gebruikersnaam' in response.headers['Location']

    def test_non_placeholder_user_not_redirected(self, client, app):
        with app.app_context():
            user = User(username='real_name', first_name='Real', last_name='Name',
                        email='real@example.com',
                        password_hash=bcrypt.generate_password_hash('Password123!').decode('utf-8'),
                        verified=True)
            db.session.add(user)
            db.session.commit()

        self._login(client, 'real@example.com', 'Password123!')
        response = client.get('/', follow_redirects=False)
        assert response.status_code == 200

    def test_choosing_username_clears_placeholder_and_unblocks(self, client, app):
        with app.app_context():
            user = User(username='user_123', first_name=None, last_name=None,
                        email='chooser@example.com',
                        password_hash=bcrypt.generate_password_hash('Password123!').decode('utf-8'),
                        verified=True)
            db.session.add(user)
            db.session.commit()

        self._login(client, 'chooser@example.com', 'Password123!')
        response = client.post('/kies-gebruikersnaam', data={'username': 'chosen_name'},
                               follow_redirects=True)
        assert response.status_code == 200

        with app.app_context():
            saved = User.query.filter_by(email='chooser@example.com').first()
            assert saved.username == 'chosen_name'

        # Nu geen redirect meer naar kies-gebruikersnaam.
        response2 = client.get('/', follow_redirects=False)
        assert response2.status_code == 200

    def test_kies_gebruikersnaam_rejects_blacklisted(self, client, app):
        with app.app_context():
            user = User(username='user_456', first_name=None, last_name=None,
                        email='blacklisttest@example.com',
                        password_hash=bcrypt.generate_password_hash('Password123!').decode('utf-8'),
                        verified=True)
            db.session.add(user)
            db.session.commit()

        self._login(client, 'blacklisttest@example.com', 'Password123!')
        client.post('/kies-gebruikersnaam', data={'username': 'admin'}, follow_redirects=True)

        with app.app_context():
            saved = User.query.filter_by(email='blacklisttest@example.com').first()
            assert saved.username == 'user_456'  # ongewijzigd


class TestTemplateFallbacks:
    """Crash-preventie: pagina's met een gebruiker zonder first_name/last_name."""

    def _login(self, client, email, password):
        return client.post('/login', data={'email': email, 'password': password},
                           follow_redirects=True)

    def test_header_avatar_no_crash_without_name(self, client, app):
        with app.app_context():
            user = User(username='noname_user', first_name=None, last_name=None,
                        email='noheader@example.com',
                        password_hash=bcrypt.generate_password_hash('Password123!').decode('utf-8'),
                        verified=True)
            db.session.add(user)
            db.session.commit()

        self._login(client, 'noheader@example.com', 'Password123!')
        response = client.get('/')
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert '@noname_user' in html or 'NO' in html  # initialen uit username

    def test_admin_dashboard_no_crash_with_nameless_user(self, client, app):
        with app.app_context():
            admin = User(username='admin_acct', first_name='Admin', last_name='Account',
                        email='admin@example.com',
                        password_hash=bcrypt.generate_password_hash('Password123!').decode('utf-8'),
                        verified=True, is_admin=True)
            nameless = User(username='ghost_user', first_name=None, last_name=None,
                            email='ghost@example.com',
                            password_hash=bcrypt.generate_password_hash('Password123!').decode('utf-8'),
                            verified=True)
            db.session.add_all([admin, nameless])
            db.session.commit()

        self._login(client, 'admin@example.com', 'Password123!')
        response = client.get('/admin')
        assert response.status_code == 200
        assert '@ghost_user' in response.get_data(as_text=True)


class TestGoogleOAuthPlaceholder:
    """Placeholder-schema voor nieuwe Google-accounts (geen live OAuth-call nodig)."""

    def test_new_user_via_backfill_style_placeholder_is_valid_format(self, app):
        """De user_<id>/user_<hex>-placeholders moeten zelf door de eigen
        formaatvalidatie komen (3-20 tekens, toegestane charset)."""
        with app.app_context():
            assert is_username_valid_format('user_7')
            assert is_username_valid_format('user_a3f9c1')
            assert not is_username_blacklisted('user_7')
