"""
conftest.py — gedeelde fixtures voor de GUF auth-testsuite.

Vereisten (pip install):
    pytest>=8.0
    pytest-mock>=3.12
    Flask-Limiter gebruikt memory-storage in tests (geen Redis nodig).

Gebruik SQLite in-memory zodat de echte Neon-database onaangeroerd blijft.
De app-factory is dezelfde als productie; we overschrijven alleen de
DATABASE_URL en schakelen rate-limiting en e-mail uit.
"""
import os
import pytest
from itsdangerous import URLSafeTimedSerializer

# Stel testomgeving in vóór de import van de app
os.environ.setdefault("SECRET_KEY", "test-secret-do-not-use-in-prod")
os.environ.setdefault("DATABASE_URL", "")          # leeg → SQLite-pad hieronder
os.environ.setdefault("RESEND_API_KEY", "fake")
os.environ.setdefault("ANTHROPIC_API_KEY", "fake")
os.environ.setdefault("GOOGLE_CLIENT_ID", "fake-client-id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "fake-client-secret")


@pytest.fixture(scope="session")
def app():
    """Maak één Flask-app voor de hele testsessie, met SQLite in-memory."""
    # Overschrijf de database-URL naar SQLite zodat Neon niet geraakt wordt
    os.environ["DATABASE_URL"] = ""

    from config import Config

    class TestConfig(Config):
        TESTING = True
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        WTF_CSRF_ENABLED = False          # CSRF uitschakelen in tests
        RATELIMIT_ENABLED = False          # Flask-Limiter uitschakelen
        SECRET_KEY = "test-secret-do-not-use-in-prod"
        SERVER_NAME = "localhost"

    # Importeer create_app ná het overschrijven van de config
    from app import create_app, db as _db

    flask_app = create_app()
    flask_app.config.from_object(TestConfig)

    with flask_app.app_context():
        _db.create_all()
        yield flask_app
        _db.session.remove()
        _db.drop_all()


@pytest.fixture()
def client(app):
    """Geeft een Flask-testclient terug."""
    return app.test_client()


@pytest.fixture()
def db(app):
    """Geeft de SQLAlchemy-db terug en rolt elke test terug."""
    from app import db as _db
    yield _db
    _db.session.rollback()


@pytest.fixture()
def new_user(db):
    """
    Helperfixture: maak een niet-geverifieerde gebruiker aan en verwijder hem
    na de test.  Gebruik deze fixture als bouwsteen in individuele tests.
    """
    from app import bcrypt
    from app.models import User

    user = User(
        first_name="Test",
        last_name="Gebruiker",
        email="test@example.com",
        password_hash=bcrypt.generate_password_hash("Sterk1!ww").decode("utf-8"),
        linkedin_url="https://www.linkedin.com/in/testgebruiker",
        verified=False,
    )
    db.session.add(user)
    db.session.commit()
    yield user

    # Teardown — verwijder als de test de user nog niet verwijderd heeft
    existing = db.session.get(User, user.id)
    if existing:
        db.session.delete(existing)
        db.session.commit()


@pytest.fixture()
def verified_user(db):
    """Geverifieerde gebruiker zonder Google-koppeling."""
    from app import bcrypt
    from app.models import User

    user = User(
        first_name="Vera",
        last_name="Verified",
        email="vera@example.com",
        password_hash=bcrypt.generate_password_hash("Sterk1!ww").decode("utf-8"),
        linkedin_url="https://www.linkedin.com/in/veraverified",
        verified=True,
    )
    db.session.add(user)
    db.session.commit()
    yield user

    existing = db.session.get(User, user.id)
    if existing:
        db.session.delete(existing)
        db.session.commit()


def make_verify_token(email: str, secret_key: str = "test-secret-do-not-use-in-prod") -> str:
    """Genereer een geldig e-mailverificatietoken (zelfde logica als utils.py)."""
    s = URLSafeTimedSerializer(secret_key)
    return s.dumps(email, salt="email-verify")


def make_reset_token(email: str, secret_key: str = "test-secret-do-not-use-in-prod") -> str:
    """Genereer een geldig wachtwoordresettoken."""
    s = URLSafeTimedSerializer(secret_key)
    return s.dumps(email, salt="password-reset")
