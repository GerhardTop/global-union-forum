from flask import Flask, session, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_login import LoginManager
from flask_mail import Mail
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
login_manager.login_view = "main.login"
login_manager.login_message_category = "error"
mail = Mail()
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
    anne_email = 'anne.top@globalunionforum.org'
    if not User.query.filter_by(email=anne_email).first():
        import os
        pw = _bcrypt.generate_password_hash(os.urandom(24).hex()).decode('utf-8')
        anne = User(
            first_name='Anne',
            last_name='Top-Verhoeven',
            email=anne_email,
            password_hash=pw,
            is_moderator=True,
            verified=True,
            linkedin_url='https://www.linkedin.com/in/anne-top-verhoeven',
        )
        db.session.add(anne)
        db.session.commit()


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    bcrypt.init_app(app)
    login_manager.init_app(app)
    mail.init_app(app)
    limiter.init_app(app)
    oauth.init_app(app)
    babel.init_app(app)
    oauth.register(
        name='google',
        client_id=app.config['GOOGLE_CLIENT_ID'],
        client_secret=app.config['GOOGLE_CLIENT_SECRET'],
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile'},
    )

    @babel.localeselector
    def get_locale():
        """Select locale based on session or browser preference."""
        if 'lang' in session:
            return session['lang'] if session['lang'] in ['nl', 'en'] else 'en'
        best = request.accept_languages.best_match(['nl', 'en'], default='en')
        return best

    _csp = {
        'default-src': "'self'",
        'script-src': ["'self'", "'unsafe-inline'"],
        'style-src': ["'self'", "'unsafe-inline'",
                      'https://fonts.googleapis.com'],
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

    @app.errorhandler(429)
    def too_many_requests(e):
        lang = session.get('lang', 'nl')
        msg = ('Te veel pogingen. Probeer het over een minuut opnieuw.' if lang == 'nl'
               else 'Too many attempts. Please try again in a minute.')
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'error': 'rate_limited', 'message': msg}), 429
        flash(msg, 'error')
        _path_map = {
            '/aanmelden': 'main.aanmelden',
            '/wachtwoord-vergeten': 'main.wachtwoord_vergeten',
        }
        return redirect(url_for(_path_map.get(request.path, 'main.login')))

    from app.routes import main
    app.register_blueprint(main)

    from app.utils import linkify
    app.jinja_env.filters['linkify'] = linkify

    @app.context_processor
    def inject_babel():
        """Inject babel utilities and current language into templates."""
        return dict(
            _=gettext,
            lang=session.get('lang', 'en'),
            LANGUAGES={'en': 'English', 'nl': 'Nederlands'}
        )

    @app.before_request
    def ensure_admin():
        if request.endpoint == 'static':
            return
        _ensure_admin_user()

    with app.app_context():
        db.create_all()
        _migrate_columns()
        from app.routes import _seed_forum
        _seed_forum()

    return app
