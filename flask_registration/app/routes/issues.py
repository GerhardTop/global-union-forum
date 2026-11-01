"""
Herontwerp, bouwstap 1: de vraagstukken (Issue).

/gesprekken          het overzicht achter "Denk mee" (polijstronde P1: filters)
/gesprekken/nieuw    startformulier voor een vraagstuk
/vraagstuk/<id>      de cockpit (stap 2): context en feiten links, de gedeelde
                     discussie in het midden, voorstellen en stemrecht rechts
/vraagstuk/<id>/feiten
                     feiten en onderzoek (stap 3): filteren, sorteren, nuttig,
                     betwisten en toevoegen (P2: kwaliteitsmerk i.p.v. nuttig)
/vraagstuk/<id>/voorstellen, /voorstellen/nieuw, /voorstel/<pid>
                     voorstellen onder het vraagstuk (stap 4): doen, steunen,
                     bijstellen, de lijst en het detail; stap 5 feiten
                     koppelen; stap 6 stemmen (/voorstel/<pid>/stemmen)
/vraagstuk/<id>/samenvattend-voorstel
                     het samenvattende voorstel van de initiatiefnemer (stap 6)
/vraagstuk/<id>/volgende-fase, /vervolg, /heropenen
                     de levenscyclus met de hand (stap 7): een fase verder,
                     de vervolgtekst, opnieuw openstellen
/vraagstuk/<id>/volgen
                     volgen aan of uit (polijstronde P3)
"""
from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, session, url_for
from flask_babel import gettext
from flask_login import current_user, login_required

from app import conduct, db, issues, limiter
from app import facts as fact_signals
from app import issue_lifecycle as lifecycle
from app import issue_proposals as ip
from app.models import FACT_DIRECTIONS, Issue, IssueProposal
from app.routes.forum import (_abort_if_thread_removed, _fact_form, _fact_phase_open, _new_fact, _require_csrf,
                              _rules_first_redirect, _thread_context)
from app.utils import _pop_form_state, _stash_form_state, safe_next

issues_bp = Blueprint("issues", __name__)

# Zoveel feiten in de linkerkolom; de rest via "Alle feiten en onderzoek".
FACTS_SHOWN = 3
# Zoveel voorstellen in de rechterkolom; de rest via de voorstellenpagina.
COCKPIT_PROPOSALS = 5
FOLLOWUP_MAX = 5000


@issues_bp.context_processor
def _inject_phase_labels():
    return {'phase_labels': {'feiten': gettext('Facts and discussion'), 'bijstellen': gettext('Revise the proposal'),
                             'stemmen': gettext('Voting'), 'samenvatten': gettext('Summary proposal'),
                             'vervolg': gettext('Follow-up')},
            'ip_status': ip.status,
            'ip_can_support': lambda p, n, supported: ip.can_support(p, current_user, n, supported)}

@issues_bp.app_template_filter('local')
def _local_time(moment):
    """Polijstronde P4: een UTC-moment in Nederlandse tijd voor de weergave."""
    return issues.to_local(moment)


_FORM_FIELDS = ('title', 'intro', 'kind', 'event_name', 'event_note', 'event_location', 'event_online_between',
                'support_threshold', 'proposal_threshold_pct', 'show_interim', 'final_vote') + tuple(
                f'{name}_{part}' for name, _t in issues.SCHEDULE_FIELDS for part in ('date', 'time'))
_FORM_LISTS = ('event_day', 'event_from', 'event_to')
_NEW_FACT_KEYS = ('text', 'direction', 'source', 'type')


def _new_issue_facts(form):
    """Polijstronde P4: de feiten die bij het aanmaken al zijn ingevuld
    (rijen fact_<i>_...). Geeft (feiten, fouten per rij, ingevulde rijen)."""
    facts, errors, rows = [], {}, []
    for i in range(issues.NEW_FACTS_MAX):
        prefix = f'fact_{i}_'
        if not any((form.get(prefix + k) or '').strip() for k in ('text', 'source')):
            continue
        data, row_errors = _fact_form(prefix)
        rows.append(data)
        if row_errors:
            errors[str(len(rows) - 1)] = row_errors  # sessie-JSON: tekst als sleutel
        else:
            facts.append(data)
    return facts, errors, rows


@issues_bp.route('/gesprekken')
def index():
    """Het overzicht achter "Denk mee": alle vraagstukken met hun fase en
    aantallen, te filteren op soort (online, event) en op lopend of afgerond."""
    which = request.args.get('filter')
    which = which if which in issues.OVERVIEW_FILTERS else 'alle'
    return render_template('issues/index.html', rows=issues.overview(which), which=which)


@issues_bp.route('/gesprekken/nieuw', methods=['GET', 'POST'])
@login_required
def new():
    if not current_user.can_contribute():
        return _rules_first_redirect()
    if request.method == 'POST':
        _require_csrf()
        values, errors = issues.parse_form(request.form)
        new_facts, fact_errors, fact_rows = _new_issue_facts(request.form)
        if fact_errors:
            errors['facts'] = fact_errors
        if errors:
            # PRG: fouten en ingevulde waarden een GET lang bewaren.
            data = {k: request.form.get(k, '') for k in _FORM_FIELDS}
            data.update({k: request.form.getlist(k) for k in _FORM_LISTS})
            data['facts'] = fact_rows
            _stash_form_state('issue_new', errors, data)
            return redirect(url_for('issues.new'), code=303)
        issue = issues.create(values, current_user, lang=session.get('lang', 'nl'))
        db.session.flush()
        made = [_new_fact(issue.thread, data) for data in new_facts]
        db.session.commit()
        op = issues.opening_post(issue)
        from app import quality
        for post in ([op] if op is not None else []) + made:
            quality.schedule_judgement(post.id)
        return redirect(url_for('issues.show', issue_id=issue.id))
    errors, data = _pop_form_state('issue_new')
    return render_template('issues/new.html', errors=errors, form_data=data,
                           schedule=issues.default_schedule(), schedule_fields=issues.SCHEDULE_FIELDS,
                           new_facts_max=issues.NEW_FACTS_MAX, event_days_max=issues.EVENT_DAYS_MAX)


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
    top = fact_signals.listing(visible, order='kwaliteit')  # kwaliteit eerst: merk, dan een link als bron
    return render_template('issues/show.html', issue=issue, has_discussion=bool(discussion),
                           top_facts=top, facts_shown=FACTS_SHOWN, fact_total=len(visible),
                           remaining=issues.remaining(issue), voting_right=issues.voting_right(current_user),
                           proposal_rows=ip.listing(issue, order='stemlijst'), proposals_shown=COCKPIT_PROPOSALS,
                           proposal_total=ip.regular_query(issue).count(),
                           final=ip.final_proposal(issue), can_make_final=ip.can_make_final(issue, current_user),
                           can_propose=ip.can_propose(issue, current_user),
                           can_step=lifecycle.can_step(issue, current_user), next_phase=lifecycle.next_phase(issue),
                           can_reopen=lifecycle.can_reopen(issue, current_user),
                           can_edit_followup=lifecycle.can_edit_followup(issue, current_user),
                           silence_end=lifecycle.silence_end(issue) if issue.kind == 'online' else None,
                           following=lifecycle.is_following(issue, current_user),
                           can_add_fact=_can_add_fact(issue), open_now=issues.is_open_now(issue),
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
    listed = fact_signals.listing(visible, direction, order)
    ids = [f.id for f in listed]
    is_mod = current_user.is_authenticated and current_user.can_moderate()
    return render_template(
        'issues/facts.html', issue=issue, thread=thread, facts=listed[:shown], listed_count=len(listed),
        total=len(visible), shown=shown, direction=direction, order=order,
        phase_open=thread is not None and _fact_phase_open(thread),
        conduct_cases=cases, fact_errors=fact_errors, fact_data=fact_data, return_to=issue.id,
        fact_marks=fact_signals.marked_ids(ids),
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
                           can_propose=ip.can_propose(issue, current_user),
                           final=ip.final_proposal(issue), can_make_final=ip.can_make_final(issue, current_user))


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
        # Stap 5: optioneel al feiten koppelen, en eventueel een nieuw feit (zelfde regel als elders).
        fact_ids = request.form.getlist('fact_ids', type=int)
        new_fact = None
        if any((request.form.get(k) or '').strip() for k in ('fact_text', 'fact_source')):
            if not _can_add_fact(issue):
                abort(403)
            new_fact, fact_errors = _fact_form('fact_')
            if fact_errors:
                errors['fact'] = fact_errors
        if errors:
            keep = ('statement', 'explanation', 'fact_text', 'fact_direction', 'fact_source')
            data = {k: request.form.get(k, '') for k in keep}
            data['fact_ids'] = fact_ids
            _stash_form_state(form_key, errors, data)
            return redirect(url_for('issues.proposal_new', issue_id=issue.id), code=303)
        proposal = ip.create(issue, current_user, statement, explanation, lang=session.get('lang', 'nl'))
        db.session.flush()
        ip.link_ids(proposal, fact_ids, current_user)
        fact = _new_fact(issue.thread, new_fact) if new_fact is not None else None
        if fact is not None:
            ip.link(proposal, fact, current_user)
        db.session.commit()
        if fact is not None:
            from app import quality
            quality.schedule_judgement(fact.id)
        return redirect(url_for('issues.proposal', issue_id=issue.id, proposal_id=proposal.id))
    errors, data = _pop_form_state(form_key)
    return render_template('issues/proposal_new.html', issue=issue, errors=errors, form_data=data,
                           can_propose=ip.can_propose(issue, current_user),
                           issue_facts=ip.visible_issue_facts(issue, current_user),
                           can_add_fact=_can_add_fact(issue), open_now=issues.is_open_now(issue),
                           fact_marks=fact_signals.marked_ids(f.id for f in ip.visible_issue_facts(issue, current_user)))


@issues_bp.route('/vraagstuk/<int:issue_id>/voorstel/<int:proposal_id>')
def proposal(issue_id, proposal_id):
    issue = _issue_or_404(issue_id)
    proposal = _proposal_or_404(issue, proposal_id)
    count = ip.support_counts([proposal.id]).get(proposal.id, 0)
    supported = proposal.id in ip.my_supports([proposal.id], current_user)
    # Eén formulierstaat per detailpagina: errors['revise'] of errors['fact'].
    errors, data = _pop_form_state(f'voorstel-{proposal.id}')
    links = ip.linked(proposal, current_user)
    linked_ids = {fact.id for _link, fact in links}
    return render_template('issues/proposal.html', issue=issue, proposal=proposal, count=count,
                           supported=supported, can_support=ip.can_support(proposal, current_user, count, supported),
                           can_revise=ip.can_revise(proposal, current_user), errors=errors, form_data=data,
                           links=links, can_link=ip.can_link(proposal, current_user),
                           can_unlink=lambda row: ip.can_unlink(row, current_user),
                           candidates=[f for f in ip.visible_issue_facts(issue, current_user) if f.id not in linked_ids],
                           can_add_fact=_can_add_fact(issue), vote=_vote_context(proposal, count),
                           timeline=ip.timeline(proposal), current_version=ip.current_version(proposal),
                           reactions=_visible_reactions(proposal), can_react=_can_react(issue),
                           fact_marks=fact_signals.marked_ids(f.id for f in ip.visible_issue_facts(issue, current_user)))


def _visible_reactions(proposal):
    """Polijstronde P5: de reacties bij dit voorstel die deze lezer ziet
    (verplaatst of verborgen is weg, zoals in de discussie)."""
    rows = ip.reactions(proposal)
    cases = conduct.cases_for([p.id for p in rows])
    return [p for p in rows if p.id not in cases or not conduct.removed_for(p, cases[p.id], current_user)]


def _can_react(issue):
    return (current_user.is_authenticated and current_user.can_contribute() and issue.thread is not None
            and not issue.thread.is_closed and issues.is_open_now(issue))


def _vote_context(proposal, count):
    """Alles voor de stemming op het detail (stap 6)."""
    from app import status
    show = ip.show_tally(proposal, count)
    return {'open': ip.voting_open(proposal, count), 'over': ip.voting_over(proposal),
            'on_list': ip.on_list(proposal, count), 'can_vote': ip.can_vote(proposal, current_user, count),
            'mine': ip.my_vote(proposal, current_user), 'show': show,
            'result': ip.result(proposal) if show else None,
            'voters': sum(ip.tally(proposal).values()),
            'final_end': ip.final_voting_end(proposal) if proposal.is_final else None,
            'voter_min': status.get_thresholds()['voter_min']}


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
        _stash_form_state(f'voorstel-{proposal.id}', {'revise': errors},
                          {'revise': {'statement': request.form.get('statement', ''),
                                      'explanation': request.form.get('explanation', '')}})
        return redirect(detail + '#bijstellen', code=303)
    ip.revise(proposal, statement, explanation)
    db.session.commit()
    return redirect(detail)


# ── Stap 5: feiten koppelen aan een voorstel ─────────────────────────────

def _can_add_fact(issue):
    """Een nieuw feit toevoegen volgt de bestaande feitregel (de feitenfase)."""
    return (current_user.is_authenticated and current_user.can_contribute() and issue.thread is not None
            and _fact_phase_open(issue.thread))


def _basis_url(issue, proposal):
    return url_for('issues.proposal', issue_id=issue.id, proposal_id=proposal.id) + '#basis'


@issues_bp.route('/vraagstuk/<int:issue_id>/voorstel/<int:proposal_id>/koppelen', methods=['POST'])
@login_required
@limiter.limit("60 per minute", key_func=lambda: f"issue-link-user-{current_user.get_id()}")
def proposal_link(issue_id, proposal_id):
    issue = _issue_or_404(issue_id)
    proposal = _proposal_or_404(issue, proposal_id)
    _require_csrf()
    if not ip.can_link(proposal, current_user):
        abort(403)
    if not ip.link_ids(proposal, request.form.getlist('fact_ids', type=int), current_user):
        flash(gettext('Choose at least one fact that is not linked yet.'), 'error')
    db.session.commit()
    return redirect(_basis_url(issue, proposal))


@issues_bp.route('/vraagstuk/<int:issue_id>/voorstel/<int:proposal_id>/feit', methods=['POST'])
@login_required
@limiter.limit("5 per minute", key_func=lambda: f"forum-fact-user-{current_user.get_id()}")
def proposal_new_fact(issue_id, proposal_id):
    """Een nieuw feit in de gedeelde lijst van het vraagstuk, meteen gekoppeld."""
    issue = _issue_or_404(issue_id)
    proposal = _proposal_or_404(issue, proposal_id)
    _require_csrf()
    if not ip.can_link(proposal, current_user) or not _can_add_fact(issue):
        abort(403)
    data, errors = _fact_form()
    if errors:
        _stash_form_state(f'voorstel-{proposal.id}', {'fact': errors}, {'fact': data})
        return redirect(_basis_url(issue, proposal), code=303)
    fact = _new_fact(issue.thread, data)
    ip.link(proposal, fact, current_user)
    db.session.commit()
    from app import quality
    quality.schedule_judgement(fact.id)
    return redirect(_basis_url(issue, proposal))


@issues_bp.route('/vraagstuk/<int:issue_id>/voorstel/<int:proposal_id>/ontkoppelen/<int:link_id>',
                 methods=['POST'])
@login_required
def proposal_unlink(issue_id, proposal_id, link_id):
    from app.models import IssueProposalFact
    issue = _issue_or_404(issue_id)
    proposal = _proposal_or_404(issue, proposal_id)
    _require_csrf()
    row = db.session.get(IssueProposalFact, link_id)
    if row is None or row.proposal_id != proposal.id:
        abort(404)
    if not ip.can_unlink(row, current_user):
        abort(403)
    ip.unlink(row)
    db.session.commit()
    return redirect(_basis_url(issue, proposal))


# ── Stap 6: stemmen en het samenvattende voorstel ──────────────────────────────

@issues_bp.route('/vraagstuk/<int:issue_id>/voorstel/<int:proposal_id>/stemmen', methods=['POST'])
@login_required
@limiter.limit("60 per minute", key_func=lambda: f"issue-vote-user-{current_user.get_id()}")
def proposal_vote(issue_id, proposal_id):
    from app.models import VOTE_CHOICES
    issue = _issue_or_404(issue_id)
    proposal = _proposal_or_404(issue, proposal_id)
    _require_csrf()
    if not ip.can_vote(proposal, current_user):
        abort(403)
    choice = request.form.get('choice')
    if choice not in VOTE_CHOICES:
        abort(400)
    ip.cast_vote(proposal, current_user, choice)
    db.session.commit()
    return redirect(url_for('issues.proposal', issue_id=issue.id, proposal_id=proposal.id) + '#stemming')


@issues_bp.route('/vraagstuk/<int:issue_id>/samenvattend-voorstel', methods=['GET', 'POST'])
@login_required
def final_new(issue_id):
    """De initiatiefnemer doet in de fase samenvatten (P6) het samenvattende voorstel, op basis
    van alle voorstellen en de discussie, en kan daarbij de vraag en de
    toelichting van het vraagstuk aanpassen."""
    issue = _issue_or_404(issue_id)
    if not ip.can_make_final(issue, current_user):
        abort(403)
    form_key = f'ultiem-{issue.id}'
    if request.method == 'POST':
        _require_csrf()
        statement, explanation, errors = ip.parse_form(request.form)
        title = ' '.join((request.form.get('title') or '').split())
        intro = (request.form.get('intro') or '').strip()
        if not title or len(title) > issues.TITLE_MAX:
            errors['title'] = True
        if errors:
            _stash_form_state(form_key, errors, {k: request.form.get(k, '')
                                                 for k in ('statement', 'explanation', 'title', 'intro')})
            return redirect(url_for('issues.final_new', issue_id=issue.id), code=303)
        proposal = ip.create_final(issue, current_user, statement, explanation, lang=session.get('lang', 'nl'),
                                   title=title if title != issue.title else None,
                                   intro=intro if intro != (issue.intro or '') else None)
        db.session.flush()
        lifecycle.summary_made(issue, proposal)  # P6: de fase loopt tot het einde van de stemming
        db.session.commit()
        return redirect(url_for('issues.proposal', issue_id=issue.id, proposal_id=proposal.id))
    errors, data = _pop_form_state(form_key)
    rows = ip.listing(issue, order='stemlijst')
    return render_template('issues/final_new.html', issue=issue, errors=errors,
                           form_data=data or {'title': issue.title, 'intro': issue.intro or ''}, rows=rows)


# ── Polijstronde P3: een vraagstuk volgen ────────────────────────────────

@issues_bp.route('/vraagstuk/<int:issue_id>/volgen', methods=['POST'])
@login_required
@limiter.limit("30 per minute", key_func=lambda: f"issue-follow-user-{current_user.get_id()}")
def follow(issue_id):
    """Volgen aan of uit; volgers krijgen de meldingen in de dagelijkse
    samenvatting. Met X-Requested-With JSON, anders terug naar de cockpit."""
    issue = _issue_or_404(issue_id)
    _require_csrf()
    following = lifecycle.toggle_follow(issue, current_user)
    db.session.commit()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'following': following})
    return redirect(url_for('issues.show', issue_id=issue.id))


# ── Stap 7: de levenscyclus met de hand ──────────────────────────────────

@issues_bp.route('/vraagstuk/<int:issue_id>/volgende-fase', methods=['POST'])
@login_required
def next_phase(issue_id):
    """De initiatiefnemer of een moderator zet het vraagstuk een fase verder:
    bij een event de gewone weg, bij online om een fase vroeg af te sluiten."""
    issue = _issue_or_404(issue_id)
    _require_csrf()
    if not lifecycle.can_step(issue, current_user):
        abort(403)
    lifecycle.step_forward(issue)
    db.session.commit()
    flash(gettext('The issue is now in the next phase.'), 'success')
    return redirect(url_for('issues.show', issue_id=issue.id))


@issues_bp.route('/vraagstuk/<int:issue_id>/vervolg', methods=['POST'])
@login_required
def followup(issue_id):
    issue = _issue_or_404(issue_id)
    _require_csrf()
    if not lifecycle.can_edit_followup(issue, current_user):
        abort(403)
    text = (request.form.get('followup_text') or '').strip()
    if len(text) > FOLLOWUP_MAX:
        flash(gettext('The text can be at most %(n)s characters.', n=FOLLOWUP_MAX), 'error')
        return redirect(url_for('issues.show', issue_id=issue.id) + '#vervolg', code=303)
    lifecycle.set_followup(issue, text)
    db.session.commit()
    return redirect(url_for('issues.show', issue_id=issue.id) + '#vervolg')


@issues_bp.route('/vraagstuk/<int:issue_id>/heropenen', methods=['POST'])
@login_required
def reopen(issue_id):
    """Opnieuw openstellen, terug naar de discussie; alleen met het vinkje
    ter bevestiging."""
    issue = _issue_or_404(issue_id)
    _require_csrf()
    if not lifecycle.can_reopen(issue, current_user):
        abort(403)
    if request.form.get('confirm') != '1':
        flash(gettext('Tick the box to confirm that you want to reopen the issue.'), 'error')
        return redirect(url_for('issues.show', issue_id=issue.id) + '#beheer', code=303)
    lifecycle.reopen(issue)
    db.session.commit()
    flash(gettext('The issue is open again for discussion.'), 'success')
    return redirect(url_for('issues.show', issue_id=issue.id))
