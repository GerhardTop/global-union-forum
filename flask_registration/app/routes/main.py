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
# Zelfde mechanisme als level_class hierboven: server-side afgeleid van
# uitnodigen_en, niet uit de NL-tekst gematcht.
_INVITE_CLASS_MAP = {
    "Yes": "yes",
    "No": "no",
    "separate status": "separate",
    "n/a": "na",
}
# Badge-tekst voor 'yes'/'no' wijkt af van de ruwe uitnodigen_nl/en-waarde uit
# de brondata (die blijft ongewijzigd bruikbaar als het onderliggende
# datagegeven); 'separate'/'na' behouden hun oorspronkelijke tekst.
_INVITE_LABEL_OVERRIDE_NL = {"yes": "Uitnodigen", "no": "Nog niet"}
_INVITE_LABEL_OVERRIDE_EN = {"yes": "Invite", "no": "Not yet"}

# Drempelwaarden per index — geverifieerd tegen alle 183 landen, reproduceert
# niveau_en exact via AND-logica over beide niveaus. Hier gebruikt voor de
# 3-standen per-index-indicatie: elke index afzonderlijk als 'below' (onder
# Boven-minimum), 'above' (Boven minimum, maar niet Op niveau) of 'meets'
# (Op niveau) geclassificeerd, los van het gecombineerde niveau_en-oordeel.
_INDEX_THRESHOLDS_ABOVE = {"di": 6.0, "cpi": 40, "hri": 0.70}
_INDEX_THRESHOLDS_MEETS = {"di": 8.0, "cpi": 70, "hri": 0.75}


def _index_level_class(value, key):
    if value is None:
        return "unknown"
    if value >= _INDEX_THRESHOLDS_MEETS[key]:
        return "meets"
    if value >= _INDEX_THRESHOLDS_ABOVE[key]:
        return "above"
    return "below"


def _load_landen_data():
    with open(_LANDEN_DATA_PATH, encoding="utf-8") as f:
        rows = json.load(f)
    for row in rows:
        row["level_class"] = _LEVEL_CLASS_MAP.get(row["niveau_en"], "below")
        row["invite_class"] = _INVITE_CLASS_MAP.get(row["uitnodigen_en"], "na")
        row["invite_label_nl"] = _INVITE_LABEL_OVERRIDE_NL.get(row["invite_class"], row["uitnodigen_nl"])
        row["invite_label_en"] = _INVITE_LABEL_OVERRIDE_EN.get(row["invite_class"], row["uitnodigen_en"])
        row["di_level"] = _index_level_class(row["di"], "di")
        row["cpi_level"] = _index_level_class(row["cpi"], "cpi")
        row["hri_level"] = _index_level_class(row["hri"], "hri")
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
