from flask import Flask, session
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_login import LoginManager

from config import Config

db = SQLAlchemy()
bcrypt = Bcrypt()
login_manager = LoginManager()
login_manager.login_view = "main.login"
login_manager.login_message_category = "error"


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    bcrypt.init_app(app)
    login_manager.init_app(app)

    from app.routes import main
    app.register_blueprint(main)

    from app.translations import TRANSLATIONS

    @app.context_processor
    def inject_translations():
        lang = session.get('lang', 'nl')
        login_manager.login_message = TRANSLATIONS[lang]['flash_invalid_credentials']
        return dict(t=TRANSLATIONS[lang], lang=lang)

    with app.app_context():
        db.create_all()

    return app
