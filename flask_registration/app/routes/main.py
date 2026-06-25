import logging

from flask import Blueprint, render_template, redirect, url_for, request, session
from flask_login import current_user

from app import db

main = Blueprint("main", __name__)

_log = logging.getLogger('babel_locale')


@main.route("/lang/<code>")
def set_lang(code):
    if code in ('nl', 'en'):
        session['lang'] = code
        session.permanent = True
        if current_user.is_authenticated:
            current_user.auto_translate = True if code == 'en' else None
            db.session.commit()
    return redirect(request.referrer or url_for('main.index'))


@main.route("/")
def index():
    modal = session.pop('modal', None)
    return render_template("index.html", modal=modal)


@main.route("/manifest")
def manifest():
    return render_template("manifest.html")


@main.route("/about")
def about():
    return render_template("about.html")


@main.route("/success")
def success():
    name = request.args.get("name", "")
    if not name:
        return redirect(url_for("social.aanmelden"))
    return render_template("success.html", name=name)


@main.route("/privacy")
def privacy():
    return render_template("privacy.html")
