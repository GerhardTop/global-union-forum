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


def ratelimit_storage_uri(db_uri: str) -> str:
    """
    Flask-Limiter/`limits` heeft geen ingebouwde SQL-backend; app/
    rate_limit_storage.py registreert een eigen 'postgresql'/'sqlite'-
    backend die dezelfde database hergebruikt, zodat rate-limit-tellers een
    container-herstart op Render overleven (zie die module voor het volledige
    verhaal). We hergebruiken hier gewoon de al-berekende SQLALCHEMY_DATABASE_URI
    — geen aparte connectiestring nodig.

    Voor elk ander schema (bv. de lokale MySQL-fallback in _build_db_url()
    hierboven, wanneer DATABASE_URL niet gezet is) valt dit terug op
    'memory://', exact het gedrag van vóór deze module — dat pad wordt nooit
    in productie gebruikt en geen enkele bestaande test hoeft hiervoor aangepast.

    Bewust GEEN class-attribuut op Config (en dus niet vanuit de class-body
    hierboven aangeroepen): meerdere testfiles overschrijven
    Config.SQLALCHEMY_DATABASE_URI ná het importeren van deze module, vlak
    vóór create_app(). Een hier-al-berekend class-attribuut zou dat nooit
    zien (bevroren op de waarde van de allereerste import van config.py) en
    zo een testbestand tegen een verkeerde database aan laten praten. Daarom
    wordt dit pas in create_app() aangeroepen, met de dan-geldende waarde uit
    app.config — zie app/__init__.py.
    """
    scheme = db_uri.split("://", 1)[0]
    if scheme in ("postgresql", "sqlite"):
        return db_uri
    return "memory://"


# ── Languages ─────────────────────────────────────────────────────────────
# The language register: every language the platform knows, with its name in
# its own language and its writing direction. app/i18n.py reads it; nothing
# else keeps its own list of languages.
LANGUAGES = [
    {"code": "nl", "name": "Nederlands", "dir": "ltr"},
    {"code": "en", "name": "English", "dir": "ltr"},
    {"code": "de", "name": "Deutsch", "dir": "ltr"},
    {"code": "es", "name": "Español", "dir": "ltr"},
    {"code": "fr", "name": "Français", "dir": "ltr"},
]

# The languages that are switched on. Switching a language on is adding its
# code here (after its catalog is translated and compiled, see CLAUDE.md).
ENABLED_LANGUAGES = ["nl", "en"]

# The language for visitors without a preference, and the fallback everywhere.
DEFAULT_LANGUAGE = "nl"

# The language of the msgids in the templates and the code.
SOURCE_LANGUAGE = "en"


class Config:
    SECRET_KEY = _require_env("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = _build_db_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,   # test connectie vóór gebruik; maakt nieuwe aan als Neon sliep
        "pool_recycle": 300,     # vervang connecties ouder dan 5 minuten
    }
    # RATELIMIT_STORAGE_URI wordt NIET hier gezet — zie ratelimit_storage_uri()
    # hierboven voor waarom, en app/__init__.py:create_app() voor waar het
    # wél gebeurt.


    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")

    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)  # outer limit; activity check enforces 30 min
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'

    # Flask-Babel configuration
    BABEL_DEFAULT_LOCALE = DEFAULT_LANGUAGE
    BABEL_DEFAULT_TIMEZONE = 'UTC'
    RESEND_API_KEY = _require_env('RESEND_API_KEY')
