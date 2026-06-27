import secrets

from datetime import datetime, timezone

from flask import Blueprint, render_template, redirect, url_for, flash, request, session, jsonify, current_app, abort
from urllib.parse import urlparse
from flask_login import login_user, logout_user, login_required, current_user
from flask_babel import gettext as _
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature

from app import db, bcrypt, limiter, oauth
from app.forms import (WachtwoordVergetenForm, WachtwoordResetForm,
                       LinkedInAanvullenForm, DeleteAccountForm)
from app.mail import send_email
from app.models import User, Post, PostLike
from app.utils import _password_strong, _PW_ERROR, _make_verify_token, _send_verify_email

auth = Blueprint("auth", __name__)


# ── Wachtwoord reset helpers ─────────────────────────────────────────────────

def _make_reset_token(email):
    s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    return s.dumps(email, salt='password-reset')


def _verify_reset_token(token):
    s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    try:
        email, issued_at = s.loads(
            token, salt='password-reset', max_age=3600, return_timestamp=True
        )
        return email, issued_at, None
    except SignatureExpired:
        return None, None, 'expired'
    except BadSignature:
        return None, None, 'invalid'


def _send_reset_email(user, reset_url, lang):
    if lang == 'en':
        subject = "Reset your password — Global Union Forum"
        html = (
            f"<p>Hi {user.first_name},</p>"
            f"<p>You requested a password reset. Click the link below to choose a new password:</p>"
            f"<p><a href='{reset_url}'>{reset_url}</a></p>"
            f"<p>This link expires in 1 hour.</p>"
            f"<p>If you did not request this, you can safely ignore this email.</p>"
            f"<p>Global Union Forum</p>"
        )
    else:
        subject = "Wachtwoord opnieuw instellen — Global Union Forum"
        html = (
            f"<p>Hoi {user.first_name},</p>"
            f"<p>Je hebt een wachtwoordreset aangevraagd. Klik op de link hieronder om een nieuw wachtwoord in te stellen:</p>"
            f"<p><a href='{reset_url}'>{reset_url}</a></p>"
            f"<p>Deze link verloopt na 1 uur.</p>"
            f"<p>Als jij dit niet hebt aangevraagd, kun je deze e-mail negeren.</p>"
            f"<p>Global Union Forum</p>"
        )
    send_email(user.email, subject, html)


# ── Routes ───────────────────────────────────────────────────────────────────

@auth.route("/register")
def register():
    return redirect(url_for("social.aanmelden"))


@auth.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])
def login():
    from app.forms import LoginForm
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    form = LoginForm()
    login_error = False
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.lower().strip()).first()
        if user and bcrypt.check_password_hash(user.password_hash, form.password.data):
            session.permanent = True
            login_user(user)
            next_page = request.args.get("next")
            parsed_next = urlparse(next_page) if next_page else None
            if not next_page or parsed_next.scheme or parsed_next.netloc:
                next_page = url_for("main.index")
            return redirect(next_page)
        login_error = True
    return render_template("login.html", form=form, login_error=login_error)


@auth.route('/wachtwoord-vergeten', methods=['GET', 'POST'])
@limiter.limit("3 per hour", methods=["POST"])
def wachtwoord_vergeten():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    lang = session.get('lang', 'nl')
    form = WachtwoordVergetenForm()
    sent = False
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        user = User.query.filter_by(email=email).first()
        if user:
            reset_url = url_for('auth.wachtwoord_reset', token=_make_reset_token(email), _external=True)
            _send_reset_email(user, reset_url, lang)
        sent = True
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'ok': True})
    return render_template('wachtwoord_vergeten.html', sent=sent, form=form)


@auth.route('/wachtwoord-reset/<token>', methods=['GET', 'POST'])
def wachtwoord_reset(token):
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    lang = session.get('lang', 'nl')
    email, issued_at, token_error = _verify_reset_token(token)
    if not email:
        if token_error == 'expired':
            current_app.logger.info("Verlopen wachtwoord-reset token gebruikt (geen aanval).")
        else:
            current_app.logger.warning("Ongeldig of geforgeerd wachtwoord-reset token.")
        return render_template('wachtwoord_reset.html', token_invalid=True, token=token)
    user = User.query.filter_by(email=email).first_or_404()
    # Token ongeldig als het wachtwoord ná uitgifte al is gewijzigd.
    if user.password_changed_at and issued_at and user.password_changed_at > issued_at:
        return render_template('wachtwoord_reset.html', token_invalid=True, token=token)
    form = WachtwoordResetForm()
    error = None
    if form.validate_on_submit():
        pw  = form.password.data
        pw2 = form.password_confirm.data
        if not _password_strong(pw):
            error = _PW_ERROR[lang]
        elif pw != pw2:
            error = ('Wachtwoorden komen niet overeen.' if lang == 'nl'
                     else 'Passwords do not match.')
        else:
            user.password_hash = bcrypt.generate_password_hash(pw).decode('utf-8')
            user.password_changed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.session.commit()
            session.permanent = True
            login_user(user)
            flash(
                'Wachtwoord gewijzigd. Je bent nu ingelogd.' if lang == 'nl'
                else 'Password changed. You are now logged in.',
                'success'
            )
            return redirect(url_for('main.index'))
    return render_template('wachtwoord_reset.html', token_invalid=False, token=token,
                           error=error, form=form)


@auth.route('/auth/google')
def auth_google():
    redirect_uri = url_for('auth.auth_google_callback', _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@auth.route('/auth/google/callback')
def auth_google_callback():
    lang = session.get('lang', 'nl')
    oauth_error = request.args.get('error')
    if oauth_error:
        current_app.logger.warning(f"Google OAuth geweigerd: {oauth_error}")
        flash(
            'Inloggen met Google geannuleerd.' if lang == 'nl'
            else 'Google sign-in cancelled.',
            'warning'
        )
        return redirect(url_for('auth.login'))
    try:
        token = oauth.google.authorize_access_token()
    except Exception:
        flash(
            'Inloggen met Google mislukt. Probeer het opnieuw.' if lang == 'nl'
            else 'Google sign-in failed. Please try again.',
            'error'
        )
        return redirect(url_for('auth.login'))

    userinfo = token.get('userinfo')
    if not userinfo:
        flash(
            'Geen gebruikersgegevens ontvangen van Google.' if lang == 'nl'
            else 'No user information received from Google.',
            'error'
        )
        return redirect(url_for('auth.login'))

    email = userinfo.get('email', '').lower().strip()
    google_id = userinfo.get('sub', '')
    first_name = userinfo.get('given_name', '')
    last_name = userinfo.get('family_name', '')

    if not email:
        flash(
            'Geen e-mailadres ontvangen van Google.' if lang == 'nl'
            else 'No email address received from Google.',
            'error'
        )
        return redirect(url_for('auth.login'))

    user = User.query.filter_by(email=email).first()
    if user:
        if not user.google_id:
            user.google_id = google_id
            db.session.commit()
        session.permanent = True
        login_user(user)
        if not user.linkedin_url:
            return redirect(url_for('auth.linkedin_aanvullen'))
        return redirect(url_for('main.index'))

    pw_hash = bcrypt.generate_password_hash(secrets.token_hex(32)).decode('utf-8')
    reg_lang = lang
    user = User(
        first_name=first_name or email.split('@')[0],
        last_name=last_name or '',
        email=email,
        password_hash=pw_hash,
        google_id=google_id,
        verified=True,
        auto_translate=True if reg_lang == 'en' else None,
    )
    db.session.add(user)
    db.session.commit()
    session.permanent = True
    login_user(user)
    return redirect(url_for('auth.linkedin_aanvullen'))


@auth.route('/profiel/linkedin-aanvullen', methods=['GET', 'POST'])
@login_required
def linkedin_aanvullen():
    if current_user.linkedin_url:
        return redirect(url_for('main.index'))
    lang = session.get('lang', 'nl')
    form = LinkedInAanvullenForm()
    error = False
    if form.validate_on_submit():
        linkedin_url = form.linkedin_url.data.strip()
        parsed = urlparse(linkedin_url)
        if parsed.scheme != 'https' or parsed.netloc not in ('www.linkedin.com', 'linkedin.com'):
            error = True
        else:
            current_user.linkedin_url = linkedin_url
            db.session.commit()
            flash(
                'Welkom bij Global Union Forum!' if lang == 'nl'
                else 'Welcome to Global Union Forum!',
                'success'
            )
            return redirect(url_for('main.index'))
    elif form.is_submitted():
        error = True
    return render_template('linkedin_aanvullen.html', error=error, form=form)


@auth.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("main.index"))


@auth.app_context_processor
def _inject_verify_resend_form():
    return {'verify_resend_form': DeleteAccountForm()}


@auth.route("/verify/<token>")
@limiter.limit("10 per hour")
def verify_email(token):
    s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    lang = session.get('lang', 'nl')
    try:
        email = s.loads(token, salt='email-verify', max_age=86400)
    except SignatureExpired:
        flash(
            "Deze verificatielink is verlopen. Vraag een nieuwe aan." if lang == 'nl'
            else "This verification link has expired. Request a new one.",
            "error"
        )
        return redirect(url_for('main.index'))
    except BadSignature:
        flash(
            "Ongeldige verificatielink." if lang == 'nl' else "Invalid verification link.",
            "error"
        )
        return redirect(url_for('main.index'))

    user = User.query.filter_by(email=email).first_or_404()
    was_unverified = not user.verified
    if was_unverified:
        user.verified = True
        db.session.commit()
        if not current_user.is_authenticated:
            login_user(user)
        session['modal'] = 'verified'
    return redirect(url_for('main.index'))


@auth.route("/verify/resend", methods=["POST"])
@login_required
@limiter.limit("3 per hour")
def verify_resend():
    form = DeleteAccountForm()
    if not form.validate_on_submit():
        abort(400)
    lang = session.get('lang', 'nl')
    if current_user.verified:
        return redirect(url_for('main.index'))
    _send_verify_email(current_user, lang, current_app.config['SECRET_KEY'])
    flash(
        "Verificatiemail opnieuw verzonden — check je inbox." if lang == 'nl'
        else "Verification email resent — check your inbox.",
        "success"
    )
    return redirect(request.referrer or url_for('main.index'))


@auth.route("/account/verwijderen", methods=["POST"])
@login_required
def account_verwijderen():
    form = DeleteAccountForm()
    if not form.validate_on_submit():
        abort(400)
    lang = session.get('lang', 'nl')
    if current_user.is_admin:
        flash(
            "Adminaccounts kunnen niet worden verwijderd." if lang == 'nl'
            else "Admin accounts cannot be deleted.",
            "error"
        )
        return redirect(url_for("profile.profile"))

    user_id = current_user.id

    try:
        PostLike.query.filter_by(user_id=user_id).delete()
        user_post_ids = [p.id for p in Post.query.filter_by(user_id=user_id).with_entities(Post.id).all()]
        if user_post_ids:
            PostLike.query.filter(PostLike.post_id.in_(user_post_ids)).delete(synchronize_session=False)
            Post.query.filter(Post.parent_id.in_(user_post_ids)).update(
                {Post.parent_id: None}, synchronize_session=False
            )
            Post.query.filter_by(user_id=user_id).delete(synchronize_session=False)
        user = db.session.get(User, user_id)
        db.session.delete(user)
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash(
            "Er is een fout opgetreden. Probeer het opnieuw." if lang == 'nl'
            else "An error occurred. Please try again.",
            "error"
        )
        return redirect(url_for("profile.profile"))

    logout_user()
    flash(
        "Je account is verwijderd." if lang == 'nl' else "Your account has been deleted.",
        "success"
    )
    return redirect(url_for("main.index"))
