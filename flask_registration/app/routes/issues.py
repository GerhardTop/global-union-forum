"""
Herontwerp, bouwstap 1: de vraagstukken (Issue).

/gesprekken          lijst met vraagstukken
/gesprekken/nieuw    startformulier voor een vraagstuk
/vraagstuk/<id>      de cockpit (stap 2): context en feiten links, de gedeelde
                     discussie in het midden, voorstellen en stemrecht rechts
"""
from flask import Blueprint, abort, redirect, render_template, request, session, url_for
from flask_babel import gettext
from flask_login import current_user, login_required

from app import db, issues
from app.models import Issue
from app.routes.forum import _require_csrf, _rules_first_redirect, _thread_context
from app.utils import _pop_form_state, _stash_form_state

issues_bp = Blueprint("issues", __name__)

# Zoveel feiten in de linkerkolom; de rest via "Alle feiten en onderzoek".
FACTS_SHOWN = 3


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
        op = issues.opening_post(issue)
        if op is not None:
            from app import quality
            quality.schedule_judgement(op.id)
        return redirect(url_for('issues.show', issue_id=issue.id))
    errors, data = _pop_form_state('issue_new')
    return render_template('issues/new.html', errors=errors, form_data=data,
                           defaults=dict(issues.DAY_FIELDS))


@issues_bp.route('/vraagstuk/<int:issue_id>')
def show(issue_id):
    issue = db.session.get(Issue, issue_id)
    if issue is None:
        abort(404)
    # De middenkolom hergebruikt de discussie van de gesprekspagina, met
    # dezelfde context (boom, keurmerken, eens/oneens, handhaving, feiten).
    discussion = _thread_context(issue.thread) if issue.thread is not None else {}
    facts = [f for f in discussion.get('facts', ())
             if f.id not in discussion['hidden_ids'] and f.id not in discussion['moved_ids']]
    return render_template('issues/show.html', issue=issue, has_discussion=bool(discussion),
                           top_facts=facts[:FACTS_SHOWN], fact_total=len(facts),
                           remaining=issues.remaining(issue), voting_right=issues.voting_right(current_user),
                           return_to=issue.id, **discussion)
