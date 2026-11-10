"""Notifications step 3a-1: the in-app store, notify()/emit(), the instant
events (reactie, melding, bezwaar, feit, betwist), /meldingen and the header
count. In-app only: no email is sent for an event."""
import json

from app import conduct, db as _db, notifications
from app.models import FactDispute, Notification, Post
from tests.test_facts import _fact, _issue
from tests.test_forum_conduct import (_client, _fresh_rate_limits, _reply, _req, fake_ai,  # noqa: F401
                                      make_thread, make_user)


def _rows(user, category=None):
    _db.session.expire_all()
    q = Notification.query.filter_by(user_id=user.id)
    if category:
        q = q.filter_by(category=category)
    return q.all()


def _no_mail(monkeypatch):
    sent = []
    import app.mail
    monkeypatch.setattr(app.mail, 'send_email', lambda *a, **k: sent.append(a))
    return sent


def test_reply_notifies_the_parent_author_only(app, db, make_user, make_thread, fake_ai, monkeypatch):
    sent = _no_mail(monkeypatch)
    anna, bram = make_user(), make_user()
    t = make_thread(op_user=anna)
    top = _reply(app, anna, t, 'Een bijdrage van Anna')        # top-level: nobody hears
    assert _rows(anna) == [] and _rows(bram) == []
    reply = _reply(app, bram, t, 'Bram reageert', parent=top)
    rows = _rows(anna, 'reactie')
    assert len(rows) == 1
    assert (rows[0].post_id, rows[0].thread_id, rows[0].actor_id, rows[0].read_at) == (reply.id, t.id, bram.id, None)
    assert _rows(bram) == []
    text, url = notifications.notification_line(rows[0], 'nl')
    assert text == 'Er is op je bijdrage gereageerd.' and url.endswith(f'#post-{reply.id}')
    assert notifications.notification_line(rows[0], 'en')[0] == 'Someone replied to your contribution.'
    assert sent == []


def test_switch_off_means_no_row(app, db, make_user, make_thread, fake_ai):
    anna, bram = make_user(), make_user()
    anna.notif_prefs = json.dumps({'reactie': {'app': False, 'email': False}})
    db.session.commit()
    t = make_thread(op_user=anna)
    top = _reply(app, anna, t, 'Anna zonder meldingen')
    _reply(app, bram, t, 'Bram reageert toch', parent=top)
    assert _rows(anna) == []


def test_report_notifies_moderators_without_stacking(app, db, make_user, make_thread, fake_ai):
    author, reporter, other, mod = make_user(), make_user('moderator'), make_user(), make_user('moderator')
    t = make_thread(op_user=author)
    post = _reply(app, author, t, 'Iets om te melden')
    r = _req(_client(app, reporter), 'POST', f'/forum/bijdrage/{post.id}/melden', data={'reason': 'Ongepast'})
    assert r.status_code == 302
    assert len(_rows(mod, 'melding')) == 1
    assert _rows(reporter, 'melding') == []          # the reporter, though a moderator, does not hear it
    assert _rows(author) == []
    text, url = notifications.notification_line(_rows(mod, 'melding')[0], 'nl')
    assert text == 'Er is een nieuwe melding om te behandelen.'
    assert url.endswith(f'/moderator/melding/bijdrage/{post.id}')
    # A second report on the same post: no second unread row for the same moderator.
    _req(_client(app, other), 'POST', f'/forum/bijdrage/{post.id}/melden', data={'reason': 'Ook ongepast'})
    assert len(_rows(mod, 'melding')) == 1
    assert len(_rows(reporter, 'melding')) == 1      # but the first reporter, as a moderator, now hears it


def test_objection_notifies_seniors_only(app, db, make_user, make_thread):
    anna, mod, senior = make_user(), make_user('moderator'), make_user('senior_moderator')
    t = make_thread(op_user=anna)
    op = Post.query.filter_by(thread_id=t.id, is_op=True).one()
    case = conduct.move(op, 'onderwerp', 'Ander onderwerp.', 'nl')
    db.session.commit()
    r = _req(_client(app, anna), 'POST', f'/forum/handhaving/{op.id}/bezwaar', data={'text': 'Dit klopt niet.'})
    assert r.status_code == 302 and case.current_objections
    rows = _rows(senior, 'bezwaar')
    assert len(rows) == 1 and rows[0].post_id == op.id
    text, url = notifications.notification_line(rows[0], 'en')
    assert text == 'A new objection to a decision.' and url.endswith('/moderator#bezwaren')
    assert _rows(mod) == [] and _rows(anna) == []


def test_dispute_and_resolve(app, db, make_user):
    author, disputer, senior, mod = (make_user(), make_user('senior_moderator'), make_user('senior_moderator'),
                                     make_user('moderator'))
    issue = _issue(db, author)
    fact = _fact(db, issue, author, 'Een feit om te betwisten')
    r = _req(_client(app, disputer), 'POST', f'/forum/feit/{fact.id}/betwisten', data={'reason': 'klopt_niet'})
    assert r.status_code == 302
    challenged = _rows(author, 'feit')
    assert len(challenged) == 1 and challenged[0].dispute_id is None and challenged[0].actor_id == disputer.id
    assert notifications.notification_line(challenged[0], 'nl')[0] == 'Je feit wordt betwist.'
    assert len(_rows(senior, 'betwist')) == 1
    text, url = notifications.notification_line(_rows(senior, 'betwist')[0], 'nl')
    assert text == 'Een feit wordt betwist.' and url.endswith('/moderator#betwist')
    assert _rows(disputer) == []                     # a senior, but the disputer
    assert _rows(mod) == []                          # betwist is for senior moderators only

    row = FactDispute.query.filter_by(fact_post_id=fact.id).one()
    _req(_client(app, mod), 'POST', f'/forum/feit/betwisting/{row.id}/afhandelen')
    rows = sorted(_rows(author, 'feit'), key=lambda n: n.id)
    assert len(rows) == 2 and rows[1].dispute_id == row.id
    text, url = notifications.notification_line(rows[1], 'nl')
    assert text == 'Je betwiste feit is afgehandeld.' and f'#post-{fact.id}' in url
    assert notifications.notification_line(rows[1], 'en')[0] == 'Your challenged fact has been resolved.'


def test_notify_guards(app, db, make_user):
    user, mod, susp = make_user(), make_user('moderator'), make_user('senior_moderator')
    assert notifications.notify(user, 'reactie', actor_id=user.id) is None      # own action
    assert notifications.notify(user, 'melding') is None                         # not a moderator
    assert notifications.notify(mod, 'bezwaar') is None                          # not a senior
    from datetime import datetime, timedelta
    susp.suspended_until = datetime.utcnow() + timedelta(days=7)
    assert notifications.notify(susp, 'betwist') is None                         # suspended
    rows = notifications.emit('reactie', [user, user, mod])                      # de-duplicated
    assert len(rows) == 2
    db.session.rollback()


def test_meldingen_page_lists_and_marks_read(app, db, make_user, make_thread, fake_ai):
    anna, bram = make_user(), make_user()
    t = make_thread(op_user=anna)
    top = _reply(app, anna, t, 'Anna schrijft iets')
    _reply(app, bram, t, 'Eerste reactie', parent=top)
    _reply(app, bram, t, 'Tweede reactie', parent=top)
    assert notifications.unread_count(anna) == 2

    # The banner on another page carries the count.
    other = _req(_client(app, anna), 'GET', '/forum').get_data(as_text=True)
    assert 'Je hebt 2 nieuwe meldingen.' in other and 'data-unread-notifications="2"' in other
    en = _req(_client(app, anna, 'en'), 'GET', '/forum').get_data(as_text=True)
    assert 'You have 2 new notifications.' in en

    page = _req(_client(app, anna), 'GET', '/meldingen').get_data(as_text=True)
    assert page.count('Er is op je bijdrage gereageerd.') == 2
    assert page.count('gu-inbox__item--unread') == 2 and 'Nog geen meldingen' not in page
    _db.session.expire_all()
    assert notifications.unread_count(anna) == 0
    page = _req(_client(app, anna), 'GET', '/meldingen').get_data(as_text=True)
    assert 'gu-inbox__item--unread' not in page and page.count('gu-inbox__item') >= 2
    assert 'data-unread-notifications' not in _req(_client(app, anna), 'GET', '/forum').get_data(as_text=True)


def test_meldingen_empty_state(app, db, make_user):
    page = _req(_client(app, make_user()), 'GET', '/meldingen').get_data(as_text=True)
    assert 'Nog geen meldingen' in page and 'gu-inbox__list' not in page


def test_deleting_a_user_or_post_removes_their_notifications(app, db, make_user, make_thread, fake_ai):
    from app import quality
    anna, bram = make_user(), make_user()
    t = make_thread(op_user=anna)
    top = _reply(app, anna, t, 'Weg ermee')
    reply = _reply(app, bram, t, 'Reactie die weggaat', parent=top)
    assert len(_rows(anna)) == 1
    quality.detach_user(bram.id)
    db.session.commit()
    assert _rows(anna)[0].actor_id is None
    quality.delete_for_posts([reply.id])
    db.session.commit()
    assert _rows(anna) == []


# ── Step 3b: the per-event email, independent of the in-app switch ───────

def _capture_mail(monkeypatch):
    sent = []
    import app.mail as mail
    monkeypatch.setattr(mail, 'send_email', lambda to, subject, html: sent.append((to, subject, html)) or True)
    return sent


def _prefs(db, user, category, app_on, email_on, lang=None):
    user.notif_prefs = json.dumps({category: {'app': app_on, 'email': email_on}})
    if lang:
        user.lang = lang
    db.session.commit()


def _reply_to(app, make_user, make_thread, anna):
    bram = make_user()
    t = make_thread(op_user=anna)
    top = _reply(app, anna, t, f'Anna {anna.id}')
    return _reply(app, bram, t, f'Bram op {anna.id}', parent=top)


def test_default_reactie_is_in_app_without_email(app, db, make_user, make_thread, fake_ai, monkeypatch):
    sent = _no_mail(monkeypatch)
    anna = make_user()
    _reply_to(app, make_user, make_thread, anna)
    rows = _rows(anna, 'reactie')
    assert len(rows) == 1 and rows[0].in_app is True and rows[0].emailed_at is None
    assert sent == []


def test_email_only_sends_mail_and_stays_out_of_the_app(app, db, make_user, make_thread, fake_ai, monkeypatch):
    sent = _capture_mail(monkeypatch)
    anna = make_user()
    _prefs(db, anna, 'reactie', False, True, lang='en')
    reply = _reply_to(app, make_user, make_thread, anna)
    rows = _rows(anna)
    assert len(rows) == 1 and rows[0].in_app is False and rows[0].emailed_at is not None
    assert notifications.unread_count(anna) == 0 and notifications.recent_notifications(anna) == []
    page = _req(_client(app, anna), 'GET', '/meldingen').get_data(as_text=True)
    assert 'gu-inbox__item' not in page
    assert 'data-unread-notifications' not in _req(_client(app, anna), 'GET', '/forum').get_data(as_text=True)
    assert [(to, subject) for to, subject, _ in sent] == [(anna.email, 'A reply to your contribution')]
    html = sent[0][2]
    assert 'Someone replied to your contribution.' in html and f'#post-{reply.id}' in html
    assert 'http://localhost/' in html and '/profiel/meldingen' in html


def test_app_and_email_both_on(app, db, make_user, make_thread, fake_ai, monkeypatch):
    sent = _capture_mail(monkeypatch)
    anna = make_user()
    _prefs(db, anna, 'reactie', True, True)                  # Dutch by default
    _reply_to(app, make_user, make_thread, anna)
    rows = _rows(anna)
    assert len(rows) == 1 and rows[0].in_app is True and notifications.unread_count(anna) == 1
    assert [(to, subject) for to, subject, _ in sent] == [(anna.email, 'Er is op je bijdrage gereageerd')]


def test_both_off_means_nothing(app, db, make_user, make_thread, fake_ai, monkeypatch):
    sent = _no_mail(monkeypatch)
    anna = make_user()
    _prefs(db, anna, 'reactie', False, False)
    _reply_to(app, make_user, make_thread, anna)
    assert _rows(anna) == [] and sent == []


def test_no_email_for_an_unverified_address(app, db, make_user, make_thread, fake_ai, monkeypatch):
    sent = _no_mail(monkeypatch)
    anna = make_user()
    _prefs(db, anna, 'reactie', True, True)
    anna.verified = False
    db.session.commit()
    _reply_to(app, make_user, make_thread, anna)
    assert len(_rows(anna)) == 1 and sent == []
    # Without an address (a guard in notify(); the column is NOT NULL, so only in memory).
    user = make_user()
    _prefs(db, user, 'reactie', False, True)
    user.email = ''
    assert notifications.notify(user, 'reactie') is not None and sent == []
    db.session.rollback()


def test_moderation_email_goes_to_moderators_who_want_it(app, db, make_user, make_thread, fake_ai, monkeypatch):
    sent = _capture_mail(monkeypatch)
    author, reporter, mod = make_user(), make_user(), make_user('moderator')
    _prefs(db, mod, 'melding', True, True)
    t = make_thread(op_user=author)
    post = _reply(app, author, t, 'Iets om per mail te melden')
    _req(_client(app, reporter), 'POST', f'/forum/bijdrage/{post.id}/melden', data={'reason': 'Ongepast'})
    assert (mod.email, 'Een nieuwe melding om te behandelen') in [(to, subject) for to, subject, _ in sent]
    assert all(to != reporter.email for to, _, _ in sent)
