"""
Herontwerp, bouwstap 5: feiten koppelen aan een voorstel (IssueProposalFact,
app/issue_proposals.py en de routes in app/routes/issues.py).

Dekt: koppelen vanuit het detail (ook tegenfeiten, één keer, alleen feiten
van hetzelfde vraagstuk), ontkoppelen (wie koppelde of een moderator), de
fasen (feiten en bijstellen wel, stemmen, vervolg en gesloten niet), een
nieuw feit toevoegen en meteen koppelen (alleen in de feitenfase), koppelen
bij het aanmaken van een voorstel, de weergave met richtinglabels, en
opruimen bij het verwijderen van een feit of een gebruiker.

Gedeelde conftest-app (CSRF uit).
"""
import uuid

import pytest
from flask import g
from flask_babel import refresh

from app import bcrypt, issue_proposals as ip, issues
from app.models import (FACT_KIND, Issue, IssueProposal, IssueProposalFact, IssueProposalSupport, Post, Thread,
                        User)
from tests.test_forum_conduct import _fresh_rate_limits  # noqa: F401  (autouse)


@pytest.fixture
def make_user(db):
    made = []

    def _make(role='contributor'):
        name = f'k_{uuid.uuid4().hex[:8]}'
        u = User(username=name, email=f'{name}@example.com', verified=True,
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
        IssueProposalFact.query.filter(IssueProposalFact.proposal_id.in_(proposal_ids)).delete(synchronize_session=False)
        IssueProposalSupport.query.filter(IssueProposalSupport.proposal_id.in_(proposal_ids)).delete(
            synchronize_session=False)
        IssueProposal.query.filter(IssueProposal.id.in_(proposal_ids)).delete(synchronize_session=False)
    db.session.commit()
    for tid in [t.id for t in Thread.query.join(Post).filter(Post.user_id.in_(made)).distinct()]:
        _delete_thread(db.session.get(Thread, tid))
    if issue_ids:
        Issue.query.filter(Issue.id.in_(issue_ids)).delete(synchronize_session=False)
    db.session.commit()
    for uid in made:
        quality.detach_user(uid)
        db.session.delete(db.session.get(User, uid))
    db.session.commit()
    db.session.expunge_all()  # SQLite hergebruikt id's; geen achtergebleven objecten


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


def _setup(db, author, phase='feiten'):
    issue = issues.create({'title': 'Wat doen we met het plein?', 'intro': 'Context.', 'kind': 'online'}, author)
    db.session.flush()
    proposal = ip.create(issue, author, 'Maak het plein autovrij.')
    issue.phase = phase
    db.session.commit()
    return issue, proposal


def _fact(db, issue, user, body, direction='voor', source='https://example.org'):
    f = Post(thread_id=issue.thread_id, user_id=user.id, author_name='', author_username=user.username,
             body=body, source_lang='nl', kind=FACT_KIND, fact_direction=direction, fact_source=source)
    db.session.add(f)
    db.session.commit()
    return f


def _base(issue, proposal):
    return f'/vraagstuk/{issue.id}/voorstel/{proposal.id}'


def _link(app, user, issue, proposal, *fact_ids):
    return _req(_client(app, user), 'POST', _base(issue, proposal) + '/koppelen',
                data={'fact_ids': [str(i) for i in fact_ids]})


# ── Koppelen en ontkoppelen ──────────────────────────────────────────────

def test_link_facts_including_against(app, db, make_user):
    author, other = make_user(), make_user()
    issue, proposal = _setup(db, author)
    pro = _fact(db, issue, author, 'Minder fijnstof in de binnenstad.', 'voor')
    con = _fact(db, issue, author, 'Winkeliers verwachten minder omzet.', 'tegen')

    r = _link(app, other, issue, proposal, pro.id, con.id)
    assert r.status_code == 302 and r.headers['Location'].endswith('#basis')
    links = IssueProposalFact.query.filter_by(proposal_id=proposal.id).all()
    assert {l.fact_post_id for l in links} == {pro.id, con.id} and {l.linked_by_id for l in links} == {other.id}
    # Koppelen, niet kopiëren: nog steeds precies twee feiten in het vraagstuk.
    assert Post.query.filter_by(thread_id=issue.thread_id, kind=FACT_KIND).count() == 2

    page = _req(_client(app), 'GET', _base(issue, proposal)).get_data(as_text=True)
    basis = page.split('id="basis"')[1].split('</section>')[0]
    assert 'Pleit voor' in basis and 'Pleit tegen' in basis
    assert 'Minder fijnstof in de binnenstad.' in basis and 'Winkeliers verwachten minder omzet.' in basis
    assert 'Gekoppeld door' in basis and '(2)' in basis


def test_same_fact_only_once_and_only_from_this_issue(app, db, make_user):
    author = make_user()
    issue, proposal = _setup(db, author)
    other_issue, _other = _setup(db, author)
    fact = _fact(db, issue, author, 'Een feit')
    foreign = _fact(db, other_issue, author, 'Feit van een ander vraagstuk')
    opening = issues.opening_post(issue)
    _link(app, author, issue, proposal, fact.id)
    _link(app, author, issue, proposal, fact.id, foreign.id, opening.id)
    assert [l.fact_post_id for l in IssueProposalFact.query.filter_by(proposal_id=proposal.id)] == [fact.id]
    assert ip.link(proposal, foreign, author) is None


def test_unlink_by_linker_or_moderator(app, db, make_user):
    author, linker, stranger, mod = make_user(), make_user(), make_user(), make_user('moderator')
    issue, proposal = _setup(db, author)
    a, b = _fact(db, issue, author, 'Feit A'), _fact(db, issue, author, 'Feit B')
    _link(app, linker, issue, proposal, a.id, b.id)
    row_a = IssueProposalFact.query.filter_by(fact_post_id=a.id).one()
    row_b = IssueProposalFact.query.filter_by(fact_post_id=b.id).one()

    page = _req(_client(app, stranger), 'GET', _base(issue, proposal)).get_data(as_text=True)
    assert 'Ontkoppelen' not in page
    assert _req(_client(app, stranger), 'POST', _base(issue, proposal) + f'/ontkoppelen/{row_a.id}').status_code == 403
    assert _req(_client(app, author), 'POST', _base(issue, proposal) + f'/ontkoppelen/{row_a.id}').status_code == 403

    assert 'Ontkoppelen' in _req(_client(app, linker), 'GET', _base(issue, proposal)).get_data(as_text=True)
    assert _req(_client(app, linker), 'POST', _base(issue, proposal) + f'/ontkoppelen/{row_a.id}').status_code == 302
    assert _req(_client(app, mod), 'POST', _base(issue, proposal) + f'/ontkoppelen/{row_b.id}').status_code == 302
    assert IssueProposalFact.query.filter_by(proposal_id=proposal.id).count() == 0
    assert Post.query.filter_by(id=a.id).count() == 1  # het feit zelf blijft


@pytest.mark.parametrize('phase,closed,allowed', [('feiten', False, True), ('bijstellen', False, True),
                                                  ('stemmen', False, False), ('vervolg', False, False),
                                                  ('feiten', True, False)])
def test_link_phases(app, db, make_user, phase, closed, allowed):
    author, other = make_user(), make_user()
    issue, proposal = _setup(db, author)
    fact, second = _fact(db, issue, author, 'Feit'), _fact(db, issue, author, 'Tweede feit')
    _link(app, other, issue, proposal, second.id)
    row = IssueProposalFact.query.filter_by(fact_post_id=second.id).one()
    issue.phase, issue.is_closed = phase, closed
    db.session.commit()

    page = _req(_client(app, other), 'GET', _base(issue, proposal)).get_data(as_text=True)
    assert ('Feit koppelen' in page) is allowed and ('Ontkoppelen' in page) is allowed
    assert (_link(app, other, issue, proposal, fact.id).status_code == 302) is allowed
    r = _req(_client(app, other), 'POST', _base(issue, proposal) + f'/ontkoppelen/{row.id}')
    assert (r.status_code == 302) is allowed


def test_link_needs_contributor(app, db, make_user):
    author = make_user()
    issue, proposal = _setup(db, author)
    fact = _fact(db, issue, author, 'Feit')
    newbie = User(username=f'n_{uuid.uuid4().hex[:6]}', email=f'{uuid.uuid4().hex[:6]}@example.com', verified=True,
                  password_hash='x')
    db.session.add(newbie)
    db.session.commit()
    try:
        assert _link(app, newbie, issue, proposal, fact.id).status_code == 403
        assert 'Feit koppelen' not in _req(_client(app, newbie), 'GET', _base(issue, proposal)).get_data(as_text=True)
        r = _link(app, None, issue, proposal, fact.id)
        assert r.status_code == 302 and '/login' in r.headers['Location']
    finally:
        db.session.delete(newbie)
        db.session.commit()


# ── Een nieuw feit toevoegen en koppelen ─────────────────────────────────

def test_new_fact_from_detail_is_added_and_linked(app, db, make_user):
    author, other = make_user(), make_user()
    issue, proposal = _setup(db, author)
    r = _req(_client(app, other), 'POST', _base(issue, proposal) + '/feit',
             data={'text': 'Nieuw onderzoek naar luchtkwaliteit.', 'direction': 'neutraal',
                   'source': 'https://rivm.example'})
    assert r.status_code == 302
    fact = Post.query.filter_by(body='Nieuw onderzoek naar luchtkwaliteit.').one()
    assert (fact.kind, fact.thread_id, fact.user_id) == (FACT_KIND, issue.thread_id, other.id)
    assert IssueProposalFact.query.filter_by(proposal_id=proposal.id, fact_post_id=fact.id).count() == 1
    # Het feit staat ook gewoon in de gedeelde lijst.
    assert 'Nieuw onderzoek naar luchtkwaliteit.' in _req(_client(app), 'GET', f'/vraagstuk/{issue.id}/feiten').get_data(as_text=True)


def test_new_fact_errors_and_phase(app, db, make_user):
    author = make_user()
    issue, proposal = _setup(db, author)
    c = _client(app, author)  # de foutmelding staat in de sessie van deze client
    r = _req(c, 'POST', _base(issue, proposal) + '/feit',
             data={'text': 'Zonder bron', 'direction': 'voor', 'source': ''})
    assert r.status_code == 303 and r.headers['Location'].endswith('#basis')
    page = _req(c, 'GET', _base(issue, proposal)).get_data(as_text=True)
    assert 'Geef een bron.' in page and 'Zonder bron' in page
    assert Post.query.filter_by(body='Zonder bron').count() == 0

    # Bijstelfase: bestaande feiten koppelen mag, een nieuw feit toevoegen niet (de feitregel).
    issue.phase = 'bijstellen'
    db.session.commit()
    r = _req(_client(app, author), 'POST', _base(issue, proposal) + '/feit',
             data={'text': 'Te laat', 'direction': 'voor', 'source': 'https://x.example'})
    assert r.status_code == 403
    page = _req(_client(app, author), 'GET', _base(issue, proposal)).get_data(as_text=True)
    assert 'Feit koppelen' in page and 'Of voeg een nieuw feit toe' not in page


# ── Koppelen bij het aanmaken ────────────────────────────────────────────

def test_link_when_making_a_proposal(app, db, make_user):
    author, other = make_user(), make_user()
    issue, _first = _setup(db, author)
    fact = _fact(db, issue, author, 'Bestaand feit', 'tegen')
    other_issue, _x = _setup(db, author)
    foreign = _fact(db, other_issue, author, 'Vreemd feit')

    form = _req(_client(app, other), 'GET', f'/vraagstuk/{issue.id}/voorstellen/nieuw').get_data(as_text=True)
    assert 'Koppel relevante feiten en onderzoek' in form and 'Bestaand feit' in form and 'Vreemd feit' not in form

    r = _req(_client(app, other), 'POST', f'/vraagstuk/{issue.id}/voorstellen/nieuw', data={
        'statement': 'Een nieuw voorstel met feiten.', 'explanation': '',
        'fact_ids': [str(fact.id), str(foreign.id)],
        'fact_text': 'Een nieuw feit erbij.', 'fact_direction': 'voor', 'fact_source': 'https://nieuw.example'})
    proposal = IssueProposal.query.filter_by(statement='Een nieuw voorstel met feiten.').one()
    assert r.headers['Location'].endswith(_base(issue, proposal))
    new_fact = Post.query.filter_by(body='Een nieuw feit erbij.').one()
    assert new_fact.thread_id == issue.thread_id and new_fact.kind == FACT_KIND
    linked = {l.fact_post_id for l in IssueProposalFact.query.filter_by(proposal_id=proposal.id)}
    assert linked == {fact.id, new_fact.id}


def test_new_fact_error_on_form_keeps_everything(app, db, make_user):
    author = make_user()
    issue, _first = _setup(db, author)
    fact = _fact(db, issue, author, 'Bestaand feit')
    c = _client(app, author)
    r = _req(c, 'POST', f'/vraagstuk/{issue.id}/voorstellen/nieuw', data={
        'statement': 'Voorstel met een half feit.', 'fact_ids': [str(fact.id)],
        'fact_text': 'Feit zonder richting', 'fact_source': 'https://x.example'})
    assert r.status_code == 303
    assert IssueProposal.query.filter_by(statement='Voorstel met een half feit.').count() == 0
    page = _req(c, 'GET', f'/vraagstuk/{issue.id}/voorstellen/nieuw').get_data(as_text=True)
    assert 'Kies een richting.' in page and 'Voorstel met een half feit.' in page
    assert f'value="{fact.id}" checked' in page and 'Feit zonder richting' in page


# ── Weergave en opruimen ─────────────────────────────────────────────────

def test_basis_section_in_english(app, db, make_user):
    author = make_user()
    issue, proposal = _setup(db, author)
    fact = _fact(db, issue, author, 'Fewer cars, cleaner air.', 'neutraal')
    _link(app, author, issue, proposal, fact.id)
    page = _req(_client(app, author, 'en'), 'GET', _base(issue, proposal)).get_data(as_text=True)
    basis = page.split('id="basis"')[1].split('</section>')[0]
    for text in ('What this proposal is based on', 'Neutral', 'Fewer cars, cleaner air.', 'Linked by', 'Unlink',
                 'Link a fact', 'Or add a new fact'):
        assert text in basis


def test_deleting_a_fact_removes_its_links(app, db, make_user):
    author = make_user()
    issue, proposal = _setup(db, author)
    fact = _fact(db, issue, author, 'Mijn feit')
    _link(app, author, issue, proposal, fact.id)
    r = _req(_client(app, author), 'POST', f'/forum/feit/{fact.id}/verwijderen')
    assert r.status_code == 302
    assert IssueProposalFact.query.filter_by(proposal_id=proposal.id).count() == 0


def test_deleting_a_user_detaches_links(app, db, make_user):
    from app import quality
    author, linker = make_user(), make_user()
    issue, proposal = _setup(db, author)
    fact = _fact(db, issue, author, 'Feit')
    _link(app, linker, issue, proposal, fact.id)
    quality.detach_user(linker.id)
    db.session.commit()
    assert IssueProposalFact.query.filter_by(proposal_id=proposal.id).one().linked_by_id is None
