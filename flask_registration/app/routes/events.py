"""
Events step 1c (docs/events.md, K3, K4, K5, K7, K13): making and managing an
event. Only with EVENTS on; otherwise every route here is a 404.

/events/nieuw              the form "New event"
/e/<code>/instellingen     the event settings: the same form, filled in, and
                           the issues of the event with Up and Down
/e/<code>/volgorde         POST: move an issue one place up or down

Until step 1d (the event page) "Create event" leads to the settings.
"""
from datetime import timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_babel import gettext
from flask_login import current_user, login_required

from app import content_i18n, db, events, i18n, issues
from app.routes.forum import _require_csrf, _rules_first_redirect, refuse_if_suspended
from app.utils import _pop_form_state, _stash_form_state

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
                           event_days_max=issues.EVENT_DAYS_MAX, **_inject_phase_labels())


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
        # Step 1d makes this the event page /e/<code>.
        return redirect(url_for('events.settings', code=event.code), code=303)
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
