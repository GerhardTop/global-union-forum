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

# One database per process: with pytest-xdist every worker (PYTEST_XDIST_WORKER,
# gw0, gw1, ...) imports this file itself and gets its own file.
_WORKER = os.environ.get("PYTEST_XDIST_WORKER", "main")
_TEST_DB_PATH = tempfile.mktemp(suffix=".sqlite3", prefix=f"guf-test-{_WORKER}-")

# Stel testomgeving in vóór de import van de app — DATABASE_URL wijst al
# hier naar het sqlite-testbestand (leeg zou terugvallen op de MySQL-branch
# in config._build_db_url()).
os.environ.setdefault("SECRET_KEY", "test-secret-do-not-use-in-prod")
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH}"
os.environ.setdefault("RESEND_API_KEY", "fake")
os.environ.setdefault("ANTHROPIC_API_KEY", "fake")
os.environ.setdefault("GOOGLE_CLIENT_ID", "fake-client-id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "fake-client-secret")


@pytest.fixture(autouse=True)
def _no_background_translations(monkeypatch):
    """Multilingual phase 2a: the suite runs with ANTHROPIC_API_KEY=fake, so
    without this every new issue or proposal would start a background thread
    that calls the real AI, and the tick's safety net would too. Off for every
    test; tests/test_content_jobs.py runs the jobs itself with a fake client."""
    from app import content_jobs
    monkeypatch.setattr(content_jobs, '_start', lambda app, keys: None)
    monkeypatch.setattr(content_jobs, 'ai_available', lambda app=None: False)


@pytest.fixture(autouse=True)
def _no_rejected_charts():
    """Background figures (step 1i): the memory of charts no issue can use
    (owid_rejected_slugs) is shared by every issue, so a chart one test
    rejected would be left out of the next test's search. Every test starts
    with it empty."""
    from flask import has_app_context
    if has_app_context():
        from app import db
        from app.models import OwidRejectedSlug
        try:
            if OwidRejectedSlug.query.first() is not None:
                OwidRejectedSlug.query.delete()
                db.session.commit()
        except Exception:   # a test with its own app and database
            db.session.rollback()
    yield


@pytest.fixture(autouse=True)
def _fresh_request_state():
    """Every test starts as a new request would: an empty flask.g and an empty
    database session.

    The whole session runs in one app context (the app fixture), and a test
    client request reuses it, so flask.g and the scoped database session
    outlive requests and tests:
    - g: what a request leaves there (Flask-Login's user in g._login_user,
      Flask-Babel's locale, the translation cache of content_i18n) leaked into
      the next test; a user that test deleted or expunged then broke it
      (DetachedInstanceError), a cached locale or translation was stale.
    - the identity map: bulk deletes (synchronize_session=False, raw SQL in
      purge and cleanup) leave the objects of deleted rows in it, and SQLite
      hands their ids out again: a new QualityAward or User then collides
      with a stale one ("Identity map already had an identity", followed by a
      DetachedInstanceError). Some files cleared it themselves
      (test_delete_issue._clean_session); now every test does."""
    from flask import g, has_app_context
    if has_app_context():
        for key in list(vars(g)):
            g.pop(key, None)
        from app import db as _db
        _db.session.rollback()
        _db.session.expunge_all()
    yield


# The limiter as the session app left it (set by the app fixture).
_LIMITER_STATE = {}


@pytest.fixture(autouse=True)
def _fresh_rate_limits_for_every_test(request):
    """Every test starts with the session's limiter and empty counters.

    - The limiter is one object for the whole process, and create_app()
      binds it to the new app's storage. Tests that build an app of their
      own (test_auth, test_verify_bypass, test_rate_limit_storage and
      others) left it pointing at their database, deleted afterwards: later
      tests failed with "no such table: rate_limit_counters". For a test on
      the session app it is put back as it was after that app was built
      (_LIMITER_STATE); a test with its own app keeps the limiter its app set.
    - The counters count per IP (every test request comes from 127.0.0.1)
      or per user, across tests: a test that adds a sixth fact within a
      minute of other tests got the 429 handler's flash and redirect instead
      of its action. Several files reset them themselves; now every test does.
    Only for tests that use the app (no app is built for the others)."""
    if 'app' in request.fixturenames:
        test_app = request.getfixturevalue('app')
        from sqlalchemy.exc import OperationalError
        from app import limiter
        if _LIMITER_STATE and test_app is _LIMITER_STATE.get('_session_app'):
            state = {k: v for k, v in _LIMITER_STATE.items() if k != '_session_app'}
            vars(limiter).clear()
            vars(limiter).update(state)
        try:
            limiter.reset()
        except (AssertionError, OperationalError):
            pass   # a limiter without storage (RATELIMIT_ENABLED off): nothing to reset
    yield


def _forget_deleted_users():
    """A safety net for the tests: many cleanups delete a User straight away
    (db.session.delete), without the app's own route (quality.detach_user).
    SQLite does not enforce the foreign keys and reuses ids, so a user's
    notifications and proposal read stamps were left behind and a later user
    with the same id got them (seen with pytest-xdist, where every worker has
    its own order). Postgres would refuse such a delete; here those rows go
    with the user."""
    from sqlalchemy import event
    from app.models import IssueProposalRead, Notification, User

    @event.listens_for(User, 'before_delete')
    def _before_delete(_mapper, connection, target):
        for table in (Notification.__table__, IssueProposalRead.__table__):
            connection.execute(table.delete().where(table.c.user_id == target.id))


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
        BACKGROUND_FIGURES = False         # tests of that track switch it on themselves

    flask_app = create_app_for_tests(TestConfig)
    _forget_deleted_users()
    from app import limiter
    _LIMITER_STATE.update(vars(limiter), _session_app=flask_app)

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
    from app import create_app, limiter
    # The limiter is one object for the process, and init_app keeps its
    # previous `enabled` when the app's config does not say
    # (config.setdefault(ENABLED, self.enabled)). A test with an app of its
    # own and RATELIMIT_ENABLED False (test_auth, test_verify_bypass,
    # test_username) that ran before this one left it disabled, without
    # storage: limiter.reset() then failed in every later test. The session
    # app starts from the constructor's default.
    limiter.enabled = True
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
