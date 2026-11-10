from datetime import datetime, timedelta

from flask import Blueprint, render_template, redirect, url_for, request, abort, jsonify, flash, session
from flask_babel import gettext, get_locale
from sqlalchemy import or_
from flask_login import login_required, current_user

from app import db
from app.forms import AdminActionForm
from app.models import ROLES, User, Thread, Post, PostLike, QualityAward, QualityReport

admin_bp = Blueprint("admin", __name__)


@admin_bp.route('/admin')
@login_required
def admin_dashboard():
    if not current_user.is_admin:
        abort(403)

    week_ago      = datetime.utcnow() - timedelta(days=7)
    total_users   = User.query.count()
    new_this_week = User.query.filter(User.created_at >= week_ago).count()

    active_threads = Thread.query.filter_by(is_closed=False).count()
    closed_threads = Thread.query.filter_by(is_closed=True).count()
    total_posts    = Post.query.count()
    demo_posts     = Post.query.filter_by(is_demo=True).count()

    users = User.query.order_by(User.created_at.desc()).all()

    return render_template(
        'admin/dashboard.html',
        total_users=total_users,
        new_this_week=new_this_week,
        active_threads=active_threads,
        closed_threads=closed_threads,
        total_posts=total_posts,
        demo_posts=demo_posts,
        users=users,
        admin_action_form=AdminActionForm(),
    )


def _phase_issues():
    from app import issue_lifecycle as lifecycle
    return [(i, lifecycle.next_phase(i), lifecycle.can_step(i, current_user), lifecycle.can_reopen(i, current_user))
            for i in lifecycle.manageable_issues()]


MOD_STREAMS = ('alle', 'meldingen', 'ai', 'bezwaren', 'zonder')
# The bin extras (docs/design/main.html): sort keys, and rows per section.
MOD_SORTS = ('wachtend', 'nieuwste', 'meldingen', 'vraagstuk')
MOD_SECTION_LIMIT = 10


def _post_issue(post):
    thread = post.thread if post is not None else None
    issues = getattr(thread, 'issues', None) or []
    return issues[0] if issues else None


def _context_title(issue, post=None):
    """The vraagstuk title an item belongs to (the issue, else its old-style
    thread, both languages), or '' when there is none."""
    if issue is not None:
        return issue.title or ''
    thread = post.thread if post is not None else None
    if thread is None:
        return ''
    return ' '.join(p for lang in ('nl', 'en') for p in thread.title(lang) if p)


def _arrange(items, title_of, at_of, count_of=None, zoek='', sorteer='wachtend', show_all=False):
    """Search, sort and limit one bin section. Returns (shown, total), where
    total is the number after searching (before the limit). The default
    'wachtend' is longest waiting first (oldest first)."""
    rows = [(item, title_of(item) or '') for item in items]
    if zoek:
        needle = zoek.casefold()
        rows = [(item, t) for item, t in rows if t and needle in t.casefold()]
    oldest = datetime.min
    if sorteer == 'wachtend':
        rows.sort(key=lambda r: at_of(r[0]) or oldest)
    elif sorteer == 'nieuwste':
        rows.sort(key=lambda r: at_of(r[0]) or oldest, reverse=True)
    elif sorteer == 'meldingen':
        rows.sort(key=lambda r: at_of(r[0]) or oldest)
        if count_of is not None:
            rows.sort(key=lambda r: count_of(r[0]), reverse=True)
    elif sorteer == 'vraagstuk':
        rows.sort(key=lambda r: at_of(r[0]) or oldest)
        rows.sort(key=lambda r: (not r[1], r[1].casefold()))
    total = len(rows)
    shown = [item for item, _t in rows]
    return (shown if show_all else shown[:MOD_SECTION_LIMIT]), total


def _phase_labels():
    """De leesbare naam van elke vraagstukfase (overzicht en beoordeelscherm)."""
    return {'feiten': gettext('Facts and discussion'), 'bijstellen': gettext('Revise the proposal'),
            'stemmen': gettext('Voting'), 'samenvatten': gettext('Summary proposal'),
            'vervolg': gettext('Follow-up')}


@admin_bp.route('/moderator')
@login_required
def moderator_dashboard():
    if not current_user.can_moderate():
        abort(403)

    active_threads = Thread.query.filter_by(is_closed=False).count()

    today = datetime.utcnow().date()
    start_of_day = datetime(today.year, today.month, today.day)
    end_of_day = start_of_day + timedelta(days=1)
    posts_today = Post.query.filter(
        Post.created_at >= start_of_day,
        Post.created_at < end_of_day,
    ).count()

    closed_threads = Thread.query.filter_by(is_closed=True).order_by(Thread.created_at.desc()).all()

    # Kwaliteit (alleen een beloning): open meldingen "geen kwaliteit" door
    # anderen, en recente bijdragen zonder oordeel (bijvoorbeeld omdat de AI
    # faalde). Er is geen bezwaarstroom meer.
    live = Post.body != '[bericht verwijderd]'
    open_reports = (QualityReport.query.filter(QualityReport.decision.is_(None))
                    .order_by(QualityReport.created_at).all())
    # Fase 2c: een verplaatste of verborgen bijdrage hoort hier niet (geen
    # publieke context meer, de link liep dood); die staat bij de gedragsregels.
    from app.models import ConductCase, ConductObjection
    removed = (db.session.query(ConductCase.id)
               .filter(ConductCase.post_id == Post.id,
                       ConductCase.action.in_(('verplaatst', 'verborgen')))
               .exists())
    unjudged_posts = (Post.query.outerjoin(QualityAward, QualityAward.post_id == Post.id)
                      .filter(QualityAward.id.is_(None), Post.is_demo.is_(False), live, ~removed,
                              or_(Post.kind.is_(None), Post.kind != 'feit'))  # feiten: geen keurmerk
                      .order_by(Post.created_at.desc()).limit(20).all())

    # Fase D: gedragsregels. Twijfelgevallen (blijven staan tot een moderator
    # oordeelt) en openstaande bezwaren tegen een verplaatsing of verberging.
    doubt_cases = (ConductCase.query.join(Post, ConductCase.post_id == Post.id)
                   .filter(ConductCase.action == 'twijfel', live)
                   .order_by(ConductCase.created_at).all())
    # Fase 2c: wat de AI zelf verplaatste of verborg (geen moderator), zodat
    # een moderator dat vanaf het overzicht kan terugplaatsen.
    ai_removed = (ConductCase.query.join(Post, ConductCase.post_id == Post.id)
                  .filter(ConductCase.acted_by_id.is_(None),
                          ConductCase.action.in_(('verplaatst', 'verborgen')), live)
                  .order_by(ConductCase.created_at).all())
    open_conduct_objections = (ConductObjection.query.filter(ConductObjection.decision.is_(None))
                               .order_by(ConductObjection.created_at).all())
    # Herontwerp stap 3: betwiste feiten (het feit blijft staan tot een moderator oordeelt).
    from app import facts
    fact_disputes = [(row, facts.fact_url(row.fact)) for row in facts.open_disputes() if row.fact is not None]

    # Fase 2: de meldknop-meldingen (bijdragen, feiten en voorstellen), gebundeld
    # per item, plus kengetallen en recente vraagstukken waar de moderator aan werkte.
    from app import reports
    from app.models import Issue
    meldingen = reports.open_meldingen()
    handled_total, handled_mine = reports.handled_counts(current_user)
    oldest_at = reports.oldest_open_at()

    # Fase 2c: één bak met tabbladen; ?stroom= filtert welke groepen je ziet.
    stroom = request.args.get('stroom')
    if stroom not in MOD_STREAMS:
        stroom = 'alle'
    stream_counts = {
        'meldingen': len(meldingen) + len(open_reports) + len(fact_disputes),
        'ai': len(doubt_cases) + len(ai_removed),
        'bezwaren': len(open_conduct_objections),
        'zonder': len(unjudged_posts),
    }
    stream_counts['alle'] = sum(stream_counts.values())

    # The bin extras: ?zoek= (vraagstuk title), ?sorteer=, ?alle=1 (no limit).
    # The tab counts above stay the real totals (the backlog).
    zoek = (request.args.get('zoek') or '').strip()[:100]
    sorteer = request.args.get('sorteer')
    if sorteer not in MOD_SORTS:
        sorteer = 'wachtend'
    show_all = request.args.get('alle') == '1'

    def arrange(items, post_of, at_of, count_of=None, issue_of=None):
        issue_of = issue_of or (lambda item: _post_issue(post_of(item)))
        return _arrange(items, lambda item: _context_title(issue_of(item), post_of(item)), at_of,
                        count_of, zoek, sorteer, show_all)

    section_total = {}
    meldingen, section_total['meldingen'] = arrange(
        meldingen, lambda m: m.item if m.kind == 'post' else None, lambda m: m.oldest_at,
        count_of=lambda m: m.count, issue_of=lambda m: m.issue)
    open_reports, section_total['kwaliteit'] = arrange(
        open_reports, lambda r: r.award.post, lambda r: r.created_at)
    fact_disputes, section_total['betwist'] = arrange(
        fact_disputes, lambda fd: fd[0].fact, lambda fd: fd[0].created_at)
    open_conduct_objections, section_total['bezwaren'] = arrange(
        open_conduct_objections, lambda o: o.case.post, lambda o: o.created_at)
    doubt_cases, section_total['twijfel'] = arrange(
        doubt_cases, lambda c: c.post, lambda c: c.created_at)
    ai_removed, section_total['ai-verwijderd'] = arrange(
        ai_removed, lambda c: c.post, lambda c: c.created_at)
    unjudged_posts, section_total['zonder'] = arrange(
        unjudged_posts, lambda p: p, lambda p: p.created_at)
    shown_groups = {'alle': section_total.keys(),
                    'meldingen': ('meldingen', 'kwaliteit', 'betwist'), 'bezwaren': ('bezwaren',),
                    'ai': ('twijfel', 'ai-verwijderd'), 'zonder': ('zonder',)}[stroom]
    bin_matches = sum(section_total[k] for k in shown_groups)

    return render_template(
        'moderator/dashboard.html',
        meldingen=meldingen,
        stroom=stroom,
        stream_counts=stream_counts,
        zoek=zoek, sorteer=sorteer, show_all=show_all,
        section_total=section_total, bin_matches=bin_matches,
        handled_total=handled_total,
        handled_mine=handled_mine,
        oldest_wait=reports.wait_bucket(oldest_at),
        wait_bucket=reports.wait_bucket,  # fase 2c: leeftijd-pill per rij
        open_issues=Issue.query.filter(Issue.is_closed.is_(False), Issue.archived_at.is_(None)).count(),
        recent_issues=[(issue, reports.wait_bucket(dt)) for issue, dt in reports.recent_for(current_user)],
        open_reports=open_reports,
        unjudged_posts=unjudged_posts,
        doubt_cases=doubt_cases,
        ai_removed=ai_removed,
        open_conduct_objections=open_conduct_objections,
        fact_disputes=fact_disputes,
        fact_can_resolve=lambda row: facts.can_resolve(row, current_user),
        # Polijststap P7: de fasen van vraagstukken beheren moderators hier.
        phase_issues=_phase_issues(),
        phase_labels_all=_phase_labels(),
        active_threads=active_threads,
        posts_today=posts_today,
        closed_threads=closed_threads,
    )


_MONTHS = {'nl': ('jan', 'feb', 'mrt', 'apr', 'mei', 'jun', 'jul', 'aug', 'sep', 'okt', 'nov', 'dec'),
           'en': ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')}


@admin_bp.route('/moderator/gebruiker/<int:user_id>')
@login_required
def gebruiker(user_id):
    """User moderation (docs/design/gebruiker.html): the dossier of one user
    (identity, figures, enforcement history, recent contributions) and the
    measures. Step 2 builds only the warning; suspending and deleting follow."""
    if not current_user.can_moderate():
        abort(403)
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    from app import facts, users
    from app.models import time_ago
    from app.routes.forum import post_url

    def item_url(c):
        case = c.post.conduct_case
        if c.status in ('verplaatst', 'verborgen'):
            return (url_for('forum.achtergrond') + f'#zaak-{case.id}') if case.on_background else None
        return facts.fact_url(c.post) if c.post.is_fact else post_url(c.post)

    contributions = [(c, item_url(c)) for c in users.recent_contributions(user)]
    since = None
    if user.created_at:
        since = {k: f'{m[user.created_at.month - 1]} {user.created_at.year}' for k, m in _MONTHS.items()}
    return render_template(
        'moderator/gebruiker.html', user=user, stats=users.user_stats(user),
        history=users.moderation_history(user), contributions=contributions,
        initials=users.initials(user), member_since=since, time_ago=time_ago,
    )


@admin_bp.route('/moderator/gebruiker/<int:user_id>/waarschuwing', methods=['POST'])
@login_required
def gebruiker_waarschuwing(user_id):
    """Send a warning: a Warning row that shows in the user's history."""
    if not current_user.can_moderate():
        abort(403)
    from app.routes.forum import _require_csrf
    _require_csrf()
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    if user.id == current_user.id:
        abort(403)
    nl = str(get_locale() or 'nl') == 'nl'
    message = (request.form.get('message') or '').strip()
    if not message:
        flash('Schrijf eerst een bericht aan de gebruiker.' if nl
              else 'Write a message to the user first.', 'error')
    else:
        from app.models import Warning
        db.session.add(Warning(user_id=user.id, by_id=current_user.id, message=message[:2000]))
        db.session.commit()
        flash('De waarschuwing is verstuurd.' if nl else 'The warning has been sent.', 'success')
    return redirect(url_for('admin.gebruiker', user_id=user.id))


SUSPENSION_DAYS = (7, 30)


@admin_bp.route('/moderator/gebruiker/<int:user_id>/schorsen', methods=['POST'])
@login_required
def gebruiker_schorsen(user_id):
    """Suspend a user for 7 or 30 days (senior moderators and the admin). The
    suspension lifts by itself when suspended_until passes."""
    if not current_user.is_senior_moderator:
        abort(403)
    from app.routes.forum import _require_csrf
    _require_csrf()
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    if user.id == current_user.id or user.is_admin:
        abort(403)
    nl = str(get_locale() or 'nl') == 'nl'
    try:
        days = int(request.form.get('duur') or 0)
    except ValueError:
        days = 0
    reason = (request.form.get('reason') or '').strip()
    if days not in SUSPENSION_DAYS:
        flash('Kies 7 of 30 dagen.' if nl else 'Choose 7 or 30 days.', 'error')
    elif not reason:
        flash('Schrijf eerst de reden van de schorsing.' if nl
              else 'Write the reason for the suspension first.', 'error')
    else:
        from app.models import Suspension
        until = datetime.utcnow() + timedelta(days=days)
        db.session.add(Suspension(user_id=user.id, by_id=current_user.id, reason=reason[:2000], until=until))
        user.suspended_until = until
        db.session.commit()
        flash((f'@{user.username} is geschorst voor {days} dagen.' if nl
               else f'@{user.username} has been suspended for {days} days.'), 'success')
    return redirect(url_for('admin.gebruiker', user_id=user.id))


def _deletable_user_or_abort(user_id):
    """The account a senior moderator or admin may delete: not your own, not an
    admin, not one already deleted (403/404 otherwise)."""
    if not current_user.is_senior_moderator:
        abort(403)
    user = db.session.get(User, user_id)
    if user is None or user.is_deleted:
        abort(404)
    if user.id == current_user.id or user.is_admin:
        abort(403)
    return user


@admin_bp.route('/moderator/gebruiker/<int:user_id>/verwijderen', methods=['GET', 'POST'])
@login_required
def gebruiker_verwijderen(user_id):
    """Delete an account (docs/design/verwijderen.html, adapted): a confirmation
    screen, then anonymise. The row stays so all contributions keep working."""
    user = _deletable_user_or_abort(user_id)
    from app import users
    nl = str(get_locale() or 'nl') == 'nl'
    reason = (request.values.get('reden') or '').strip()[:2000]
    if request.method == 'POST':
        from app.routes.forum import _require_csrf
        _require_csrf()
        typed = (request.form.get('confirm_username') or '').strip().lstrip('@')
        if typed != user.username:
            flash('De gebruikersnaam klopt niet. Er is niets verwijderd.' if nl
                  else 'The username does not match. Nothing was deleted.', 'error')
            return redirect(url_for('admin.gebruiker_verwijderen', user_id=user.id, reden=reason or None))
        old = user.username
        users.anonymise(user, current_user, reason or None)
        db.session.commit()
        flash((f'Het account @{old} is verwijderd. De bijdragen staan nu onder @verwijderd.' if nl
               else f'The account @{old} has been deleted. Its contributions now show as @verwijderd.'),
              'success')
        return redirect(url_for('admin.moderator_dashboard'))
    return render_template('moderator/gebruiker_verwijderen.html', user=user, reason=reason,
                           effects=users.deletion_effects(user))


@admin_bp.route('/moderator/melding/<kind>/<int:item_id>', methods=['GET', 'POST'])
@login_required
def melding_beoordelen(kind, item_id):
    """Een gemeld item beoordelen: de bijdrage of het voorstel in context, de
    redenen van de melders, en een besluit (niets doen, verplaatsen, verbergen).
    Verplaatsen en verbergen gelden alleen voor een bijdrage of feit."""
    if not current_user.can_moderate():
        abort(403)
    from app import conduct, reports
    if kind in ('bijdrage', 'feit', 'post'):
        kind = 'post'
    elif kind in ('voorstel', 'proposal'):
        kind = 'proposal'
    else:
        abort(404)
    melding = reports.find(kind, item_id)
    if melding is None:
        abort(404)

    if request.method == 'POST':
        from app.routes.forum import _require_csrf
        _require_csrf()
        if kind == 'post' and melding.item.user_id == current_user.id:
            abort(403)
        if kind == 'proposal' and melding.item.created_by_id == current_user.id:
            abort(403)
        action = request.form.get('action')
        note = (request.form.get('note') or '').strip()
        rule = request.form.get('rule')
        lang = str(get_locale() or 'nl')
        if action == 'verplaatsen' and kind == 'post':
            conduct.move(melding.item, rule if rule in ('respect', 'onderwerp') else 'respect',
                         note, lang, actor=current_user)
            reports.resolve(melding, 'verplaatst', note, current_user)
        elif action == 'verbergen' and kind == 'post':
            conduct.hide(melding.item, note, lang, actor=current_user)
            reports.resolve(melding, 'verborgen', note, current_user)
        elif action == 'handhaven':
            reports.resolve(melding, 'handhaven', note, current_user)
        else:
            flash(gettext('Choose what to do with this report.'), 'error')
            return redirect(url_for('admin.melding_beoordelen', kind=kind, item_id=item_id))
        db.session.commit()
        flash(gettext('The report has been handled.'), 'success')
        return redirect(url_for('admin.moderator_dashboard'))

    if melding.kind == 'post':
        from app.routes.forum import post_url
        context_url = post_url(melding.item)
    elif melding.issue is not None:
        context_url = url_for('issues.proposal', issue_id=melding.issue.id, proposal_id=melding.item.id)
    else:
        context_url = '#'
    # Fase 2c: het gesprek eromheen (de eerste zichtbare reactie eronder) en de
    # meldcijfers bij de namen van de auteur en de melders.
    reply = None
    if melding.kind == 'post':
        from app.models import ConductCase
        removed = (db.session.query(ConductCase.id)
                   .filter(ConductCase.post_id == Post.id,
                           ConductCase.action.in_(('verplaatst', 'verborgen')))
                   .exists())
        reply = (Post.query.filter(Post.parent_id == melding.item.id,
                                   Post.body != '[bericht verwijderd]', ~removed)
                 .order_by(Post.created_at, Post.id).first())
    author = melding.item.author if melding.kind == 'post' else melding.item.created_by
    activity = {}
    for user in [author] + [r.reporter for r in melding.reports]:
        if user is not None and user.id not in activity:
            activity[user.id] = reports.user_activity(user)
    return render_template('moderator/melding_beoordelen.html', m=melding, context_url=context_url,
                           phase_labels=_phase_labels(), reply=reply,
                           author_activity=activity.get(author.id) if author is not None else None,
                           activity=activity)


@admin_bp.route('/admin/gebruiker/<int:user_id>/rol', methods=['POST'])
@login_required
def admin_set_rol(user_id):
    if not current_user.is_admin:
        abort(403)
    if not AdminActionForm().validate_on_submit():
        return jsonify({'error': 'invalid csrf token'}), 400
    if user_id == current_user.id:
        return jsonify({'error': 'cannot change own role'}), 400
    user = User.query.get_or_404(user_id)
    role = request.form.get('role', '')
    # Forum fase A: handmatig de contentrol zetten (vervangt de automatische
    # promotie tot fase C). 'gebruiker' is de oude waarde voor contributor.
    if role == 'gebruiker':
        role = 'contributor'
    if role == 'admin':
        user.is_admin = True
        user.is_moderator = True
    elif role in ROLES:
        user.is_admin = False
        user.set_role(role)
    else:
        return jsonify({'error': 'invalid role'}), 400
    db.session.commit()
    return jsonify({'ok': True, 'role': role})


@admin_bp.route('/admin/gebruiker/<int:user_id>/verwijder', methods=['POST'])
@login_required
def admin_verwijder_gebruiker(user_id):
    if not current_user.is_admin:
        abort(403)
    if not AdminActionForm().validate_on_submit():
        abort(400)
    user = User.query.get_or_404(user_id)
    if user.is_admin:
        abort(400)

    uid = user.id
    from app import quality
    quality.detach_user(uid)
    PostLike.query.filter_by(user_id=uid).delete()
    user_post_ids = [
        p.id for p in Post.query.filter_by(user_id=uid).with_entities(Post.id).all()
    ]
    if user_post_ids:
        PostLike.query.filter(
            PostLike.post_id.in_(user_post_ids)
        ).delete(synchronize_session=False)
        Post.query.filter(Post.parent_id.in_(user_post_ids)).update(
            {Post.parent_id: None}, synchronize_session=False
        )
        quality.delete_for_posts(user_post_ids)
        Post.query.filter_by(user_id=uid).delete(synchronize_session=False)

    db.session.delete(user)
    db.session.commit()
    return redirect(url_for('admin.admin_dashboard'))


# ── Forum fase C2: drempels en taal-monitor ───────────────────────────────

@admin_bp.route('/admin/drempels', methods=['GET', 'POST'])
@login_required
def admin_thresholds():
    """Admin stelt de drempels voor de verdiende status in. Gelden vanaf het
    opslaan; er wordt niemand herberekend (promotie blijft alleen omhoog)."""
    from app import status
    if not current_user.is_admin:
        abort(403)
    form = AdminActionForm()
    errors = {}
    if request.method == 'POST':
        if not form.validate_on_submit():
            abort(400)
        values = {}
        for key in status.DEFAULT_THRESHOLDS:
            raw = (request.form.get(key) or '').strip()
            if not raw.isdigit() or not (1 <= int(raw) <= 100000):
                errors[key] = True
            else:
                values[key] = int(raw)
        if not errors:
            status.set_thresholds(values, user_id=current_user.id)
            flash('Drempels opgeslagen.' if session.get('lang', 'nl') == 'nl' else 'Thresholds saved.', 'success')
            return redirect(url_for('admin.admin_thresholds'))
    return render_template('admin/thresholds.html', thresholds=status.get_thresholds(),
                           labels=status.THRESHOLD_LABELS, defaults=status.DEFAULT_THRESHOLDS,
                           errors=errors, form_values=request.form if errors else None,
                           admin_action_form=form)


@admin_bp.route('/admin/voorstellen', methods=['GET', 'POST'])
@login_required
def admin_proposal_limits():
    """Fase E: de admin stelt per fase de maximale doorlooptijd van een
    publiek voorstel in (vangrail; standaard 60, 10 en 14 dagen). Gelden voor
    nieuwe voorstellen; bestaande planningen veranderen niet."""
    from app import proposals
    if not current_user.is_admin:
        abort(403)
    form = AdminActionForm()
    errors = {}
    if request.method == 'POST':
        if not form.validate_on_submit():
            abort(400)
        values = {}
        for phase in proposals.DEFAULT_MAX_DAYS:
            raw = (request.form.get(phase) or '').strip()
            if not raw.isdigit() or not (1 <= int(raw) <= 365):
                errors[phase] = True
            else:
                values[phase] = int(raw)
        if not errors:
            proposals.set_max_days(values, user_id=current_user.id)
            flash(gettext('Maximum durations saved.'), 'success')
            return redirect(url_for('admin.admin_proposal_limits'))
    from app import issue_lifecycle
    return render_template('admin/proposal_limits.html', max_days=proposals.get_max_days(),
                           defaults=proposals.DEFAULT_MAX_DAYS, errors=errors,
                           form_values=request.form if errors else None, admin_action_form=form,
                           silence_days=issue_lifecycle.get_silence_days(),
                           silence_default=issue_lifecycle.DEFAULT_SILENCE_DAYS)


@admin_bp.route('/admin/vraagstukken/stilte', methods=['POST'])
@login_required
def admin_issue_silence():
    """Herontwerp stap 7: na hoeveel dagen zonder nieuw keurmerk de
    feitenfase van een online vraagstuk sluit (standaard 5)."""
    from app import issue_lifecycle
    if not current_user.is_admin:
        abort(403)
    if not AdminActionForm().validate_on_submit():
        abort(400)
    raw = (request.form.get('silence_days') or '').strip()
    if not raw.isdigit() or not issue_lifecycle.SILENCE_MIN <= int(raw) <= issue_lifecycle.SILENCE_MAX:
        flash(gettext('Enter a whole number from 1 to 365.'), 'error')
    else:
        issue_lifecycle.set_silence_days(int(raw), user_id=current_user.id)
        flash(gettext('Saved.'), 'success')
    return redirect(url_for('admin.admin_proposal_limits') + '#stilte')


@admin_bp.route('/admin/taalmonitor')
@login_required
def language_monitor():
    """Toekenningspercentage van de AI per taal van de bijdrage (source_lang),
    met periodefilter en een steekproeflijst voor handmatige controle."""
    from app import status
    if not current_user.can_moderate():
        abort(403)
    period = request.args.get('periode', '30')
    if period not in status.PERIODS:
        period = '30'
    sample_lang = request.args.get('taal')
    if sample_lang not in ('nl', 'en'):
        sample_lang = None
    return render_template('admin/language_monitor.html', period=period,
                           stats=status.language_monitor(period),
                           sample=status.monitor_sample(sample_lang), sample_lang=sample_lang)
