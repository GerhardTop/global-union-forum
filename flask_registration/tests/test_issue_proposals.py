"""
Herontwerp, bouwstap 4: voorstellen onder een vraagstuk (app/issue_proposals.py
en de routes in app/routes/issues.py).

Dekt: een voorstel doen (meerdere per vraagstuk, zonder eigen gesprek),
foute invoer en de fase; steunen (één keer, niet het eigen voorstel, alleen
zolang het steun verzamelt) en de status; bijstellen (alleen de inbrenger,
in de bijstelfase, één keer, met behoud van de oorspronkelijke versie); de
voorstellenpagina met filter en sorteren; het detail; de cockpit; opruimen.

Gedeelde conftest-app (CSRF uit).
"""
import uuid
from datetime import datetime, timedelta

import pytest
from flask import g
from flask_babel import refresh

from app import bcrypt, issue_proposals as ip, issues
from app.models import Issue, IssueProposal, IssueProposalSupport, Post, Thread, User
from tests.test_forum_conduct import _fresh_rate_limits  # noqa: F401  (autouse)


@pytest.fixture
def make_user(db):
    made = []

    def _make(rules=True):
        name = f'p_{uuid.uuid4().hex[:8]}'
        u = User(username=name, email=f'{name}@example.com', verified=True,
                 password_hash=bcrypt.generate_password_hash('Sterk1!ww').decode('utf-8'))
        if rules:
            u.set_role('contributor')
            u.accept_rules()
        db.session.add(u)
        db.session.commit()
        made.append(u.id)
        return u

    yield _make
    from app import quality
    from app.routes.forum import _delete_thread
    for tid in [t.id for t in Thread.query.join(Post).filter(Post.user_id.in_(made)).distinct()]:
        _delete_thread(db.session.get(Thread, tid))
    issue_ids = [i for (i,) in db.session.query(Issue.id).filter(Issue.created_by_id.in_(made))]
    if issue_ids:
        proposal_ids = [p for (p,) in db.session.query(IssueProposal.id).filter(IssueProposal.issue_id.in_(issue_ids))]
        if proposal_ids:
            IssueProposalSupport.query.filter(IssueProposalSupport.proposal_id.in_(proposal_ids)).delete(
                synchronize_session=False)
            from app.models import IssueProposalVersion  # polijstronde P5
            IssueProposalVersion.query.filter(IssueProposalVersion.proposal_id.in_(proposal_ids)).delete(
                synchronize_session=False)
            Post.query.filter(Post.issue_proposal_id.in_(proposal_ids)).update(
                {Post.issue_proposal_id: None}, synchronize_session=False)
            IssueProposal.query.filter(IssueProposal.id.in_(proposal_ids)).delete(synchronize_session=False)
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


def _issue(db, user, threshold=2, phase='feiten'):
    issue = issues.create({'title': 'Hoe maken we de wijk groener?', 'intro': 'Context.', 'kind': 'online',
                           'support_threshold': threshold}, user)
    issue.phase = phase
    db.session.commit()
    return issue


def _propose(app, user, issue, statement='Meer bomen in elke straat.', explanation='Koelte en groen.'):
    return _req(_client(app, user), 'POST', f'/vraagstuk/{issue.id}/voorstellen/nieuw',
                data={'statement': statement, 'explanation': explanation})


def _support(app, user, issue, proposal, **data):
    return _req(_client(app, user), 'POST', f'/vraagstuk/{issue.id}/voorstel/{proposal.id}/steunen', data=data)


# ── Voorstel doen ────────────────────────────────────────────────────────

def test_make_several_proposals_without_own_thread(app, db, make_user):
    author, other = make_user(), make_user()
    issue = _issue(db, author)
    threads_before = Thread.query.count()
    r = _propose(app, other, issue, '  Meer   bomen in elke straat.  ')
    first = IssueProposal.query.filter_by(issue_id=issue.id).one()
    assert r.headers['Location'].endswith(f'/vraagstuk/{issue.id}/voorstel/{first.id}')
    assert (first.statement, first.explanation, first.created_by_id, first.lang) == (
        'Meer bomen in elke straat.', 'Koelte en groen.', other.id, 'nl')
    _propose(app, author, issue, 'Geveltuinen subsidiëren.', '')
    assert IssueProposal.query.filter_by(issue_id=issue.id).count() == 2
    assert IssueProposal.query.filter_by(statement='Geveltuinen subsidiëren.').one().explanation is None
    assert Thread.query.count() == threads_before  # geen eigen gesprek per voorstel


@pytest.mark.parametrize('statement,explanation', [('', 'x'), ('x' * 301, ''), ('Goed', 'x' * 5001)])
def test_invalid_proposal_is_refused(app, db, make_user, statement, explanation):
    user = make_user()
    issue = _issue(db, user)
    r = _propose(app, user, issue, statement, explanation)
    assert r.status_code == 303 and r.headers['Location'].endswith(f'/vraagstuk/{issue.id}/voorstellen/nieuw')
    assert IssueProposal.query.filter_by(issue_id=issue.id).count() == 0


def test_proposing_rules(app, db, make_user):
    author, newbie = make_user(), make_user(rules=False)
    issue = _issue(db, author)
    assert _propose(app, author, issue).status_code == 302  # feitenfase
    assert '/spelregels' in _propose(app, newbie, issue).headers['Location']
    r = _req(_client(app), 'GET', f'/vraagstuk/{issue.id}/voorstellen/nieuw')
    assert r.status_code == 302 and '/login' in r.headers['Location']


# ── Steun en status ──────────────────────────────────────────────────────

def test_support_once_not_own_and_status(app, db, make_user):
    author, a, b, c = make_user(), make_user(), make_user(), make_user()
    issue = _issue(db, author, threshold=2)
    _propose(app, author, issue)
    proposal = IssueProposal.query.filter_by(issue_id=issue.id).one()
    detail = f'/vraagstuk/{issue.id}/voorstel/{proposal.id}'

    assert _support(app, author, issue, proposal).status_code == 403  # niet je eigen voorstel
    page = _req(_client(app, a), 'GET', detail).get_data(as_text=True)
    assert 'Verzamelt steun (0 van 2)' in page and '>Steunen<' in page

    assert _support(app, a, issue, proposal).status_code == 302
    _support(app, a, issue, proposal)  # een tweede keer telt niet
    assert IssueProposalSupport.query.filter_by(proposal_id=proposal.id).count() == 1
    page = _req(_client(app, a), 'GET', detail).get_data(as_text=True)
    assert 'Verzamelt steun (1 van 2)' in page and 'Je steunt dit voorstel' in page and '>Steunen<' not in page

    _support(app, b, issue, proposal)
    page = _req(_client(app, c), 'GET', detail).get_data(as_text=True)
    assert 'Op de stemlijst' in page and '>Steunen<' not in page
    assert _support(app, c, issue, proposal).status_code == 403  # al op de stemlijst
    assert IssueProposalSupport.query.filter_by(proposal_id=proposal.id).count() == 2


def test_no_support_in_voting_phase(app, db, make_user):
    author, a = make_user(), make_user()
    issue = _issue(db, author)
    _propose(app, author, issue)
    proposal = IssueProposal.query.filter_by(issue_id=issue.id).one()
    issue.phase = 'stemmen'
    db.session.commit()
    assert _support(app, a, issue, proposal).status_code == 403


def test_support_returns_to_next(app, db, make_user):
    author, a = make_user(), make_user()
    issue = _issue(db, author)
    _propose(app, author, issue)
    proposal = IssueProposal.query.filter_by(issue_id=issue.id).one()
    nxt = f'/vraagstuk/{issue.id}/voorstellen?sorteer=nieuwste#voorstel-{proposal.id}'
    assert _support(app, a, issue, proposal, next=nxt).headers['Location'].endswith(nxt)
    assert _support(app, a, issue, proposal, next='https://evil.example').headers['Location'].endswith(
        f'/vraagstuk/{issue.id}/voorstel/{proposal.id}')


def test_proposal_of_another_issue_is_404(app, db, make_user):
    author = make_user()
    one, two = _issue(db, author), _issue(db, author)
    _propose(app, author, one)
    proposal = IssueProposal.query.filter_by(issue_id=one.id).one()
    assert _req(_client(app), 'GET', f'/vraagstuk/{two.id}/voorstel/{proposal.id}').status_code == 404


# ── Bijstellen ───────────────────────────────────────────────────────────

def test_revise_in_revision_phase_keeping_every_version(app, db, make_user):
    author, other = make_user(), make_user()
    issue = _issue(db, author)
    _propose(app, author, issue, 'Meer bomen.', 'Eerste toelichting.')
    proposal = IssueProposal.query.filter_by(issue_id=issue.id).one()
    url = f'/vraagstuk/{issue.id}/voorstel/{proposal.id}/bijstellen'
    data = {'statement': 'Meer bomen, ook op pleinen.', 'explanation': 'Aangescherpt.'}

    assert _req(_client(app, author), 'POST', url, data=data).status_code == 403  # nog in de feitenfase
    issue.phase = 'bijstellen'
    db.session.commit()
    assert _req(_client(app, other), 'POST', url, data=data).status_code == 403  # niet de inbrenger
    r = _req(_client(app, author), 'POST', url, data={'statement': 'Meer bomen.', 'explanation': 'Eerste toelichting.'})
    assert r.status_code == 303  # niets veranderd

    page = _req(_client(app, author), 'GET', f'/vraagstuk/{issue.id}/voorstel/{proposal.id}').get_data(as_text=True)
    assert 'Bijstelling opslaan' in page
    assert _req(_client(app, author), 'POST', url, data=data).status_code == 302
    db.session.refresh(proposal)
    assert (proposal.statement, proposal.explanation) == ('Meer bomen, ook op pleinen.', 'Aangescherpt.')
    assert (proposal.original_statement, proposal.original_explanation) == ('Meer bomen.', 'Eerste toelichting.')
    assert proposal.revised_at is not None
    # Polijstronde P5: nog een keer bijstellen kan; elke versie blijft bewaard.
    assert _req(_client(app, author), 'POST', url, data={'statement': 'Nog eens.'}).status_code == 302
    db.session.refresh(proposal)
    assert proposal.original_statement == 'Meer bomen.'
    assert [v.statement for v in ip.versions(proposal)] == ['Meer bomen.', 'Meer bomen, ook op pleinen.', 'Nog eens.']

    page = _req(_client(app, other), 'GET', f'/vraagstuk/{issue.id}/voorstel/{proposal.id}').get_data(as_text=True)
    # Eerdere versies ingeklapt boven de huidige, elk met de eigen tekst.
    older = page.split('id="versies"')[1].split('class="gu-pv__curhead"')[0]
    assert older.index('Versie 1') < older.index('Versie 2') and 'Versie 3' not in older
    assert '>Meer bomen.</p>' in older and '>Meer bomen, ook op pleinen.</p>' in older and 'Aangescherpt.' in older
    assert 'Huidige versie</span> Versie 3' in page and 'Bijstelling opslaan' not in page


# ── Pagina's ─────────────────────────────────────────────────────────────

def test_proposals_page_filter_and_sort(app, db, make_user):
    author, a, b = make_user(), make_user(), make_user()
    issue = _issue(db, author, threshold=2)
    base = datetime.utcnow() - timedelta(days=1)
    rows = {}
    for i, name in enumerate(('Oud met steun', 'Midden zonder steun', 'Nieuw met een steun')):
        p = ip.create(issue, author, name)
        p.created_at = base + timedelta(hours=i)
        rows[name] = p
    db.session.commit()
    for u in (a, b):
        ip.support(rows['Oud met steun'], u)
    ip.support(rows['Nieuw met een steun'], a)
    db.session.commit()

    def order(**args):
        q = '&'.join(f'{k}={v}' for k, v in args.items())
        page = _req(_client(app, a), 'GET', f'/vraagstuk/{issue.id}/voorstellen?{q}').get_data(as_text=True)
        return sorted((n for n in rows if n in page), key=page.index)

    assert order() == ['Oud met steun', 'Nieuw met een steun', 'Midden zonder steun']
    assert order(sorteer='nieuwste') == ['Nieuw met een steun', 'Midden zonder steun', 'Oud met steun']
    assert order(sorteer='stemlijst') == ['Oud met steun', 'Nieuw met een steun', 'Midden zonder steun']
    assert order(filter='stemlijst') == ['Oud met steun']
    assert order(filter='steun') == ['Nieuw met een steun', 'Midden zonder steun']

    page = _req(_client(app, b), 'GET', f'/vraagstuk/{issue.id}/voorstellen').get_data(as_text=True)
    assert page.count('>Steunen<') == 2 and 'Nieuw voorstel doen' in page
    assert f'/vraagstuk/{issue.id}/voorstellen/nieuw' in page


def test_detail_page_sections_in_english(app, db, make_user):
    author = make_user()
    issue = _issue(db, author)
    _propose(app, author, issue, 'Plant more trees.', 'Shade and green.')
    proposal = IssueProposal.query.filter_by(issue_id=issue.id).one()
    page = _req(_client(app, lang='en'), 'GET', f'/vraagstuk/{issue.id}/voorstel/{proposal.id}').get_data(as_text=True)
    for text in ('Plant more trees.', 'Shade and green.', 'Collecting support (0 of 2)', author.username,
                 'What this proposal is based on', 'Vote', 'Current version', 'Reactions to version 1',
                 'Back to the issue', 'Facts and background', 'All proposals'):
        assert text in page
    assert 'Earlier versions' not in page  # only one version


def test_cockpit_lists_proposals_with_status(app, db, make_user):
    author, a = make_user(), make_user()
    issue = _issue(db, author, threshold=1)
    _propose(app, author, issue, 'Op de lijst.')
    _propose(app, author, issue, 'Nog niet.')
    listed = IssueProposal.query.filter_by(statement='Op de lijst.').one()
    _support(app, a, issue, listed)
    body = _req(_client(app, a), 'GET', f'/vraagstuk/{issue.id}').get_data(as_text=True)
    panel = body.split('id="panel-proposals"')[1].split('</section>')[0]
    assert 'Op de stemlijst' in panel and 'Verzamelt steun (0 van 1)' in panel
    assert panel.index('Op de lijst.') < panel.index('Nog niet.')  # stemlijst eerst
    assert f'/vraagstuk/{issue.id}/voorstel/{listed.id}' in panel
    assert f'/vraagstuk/{issue.id}/voorstellen/nieuw' in panel and '/forum/nieuw' not in panel
    # P7: geen link "Alle voorstellen" meer; "toon meer" alleen als er meer is.
    assert 'data-more' not in panel.split('data-more-list')[1]


# ── Opruimen ─────────────────────────────────────────────────────────────

def test_deleting_a_user_detaches_proposals_and_supports(app, db, make_user):
    from app import quality
    author, a = make_user(), make_user()
    issue = _issue(db, author)
    _propose(app, a, issue)
    proposal = IssueProposal.query.filter_by(issue_id=issue.id).one()
    ip.support(proposal, author)
    db.session.commit()
    quality.detach_user(a.id)
    quality.detach_user(author.id)
    db.session.commit()
    db.session.refresh(proposal)
    assert proposal.created_by_id is None
    assert IssueProposalSupport.query.filter_by(proposal_id=proposal.id).count() == 0


def test_wrapper_issue_with_own_proposals_survives_thread_delete(db, make_user):
    from app.routes.forum import _delete_thread
    from tests.test_issues import _old_proposal
    author = make_user()
    proposal = _old_proposal(db, author, 'Ingepakt')
    issues.backfill()
    db.session.refresh(proposal)
    issue = proposal.issue
    ip.create(issue, author, 'Een nieuw voorstel eronder.')
    db.session.commit()
    issue_id = issue.id
    _delete_thread(proposal.thread)
    assert db.session.get(Issue, issue_id) is not None


# ── Alleen in de feitenfase: voorstellen doen en steunen ─────────────────

@pytest.mark.parametrize('phase,closed', [('bijstellen', False), ('stemmen', False), ('vervolg', False),
                                          ('feiten', True)])
def test_propose_and_support_only_in_facts_phase(app, db, make_user, phase, closed):
    author, a = make_user(), make_user()
    issue = _issue(db, author)
    _propose(app, author, issue, 'Gedaan in de feitenfase.')
    proposal = IssueProposal.query.filter_by(issue_id=issue.id).one()
    issue.phase, issue.is_closed = phase, closed
    db.session.commit()

    # Serverkant: de routes weigeren.
    assert _propose(app, a, issue, 'Te laat.').status_code == 403
    assert _support(app, a, issue, proposal).status_code == 403
    assert IssueProposal.query.filter_by(issue_id=issue.id).count() == 1
    assert IssueProposalSupport.query.filter_by(proposal_id=proposal.id).count() == 0

    # Weergave: geen knoppen op het formulier, de voorstellenpagina, het detail en in de cockpit.
    form = _req(_client(app, a), 'GET', f'/vraagstuk/{issue.id}/voorstellen/nieuw').get_data(as_text=True)
    assert 'alleen in de feitenfase' in form and 'Voorstel indienen' not in form
    listing = _req(_client(app, a), 'GET', f'/vraagstuk/{issue.id}/voorstellen').get_data(as_text=True)
    assert '>Steunen<' not in listing and 'Nieuw voorstel doen' not in listing
    detail = _req(_client(app, a), 'GET', f'/vraagstuk/{issue.id}/voorstel/{proposal.id}').get_data(as_text=True)
    assert '>Steunen<' not in detail
    cockpit = _req(_client(app, a), 'GET', f'/vraagstuk/{issue.id}').get_data(as_text=True)
    panel = cockpit.split('id="panel-proposals"')[1].split('</section>')[0]
    assert 'title="Nieuw voorstel"' not in panel and 'Gedaan in de feitenfase.' in panel


def test_buttons_shown_in_facts_phase(app, db, make_user):
    author, a = make_user(), make_user()
    issue = _issue(db, author)
    _propose(app, author, issue)
    listing = _req(_client(app, a), 'GET', f'/vraagstuk/{issue.id}/voorstellen').get_data(as_text=True)
    assert '>Steunen<' in listing and 'Nieuw voorstel doen' in listing
    cockpit = _req(_client(app, a), 'GET', f'/vraagstuk/{issue.id}').get_data(as_text=True)
    assert 'title="Nieuw voorstel"' in cockpit.split('id="panel-proposals"')[1].split('</section>')[0]


def test_revision_phase_is_only_for_revising(app, db, make_user):
    author = make_user()
    issue = _issue(db, author)
    _propose(app, author, issue, 'Eerste versie.')
    proposal = IssueProposal.query.filter_by(issue_id=issue.id).one()
    issue.phase = 'bijstellen'
    db.session.commit()
    assert _propose(app, author, issue, 'Nieuw in de bijstelfase.').status_code == 403
    r = _req(_client(app, author), 'POST', f'/vraagstuk/{issue.id}/voorstel/{proposal.id}/bijstellen',
             data={'statement': 'Bijgestelde versie.', 'explanation': ''})
    assert r.status_code == 302
