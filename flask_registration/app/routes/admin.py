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

    return render_template(
        'moderator/dashboard.html',
        meldingen=meldingen,
        stroom=stroom,
        stream_counts=stream_counts,
        handled_total=handled_total,
        handled_mine=handled_mine,
        oldest_wait=reports.wait_bucket(oldest_at),
        wait_bucket=reports.wait_bucket,  # fase 2c: leeftijd-pill per rij
        open_issues=Issue.query.filter(Issue.is_closed.is_(False)).count(),
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
        phase_labels_all={'feiten': gettext('Facts and discussion'), 'bijstellen': gettext('Revise the proposal'),
                          'stemmen': gettext('Voting'), 'samenvatten': gettext('Summary proposal'),
                          'vervolg': gettext('Follow-up')},
        active_threads=active_threads,
        posts_today=posts_today,
        closed_threads=closed_threads,
    )


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
    return render_template('moderator/melding_beoordelen.html', m=melding, context_url=context_url)


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
