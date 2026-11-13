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
from datetime import datetime, timedelta

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, session, url_for
from flask_babel import get_locale, gettext
from flask_login import current_user, login_required

from app import conduct, db, issues, limiter
from app import facts as fact_signals
from app import issue_lifecycle as lifecycle
from app import issue_proposals as ip
from app.models import FACT_DIRECTIONS, Issue, IssueProposal
from app.routes.forum import (_abort_if_thread_removed, _fact_form, _fact_phase_open, _new_fact, _require_csrf,
                              _rules_first_redirect, _thread_context, refuse_if_suspended)
from app.utils import _pop_form_state, _stash_form_state, safe_next

issues_bp = Blueprint("issues", __name__)
issues_bp.before_request(refuse_if_suspended)

# Zoveel feiten in de linkerkolom; de rest via "Alle feiten en onderzoek".
FACTS_SHOWN = 10
# Zoveel voorstellen in de rechterkolom; de rest via de voorstellenpagina.
COCKPIT_PROPOSALS = 5
# Zoveel feiten en voorstellen in de linkerkolom van het voorstel; dan "Toon meer".
RAIL_SHOWN = 10
FOLLOWUP_MAX = 5000


@issues_bp.context_processor
def _inject_phase_labels():
    nl = str(get_locale()) == 'nl'
    return {'phase_labels': {'feiten': 'Voorstellen maken' if nl else 'Making proposals',
                             'bijstellen': 'Voorstellen aanpassen' if nl else 'Adjust the proposals',
                             'stemmen': 'Stemmen' if nl else 'Voting',
                             'samenvatten': 'Samenvattend voorstel' if nl else 'Summary proposal',
                             'vervolg': 'Vervolg' if nl else 'Follow-up'},
            'ip_status': ip.status,
            'ip_can_support': lambda p, n, supported: ip.can_support(p, current_user, n, supported)}

@issues_bp.app_template_filter('local')
def _local_time(moment):
    """Polijstronde P4: een UTC-moment in Nederlandse tijd voor de weergave."""
    return issues.to_local(moment)


@issues_bp.app_template_filter('end_day')
def _end_day(local):
    """Een einde om 00:00 (ingevoerd als 24:00) hoort bij de dag ervoor; zo
    toont een datum zonder tijd de laatste dag van de fase."""
    if local is not None and not (local.hour or local.minute):
        return local - timedelta(minutes=1)
    return local


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
    proposal_rows = ip.listing(issue, order='stemlijst')
    my_reported_proposal_ids = _reported_proposal_ids([p.id for p, _c in proposal_rows])
    reaction_counts, new_discussion = _reaction_stats([p for p, _c in proposal_rows])
    return render_template('issues/show.html', issue=issue, has_discussion=bool(discussion),
                           top_facts=top, facts_shown=FACTS_SHOWN, fact_total=len(visible),
                           remaining=issues.remaining(issue), voting_right=issues.voting_right(current_user),
                           proposal_rows=proposal_rows, proposals_shown=COCKPIT_PROPOSALS,
                           reaction_counts=reaction_counts, new_discussion=new_discussion,
                           proposal_total=ip.regular_query(issue).count(),
                           final=ip.final_proposal(issue), can_make_final=ip.can_make_final(issue, current_user),
                           can_propose=ip.can_propose(issue, current_user),
                           # P7: "Fasen beheren" in de cockpit alleen voor de initiatiefnemer.
                           can_step=lifecycle.can_step(issue, current_user) and lifecycle.is_initiator(issue, current_user),
                           next_phase=lifecycle.next_phase(issue),
                           can_reopen=lifecycle.can_reopen(issue, current_user) and lifecycle.is_initiator(issue, current_user),
                           can_edit_followup=lifecycle.can_edit_followup(issue, current_user),
                           following=lifecycle.is_following(issue, current_user),
                           can_add_fact=_can_add_fact(issue), open_now=issues.is_open_now(issue),
                           return_to=issue.id,
                           my_reported_proposal_ids=my_reported_proposal_ids, **discussion)


@issues_bp.route('/vraagstuk/<int:issue_id>/feiten')
def facts(issue_id):
    """Alle feiten en onderzoek van een vraagstuk, met filter op richting,
    sorteren en "toon meer" (P7: eerst 15, dan steeds 15 erbij, zonder JavaScript)."""
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
    my_reported_ids = _reported_fact_ids([f.id for f in visible])
    ids = [f.id for f in listed]
    return render_template(
        'issues/facts.html', issue=issue, thread=thread, facts=listed[:shown], listed_count=len(listed),
        total=len(visible), shown=shown, direction=direction, order=order,
        phase_open=thread is not None and _fact_phase_open(thread),
        conduct_cases=cases, fact_errors=fact_errors, fact_data=fact_data, return_to=issue.id,
        fact_marks=fact_signals.marked_ids(ids),
        fact_my_disputed=fact_signals.my_disputed(ids, current_user),
        my_reported_ids=my_reported_ids)


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


def _reported_fact_ids(fact_ids):
    """Welke van deze feiten (posts) de huidige gebruiker open heeft gemeld."""
    from app.models import PostReport
    fact_ids = list(fact_ids)
    if not current_user.is_authenticated or not fact_ids:
        return set()
    return {r.post_id for r in PostReport.query.filter(
        PostReport.reporter_id == current_user.id,
        PostReport.post_id.in_(fact_ids), PostReport.decision.is_(None))}


def _reported_proposal_ids(proposal_ids):
    """Welke van deze voorstellen de huidige gebruiker open heeft gemeld."""
    from app.models import ProposalReport
    proposal_ids = list(proposal_ids)
    if not current_user.is_authenticated or not proposal_ids:
        return set()
    return {r.proposal_id for r in ProposalReport.query.filter(
        ProposalReport.reporter_id == current_user.id,
        ProposalReport.proposal_id.in_(proposal_ids), ProposalReport.decision.is_(None))}


@issues_bp.route('/vraagstuk/<int:issue_id>/voorstel/<int:proposal_id>/melden', methods=['POST'])
@login_required
@limiter.limit("10 per hour", key_func=lambda: f"issue-report-user-{current_user.get_id()}")
def report_proposal(issue_id, proposal_id):
    """Meld een voorstel aan een moderator, met een verplichte reden.
    Niet op een eigen voorstel, één open melding per persoon."""
    from app.models import ProposalReport
    _require_csrf()
    issue = _issue_or_404(issue_id)
    proposal = _proposal_or_404(issue, proposal_id)
    if proposal.created_by_id == current_user.id:
        abort(403)
    nxt = safe_next(request.form.get('next'), url_for('issues.show', issue_id=issue.id))
    reason = (request.form.get('reason') or '').strip()
    if not reason:
        flash(gettext('Give a reason for your report.'), 'error')
        return redirect(nxt)
    already = ProposalReport.query.filter_by(proposal_id=proposal.id, reporter_id=current_user.id, decision=None).first()
    if already is None:
        db.session.add(ProposalReport(proposal_id=proposal.id, reporter_id=current_user.id, reason=reason[:500]))
        db.session.commit()
        flash(gettext('Thanks, your report has been sent to a moderator.'), 'success')
    return redirect(nxt)


@issues_bp.route('/vraagstuk/<int:issue_id>/voorstellen')
def proposals(issue_id):
    issue = _issue_or_404(issue_id)
    which = request.args.get('filter')
    which = which if which in ip.FILTERS else 'alle'
    order = request.args.get('sorteer')
    order = order if order in ip.ORDERS else ip.DEFAULT_ORDER
    rows = ip.listing(issue, which, order)
    my_reported_proposal_ids = _reported_proposal_ids([p.id for p, _c in rows])
    return render_template('issues/proposals.html', issue=issue, rows=rows, which=which, order=order,
                           total=ip.regular_query(issue).count(),  # vóór het filter: sorteren en filteren pas bij > 5
                           my_supports=ip.my_supports([p.id for p, _n in rows], current_user),
                           my_reported_proposal_ids=my_reported_proposal_ids,
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
            keep = ('statement', 'explanation', 'fact_title', 'fact_text', 'fact_direction', 'fact_source')
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
    """The proposal screen (docs/design/voorstel.html), in the same three-column
    frame as the issue page: facts on the left, the proposal with its earlier
    versions collapsed above the current one and the reactions grouped per
    version in the middle, the issue's proposals with their reaction counts on
    the right."""
    issue = _issue_or_404(issue_id)
    proposal = _proposal_or_404(issue, proposal_id)
    count = ip.support_counts([proposal.id]).get(proposal.id, 0)
    supported = proposal.id in ip.my_supports([proposal.id], current_user)
    # Eén formulierstaat per detailpagina: errors['revise'] of errors['fact'].
    errors, data = _pop_form_state(f'voorstel-{proposal.id}')
    links = ip.linked(proposal, current_user)
    linked_ids = {fact.id for _link, fact in links}
    issue_facts = ip.visible_issue_facts(issue, current_user)
    issue_fact_ids = [f.id for f in issue_facts]
    # The rail: the same proposals as the cockpit (voting list first), with
    # the summary proposal on top when there is one.
    rail_proposals = ip.listing(issue, order='stemlijst')
    final = ip.final_proposal(issue)
    if final is not None:
        rail_proposals.insert(0, (final, ip.support_counts([final.id]).get(final.id, 0)))
    # Discussie stap 3: eerst het openen onthouden, dan de markeringen, zodat
    # dit voorstel in de eigen kolom rechts grijs staat.
    if current_user.is_authenticated:
        ip.mark_read(proposal, current_user)
        db.session.commit()
    reaction_counts, new_discussion = _reaction_stats([p for p, _c in rail_proposals])
    return render_template('issues/proposal.html', issue=issue, proposal=proposal, count=count,
                           supported=supported, can_support=ip.can_support(proposal, current_user, count, supported),
                           can_revise=ip.can_revise(proposal, current_user), errors=errors, form_data=data,
                           links=links, can_link=ip.can_link(proposal, current_user),
                           can_unlink=lambda row: ip.can_unlink(row, current_user),
                           candidates=[f for f in issue_facts if f.id not in linked_ids],
                           can_add_fact=_can_add_fact(issue), vote=_vote_context(proposal, count),
                           versions=_versions_with_reactions(proposal), current_version=ip.current_version(proposal),
                           can_react=_can_react(issue), remaining=issues.remaining(issue),
                           rail_facts=fact_signals.listing(issue_facts, order='kwaliteit'),
                           rail_proposals=rail_proposals, rail_shown=RAIL_SHOWN,
                           rail_versions={p.id: ip.current_version(p) for p, _c in rail_proposals},
                           reaction_counts=reaction_counts, new_discussion=new_discussion,
                           fact_marks=fact_signals.marked_ids(issue_fact_ids),
                           my_reported_ids=_reported_fact_ids(issue_fact_ids))


def _versions_with_reactions(proposal):
    """[{'version', 'reactions'}] oldest first: each version with the visible
    reactions made on it. A reaction without a version number (from before
    versions were recorded) goes with the current version."""
    rows = ip.versions(proposal)
    by_number = {v.number: [] for v in rows}
    newest = rows[-1].number if rows else 1
    for post in _visible_reactions(proposal):
        by_number.setdefault(post.proposal_version if post.proposal_version in by_number else newest, []).append(post)
    return [{'version': v, 'reactions': by_number.get(v.number, [])} for v in rows]


def _visible_to_reader(posts):
    """De berichten die deze lezer ziet (verplaatst of verborgen is weg, zoals in de discussie)."""
    cases = conduct.cases_for([p.id for p in posts])
    return [p for p in posts if p.id not in cases or not conduct.removed_for(p, cases[p.id], current_user)]


def _visible_reactions(proposal):
    """Polijstronde P5: de reacties bij dit voorstel die deze lezer ziet."""
    return _visible_to_reader(ip.reactions(proposal))


def _reaction_stats(proposals):
    """({proposal_id: n}, {proposal_id: nieuw}) in één ronde over de reacties
    die deze lezer ziet, over alle versies (dezelfde telling als op de
    voorstelpagina). Nieuw (discussie stap 3): een zichtbare reactie van na
    het laatste openen van het voorstel door deze lezer, of nooit geopend en er
    is minstens één reactie. Een anonieme bezoeker: nooit nieuw."""
    ids = [p.id for p in proposals]
    counts, latest = dict.fromkeys(ids, 0), {}
    if ids:
        for post in _visible_to_reader(ip.reactions_for(ids)):
            pid = post.issue_proposal_id
            counts[pid] += 1
            if post.created_at is not None and (pid not in latest or post.created_at > latest[pid]):
                latest[pid] = post.created_at
    new = dict.fromkeys(ids, False)
    if current_user.is_authenticated:
        seen = ip.last_seen(ids, current_user)
        for pid in ids:
            if counts[pid]:
                new[pid] = pid not in seen or (pid in latest and latest[pid] > seen[pid])
    return counts, new


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
    return redirect(safe_next(request.form.get('next'), url_for('issues.show', issue_id=issue.id)))


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
    back = safe_next(request.form.get('next'), url_for('issues.show', issue_id=issue.id))
    if request.form.get('confirm') != '1':
        flash(gettext('Tick the box to confirm that you want to reopen the issue.'), 'error')
        return redirect(back if request.form.get('next') else url_for('issues.show', issue_id=issue.id) + '#beheer',
                        code=303)
    lifecycle.reopen(issue)
    db.session.commit()
    flash(gettext('The issue is open again for discussion.'), 'success')
    return redirect(back)


# ── Archiving (delete flow step 1, Toegang.dc.html): admin only ───────

@issues_bp.route('/vraagstuk/<int:issue_id>/archiveren', methods=['POST'])
@login_required
def archive_issue(issue_id):
    """Archive an issue: hidden from the public listings, closed for new
    contributions, still reachable by its URL so an admin can restore it."""
    if not current_user.is_admin:
        abort(403)
    issue = _issue_or_404(issue_id)
    _require_csrf()
    issue.archived_at = datetime.utcnow()
    issue.is_closed = True
    # The discussion checks the thread, not the issue: close it too, so no new
    # replies come in. Reopening (lifecycle.reopen) opens both again.
    if issue.thread is not None:
        issue.thread.is_closed = True
    db.session.commit()
    flash(gettext('The issue has been archived.'), 'success')
    return redirect(url_for('issues.show', issue_id=issue.id))


@issues_bp.route('/vraagstuk/<int:issue_id>/terug-uit-archief', methods=['POST'])
@login_required
def unarchive_issue(issue_id):
    """Take an issue out of the archive. It stays closed; reopening is a
    separate step (Heropenen)."""
    if not current_user.is_admin:
        abort(403)
    issue = _issue_or_404(issue_id)
    _require_csrf()
    issue.archived_at = None
    db.session.commit()
    flash(gettext('The issue is back from the archive.'), 'success')
    return redirect(url_for('issues.show', issue_id=issue.id))


# ── Deleting (phase 3, rights from delete-rights step 1): type the title ──

@issues_bp.route('/vraagstuk/<int:issue_id>/verwijderen', methods=['GET', 'POST'])
@login_required
def delete_issue(issue_id):
    """Delete a whole issue for good, for whoever issues.can_delete_issue
    allows, behind a confirmation where the title is typed over."""
    issue = _issue_or_404(issue_id)
    if not issues.can_delete_issue(current_user, issue):
        abort(403)
    if request.method == 'POST':
        _require_csrf()
        typed = (request.form.get('confirm_title') or '').strip()
        if typed != (issue.title or '').strip():
            flash(gettext('Type the exact title to confirm.'), 'error')
            return redirect(url_for('issues.delete_issue', issue_id=issue.id), code=303)
        target_id, title = issue.id, issue.title
        issues.purge(issue)
        issues.log_action(current_user, 'delete_issue', 'issue', target_id, title)
        db.session.commit()
        flash(gettext('The issue has been deleted.'), 'success')
        return redirect(url_for('issues.index'))
    return render_template('issues/verwijderen.html', target='issue', issue=issue,
                           counts=issues.purge_counts(issue))


@issues_bp.route('/vraagstuk/<int:issue_id>/voorstel/<int:proposal_id>/verwijderen', methods=['GET', 'POST'])
@login_required
def delete_proposal(issue_id, proposal_id):
    """Delete a single proposal for good (with its reactions), for whoever
    issues.can_delete_proposal allows, behind a confirmation where the
    statement is typed over."""
    issue = _issue_or_404(issue_id)
    proposal = _proposal_or_404(issue, proposal_id)
    if not issues.can_delete_proposal(current_user, proposal):
        abort(403)
    if request.method == 'POST':
        _require_csrf()
        typed = (request.form.get('confirm_title') or '').strip()
        if typed != (proposal.statement or '').strip():
            flash(gettext('Type the exact text to confirm.'), 'error')
            return redirect(url_for('issues.delete_proposal', issue_id=issue.id, proposal_id=proposal.id), code=303)
        target_id, statement = proposal.id, proposal.statement
        issues.purge_proposal(proposal)
        issues.log_action(current_user, 'delete_proposal', 'proposal', target_id, statement)
        db.session.commit()
        flash(gettext('The proposal has been deleted.'), 'success')
        return redirect(url_for('issues.show', issue_id=issue.id))
    return render_template('issues/verwijderen.html', target='proposal', issue=issue, proposal=proposal,
                           counts=issues.purge_proposal_counts(proposal))


def _hide_toggle(issue_id, proposal_id, hide):
    issue = _issue_or_404(issue_id)
    proposal = _proposal_or_404(issue, proposal_id)
    if not issues.can_hide_proposal(current_user, proposal):
        abort(403)
    _require_csrf()
    changed = issues.hide(proposal, current_user) if hide else issues.unhide(proposal, current_user)
    if changed:
        db.session.commit()
        flash(gettext('The proposal is hidden.') if hide else gettext('The proposal is visible again.'), 'success')
    return redirect(safe_next(request.form.get('next'),
                              url_for('issues.proposal', issue_id=issue.id, proposal_id=proposal.id)))


@issues_bp.route('/vraagstuk/<int:issue_id>/voorstel/<int:proposal_id>/verbergen', methods=['POST'])
@login_required
def hide_proposal(issue_id, proposal_id):
    """Soft-hide a proposal (delete-rights step 1; the manage screen uses it later)."""
    return _hide_toggle(issue_id, proposal_id, hide=True)


@issues_bp.route('/vraagstuk/<int:issue_id>/voorstel/<int:proposal_id>/zichtbaar', methods=['POST'])
@login_required
def unhide_proposal(issue_id, proposal_id):
    """Make a hidden proposal visible again."""
    return _hide_toggle(issue_id, proposal_id, hide=False)
