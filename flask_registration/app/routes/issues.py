"""
Herontwerp, bouwstap 1: de vraagstukken (Issue).

/gesprekken          lijst met vraagstukken
/gesprekken/nieuw    startformulier voor een vraagstuk
/vraagstuk/<id>      een minimale pagina met de vraag, online of event en de
                     fase met de tijdlijn (de volledige cockpit volgt in stap 2)
"""
from flask import Blueprint, abort, redirect, render_template, request, session, url_for
from flask_babel import gettext
from flask_login import current_user, login_required

from app import db, issues
from app.models import Issue
from app.routes.forum import _require_csrf, _rules_first_redirect
from app.utils import _pop_form_state, _stash_form_state

issues_bp = Blueprint("issues", __name__)


@issues_bp.context_processor
def _inject_phase_labels():
    return {'phase_labels': {'feiten': gettext('Facts and discussion'), 'bijstellen': gettext('Revise the proposal'),
                             'stemmen': gettext('Voting'), 'vervolg': gettext('Follow-up')}}

_FORM_FIELDS = ('title', 'intro', 'kind', 'event_date', 'event_location', 'voting_deadline',
                'days_facts', 'days_adjust', 'days_vote', 'support_threshold', 'proposal_threshold_pct',
                'show_interim', 'final_vote')


@issues_bp.route('/gesprekken')
def index():
    open_issues = Issue.query.filter_by(is_closed=False).order_by(Issue.created_at.desc(), Issue.id.desc()).all()
    return render_template('issues/index.html', issues=open_issues)


@issues_bp.route('/gesprekken/nieuw', methods=['GET', 'POST'])
@login_required
def new():
    if not current_user.can_contribute():
        return _rules_first_redirect()
    if request.method == 'POST':
        _require_csrf()
        values, errors = issues.parse_form(request.form)
        if errors:
            # PRG: fouten en ingevulde waarden een GET lang bewaren.
            data = {k: request.form.get(k, '') for k in _FORM_FIELDS}
            _stash_form_state('issue_new', errors, data)
            return redirect(url_for('issues.new'), code=303)
        issue = issues.create(values, current_user, lang=session.get('lang', 'nl'))
        db.session.commit()
        return redirect(url_for('issues.show', issue_id=issue.id))
    errors, data = _pop_form_state('issue_new')
    return render_template('issues/new.html', errors=errors, form_data=data,
                           defaults=dict(issues.DAY_FIELDS))


@issues_bp.route('/vraagstuk/<int:issue_id>')
def show(issue_id):
    issue = db.session.get(Issue, issue_id)
    if issue is None:
        abort(404)
    return render_template('issues/show.html', issue=issue)
