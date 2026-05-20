from flask import Blueprint, render_template, redirect, url_for, flash, request, session
from flask_login import login_user, logout_user, login_required, current_user

from app import db, bcrypt
from app.models import User
from app.forms import RegistrationForm, LoginForm, ChangePasswordForm
from app.translations import TRANSLATIONS

main = Blueprint("main", __name__)


def _t():
    return TRANSLATIONS[session.get('lang', 'nl')]


@main.route("/lang/<code>")
def set_lang(code):
    if code in ('nl', 'en'):
        session['lang'] = code
    return redirect(request.referrer or url_for('main.index'))


@main.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    return render_template("welcome.html")


@main.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    form = RegistrationForm()
    if form.validate_on_submit():
        existing_user = User.query.filter_by(email=form.email.data.lower()).first()
        if existing_user:
            flash(_t()['flash_email_in_use'], "error")
            return render_template("register.html", form=form)

        password_hash = bcrypt.generate_password_hash(form.password.data).decode("utf-8")
        user = User(
            first_name=form.first_name.data.strip(),
            last_name=form.last_name.data.strip(),
            email=form.email.data.lower().strip(),
            password_hash=password_hash,
        )
        db.session.add(user)
        db.session.commit()
        return redirect(url_for("main.success", name=user.first_name))

    return render_template("register.html", form=form)


@main.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.lower().strip()).first()
        if user and bcrypt.check_password_hash(user.password_hash, form.password.data):
            login_user(user)
            next_page = request.args.get("next")
            return redirect(next_page or url_for("main.dashboard"))
        flash(_t()['flash_invalid_credentials'], "error")

    return render_template("login.html", form=form)


@main.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("main.index"))


@main.route("/dashboard")
@login_required
def dashboard():
    return render_template("landing.html")


@main.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if not bcrypt.check_password_hash(current_user.password_hash, form.current_password.data):
            flash(_t()['flash_wrong_password'], "error")
        else:
            current_user.password_hash = bcrypt.generate_password_hash(
                form.new_password.data
            ).decode("utf-8")
            db.session.commit()
            flash(_t()['flash_password_changed'], "success")
            return redirect(url_for("main.profile"))

    return render_template("profile.html", form=form)


@main.route("/success")
def success():
    name = request.args.get("name", "")
    if not name:
        return redirect(url_for("main.register"))
    return render_template("success.html", name=name)
