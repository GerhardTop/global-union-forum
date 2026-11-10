"""Notifications: the categories, the per-user preferences and the summary
frequency (docs/design/notificaties.html).

Step 1 built the settings page and saving. Step 2 wires the summary frequency
into the digest (app/digest.send_for): off sends nothing, daily at most once
per local day, weekly at most once per rolling 7 days. The old on/off email
toggle (User.digest_email) is no longer written; it only feeds the fallback in
summary_frequency() for rows that never chose a frequency. The per-category
switches are stored.

Step 3a-1 adds the in-app store (models.Notification) and the instant events,
produced at the moment a user acts: reactie (a reply to your contribution),
melding (a new report, for moderators), bezwaar (a new objection, for senior
moderators), feit (your fact challenged or resolved) and betwist (a challenged
fact, for senior moderators). In-app only: no email per event yet, and the
tick-driven follow/issue events (voorstel, gevolgd, uitslag) come later.
Rows hold references only; notification_line() builds the text and the link
per language when rendering."""
import json
from datetime import datetime, timedelta

from app import db

SUMMARY_FREQUENCIES = ('off', 'daily', 'weekly')
DEFAULT_SUMMARY_FREQUENCY = 'weekly'

# (key, section, nl, en, sub_nl, sub_en, senior_only), in display order.
CATEGORIES = (
    ('reactie', 'deelname', 'Reactie op je bijdrage', 'A reply to your contribution', None, None, False),
    ('voorstel', 'deelname', 'Je voorstel haalt de drempel of gaat een fase verder',
     'Your proposal reaches the threshold or moves a phase on', None, None, False),
    ('gevolgd', 'deelname', 'Een gevolgd vraagstuk wisselt van fase of de stemming sluit bijna',
     'An issue you follow changes phase or voting is about to close', None, None, False),
    ('uitslag', 'deelname', 'De uitslag van een vraagstuk dat je volgt', 'The outcome of an issue you follow',
     None, None, False),
    ('feit', 'deelname', 'Je feit wordt betwist of afgehandeld', 'Your fact is challenged or resolved',
     None, None, False),
    ('melding', 'moderatie', 'Een nieuwe melding om te behandelen', 'A new report to handle', None, None, False),
    ('bezwaar', 'moderatie', 'Een nieuw bezwaar tegen een beslissing', 'A new objection to a decision',
     'Senior moderators', 'Senior moderators', True),
    ('betwist', 'moderatie', 'Een betwist feit', 'A challenged fact',
     'Senior moderators', 'Senior moderators', True),
)
CATEGORY_KEYS = tuple(c[0] for c in CATEGORIES)

# Defaults per category: (app, email).
DEFAULTS = {
    'reactie': (True, False), 'voorstel': (True, True), 'gevolgd': (True, False), 'uitslag': (True, True),
    'feit': (True, False), 'melding': (True, False), 'bezwaar': (True, False), 'betwist': (True, False),
}


def _stored(user):
    try:
        data = json.loads(user.notif_prefs) if user.notif_prefs else {}
    except (TypeError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def effective_prefs(user):
    """{key: {'app': bool, 'email': bool}}: the stored values over the defaults."""
    stored = _stored(user)
    prefs = {}
    for key in CATEGORY_KEYS:
        app_on, email_on = DEFAULTS[key]
        row = stored.get(key) if isinstance(stored.get(key), dict) else {}
        prefs[key] = {'app': bool(row.get('app', app_on)), 'email': bool(row.get('email', email_on))}
    return prefs


def summary_frequency(user):
    """The chosen frequency; for a row that never chose one (NULL), derived
    from the old toggle without a backfill: digest_email False -> 'off',
    otherwise the new default 'weekly'."""
    value = getattr(user, 'summary_frequency', None)
    if value in SUMMARY_FREQUENCIES:
        return value
    if getattr(user, 'digest_email', None) is False:
        return 'off'
    return DEFAULT_SUMMARY_FREQUENCY


def allowed_categories(user):
    """The categories this user may see and set: the moderation ones only for
    a moderator, bezwaar and betwist only for a senior moderator (or admin)."""
    keys = []
    for key, section, *_rest, senior_only in CATEGORIES:
        if section == 'moderatie' and not user.can_moderate():
            continue
        if senior_only and not user.is_senior_moderator:
            continue
        keys.append(key)
    return keys


def save_from_form(user, form):
    """Store the switches (checkboxes named '<key>_app' / '<key>_email') for
    the categories the user may set, and the summary frequency. Categories the
    user is not entitled to keep their stored value. Commit niet zelf."""
    stored = _stored(user)
    for key in allowed_categories(user):
        stored[key] = {'app': form.get(f'{key}_app') == '1', 'email': form.get(f'{key}_email') == '1'}
    user.notif_prefs = json.dumps(stored, sort_keys=True)
    freq = form.get('summary_frequency')
    if freq in SUMMARY_FREQUENCIES:
        user.summary_frequency = freq


# ── In-app notifications (step 3a-1) ─────────────────────────────────────

MODERATION_CATEGORIES = frozenset(key for key, section, *_rest in CATEGORIES if section == 'moderatie')


def _may_receive(user, category, actor_id):
    if user is None or getattr(user, 'is_deleted', False):
        return False
    if actor_id is not None and actor_id == user.id:
        return False
    if category in MODERATION_CATEGORIES:
        if user.is_suspended or category not in allowed_categories(user):
            return False
    return effective_prefs(user)[category]['app']


def notify(user, category, *, thread_id=None, issue_id=None, post_id=None, actor_id=None, dispute_id=None,
           event_key=None):
    """Add one Notification for user to the session when they want it in the
    app, did not cause it themselves and (for a moderation category) may hold
    it. With an event_key (the tick, step 3a-2) it is idempotent: nothing
    happens when this user already has a row with that key. Returns the row
    or None. Commit niet zelf: the caller's commit persists it."""
    from app.models import Notification
    if category not in CATEGORY_KEYS or not _may_receive(user, category, actor_id):
        return None
    if event_key is not None:
        # The tick is single-process: check-then-insert is enough.
        with db.session.no_autoflush:
            seen = Notification.query.filter_by(user_id=user.id, event_key=event_key).first()
        pending = any(isinstance(o, Notification) and o.user_id == user.id and o.event_key == event_key
                      for o in db.session.new)
        if seen is not None or pending:
            return None
    if category == 'melding' and post_id is not None:
        # Repeated reports on the same post don't stack up while one is unread.
        with db.session.no_autoflush:
            open_row = Notification.query.filter_by(user_id=user.id, category='melding', post_id=post_id,
                                                    read_at=None).first()
        pending = any(isinstance(o, Notification) and o.user_id == user.id and o.category == 'melding'
                      and o.post_id == post_id for o in db.session.new)
        if open_row is not None or pending:
            return None
    row = Notification(user_id=user.id, category=category, thread_id=thread_id, issue_id=issue_id,
                       post_id=post_id, actor_id=actor_id, dispute_id=dispute_id, event_key=event_key)
    db.session.add(row)
    return row


def emit(category, users, **refs):
    """notify() for each user in an iterable, once per user id."""
    seen, rows = set(), []
    for user in users:
        if user is None or user.id in seen:
            continue
        seen.add(user.id)
        row = notify(user, category, **refs)
        if row is not None:
            rows.append(row)
    return rows


def moderators():
    """Users who may moderate now (can_moderate is False while suspended)."""
    from app.models import _MODERATING_ROLES, User
    candidates = User.query.filter(db.or_(User.is_admin.is_(True), User.is_moderator.is_(True),
                                          User.role.in_(_MODERATING_ROLES)),
                                   User.deleted_at.is_(None))
    return [u for u in candidates if u.can_moderate()]


def senior_moderators():
    return [u for u in moderators() if u.is_senior_moderator]


def unread_count(user):
    """One COUNT on this user's unread rows."""
    from app.models import Notification
    if not getattr(user, 'is_authenticated', False):
        return 0
    return Notification.query.filter_by(user_id=user.id, read_at=None).count()


def recent_notifications(user, limit=50):
    from app.models import Notification
    return (Notification.query.filter_by(user_id=user.id)
            .order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit).all())


def mark_all_read(user, now=None):
    """Set read_at on this user's unread notifications. Commit niet zelf."""
    from app.models import Notification
    return (Notification.query.filter_by(user_id=user.id, read_at=None)
            .update({Notification.read_at: now or datetime.utcnow()}, synchronize_session=False))


_LINES = {
    'reactie': ('Er is op je bijdrage gereageerd.', 'Someone replied to your contribution.'),
    'feit': ('Je feit wordt betwist.', 'Your fact is being challenged.'),
    'feit_resolved': ('Je betwiste feit is afgehandeld.', 'Your challenged fact has been resolved.'),
    'melding': ('Er is een nieuwe melding om te behandelen.', 'A new report to handle.'),
    'bezwaar': ('Er is een nieuw bezwaar tegen een beslissing.', 'A new objection to a decision.'),
    'betwist': ('Een feit wordt betwist.', 'A fact is being challenged.'),
}


def notification_line(n, lang):
    """(text, url) for one Notification in 'nl' or 'en'. A 'feit' row that
    points at a dispute (dispute_id) is the resolved case."""
    from flask import url_for
    from app.models import Post
    if n.event_key:
        return _tick_line(n, lang)
    key = 'feit_resolved' if n.category == 'feit' and n.dispute_id is not None else n.category
    nl, en = _LINES.get(key, ('Nieuwe melding.', 'New notification.'))
    text = nl if lang == 'nl' else en
    post = db.session.get(Post, n.post_id) if n.post_id is not None else None
    dashboard = url_for('admin.moderator_dashboard')
    if n.category == 'reactie' and post is not None:
        from app.routes.forum import post_url
        url = post_url(post)
    elif n.category == 'feit' and post is not None:
        from app import facts
        url = facts.fact_url(post)
    elif n.category == 'melding':
        from app import reports
        if post is not None and reports.find('post', post.id) is not None:
            url = url_for('admin.melding_beoordelen', kind='feit' if post.is_fact else 'bijdrage',
                          item_id=post.id)
        else:
            url = dashboard + '#meldingen'  # already handled: the queue
    elif n.category == 'bezwaar':
        url = dashboard + '#bezwaren'
    elif n.category == 'betwist':
        url = dashboard + '#betwist'
    elif n.thread_id is not None:
        url = url_for('forum.forum_thread', thread_id=n.thread_id)
    else:
        url = url_for('profile.meldingen')
    return text, url



# ── Tick-driven events (step 3a-2) ───────────────────────────────────────
# Called by digest.tick() after the phase transitions and independently of
# the summary, so an in-app notification also reaches a user whose summary is
# off. The detection is the digest's: issue_lifecycle.changes() for issues and
# digest._changes() for the old public proposals (still created through
# /forum/nieuw). Each notification carries a stable event_key, so a second
# tick (or an overlapping window) never repeats one.

TICK_LOOKBACK = timedelta(days=2)  # only bounds the work; event_key guarantees once
_ISSUE_PHASE_CHANGES = ('issue_bijstellen', 'stemmen_open', 'issue_heropend', 'issue_samenvatten', 'issue_vervolg')


def _users(ids):
    from app.models import User
    return [u for u in (db.session.get(User, uid) for uid in sorted(i for i in ids if i is not None))
            if u is not None]


def _emit_issue(issue, since, now):
    from app import issue_lifecycle
    types = {c['type'] for c in issue_lifecycle.changes(issue, since, now)}
    initiator = issue.created_by_id
    followers = issue_lifecycle.followers(issue)
    others = _users(followers - {initiator})
    refs = {'issue_id': issue.id, 'thread_id': issue.thread_id}
    started = issue.phase_started_at.isoformat() if issue.phase_started_at else None
    if started and types & set(_ISSUE_PHASE_CHANGES):
        emit('gevolgd', others, event_key=f'issue:{issue.id}:phase:{issue.phase}:{started}', **refs)
        emit('voorstel', _users({initiator}),
             event_key=f'issue:{issue.id}:voorstel:phase:{issue.phase}:{started}', **refs)
    if 'sluit_binnenkort' in types and issue.phase_deadline:
        emit('gevolgd', others, event_key=f'issue:{issue.id}:closing:{issue.phase_deadline.isoformat()}', **refs)
    if started and 'issue_uitslag' in types:
        emit('uitslag', _users(followers | {initiator}), event_key=f'issue:{issue.id}:result:{started}', **refs)


def _emit_thresholds(since):
    """'voorstel': an IssueProposal (a proposed statement) reaches its issue's
    support threshold and so makes the voting list. Only proposals with new
    support in the window are checked; the crossing moment is the created_at
    of the support that reached the threshold."""
    from app import issue_lifecycle
    from app.models import IssueProposal, IssueProposalSupport
    recent = {pid for (pid,) in db.session.query(IssueProposalSupport.proposal_id)
              .filter(IssueProposalSupport.created_at > since).distinct()}
    for ip in IssueProposal.query.filter(IssueProposal.id.in_(recent)).all() if recent else ():
        issue = ip.issue
        need = issue.support_threshold if issue is not None else 0
        if ip.is_final or ip.created_by_id is None or need <= 0 or issue_lifecycle.is_wrapper(issue):
            continue
        times = [t for (t,) in db.session.query(IssueProposalSupport.created_at)
                 .filter(IssueProposalSupport.proposal_id == ip.id)
                 .order_by(IssueProposalSupport.created_at, IssueProposalSupport.id)]
        if len(times) >= need and times[need - 1] is not None and times[need - 1] > since:
            emit('voorstel', _users({ip.created_by_id}), issue_id=issue.id, thread_id=issue.thread_id,
                 event_key=f'issueproposal:{ip.id}:threshold')


def _emit_proposal(proposal, since, now):
    """The old public proposals: the same mapping as for issues."""
    from app import digest
    from app.models import ProposalFollow
    host = proposal.host_id
    followers = {u for (u,) in db.session.query(ProposalFollow.user_id)
                 .filter(ProposalFollow.proposal_id == proposal.id, ProposalFollow.active.is_(True))}
    others = _users(followers - {host})
    refs = {'thread_id': proposal.thread_id}
    base = f'proposal:{proposal.id}'
    for change in digest._changes(proposal, since, now):
        kind = change['type']
        if kind in ('bijstellen', 'stemmen_open') and proposal.phase_changed_at:
            at = proposal.phase_changed_at.isoformat()
            emit('gevolgd', others, event_key=f'{base}:phase:{proposal.phase}:{at}', **refs)
            emit('voorstel', _users({host}), event_key=f'{base}:voorstel:phase:{proposal.phase}:{at}', **refs)
        elif kind == 'ronde_open':
            emit('gevolgd', others, event_key=f'{base}:round:{change["number"]}:open', **refs)
            emit('voorstel', _users({host}), event_key=f'{base}:voorstel:round:{change["number"]}', **refs)
        elif kind == 'sluit_binnenkort' and change.get('until'):
            emit('gevolgd', others, event_key=f'{base}:closing:{change["until"]}', **refs)
        elif kind == 'uitslag' and proposal.result_at:
            emit('uitslag', _users(followers | {host}),
                 event_key=f'{base}:result:{proposal.result_at.isoformat()}', **refs)
        elif kind == 'ronde_uitslag':
            emit('uitslag', _users(followers | {host}), event_key=f'{base}:round:{change["number"]}:result',
                 **refs)


def emit_tick(now=None):
    """The tick-driven notifications: gevolgd, uitslag and voorstel, for issues
    and for the old public proposals. Commits. Returns the number of rows."""
    from flask import current_app
    from app import digest, issue_lifecycle
    from app.models import Notification, Proposal
    now = now or datetime.utcnow()
    since = now - TICK_LOOKBACK
    db.session.commit()  # start clean: what advance_due changed is saved, so a rollback below keeps it
    jobs = [(_emit_issue, (i, since, now)) for i in issue_lifecycle.notifiable_issues()]
    jobs += [(_emit_proposal, (p, since, now)) for p in Proposal.query.filter(digest._public_filter()).all()]
    jobs.append((_emit_thresholds, (since,)))
    made = 0
    for fn, args in jobs:
        try:
            fn(*args)
            made += sum(1 for o in db.session.new if isinstance(o, Notification))
            db.session.commit()
        except Exception as exc:  # one broken issue or proposal does not stop the rest
            db.session.rollback()
            current_app.logger.error('Meldingen (%s, %s) mislukt: %s', fn.__name__, args[0], exc)
    return made


_PHASES = {'feiten': ('Feiten', 'Facts'), 'bijstellen': ('Bijstellen', 'Adjusting'),
           'stemmen': ('Stemmen', 'Voting'), 'samenvatten': ('Samenvatten', 'Summarising'),
           'vervolg': ('Vervolg', 'Follow-up')}


def _tick_line(n, lang):
    """(text, url) for a tick-driven notification; the event_key says which
    event it was (phase, closing, result, threshold, round)."""
    from flask import url_for
    from app.models import Issue, IssueProposal, Thread
    nl = lang == 'nl'
    parts = n.event_key.split(':')
    source = parts[0]
    url = url_for('profile.meldingen')
    title = None
    if n.issue_id is not None and (issue := db.session.get(Issue, n.issue_id)) is not None:
        title = issue.title
        url = url_for('issues.show', issue_id=issue.id)
    elif n.thread_id is not None and (thread := db.session.get(Thread, n.thread_id)) is not None:
        title = thread.title_prefix_nl if nl else (thread.title_prefix_en or thread.title_prefix_nl)
        url = url_for('forum.forum_thread', thread_id=thread.id)
    noun = ('je voorstel' if nl else 'your proposal') if source == 'proposal' else (
        'je vraagstuk' if nl else 'your issue')
    t = f'‘{title}’' if title else (('een vraagstuk' if nl else 'an issue') if source == 'issue'
                                     else ('een voorstel' if nl else 'a proposal'))
    if source == 'issueproposal':
        ip = db.session.get(IssueProposal, int(parts[1])) if parts[1].isdigit() else None
        if ip is not None:
            url = url_for('issues.proposal', issue_id=ip.issue_id, proposal_id=ip.id)
        return ((f'Je voorstel bij {t} heeft genoeg steun en staat op de stemlijst.' if nl
                 else f'Your proposal on {t} has enough support and is on the voting list.'), url)
    kind = parts[2]
    if kind == 'closing':
        return (f'De stemming over {t} sluit binnen 24 uur.' if nl else f'Voting on {t} closes within 24 hours.'), url
    if kind == 'result' or (kind == 'round' and parts[-1] == 'result'):
        return (f'De uitslag van {t} is bekend.' if nl else f'The outcome of {t} is in.'), url
    if kind == 'round' or (kind == 'voorstel' and parts[3] == 'round'):
        if kind == 'voorstel':
            return (f'Er is een nieuwe stemronde over {noun} {t}.' if nl
                    else f'A new voting round on {noun} {t} has opened.'), url
        return (f'Er is een nieuwe stemronde over {t}.' if nl else f'A new voting round on {t} has opened.'), url
    phase = parts[4] if kind == 'voorstel' else parts[3]
    label = _PHASES.get(phase, (phase, phase))[0 if nl else 1]
    if kind == 'voorstel':
        return (f'{noun[0].upper()}{noun[1:]} {t} gaat een fase verder: {label}.' if nl
                else f'{noun[0].upper()}{noun[1:]} {t} moves on to the next phase: {label}.'), url
    return (f'{t[0].upper()}{t[1:]} is in een nieuwe fase: {label}.' if nl
            else f'{t[0].upper()}{t[1:]} has moved to a new phase: {label}.'), url

# ── Opruimen ─────────────────────────────────────────────────────────────

def delete_for_posts(post_ids):
    """Before posts (and the disputes on them) go: the notifications that point
    at them go first. Called from quality.delete_for_posts."""
    from app.models import Notification
    post_ids = list(post_ids)
    if post_ids:
        Notification.query.filter(Notification.post_id.in_(post_ids)).delete(synchronize_session='evaluate')


def delete_for_issues(issue_ids):
    from app.models import Notification
    issue_ids = list(issue_ids)
    if issue_ids:
        Notification.query.filter(Notification.issue_id.in_(issue_ids)).delete(synchronize_session='evaluate')


def delete_for_user(user_id):
    """A deleted or anonymised account: its own notifications go, its name as
    the actor on someone else's is detached."""
    from app.models import Notification
    Notification.query.filter_by(user_id=user_id).delete(synchronize_session='evaluate')
    Notification.query.filter_by(actor_id=user_id).update({Notification.actor_id: None},
                                                          synchronize_session=False)
