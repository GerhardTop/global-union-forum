import re
from urllib.parse import urlparse
from markupsafe import escape, Markup
from flask import url_for, session
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature

_URL_RE = re.compile(r'(https?://[^\s<>"\']+)', re.IGNORECASE)

# ── Username-validatie (pseudoniem-model, fase 1) ────────────────────────────
#
# Herbruikbaar op meerdere plekken: registratie, de blur-check, toekomstige
# moderator-wijzigingen van een username, en de migratie-backfill.
USERNAME_BLACKLIST = {"admin", "administrator", "moderator", "beheerder", "mod"}
_USERNAME_RE = re.compile(r'^[A-Za-z0-9_-]{3,20}$')


def is_username_valid_format(username):
    return bool(_USERNAME_RE.match(username or ""))


def is_username_blacklisted(username):
    """Exacte match, hoofdletter-ongevoelig — geen substring-matching."""
    return (username or "").strip().lower() in USERNAME_BLACKLIST


def is_username_available(username, exclude_user_id=None):
    """Hoofdletter-ongevoelige uniciteitscheck tegen bestaande gebruikers."""
    from app import db
    from app.models import User
    q = User.query.filter(db.func.lower(User.username) == (username or "").lower())
    if exclude_user_id:
        q = q.filter(User.id != exclude_user_id)
    return q.first() is None


def _short_label(url):
    """Return domain without www. prefix, truncated to 15 chars, plus '...'."""
    try:
        host = urlparse(url).netloc or url
    except Exception:
        host = url
    if host.startswith('www.'):
        host = host[4:]
    return host[:15] + '...'


def linkify(text):
    """Convert bare URLs in text to clickable <a> links. HTML-safe."""
    if not text:
        return Markup('')
    parts = _URL_RE.split(text)
    result = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            href = str(escape(part))
            label = str(escape(_short_label(part)))
            result.append(
                f'<a href="{href}" target="_blank" rel="noopener noreferrer">{label}</a>'
            )
        else:
            result.append(str(escape(part)))
    return Markup(''.join(result))


# ── Wachtwoord validatie (gedeeld door auth en social) ───────────────────────

_SPECIAL = re.compile(r'[^A-Za-z0-9]')


def _password_strong(pw):
    return (
        len(pw) >= 8 and
        len(pw.encode('utf-8')) <= 72 and  # bcrypt kapt stil af op 72 bytes
        bool(re.search(r'[A-Z]', pw)) and
        bool(re.search(r'[0-9]', pw)) and
        bool(_SPECIAL.search(pw))
    )


# ── PRG (Post/Redirect/Get) form-state ──────────────────────────────────────
#
# Generaliseert het bestaande session['modal']-eenmalig-poppen-patroon
# (zie main.index()) naar formuliervalidatie: na een mislukte POST bewaren
# we fouten + niet-gevoelige old-input één GET lang, redirecten (303), en de
# GET popt het weer. Zo blijft de rest van de request-cyclus — inline rode
# velden, scroll-naar-fout, blur-JS — precies zoals bij de oude in-place
# render, alleen zonder dat de laatste geschiedenis-entry een POST is (wat
# de "Confirm Form Resubmission"-melding bij back/refresh veroorzaakte).

def _stash_form_state(form_key, errors, data=None):
    """Bewaar validatiefouten + old input (nooit wachtwoorden!) voor form_key."""
    session['form_state'] = {'form': form_key, 'errors': errors, 'data': data or {}}


def _pop_form_state(form_key):
    """
    Haal de eenmalige state voor form_key op. Popt ONVOORWAARDELIJK (ook als
    er niets voor form_key klaarstond) zodat state van een ander formulier
    nooit blijft hangen of op het verkeerde scherm verschijnt.
    """
    state = session.pop('form_state', None)
    if not state or state.get('form') != form_key:
        return {}, {}
    return state.get('errors', {}), state.get('data', {})


# ── E-mail verificatie (gedeeld door auth en social) ────────────────────────

def _make_verify_token(email, secret_key):
    s = URLSafeTimedSerializer(secret_key)
    return s.dumps(email, salt='email-verify')


def _send_verify_email(user, lang, secret_key):
    from app.mail import send_email
    token = _make_verify_token(user.email, secret_key)
    verify_url = url_for('auth.verify_email', token=token, _external=True)
    if lang == 'en':
        subject = "Confirm your email — Global Union Forum"
        html = (
            f"<p>Hi {user.first_name},</p>"
            f"<p>Please confirm your email address by clicking the link below:</p>"
            f"<p><a href='{verify_url}'>{verify_url}</a></p>"
            f"<p>This link expires in 24 hours.</p>"
            f"<p>If you did not create an account, you can ignore this email.</p>"
            f"<p>Global Union Forum</p>"
        )
    else:
        subject = "Bevestig je e-mailadres — Global Union Forum"
        html = (
            f"<p>Hoi {user.first_name},</p>"
            f"<p>Bevestig je e-mailadres via de onderstaande link:</p>"
            f"<p><a href='{verify_url}'>{verify_url}</a></p>"
            f"<p>Deze link verloopt na 24 uur.</p>"
            f"<p>Als je geen account hebt aangemaakt, kun je deze e-mail negeren.</p>"
            f"<p>Global Union Forum</p>"
        )
    send_email(user.email, subject, html)
