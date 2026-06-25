import logging
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

ADMIN_EMAIL = "top.gerhard@gmail.com"


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


def _ensure_admin_user():
    from app.models import User
    from flask_bcrypt import Bcrypt
    _bcrypt = Bcrypt()

    # Gerhard — admin + linkedin
    user = User.query.filter_by(email=ADMIN_EMAIL).first()
    if user:
        changed = False
        if not (user.is_admin and user.is_moderator):
            user.is_admin = True
            user.is_moderator = True
            changed = True
        if not user.linkedin_url:
            user.linkedin_url = 'https://www.linkedin.com/in/gerhardtop'
            changed = True
        if changed:
            db.session.commit()

    # Anne Top-Verhoeven — moderator
    anne = User.query.filter_by(email='atopverhoeven@hotmail.com').first()
    if anne:
        if not anne.is_moderator:
            anne.is_moderator = True
            db.session.commit()


def create_app():
    logging.basicConfig(level=logging.DEBUG)
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
        force_https=False,
        strict_transport_security=False,
        session_cookie_secure=False,
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
        lang = session.get('lang', 'nl')
        msg = ('Te veel pogingen. Probeer het over een minuut opnieuw.' if lang == 'nl'
               else 'Too many attempts. Please try again in a minute.')
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'error': 'rate_limited', 'message': msg}), 429
        flash(msg, 'error')
        _path_map = {
            '/aanmelden': 'social.aanmelden',
            '/wachtwoord-vergeten': 'auth.wachtwoord_vergeten',
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

    return app
