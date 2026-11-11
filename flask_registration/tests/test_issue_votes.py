"""
Herontwerp, bouwstap 6: stemmen per voorstel, de uitslag en het samenvattende
voorstel (app/issue_proposals.py en de routes in app/routes/issues.py).

Dekt: stemmen door stemgerechtigden, één stem die je kunt wijzigen, alleen
in de stemfase en alleen op de stemlijst; de drempel en de uitslag
(onthoudingen buiten de breuk); de tussenstand aan en uit; het samenvattende
voorstel (alleen de initiatiefnemer, in de vervolgfase, als final_vote
aanstaat, één keer) met zijn eigen stemming en het aanpassen van het
vraagstuk; de weergave in het detail, de lijst en de cockpit; de tekst in
het startformulier; anonimiseren van stemmen bij het verwijderen van een
account.

Gedeelde conftest-app (CSRF uit).
"""
import uuid
from datetime import datetime, timedelta

import pytest
from flask import g
from flask_babel import refresh

from app import bcrypt, issue_proposals as ip, issues
from app.models import (Issue, IssueProposal, IssueProposalFact, IssueProposalSupport, IssueProposalVote, Post,
                        Thread, User)
from tests.test_forum_conduct import _fresh_rate_limits  # noqa: F401  (autouse)


@pytest.fixture
def make_user(db):
    made = []

    def _make(role='contributor'):
        name = f's_{uuid.uuid4().hex[:8]}'
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


def _setup(db, author, supporters, threshold=1, interim=True, final_vote=True, phase='stemmen', pct=50):
    """Een vraagstuk met één voorstel op de stemlijst en één zonder steun."""
    issue = issues.create({'title': 'Wat doen we met het plein?', 'intro': 'Context.', 'kind': 'online',
                           'support_threshold': threshold, 'show_interim': interim, 'final_vote': final_vote,
                           'proposal_threshold_pct': pct}, author)
    db.session.flush()
    listed = ip.create(issue, author, 'Maak het plein autovrij.')
    unlisted = ip.create(issue, author, 'Verplaats de markt.')
    db.session.flush()
    for u in supporters[:threshold]:
        ip.support(listed, u)
    issue.phase = phase
    db.session.commit()
    return issue, listed, unlisted


def _detail(issue, proposal):
    return f'/vraagstuk/{issue.id}/voorstel/{proposal.id}'


def _vote(app, user, issue, proposal, choice):
    return _req(_client(app, user), 'POST', _detail(issue, proposal) + '/stemmen', data={'choice': choice})


# ── Stemmen ──────────────────────────────────────────────────────────────

def test_voter_votes_once_and_can_change(app, db, make_user):
    author, voter = make_user(), make_user('voter')
    issue, listed, _u = _setup(db, author, [voter])
    r = _vote(app, voter, issue, listed, 'voor')
    assert r.status_code == 302 and r.headers['Location'].endswith('#stemming')
    _vote(app, voter, issue, listed, 'tegen')
    rows = IssueProposalVote.query.filter_by(proposal_id=listed.id).all()
    assert [(r.user_id, r.choice) for r in rows] == [(voter.id, 'tegen')]
    page = _req(_client(app, voter), 'GET', _detail(issue, listed)).get_data(as_text=True)
    assert 'Je stem: Tegen.' in page and 'aria-pressed="true">Tegen<' in page
    assert _vote(app, voter, issue, listed, 'misschien').status_code == 400


def test_vote_rules(app, db, make_user):
    author, voter, contributor = make_user(), make_user('voter'), make_user()
    issue, listed, unlisted = _setup(db, author, [voter])
    assert _vote(app, contributor, issue, listed, 'voor').status_code == 403  # geen stemrecht
    assert _vote(app, voter, issue, unlisted, 'voor').status_code == 403      # niet op de stemlijst
    page = _req(_client(app, contributor), 'GET', _detail(issue, listed)).get_data(as_text=True)
    assert 'Je hebt nog geen stemrecht' in page and 'Je mag meestemmen na 3 kwaliteitsbijdragen' in page
    assert 'name="choice"' not in page
    page = _req(_client(app, voter), 'GET', _detail(issue, unlisted)).get_data(as_text=True)
    # Not on the list: the status at the top says so; there is no vote card.
    assert 'Niet op de stemlijst' in page and 'id="stemming"' not in page and 'name="choice"' not in page
    anon = _req(_client(app), 'GET', _detail(issue, listed)).get_data(as_text=True)
    assert 'Log in om te stemmen.' in anon

    for phase, closed in (('feiten', False), ('bijstellen', False), ('vervolg', False), ('stemmen', True)):
        issue.phase, issue.is_closed = phase, closed
        db.session.commit()
        assert _vote(app, voter, issue, listed, 'voor').status_code == 403
    assert IssueProposalVote.query.filter_by(proposal_id=listed.id).count() == 0


# ── Uitslag en tussenstand ───────────────────────────────────────────────

def _cast(db, proposal, choices):
    voters = []
    for choice in choices:
        u = User(username=f'v_{uuid.uuid4().hex[:8]}', email=f'{uuid.uuid4().hex[:8]}@example.com',
                 verified=True, password_hash='x')
        db.session.add(u)
        db.session.flush()
        db.session.add(IssueProposalVote(proposal_id=proposal.id, user_id=u.id, choice=choice))
        voters.append(u)
    db.session.commit()
    return voters


def _drop(db, users):
    IssueProposalVote.query.filter(IssueProposalVote.user_id.in_([u.id for u in users])).update(
        {IssueProposalVote.user_id: None}, synchronize_session=False)
    for u in users:
        db.session.delete(u)
    db.session.commit()


def test_result_threshold_and_abstentions(app, db, make_user):
    author, supporter = make_user(), make_user()
    issue, listed, _u = _setup(db, author, [supporter])
    others = _cast(db, listed, ['voor', 'voor', 'voor', 'tegen', 'onthouding', 'onthouding'])
    try:
        res = ip.result(listed)
        assert res['counts'] == {'voor': 3, 'tegen': 1, 'onthouding': 2} and res['total'] == 6
        assert res['pct_for'] == 75.0 and res['pct_against'] == 25.0 and res['adopted'] is True
        # Precies op de drempel is niet genoeg: "meer dan".
        issue.proposal_threshold_pct = 75
        db.session.commit()
        assert ip.result(listed)['adopted'] is False
        issue.proposal_threshold_pct = 50
        issue.phase = 'vervolg'
        db.session.commit()
        page = _req(_client(app), 'GET', _detail(issue, listed)).get_data(as_text=True)
        assert 'Aangenomen' in page and '75% voor, 25% tegen (van de geldige stemmen)' in page
        assert 'Uitgebrachte stemmen: 6' in page or 'stemmen: 6' in page
        listing = _req(_client(app), 'GET', f'/vraagstuk/{issue.id}/voorstellen').get_data(as_text=True)
        assert 'Aangenomen met 75% voor' in listing and 'Niet op de stemlijst (0 van 1)' in listing
        cockpit = _req(_client(app), 'GET', f'/vraagstuk/{issue.id}').get_data(as_text=True)
        assert 'Aangenomen met 75% voor' in cockpit.split('id="panel-proposals"')[1]
    finally:
        _drop(db, others)


def test_rejected_without_valid_votes(app, db, make_user):
    author, supporter = make_user(), make_user()
    issue, listed, _u = _setup(db, author, [supporter], phase='vervolg')
    res = ip.result(listed)
    assert res['pct_for'] is None and res['adopted'] is False
    listing = _req(_client(app), 'GET', f'/vraagstuk/{issue.id}/voorstellen').get_data(as_text=True)
    assert 'Verworpen, geen geldige stemmen' in listing


@pytest.mark.parametrize('interim', [True, False])
def test_interim_tally(app, db, make_user, interim):
    author, supporter = make_user(), make_user()
    issue, listed, _u = _setup(db, author, [supporter], interim=interim)
    others = _cast(db, listed, ['voor', 'tegen'])
    try:
        page = _req(_client(app), 'GET', _detail(issue, listed)).get_data(as_text=True)
        vote = page.split('id="stemming"')[1].split('</section>')[0]
        assert ('Tussenstand' in vote) is interim
        assert ('2 mensen hebben al gestemd.' in vote) is (not interim)
        # The rules belong to the issue: the proposal page only links to it.
        assert 'Stemregels bij dit vraagstuk' in page and 'id="stemregels"' not in page
    finally:
        _drop(db, others)


# ── Het samenvattende voorstel ─────────────────────────────────────────────────

def test_final_proposal_by_initiator_in_summary_phase(app, db, make_user):
    # Polijstronde P6: het samenvattende voorstel heeft een eigen fase (samenvatten).
    author, other, voter = make_user(), make_user(), make_user('voter')
    issue, listed, _u = _setup(db, author, [other], phase='samenvatten')
    url = f'/vraagstuk/{issue.id}/samenvattend-voorstel'
    data = {'statement': 'Het plein wordt autovrij, met de markt op zaterdag.', 'explanation': 'Samengevoegd.',
            'title': 'Hoe richten we het plein in?', 'intro': 'Aangescherpte vraag.'}

    assert _req(_client(app, other), 'GET', url).status_code == 403  # alleen de initiatiefnemer
    cockpit = _req(_client(app, author), 'GET', f'/vraagstuk/{issue.id}').get_data(as_text=True)
    assert 'Samenvattend voorstel doen' in cockpit
    form = _req(_client(app, author), 'GET', url).get_data(as_text=True)
    assert 'De voorstellen en hun uitslag' in form and 'Maak het plein autovrij.' in form

    r = _req(_client(app, author), 'POST', url, data=data)
    final = IssueProposal.query.filter_by(issue_id=issue.id, is_final=True).one()
    assert r.headers['Location'].endswith(_detail(issue, final))
    db.session.refresh(issue)
    assert (issue.title, issue.intro) == ('Hoe richten we het plein in?', 'Aangescherpte vraag.')
    assert issue.thread.title_prefix_nl == 'Hoe richten we het plein in?'
    assert _req(_client(app, author), 'POST', url, data=data).status_code == 403  # maar één
    assert final not in [p for p, _n in ip.listing(issue)]  # niet tussen de gewone voorstellen

    # Stemmen over het samenvattende voorstel: zonder steunronde, in de fase samenvatten.
    assert _vote(app, voter, issue, final, 'voor').status_code == 302
    page = _req(_client(app), 'GET', _detail(issue, final)).get_data(as_text=True)
    assert 'Eindstemming' in page and 'Samenvattend voorstel' in page and 'Stemmen open tot' in page
    for path in (f'/vraagstuk/{issue.id}/voorstellen', f'/vraagstuk/{issue.id}'):
        body = _req(_client(app), 'GET', path).get_data(as_text=True)
        assert 'gu-ip-finalbox' in body and 'Het plein wordt autovrij, met de markt op zaterdag.' in body

    # Na days_vote dagen is de eindstemming voorbij: de uitslag staat vast.
    final.created_at = datetime.utcnow() - timedelta(days=issue.days_vote, minutes=1)
    db.session.commit()
    assert _vote(app, voter, issue, final, 'tegen').status_code == 403
    page = _req(_client(app), 'GET', _detail(issue, final)).get_data(as_text=True)
    assert 'Aangenomen' in page and '100% voor, 0% tegen' in page


def test_final_proposal_rules(app, db, make_user):
    author, other = make_user(), make_user()
    issue, _l, _u = _setup(db, author, [other], phase='stemmen')
    url = f'/vraagstuk/{issue.id}/samenvattend-voorstel'
    assert _req(_client(app, author), 'GET', url).status_code == 403  # nog niet in de fase samenvatten
    issue.phase = 'vervolg'
    db.session.commit()
    assert _req(_client(app, author), 'GET', url).status_code == 403  # P6: niet meer in het vervolg
    issue.phase, issue.final_vote = 'samenvatten', False
    db.session.commit()
    assert _req(_client(app, author), 'GET', url).status_code == 403  # staat uit bij dit vraagstuk
    issue.final_vote = True
    db.session.commit()
    c = _client(app, author)
    r = _req(c, 'POST', url, data={'statement': '', 'title': 'Vraag'})
    assert r.status_code == 303 and IssueProposal.query.filter_by(issue_id=issue.id, is_final=True).count() == 0
    assert 'Schrijf de stelling in één zin' in _req(c, 'GET', url).get_data(as_text=True)


def test_start_form_text(app, db, make_user):
    user = make_user()
    nl = _req(_client(app, user), 'GET', '/gesprekken/nieuw').get_data(as_text=True)
    en = _req(_client(app, user, 'en'), 'GET', '/gesprekken/nieuw').get_data(as_text=True)
    assert 'Afsluiten met een samenvattend voorstel' in nl and 'name="final_vote"' in nl
    assert 'End with a summary proposal' in en


def test_deleting_a_voter_keeps_the_vote_anonymously(app, db, make_user):
    from app import quality
    author, voter = make_user(), make_user('voter')
    issue, listed, _u = _setup(db, author, [voter])
    _vote(app, voter, issue, listed, 'voor')
    quality.detach_user(voter.id)
    db.session.commit()
    row = IssueProposalVote.query.filter_by(proposal_id=listed.id).one()
    assert row.user_id is None and row.choice == 'voor'
    assert ip.tally(listed)['voor'] == 1


def test_filter_label_after_the_facts_phase(app, db, make_user):
    author, supporter = make_user(), make_user()
    issue, _l, _u = _setup(db, author, [supporter], phase='feiten')
    for i in range(5):  # de filterknoppen staan er pas bij meer dan 5 voorstellen
        ip.create(issue, supporter, f'Extra voorstel {i}.')
    db.session.commit()
    page = _req(_client(app), 'GET', f'/vraagstuk/{issue.id}/voorstellen').get_data(as_text=True)
    assert '>Verzamelt steun</a>' in page
    issue.phase = 'stemmen'
    db.session.commit()
    page = _req(_client(app), 'GET', f'/vraagstuk/{issue.id}/voorstellen').get_data(as_text=True)
    assert '>Niet op de stemlijst</a>' in page and '>Verzamelt steun</a>' not in page
