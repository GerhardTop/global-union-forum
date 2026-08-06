import logging
import os
from flask import Flask, session, request, redirect, url_for, flash, jsonify, render_template
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_login import LoginManager
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_talisman import Talisman
from flask_babel import Babel, gettext
from authlib.integrations.flask_client import OAuth
from sqlalchemy import text, inspect as sa_inspect

from config import Config

db = SQLAlchemy()
bcrypt = Bcrypt()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message_category = "error"
limiter = Limiter(key_func=get_remote_address, default_limits=[])
oauth = OAuth()
babel = Babel()

ADMIN_EMAIL        = os.environ.get("ADMIN_EMAIL", "")
MODERATOR_EMAIL    = os.environ.get("MODERATOR_EMAIL", "")
ADMIN_LINKEDIN_URL = os.environ.get("ADMIN_LINKEDIN_URL", "")


def _migrate_columns():
    """Add new columns to existing tables without Flask-Migrate."""
    insp = sa_inspect(db.engine)
    existing = insp.get_table_names()
    is_pg = db.engine.dialect.name == 'postgresql'
    alters = []

    if 'users' in existing:
        user_col_list = insp.get_columns('users')
        cols = {c['name'] for c in user_col_list}
        if 'is_admin' not in cols:
            alters.append('ALTER TABLE users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT FALSE')
        if 'is_moderator' not in cols:
            alters.append('ALTER TABLE users ADD COLUMN is_moderator BOOLEAN NOT NULL DEFAULT FALSE')
        if 'verified' not in cols:
            alters.append('ALTER TABLE users ADD COLUMN verified BOOLEAN NOT NULL DEFAULT FALSE')
        if 'linkedin_url' not in cols:
            alters.append('ALTER TABLE users ADD COLUMN linkedin_url VARCHAR(255) NULL')
        if 'auto_translate' not in cols:
            alters.append('ALTER TABLE users ADD COLUMN auto_translate BOOLEAN NULL DEFAULT NULL')
        else:
            at_info = next(c for c in user_col_list if c['name'] == 'auto_translate')
            if not at_info.get('nullable', True):
                # Was NOT NULL DEFAULT FALSE — maak nullable en reset ongewijzigde defaults
                if is_pg:
                    alters.append('ALTER TABLE users ALTER COLUMN auto_translate DROP NOT NULL')
                    alters.append('ALTER TABLE users ALTER COLUMN auto_translate SET DEFAULT NULL')
                else:
                    alters.append('ALTER TABLE users MODIFY COLUMN auto_translate BOOLEAN NULL DEFAULT NULL')
                alters.append('UPDATE users SET auto_translate = NULL WHERE auto_translate = FALSE')
        if 'google_id' not in cols:
            alters.append('ALTER TABLE users ADD COLUMN google_id VARCHAR(100) NULL')
            if is_pg:
                alters.append('CREATE UNIQUE INDEX IF NOT EXISTS uq_users_google_id ON users (google_id)')
            else:
                alters.append('ALTER TABLE users ADD UNIQUE INDEX uq_users_google_id (google_id)')
        if 'password_changed_at' not in cols:
            alters.append('ALTER TABLE users ADD COLUMN password_changed_at TIMESTAMP NULL DEFAULT NULL')
        # Pseudoniem-model (fase 1): first_name/last_name worden optioneel.
        for _col_name in ('first_name', 'last_name'):
            _col_info = next((c for c in user_col_list if c['name'] == _col_name), None)
            if _col_info and not _col_info.get('nullable', True):
                if is_pg:
                    alters.append(f'ALTER TABLE users ALTER COLUMN {_col_name} DROP NOT NULL')
                else:
                    alters.append(f'ALTER TABLE users MODIFY COLUMN {_col_name} VARCHAR(50) NULL')

    if 'threads' in existing:
        cols = {c['name'] for c in insp.get_columns('threads')}
        if 'is_closed' not in cols:
            alters.append('ALTER TABLE threads ADD COLUMN is_closed BOOLEAN NOT NULL DEFAULT FALSE')
        if 'is_demo' not in cols:
            alters.append('ALTER TABLE threads ADD COLUMN is_demo BOOLEAN NOT NULL DEFAULT FALSE')

    if 'posts' in existing:
        cols = {c['name'] for c in insp.get_columns('posts')}
        if 'parent_id' not in cols:
            alters.append('ALTER TABLE posts ADD COLUMN parent_id INTEGER NULL')
        if 'image_url' not in cols:
            alters.append('ALTER TABLE posts ADD COLUMN image_url VARCHAR(500) NULL')
        if 'is_demo' not in cols:
            alters.append('ALTER TABLE posts ADD COLUMN is_demo BOOLEAN NOT NULL DEFAULT FALSE')

    if alters:
        with db.engine.connect() as conn:
            for sql in alters:
                conn.execute(text(sql))
            conn.commit()

    _migrate_username_and_backfill(insp, existing, is_pg)


def _migrate_username_and_backfill(insp, existing, is_pg):
    """
    username/author_username vereisen een Python-backfill-stap tússen
    'kolom toevoegen' en 'NOT NULL zetten' — dat past niet in de generieke
    alters-batch hierboven (die voert alles blind uit, geen ruimte voor
    logica ertussenin). Daarom een eigen, strikt-sequentiële functie.

    Idempotent: elke fase checkt zijn eigen voorwaarde (kolom bestaat al?
    nog NULL-rijen? index bestaat al?), dus herhaald draaien — ook na een
    eerdere gedeeltelijke mislukking — is veilig en hervat waar het bleef.
    De NOT NULL-constraint wordt pas gezet als de allerlaatste sub-stap,
    nooit vóór de backfill: bij een fout halverwege blijft de database een
    werkende (nullable, deels gevulde) tussentoestand, nooit een kapotte.
    """
    if 'users' not in existing:
        return

    # ── 1. users.username: kolom toevoegen (nog nullable) ────────────────
    user_cols = {c['name']: c for c in insp.get_columns('users')}
    if 'username' not in user_cols:
        with db.engine.connect() as conn:
            conn.execute(text('ALTER TABLE users ADD COLUMN username VARCHAR(30) NULL'))
            conn.commit()

    # ── 2. Python-backfill van elke NULL-username ─────────────────────────
    from app.models import User
    if User.query.filter(User.username.is_(None)).count():
        _backfill_usernames()

    # ── 3. NOT NULL zetten, alleen als dat nog niet zo is ─────────────────
    user_cols = {c['name']: c for c in sa_inspect(db.engine).get_columns('users')}
    if user_cols['username'].get('nullable', True):
        with db.engine.connect() as conn:
            if is_pg:
                conn.execute(text('ALTER TABLE users ALTER COLUMN username SET NOT NULL'))
            else:
                conn.execute(text('ALTER TABLE users MODIFY COLUMN username VARCHAR(30) NOT NULL'))
            conn.commit()

    # ── 4. Hoofdletter-ongevoelige unieke index, los van kolom-status ─────
    # (MySQL's standaard-collation is al _ci, dus een gewone UNIQUE INDEX
    # volstaat daar; Postgres heeft een functionele LOWER()-index nodig.
    # SQLite — alleen relevant voor lokaal testen, nooit een echt
    # deploy-doel — is standaard hoofdlettergevoelig, dus COLLATE NOCASE.)
    dialect = db.engine.dialect.name
    index_names = {ix['name'] for ix in sa_inspect(db.engine).get_indexes('users')}
    if 'uq_users_username_ci' not in index_names and 'uq_users_username' not in index_names:
        with db.engine.connect() as conn:
            if is_pg:
                conn.execute(text(
                    'CREATE UNIQUE INDEX IF NOT EXISTS uq_users_username_ci ON users (LOWER(username))'
                ))
            elif dialect == 'sqlite':
                conn.execute(text(
                    'CREATE UNIQUE INDEX IF NOT EXISTS uq_users_username_ci ON users (username COLLATE NOCASE)'
                ))
            else:
                conn.execute(text('ALTER TABLE users ADD UNIQUE INDEX uq_users_username (username)'))
            conn.commit()

    # ── posts.author_username: zelfde sequentiële aanpak ──────────────────
    if 'posts' not in existing:
        return

    post_cols = {c['name']: c for c in sa_inspect(db.engine).get_columns('posts')}
    if 'author_username' not in post_cols:
        with db.engine.connect() as conn:
            conn.execute(text('ALTER TABLE posts ADD COLUMN author_username VARCHAR(30) NULL'))
            conn.commit()

    from app.models import Post
    if Post.query.filter(Post.author_username.is_(None)).count():
        # Posts met een nog-bestaande auteur: username via join backfillen.
        with db.engine.connect() as conn:
            conn.execute(text(
                'UPDATE posts SET author_username = '
                '(SELECT username FROM users WHERE users.id = posts.user_id) '
                'WHERE posts.author_username IS NULL AND posts.user_id IS NOT NULL'
            ))
            conn.commit()
        # Tombstone-posts (user_id IS NULL, account al losgekoppeld/verwijderd):
        # gedeelde placeholder, geen per-post gok — zie forum_post_verwijderen()
        # voor hetzelfde gedrag bij toekomstige verwijderingen.
        Post.query.filter(Post.author_username.is_(None)).update(
            {Post.author_username: 'deleted_user'}, synchronize_session=False
        )
        db.session.commit()

    post_cols = {c['name']: c for c in sa_inspect(db.engine).get_columns('posts')}
    if post_cols['author_username'].get('nullable', True):
        with db.engine.connect() as conn:
            if is_pg:
                conn.execute(text('ALTER TABLE posts ALTER COLUMN author_username SET NOT NULL'))
            else:
                conn.execute(text('ALTER TABLE posts MODIFY COLUMN author_username VARCHAR(30) NOT NULL'))
            conn.commit()


def _backfill_usernames():
    """
    Wijst een unieke username toe aan elke User zonder username. De 4 demo-
    gebruikers krijgen hun vaste, herkenbare namen; elke andere bestaande
    gebruiker (bv. een admin/moderator-account van vóór dit veld bestond)
    krijgt de placeholder 'user_<id>' — hetzelfde 'user_'-prefixschema als
    nieuwe Google-OAuth-accounts (zie auth.py), zodat één simpele check
    (username.startswith('user_')) overal betrouwbaar detecteert dat de
    gebruiker nog een eigen gebruikersnaam moet kiezen bij eerstvolgende
    login.
    """
    from app.models import User
    from app.utils import is_username_blacklisted

    DEMO_USERNAMES = {
        'jan.hofman.demo@globalunionforum.org':       'jan_h',
        'fatima.elidrissi.demo@globalunionforum.org': 'fatima_e',
        'sarah.chen.demo@globalunionforum.org':       'sarah_c',
        'marcus.osei.demo@globalunionforum.org':      'marcus_o',
    }

    taken = {u.username.lower() for u in User.query.filter(User.username.isnot(None)).all()}
    rows = User.query.filter(User.username.is_(None)).order_by(User.id).all()

    for user in rows:
        candidate = DEMO_USERNAMES.get(user.email, f'user_{user.id}')
        base, n = candidate, 1
        while candidate.lower() in taken or is_username_blacklisted(candidate):
            n += 1
            candidate = f'{base}{n}'[:20]
        user.username = candidate
        taken.add(candidate.lower())

    db.session.commit()


def _ensure_admin_user():
    from app.models import User

    # Gerhard — admin + linkedin
    user = User.query.filter_by(email=ADMIN_EMAIL).first()
    if user:
        changed = False
        if not (user.is_admin and user.is_moderator):
            user.is_admin = True
            user.is_moderator = True
            changed = True
        if not user.linkedin_url and ADMIN_LINKEDIN_URL:
            user.linkedin_url = ADMIN_LINKEDIN_URL
            changed = True
        if changed:
            db.session.commit()

    # Moderator (configureerbaar via MODERATOR_EMAIL)
    if MODERATOR_EMAIL:
        moderator = User.query.filter_by(email=MODERATOR_EMAIL).first()
        if moderator and not moderator.is_moderator:
            moderator.is_moderator = True
            db.session.commit()


def create_app():
    # sqlite is uitsluitend een test-artefact in dit project (productie=Neon/
    # Postgres, lokale fallback=MySQL) — een sqlite-DATABASE_URL mag dus nooit
    # 'production'-gedrag triggeren (Talisman force_https/secure cookies,
    # logging-niveau), anders redirect Talisman testrequests naar https en
    # kan de Flask-testclient (die gewoon http gebruikt) de app niet bereiken.
    _db_url = os.environ.get("DATABASE_URL", "")
    _production = bool(_db_url) and not _db_url.startswith("sqlite")
    logging.basicConfig(level=logging.WARNING if _production else logging.DEBUG)
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    bcrypt.init_app(app)
    login_manager.init_app(app)
    limiter.init_app(app)
    oauth.init_app(app)
    def get_locale():
        lang_in_session = session.get('lang')
        if lang_in_session in ('nl', 'en'):
            return lang_in_session
        return request.accept_languages.best_match(['nl', 'en'], default='nl')

    babel.init_app(app, locale_selector=get_locale)
    oauth.register(
        name='google',
        client_id=app.config['GOOGLE_CLIENT_ID'],
        client_secret=app.config['GOOGLE_CLIENT_SECRET'],
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile'},
    )

    _csp = {
        'default-src': "'self'",
        'script-src': ["'self'", "'unsafe-inline'"],
        'style-src': ["'self'", "'unsafe-inline'", 'https://fonts.googleapis.com'],
        'font-src': ["'self'", 'https://fonts.gstatic.com'],
        'img-src': ["'self'", 'data:'],
        'connect-src': ["'self'", 'https://api.anthropic.com'],
        'frame-ancestors': "'none'",
        'object-src': "'none'",
        'base-uri': "'self'",
    }
    Talisman(
        app,
        force_https=_production,
        strict_transport_security=_production,
        session_cookie_secure=_production,
        session_cookie_http_only=True,
        frame_options='DENY',
        content_security_policy=_csp,
    )

    @app.errorhandler(404)
    def not_found(e):
        return render_template('404.html'), 404

    @app.errorhandler(500)
    def server_error(e):
        import traceback as tb
        from app.mail import send_error_email
        try:
            send_error_email(str(e), tb.format_exc())
        except Exception:
            pass
        return render_template('500.html'), 500

    @app.errorhandler(429)
    def too_many_requests(e):
        app.logger.warning(
            f"Rate-limit overschreden: {request.method} {request.path} "
            f"van {request.remote_addr}"
        )
        lang = session.get('lang', 'nl')
        msg = ('Te veel pogingen. Probeer het over een minuut opnieuw.' if lang == 'nl'
               else 'Too many attempts. Please try again in a minute.')
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'error': 'rate_limited', 'message': msg}), 429
        flash(msg, 'error')
        _path_map = {
            '/aanmelden': 'social.aanmelden',
            '/wachtwoord-vergeten': 'auth.wachtwoord_vergeten',
            '/feedback': 'main.index',
        }
        return redirect(url_for(_path_map.get(request.path, 'auth.login')))

    from datetime import datetime
    from flask_login import logout_user as _logout_user

    @app.before_request
    def enforce_session_timeout():
        from flask_login import current_user as _cu
        if not _cu.is_authenticated:
            return
        last = session.get('_last_active')
        now = datetime.utcnow().timestamp()
        if last and (now - last) > 1800:
            lang = session.get('lang', 'nl')
            _logout_user()
            session.clear()
            session['lang'] = lang
            flash(
                'Je sessie is verlopen. Log opnieuw in.' if lang == 'nl'
                else 'Your session has expired. Please log in again.',
                'error'
            )
            return redirect(url_for('auth.login'))
        session['_last_active'] = now

    _USERNAME_CHOICE_EXEMPT = {'auth.kies_gebruikersnaam', 'auth.logout', 'static'}

    @app.before_request
    def enforce_username_choice():
        """
        Pseudoniem-model (fase 1): een account met een placeholder-username
        (het 'user_'-prefixschema, zie _backfill_usernames() en
        auth_google_callback()) moet eerst een eigen naam kiezen vóór het
        de rest van de site kan gebruiken. Als before_request-hook i.p.v.
        alleen een check in login() zodat dit ELKE binnenkomstroute dekt
        (wachtwoord-login, Google-OAuth, een sessie die al bestond toen dit
        veld werd toegevoegd) — niet slechts één van de twee login-paden.
        """
        from flask_login import current_user as _cu
        if not _cu.is_authenticated:
            return
        if not (_cu.username or '').startswith('user_'):
            return
        if request.endpoint in _USERNAME_CHOICE_EXEMPT:
            return
        return redirect(url_for('auth.kies_gebruikersnaam'))

    from app.routes import main, auth, forum_bp, profile_bp, admin_bp, social
    app.register_blueprint(main)
    app.register_blueprint(auth)
    app.register_blueprint(forum_bp)
    app.register_blueprint(profile_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(social)

    from app.utils import linkify
    app.jinja_env.filters['linkify'] = linkify

    @app.context_processor
    def inject_babel():
        """Inject babel utilities and current language into templates."""
        return dict(
            _=gettext,
            lang=session.get('lang', 'nl'),
            LANGUAGES={'en': 'English', 'nl': 'Nederlands'}
        )

    with app.app_context():
        db.create_all()
        _migrate_columns()
        _ensure_admin_user()
        from app.routes.forum import _seed_forum
        _seed_forum()

    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    return app
