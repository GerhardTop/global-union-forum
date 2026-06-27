import re
from urllib.parse import urlparse
from markupsafe import escape, Markup
from flask import url_for
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature

_URL_RE = re.compile(r'(https?://[^\s<>"\']+)', re.IGNORECASE)


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


_PW_ERROR = {
    'nl': ('Wachtwoord moet tussen 8 en 72 tekens bevatten, '
           'een hoofdletter, een cijfer en een speciaal teken.'),
    'en': ('Password must be between 8 and 72 characters and contain '
           'one uppercase letter, one number and one special character.'),
}


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
