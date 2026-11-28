"""
Events step 1c (docs/events.md, K3, K4, K5, K7, K13): making and managing an
event. Only with EVENTS on; otherwise every route here is a 404.

/events/nieuw              the form "New event"
/e/<code>/instellingen     the event settings: the same form, filled in, and
                           the issues of the event with Up and Down
/e/<code>/volgorde         POST: move an issue one place up or down

Step 1d (K2, K2b, K10):
/e/<code>                  the event page, for everyone with the link
/e?code=...                the field "Do you have an event code?"
/e/<code>/qr.svg, qr.png   the QR code of the link (made here, no network)
/e/<code>/nieuwe-link      a new code; the old link stops working
/e/<code>/delen            POST: share with everyone, not now, or undo
"""
from datetime import timedelta

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for
from flask_babel import gettext
from flask_login import current_user, login_required

from app import content_i18n, db, events, i18n, issues, limiter
from app.routes.forum import _require_csrf, _rules_first_redirect, refuse_if_suspended
from app.utils import _pop_form_state, _stash_form_state, real_client_ip

events_bp = Blueprint('events', __name__)


@events_bp.before_request
def _only_when_enabled():
    if not events.enabled():
        abort(404)
    return refuse_if_suspended()


def _form_data(form):
    data = {k: form.get(k, '') for k in events.FORM_FIELDS}
    data.update({k: form.getlist(k) for k in events.FORM_LISTS})
    return data


def _data_of(event):
    """The settings form filled in from the stored event (local times)."""
    days = [(issues.to_local(d.starts_at), issues.to_local(d.ends_at)) for d in event.days]
    return {
        'name': event.name, 'note': event.note or '', 'format': event.format,
        'location': event.location or '', 'online_url': event.online_url or '',
        'online_between': '1' if event.online_between else '',
        'open_for_issues': '1' if event.open_for_issues else '',
        'everyone_votes': '1' if event.everyone_votes else '',
        'event_day': [b.date().isoformat() for b, _e in days] or [''],
        'event_from': [b.strftime('%H:%M') for b, _e in days] or ['09:00'],
        # An end at 00:00 belongs to the day before: shown as 24:00 would not
        # parse, so the end of the day is 23:59.
        'event_to': [(e - timedelta(minutes=1) if e.date() > b.date() else e).strftime('%H:%M')
                     for b, e in days] or ['17:00'],
    }


def _render(event, errors, data):
    from app import status
    from app.routes.issues import _inject_phase_labels
    rows = events._ordered(event) if event is not None else []
    if event is not None:
        content_i18n.preload([event], 'name', 'note')
        content_i18n.preload(rows, 'title')
    return render_template('events/form.html', event=event, errors=errors or {}, form_data=data or {},
                           fresh=not data, rows=rows, locked=events.votes_locked(event),
                           voter_min=status.get_thresholds()['voter_min'],
                           event_days_max=issues.EVENT_DAYS_MAX, can_share=events.can_share(event, current_user)
                           if event is not None else False, **_inject_phase_labels())


@events_bp.route('/events/nieuw', methods=['GET', 'POST'])
@login_required
def new():
    if not events.can_create(current_user):
        return _rules_first_redirect()
    if request.method == 'POST':
        _require_csrf()
        values, errors = events.parse_form(request.form)
        if errors:
            _stash_form_state('event_new', errors, _form_data(request.form))
            return redirect(url_for('events.new'), code=303)
        event = events.create(values, current_user, lang=i18n.current())
        db.session.commit()
        # TRANSLATORS: message after making a new event.
        flash(gettext('Event created.'), 'success')
        return redirect(url_for('events.page', code=event.code), code=303)
    errors, data = _pop_form_state('event_new')
    return _render(None, errors, data)


def _managed_event(code):
    event = events.by_code(code)
    if event is None:
        abort(404)
    if not events.can_manage(event, current_user):
        abort(403)
    return event


@events_bp.route('/e/<code>/instellingen', methods=['GET', 'POST'])
@login_required
def settings(code):
    event = _managed_event(code)
    key = f'event_settings_{event.id}'
    if request.method == 'POST':
        _require_csrf()
        values, errors = events.parse_form(request.form)
        if errors:
            _stash_form_state(key, errors, _form_data(request.form))
            return redirect(url_for('events.settings', code=event.code), code=303)
        events.update(event, values)
        db.session.commit()
        # TRANSLATORS: message after saving the settings of an event.
        flash(gettext('Event saved.'), 'success')
        return redirect(url_for('events.settings', code=event.code), code=303)
    errors, data = _pop_form_state(key)
    return _render(event, errors, data or _data_of(event))


@events_bp.route('/e/<code>/volgorde', methods=['POST'])
@login_required
def order(code):
    event = _managed_event(code)
    _require_csrf()
    try:
        issue_id = int(request.form.get('issue_id') or 0)
    except ValueError:
        abort(400)
    if not events.move(event, issue_id, request.form.get('direction')):
        abort(400)
    db.session.commit()
    return redirect(url_for('events.settings', code=event.code) + '#vraagstukken', code=303)


# ── Step 1d: the event page ──────────────────────────────────────────────

def _gone():
    """An unknown code (also an old one after a new link): 404 with K2's message."""
    # TRANSLATORS: shown for an event link or code that does not exist (any more).
    message = gettext('This link no longer works. Ask the organiser for the new link.')
    return render_template('events/notice.html', message=message), 404


@events_bp.route('/e/verlaten')
def leave():
    """K9: "See the issues outside this event": the session forgets the
    current event (the access to its issues stays) and Have your say opens."""
    events.leave()
    return redirect(url_for('issues.index'))


@events_bp.route('/e/<code>')
def page(code):
    from app import issue_lifecycle  # noqa: F401  (participants in the counts)
    from app.models import Issue
    from app.routes.issues import _inject_phase_labels
    from sqlalchemy.orm import selectinload
    event = events.by_code(code)
    if event is None:
        return _gone()
    if event.code != code:
        return redirect(url_for('events.page', code=event.code))
    events.grant(event)
    if not events.is_shared(event):
        events.mark_noindex()
    events.enter(event)   # step 1e: the bar and the current event (not for a shared or archived event)
    rows = Issue.query.options(selectinload(Issue.created_by)).filter(Issue.event_id == event.id) \
        .order_by(Issue.event_position, Issue.id).all()
    content_i18n.preload([event], 'name', 'note')
    content_i18n.preload(rows, 'title')
    counted = events.issue_rows(rows)
    archived = event.archived_at is not None
    manage = events.can_manage(event, current_user) and not archived
    open_now = events.open_now(event)
    unfinished = [i for i in rows if not issues.is_finished(i)]
    labels = _inject_phase_labels()
    phases = []
    for issue in unfinished:
        label = labels['phase_labels'].get(issue.phase, issue.phase)
        if label not in phases:
            phases.append(label)
    return render_template(
        'events/page.html', event=event, rows=counted, archived=archived, manage=manage,
        add_issue=events.can_add_issue(event, current_user), share_card=events.share_card(event, current_user),
        unfinished=unfinished, unfinished_phases=', '.join(str(p) for p in phases), open_now=open_now,
        where=events.where(event), link=url_for('events.page', code=event.code, _external=True), **labels)


@events_bp.route('/e')
@limiter.limit('10 per minute', key_func=real_client_ip)
def lookup():
    """The field "Do you have an event code?": upper case, spaces and hyphens do not matter."""
    event = events.by_code(request.args.get('code'))
    if event is None or event.archived_at is not None:
        # TRANSLATORS: after typing an event code that does not exist.
        flash(gettext('We do not know this event code. Check the code or ask the organiser.'), 'error')
        return redirect(url_for('issues.index') + '#eventcode')
    return redirect(url_for('events.page', code=event.code))


def _qr(code, kind):
    event = _managed_event(code)
    data = events.qr(url_for('events.page', code=event.code, _external=True), kind)
    mimetype = 'image/svg+xml' if kind == 'svg' else 'image/png'
    resp = Response(data, mimetype=mimetype)
    resp.headers['Cache-Control'] = 'private, max-age=300'
    return resp


@events_bp.route('/e/<code>/qr.svg')
def qr_svg(code):
    return _qr(code, 'svg')


@events_bp.route('/e/<code>/qr.png')
def qr_png(code):
    return _qr(code, 'png')


@events_bp.route('/e/<code>/nieuwe-link', methods=['GET', 'POST'])
@login_required
def new_link(code):
    event = _managed_event(code)
    if request.method == 'POST':
        _require_csrf()
        events.renew_code(event, current_user)
        db.session.commit()
        # TRANSLATORS: after "Make a new link": the old link and QR code no longer work.
        flash(gettext('There is a new link. The old link and QR code no longer work.'), 'success')
        return redirect(url_for('events.page', code=event.code), code=303)
    content_i18n.preload([event], 'name')
    return render_template('events/new_link.html', event=event)


@events_bp.route('/e/<code>/delen', methods=['POST'])
@login_required
def share(code):
    """K10: share with all participants, "Not now", or undo; only the organiser."""
    event = events.by_code(code)
    if event is None:
        abort(404)
    if not events.can_share(event, current_user):
        abort(403)
    _require_csrf()
    action = request.form.get('action')
    if action == 'share' and event.shared_at is None:
        events.share(event, current_user)
        # TRANSLATORS: after sharing an event with all participants of Global Union.
        flash(gettext('The event is shared with all participants of Global Union.'), 'success')
    elif action == 'later':
        events.dismiss_share(event)
    elif action == 'unshare' and event.shared_at is not None:
        events.unshare(event, current_user)
        # TRANSLATORS: after undoing the sharing of an event.
        flash(gettext('The event is only visible to its participants again.'), 'success')
    else:
        abort(400)
    db.session.commit()
    back = request.form.get('back')
    target = url_for('events.settings', code=event.code) if back == 'settings' else url_for('events.page', code=event.code)
    return redirect(target, code=303)
