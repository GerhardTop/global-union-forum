from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, flash, request, session, jsonify
from flask_login import login_required, current_user
from flask_babel import gettext as _

from app import db, bcrypt

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
    linkedin_error = False
    form = ChangePasswordForm()

    if request.method == "POST":
        action = request.form.get("action")

        if action == "linkedin":
            linkedin_url = request.form.get("linkedin_url", "").strip()
            is_xhr = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            if not linkedin_url.startswith("https://www.linkedin.com/"):
                linkedin_error = True
                if is_xhr:
                    return jsonify({'ok': False})
            else:
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
            else:
                current_user.password_hash = bcrypt.generate_password_hash(
                    form.new_password.data
                ).decode("utf-8")
                current_user.password_changed_at = datetime.utcnow()
                db.session.commit()
                flash(_('Password changed successfully.'), "success")
                return redirect(url_for("profile.profile"))
        elif form.is_submitted():
            for field in (form.current_password, form.new_password, form.confirm_new_password):
                if field.errors:
                    flash(field.errors[0], "error")
                    break

    return render_template("profile.html", form=form, linkedin_error=linkedin_error)


@profile_bp.route("/profiel/vertaalvoorkeur", methods=["POST"])
@login_required
def profiel_vertaalvoorkeur():
    current_user.auto_translate = request.form.get("auto_translate") == "1"
    db.session.commit()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'ok': True})
    next_url = request.form.get('next') or url_for("profile.profile")
    return redirect(next_url)
