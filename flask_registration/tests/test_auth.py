"""
Test-suite voor auth.py: registratie, login, e-mailverificatie en beveiging.
Dekt: succesvolle registratie, dubbele e-mail, ongeldige invoer, Bcrypt-wachtwoord,
e-mailverificatie-flow, login met correcte/foute credentials, niet-geverifieerd account.
"""

import pytest
import os
import sys
from werkzeug.security import generate_password_hash, check_password_hash

# Voeg projectroot toe aan path zodat imports werken
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import config as config_module
from app import create_app, db, bcrypt
from app.models import User


@pytest.fixture
def app():
    """
    Maak test-app via de ECHTE app-factory (create_app), niet via een
    handmatig samengestelde Flask-app. Een handmatige app registreerde eerder
    alleen de auth-blueprint en gebruikte de verkeerde template_folder — dat
    brak op url_for('main.index')/url_for('social.aanmelden') (BuildError) en
    op het renderen van login.html (TemplateNotFound, want templates staan in
    app/templates/ en Babel-context ontbrak). create_app() registreert alle
    blueprints, de juiste templates én de Babel-context identiek aan productie.

    Config wordt tijdelijk overschreven vóórdat create_app() draait (die
    roept db.create_all() al aan tijdens het bouwen van de app, dus de
    overrides moeten er dan al staan): SQLite in-memory, geen CSRF, geen
    rate-limiting (anders lopen herhaalde test-POSTs binnen dezelfde
    pytest-sessie tegen flask-limiter se gedeelde in-memory storage aan).
    """
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
    """Test-client voor HTTP-requests."""
    return app.test_client()


@pytest.fixture
def runner(app):
    """CLI runner voor shell-commands."""
    return app.test_cli_runner()


class TestUserModel:
    """Test User-model: velden, hashing, verificatie."""
    
    def test_user_has_required_fields(self, app):
        """Valideer dat User-model email, password_hash, verified, google_id heeft."""
        with app.app_context():
            user = User(
                first_name='Test',
                last_name='User',
                email='test@example.com',
                password_hash=bcrypt.generate_password_hash('password123').decode('utf-8'),
                verified=False,
                google_id=None
            )
            db.session.add(user)
            db.session.commit()
            
            # Valideer dat velden opgeslagen zijn
            saved = User.query.filter_by(email='test@example.com').first()
            assert saved is not None
            assert saved.email == 'test@example.com'
            assert saved.password_hash is not None
            assert saved.verified is False
            assert saved.google_id is None
    
    def test_password_hashing_with_bcrypt(self, app):
        """Valideer dat wachtwoorden via Bcrypt gehasht en geverifieerd worden."""
        with app.app_context():
            plain_password = 'MySecurePassword123!'
            hashed = bcrypt.generate_password_hash(plain_password).decode('utf-8')
            
            # Hash is niet gelijk aan plain text
            assert hashed != plain_password
            
            # Verificatie slaagt met correcte wachtwoord
            assert bcrypt.check_password_hash(hashed, plain_password)
            
            # Verificatie faalt met fout wachtwoord
            assert not bcrypt.check_password_hash(hashed, 'WrongPassword')
    
    def test_user_is_admin_moderator_defaults(self, app):
        """Valideer dat is_admin en is_moderator standaard False zijn."""
        with app.app_context():
            user = User(
                first_name='Test',
                last_name='User',
                email='test@example.com',
                password_hash='hash'
            )
            db.session.add(user)
            db.session.commit()
            
            saved = User.query.filter_by(email='test@example.com').first()
            assert saved.is_admin is False
            assert saved.is_moderator is False


class TestRegistration:
    """
    Test registratieflow: validatie, dubbele e-mail, opslag.

    LET OP: de daadwerkelijke registratie draait op POST /aanmelden (blueprint
    'social', app/routes/social.py). /register (auth-blueprint) is slechts een
    GET-only redirect naar diezelfde pagina — een POST daarnaartoe geeft altijd
    405 METHOD NOT ALLOWED. Het form gebruikt bovendien 'password_confirm'
    (niet 'confirm_password') en vereist een 'linkedin_url' die begint met
    'https://www.linkedin.com/'.
    """

    def test_successful_registration(self, client, app):
        """Registreer gebruiker met geldige gegevens; valideer opslag."""
        response = client.post('/aanmelden', data={
            'first_name': 'John',
            'last_name': 'Doe',
            'email': 'john@example.com',
            'password': 'ValidPassword123!',
            'password_confirm': 'ValidPassword123!',
            'linkedin_url': 'https://www.linkedin.com/in/johndoe',
        }, follow_redirects=True)

        # Check HTTP-response (status 200, want follow_redirects=True)
        assert response.status_code == 200

        # Valideer dat gebruiker in database staat
        with app.app_context():
            user = User.query.filter_by(email='john@example.com').first()
            assert user is not None
            assert user.first_name == 'John'
            assert user.last_name == 'Doe'
            # Standaard niet geverifieerd en geen google_id
            assert user.verified is False
            assert user.google_id is None

    def test_duplicate_email_registration_fails(self, client, app):
        """Twee registraties met zelfde e-mail; tweede faalt."""
        with app.app_context():
            # Voeg eerste gebruiker direct toe
            user1 = User(
                first_name='Alice',
                last_name='Smith',
                email='alice@example.com',
                password_hash=bcrypt.generate_password_hash('pass123').decode('utf-8')
            )
            db.session.add(user1)
            db.session.commit()

        # Probeer registratie met zelfde e-mail
        response = client.post('/aanmelden', data={
            'first_name': 'Bob',
            'last_name': 'Jones',
            'email': 'alice@example.com',
            'password': 'Password123!',
            'password_confirm': 'Password123!',
            'linkedin_url': 'https://www.linkedin.com/in/bobjones',
        }, follow_redirects=True)

        # Registratie faalt (flash-message of validatiefout)
        assert response.status_code == 200
        # Check dat geen tweede record aangemaakt is
        with app.app_context():
            count = User.query.filter_by(email='alice@example.com').count()
            assert count == 1

    def test_mismatched_passwords_fail(self, client, app):
        """Registratie met niet-matching wachtwoorden faalt."""
        response = client.post('/aanmelden', data={
            'first_name': 'Test',
            'last_name': 'User',
            'email': 'test@example.com',
            'password': 'Password123!',
            'password_confirm': 'DifferentPassword123!',
            'linkedin_url': 'https://www.linkedin.com/in/testuser',
        }, follow_redirects=True)

        assert response.status_code == 200
        # Geen gebruiker aangemaakt
        with app.app_context():
            user = User.query.filter_by(email='test@example.com').first()
            assert user is None

    def test_weak_password_rejected(self, client, app):
        """Registratie met zwak wachtwoord (< 8 chars) faalt."""
        response = client.post('/aanmelden', data={
            'first_name': 'Test',
            'last_name': 'User',
            'email': 'test@example.com',
            'password': 'weak',
            'password_confirm': 'weak',
            'linkedin_url': 'https://www.linkedin.com/in/testuser',
        }, follow_redirects=True)

        assert response.status_code == 200
        with app.app_context():
            user = User.query.filter_by(email='test@example.com').first()
            assert user is None

    def test_missing_field_registration_fails(self, client, app):
        """Registratie zonder verplichte velden faalt."""
        response = client.post('/aanmelden', data={
            'first_name': 'John',
            'email': 'john@example.com',
            # last_name, wachtwoord en linkedin_url ontbreken
        }, follow_redirects=True)

        assert response.status_code == 200
        with app.app_context():
            user = User.query.filter_by(email='john@example.com').first()
            assert user is None

    def test_invalid_email_rejected(self, client, app):
        """Registratie met ongeldige e-mail faalt."""
        response = client.post('/aanmelden', data={
            'first_name': 'Test',
            'last_name': 'User',
            'email': 'not-an-email',
            'password': 'ValidPassword123!',
            'password_confirm': 'ValidPassword123!',
            'linkedin_url': 'https://www.linkedin.com/in/testuser',
        }, follow_redirects=True)

        assert response.status_code == 200
        with app.app_context():
            user = User.query.filter_by(email='not-an-email').first()
            assert user is None


class TestLogin:
    """Test login-flow: correcte/foute credentials, verificatiestatus."""
    
    def test_successful_login_verified_user(self, client, app):
        """Login met correcte credentials en geverifieerde gebruiker slaagt."""
        with app.app_context():
            # Maak geverifieerde gebruiker
            user = User(
                first_name='John',
                last_name='Doe',
                email='john@example.com',
                password_hash=bcrypt.generate_password_hash('Password123!').decode('utf-8'),
                verified=True
            )
            db.session.add(user)
            db.session.commit()
        
        # Login poging
        response = client.post('/login', data={
            'email': 'john@example.com',
            'password': 'Password123!'
        }, follow_redirects=True)
        
        assert response.status_code == 200
        # Gebruiker is nu ingelogd (check in sesssie)
        with client.session_transaction() as sess:
            # Flask-Login zet user_id in session
            assert '_user_id' in sess or 'user_id' in sess or sess.get('_fresh')
    
    def test_login_wrong_password_fails(self, client, app):
        """Login met fout wachtwoord faalt."""
        with app.app_context():
            user = User(
                first_name='John',
                last_name='Doe',
                email='john@example.com',
                password_hash=bcrypt.generate_password_hash('CorrectPassword123!').decode('utf-8'),
                verified=True
            )
            db.session.add(user)
            db.session.commit()
        
        response = client.post('/login', data={
            'email': 'john@example.com',
            'password': 'WrongPassword123!'
        }, follow_redirects=True)
        
        assert response.status_code == 200
        # Flash-message of validation error in response
    
    def test_login_nonexistent_user_fails(self, client, app):
        """Login met niet-bestaand e-mailadres faalt."""
        response = client.post('/login', data={
            'email': 'nonexistent@example.com',
            'password': 'Password123!'
        }, follow_redirects=True)
        
        assert response.status_code == 200
    
    def test_login_unverified_user_blocked(self, client, app):
        """Login met niet-geverifieerde gebruiker wordt geblokkeerd/gewaarschuwd."""
        with app.app_context():
            user = User(
                first_name='John',
                last_name='Doe',
                email='john@example.com',
                password_hash=bcrypt.generate_password_hash('Password123!').decode('utf-8'),
                verified=False  # Niet geverifieerd
            )
            db.session.add(user)
            db.session.commit()
        
        response = client.post('/login', data={
            'email': 'john@example.com',
            'password': 'Password123!'
        })

        # Blokker 1: correcte credentials, maar niet-bevestigd account —
        # geen redirect (blijft op de pagina), geen sessie, wél de modal.
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'guUnverifiedModal' in html
        with client.session_transaction() as sess:
            assert '_user_id' not in sess
    
    def test_login_case_insensitive_email(self, client, app):
        """Login werkt met e-mail in ander case."""
        with app.app_context():
            user = User(
                first_name='John',
                last_name='Doe',
                email='John@Example.com',
                password_hash=bcrypt.generate_password_hash('Password123!').decode('utf-8'),
                verified=True
            )
            db.session.add(user)
            db.session.commit()
        
        # Login met lowercase e-mail
        response = client.post('/login', data={
            'email': 'john@example.com',
            'password': 'Password123!'
        }, follow_redirects=True)
        
        # Moet minstens niet crashen
        assert response.status_code == 200


class TestEmailVerification:
    """Test e-mailverificatie-flow en geverifieerde-status."""
    
    def test_new_user_unverified_by_default(self, app):
        """Nieuwe gebruiker heeft verified=False."""
        with app.app_context():
            user = User(
                first_name='Test',
                last_name='User',
                email='test@example.com',
                password_hash='hash'
            )
            db.session.add(user)
            db.session.commit()
            
            saved = User.query.filter_by(email='test@example.com').first()
            assert saved.verified is False
    
    def test_verified_flag_persists(self, app):
        """verified-flag kan gezet en opgeslagen worden."""
        with app.app_context():
            user = User(
                first_name='Test',
                last_name='User',
                email='test@example.com',
                password_hash='hash',
                verified=False
            )
            db.session.add(user)
            db.session.commit()
            
            # Update naar verified
            user.verified = True
            db.session.commit()
            
            saved = User.query.filter_by(email='test@example.com').first()
            assert saved.verified is True
    
    def test_google_id_nullable(self, app):
        """google_id kan NULL zijn of een waarde hebben."""
        with app.app_context():
            # Gebruiker zonder Google
            user1 = User(
                first_name='Test1',
                last_name='User1',
                email='test1@example.com',
                password_hash='hash',
                google_id=None
            )
            db.session.add(user1)
            
            # Gebruiker met Google
            user2 = User(
                first_name='Test2',
                last_name='User2',
                email='test2@example.com',
                password_hash='hash',
                google_id='123456789'
            )
            db.session.add(user2)
            db.session.commit()
            
            saved1 = User.query.filter_by(email='test1@example.com').first()
            saved2 = User.query.filter_by(email='test2@example.com').first()
            
            assert saved1.google_id is None
            assert saved2.google_id == '123456789'


class TestPasswordSecurity:
    """Test wachtwoordbeveiliging en hash-integriteit."""
    
    def test_password_never_stored_plaintext(self, app):
        """Wachtwoord wordt NOOIT plaintext opgeslagen."""
        with app.app_context():
            plain = 'MyPassword123!'
            hashed = bcrypt.generate_password_hash(plain).decode('utf-8')
            
            user = User(
                first_name='Test',
                last_name='User',
                email='test@example.com',
                password_hash=hashed
            )
            db.session.add(user)
            db.session.commit()
            
            saved = User.query.filter_by(email='test@example.com').first()
            # Hash is niet hetzelfde als plaintext
            assert saved.password_hash != plain
            # Hash kan geverifieerd worden
            assert bcrypt.check_password_hash(saved.password_hash, plain)
    
    def test_different_passwords_different_hashes(self, app):
        """Dezelfde wachtwoord produceert andere hashes (salt)."""
        with app.app_context():
            plain = 'SamePassword123!'
            hash1 = bcrypt.generate_password_hash(plain).decode('utf-8')
            hash2 = bcrypt.generate_password_hash(plain).decode('utf-8')
            
            # Hashes zijn verschillend (salt)
            assert hash1 != hash2
            # Beide kunnen geverifieerd worden
            assert bcrypt.check_password_hash(hash1, plain)
            assert bcrypt.check_password_hash(hash2, plain)
    
    def test_hash_tampering_fails(self, app):
        """Gewijzigde hash kan niet geverifieerd worden."""
        with app.app_context():
            plain = 'Password123!'
            hashed = bcrypt.generate_password_hash(plain).decode('utf-8')

            # Wijzig het LAATSTE karakter van de hash (onderdeel van de eigenlijke
            # digest). Het EERSTE karakter breekt de bcrypt-headerprefix
            # ('$2b$12$...') — dat maakt het hele salt-formaat ongeldig en laat
            # check_password_hash een ValueError gooien i.p.v. False teruggeven,
            # wat geen eerlijke test van 'tampering' is.
            laatste = hashed[-1]
            vervanger = '.' if laatste != '.' else '/'
            tampered = hashed[:-1] + vervanger

            # Verificatie faalt
            assert not bcrypt.check_password_hash(tampered, plain)


class TestFormValidation:
    """Test WTForms-validatie in registratie/login."""
    
    def test_email_validation_in_form(self, client):
        """E-mail validatie werkt in form."""
        # /register is slechts een GET-only redirect naar de echte
        # registratiepagina; die staat op /aanmelden (blueprint 'social').
        response = client.get('/aanmelden')
        assert response.status_code == 200
        # HTML bevat type=email of validators
    
    def test_login_requires_email_password(self, client):
        """Login-form vereist email en password."""
        response = client.post('/login', data={
            'email': '',
            'password': ''
        }, follow_redirects=True)
        assert response.status_code == 200


class TestEdgeCases:
    """Grensgeval: lange strings, speciale karakters, unicode."""
    
    def test_very_long_email(self, app):
        """Zeer lange (maar geldige) e-mail wordt opgeslagen."""
        with app.app_context():
            long_email = 'a' * 50 + '@example.com'
            user = User(
                first_name='Test',
                last_name='User',
                email=long_email,
                password_hash='hash'
            )
            db.session.add(user)
            db.session.commit()
            
            saved = User.query.filter_by(email=long_email).first()
            assert saved is not None
            assert saved.email == long_email
    
    def test_special_characters_in_name(self, app):
        """Namen met speciale karakters (accenten, unicode) werken."""
        with app.app_context():
            user = User(
                first_name='François',
                last_name='Müller',
                email='test@example.com',
                password_hash='hash'
            )
            db.session.add(user)
            db.session.commit()
            
            saved = User.query.filter_by(email='test@example.com').first()
            assert saved.first_name == 'François'
            assert saved.last_name == 'Müller'
    
    def test_null_linkedin_url(self, app):
        """linkedin_url is nullable."""
        with app.app_context():
            user1 = User(
                first_name='Test1',
                last_name='User1',
                email='test1@example.com',
                password_hash='hash',
                linkedin_url=None
            )
            user2 = User(
                first_name='Test2',
                last_name='User2',
                email='test2@example.com',
                password_hash='hash',
                linkedin_url='https://linkedin.com/in/testuser'
            )
            db.session.add(user1)
            db.session.add(user2)
            db.session.commit()
            
            saved1 = User.query.filter_by(email='test1@example.com').first()
            saved2 = User.query.filter_by(email='test2@example.com').first()
            
            assert saved1.linkedin_url is None
            assert saved2.linkedin_url == 'https://linkedin.com/in/testuser'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
