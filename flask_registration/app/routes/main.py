import json
import logging
from pathlib import Path

from flask import Blueprint, render_template, redirect, url_for, request, session
from flask_login import current_user

from app import db

main = Blueprint("main", __name__)

_log = logging.getLogger('babel_locale')

# Landenlijst: eenmalig ingeladen bij module-import (statische referentiedata,
# ~183 rijen) i.p.v. per request van schijf te lezen. level_class wordt hier
# server-side afgeleid van niveau_en (stabieler dan op niveau_nl matchen) zodat
# de template puur op CSS-modifier-klasse kan renderen, geen stringvergelijking
# per rij in Jinja.
_LANDEN_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "landenlijst_data.json"
_LEVEL_CLASS_MAP = {
    "Meets standard": "meets",
    "Above minimum": "above",
    "Below minimum": "below",
    "Insufficient data": "insufficient",
}


def _load_landen_data():
    with open(_LANDEN_DATA_PATH, encoding="utf-8") as f:
        rows = json.load(f)
    for row in rows:
        row["level_class"] = _LEVEL_CLASS_MAP.get(row["niveau_en"], "below")
    return rows


_LANDEN_DATA = _load_landen_data()


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


@main.route("/manifest/landen")
def landenlijst():
    return render_template("landenlijst.html", countries=_LANDEN_DATA)


@main.route("/about")
def about():
    return render_template("about.html")


@main.route("/initiatiefnemer")
def initiatiefnemer():
    return render_template("initiatiefnemer.html")


@main.route("/success")
def success():
    name = request.args.get("name", "")
    if not name:
        return redirect(url_for("social.aanmelden"))
    return render_template("success.html", name=name)


@main.route("/privacy")
def privacy():
    return render_template("privacy.html")
