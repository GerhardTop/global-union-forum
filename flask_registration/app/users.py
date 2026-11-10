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

