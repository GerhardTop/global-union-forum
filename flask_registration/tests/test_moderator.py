"""Moderator-overzicht (fase 2): de meldingen-bak met filters en het
beoordeelscherm. Een melding beoordelen sluit alle open meldingen van dat item
en kan de bijdrage verplaatsen of verbergen."""
import uuid

from flask import g
from flask_babel import refresh

from app import bcrypt
from app.models import (ConductCase, IssueProposal, Post, PostReport,
                        ProposalReport, User)


def _user(db, role='contributor'):
    n = f'u_{uuid.uuid4().hex[:8]}'
    u = User(username=n, email=f'{n}@example.com', verified=True,
             password_hash=bcrypt.generate_password_hash('Sterk1!ww').decode('utf-8'))
    u.set_role(role)
    u.accept_rules()
    db.session.add(u)
    db.session.commit()
    return u


def _issue_with_fact(db, author):
    import importlib
    ti = importlib.import_module('tests.test_issues')
    from app import issues
    proposal = ti._old_proposal(db, author, 'Een vraagstuk')
    fact = ti._fact(db, proposal, author, 'Een feit.', 'voor', 'https://example.org/a')
    issues.backfill()
    db.session.commit()
    return proposal.issue, fact


def _proposal(db, author, issue):
    prop = IssueProposal(issue_id=issue.id, statement='Een voorstel.', created_by_id=author.id)
    db.session.add(prop)
    db.session.commit()
    return prop


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


def _report_fact(db, fact, reporter, reason='ongepast'):
    db.session.add(PostReport(post_id=fact.id, reporter_id=reporter.id, reason=reason))
    db.session.commit()


def test_dashboard_renders_each_stream(app, db):
    mod, author, reporter = _user(db, 'moderator'), _user(db), _user(db)
    issue, fact = _issue_with_fact(db, author)
    _report_fact(db, fact, reporter)
    c = _client(app, mod)
    for q in ('', '?stroom=meldingen', '?stroom=alle', '?stroom=bezwaren', '?stroom=ai', '?stroom=zonder'):
        r = _req(c, 'GET', '/moderator' + q)
        assert r.status_code == 200
    body = _req(c, 'GET', '/moderator').get_data(as_text=True)
    assert 'Meldingen' in body and 'ongepast' in body


def test_dashboard_blocks_non_moderator(app, db):
    user = _user(db)
    assert _req(_client(app, user), 'GET', '/moderator').status_code == 403


def test_beoordelen_shows_reasons(app, db):
    mod, author, r1, r2 = _user(db, 'moderator'), _user(db), _user(db), _user(db)
    issue, fact = _issue_with_fact(db, author)
    _report_fact(db, fact, r1, 'reden een')
    _report_fact(db, fact, r2, 'reden twee')
    body = _req(_client(app, mod), 'GET', f'/moderator/melding/bijdrage/{fact.id}').get_data(as_text=True)
    assert 'reden een' in body and 'reden twee' in body


def test_handhaven_closes_all_reports(app, db):
    mod, author, r1, r2 = _user(db, 'moderator'), _user(db), _user(db), _user(db)
    issue, fact = _issue_with_fact(db, author)
    _report_fact(db, fact, r1)
    _report_fact(db, fact, r2)
    r = _req(_client(app, mod), 'POST', f'/moderator/melding/bijdrage/{fact.id}',
             data={'action': 'handhaven'})
    assert r.status_code == 302
    assert PostReport.query.filter_by(post_id=fact.id, decision=None).count() == 0
    assert PostReport.query.filter_by(post_id=fact.id, decision='handhaven').count() == 2


def test_verbergen_hides_post_and_closes_report(app, db):
    mod, author, reporter = _user(db, 'moderator'), _user(db), _user(db)
    issue, fact = _issue_with_fact(db, author)
    _report_fact(db, fact, reporter)
    r = _req(_client(app, mod), 'POST', f'/moderator/melding/bijdrage/{fact.id}',
             data={'action': 'verbergen', 'note': 'spam'})
    assert r.status_code == 302
    assert PostReport.query.filter_by(post_id=fact.id, decision='verborgen').count() == 1
    assert ConductCase.query.filter_by(post_id=fact.id, action='verborgen').count() == 1


def test_verplaatsen_moves_post(app, db):
    mod, author, reporter = _user(db, 'moderator'), _user(db), _user(db)
    issue, fact = _issue_with_fact(db, author)
    _report_fact(db, fact, reporter)
    r = _req(_client(app, mod), 'POST', f'/moderator/melding/bijdrage/{fact.id}',
             data={'action': 'verplaatsen', 'rule': 'respect', 'note': 'niet netjes'})
    assert r.status_code == 302
    assert ConductCase.query.filter_by(post_id=fact.id, action='verplaatst').count() == 1
    assert PostReport.query.filter_by(post_id=fact.id, decision='verplaatst').count() == 1


def test_proposal_report_handled(app, db):
    mod, author, reporter = _user(db, 'moderator'), _user(db), _user(db)
    issue, fact = _issue_with_fact(db, author)
    prop = _proposal(db, author, issue)
    db.session.add(ProposalReport(proposal_id=prop.id, reporter_id=reporter.id, reason='ongepast'))
    db.session.commit()
    assert _req(_client(app, mod), 'GET', f'/moderator/melding/voorstel/{prop.id}').status_code == 200
    r = _req(_client(app, mod), 'POST', f'/moderator/melding/voorstel/{prop.id}',
             data={'action': 'handhaven'})
    assert r.status_code == 302
    assert ProposalReport.query.filter_by(proposal_id=prop.id, decision='handhaven').count() == 1


def test_cannot_review_own_item(app, db):
    mod = _user(db, 'moderator')
    issue, fact = _issue_with_fact(db, mod)
    other = _user(db)
    _report_fact(db, fact, other)
    r = _req(_client(app, mod), 'POST', f'/moderator/melding/bijdrage/{fact.id}',
             data={'action': 'verbergen', 'note': 'x'})
    assert r.status_code == 403
    assert PostReport.query.filter_by(post_id=fact.id, decision=None).count() == 1


def test_unknown_melding_404(app, db):
    mod = _user(db, 'moderator')
    assert _req(_client(app, mod), 'GET', '/moderator/melding/bijdrage/999999').status_code == 404


def test_unknown_kind_404(app, db):
    mod = _user(db, 'moderator')
    assert _req(_client(app, mod), 'GET', '/moderator/melding/iets/1').status_code == 404


def test_beoordelen_form_posts_to_a_working_url(app, db):
    """De knoppen bij een gemeld feit moeten werken: haal het formulier op, lees
    de action eruit en post daarheen (niet via /bijdrage/ eromheen)."""
    import re
    mod, author, reporter = _user(db, 'moderator'), _user(db), _user(db)
    issue, fact = _issue_with_fact(db, author)
    _report_fact(db, fact, reporter)
    c = _client(app, mod)
    html = _req(c, 'GET', f'/moderator/melding/bijdrage/{fact.id}').get_data(as_text=True)
    m = re.search(r'<form[^>]*action="([^"]+)"[^>]*class="gu-mod-decide"', html)
    assert m, 'beslisformulier niet gevonden'
    action = m.group(1)
    assert '/melding/feit/' not in action
    r = _req(c, 'POST', action, data={'action': 'handhaven'})
    assert r.status_code == 302
    assert PostReport.query.filter_by(post_id=fact.id, decision='handhaven').count() == 1
