from datetime import datetime, timedelta
from flask_login import UserMixin
from app import db, login_manager


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(50), nullable=False)
    last_name = db.Column(db.String(50), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, nullable=False, default=False)
    is_moderator = db.Column(db.Boolean, nullable=False, default=False)
    verified = db.Column(db.Boolean, nullable=False, default=False)
    linkedin_url = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<User {self.email}>"


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


class Thread(db.Model):
    __tablename__ = "threads"

    id           = db.Column(db.Integer, primary_key=True)
    eyebrow_nl   = db.Column(db.String(200), nullable=False)
    eyebrow_en   = db.Column(db.String(200), nullable=False)
    title_prefix_nl = db.Column(db.String(300), nullable=False, default='')
    title_accent_nl = db.Column(db.String(200), nullable=True)
    title_suffix_nl = db.Column(db.String(50),  nullable=False, default='')
    title_prefix_en = db.Column(db.String(300), nullable=False, default='')
    title_accent_en = db.Column(db.String(200), nullable=True)
    title_suffix_en = db.Column(db.String(50),  nullable=False, default='')
    is_closed    = db.Column(db.Boolean, nullable=False, default=False)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    posts        = db.relationship('Post', backref='thread', lazy=True,
                                   order_by='Post.created_at')

    def title(self, lang):
        if lang == 'nl':
            return (self.title_prefix_nl, self.title_accent_nl, self.title_suffix_nl)
        return (self.title_prefix_en, self.title_accent_en, self.title_suffix_en)


class Post(db.Model):
    __tablename__ = "posts"

    id             = db.Column(db.Integer, primary_key=True)
    thread_id      = db.Column(db.Integer, db.ForeignKey('threads.id'), nullable=False)
    user_id        = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    parent_id      = db.Column(db.Integer, db.ForeignKey('posts.id'), nullable=True)
    author_name    = db.Column(db.String(100), nullable=False)
    author_role    = db.Column(db.String(20),  nullable=False, default='member')
    author_badge_nl = db.Column(db.String(200), nullable=True)
    author_badge_en = db.Column(db.String(200), nullable=True)
    author_age     = db.Column(db.Integer, nullable=True)
    body           = db.Column(db.Text, nullable=True)
    body_nl        = db.Column(db.Text, nullable=True)
    body_en        = db.Column(db.Text, nullable=True)
    source_lang    = db.Column(db.String(5), nullable=False, default='nl')
    is_op          = db.Column(db.Boolean, default=False)
    vote_count     = db.Column(db.Integer, default=0)
    image_url      = db.Column(db.String(500), nullable=True)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)
    author         = db.relationship('User', foreign_keys=[user_id], lazy='joined')

    @property
    def time_ago(self):
        delta = datetime.utcnow() - self.created_at
        d, s = delta.days, delta.seconds
        if d >= 2:
            return {'nl': f'{d} dagen geleden', 'en': f'{d} days ago'}
        if d == 1:
            return {'nl': '1 dag geleden', 'en': '1 day ago'}
        h = s // 3600
        if h >= 1:
            return {'nl': f'{h} uur geleden', 'en': f'{h} hour{"s" if h > 1 else ""} ago'}
        m = s // 60
        if m >= 1:
            return {'nl': f'{m} min geleden', 'en': f'{m} min ago'}
        return {'nl': 'zojuist', 'en': 'just now'}


class PostLike(db.Model):
    __tablename__ = "post_likes"

    id         = db.Column(db.Integer, primary_key=True)
    post_id    = db.Column(db.Integer, db.ForeignKey('posts.id'), nullable=False)
    user_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('post_id', 'user_id', name='uq_post_like'),)
