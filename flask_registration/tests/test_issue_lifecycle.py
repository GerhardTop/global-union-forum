"""
Herontwerp, bouwstap 7: de levenscyclus van een vraagstuk
(app/issue_lifecycle.py, de tik in app/digest.py en de routes in
app/routes/issues.py).

Dekt: automatische overgangen op de deadline (ook meerdere in één tik), de
stilte-regel van de feitenfase en de instelling ervan, geen automatiek bij
een event, in de vervolgfase of bij een ingepakt oud voorstel (dat volgt het
voorstel); de tik via /intern/tik; met de hand een fase verder; de
vervolgtekst; opnieuw openstellen met bevestiging; de meldingen in de
dagelijkse samenvatting voor de betrokkenen.

Gedeelde conftest-app (CSRF uit).
"""
import uuid
from datetime import datetime, timedelta

import pytest
from flask import g
from flask_babel import refresh

from app import bcrypt, digest, issue_lifecycle as lifecycle, issue_proposals as ip, issues
from app.models import (AppSetting, Digest, Issue, IssueProposal, IssueProposalFact, IssueProposalSupport,
                        IssueProposalVote, Post, QualityAward, Thread, User)
from tests.test_forum_conduct import _fresh_rate_limits  # noqa: F401  (autouse)

SECRET = 'test-tick-secret'


@pytest.fixture
def make_user(db):
    made = []

    def _make(role='contributor', admin=False):
        name = f'l_{uuid.uuid4().hex[:8]}'
        u = User(username=name, email=f'{name}@example.com', verified=True, is_admin=admin,
                 password_hash=bcrypt.generate_password_hash('Sterk1!ww').decode('utf-8'))
        u.set_role(role)
        u.accept_rules()
        db.session.add(u)
        db.session.commit()
        made.append(u.id)
        return u

    yield _make
    from app import quality
    from app.routes.forum import _delete_thread
    issue_ids = [i for (i,) in db.session.query(Issue.id).filter(Issue.created_by_id.in_(made))]
    proposal_ids = [p for (p,) in db.session.query(IssueProposal.id).filter(IssueProposal.issue_id.in_(issue_ids))] \
        if issue_ids else []
    if proposal_ids:
        for model in (IssueProposalVote, IssueProposalFact, IssueProposalSupport):
            model.query.filter(model.proposal_id.in_(proposal_ids)).delete(synchronize_session=False)
        from app.models import IssueProposalVersion  # polijstronde P5
        IssueProposalVersion.query.filter(IssueProposalVersion.proposal_id.in_(proposal_ids)).delete(
            synchronize_session=False)
        Post.query.filter(Post.issue_proposal_id.in_(proposal_ids)).update(
            {Post.issue_proposal_id: None}, synchronize_session=False)
        IssueProposal.query.filter(IssueProposal.id.in_(proposal_ids)).delete(synchronize_session=False)
    db.session.commit()
    for tid in [t.id for t in Thread.query.join(Post).filter(Post.user_id.in_(made)).distinct()]:
        _delete_thread(db.session.get(Thread, tid))
    if issue_ids:
        Issue.query.filter(Issue.id.in_(issue_ids)).delete(synchronize_session=False)
    row = db.session.get(AppSetting, lifecycle.SILENCE_KEY)
    if row is not None:
        db.session.delete(row)
    db.session.commit()
    for uid in made:
        quality.detach_user(uid)
        db.session.delete(db.session.get(User, uid))
    db.session.commit()
    db.session.expunge_all()  # SQLite hergebruikt id's; geen achtergebleven objecten


@pytest.fixture
def mails(monkeypatch):
    sent = []
    import app.mail
    monkeypatch.setattr(app.mail, 'send_email', lambda to, subject, html: sent.append((to, subject, html)) or True)
    return sent


def _client(app, user=None, lang='nl'):
    c = app.test_client()
    with c.session_transaction() as s:
        s['lang'] = lang
        if user is not None:
            s['_user_id'] = str(user.id)
            s['_fresh'] = True
    return c


def _req(client, method, path, **kw):
    g.pop('_login_user', None)
    refresh()
    return client.open(path, method=method, **kw)


def _issue(db, user, kind='online', started_days_ago=0, **extra):
    """Een vraagstuk dat `started_days_ago` dagen geleden begon (feitenfase)."""
    values = {'title': f'Vraagstuk {uuid.uuid4().hex[:6]}', 'intro': 'Context.', 'kind': kind, **extra}
    if kind == 'event':
        values['event_date'] = datetime.utcnow() + timedelta(days=30)
    start = datetime.utcnow() - timedelta(days=started_days_ago)
    issue = issues.create(values, user, now=start)
    if kind == 'event':
        issue.phase_deadline = None
    db.session.commit()
    return issue


def _award(db, issue, user, days_ago):
    post = Post(thread_id=issue.thread_id, user_id=user.id, author_name='', author_username=user.username,
                body='Een goed onderbouwde reactie.', source_lang='nl')
    db.session.add(post)
    db.session.flush()
    when = datetime.utcnow() - timedelta(days=days_ago)
    db.session.add(QualityAward(post_id=post.id, criterion='K1', status='toegekend', source='ai',
                                created_at=when, decided_at=when))
    db.session.commit()


# ── Automatische overgangen ──────────────────────────────────────────────

def test_advances_on_the_deadline(db, make_user):
    user = make_user()
    issue = _issue(db, user, started_days_ago=3, days_facts=2, days_adjust=5, days_vote=7)
    _award(db, issue, user, days_ago=0)  # geen stilte: alleen de deadline telt
    old_deadline = issue.phase_deadline
    assert lifecycle.advance_due() >= 1
    db.session.refresh(issue)
    assert issue.phase == 'bijstellen'
    assert issue.phase_started_at == old_deadline  # de planning blijft kloppen
    assert issue.phase_deadline == old_deadline + timedelta(days=5)
    assert lifecycle.advance_due() == 0  # idempotent


def test_catches_up_several_phases_and_stops_at_follow_up(db, make_user):
    user = make_user()
    issue = _issue(db, user, started_days_ago=20, days_facts=2, days_adjust=2, days_vote=2)
    lifecycle.advance_due()
    db.session.refresh(issue)
    assert issue.phase == 'vervolg' and issue.phase_deadline is None
    # In de stemfase is de stemdeadline bijgewerkt (het label "stemmen sluit").
    assert issue.voting_deadline == (issue.phase_started_at).date()
    assert lifecycle.advance_due() == 0


def test_facts_phase_closes_after_silence(db, make_user):
    user = make_user()
    quiet = _issue(db, user, started_days_ago=6)            # 21 dagen gepland, 6 dagen stil
    lively = _issue(db, user, started_days_ago=6)
    _award(db, lively, user, days_ago=2)                    # keurmerk 2 dagen geleden
    lifecycle.advance_due()
    db.session.refresh(quiet)
    db.session.refresh(lively)
    assert quiet.phase == 'bijstellen'
    assert quiet.phase_started_at == quiet.created_at + timedelta(days=5)
    assert lively.phase == 'feiten'
    assert lifecycle.silence_end(lively) > datetime.utcnow()


def test_silence_threshold_is_configurable(app, db, make_user):
    admin, user = make_user(admin=True), make_user()
    issue = _issue(db, user, started_days_ago=6)
    r = _req(_client(app, admin), 'POST', '/admin/vraagstukken/stilte', data={'silence_days': '10'})
    assert r.status_code == 302 and lifecycle.get_silence_days() == 10
    assert _req(_client(app, user), 'POST', '/admin/vraagstukken/stilte', data={'silence_days': '2'}).status_code == 403
    _req(_client(app, admin), 'POST', '/admin/vraagstukken/stilte', data={'silence_days': '0'})
    assert lifecycle.get_silence_days() == 10
    page = _req(_client(app, admin), 'GET', '/admin/voorstellen').get_data(as_text=True)
    assert 'Vraagstukken: stilte in de feitenfase' in page and 'value="10"' in page
    lifecycle.advance_due()
    db.session.refresh(issue)
    assert issue.phase == 'feiten'  # 6 dagen stil, drempel 10


def test_no_automatic_moves_for_events_follow_up_or_closed(db, make_user):
    user = make_user()
    event = _issue(db, user, kind='event', started_days_ago=40)
    closed = _issue(db, user, started_days_ago=40)
    closed.is_closed = True
    done = _issue(db, user, started_days_ago=40)
    lifecycle.set_phase(done, 'vervolg', datetime.utcnow() - timedelta(days=30))
    db.session.commit()
    lifecycle.advance_due()
    for issue, phase in ((event, 'feiten'), (closed, 'feiten'), (done, 'vervolg')):
        db.session.refresh(issue)
        assert issue.phase == phase


def test_wrapped_old_proposal_follows_its_proposal(db, make_user):
    from app import proposals
    from tests.test_issues import _old_proposal
    user = make_user()
    proposal = _old_proposal(db, user, 'Oud voorstel')
    issues.backfill()
    db.session.refresh(proposal)
    issue = proposal.issue
    issue.phase_deadline = datetime.utcnow() - timedelta(days=1)  # zou anders doorschuiven
    db.session.commit()
    lifecycle.advance_due()
    db.session.refresh(issue)
    assert issue.phase == 'feiten'  # geen eigen automatiek
    proposals.step(proposal, 'volgende') if proposal.kind == 'event' else proposals._set_phase(
        proposal, 'bijstellen', datetime.utcnow())
    db.session.commit()
    lifecycle.advance_due()
    db.session.refresh(issue)
    assert issue.phase == 'bijstellen'  # volgt het oude voorstel


def test_tick_endpoint_moves_issues(app, db, make_user, monkeypatch, mails):
    monkeypatch.setenv('TICK_SECRET', SECRET)
    user = make_user()
    issue = _issue(db, user, started_days_ago=30)
    r = app.test_client().post('/intern/tik', headers={'Authorization': f'Bearer {SECRET}'})
    assert r.status_code == 200 and r.get_json()['issues_moved'] >= 1
    db.session.refresh(issue)
    assert issue.phase != 'feiten'
    assert app.test_client().post('/intern/tik').status_code == 403  # beveiliging ongewijzigd


# ── Met de hand ──────────────────────────────────────────────────────────

def test_manual_next_phase(app, db, make_user):
    host, mod, other = make_user(), make_user('moderator'), make_user()
    event = _issue(db, host, kind='event')
    online = _issue(db, host)
    url = lambda i: f'/vraagstuk/{i.id}/volgende-fase'  # noqa: E731

    assert _req(_client(app, other), 'POST', url(event)).status_code == 403
    page = _req(_client(app, host), 'GET', f'/vraagstuk/{event.id}').get_data(as_text=True)
    assert 'Fasen beheren' in page and 'Bij een event zet je de fasen zelf door.' in page
    assert 'Fasen beheren' not in _req(_client(app, other), 'GET', f'/vraagstuk/{event.id}').get_data(as_text=True)

    assert _req(_client(app, host), 'POST', url(event)).status_code == 302
    db.session.refresh(event)
    assert event.phase == 'bijstellen' and event.phase_deadline is None  # een event heeft geen datums

    assert _req(_client(app, mod), 'POST', url(online)).status_code == 302
    db.session.refresh(online)
    assert online.phase == 'bijstellen'
    assert online.phase_deadline - online.phase_started_at == timedelta(days=online.days_adjust)

    for _ in range(2):
        _req(_client(app, host), 'POST', url(event))
    db.session.refresh(event)
    assert event.phase == 'vervolg'
    assert _req(_client(app, host), 'POST', url(event)).status_code == 403  # uit het vervolg niet verder


def test_followup_text(app, db, make_user):
    host, other = make_user(), make_user()
    issue = _issue(db, host)
    url = f'/vraagstuk/{issue.id}/vervolg'
    assert _req(_client(app, host), 'POST', url, data={'followup_text': 'Te vroeg'}).status_code == 403
    lifecycle.set_phase(issue, 'vervolg', datetime.utcnow())
    db.session.commit()
    assert _req(_client(app, other), 'POST', url, data={'followup_text': 'Niet van mij'}).status_code == 403
    assert _req(_client(app, host), 'POST', url,
                data={'followup_text': 'De gemeenteraad bespreekt de uitkomst in maart.'}).status_code == 302
    db.session.refresh(issue)
    assert issue.followup_text == 'De gemeenteraad bespreekt de uitkomst in maart.' and issue.followup_at
    page = _req(_client(app), 'GET', f'/vraagstuk/{issue.id}').get_data(as_text=True)
    assert 'Wat er nu gebeurt' in page and 'De gemeenteraad bespreekt de uitkomst in maart.' in page


def test_reopen_with_confirmation(app, db, make_user):
    host, mod, other = make_user(), make_user('moderator'), make_user()
    issue = _issue(db, host)
    url = f'/vraagstuk/{issue.id}/heropenen'
    assert _req(_client(app, host), 'POST', url, data={'confirm': '1'}).status_code == 403  # nog niet afgerond
    lifecycle.set_phase(issue, 'vervolg', datetime.utcnow() - timedelta(days=1))
    issue.is_closed = True
    issue.thread.is_closed = True
    db.session.commit()

    assert _req(_client(app, other), 'POST', url, data={'confirm': '1'}).status_code == 403
    c = _client(app, host)
    r = _req(c, 'POST', url, data={})
    assert r.status_code == 303
    db.session.refresh(issue)
    assert issue.phase == 'vervolg' and issue.is_closed
    page = _req(c, 'GET', f'/vraagstuk/{issue.id}').get_data(as_text=True)
    assert 'Vink het vakje aan' in page and 'Ja, stel dit vraagstuk opnieuw open voor discussie' in page

    assert _req(_client(app, mod), 'POST', url, data={'confirm': '1'}).status_code == 302
    db.session.refresh(issue)
    assert issue.phase == 'feiten' and not issue.is_closed and not issue.thread.is_closed
    assert issue.phase_deadline - issue.phase_started_at == timedelta(days=issue.days_facts)


# ── Meldingen in de dagelijkse samenvatting ──────────────────────────────

def test_participants_get_phase_changes_and_results(app, db, make_user, mails):
    host, voter, outsider = make_user(), make_user('voter'), make_user()
    issue = _issue(db, host, started_days_ago=2, support_threshold=1)
    issue.created_at = datetime.utcnow() - timedelta(days=3)
    proposal = ip.create(issue, host, 'Meer bomen op het plein.')
    db.session.flush()
    ip.support(proposal, voter)
    db.session.commit()
    assert lifecycle.participants(issue) >= {host.id, voter.id} and outsider.id not in lifecycle.participants(issue)

    # Stemfase: de stemming is geopend.
    lifecycle.set_phase(issue, 'stemmen', datetime.utcnow() - timedelta(minutes=5))
    db.session.commit()
    items = digest.build(voter)
    assert [c['type'] for c in items[0]['changes']][:1] == ['stemmen_open'] and items[0]['issue_id'] == issue.id
    assert digest.build(outsider) == []

    # Uitslag: na het stemmen de resultaten per voorstel op de stemlijst.
    ip.cast_vote(proposal, voter, 'voor')
    lifecycle.set_phase(issue, 'vervolg', datetime.utcnow() - timedelta(minutes=1))
    db.session.commit()
    change = next(c for c in digest.build(voter)[0]['changes'] if c['type'] == 'issue_uitslag')
    assert change['results'] == [{'statement': 'Meer bomen op het plein.', 'adopted': True, 'pct': 100.0}]

    # De samenvatting gaat ook echt uit, met een link naar de cockpit.
    with app.test_request_context():  # de tik draait in een verzoek (zoals in test_forum_digest)
        assert digest.run_daily() >= 1
    row = Digest.query.filter_by(user_id=voter.id).first()
    assert row is not None and any(i.get('issue_id') == issue.id for i in row.items)
    html = next(h for to, _s, h in mails if to == voter.email)
    assert f'/vraagstuk/{issue.id}' in html and 'Meer bomen op het plein.' in html
    page = _req(_client(app, voter), 'GET', '/meldingen').get_data(as_text=True)
    assert 'Het stemmen is gesloten. De uitslagen:' in page


def test_followup_and_reopen_are_announced(db, make_user):
    host, other = make_user(), make_user()
    issue = _issue(db, host, started_days_ago=10)
    issue.created_at = datetime.utcnow() - timedelta(days=10)
    db.session.add(Post(thread_id=issue.thread_id, user_id=other.id, author_name='', author_username=other.username,
                        body='Een reactie.', source_lang='nl'))
    lifecycle.set_phase(issue, 'vervolg', datetime.utcnow() - timedelta(days=2))
    db.session.commit()
    since = datetime.utcnow() - timedelta(hours=1)
    lifecycle.set_followup(issue, 'Wat er gebeurt.')
    db.session.commit()
    assert {'type': 'issue_vervolgtekst'} in lifecycle.changes(issue, since)
    lifecycle.reopen(issue)
    db.session.commit()
    assert {'type': 'issue_heropend'} in lifecycle.changes(issue, since)
