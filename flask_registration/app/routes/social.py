from flask import Blueprint, render_template, redirect, url_for, flash, request, session, current_app
from flask_login import current_user
from flask_babel import gettext as _

from app import db, bcrypt, limiter
from app.mail import send_email
from app.models import User
from app.utils import _password_strong, _send_verify_email, _stash_form_state, _pop_form_state

social = Blueprint("social", __name__)


@social.route("/uitnodiging", methods=["POST"])
def uitnodiging():
    lang = request.form.get('lang', session.get('lang', 'nl'))
    invite_email = request.form.get('invite_email', '').strip()
    invite_message = request.form.get('invite_message', '').strip()

    if not invite_email:
        flash(
            "Vul een e-mailadres in." if lang == 'nl' else "Please enter an email address.",
            "error"
        )
        return redirect(url_for("main.index"))

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
def feedback():
    lang = request.form.get('lang', session.get('lang', 'nl'))
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


@social.route("/aanmelden", methods=["GET", "POST"])
@limiter.limit("3 per hour", methods=["POST"])
def aanmelden():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    session.permanent = True

    if request.method == "POST":
        errors = {}
        form_data = {"first_name": "", "last_name": "", "email": "", "linkedin_url": ""}
        form_data["first_name"]   = request.form.get("first_name", "").strip()
        form_data["last_name"]    = request.form.get("last_name", "").strip()
        form_data["email"]        = request.form.get("email", "").strip().lower()
        form_data["linkedin_url"] = request.form.get("linkedin_url", "").strip()
        password         = request.form.get("password", "")
        password_confirm = request.form.get("password_confirm", "")

        if not form_data["first_name"]:
            errors["first_name"] = True
        if not form_data["last_name"]:
            errors["last_name"] = True
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
        if not form_data["linkedin_url"].startswith("https://www.linkedin.com/"):
            errors["linkedin_url"] = True

        if not errors:
            password_hash = bcrypt.generate_password_hash(password).decode("utf-8")
            reg_lang = session.get('lang', 'nl')
            user = User(
                first_name=form_data["first_name"],
                last_name=form_data["last_name"],
                email=form_data["email"],
                password_hash=password_hash,
                linkedin_url=form_data["linkedin_url"],
                auto_translate=True if reg_lang == 'en' else None,
            )
            db.session.add(user)
            db.session.commit()
            _send_verify_email(user, session.get('lang', 'nl'), current_app.config['SECRET_KEY'])
            session['modal'] = 'email_sent'
            return redirect(url_for("main.index"))

        # PRG: fouten + old input (nooit het wachtwoord) één GET lang bewaren
        # en redirecten (303), zodat 'back'/'refresh' geen POST-resubmit-
        # melding meer geeft. De GET hieronder popt dit weer.
        _stash_form_state("aanmelden", errors, form_data)
        return redirect(url_for("social.aanmelden"), code=303)

    errors, stashed_data = _pop_form_state("aanmelden")
    form_data = {"first_name": "", "last_name": "", "email": "", "linkedin_url": ""}
    form_data.update(stashed_data)
    return render_template("aanmelden/aanmelden.html", form_data=form_data, errors=errors)
