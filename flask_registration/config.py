import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv(override=False)


def _require_env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        raise RuntimeError(f"Required environment variable '{key}' is not set")
    return val


def _build_db_url():
    url = os.environ.get("DATABASE_URL", "")
    if url:
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        return url
    return (
        f"mysql+pymysql://{os.environ.get('DB_USER', 'root')}:"
        f"{os.environ.get('DB_PASSWORD', '')}@"
        f"{os.environ.get('DB_HOST', 'localhost')}:"
        f"{os.environ.get('DB_PORT', '3306')}/"
        f"{os.environ.get('DB_NAME', 'flask_registration')}"
    )


class Config:
    SECRET_KEY = _require_env("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = _build_db_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,   # test connectie vóór gebruik; maakt nieuwe aan als Neon sliep
        "pool_recycle": 300,     # vervang connecties ouder dan 5 minuten
    }


    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")

    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)  # outer limit; activity check enforces 30 min
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'

    # Flask-Babel configuration
    BABEL_DEFAULT_LOCALE = 'nl'
    BABEL_SUPPORTED_LOCALES = ['en', 'nl']
    BABEL_DEFAULT_TIMEZONE = 'UTC'
    RESEND_API_KEY = _require_env('RESEND_API_KEY')
