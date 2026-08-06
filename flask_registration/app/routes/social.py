from flask import Blueprint, render_template, redirect, url_for, flash, request, session, current_app, jsonify, abort
from flask_login import current_user

from sqlalchemy.exc import IntegrityError

from app import db, bcrypt, limiter
from app.forms import FeedbackForm, InvitationForm
from app.mail import send_email
from app.models import User
from app.utils import (_password_strong, _send_verify_email, _stash_form_state, _pop_form_state,
                       is_username_valid_format, is_username_blacklisted, is_username_available)

social = Blueprint("social", __name__)


@social.app_context_processor
def _inject_social_forms():
    return {'feedback_form': FeedbackForm(), 'invitation_form': InvitationForm()}


@social.route("/uitnodiging", methods=["POST"])
@limiter.limit("5 per hour")
def uitnodiging():
    lang = request.form.get('lang', session.get('lang', 'nl'))
    form = InvitationForm()
    if not form.validate_on_submit():
        if form.csrf_token.errors:
            abort(400)
        flash(
            "Vul een geldig e-mailadres in." if lang == 'nl' else "Please enter a valid email address.",
            "error"
        )
        return redirect(url_for("main.index"))

    invite_email = form.invite_email.data.strip()
    invite_message = (form.invite_message.data or '').strip()

    register_url = url_for('social.aanmelden', _external=True)
    if current_user.is_authenticated:
        sender_name = f"{current_user.first_name} {current_user.last_name}"
    else:
        sender_name = "Gerhard Top"

    if lang == 'en':
        subject = "Invitation: join the Global Union Forum conversation"
        body = (
            f"Hi,\n\n"
            f"{sender_name} invites you to join Global Union Forum — a think tank exploring "
            f"whether the success of the European Union can be replicated worldwide.\n\n"
        )
        if invite_message:
            body += f'Personal message from {sender_name}:\n"{invite_message}"\n\n'
        body += f"Create a free account here:\n{register_url}\n\nGlobal Union Forum"
    else:
        subject = "Uitnodiging: doe mee aan het Global Union Forum gesprek"
        body = (
            f"Hoi,\n\n"
            f"{sender_name} nodigt je uit om mee te denken bij Global Union Forum — een denktank "
            f"die onderzoekt of het succes van de Europese Unie wereldwijd herhaald kan worden.\n\n"
        )
        if invite_message:
            body += f'Persoonlijk bericht van {sender_name}:\n"{invite_message}"\n\n'
        body += f"Maak gratis een account aan:\n{register_url}\n\nGlobal Union Forum"

    html = body.replace("\n", "<br>")
    ok = send_email(invite_email, subject, html)
    if ok:
        flash(
            "Uitnodiging verstuurd!" if lang == 'nl' else "Invitation sent!",
            "success"
        )
    else:
        flash(
            "Er is iets misgegaan. Probeer het later opnieuw."
            if lang == 'nl' else "Something went wrong. Please try again later.",
            "error"
        )
    return redirect(url_for("main.index"))


@social.route("/feedback", methods=["POST"])
@limiter.limit("5 per hour")
def feedback():
    form = FeedbackForm()
    if not form.validate_on_submit():
        abort(400)

    lang = request.form.get('lang', session.get('lang', 'nl'))

    # Honeypot: bots vullen dit verborgen veld vaak automatisch in, mensen
    # zien het nooit. Doe stil alsof het gelukt is — geen mail versturen,
    # geen foutmelding — zodat bots niet leren dat ze gefilterd worden.
    if request.form.get('website', '').strip():
        flash(
            "Bedankt voor je feedback!" if lang == 'nl' else "Thanks for your feedback!",
            "success"
        )
        return redirect(url_for("main.index"))

    if current_user.is_authenticated:
        name = f"{current_user.first_name} {current_user.last_name}".strip()
        email = current_user.email
    else:
        name = request.form.get('feedback_name', '').strip()
        email = request.form.get('feedback_email', '').strip()
    message = request.form.get('feedback_message', '').strip()

    if not (name and email and message):
        flash(
            "Vul alle velden in." if lang == 'nl' else "Please fill in all fields.",
            "error"
        )
        return redirect(url_for("main.index"))

    subject = f"Feedback van {name} via Global Union Forum"
    html = (
        f"<p><strong>Naam:</strong> {name}</p>"
        f"<p><strong>E-mail:</strong> {email}</p>"
        f"<p><strong>Bericht:</strong><br>{message.replace(chr(10), '<br>')}</p>"
    )
    ok = send_email("feedback@globalunionforum.org", subject, html)
    if ok:
        flash(
            "Bedankt voor je feedback!" if lang == 'nl' else "Thanks for your feedback!",
            "success"
        )
    else:
        flash(
            "Er is iets misgegaan. Probeer het later opnieuw."
            if lang == 'nl' else "Something went wrong. Please try again later.",
            "error"
        )
    return redirect(url_for("main.index"))


@social.route("/aanmelden/username-check", methods=["POST"])
@limiter.limit("30 per minute")
def username_check():
    """
    Blur-validatie voor het username-veld op /aanmelden. Zelfde regels als
    de server-side check bij submit (format/blacklist/uniciteit) — dit is
    puur een snellere, eerdere terugkoppeling, geen vervanging daarvan.
    """
    username = (request.form.get("username") or "").strip()
    if not is_username_valid_format(username):
        return jsonify({"ok": False, "reason": "format"})
    if is_username_blacklisted(username):
        return jsonify({"ok": False, "reason": "blacklisted"})
    if not is_username_available(username):
        return jsonify({"ok": False, "reason": "taken"})
    return jsonify({"ok": True})


@social.route("/aanmelden", methods=["GET", "POST"])
@limiter.limit("3 per hour", methods=["POST"])
def aanmelden():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    session.permanent = True

    if request.method == "POST":
        errors = {}
        form_data = {"username": "", "first_name": "", "last_name": "", "email": "", "linkedin_url": ""}
        form_data["username"]     = request.form.get("username", "").strip()
        form_data["first_name"]   = request.form.get("first_name", "").strip()
        form_data["last_name"]    = request.form.get("last_name", "").strip()
        form_data["email"]        = request.form.get("email", "").strip().lower()
        form_data["linkedin_url"] = request.form.get("linkedin_url", "").strip()
        password         = request.form.get("password", "")
        password_confirm = request.form.get("password_confirm", "")

        # Username: formaat, blacklist en uniciteit zijn drie losse, elk
        # apart te tonen problemen — vandaar losse vlaggen i.p.v. één
        # generieke 'username'-fout (zelfde principe als email/email_in_use).
        if not is_username_valid_format(form_data["username"]):
            errors["username_format"] = True
        elif is_username_blacklisted(form_data["username"]):
            errors["username_blacklisted"] = True
        elif not is_username_available(form_data["username"]):
            errors["username_taken"] = True
        # Voornaam/achternaam zijn een koppel: één zonder de ander is fout,
        # beide leeg of beide gevuld is prima (velden zijn optioneel).
        if bool(form_data["first_name"]) != bool(form_data["last_name"]):
            errors["name_pair"] = True
        if not form_data["email"] or "@" not in form_data["email"]:
            errors["email"] = True
        elif User.query.filter_by(email=form_data["email"]).first():
            errors["email_in_use"] = True
        # Onafhankelijke checks (geen elif): een te zwak wachtwoord en een
        # mismatch zijn twee losse problemen die allebei getoond moeten
        # worden, niet alleen de eerst-gevonden.
        if not _password_strong(password):
            errors["password"] = True
        if password != password_confirm:
            errors["password_mismatch"] = True
        if form_data["linkedin_url"] and not form_data["linkedin_url"].startswith("https://www.linkedin.com/"):
            errors["linkedin_url"] = True

        if not errors:
            password_hash = bcrypt.generate_password_hash(password).decode("utf-8")
            reg_lang = session.get('lang', 'nl')
            user = User(
                username=form_data["username"],
                first_name=form_data["first_name"] or None,
                last_name=form_data["last_name"] or None,
                email=form_data["email"],
                password_hash=password_hash,
                linkedin_url=form_data["linkedin_url"] or None,
                auto_translate=True if reg_lang == 'en' else None,
            )
            db.session.add(user)
            try:
                db.session.commit()
            except IntegrityError:
                # Race condition: iemand anders claimde deze username tussen
                # de blur-check/submit-check en deze commit. De unieke index
                # is de uiteindelijke waarheid — nette foutmelding i.p.v. 500.
                db.session.rollback()
                errors["username_taken"] = True
                _stash_form_state("aanmelden", errors, form_data)
                return redirect(url_for("social.aanmelden"), code=303)
            _send_verify_email(user, session.get('lang', 'nl'), current_app.config['SECRET_KEY'])
            session['modal'] = 'email_sent'
            return redirect(url_for("main.index"))

        # PRG: fouten + old input (nooit het wachtwoord) één GET lang bewaren
        # en redirecten (303), zodat 'back'/'refresh' geen POST-resubmit-
        # melding meer geeft. De GET hieronder popt dit weer.
        _stash_form_state("aanmelden", errors, form_data)
        return redirect(url_for("social.aanmelden"), code=303)

    errors, stashed_data = _pop_form_state("aanmelden")
    form_data = {"username": "", "first_name": "", "last_name": "", "email": "", "linkedin_url": ""}
    form_data.update(stashed_data)
    return render_template("aanmelden/aanmelden.html", form_data=form_data, errors=errors)
