"""Notifications step 3a-2: the tick-driven events (gevolgd, uitslag,
voorstel), in-app only, de-duplicated by event_key so a second tick never
repeats a notification. Uses the lifecycle test fixtures (users, issues and
their cleanup).

Step 3b: 'voorstel' and 'uitslag' have email on by default, so those events
now also send the per-event email; the tests assert those mails (and that a
second tick sends none again). The tick runs inside a request in production
(POST /intern/tik), so every test here runs in a request context too."""
import json
import uuid
from datetime import datetime, timedelta

import pytest

from app import digest, issue_lifecycle as lifecycle, issue_proposals as ip, notifications, proposals
from app.models import IssueFollow, Notification, Post, Proposal, Thread
from tests.test_issue_lifecycle import _issue, mails, make_user  # noqa: F401


@pytest.fixture(autouse=True)
def _in_request(app):
    from flask import g
    with app.test_request_context():
        # The shared app context can keep a detached current_user from an
        # earlier test; the email templates' context processors would trip on it.
        g.pop('_login_user', None)
        yield


def _mails_to(mails, *users):
    """(recipient id, subject) of the mails to these users, in order."""
    emails = {u.email: u for u in users}
    return [(emails[to].id, subject) for to, subject, _html in mails if to in emails]


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
    # Step 3b: 'voorstel' has email on by default, 'gevolgd' not: one mail, to the
    # initiator, and the later ticks did not send it again.
    assert _mails_to(mails, host, fan, quiet) == [(host.id, 'Nieuws over je voorstel')]
    assert _rows(host)[0].emailed_at is not None


def test_result_reaches_all_followers_including_the_initiator(app, db, make_user, mails):
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
    # Step 3b: uitslag and voorstel email by default (once), gevolgd does not.
    assert sorted(_mails_to(mails, host, fan)) == sorted([
        (host.id, 'De uitslag van een vraagstuk dat je volgt'), (host.id, 'Nieuws over je voorstel'),
        (fan.id, 'De uitslag van een vraagstuk dat je volgt')])


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


def test_proposal_reaching_the_support_threshold(app, db, make_user, mails):
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
    assert _mails_to(mails, author, host) == [(author.id, 'Nieuws over je voorstel')]  # step 3b, once


def test_old_public_proposal_is_mirrored(app, db, make_user, mails):
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
    assert sorted(_mails_to(mails, host, fan)) == sorted([   # step 3b: voorstel and uitslag email by default
        (host.id, 'Nieuws over je voorstel'), (host.id, 'De uitslag van een vraagstuk dat je volgt'),
        (fan.id, 'De uitslag van een vraagstuk dat je volgt')])


# ── Step 3a-3: the final vote on the summarizing proposal ────────────────

def _final_vote_issue(db, make_user):
    host, fan, no_follow_news, no_results = make_user(), make_user(), make_user(), make_user()
    no_follow_news.notif_prefs = json.dumps({'gevolgd': {'app': False, 'email': False}})
    no_results.notif_prefs = json.dumps({'uitslag': {'app': False, 'email': False}})
    _summary_off(db, host, fan, no_follow_news, no_results)
    issue = _issue(db, host)
    _follow(db, issue, host, fan, no_follow_news, no_results)
    # The summarize phase began long ago (outside the lookback): only the final vote is news.
    lifecycle.set_phase(issue, 'samenvatten', datetime.utcnow() - timedelta(days=10))
    db.session.commit()
    return issue, host, fan, no_follow_news, no_results


def test_final_vote_opens_once_for_followers_not_the_initiator(app, db, make_user, mails):
    issue, host, fan, no_follow_news, no_results = _final_vote_issue(db, make_user)
    final = ip.create_final(issue, host, 'Samen verder.')
    db.session.commit()

    digest.tick()
    digest.tick()
    rows = _rows(fan, 'gevolgd')
    assert len(rows) == 1
    assert rows[0].event_key == f'issue:{issue.id}:ultiem_open:{final.created_at.isoformat()}'
    text, url = notifications.notification_line(rows[0], 'nl')
    assert text == f'De eindstemming over ‘{issue.title}’ is begonnen.' and url.endswith(f'/vraagstuk/{issue.id}')
    assert notifications.notification_line(rows[0], 'en')[0] == f'The final vote on ‘{issue.title}’ has started.'
    assert _rows(host, 'gevolgd') == []           # the initiator: no 'gevolgd' for it
    assert _rows(no_follow_news) == []            # 'gevolgd' switched off
    assert len(_rows(no_results, 'gevolgd')) == 1
    # 'gevolgd' has email off by default: no mail (step 3b) to any of them.
    assert _mails_to(mails, host, fan, no_follow_news, no_results) == []


def test_final_vote_result_once_for_all_followers(app, db, make_user, mails):
    issue, host, fan, no_follow_news, no_results = _final_vote_issue(db, make_user)
    final = ip.create_final(issue, host, 'Samen verder.')
    final.created_at = datetime.utcnow() - timedelta(days=issue.days_vote or 7, hours=1)  # the vote just ended
    db.session.commit()
    end = ip.final_voting_end(final)

    digest.tick()
    digest.tick(datetime.utcnow() + timedelta(hours=1))
    for user in (host, fan, no_follow_news):
        rows = _rows(user, 'uitslag')
        assert len(rows) == 1 and rows[0].event_key == f'issue:{issue.id}:ultiem_result:{end.isoformat()}'
    assert notifications.notification_line(_rows(fan, 'uitslag')[0], 'nl')[0] == \
        f'De eindstemming over ‘{issue.title}’ is afgerond.'
    assert notifications.notification_line(_rows(fan, 'uitslag')[0], 'en')[0] == \
        f'The final vote on ‘{issue.title}’ has concluded.'
    assert _rows(no_results, 'uitslag') == []     # 'uitslag' switched off
    # Step 3b: 'uitslag' emails by default, once per follower (not to no_results).
    results = [m for m in _mails_to(mails, host, fan, no_follow_news, no_results)
               if m[1] == 'De uitslag van een vraagstuk dat je volgt']
    assert sorted(results) == sorted((u.id, 'De uitslag van een vraagstuk dat je volgt')
                                     for u in (host, fan, no_follow_news))



# ── Step 3b: email only, de-duplicated across both channels ──────────────

def test_email_only_tick_category_mails_once_and_stays_out_of_the_app(app, db, make_user, mails):
    host, fan = make_user(), make_user()
    fan.notif_prefs = json.dumps({'gevolgd': {'app': False, 'email': True}})
    fan.lang = 'en'
    _summary_off(db, host, fan)
    issue = _issue(db, host, started_days_ago=3, days_facts=2, days_adjust=5, days_vote=7)
    _follow(db, issue, fan)
    digest.tick()
    digest.tick()
    digest.tick(datetime.utcnow() + timedelta(hours=1))
    rows = _rows(fan)
    assert len(rows) == 1 and rows[0].in_app is False and rows[0].emailed_at is not None
    assert notifications.unread_count(fan) == 0 and notifications.recent_notifications(fan) == []
    sent = [(to, subject, html) for to, subject, html in mails if to == fan.email]
    assert len(sent) == 1                                    # the later ticks do not email again
    assert sent[0][1] == 'News about an issue you follow'    # in the user's language
    assert 'has moved to a new phase: Adjusting.' in sent[0][2]
    assert f'http://localhost/vraagstuk/{issue.id}' in sent[0][2]
