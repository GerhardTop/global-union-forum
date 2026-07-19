"""
conftest.py — gedeelde fixtures voor de GUF auth-testsuite.

Vereisten (pip install):
    pytest>=8.0
    pytest-mock>=3.12
    Flask-Limiter gebruikt memory-storage in tests (geen Redis nodig).

Gebruikt een losse SQLite-bestandsdatabase per testsessie, zodat de echte
(lokale of Neon-)database onaangeroerd blijft.

BELANGRIJK — waarom een bestand i.p.v. ':memory:' of een naderhand
overschreven config: create_app() doet zelf al app.config.from_object(Config)
+ db.init_app(app) + db.create_all()/_migrate_columns()/_ensure_admin_user()/
_seed_forum() binnen zijn eigen functie-body, vóórdat een latere
flask_app.config.from_object(TestConfig)-override kan aangrijpen. Flask-
SQLAlchemy cachet de Engine bij dat eerste gebruik, dus een override ns
create_app() verandert de config-dict wel, maar niet de al-gebonden
database-verbinding — create_app() migreert/seedt dan alsnog tegen de
echte, in .env geconfigureerde database (lokaal: MySQL). DATABASE_URL moet
daarom vóór de allereerste import van config.py al naar een sqlite-pad
wijzen, zodat Config.SQLALCHEMY_DATABASE_URI dat direct oppikt.
"""
import os
import tempfile
import pytest
from itsdangerous import URLSafeTimedSerializer

_TEST_DB_PATH = tempfile.mktemp(suffix=".sqlite3", prefix="guf-test-")

# Stel testomgeving in vóór de import van de app — DATABASE_URL wijst al
# hier naar het sqlite-testbestand (leeg zou terugvallen op de MySQL-branch
# in config._build_db_url()).
os.environ.setdefault("SECRET_KEY", "test-secret-do-not-use-in-prod")
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH}"
os.environ.setdefault("RESEND_API_KEY", "fake")
os.environ.setdefault("ANTHROPIC_API_KEY", "fake")
os.environ.setdefault("GOOGLE_CLIENT_ID", "fake-client-id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "fake-client-secret")


@pytest.fixture(scope="session")
def app():
    """Maak één Flask-app voor de hele testsessie, met een losse SQLite-file."""
    from config import Config

    class TestConfig(Config):
        TESTING = True
        WTF_CSRF_ENABLED = False          # CSRF uitschakelen in tests
        RATELIMIT_ENABLED = False          # Flask-Limiter uitschakelen
        SECRET_KEY = "test-secret-do-not-use-in-prod"
        SERVER_NAME = "localhost"

    flask_app = create_app_for_tests(TestConfig)

    with flask_app.app_context():
        yield flask_app
        from app import db as _db
        _db.session.remove()
        _db.drop_all()

    # Opruimen: het sqlite-bestand weggooien na afloop van de testsessie.
    if os.path.exists(_TEST_DB_PATH):
        os.remove(_TEST_DB_PATH)


def create_app_for_tests(test_config_cls):
    """
    create_app() zelf doet al db.create_all()/_migrate_columns()/
    _ensure_admin_user()/_seed_forum() vóórdat wij enige kans hebben iets te
    overschrijven — met DATABASE_URL al goedgezet (zie boven) gebeurt dat nu
    correct tegen het sqlite-testbestand, dus we hoeven hier alleen nog de
    overige TestConfig-vlaggen (CSRF, rate-limiting) na te zetten.
    """
    from app import create_app
    flask_app = create_app()
    flask_app.config.from_object(test_config_cls)
    return flask_app


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
        username="test_gebruiker",
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
        username="vera_verified",
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
