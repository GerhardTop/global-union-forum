"""Meldingen voor de moderator (fase 2): de meldknop-meldingen (PostReport bij
bijdragen en feiten, ProposalReport bij voorstellen) gebundeld per gemeld item.

Een item is een bijdrage, een feit of een voorstel. Meerdere mensen kunnen
hetzelfde item melden; de moderator behandelt het item in een keer. Een groep
bundelt de open meldingen van een item, met het aantal, de oudste wachttijd en
het vraagstuk waar het bij hoort."""
from datetime import datetime

from app import db
from app.models import (Issue, IssueProposal, Post, PostReport, ProposalReport)


class Melding:
    """Een gemeld item met zijn open meldingen, voor het overzicht en het
    beoordeelscherm. kind is 'post' (bijdrage of feit) of 'proposal'."""

    def __init__(self, kind, item, issue, reports):
        self.kind = kind
        self.item = item
        self.issue = issue
        self.reports = reports  # nieuwste eerst

    @property
    def id(self):
        return self.item.id

    @property
    def count(self):
        return len(self.reports)

    @property
    def newest_at(self):
        return self.reports[0].created_at if self.reports else None

    @property
    def oldest_at(self):
        return self.reports[-1].created_at if self.reports else None

    @property
    def is_fact(self):
        return self.kind == 'post' and self.item.is_fact

    @property
    def author_id(self):
        """The author's user id (None for an anonymous or removed author)."""
        return self.item.user_id if self.kind == 'post' else self.item.created_by_id

    @property
    def author_name(self):
        if self.kind == 'post':
            return self.item.public_author_name()
        author = self.item.created_by
        return author.public_name if author is not None else None

    @property
    def wait(self):
        """De wachttijd van de oudste open melding, als badge-dict."""
        return wait_bucket(self.oldest_at)

    @property
    def snippet(self):
        text = self.item.body if self.kind == 'post' else self.item.statement
        text = (text or '').strip().replace('\n', ' ')
        return text if len(text) <= 120 else text[:117] + '...'


UPHELD = ('verplaatst', 'verborgen')


def user_activity(user):
    """De meldcijfers bij een naam op het beoordeelscherm (fase 2c): hoeveel
    meldingen iemand deed (made), hoeveel er op zijn eigen bijdragen en
    voorstellen binnenkwamen (received) en hoeveel daarvan gegrond bleken
    (upheld: verplaatst of verborgen). None voor een anonieme of verwijderde
    gebruiker. Vijf eenvoudige telqueries."""
    if user is None or getattr(user, 'id', None) is None:
        return None
    made = (PostReport.query.filter(PostReport.reporter_id == user.id).count()
            + ProposalReport.query.filter(ProposalReport.reporter_id == user.id).count())
    on_posts = PostReport.query.join(Post, PostReport.post_id == Post.id).filter(Post.user_id == user.id)
    received_posts = on_posts.count()
    upheld = on_posts.filter(PostReport.decision.in_(UPHELD)).count()
    received_props = (ProposalReport.query.join(IssueProposal, ProposalReport.proposal_id == IssueProposal.id)
                      .filter(IssueProposal.created_by_id == user.id).count())
    return {'made': made, 'received': received_posts + received_props, 'upheld': upheld}


def wait_bucket(dt):
    """Een wachttijd als label plus ernst (hot vanaf 5 dagen, warm vanaf 1 dag)."""
    if dt is None:
        return None
    secs = (datetime.utcnow() - dt).total_seconds()
    days = int(secs // 86400)
    hours = int(secs // 3600)
    if days >= 1:
        label_nl = f"{days} dag" if days == 1 else f"{days} dagen"
        label_en = f"{days} day" if days == 1 else f"{days} days"
    else:
        h = max(hours, 1)
        label_nl = f"{h} uur"
        label_en = f"{h} hour" if h == 1 else f"{h} hours"
    sev = 'hot' if days >= 5 else ('warm' if days >= 1 else 'cool')
    return {'nl': label_nl, 'en': label_en, 'sev': sev}


def _group(reports, item_by_id, issue_of, kind):
    """Bundel een lijst meldingen (nieuwste eerst) per item-id tot Meldingen."""
    by_item = {}
    for r in reports:
        by_item.setdefault(_item_id(r, kind), []).append(r)
    out = []
    for item_id, rows in by_item.items():
        item = item_by_id.get(item_id)
        if item is None:
            continue
        out.append(Melding(kind, item, issue_of(item), rows))
    return out


def _item_id(report, kind):
    return report.post_id if kind == 'post' else report.proposal_id


def _issue_for_thread(thread_id, cache):
    if thread_id not in cache:
        cache[thread_id] = Issue.query.filter_by(thread_id=thread_id).first()
    return cache[thread_id]


def open_meldingen():
    """Alle open meldingen, gebundeld per item, nieuwste melding eerst."""
    post_reports = (PostReport.query.filter(PostReport.decision.is_(None))
                    .order_by(PostReport.created_at.desc()).all())
    prop_reports = (ProposalReport.query.filter(ProposalReport.decision.is_(None))
                    .order_by(ProposalReport.created_at.desc()).all())

    thread_cache = {}
    post_ids = {r.post_id for r in post_reports}
    posts = {p.id: p for p in Post.query.filter(Post.id.in_(post_ids))} if post_ids else {}
    groups = _group(post_reports, posts,
                    lambda p: _issue_for_thread(p.thread_id, thread_cache), 'post')

    prop_ids = {r.proposal_id for r in prop_reports}
    props = {p.id: p for p in IssueProposal.query.filter(IssueProposal.id.in_(prop_ids))} if prop_ids else {}
    issue_cache = {}

    def issue_of_prop(prop):
        if prop.issue_id not in issue_cache:
            issue_cache[prop.issue_id] = Issue.query.get(prop.issue_id)
        return issue_cache[prop.issue_id]

    groups += _group(prop_reports, props, issue_of_prop, 'proposal')
    groups.sort(key=lambda m: m.newest_at or datetime.min, reverse=True)
    return groups


def oldest_open_at():
    """Het tijdstip van de oudste open melding, of None."""
    oldest = db.session.query(db.func.min(PostReport.created_at)).filter(
        PostReport.decision.is_(None)).scalar()
    oldest_p = db.session.query(db.func.min(ProposalReport.created_at)).filter(
        ProposalReport.decision.is_(None)).scalar()
    candidates = [t for t in (oldest, oldest_p) if t is not None]
    return min(candidates) if candidates else None


def handled_counts(user):
    """(totaal behandelde meldingen, door deze moderator) over beide tabellen."""
    total = (PostReport.query.filter(PostReport.decision.isnot(None)).count()
             + ProposalReport.query.filter(ProposalReport.decision.isnot(None)).count())
    mine = (PostReport.query.filter(PostReport.decided_by_id == user.id).count()
            + ProposalReport.query.filter(ProposalReport.decided_by_id == user.id).count())
    return total, mine


def find(kind, item_id):
    """Een enkele Melding voor het beoordeelscherm, of None als er geen open
    melding (meer) is."""
    if kind == 'post':
        rows = (PostReport.query.filter(PostReport.post_id == item_id, PostReport.decision.is_(None))
                .order_by(PostReport.created_at.desc()).all())
        if not rows:
            return None
        post = Post.query.get(item_id)
        if post is None:
            return None
        return Melding('post', post, Issue.query.filter_by(thread_id=post.thread_id).first(), rows)
    rows = (ProposalReport.query.filter(ProposalReport.proposal_id == item_id, ProposalReport.decision.is_(None))
            .order_by(ProposalReport.created_at.desc()).all())
    if not rows:
        return None
    prop = IssueProposal.query.get(item_id)
    if prop is None:
        return None
    return Melding('proposal', prop, Issue.query.get(prop.issue_id), rows)


def resolve(melding, decision, note, actor):
    """Sluit alle open meldingen van dit item met een besluit. Commit niet zelf.
    decision: 'handhaven', 'verplaatst' of 'verborgen'."""
    now = datetime.utcnow()
    for r in melding.reports:
        r.decision = decision
        r.decision_note = (note or None)
        r.decided_by_id = actor.id
        r.decided_at = now


def open_counts_by_issue(meldingen=None):
    """{issue_id: number of open meldingen (items)} for the manage list and screen."""
    counts = {}
    for m in (open_meldingen() if meldingen is None else meldingen):
        if m.issue is not None:
            counts[m.issue.id] = counts.get(m.issue.id, 0) + 1
    return counts

