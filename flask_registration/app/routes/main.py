import json
import logging
from pathlib import Path

from flask import Blueprint, render_template, redirect, url_for, request, session, abort, flash
from flask_babel import get_locale, gettext, lazy_gettext
from flask_login import current_user, login_required

from app import db, i18n
from app.forms import RulesAcceptForm
from app.utils import safe_next

main = Blueprint("main", __name__)

_log = logging.getLogger('babel_locale')

# Landenlijst: eenmalig ingeladen bij module-import (statische referentiedata,
# ~183 rijen) i.p.v. per request van schijf te lezen. Multilingual step 6e:
# per land de ISO 3166-code; de naam komt per taal uit Babel (met een paar
# vaste namen in "names" waar de site een eigen naam gebruikt), het niveau en
# uitnodigen zijn codes met een gettext-label, remarks per taal met terugval
# op Engels.
_LANDEN_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "landenlijst_data.json"
# The level is a fixed vocabulary: the code is also the CSS modifier.
LEVEL_LABELS = {
    "meets": lazy_gettext("Meets standard"),
    "above": lazy_gettext("Above minimum"),
    "below": lazy_gettext("Below minimum"),
    "insufficient": lazy_gettext("Insufficient data"),
}
INVITE_LABELS = {
    "yes": lazy_gettext("Yes"),
    "no": lazy_gettext("No"),
    "na": lazy_gettext("n/a"),
    "separate": lazy_gettext("separate status"),
}
# The downloads per language (Excel, PDF), with the English files as the
# fallback for a language without its own.
_LANDEN_DOWNLOADS = (
    ("nl", "downloads/bijlage_a_landenoverzicht_NL.xlsx", "downloads/landenlijst.pdf"),
    ("en", "downloads/appendix_a_country_overview_EN.xlsx", "downloads/country-list.pdf"),
)


def landen_downloads(lang):
    """(xlsx, pdf) static paths for `lang`, else the English ones."""
    table = {code: (xlsx, pdf) for code, xlsx, pdf in _LANDEN_DOWNLOADS}
    return table.get(lang) or table[i18n.source_language()]


def country_name(row, lang):
    """The country's name in `lang`: the site's own name for that language
    (every country has one in nl and en), else Babel's, else the site's
    English name. A politically sensitive territory ("sensitive" in the
    data: PS, HK, TW, XK) never falls back to Babel: it has an own name in
    every enabled language (a test guards that) and otherwise shows the
    site's English name."""
    from babel import Locale, UnknownLocaleError
    names = row.get("names") or {}
    own = names.get(lang)
    if own:
        return own
    if row.get("sensitive"):
        return names["en"]
    try:
        name = Locale.parse(lang).territories.get(row["iso"])
    except (UnknownLocaleError, ValueError, TypeError):
        name = None
    return name or names.get("en") or Locale("en").territories.get(row["iso"], row["iso"])


def country_remark(row, lang):
    remarks = row.get("remarks") or {}
    return remarks.get(lang) or remarks.get("en")
# Numerieke rangorde voor het sorteren van de Niveau-kolom (wijziging 3 op de
# landenlijst) — laag naar hoog, analoog aan level_class hierboven.
_LEVEL_SORT_RANK = {"insufficient": 0, "below": 1, "above": 2, "meets": 3}
# Drempelwaarden per index — geverifieerd tegen alle 183 landen, reproduceert
# het niveau exact via AND-logica over beide niveaus. Hier gebruikt voor de
# 3-standen per-index-indicatie: elke index afzonderlijk als 'below' (onder
# Boven-minimum), 'above' (Boven minimum, maar niet Op niveau) of 'meets'
# (Op niveau) geclassificeerd, los van het gecombineerde niveau-oordeel.
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
        row["level_class"] = row["niveau"] if row["niveau"] in LEVEL_LABELS else "below"
        row["level_sort"] = _LEVEL_SORT_RANK[row["level_class"]]
        row["di_level"] = _index_level_class(row["di"], "di")
        row["cpi_level"] = _index_level_class(row["cpi"], "cpi")
        row["hri_level"] = _index_level_class(row["hri"], "hri")
    return rows


_LANDEN_DATA = _load_landen_data()


@main.route("/lang/<code>")
def set_lang(code):
    if i18n.is_enabled(code):
        session['lang'] = code
        session.permanent = True
        i18n.remember(code)   # the cookie keeps the choice after the session
        if current_user.is_authenticated:
            current_user.auto_translate = True if i18n.auto_translate_default(code) else None
            current_user.lang = code  # keep the stored language preference in sync
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
    lang = i18n.normalize(str(get_locale()))
    countries = [dict(row, name=country_name(row, lang), remark=country_remark(row, lang),
                      level_label=LEVEL_LABELS[row["level_class"]]) for row in _LANDEN_DATA]
    xlsx, pdf = landen_downloads(lang)
    return render_template("landenlijst.html", countries=countries, download_xlsx=xlsx, download_pdf=pdf)


@main.route("/about")
def about():
    return render_template("about.html")


@main.route("/initiatiefnemer")
def initiatiefnemer():
    return render_template("initiatiefnemer.html")


@main.route("/motie_d66")
def motie_d66():
    return render_template("motie_d66.html")


@main.route("/success")
def success():
    name = request.args.get("name", "")
    if not name:
        return redirect(url_for("social.aanmelden"))
    return render_template("success.html", name=name)


@main.route("/privacy")
def privacy():
    return render_template("privacy.html")


@main.route("/faq")
def faq():
    from app import faq as faq_content
    return render_template("faq.html", themes=faq_content.themes())


@main.route("/spelregels")
def spelregels():
    """Publieke, tweetalige pagina met de spelregels: de gedragsregels (waarmee
    je akkoord gaat) en de kwaliteitsrichtlijnen (informatie). Ingelogd
    zonder akkoord: hier staat ook de akkoordknop (zie spelregels_accepteren)."""
    return render_template("spelregels.html", form=RulesAcceptForm(),
                           next=safe_next(request.args.get("next"), ""))


@main.route("/spelregels/accepteren", methods=["POST"])
@login_required
def spelregels_accepteren():
    """Vangnet voor wie zich registreerde zonder akkoord (of via een pad
    zonder vinkje): akkoord geven maakt de gebruiker Contributor."""
    if not RulesAcceptForm().validate_on_submit():
        abort(400)
    current_user.accept_rules()
    db.session.commit()
    flash(
        gettext('Thank you! You can now join the conversation.'),
        "success"
    )
    return redirect(safe_next(request.form.get("next"), url_for("forum.forum")))
