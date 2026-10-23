import hmac
import ipaddress
import os
import re
from urllib.parse import urlparse
from markupsafe import escape, Markup
from flask import url_for, session, request
from itsdangerous import URLSafeTimedSerializer

_URL_RE = re.compile(r'(https?://[^\s<>"\']+)', re.IGNORECASE)


# ── Echt client-IP (sleutel voor de rate limiter en de logs) ───────────────
#
# Productieketen: bezoeker -> Cloudflare -> Render -> gunicorn. Het socket-
# adres (REMOTE_ADDR) is daar een proxy, en X-Forwarded-For is (deels) door de
# client zelf aan te leveren. Daarom vertrouwen we alleen Cloudflare's
# CF-Connecting-IP, en dat alleen als het verzoek bewijsbaar via Cloudflare
# kwam:
#
#   * Cloudflare (Rules > Transform Rules > Modify Request Header) zet op elk
#     verzoek de header X-GU-Origin-Verify met een geheime waarde.
#   * Render krijgt dezelfde waarde als omgevingsvariabele ORIGIN_VERIFY_SECRET.
#
# Met secret: juiste header + CF-Connecting-IP -> dat IP; anders (direct op
# *.onrender.com, header mogelijk vervalst) het ruwe socket-adres.
# Zonder secret (hardening nog niet ingesteld): CF-Connecting-IP als die er
# is, anders het ruwe socket-adres.
ORIGIN_VERIFY_HEADER = 'X-GU-Origin-Verify'


def _socket_ip():
    """Het ruwe verbindingsadres. ProxyFix bewaart het origineel voordat hij
    REMOTE_ADDR eventueel herschrijft; dat gebruiken we als het er is."""
    orig = request.environ.get('werkzeug.proxy_fix.orig') or {}
    return orig.get('REMOTE_ADDR') or request.environ.get('REMOTE_ADDR') or '127.0.0.1'


def _valid_ip(value):
    value = (value or '').strip()
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return None
    return value


def real_client_ip():
    """Het IP van de bezoeker; zie de toelichting hierboven."""
    secret = os.environ.get('ORIGIN_VERIFY_SECRET', '')
    cf_ip = _valid_ip(request.headers.get('CF-Connecting-IP'))
    if secret:
        given = request.headers.get(ORIGIN_VERIFY_HEADER, '')
        if cf_ip and hmac.compare_digest(given.encode('utf-8'), secret.encode('utf-8')):
            return cf_ip
        return _socket_ip()
    return cf_ip or _socket_ip()


def safe_next(target, fallback):
    """Geef `target` terug als het een pad binnen de eigen site is, anders
    `fallback`. Tegen open redirects via een next-veld of -parameter.

    Alleen een pad dat met precies één "/" begint mag. Geweigerd: lege
    waarden, "//host" en "///host" (protocol-relatief), elke backslash
    (browsers lezen "/\\host" als "//host"), witruimte en stuurtekens, en
    alles met een scheme of host volgens urlparse.
    """
    if not target or not isinstance(target, str):
        return fallback
    if not target.startswith('/') or target.startswith('//'):
        return fallback
    if '\\' in target or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in target):
        return fallback
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return fallback
    return target

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


# ── Gedeelde globale mail-rate-limit (gedeeld door auth en social) ─────────
#
# Structureel vangnet bovenop de bestaande per-route- en per-IP-limieten:
# welk formulier een bot ook vindt (feedback, uitnodiging, aanmelden,
# wachtwoord-vergeten, verificatiemail opnieuw versturen — elke route die
# send_email() aanroept), ze delen allemaal deze ENE teller. Toegepast via
# @limiter.shared_limit(...) (niet .limit()!) op elke route: .limit() zou
# de scope stilzwijgend per endpoint uitsplitsen (elke route krijgt dan
# alsnog zijn EIGEN teller, ondanks de vaste key_func hieronder) — pas
# .shared_limit() met een vaste scope-string maakt 'm écht route-
# onafhankelijk. Vaste key_func (dus geen per-IP-sleutel) maakt 'm ook
# IP-onafhankelijk. Samen: "max X mail-triggerende requests per uur over de
# HELE app", ongeacht welke route of welk IP.
MAIL_GLOBAL_RATE_LIMIT = "30 per hour"
MAIL_GLOBAL_SCOPE = "mail-global"


def mail_global_key():
    return "mail-global"


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
