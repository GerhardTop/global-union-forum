from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, abort, session
from flask_login import login_required, current_user
from flask_babel import gettext, gettext as _, get_locale

from app import db, bcrypt
from app.forms import ForumActionForm
from app.utils import _password_strong, _stash_form_state, _pop_form_state, safe_next

profile_bp = Blueprint("profile", __name__)


@profile_bp.route("/dashboard")
@login_required
def dashboard():
    return render_template("landing.html")


@profile_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    from app.forms import ChangePasswordForm
    form = ChangePasswordForm()

    if request.method == "POST":
        if form.validate_on_submit():
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

    # GET (or the PRG redirect above): one shared form_state key for the
    # page; the password form and the name form (profiel_naamweergave) both
    # come back here. Which error set applies follows from the keys.
    errors, data = _pop_form_state("profile")
    return render_template(
        "profile.html", form=form,
        name_errors=errors.get("name_errors", []), name_data=data if errors.get("name_errors") else {},
        password_weak=errors.get("password_weak", False),
        password_mismatch=errors.get("password_mismatch", False),
        current_password_empty=errors.get("current_password_empty", False),
        about_me=_about_me_context(),
        **_status_context(),
    )


def _about_me_context():
    """Representativeness 1b: the questions of About me, only with
    PARTICIPANT_PROFILE on (else None: no section)."""
    from app import i18n, participants
    if not participants.enabled():
        return None
    return participants.form_context(current_user, i18n.normalize(str(get_locale())))


@profile_bp.route("/profiel/over-mij", methods=["POST"])
@login_required
def over_mij():
    """Save or clear the voluntary answers (docs/representativiteit.md).
    Without JavaScript: a plain form, back to the section."""
    from app import participants
    if not participants.enabled():
        abort(404)
    if not ForumActionForm().validate_on_submit():
        abort(400)
    # Step 1c: the card after the first vote posts here too, with next (the
    # proposal page). Only an internal path counts; else back to the profile.
    back = safe_next(request.form.get("next"), None)
    action = request.form.get("action")
    message = None
    if action == "save":
        participants.save(current_user, request.form)
        message = gettext('Saved.')
    elif action == "clear":
        participants.clear(current_user)
        # TRANSLATORS: profile, About me: the message after "Clear answers".
        message = gettext('Your answers have been cleared.')
    elif action != "skip":   # skip: nothing to save, asked_at is already set
        abort(400)
    db.session.commit()
    if back:
        # The proposal page shows the thank-you line in the vote block, once.
        if action == "save":
            from app.routes.issues import PP_SAVED
            session[PP_SAVED] = back.split("#")[0].split("?")[0]
        return redirect(back, code=303)
    if message:
        flash(message, "success")
    return redirect(url_for("profile.profile") + "#over-mij", code=303)


def _status_context():
    """Fase C2: verdiende status voor de profielpagina, met de voortgang."""
    from app import status
    count, topics = status.quality_counts(current_user.id)
    return {'quality_count': count, 'quality_topics': topics, 'thresholds': status.get_thresholds()}


NAME_MAX = 50        # users.first_name / users.last_name (String(50))
LINKEDIN_MAX = 255   # users.linkedin_url (String(255))
LINKEDIN_PREFIX = "https://www.linkedin.com/"


def _linkedin_valid(url):
    return not url or (url.startswith(LINKEDIN_PREFIX) and len(url) <= LINKEDIN_MAX)


@profile_bp.route("/profiel/naamweergave", methods=["POST"])
@login_required
def profiel_naamweergave():
    """How you appear in the forum (docs/design/profiel.html): under your
    username (the default) or under your real name, with first name, last
    name and LinkedIn in the same form. The real name needs a first and a
    last name, and the checkbox (confirm=1) when you now still appear under
    your username; who already shows the real name can change name and
    LinkedIn without it. Back to the username is always possible: name and
    LinkedIn stay stored and are not shown."""
    if not ForumActionForm().validate_on_submit():
        abort(400)
    is_xhr = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    want_real = request.form.get("show_real_name") == "1"
    first = (request.form.get("first_name") or "").strip()[:NAME_MAX]
    last = (request.form.get("last_name") or "").strip()[:NAME_MAX]
    linkedin = (request.form.get("linkedin_url") or "").strip()
    errors = []
    if want_real:
        if not first:
            errors.append("first_name")
        if not last:
            errors.append("last_name")
        if not _linkedin_valid(linkedin):
            errors.append("linkedin")
        if not current_user.shows_real_name and request.form.get("confirm") != "1":
            errors.append("not_confirmed")
    if errors:
        if is_xhr:
            return jsonify({'ok': False, 'errors': errors}), 422
        _stash_form_state("profile", {"kind": "name_display", "name_errors": errors},
                          {"show_real_name": "1", "first_name": first, "last_name": last,
                           "linkedin_url": linkedin})
        return redirect(url_for("profile.profile") + "#guNameDisplay", code=303)
    if want_real:
        current_user.first_name, current_user.last_name = first, last
        current_user.linkedin_url = linkedin or None
    current_user.show_real_name = want_real
    db.session.commit()
    if is_xhr:
        return jsonify({'ok': True, 'show_real_name': want_real})
    flash(gettext('Saved.'), "success")
    return redirect(url_for("profile.profile") + "#guNameDisplay", code=303)


@profile_bp.route("/profiel/promotie-gezien", methods=["POST"])
@login_required
def promotie_gezien():
    """Sluit de eenmalige promotiemelding (fase C2)."""
    from app import status
    if not ForumActionForm().validate_on_submit():
        abort(400)
    status.mark_promotions_seen(current_user.id)
    return redirect(safe_next(request.form.get("next"), url_for("main.index")))


@profile_bp.route("/profiel/vertaalvoorkeur", methods=["POST"])
@login_required
def profiel_vertaalvoorkeur():
    # CSRF zoals bij de forum-acties (de knop staat ook onder forumberichten).
    if not ForumActionForm().validate_on_submit():
        abort(400)
    current_user.auto_translate = request.form.get("auto_translate") == "1"
    db.session.commit()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'ok': True})
    # Alleen interne paden: anders valt de redirect terug op het profiel.
    return redirect(safe_next(request.form.get('next'), url_for("profile.profile")))


# ── Fase E, stap 5b: de dagelijkse samenvatting ──────────────────────────

@profile_bp.route("/profiel/meldingen", methods=["GET", "POST"])
@login_required
def notificaties():
    """Notification settings (docs/design/notificaties.html): per category a
    switch for in-app and for email, and the summary frequency. Saving only;
    nothing sends notifications yet."""
    from app import notifications
    from app.routes.forum import _require_csrf
    if request.method == "POST":
        _require_csrf()
        notifications.save_from_form(current_user, request.form)
        db.session.commit()
        flash(gettext('Saved'), "success")
        return redirect(url_for("profile.notificaties"))
    allowed = set(notifications.allowed_categories(current_user))
    return render_template(
        "notificaties.html",
        categories=[c for c in notifications.CATEGORIES if c[0] in allowed],
        prefs=notifications.effective_prefs(current_user),
        frequency=notifications.summary_frequency(current_user),
    )


@profile_bp.route("/meldingen")
@login_required
def meldingen():
    """Your notifications (step 3a-1), newest first, and below them the
    summaries of the proposals you follow. Opening the page marks both as
    read. The summaries themselves are rendered in the user's stored language
    (users.lang, Dutch by default), like the email."""
    from flask_babel import force_locale
    from markupsafe import Markup
    from app import digest, notifications
    from app.models import time_ago
    lang = str(get_locale() or 'nl')
    items = []
    for n in notifications.recent_notifications(current_user):
        text, url = notifications.notification_line(n, lang)
        items.append({'category': n.category, 'text': text, 'url': url, 'unread': n.read_at is None,
                      'when': time_ago(n.created_at), 'at': n.created_at})
    digests = digest.recent_for(current_user)
    existing = digest.thread_exists({i['thread_id'] for d in digests for i in d.items})
    cards = None
    if digests:
        with force_locale(digest.lang_for(current_user)):
            cards = Markup(render_template("forum/_digest_cards.html", digests=digests, existing_threads=existing))
    notifications.mark_all_read(current_user)  # also email-only rows (step 3b), so they never pile up
    if current_user.has_unread_digest:
        current_user.has_unread_digest = False
    db.session.commit()
    return render_template("meldingen.html", notification_items=items, digest_cards=cards)
