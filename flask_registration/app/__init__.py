from flask import Flask, session
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_login import LoginManager
from sqlalchemy import text, inspect as sa_inspect

from config import Config

db = SQLAlchemy()
bcrypt = Bcrypt()
login_manager = LoginManager()
login_manager.login_view = "main.login"
login_manager.login_message_category = "error"

ADMIN_EMAIL = "top.gerhard@gmail.com"


def _migrate_columns():
    """Add new columns to existing tables without Flask-Migrate."""
    insp = sa_inspect(db.engine)
    existing = insp.get_table_names()
    alters = []

    if 'users' in existing:
        cols = {c['name'] for c in insp.get_columns('users')}
        if 'is_admin' not in cols:
            alters.append('ALTER TABLE users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT FALSE')
        if 'is_moderator' not in cols:
            alters.append('ALTER TABLE users ADD COLUMN is_moderator BOOLEAN NOT NULL DEFAULT FALSE')

    if 'threads' in existing:
        cols = {c['name'] for c in insp.get_columns('threads')}
        if 'is_closed' not in cols:
            alters.append('ALTER TABLE threads ADD COLUMN is_closed BOOLEAN NOT NULL DEFAULT FALSE')

    if 'posts' in existing:
        cols = {c['name'] for c in insp.get_columns('posts')}
        if 'parent_id' not in cols:
            alters.append('ALTER TABLE posts ADD COLUMN parent_id INTEGER NULL')
        if 'image_url' not in cols:
            alters.append('ALTER TABLE posts ADD COLUMN image_url VARCHAR(500) NULL')

    if alters:
        with db.engine.connect() as conn:
            for sql in alters:
                conn.execute(text(sql))
            conn.commit()


def _ensure_admin_user():
    from app.models import User
    user = User.query.filter_by(email=ADMIN_EMAIL).first()
    if user and not (user.is_admin and user.is_moderator):
        user.is_admin = True
        user.is_moderator = True
        db.session.commit()


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    bcrypt.init_app(app)
    login_manager.init_app(app)

    from app.routes import main
    app.register_blueprint(main)

    from app.utils import linkify
    app.jinja_env.filters['linkify'] = linkify

    from app.translations import TRANSLATIONS

    @app.context_processor
    def inject_translations():
        lang = session.get('lang', 'nl')
        login_manager.login_message = TRANSLATIONS[lang]['flash_invalid_credentials']
        return dict(t=TRANSLATIONS[lang], lang=lang)

    with app.app_context():
        db.create_all()
        _migrate_columns()
        _ensure_admin_user()

    return app
