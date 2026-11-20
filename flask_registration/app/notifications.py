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
per language when rendering.

Step 3a-2/3a-3 add the tick-driven events (gevolgd, uitslag, voorstel), made
idempotent by event_key. Step 3b adds the per-event email: the in-app and
email switches are independent; a row exists whenever either is on (in_app
says whether it shows in the app), so event_key de-dups both channels."""
import json
from flask_babel import force_locale, gettext, lazy_gettext, lazy_pgettext, pgettext
from datetime import datetime, timedelta

from app import db, i18n

SUMMARY_FREQUENCIES = ('off', 'daily', 'weekly')
DEFAULT_SUMMARY_FREQUENCY = 'weekly'

# (key, section, nl, en, sub_nl, sub_en, senior_only), in display order.
# (key, section, label, sub, senior_only). The keys and sections are the
# stored codes; the labels are lazy (translated when shown).
CATEGORIES = (
    ('reactie', 'deelname', lazy_pgettext('notification setting', 'A reply to your contribution'), None, False),
    ('voorstel', 'deelname', lazy_gettext('Your proposal reaches the threshold or moves a phase on'), None, False),
    ('gevolgd', 'deelname', lazy_gettext('An issue you follow changes phase or voting is about to close'), None, False),
    ('uitslag', 'deelname', lazy_pgettext('notification setting', 'The outcome of an issue you follow'), None, False),
    ('feit', 'deelname', lazy_gettext('Your fact is challenged or resolved'), None, False),
    ('melding', 'moderatie', lazy_pgettext('notification setting', 'A new report to handle'), None, False),
    ('bezwaar', 'moderatie', lazy_pgettext('notification setting', 'A new objection to a decision'),
     lazy_gettext('Senior moderators'), True),
    ('betwist', 'moderatie', lazy_gettext('A challenged fact'), lazy_gettext('Senior moderators'), True),
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
    """The gates that hold for both channels: not a deleted account, not your
    own action, and a moderation category only for who may hold it now."""
    if user is None or getattr(user, 'is_deleted', False):
        return False
    if actor_id is not None and actor_id == user.id:
        return False
    if category in MODERATION_CATEGORIES:
        if user.is_suspended or category not in allowed_categories(user):
            return False
    return True


def _pending(match):
    from app.models import Notification
    return any(isinstance(o, Notification) and match(o) for o in db.session.new)


def notify(user, category, *, thread_id=None, issue_id=None, post_id=None, actor_id=None, dispute_id=None,
           event_key=None):
    """One event for one user, on the channels they switched on (step 3b):
    the in-app switch and the email switch are independent. Whenever either
    is on, a Notification row is added (in_app says whether it shows in the
    app); with the email switch on, a verified address gets the per-event
    email right away. Not for your own action, and a moderation category
    only for who may hold it. With an event_key (the tick) it is idempotent
    across both channels: nothing happens when this user already has a row
    with that key. Returns the row or None. Commit niet zelf: the caller's
    commit persists it."""
    from app.models import Notification
    if category not in CATEGORY_KEYS or not _may_receive(user, category, actor_id):
        return None
    pref = effective_prefs(user)[category]
    app_on, email_on = pref['app'], pref['email']
    if not app_on and not email_on:
        return None
    if event_key is not None:
        # The tick is single-process: check-then-insert is enough.
        with db.session.no_autoflush:
            seen = Notification.query.filter_by(user_id=user.id, event_key=event_key).first()
        if seen is not None or _pending(lambda o: o.user_id == user.id and o.event_key == event_key):
            return None
    if category == 'melding' and post_id is not None:
        # Repeated reports on the same post don't stack up while one is unread.
        with db.session.no_autoflush:
            open_row = Notification.query.filter_by(user_id=user.id, category='melding', post_id=post_id,
                                                    read_at=None).first()
        if open_row is not None or _pending(lambda o: o.user_id == user.id and o.category == 'melding'
                                            and o.post_id == post_id):
            return None
    row = Notification(user_id=user.id, category=category, thread_id=thread_id, issue_id=issue_id,
                       post_id=post_id, actor_id=actor_id, dispute_id=dispute_id, event_key=event_key,
                       in_app=app_on)
    db.session.add(row)
    if email_on and user.verified and user.email:
        send_notification_email(user, row)
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
    return (Notification.query.filter_by(user_id=user.id, read_at=None)
            .filter(Notification.in_app.isnot(False)).count())


def recent_notifications(user, limit=50):
    from app.models import Notification
    return (Notification.query.filter_by(user_id=user.id).filter(Notification.in_app.isnot(False))
            .order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit).all())


def mark_all_read(user, now=None):
    """Set read_at on this user's unread notifications. Commit niet zelf."""
    from app.models import Notification
    # Email-only rows too: after a visit, a further report on the same post may notify again.
    return (Notification.query.filter_by(user_id=user.id, read_at=None)
            .update({Notification.read_at: now or datetime.utcnow()}, synchronize_session=False))


_LINES = {
    'reactie': lazy_gettext('Someone replied to your contribution.'),
    'feit': lazy_gettext('Your fact is being challenged.'),
    'feit_resolved': lazy_gettext('Your challenged fact has been resolved.'),
    'melding': lazy_gettext('A new report to handle.'),
    'bezwaar': lazy_gettext('A new objection to a decision.'),
    'betwist': lazy_gettext('A fact is being challenged.'),
}


def notification_line(n, lang):
    """(text, url) for one Notification in the given language (a locale code
    such as 'nl' or 'en'). A 'feit' row that points at a dispute (dispute_id)
    is the resolved case."""
    with force_locale(i18n.normalize(lang)):
        return _notification_line(n, lang)


def _notification_line(n, lang):
    from flask import url_for
    from app.models import Post
    if n.category in SYSTEM_CATEGORIES:
        return _system_line(n, lang)
    if n.event_key:
        return _tick_line(n, lang)
    key = 'feit_resolved' if n.category == 'feit' and n.dispute_id is not None else n.category
    text = str(_LINES[key]) if key in _LINES else gettext('New notification.')
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
    # Step 3a-3: the final vote on the summarizing proposal (de eindstemming),
    # keyed on the same moments changes() detects them by.
    if types & {'ultiem_open', 'ultiem_uitslag'}:
        from app import issue_proposals
        final = issue_proposals.final_proposal(issue)
        if final is not None and 'ultiem_open' in types and final.created_at:
            emit('gevolgd', others, event_key=f'issue:{issue.id}:ultiem_open:{final.created_at.isoformat()}', **refs)
        if final is not None and 'ultiem_uitslag' in types:
            end = issue_proposals.final_voting_end(final)
            emit('uitslag', _users(followers | {initiator}),
                 event_key=f'issue:{issue.id}:ultiem_result:{end.isoformat()}', **refs)


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


_PHASES = {'feiten': lazy_pgettext('notification phase', 'Facts'),
           'bijstellen': lazy_pgettext('notification phase', 'Adjusting'),
           'stemmen': lazy_pgettext('notification phase', 'Voting'),
           'samenvatten': lazy_pgettext('notification phase', 'Summarising'),
           'vervolg': lazy_pgettext('notification phase', 'Follow-up')}


def _tick_line(n, lang):
    """(text, url) for a tick-driven notification; the event_key says which
    event it was (phase, closing, result, threshold, round, and since step
    3a-3 the final vote: ultiem_open, ultiem_result)."""
    from flask import url_for
    from app.models import Issue, IssueProposal, Thread
    parts = n.event_key.split(':')
    source = parts[0]
    url = url_for('profile.meldingen')
    title = None
    if n.issue_id is not None and (issue := db.session.get(Issue, n.issue_id)) is not None:
        title = issue.title
        url = url_for('issues.show', issue_id=issue.id)
    elif n.thread_id is not None and (thread := db.session.get(Thread, n.thread_id)) is not None:
        # Content model (phase 0 step 6): the stored title per language.
        title = thread.title_prefix_nl if lang == 'nl' else (thread.title_prefix_en or thread.title_prefix_nl)
        url = url_for('forum.forum_thread', thread_id=thread.id)
    if title:
        t = f'‘{title}’'
    elif source == 'issue':
        t = pgettext('notification subject', 'an issue')
    else:
        t = pgettext('notification subject', 'a proposal')
    if source == 'issueproposal':
        ip = db.session.get(IssueProposal, int(parts[1])) if parts[1].isdigit() else None
        if ip is not None:
            url = url_for('issues.proposal', issue_id=ip.issue_id, proposal_id=ip.id)
        return gettext('Your proposal on %(title)s has enough support and is on the voting list.', title=t), url
    kind = parts[2]
    if kind == 'ultiem_open':
        return gettext('The final vote on %(title)s has started.', title=t), url
    if kind == 'ultiem_result':
        return gettext('The final vote on %(title)s has concluded.', title=t), url
    if kind == 'closing':
        return gettext('Voting on %(title)s closes within 24 hours.', title=t), url
    if kind == 'result' or (kind == 'round' and parts[-1] == 'result'):
        return gettext('The outcome of %(title)s is in.', title=t), url
    if kind == 'round' or (kind == 'voorstel' and parts[3] == 'round'):
        if kind == 'voorstel':
            if source == 'proposal':
                return gettext('A new voting round on your proposal %(title)s has opened.', title=t), url
            return gettext('A new voting round on your issue %(title)s has opened.', title=t), url
        return gettext('A new voting round on %(title)s has opened.', title=t), url
    phase = parts[4] if kind == 'voorstel' else parts[3]
    label = str(_PHASES[phase]) if phase in _PHASES else phase
    if kind == 'voorstel':
        if source == 'proposal':
            return gettext('Your proposal %(title)s moves on to the next phase: %(phase)s.', title=t, phase=label), url
        return gettext('Your issue %(title)s moves on to the next phase: %(phase)s.', title=t, phase=label), url
    text = gettext('%(title)s has moved to a new phase: %(phase)s.', title=t, phase=label)
    return text[0].upper() + text[1:], url


# ── The per-event email (step 3b) ────────────────────────────────────────

_SUBJECTS = {
    'reactie': lazy_pgettext('email subject', 'A reply to your contribution'),
    'voorstel': lazy_gettext('News about your proposal'),
    'gevolgd': lazy_gettext('News about an issue you follow'),
    'uitslag': lazy_pgettext('email subject', 'The outcome of an issue you follow'),
    'feit': lazy_gettext('News about your fact'),
    'melding': lazy_pgettext('email subject', 'A new report to handle'),
    'bezwaar': lazy_pgettext('email subject', 'A new objection to a decision'),
    'betwist': lazy_gettext('A fact is being challenged'),
}


def email_for(user, n):
    """(subject, html) of the per-event email, in the user's stored language
    (users.lang, Dutch by default), like the summary."""
    from flask import render_template, url_for
    from flask_babel import force_locale
    from app import digest
    lang = digest.lang_for(user)
    with force_locale(lang):
        text, url = notification_line(n, lang)
        base = url_for('main.index', _external=True).rstrip('/')
        if url.startswith('/'):
            url = base + url
        system = n.category in SYSTEM_CATEGORIES
        subject = str((_SYSTEM_SUBJECTS if system else _SUBJECTS)[n.category])
        note = str(_SYSTEM_NOTES[n.category]) if system else None
        html = render_template('email/notification.html', lang=lang, text=text, url=url, note=note,
                               system=system, settings_url=url_for('profile.notificaties', _external=True))
    return subject, html


def send_notification_email(user, n):
    """Send it now; a failure is logged and never breaks the action that
    caused the event. Sets emailed_at when the mail went out."""
    from flask import current_app
    from app.mail import send_email
    try:
        db.session.flush()  # the row and what it points at have ids for the link
        subject, html = email_for(user, n)
        if send_email(user.email, subject, html):
            n.emailed_at = datetime.utcnow()
    except Exception as exc:  # noqa: BLE001
        current_app.logger.error('Meldingsmail aan gebruiker %s mislukt: %s', user.id, exc)


# ── System notifications (step 4) ────────────────────────────────────────
# A warning or a suspension always reaches the user, in the app and by email
# (to a verified address). Not part of the settings matrix (CATEGORY_KEYS)
# and not switchable: no preference gate, no actor skip, no event_key.

SYSTEM_CATEGORIES = ('waarschuwing', 'schorsing')
_SYSTEM_SUBJECTS = {
    'waarschuwing': lazy_gettext('You have received a warning'),
    'schorsing': lazy_gettext('You have been suspended'),
}
_SYSTEM_NOTES = {
    'waarschuwing': lazy_gettext('This warning is about the forum rules.'),
    'schorsing': lazy_gettext('Until then you can still read, but not contribute or vote.'),
}


def notify_system(user, category, *, warning=None, suspension=None):
    """Always: one in-app row (in_app True) and, for a verified address, the
    email. Call it after the warning or suspension is committed; it commits
    its own row first and then sends, so no mail goes out for an action that
    rolled back. Returns the row."""
    from app.models import Notification
    if category not in SYSTEM_CATEGORIES:
        raise ValueError(f'unknown system category: {category}')
    row = Notification(user_id=user.id, category=category, in_app=True,
                       warning_id=warning.id if warning is not None else None,
                       suspension_id=suspension.id if suspension is not None else None)
    db.session.add(row)
    db.session.commit()
    if user.verified and user.email:
        send_notification_email(user, row)
        db.session.commit()
    return row


def _local(moment):
    from app.issues import to_local
    return to_local(moment).strftime('%d-%m-%Y %H:%M') if moment else ''


def _system_line(n, lang):
    from flask import url_for
    from app.models import Suspension, Warning
    url = url_for('main.spelregels')
    if n.category == 'waarschuwing':
        w = db.session.get(Warning, n.warning_id) if n.warning_id else None
        if w is None or not w.message:
            return gettext('A moderator has sent you a warning.'), url
        return gettext('A moderator has sent you a warning: “%(message)s”', message=w.message), url
    s = db.session.get(Suspension, n.suspension_id) if n.suspension_id else None
    if s is None:
        return gettext('You have been suspended.'), url
    reason = ' ' + gettext('Reason: %(reason)s', reason=s.reason) if s.reason else ''
    return gettext('You are suspended until %(until)s.', until=_local(s.until)) + reason, url

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
