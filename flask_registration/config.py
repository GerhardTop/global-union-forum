import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-this-in-production")
    _db_url = os.environ.get("DATABASE_URL")
    if _db_url:
        # Render.com levert postgres:// — SQLAlchemy vereist postgresql://
        if _db_url.startswith("postgres://"):
            _db_url = _db_url.replace("postgres://", "postgresql://", 1)
        SQLALCHEMY_DATABASE_URI = _db_url
    else:
        SQLALCHEMY_DATABASE_URI = (
            f"mysql+pymysql://{os.environ.get('DB_USER', 'root')}:"
            f"{os.environ.get('DB_PASSWORD', '')}@"
            f"{os.environ.get('DB_HOST', 'localhost')}:"
            f"{os.environ.get('DB_PORT', '3306')}/"
            f"{os.environ.get('DB_NAME', 'flask_registration')}"
        )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    MAIL_SERVER   = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT     = int(os.environ.get("MAIL_PORT", 587))
    MAIL_USE_TLS  = os.environ.get("MAIL_USE_TLS", "true").lower() == "true"
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_USERNAME", "noreply@globalunionforum.org")

    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")

    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)  # outer limit; activity check enforces 30 min
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
