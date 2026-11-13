"""Verwijderen (fase 3): een admin verwijdert een heel vraagstuk of een los
voorstel, achter een bevestiging waarbij de titel/stelling wordt overgetypt.
Alles wat eronder hangt (discussie, voorstellen, feiten, meldingen) gaat mee."""
import uuid

import pytest
from flask import g
from flask_babel import refresh

from app import bcrypt, db
from app.models import (Issue, IssueProposal, Post, PostReport, ProposalReport,
                        Thread, User)


@pytest.fixture(autouse=True)
def _clean_session(db):
    """De test-DB is sessie-breed en onze route commit verwijderingen. Maak de
    identity-map leeg voor elke test, zodat verwijderde objecten uit een vorige
    test niet botsen met id's die SQLite opnieuw uitgeeft."""
    db.session.expunge_all()
    yield


def _user(db, role='contributor', admin=False):
    n = f'u_{uuid.uuid4().hex[:8]}'
    u = User(username=n, email=f'{n}@example.com', verified=True,
             password_hash=bcrypt.generate_password_hash('Sterk1!ww').decode('utf-8'))
    u.set_role(role)
    u.accept_rules()
    u.is_admin = admin
    db.session.add(u)
    db.session.commit()
    return u


def _issue_full(db, author):
    """Een vraagstuk met een gesprek, een feit met melding, en een voorstel met melding."""
    import importlib
    ti = importlib.import_module('tests.test_issues')
    from app import issues
    proposal_old = ti._old_proposal(db, author, 'Groener plein?')
    fact = ti._fact(db, proposal_old, author, 'Een feit.', 'voor', 'https://example.org/a')
    issues.backfill()
    db.session.refresh(proposal_old)
    issue = proposal_old.issue
    prop = IssueProposal(issue_id=issue.id, statement='Meer bomen.', created_by_id=author.id)
    db.session.add(prop)
    reporter = _user(db)
    db.session.add(PostReport(post_id=fact.id, reporter_id=reporter.id, reason='x'))
    db.session.commit()
    db.session.add(ProposalReport(proposal_id=prop.id, reporter_id=reporter.id, reason='y'))
    db.session.commit()
    return issue, fact, prop


def _client(app, user, lang='nl'):
    c = app.test_client()
    with c.session_transaction() as s:
        s['lang'] = lang
        s['_user_id'] = str(user.id)
        s['_fresh'] = True
    return c


def _req(c, method, path, **kw):
    g.pop('_login_user', None)
    refresh()
    return c.open(path, method=method, **kw)


def _cleanup(issue_id):
    """De test-DB is sessie-breed en rolt alleen niet-gecommitte wijzigingen
    terug; onze route commit. Ruim een achtergebleven vraagstuk op, zodat latere
    tests (zoals de query-telling op /forum) geen gecommitte draden zien."""
    from app import issues
    issue = Issue.query.get(issue_id)
    if issue is not None:
        issues.purge(issue)


def test_admin_deletes_issue(app, db):
    admin, author = _user(db, admin=True), _user(db)
    issue, fact, prop = _issue_full(db, author)
    issue_id, thread_id, fact_id, prop_id = issue.id, issue.thread_id, fact.id, prop.id
    c = _client(app, admin)
    assert _req(c, 'GET', f'/vraagstuk/{issue_id}/verwijderen').status_code == 200
    # Verkeerde titel: blijft staan.
    _req(c, 'POST', f'/vraagstuk/{issue_id}/verwijderen', data={'confirm_title': 'fout'})
    assert Issue.query.get(issue_id) is not None
    # Juiste titel: alles weg.
    r = _req(c, 'POST', f'/vraagstuk/{issue_id}/verwijderen', data={'confirm_title': 'Groener plein?'})
    assert r.status_code == 302
    assert Issue.query.get(issue_id) is None
    assert Thread.query.get(thread_id) is None
    assert Post.query.get(fact_id) is None
    assert IssueProposal.query.get(prop_id) is None
    assert PostReport.query.filter_by(post_id=fact_id).count() == 0
    assert ProposalReport.query.filter_by(proposal_id=prop_id).count() == 0


def test_admin_deletes_proposal_only(app, db):
    admin, author = _user(db, admin=True), _user(db)
    issue, fact, prop = _issue_full(db, author)
    issue_id, prop_id = issue.id, prop.id
    c = _client(app, admin)
    assert _req(c, 'GET', f'/vraagstuk/{issue_id}/voorstel/{prop_id}/verwijderen').status_code == 200
    r = _req(c, 'POST', f'/vraagstuk/{issue_id}/voorstel/{prop_id}/verwijderen',
             data={'confirm_title': 'Meer bomen.'})
    assert r.status_code == 302
    assert IssueProposal.query.get(prop_id) is None
    assert ProposalReport.query.filter_by(proposal_id=prop_id).count() == 0
    assert Issue.query.get(issue_id) is not None  # het vraagstuk blijft
    _cleanup(issue_id)


def test_delete_proposal_with_reply(app, db):
    """Een reactie op een voorstel is een bijdrage die ernaar verwijst
    (issue_proposal_id). Die moet meegaan, anders weigert Postgres de verwijdering."""
    admin, author, replier = _user(db, admin=True), _user(db), _user(db)
    issue, fact, prop = _issue_full(db, author)
    reply = Post(thread_id=issue.thread_id, user_id=replier.id, author_name='',
                 author_username=replier.username, body='Mee eens.', source_lang='nl',
                 issue_proposal_id=prop.id)
    db.session.add(reply)
    db.session.commit()
    reply_id, prop_id = reply.id, prop.id
    r = _req(_client(app, admin), 'POST', f'/vraagstuk/{issue.id}/voorstel/{prop_id}/verwijderen',
             data={'confirm_title': 'Meer bomen.'})
    assert r.status_code == 302
    assert IssueProposal.query.get(prop_id) is None
    assert Post.query.get(reply_id) is None
    _cleanup(issue.id)


def test_non_admin_cannot_delete(app, db):
    mod, author = _user(db, 'moderator'), _user(db)
    issue, fact, prop = _issue_full(db, author)
    for user in (mod, author):
        assert _req(_client(app, user), 'GET', f'/vraagstuk/{issue.id}/verwijderen').status_code == 403
        assert _req(_client(app, user), 'POST', f'/vraagstuk/{issue.id}/verwijderen',
                    data={'confirm_title': 'Groener plein?'}).status_code == 403
    assert Issue.query.get(issue.id) is not None
    _cleanup(issue.id)


def test_no_delete_link_on_the_public_page(app, db):
    """Delete rights step 3: deleting is on the manage screen; the public issue
    page only links there, for whoever may manage the issue."""
    admin, author = _user(db, admin=True), _user(db)
    issue, fact, prop = _issue_full(db, author)
    admin_page = _req(_client(app, admin), 'GET', f'/vraagstuk/{issue.id}').get_data(as_text=True)
    author_page = _req(_client(app, author), 'GET', f'/vraagstuk/{issue.id}').get_data(as_text=True)
    for page in (admin_page, author_page):
        assert f'/vraagstuk/{issue.id}/verwijderen' not in page
    assert f'/moderator/vraagstuk/{issue.id}/beheer' in admin_page
    assert f'/moderator/vraagstuk/{issue.id}/beheer' not in author_page
    _cleanup(issue.id)


def test_delete_screen_shows_counts_and_admin_pill(app, db):
    """Verwijderen.dc.html: the screen shows what will be removed, the Admin
    pill, the warning and the type-the-title form (server check unchanged)."""
    import re
    from app import issues
    from app.models import IssueFollow
    admin, author = _user(db, admin=True), _user(db)
    issue, fact, prop = _issue_full(db, author)
    issue_id = issue.id
    try:
        db.session.add(IssueFollow(issue_id=issue_id, user_id=author.id))
        db.session.commit()
        counts = dict(issues.purge_counts(issue))
        assert counts['proposals'] >= 1 and counts['facts'] >= 1 and counts['followers'] == 1

        c = _client(app, admin)
        page = re.sub(r'\s+', ' ', _req(c, 'GET', f'/vraagstuk/{issue_id}/verwijderen').get_data(as_text=True))
        assert 'Vraagstuk definitief verwijderen' in page and '>Admin</span>' in page
        assert 'Wat er verdwijnt' in page and 'Dit kan niet ongedaan worden gemaakt.' in page
        assert f'<b>{counts["proposals"]}</b> voorst' in page
        assert f'<b>{counts["facts"]}</b> feit' in page
        assert '<b>1</b> volger wordt ontkoppeld' in page
        assert 'name="confirm_title"' in page and 'data-expected="Groener plein?"' in page
        assert 'De knop wordt pas actief als de titel exact klopt.' in page

        ppage = re.sub(r'\s+', ' ', _req(c, 'GET', f'/vraagstuk/{issue_id}/voorstel/{prop.id}/verwijderen')
                       .get_data(as_text=True))
        assert 'Voorstel definitief verwijderen' in ppage and '<b>1</b> melding' in ppage
        assert 'data-count="facts"' not in ppage and 'data-count="followers"' not in ppage
    finally:
        _cleanup(issue_id)
