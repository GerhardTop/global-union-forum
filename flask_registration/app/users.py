"""User moderation (Gebruiker.dc.html), step 1: the figures and the assembled
enforcement history for a user's dossier. No UI and no actions yet; the page
(step 2) renders user_stats() and moderation_history()."""
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import or_

from app.models import (FACT_KIND, REMOVING_ACTIONS, ConductCase, Post, QualityAward,
                        Suspension, Warning)
from app.reports import user_activity


def user_stats(user):
    """The dossier figures for a user. A handful of count queries; the report
    figures reuse reports.user_activity (made, received, upheld)."""
    own = Post.query.filter(Post.user_id == user.id)
    activity = user_activity(user) or {'made': 0, 'received': 0, 'upheld': 0}
    return {
        'bijdragen': own.filter(or_(Post.kind.is_(None), Post.kind != FACT_KIND)).count(),
        'feiten': own.filter(Post.kind == FACT_KIND).count(),
        'keurmerken': (QualityAward.query.join(Post, QualityAward.post_id == Post.id)
                       .filter(Post.user_id == user.id, QualityAward.status == 'toegekend').count()),
        'meldingen_ontvangen': activity['received'],
        'gegrond': activity['upheld'],
        'meldingen_gedaan': activity['made'],
        'waarschuwingen': Warning.query.filter_by(user_id=user.id).count(),
        'schorsingen': Suspension.query.filter_by(user_id=user.id).count(),
    }


@dataclass
class HistoryItem:
    """One line in the enforcement history.
    kind: verplaatst / verborgen / waarschuwing / schorsing.
    description: the explanation, warning message or suspension reason.
    issue: the issue (vraagstuk) where it happened, or None.
    actor: the moderator, or None (the AI, or a deleted account).
    at: when it happened. until: the end of a suspension. source: the
    ConductCase, Warning or Suspension row, for links and details."""
    kind: str
    description: str
    issue: object
    actor: object
    at: datetime
    until: datetime = None
    source: object = None

    @property
    def by_ai(self):
        return self.kind in REMOVING_ACTIONS and getattr(self.source, 'source', None) == 'ai'


def _issue_of(post):
    """The issue a post belongs to (through its discussion thread), or None."""
    thread = post.thread if post is not None else None
    issues = getattr(thread, 'issues', None) or []
    return issues[0] if issues else None


def moderation_history(user):
    """The user's enforcement history, newest first: conduct cases that moved
    or hid one of their posts, their warnings and their suspensions. A case
    that was restored (teruggeplaatst) no longer counts and is left out."""
    items = []
    cases = (ConductCase.query.join(Post, ConductCase.post_id == Post.id)
             .filter(Post.user_id == user.id, ConductCase.action.in_(REMOVING_ACTIONS)).all())
    for case in cases:
        items.append(HistoryItem(kind=case.action, description=case.explanation or '',
                                 issue=_issue_of(case.post), actor=case.acted_by,
                                 at=case.acted_at or case.created_at, source=case))
    for w in Warning.query.filter_by(user_id=user.id).all():
        items.append(HistoryItem(kind='waarschuwing', description=w.message, issue=None,
                                 actor=w.by, at=w.created_at, source=w))
    for s in Suspension.query.filter_by(user_id=user.id).all():
        items.append(HistoryItem(kind='schorsing', description=s.reason, issue=None,
                                 actor=s.by, at=s.created_at, until=s.until, source=s))
    items.sort(key=lambda i: i.at or datetime.min, reverse=True)
    return items



@dataclass
class Contribution:
    """One of the user's posts or facts for the dossier list. status is the
    most telling of verplaatst / verborgen / gemeld / keurmerk / feit, or None."""
    post: object
    issue: object
    status: str = None


def recent_contributions(user, limit=20):
    """The user's posts and facts, newest first, with their issue and a status.
    Three queries: the posts, the awarded ones and the ones with open reports
    (conduct_case comes along per post through its backref)."""
    from app.models import PostReport
    posts = (Post.query.filter(Post.user_id == user.id)
             .order_by(Post.created_at.desc(), Post.id.desc()).limit(limit).all())
    ids = [p.id for p in posts]
    awarded, reported = set(), set()
    if ids:
        awarded = {pid for (pid,) in QualityAward.query.with_entities(QualityAward.post_id)
                   .filter(QualityAward.post_id.in_(ids), QualityAward.status == 'toegekend')}
        reported = {pid for (pid,) in PostReport.query.with_entities(PostReport.post_id)
                    .filter(PostReport.post_id.in_(ids), PostReport.decision.is_(None))}
    items = []
    for p in posts:
        case = p.conduct_case
        if case is not None and case.action in REMOVING_ACTIONS:
            status = case.action
        elif p.id in reported:
            status = 'gemeld'
        elif p.id in awarded:
            status = 'keurmerk'
        elif p.is_fact:
            status = 'feit'
        else:
            status = None
        items.append(Contribution(post=p, issue=_issue_of(p), status=status))
    return items


def initials(user):
    """Two letters for the avatar, from the username (jan_hofman -> JH)."""
    import re
    parts = [p for p in re.split(r'[_.\-\s]+', user.username or '') if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return (user.username or '?')[:2].upper()


def deletion_effects(user):
    """What deleting (anonymising) this account does, as (key, n) pairs for
    the confirmation screen: posts and facts that stay without a name, own
    warnings and suspensions that go, and those given as a moderator that stay
    without a name. Rows with n == 0 are left out, except the posts."""
    from app.models import Post
    own_posts = Post.query.filter(Post.user_id == user.id)
    rows = [
        ('posts', own_posts.filter(or_(Post.kind.is_(None), Post.kind != FACT_KIND)).count()),
        ('facts', own_posts.filter(Post.kind == FACT_KIND).count()),
        ('warnings', Warning.query.filter_by(user_id=user.id).count()),
        ('suspensions', Suspension.query.filter_by(user_id=user.id).count()),
        ('given', Warning.query.filter_by(by_id=user.id).count()
         + Suspension.query.filter_by(by_id=user.id).count()),
    ]
    return [(k, n) for k, n in rows if n or k == 'posts']


def anonymise(user, actor, reason):
    """Delete an account by anonymising it: the row stays, so every post, fact,
    proposal, vote and report keeps working, but nothing personal remains and
    login is impossible. Commit niet zelf."""
    import secrets
    from app import bcrypt, db
    from app.models import DELETED_HANDLE, Post, User
    handle = f'{DELETED_HANDLE}_{user.id}'
    if User.query.filter(db.func.lower(User.username) == handle, User.id != user.id).first():
        handle = f'{DELETED_HANDLE}_{user.id}_{secrets.token_hex(3)}'
    user.deleted_at = datetime.utcnow()
    user.deleted_by_id = actor.id
    user.deleted_reason = reason
    user.username = handle
    user.email = f'{handle}@deleted.invalid'
    # An unguessable hash: no password matches it (and login also checks is_deleted).
    user.password_hash = bcrypt.generate_password_hash(secrets.token_urlsafe(32)).decode('utf-8')
    user.verified = False
    user.first_name = user.last_name = user.linkedin_url = user.google_id = None
    user.show_real_name = False
    user.digest_email = False
    user.has_unread_digest = None
    user.suspended_until = None
    user.is_moderator = False
    user.role = None
    # The name snapshots on their posts (the fallback display) go too.
    Post.query.filter(Post.user_id == user.id).update(
        {Post.author_name: '', Post.author_username: DELETED_HANDLE,
         Post.author_badge_nl: None, Post.author_badge_en: None}, synchronize_session=False)
    # Own warnings and suspensions go; those given as a moderator stay without a name.
    for model in (Warning, Suspension):
        model.query.filter_by(user_id=user.id).delete(synchronize_session=False)
        model.query.filter_by(by_id=user.id).update({model.by_id: None}, synchronize_session=False)
    # No more notifications for a deleted account.
    from app import digest, issue_lifecycle, notifications
    digest.delete_for_user(user.id)
    notifications.delete_for_user(user.id)
    issue_lifecycle.delete_follows(user_id=user.id)
