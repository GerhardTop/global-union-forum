from datetime import datetime, timedelta

from flask import Blueprint, render_template, redirect, url_for, request, abort, jsonify
from flask_login import login_required, current_user

from app import db
from app.models import User, Thread, Post, PostLike

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
    )


@admin_bp.route('/moderator')
@login_required
def moderator_dashboard():
    if not (current_user.is_admin or current_user.is_moderator):
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

    return render_template(
        'moderator/dashboard.html',
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
    if user_id == current_user.id:
        return jsonify({'error': 'cannot change own role'}), 400
    user = User.query.get_or_404(user_id)
    role = request.form.get('role', '')
    if role == 'admin':
        user.is_admin = True
        user.is_moderator = True
    elif role == 'moderator':
        user.is_admin = False
        user.is_moderator = True
    elif role == 'gebruiker':
        user.is_admin = False
        user.is_moderator = False
    else:
        return jsonify({'error': 'invalid role'}), 400
    db.session.commit()
    return jsonify({'ok': True, 'role': role})


@admin_bp.route('/admin/gebruiker/<int:user_id>/verwijder', methods=['POST'])
@login_required
def admin_verwijder_gebruiker(user_id):
    if not current_user.is_admin:
        abort(403)
    user = User.query.get_or_404(user_id)
    if user.is_admin:
        abort(400)

    uid = user.id
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
        Post.query.filter_by(user_id=uid).delete(synchronize_session=False)

    db.session.delete(user)
    db.session.commit()
    return redirect(url_for('admin.admin_dashboard'))
