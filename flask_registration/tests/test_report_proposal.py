"""Meldknop bij voorstellen: een deelnemer meldt een voorstel met een
verplichte reden. Eén open melding per persoon, niet op een eigen voorstel."""
import uuid

import pytest
from flask import g
from flask_babel import refresh

from app import bcrypt
from app.models import IssueProposal, ProposalReport, User


@pytest.fixture(autouse=True)
def _reset_limiter():
    from sqlalchemy.exc import OperationalError
    from app import limiter
    try:
        limiter.reset()
    except (AssertionError, OperationalError):
        pass
    yield


def _user(db):
    name = f'u_{uuid.uuid4().hex[:8]}'
    u = User(username=name, email=f'{name}@example.com', verified=True,
             password_hash=bcrypt.generate_password_hash('Sterk1!ww').decode('utf-8'))
    u.set_role('contributor')
    u.accept_rules()
    db.session.add(u)
    db.session.commit()
    return u


def _issue_with_proposal(db, author):
    from app import issues
    issue = issues.create({'title': 'Een vraagstuk', 'intro': 'Toelichting.', 'kind': 'online'}, author)
    db.session.commit()
    prop = IssueProposal(issue_id=issue.id, statement='Een voorstel.', created_by_id=author.id)
    db.session.add(prop)
    db.session.commit()
    return issue, prop


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


def test_report_proposal_creates_one(app, db):
    author, reporter = _user(db), _user(db)
    issue, prop = _issue_with_proposal(db, author)
    r = _req(_client(app, reporter), 'POST', f'/vraagstuk/{issue.id}/voorstel/{prop.id}/melden',
             data={'reason': 'ongepast'})
    assert r.status_code == 302
    rows = ProposalReport.query.filter_by(proposal_id=prop.id).all()
    assert len(rows) == 1
    assert rows[0].reporter_id == reporter.id and rows[0].reason == 'ongepast' and rows[0].decision is None


def test_report_proposal_reason_required(app, db):
    author, reporter = _user(db), _user(db)
    issue, prop = _issue_with_proposal(db, author)
    r = _req(_client(app, reporter), 'POST', f'/vraagstuk/{issue.id}/voorstel/{prop.id}/melden',
             data={'reason': '   '})
    assert r.status_code == 302
    assert ProposalReport.query.filter_by(proposal_id=prop.id).count() == 0


def test_cannot_report_own_proposal(app, db):
    author = _user(db)
    issue, prop = _issue_with_proposal(db, author)
    r = _req(_client(app, author), 'POST', f'/vraagstuk/{issue.id}/voorstel/{prop.id}/melden',
             data={'reason': 'x'})
    assert r.status_code == 403
    assert ProposalReport.query.filter_by(proposal_id=prop.id).count() == 0


def test_one_open_proposal_report_per_person(app, db):
    author, reporter = _user(db), _user(db)
    issue, prop = _issue_with_proposal(db, author)
    for _ in range(2):
        _req(_client(app, reporter), 'POST', f'/vraagstuk/{issue.id}/voorstel/{prop.id}/melden',
             data={'reason': 'reden'})
    assert ProposalReport.query.filter_by(proposal_id=prop.id, reporter_id=reporter.id).count() == 1
