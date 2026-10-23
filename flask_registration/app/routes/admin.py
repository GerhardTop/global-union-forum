from datetime import datetime, timedelta

from flask import Blueprint, render_template, redirect, url_for, request, abort, jsonify, flash, session
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

    gerhard = User.query.filter_by(email='top.gerhard@gmail.com').first()
    if gerhard:
        replied_thread_ids = db.session.query(Post.thread_id).filter_by(
            user_id=gerhard.id
        ).distinct()
        threads_no_gerhard = (
            Thread.query.filter_by(is_closed=False)
            .filter(~Thread.id.in_(replied_thread_ids.scalar_subquery()))
            .order_by(Thread.created_at.desc())
            .all()
        )
    else:
        threads_no_gerhard = Thread.query.filter_by(is_closed=False).order_by(Thread.created_at.desc()).all()

    recent_posts = (
        Post.query
        .filter(Post.body != '[bericht verwijderd]')
        .order_by(Post.created_at.desc())
        .limit(10)
        .all()
    )

    closed_threads = Thread.query.filter_by(is_closed=True).order_by(Thread.created_at.desc()).all()

    # Kwaliteit (alleen een beloning): open meldingen "geen kwaliteit" door
    # anderen, en recente bijdragen zonder oordeel (bijvoorbeeld omdat de AI
    # faalde). Er is geen bezwaarstroom meer.
    live = Post.body != '[bericht verwijderd]'
    open_reports = (QualityReport.query.filter(QualityReport.decision.is_(None))
                    .order_by(QualityReport.created_at).all())
    unjudged_posts = (Post.query.outerjoin(QualityAward, QualityAward.post_id == Post.id)
                      .filter(QualityAward.id.is_(None), Post.is_demo.is_(False), live)
                      .order_by(Post.created_at.desc()).limit(20).all())

    return render_template(
        'moderator/dashboard.html',
        open_reports=open_reports,
        unjudged_posts=unjudged_posts,
        active_threads=active_threads,
        posts_today=posts_today,
        threads_no_gerhard_count=len(threads_no_gerhard),
        threads_no_gerhard=threads_no_gerhard,
        recent_posts=recent_posts,
        closed_threads=closed_threads,
    )


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
