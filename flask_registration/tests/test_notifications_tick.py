"""Notifications step 3a-2: the tick-driven events (gevolgd, uitslag,
voorstel), in-app only, de-duplicated by event_key so a second tick never
repeats a notification. Uses the lifecycle test fixtures (users, issues and
their cleanup)."""
import json
import uuid
from datetime import datetime, timedelta

from app import digest, issue_lifecycle as lifecycle, issue_proposals as ip, notifications, proposals
from app.models import IssueFollow, Notification, Post, Proposal, Thread
from tests.test_issue_lifecycle import _issue, mails, make_user  # noqa: F401


def _rows(user, category=None):
    from app import db
    db.session.expire_all()
    q = Notification.query.filter_by(user_id=user.id)
    if category:
        q = q.filter_by(category=category)
    return q.order_by(Notification.id).all()


def _follow(db, issue, *users):
    for u in users:
        db.session.add(IssueFollow(issue_id=issue.id, user_id=u.id))
    db.session.commit()


def _summary_off(db, *users):
    for u in users:
        u.summary_frequency = 'off'
    db.session.commit()


def test_phase_advance_once_and_initiator_gets_voorstel(app, db, make_user, mails):
    host, fan, quiet = make_user(), make_user(), make_user()
    quiet.notif_prefs = json.dumps({'gevolgd': {'app': False, 'email': False}})
    # Every summary off: the in-app notifications do not depend on the summary.
    _summary_off(db, host, fan, quiet)
    issue = _issue(db, host, started_days_ago=3, days_facts=2, days_adjust=5, days_vote=7)
    _follow(db, issue, host, fan, quiet)          # the initiator follows too

    digest.tick()
    db.session.refresh(issue)
    assert issue.phase == 'bijstellen'
    rows = _rows(fan, 'gevolgd')
    assert len(rows) == 1 and rows[0].issue_id == issue.id
    assert rows[0].event_key == f'issue:{issue.id}:phase:bijstellen:{issue.phase_started_at.isoformat()}'
    text, url = notifications.notification_line(rows[0], 'nl')
    assert text == f'‘{issue.title}’ is in een nieuwe fase: Bijstellen.' and url.endswith(f'/vraagstuk/{issue.id}')
    assert notifications.notification_line(rows[0], 'en')[0] == \
        f'‘{issue.title}’ has moved to a new phase: Adjusting.'
    # The initiator hears it as 'voorstel', not also as 'gevolgd'.
    assert _rows(host, 'gevolgd') == []
    own = _rows(host, 'voorstel')
    assert len(own) == 1
    assert notifications.notification_line(own[0], 'nl')[0] == \
        f'Je vraagstuk ‘{issue.title}’ gaat een fase verder: Bijstellen.'
    # The in-app switch for 'gevolgd' is off: nothing.
    assert _rows(quiet) == []

    # A second tick: no duplicates, and no email at all.
    digest.tick()
    digest.tick(datetime.utcnow() + timedelta(hours=1))
    assert len(_rows(fan)) == 1 and len(_rows(host)) == 1
    assert mails == []


def test_result_reaches_all_followers_including_the_initiator(app, db, make_user):
    host, fan = make_user(), make_user()
    issue = _issue(db, host)
    _follow(db, issue, fan)                        # the initiator does not even follow
    lifecycle.set_phase(issue, 'samenvatten', datetime.utcnow() - timedelta(minutes=1))
    db.session.commit()
    notifications.emit_tick()
    notifications.emit_tick()
    for user in (host, fan):
        rows = _rows(user, 'uitslag')
        assert len(rows) == 1
        assert notifications.notification_line(rows[0], 'nl')[0] == f'De uitslag van ‘{issue.title}’ is bekend.'
    # Samenvatten is also a phase change: 'gevolgd' for the follower, 'voorstel' for the initiator.
    assert len(_rows(fan, 'gevolgd')) == 1 and _rows(host, 'gevolgd') == []
    assert len(_rows(host, 'voorstel')) == 1


def test_closing_soon_fires_once_per_voting_window(app, db, make_user):
    host, fan = make_user(), make_user()
    issue = _issue(db, host)
    _follow(db, issue, host, fan)
    now = datetime.utcnow()
    issue.phase, issue.phase_started_at, issue.phase_deadline = 'stemmen', now - timedelta(days=5), now + timedelta(hours=5)
    db.session.commit()
    notifications.emit_tick(now)
    rows = _rows(fan, 'gevolgd')
    assert len(rows) == 1 and rows[0].event_key == f'issue:{issue.id}:closing:{issue.phase_deadline.isoformat()}'
    assert notifications.notification_line(rows[0], 'en')[0] == f'Voting on ‘{issue.title}’ closes within 24 hours.'
    assert _rows(host) == []                       # gevolgd never goes to the initiator
    notifications.emit_tick(now + timedelta(hours=1))
    assert len(_rows(fan, 'gevolgd')) == 1


def test_proposal_reaching_the_support_threshold(app, db, make_user):
    host, author, a, b = make_user(), make_user(), make_user(), make_user()
    issue = _issue(db, host, support_threshold=2)
    proposal = ip.create(issue, author, 'Meer bomen op het plein.')
    db.session.flush()
    ip.support(proposal, a)
    db.session.commit()
    notifications.emit_tick()
    assert _rows(author) == []                     # 1 of 2: not yet
    ip.support(proposal, b)
    db.session.commit()
    notifications.emit_tick()
    notifications.emit_tick()
    rows = _rows(author, 'voorstel')
    assert len(rows) == 1 and rows[0].event_key == f'issueproposal:{proposal.id}:threshold'
    text, url = notifications.notification_line(rows[0], 'nl')
    assert text == f'Je voorstel bij ‘{issue.title}’ heeft genoeg steun en staat op de stemlijst.'
    assert url.endswith(f'/vraagstuk/{issue.id}/voorstel/{proposal.id}')
    assert _rows(host, 'voorstel') == []


def test_old_public_proposal_is_mirrored(app, db, make_user):
    host, fan = make_user(), make_user()
    title = f'Stelling {uuid.uuid4().hex[:6]}'
    t = Thread(eyebrow_nl='Forum · voorstel', eyebrow_en='Forum · proposal', title_prefix_nl=title,
               title_prefix_en=title)
    db.session.add(t)
    db.session.flush()
    db.session.add(Post(thread_id=t.id, user_id=host.id, author_name='', author_username=host.username,
                        body='Toelichting.', is_op=True, source_lang='nl'))
    p = proposals.create(t, 'publiek', host, minutes={'feiten': 60, 'bijstellen': 60, 'stemmen': 60})
    db.session.flush()
    digest.follow(p, host)
    digest.follow(p, fan)
    now = datetime.utcnow()
    p.phase, p.phase_changed_at, p.stemmen_end = 'stemmen', now - timedelta(minutes=5), now + timedelta(days=2)
    db.session.commit()
    notifications.emit_tick(now)
    notifications.emit_tick(now)
    assert [n.category for n in _rows(fan)] == ['gevolgd']
    assert [n.category for n in _rows(host)] == ['voorstel']
    assert notifications.notification_line(_rows(host)[0], 'nl')[0] == \
        f'Je voorstel ‘{title}’ gaat een fase verder: Stemmen.'
    p = Proposal.query.filter_by(thread_id=t.id).one()
    p.phase, p.phase_changed_at, p.result, p.result_pct, p.result_at = 'vervolg', now, 'aangenomen', 70.0, now
    db.session.commit()
    notifications.emit_tick(now + timedelta(minutes=1))
    assert len(_rows(fan, 'uitslag')) == 1 and len(_rows(host, 'uitslag')) == 1
