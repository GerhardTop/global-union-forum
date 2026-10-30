"""
Herontwerp, bouwstap 1: de vraagstukken (Issue).

/gesprekken          lijst met vraagstukken
/gesprekken/nieuw    startformulier voor een vraagstuk
/vraagstuk/<id>      de cockpit (stap 2): context en feiten links, de gedeelde
                     discussie in het midden, voorstellen en stemrecht rechts
/vraagstuk/<id>/feiten
                     feiten en onderzoek (stap 3): filteren, sorteren, nuttig,
                     betwisten en toevoegen
/vraagstuk/<id>/voorstellen, /voorstellen/nieuw, /voorstel/<pid>
                     voorstellen onder het vraagstuk (stap 4): doen, steunen,
                     bijstellen, de lijst en het detail
"""
from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from flask_babel import gettext
from flask_login import current_user, login_required

from app import conduct, db, issues, limiter
from app import facts as fact_signals
from app import issue_proposals as ip
from app.models import FACT_DIRECTIONS, Issue, IssueProposal
from app.routes.forum import (_abort_if_thread_removed, _fact_phase_open, _require_csrf, _rules_first_redirect,
                              _thread_context)
from app.utils import _pop_form_state, _stash_form_state, safe_next

issues_bp = Blueprint("issues", __name__)

# Zoveel feiten in de linkerkolom; de rest via "Alle feiten en onderzoek".
FACTS_SHOWN = 3
# Zoveel voorstellen in de rechterkolom; de rest via de voorstellenpagina.
COCKPIT_PROPOSALS = 5


@issues_bp.context_processor
def _inject_phase_labels():
    return {'phase_labels': {'feiten': gettext('Facts and discussion'), 'bijstellen': gettext('Revise the proposal'),
                             'stemmen': gettext('Voting'), 'vervolg': gettext('Follow-up')},
            'ip_status': ip.status,
            'ip_can_support': lambda p, n, supported: ip.can_support(p, current_user, n, supported)}

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
                           proposal_rows=ip.listing(issue, order='stemlijst')[:COCKPIT_PROPOSALS],
                           proposal_total=len(issue.issue_proposals),
                           can_propose=ip.can_propose(issue, current_user),
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


# ── Stap 4: voorstellen onder een vraagstuk ──────────────────────────────

def _issue_or_404(issue_id):
    issue = db.session.get(Issue, issue_id)
    if issue is None:
        abort(404)
    return issue


def _proposal_or_404(issue, proposal_id):
    proposal = db.session.get(IssueProposal, proposal_id)
    if proposal is None or proposal.issue_id != issue.id:
        abort(404)
    return proposal


@issues_bp.route('/vraagstuk/<int:issue_id>/voorstellen')
def proposals(issue_id):
    issue = _issue_or_404(issue_id)
    which = request.args.get('filter')
    which = which if which in ip.FILTERS else 'alle'
    order = request.args.get('sorteer')
    order = order if order in ip.ORDERS else ip.DEFAULT_ORDER
    rows = ip.listing(issue, which, order)
    return render_template('issues/proposals.html', issue=issue, rows=rows, which=which, order=order,
                           my_supports=ip.my_supports([p.id for p, _n in rows], current_user),
                           can_propose=ip.can_propose(issue, current_user))


@issues_bp.route('/vraagstuk/<int:issue_id>/voorstellen/nieuw', methods=['GET', 'POST'])
@login_required
@limiter.limit("10 per hour", methods=["POST"], key_func=lambda: f"issue-proposal-user-{current_user.get_id()}")
def proposal_new(issue_id):
    issue = _issue_or_404(issue_id)
    if not current_user.can_contribute():
        return _rules_first_redirect()
    form_key = f'voorstel-nieuw-{issue.id}'
    if request.method == 'POST':
        _require_csrf()
        if not ip.can_propose(issue, current_user):
            abort(403)
        statement, explanation, errors = ip.parse_form(request.form)
        if errors:
            _stash_form_state(form_key, errors, {'statement': request.form.get('statement', ''),
                                                 'explanation': request.form.get('explanation', '')})
            return redirect(url_for('issues.proposal_new', issue_id=issue.id), code=303)
        proposal = ip.create(issue, current_user, statement, explanation, lang=session.get('lang', 'nl'))
        db.session.commit()
        return redirect(url_for('issues.proposal', issue_id=issue.id, proposal_id=proposal.id))
    errors, data = _pop_form_state(form_key)
    return render_template('issues/proposal_new.html', issue=issue, errors=errors, form_data=data,
                           can_propose=ip.can_propose(issue, current_user))


@issues_bp.route('/vraagstuk/<int:issue_id>/voorstel/<int:proposal_id>')
def proposal(issue_id, proposal_id):
    issue = _issue_or_404(issue_id)
    proposal = _proposal_or_404(issue, proposal_id)
    count = ip.support_counts([proposal.id]).get(proposal.id, 0)
    supported = proposal.id in ip.my_supports([proposal.id], current_user)
    errors, data = _pop_form_state(f'voorstel-bijstellen-{proposal.id}')
    return render_template('issues/proposal.html', issue=issue, proposal=proposal, count=count,
                           supported=supported, can_support=ip.can_support(proposal, current_user, count, supported),
                           can_revise=ip.can_revise(proposal, current_user), errors=errors, form_data=data)


@issues_bp.route('/vraagstuk/<int:issue_id>/voorstel/<int:proposal_id>/steunen', methods=['POST'])
@login_required
@limiter.limit("60 per minute", key_func=lambda: f"issue-support-user-{current_user.get_id()}")
def proposal_support(issue_id, proposal_id):
    issue = _issue_or_404(issue_id)
    proposal = _proposal_or_404(issue, proposal_id)
    _require_csrf()
    back = safe_next(request.form.get('next'),
                     url_for('issues.proposal', issue_id=issue.id, proposal_id=proposal.id))
    count = ip.support_counts([proposal.id]).get(proposal.id, 0)
    if proposal.id in ip.my_supports([proposal.id], current_user):
        flash(gettext('You already support this proposal.'), 'info')
        return redirect(back)
    if not ip.can_support(proposal, current_user, count):
        abort(403)
    ip.support(proposal, current_user)
    db.session.commit()
    return redirect(back)


@issues_bp.route('/vraagstuk/<int:issue_id>/voorstel/<int:proposal_id>/bijstellen', methods=['POST'])
@login_required
def proposal_revise(issue_id, proposal_id):
    issue = _issue_or_404(issue_id)
    proposal = _proposal_or_404(issue, proposal_id)
    _require_csrf()
    if not ip.can_revise(proposal, current_user):
        abort(403)
    statement, explanation, errors = ip.parse_form(request.form)
    if not errors and statement == proposal.statement and explanation == proposal.explanation:
        errors['unchanged'] = True
    detail = url_for('issues.proposal', issue_id=issue.id, proposal_id=proposal.id)
    if errors:
        _stash_form_state(f'voorstel-bijstellen-{proposal.id}', errors,
                          {'statement': request.form.get('statement', ''),
                           'explanation': request.form.get('explanation', '')})
        return redirect(detail + '#bijstellen', code=303)
    ip.revise(proposal, statement, explanation)
    db.session.commit()
    return redirect(detail)
