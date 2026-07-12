from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, flash, request, session, jsonify
from flask_login import login_required, current_user
from flask_babel import gettext as _

from app import db, bcrypt
from app.utils import _password_strong, _stash_form_state, _pop_form_state

profile_bp = Blueprint("profile", __name__)


@profile_bp.route("/dashboard")
@login_required
def dashboard():
    return render_template("landing.html")


@profile_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    from app.forms import ChangePasswordForm
    lang = session.get('lang', 'nl')
    form = ChangePasswordForm()

    if request.method == "POST":
        action = request.form.get("action")
        is_xhr = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

        if action == "linkedin":
            linkedin_url = request.form.get("linkedin_url", "").strip()
            if not linkedin_url.startswith("https://www.linkedin.com/"):
                if is_xhr:
                    # XHR krijgt nooit een redirect — 422 zodat de JS-fetch-
                    # afhandeling (zie profile.html) dit als fout herkent.
                    return jsonify({'ok': False}), 422
                # Non-AJAX fallback (JS uitgeschakeld): zelfde PRG-patroon als
                # de andere formulieren, één gedeelde form_state-sleutel voor
                # deze hele route (zie 'kind' hieronder bij het poppen).
                _stash_form_state("profile", {"kind": "linkedin", "linkedin_error": True})
                return redirect(url_for("profile.profile"), code=303)
            current_user.linkedin_url = linkedin_url
            db.session.commit()
            if is_xhr:
                return jsonify({'ok': True, 'linkedin_url': linkedin_url})
            flash(
                "LinkedIn profiel opgeslagen." if lang == 'nl' else "LinkedIn profile saved.",
                "success"
            )
            return redirect(url_for("profile.profile"))

        elif form.validate_on_submit():
            if not bcrypt.check_password_hash(current_user.password_hash, form.current_password.data):
                flash(_('Current password is incorrect.'), "error")
                return redirect(url_for("profile.profile"))
            current_user.password_hash = bcrypt.generate_password_hash(
                form.new_password.data
            ).decode("utf-8")
            current_user.password_changed_at = datetime.utcnow()
            db.session.commit()
            flash(_('Password changed successfully.'), "success")
            return redirect(url_for("profile.profile"))

        elif form.is_submitted():
            # Expliciet, los van WTForms' .errors berekend — zelfde aanpak als
            # auth.py/wachtwoord_reset() en social.py/aanmelden(), zodat er
            # nooit een ruwe validator-sleutel op het scherm kan belanden.
            # novalidate staat op ELK formulier in dit project (zie memory) —
            # 'required' wordt daardoor nooit native afgedwongen, dus een leeg
            # current_password is gewoon bereikbaar via een normale klik op
            # "Opslaan", geen exotisch geval.
            new_pw = form.new_password.data or ""
            confirm_pw = form.confirm_new_password.data or ""
            _stash_form_state("profile", {
                "kind": "password",
                "current_password_empty": not form.current_password.data,
                "password_weak": bool(new_pw and not _password_strong(new_pw)),
                "password_mismatch": bool(confirm_pw and new_pw != confirm_pw),
            })
            return redirect(url_for("profile.profile"), code=303)

    # GET (of de PRG-redirect hierboven): één gedeelde form_state-sleutel voor
    # de hele route, want linkedin- en wachtwoord-formulier posten allebei
    # naar dezelfde URL. Welke van de twee foutsets van toepassing is, volgt
    # vanzelf uit welke keys er in `errors` zitten — de andere blijven op hun
    # default (False) staan.
    errors, _unused = _pop_form_state("profile")
    return render_template(
        "profile.html", form=form,
        linkedin_error=errors.get("linkedin_error", False),
        password_weak=errors.get("password_weak", False),
        password_mismatch=errors.get("password_mismatch", False),
        current_password_empty=errors.get("current_password_empty", False),
    )


@profile_bp.route("/profiel/vertaalvoorkeur", methods=["POST"])
@login_required
def profiel_vertaalvoorkeur():
    current_user.auto_translate = request.form.get("auto_translate") == "1"
    db.session.commit()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'ok': True})
    next_url = request.form.get('next') or url_for("profile.profile")
    return redirect(next_url)
