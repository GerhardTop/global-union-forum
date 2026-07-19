import secrets

from datetime import datetime, timezone

from flask import Blueprint, render_template, redirect, url_for, flash, request, session, jsonify, current_app, abort
from urllib.parse import urlparse
from flask_login import login_user, logout_user, login_required, current_user
from flask_babel import gettext as _
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature

from sqlalchemy.exc import IntegrityError

from app import db, bcrypt, limiter, oauth
from app.forms import (LoginForm, WachtwoordVergetenForm, WachtwoordResetForm,
                       LinkedInAanvullenForm, KiesGebruikersnaamForm, DeleteAccountForm,
                       VerifyResendForm)
from app.mail import send_email
from app.models import User, Post, PostLike
from app.utils import (_make_verify_token, _send_verify_email, _password_strong,
                       _stash_form_state, _pop_form_state,
                       is_username_valid_format, is_username_blacklisted, is_username_available)

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
    ok = send_email(user.email, subject, html)
    if not ok:
        current_app.logger.error(
            f"Wachtwoord-reset e-mail naar {user.email} mislukt."
        )
    return ok


# ── Routes ───────────────────────────────────────────────────────────────────

@auth.route("/register")
def register():
    return redirect(url_for("social.aanmelden"))


@auth.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    form = LoginForm()
    login_error = False
    # Gevuld i.p.v. ingelogd wanneer credentials kloppen maar het account nog
    # niet bevestigd is (Blokker 1) — login.html toont dan een modal i.p.v.
    # de gebruiker in te loggen.
    unverified_email = None
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.lower().strip()).first()
        if user and bcrypt.check_password_hash(user.password_hash, form.password.data):
            if not user.verified:
                unverified_email = user.email
            else:
                lang = session.get('lang', 'nl')
                session.clear()
                session['lang'] = lang
                session.permanent = True
                login_user(user)
                next_page = request.args.get("next")
                parsed_next = urlparse(next_page) if next_page else None
                if not next_page or parsed_next.scheme or parsed_next.netloc:
                    next_page = url_for("main.index")
                return redirect(next_page)
        else:
            login_error = True
    return render_template("login.html", form=form, login_error=login_error,
                           unverified_email=unverified_email)


@auth.route('/wachtwoord-vergeten', methods=['GET', 'POST'])
@limiter.limit("3 per hour", methods=["POST"])
def wachtwoord_vergeten():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    lang = session.get('lang', 'nl')
    form = WachtwoordVergetenForm()
    is_xhr = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    if request.method == 'POST':
        if form.validate_on_submit():
            email = form.email.data.strip().lower()
            user = User.query.filter_by(email=email).first()
            if user:
                reset_url = url_for('auth.wachtwoord_reset', token=_make_reset_token(email), _external=True)
                mail_ok = _send_reset_email(user, reset_url, lang)
                if not mail_ok:
                    flash(
                        'E-mail versturen mislukt. Probeer het later opnieuw.' if lang == 'nl'
                        else 'Failed to send email. Please try again later.',
                        'error'
                    )
                    if is_xhr:
                        return jsonify({'ok': False})
                    return redirect(url_for('auth.wachtwoord_vergeten'), code=303)
            if is_xhr:
                return jsonify({'ok': True})
            # PRG: 'sent' is hier een eenmalig succes-signaal, geen veldfout —
            # zelfde form_state-mechanisme als de andere formulieren, alleen
            # met een ander soort payload.
            _stash_form_state("wachtwoord_vergeten", {"sent": True})
            return redirect(url_for('auth.wachtwoord_vergeten'), code=303)

        if is_xhr:
            # 422 i.p.v. de vorige (impliciete) 200 — consistent met de
            # andere XHR-paden in dit project.
            return jsonify({'ok': False, 'error': 'invalid_form'}), 422
        return redirect(url_for('auth.wachtwoord_vergeten'), code=303)

    errors, _unused = _pop_form_state("wachtwoord_vergeten")
    return render_template('wachtwoord_vergeten.html', sent=errors.get("sent", False), form=form)


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
    # issued_at van itsdangerous 2.2+ is timezone-aware; password_changed_at is naive.
    # Strip tzinfo zodat de vergelijking niet crasht.
    if user.password_changed_at and issued_at:
        issued_naive = issued_at.replace(tzinfo=None) if issued_at.tzinfo else issued_at
        if user.password_changed_at > issued_naive:
            return render_template('wachtwoord_reset.html', token_invalid=True, token=token)
    form = WachtwoordResetForm()
    # Expliciet, los van WTForms' .errors berekend — zo kan er nooit een ruwe,
    # onvertaalde validator-sleutel op het scherm belanden (zie ook profile.py
    # en social.py: dezelfde aanpak, voor identiek gedrag over alle drie de
    # wachtwoordbevestigings-formulieren).
    if form.validate_on_submit():
        pw = form.password.data
        user.password_hash = bcrypt.generate_password_hash(pw).decode('utf-8')
        user.password_changed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.session.commit()
        session.clear()
        session['lang'] = lang
        session.permanent = True
        login_user(user)
        flash(
            'Wachtwoord gewijzigd. Je bent nu ingelogd.' if lang == 'nl'
            else 'Password changed. You are now logged in.',
            'success'
        )
        return redirect(url_for('main.index'))
    elif form.is_submitted():
        pw = form.password.data or ""
        pw_confirm = form.password_confirm.data or ""
        password_weak = bool(pw and not _password_strong(pw))
        password_mismatch = bool(pw_confirm and pw != pw_confirm)
        # PRG: geen wachtwoorden bewaren (die zijn hier niet eens onderdeel van
        # form_data), alleen de foutvlaggen — dan 303 terug naar dezelfde
        # reset-URL zodat 'back'/'refresh' geen POST-resubmit-melding geeft.
        _stash_form_state("wachtwoord_reset", {
            "password_weak": password_weak,
            "password_mismatch": password_mismatch,
        })
        return redirect(url_for('auth.wachtwoord_reset', token=token), code=303)

    errors, _unused_data = _pop_form_state("wachtwoord_reset")
    return render_template('wachtwoord_reset.html', token_invalid=False, token=token,
                           form=form, password_weak=errors.get("password_weak", False),
                           password_mismatch=errors.get("password_mismatch", False))


@auth.route('/auth/google')
@limiter.limit("20 per minute",
               key_func=lambda: request.headers.get(
                   'X-Forwarded-For', request.remote_addr
               ).split(',')[0].strip())
def auth_google():
    redirect_uri = url_for('auth.auth_google_callback', _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@auth.route('/auth/google/callback')
@limiter.limit("20 per minute",
               key_func=lambda: request.headers.get(
                   'X-Forwarded-For', request.remote_addr
               ).split(',')[0].strip())
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
    except Exception as exc:
        current_app.logger.error(
            f"Google OAuth token-uitwisseling mislukt: {type(exc).__name__}: {exc}"
        )
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

    if not userinfo.get('email_verified'):
        current_app.logger.warning(
            f"Google OAuth geweigerd: e-mailadres niet geverifieerd bij Google ({email})."
        )
        flash(
            'Je Google-e-mailadres is nog niet bevestigd. Bevestig het eerst via Google.' if lang == 'nl'
            else 'Your Google email address is not verified. Please verify it with Google first.',
            'error'
        )
        return redirect(url_for('auth.login'))

    user = User.query.filter_by(email=email).first()
    if user:
        changed = False
        if not user.google_id:
            user.google_id = google_id
            changed = True
        # Blokker 2: Google's email_verified is hierboven al hard gecontroleerd
        # (regel ~239), dus een bestaand account dat via Google inlogt is per
        # definitie bevestigd. Zonder dit zou Blokker 1 (verificatie afdwingen
        # bij e-mail/wachtwoord-login) bestaande, legitieme Google-gebruikers
        # alsnog buitensluiten als hun 'verified' nog False stond.
        if not user.verified:
            user.verified = True
            changed = True
        if changed:
            db.session.commit()
        session.permanent = True
        login_user(user)
        # Blokker: bestaand account zonder linkedin gaat nog naar
        # linkedin_aanvullen — dat verandert pas in fase 2 (linkedin wordt
        # dan optioneel). Placeholder-usernames worden al wél afgevangen
        # door de before_request-hook (enforce_username_choice), dus een
        # account dat toevallig beide mist krijgt eerst de username-stap.
        if not user.linkedin_url:
            return redirect(url_for('auth.linkedin_aanvullen'))
        return redirect(url_for('main.index'))

    pw_hash = bcrypt.generate_password_hash(secrets.token_hex(32)).decode('utf-8')
    reg_lang = lang
    # Placeholder-username, zelfde 'user_'-prefixschema als de migratie-
    # backfill (_backfill_usernames) — zodat één simpele check
    # (username.startswith('user_')) overal betrouwbaar herkent dat er nog
    # een echte keuze gemaakt moet worden. Botsingskans met token_hex(6) is
    # verwaarloosbaar, maar de retry-lus maakt 'm hard in plaats van hopen.
    placeholder = f'user_{secrets.token_hex(6)}'
    while User.query.filter(db.func.lower(User.username) == placeholder.lower()).first():
        placeholder = f'user_{secrets.token_hex(6)}'
    user = User(
        username=placeholder,
        first_name=first_name or None,
        last_name=last_name or None,
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
    return redirect(url_for('auth.kies_gebruikersnaam'))


@auth.route('/profiel/linkedin-aanvullen', methods=['GET', 'POST'])
@login_required
def linkedin_aanvullen():
    if current_user.linkedin_url:
        return redirect(url_for('main.index'))
    lang = session.get('lang', 'nl')
    form = LinkedInAanvullenForm()
    if form.validate_on_submit():
        linkedin_url = form.linkedin_url.data.strip()
        parsed = urlparse(linkedin_url)
        if parsed.scheme != 'https' or parsed.netloc not in ('www.linkedin.com', 'linkedin.com'):
            _stash_form_state("linkedin_aanvullen", {"error": True})
            return redirect(url_for('auth.linkedin_aanvullen'), code=303)
        current_user.linkedin_url = linkedin_url
        db.session.commit()
        flash(
            'Welkom bij Global Union Forum!' if lang == 'nl'
            else 'Welcome to Global Union Forum!',
            'success'
        )
        return redirect(url_for('main.index'))
    elif form.is_submitted():
        _stash_form_state("linkedin_aanvullen", {"error": True})
        return redirect(url_for('auth.linkedin_aanvullen'), code=303)

    errors, _unused = _pop_form_state("linkedin_aanvullen")
    return render_template('linkedin_aanvullen.html', error=errors.get("error", False), form=form)


@auth.route('/kies-gebruikersnaam', methods=['GET', 'POST'])
@login_required
def kies_gebruikersnaam():
    """
    Verplichte stap voor accounts met een placeholder-username (het
    'user_'-prefixschema — zie _backfill_usernames() en
    auth_google_callback()). Zolang current_user.username met 'user_'
    begint, stuurt de before_request-hook (app/__init__.py) elke andere
    pagina automatisch hierheen — deze route is dus de enige uitweg.
    """
    if not (current_user.username or '').startswith('user_'):
        return redirect(url_for('main.index'))
    lang = session.get('lang', 'nl')
    form = KiesGebruikersnaamForm()
    if form.validate_on_submit():
        username = form.username.data.strip()
        if not is_username_valid_format(username):
            errors = {"username_format": True}
        elif is_username_blacklisted(username):
            errors = {"username_blacklisted": True}
        elif not is_username_available(username, exclude_user_id=current_user.id):
            errors = {"username_taken": True}
        else:
            errors = {}
        if errors:
            _stash_form_state("kies_gebruikersnaam", errors, {"username": username})
            return redirect(url_for('auth.kies_gebruikersnaam'), code=303)
        current_user.username = username
        try:
            db.session.commit()
        except IntegrityError:
            # Race condition, zelfde aanpak als /aanmelden.
            db.session.rollback()
            _stash_form_state("kies_gebruikersnaam", {"username_taken": True}, {"username": username})
            return redirect(url_for('auth.kies_gebruikersnaam'), code=303)
        flash(
            'Gebruikersnaam ingesteld!' if lang == 'nl' else 'Username set!',
            'success'
        )
        return redirect(url_for('main.index'))
    elif form.is_submitted():
        _stash_form_state("kies_gebruikersnaam", {"username_format": True}, {})
        return redirect(url_for('auth.kies_gebruikersnaam'), code=303)

    errors, stashed_data = _pop_form_state("kies_gebruikersnaam")
    form_data = {"username": stashed_data.get("username", "")}
    return render_template('kies_gebruikersnaam.html', errors=errors, form_data=form_data, form=form)


@auth.route("/logout")
@login_required
def logout():
    lang = session.get('lang', 'nl')
    logout_user()
    session.clear()
    session['lang'] = lang
    return redirect(url_for("main.index"))


@auth.app_context_processor
def _inject_verify_resend_form():
    return {'verify_resend_form': VerifyResendForm()}


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
            session.clear()
            session['lang'] = lang
            login_user(user)
        session['modal'] = 'verified'
    return redirect(url_for('main.index'))


@auth.route("/verify/resend", methods=["POST"])
@login_required
@limiter.limit("3 per hour")
def verify_resend():
    form = VerifyResendForm()
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


@auth.route("/verify/resend-onbevestigd", methods=["POST"])
@limiter.limit("3 per hour")
def verify_resend_onbevestigd():
    """
    Verificatiemail opnieuw versturen voor een gebruiker die NOG NIET is
    ingelogd — het pad vanuit de modal die login() toont wanneer credentials
    kloppen maar het account niet bevestigd is (Blokker 1). Bewust GEEN
    @login_required: 'verify_resend' hierboven werkt hier niet, want de
    gebruiker mag op dit punt per ontwerp nog niet ingelogd zijn.

    Reageert ALTIJD hetzelfde (ok: True) ongeacht of het account bestaat of
    al bevestigd is — zelfde anti-enumeration-aanpak als wachtwoord_vergeten().
    Uitsluitend bedoeld voor de AJAX-aanroep vanuit login.html; geen niet-JS
    fallback nodig (net als de 'wachtwoord vergeten'-link op diezelfde pagina).
    """
    form = VerifyResendForm()
    if not form.validate_on_submit():
        return jsonify({'ok': False}), 400
    lang = session.get('lang', 'nl')
    email = (request.form.get('email') or '').strip().lower()
    user = User.query.filter_by(email=email).first()
    if user and not user.verified:
        _send_verify_email(user, lang, current_app.config['SECRET_KEY'])
    return jsonify({'ok': True})


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
    session.clear()
    session['lang'] = lang

    flash(
        "Je account is verwijderd." if lang == 'nl' else "Your account has been deleted.",
        "success"
    )
    return redirect(url_for("main.index"))
