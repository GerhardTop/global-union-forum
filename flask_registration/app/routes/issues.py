"""
Herontwerp, bouwstap 1: de vraagstukken (Issue).

/gesprekken          lijst met vraagstukken
/gesprekken/nieuw    startformulier voor een vraagstuk
/vraagstuk/<id>      de cockpit (stap 2): context en feiten links, de gedeelde
                     discussie in het midden, voorstellen en stemrecht rechts
/vraagstuk/<id>/feiten
                     feiten en onderzoek (stap 3): filteren, sorteren, nuttig,
                     betwisten en toevoegen
"""
from flask import Blueprint, abort, redirect, render_template, request, session, url_for
from flask_babel import gettext
from flask_login import current_user, login_required

from app import conduct, db, issues
from app import facts as fact_signals
from app.models import Issue
from app.models import FACT_DIRECTIONS
from app.routes.forum import (_abort_if_thread_removed, _fact_phase_open, _require_csrf, _rules_first_redirect,
                              _thread_context)
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
    visible = [f for f in discussion.get('facts', ())
               if f.id not in discussion['hidden_ids'] and f.id not in discussion['moved_ids']]
    top, _counts = fact_signals.listing(visible, order='kwaliteit')  # stap 3: kwaliteit eerst
    return render_template('issues/show.html', issue=issue, has_discussion=bool(discussion),
                           top_facts=top[:FACTS_SHOWN], fact_total=len(visible),
                           remaining=issues.remaining(issue), voting_right=issues.voting_right(current_user),
                           return_to=issue.id, **discussion)


@issues_bp.route('/vraagstuk/<int:issue_id>/feiten')
def facts(issue_id):
    """Alle feiten en onderzoek van een vraagstuk, met filter op richting,
    sorteren en "toon meer" (steeds tien erbij, zonder JavaScript)."""
    issue = db.session.get(Issue, issue_id)
    if issue is None:
        abort(404)
    thread = issue.thread
    visible, cases, fact_errors, fact_data = [], {}, {}, {}
    if thread is not None:
        _abort_if_thread_removed(thread)
        everything = fact_signals.thread_facts(thread.id)
        cases = conduct.cases_for([f.id for f in everything])
        # Zoals in het gesprek: een verplaatst of verborgen feit is weg voor
        # anderen; de inbrenger en moderators zien het gemarkeerd.
        visible = [f for f in everything
                   if f.id not in cases or not conduct.removed_for(f, cases[f.id], current_user)]
        fact_errors, fact_data = _pop_form_state(f'feit-{thread.id}')
    direction = request.args.get('richting')
    direction = direction if direction in FACT_DIRECTIONS else None
    order = request.args.get('sorteer')
    order = order if order in fact_signals.ORDERS else fact_signals.DEFAULT_ORDER
    shown = min(max(request.args.get('n', type=int) or fact_signals.PAGE_SIZE, fact_signals.PAGE_SIZE), 1000)
    listed, counts = fact_signals.listing(visible, direction, order)
    ids = [f.id for f in listed]
    is_mod = current_user.is_authenticated and current_user.can_moderate()
    return render_template(
        'issues/facts.html', issue=issue, thread=thread, facts=listed[:shown], listed_count=len(listed),
        total=len(visible), shown=shown, direction=direction, order=order,
        phase_open=thread is not None and _fact_phase_open(thread),
        conduct_cases=cases, fact_errors=fact_errors, fact_data=fact_data, return_to=issue.id,
        fact_useful_counts=counts, fact_my_useful=fact_signals.my_useful(ids, current_user),
        fact_disputed=fact_signals.disputed_ids(ids) if is_mod else set(),
        fact_my_disputed=fact_signals.my_disputed(ids, current_user))
