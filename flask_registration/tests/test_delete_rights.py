"""Delete rights step 1: the rights core (backend only).

The permission matrix for deleting issues and proposals and hiding proposals
(app/issues.can_delete_issue, can_hide_proposal, can_delete_proposal), the two
AppSetting toggles that change it, the soft-hidden state of a proposal with
hide/unhide, and the audit log (ModerationAction) written by hide, unhide and
the two delete routes.
"""
from datetime import datetime, timedelta

import pytest
from flask_login import AnonymousUserMixin

from app import issues
from app.models import AppSetting, Issue, IssueProposal, ModerationAction, Post
from tests.test_delete_issue import _clean_session, _cleanup, _client, _req, _user  # noqa: F401  (autouse fixture)

SENIOR_KEY = 'delete.senior_can_delete_issues'
MOD_KEY = 'delete.moderator_can_delete_own_issue_proposals'


@pytest.fixture(autouse=True)
def _default_settings(db):
    """The test DB is session-wide (and SQLite reuses ids): start and end every
    test with the default settings and an empty audit log."""
    def reset():
        AppSetting.query.filter(AppSetting.key.in_([SENIOR_KEY, MOD_KEY])).delete(synchronize_session=False)
        ModerationAction.query.delete(synchronize_session=False)
        db.session.commit()
    reset()
    yield
    reset()


def _set(db, key, on):
    issues.set_delete_setting(key, on)
    db.session.commit()


def _issue(db, owner, title):
    issue = issues.create({'title': title, 'intro': 'Toelichting.', 'kind': 'online'}, owner)
    db.session.commit()
    return issue


def _proposal(db, issue, author, statement):
    from app import issue_proposals as ip
    proposal = ip.create(issue, author, statement)
    db.session.commit()
    return proposal


def _react(db, issue, proposal, user):
    db.session.add(Post(thread_id=issue.thread_id, user_id=user.id, author_name='', author_username=user.username,
                        body='Een reactie.', source_lang='nl', issue_proposal_id=proposal.id, proposal_version=1))
    db.session.commit()


@pytest.fixture
def world(db):
    """Users of every rank, an issue owned by a moderator (with a proposal by an
    author), one by a senior moderator and one by a contributor."""
    w = {
        'admin': _user(db, admin=True), 'senior': _user(db, 'senior_moderator'),
        'mod': _user(db, 'moderator'), 'other_mod': _user(db, 'moderator'),
        'author': _user(db), 'contributor': _user(db), 'voter': _user(db, 'voter'),
        'anon': AnonymousUserMixin(),
    }
    w['mod_issue'] = _issue(db, w['mod'], 'Vraagstuk van de moderator')
    w['senior_issue'] = _issue(db, w['senior'], 'Vraagstuk van de senior')
    w['plain_issue'] = _issue(db, w['contributor'], 'Vraagstuk van een deelnemer')
    w['p_mod'] = _proposal(db, w['mod_issue'], w['author'], 'Voorstel bij de moderator.')
    w['p_plain'] = _proposal(db, w['plain_issue'], w['author'], 'Voorstel bij de deelnemer.')
    issue_ids = [w[key].id for key in ('mod_issue', 'senior_issue', 'plain_issue')]
    yield w
    db.session.rollback()
    for issue_id in issue_ids:
        _cleanup(issue_id)


# ── The matrix ────────────────────────────────────────────────────────────

def test_delete_issue_matrix(world):
    w = world
    expect = {
        # user:        (own-by-mod, own-by-senior, own-by-contributor)
        'admin':       (True, True, True),
        'senior':      (False, True, False),   # toggle off: only the own issue
        'mod':         (True, False, False),   # only the own issue
        'other_mod':   (False, False, False),
        'author':      (False, False, False),
        'contributor': (False, False, False),  # owning an issue is not enough without a moderator role
        'voter':       (False, False, False),
        'anon':        (False, False, False),
    }
    for who, row in expect.items():
        got = tuple(issues.can_delete_issue(w[who], w[k]) for k in ('mod_issue', 'senior_issue', 'plain_issue'))
        assert got == row, who


def test_hide_proposal_matrix(world):
    w = world
    expect = {
        # user:        (on the moderator's issue, on the contributor's issue)
        'admin':       (True, True),
        'senior':      (True, True),     # all proposals, no toggle
        'mod':         (True, False),    # only on the own issue, no toggle
        'other_mod':   (False, False),
        'author':      (False, False),
        'contributor': (False, False),   # the issue owner without a moderator role
        'voter':       (False, False),
        'anon':        (False, False),
    }
    for who, row in expect.items():
        assert (issues.can_hide_proposal(w[who], w['p_mod']), issues.can_hide_proposal(w[who], w['p_plain'])) == row, who


def test_delete_proposal_matrix(db, world):
    w = world
    expect = {
        'admin':       (True, True),
        'senior':      (True, True),
        'mod':         (True, False),    # own issue, toggle on by default
        'other_mod':   (False, False),
        'author':      (True, True),     # no reactions yet
        'contributor': (False, False),
        'voter':       (False, False),
        'anon':        (False, False),
    }
    for who, row in expect.items():
        got = (issues.can_delete_proposal(w[who], w['p_mod']), issues.can_delete_proposal(w[who], w['p_plain']))
        assert got == row, who
    # Once anyone reacted, the author can no longer delete; the others are unchanged.
    _react(db, w['mod_issue'], w['p_mod'], w['contributor'])
    assert not issues.can_delete_proposal(w['author'], w['p_mod'])
    assert issues.can_delete_proposal(w['author'], w['p_plain'])
    for who in ('admin', 'senior', 'mod'):
        assert issues.can_delete_proposal(w[who], w['p_mod']), who


def test_a_suspended_moderator_can_do_nothing(db, world):
    w = world
    w['mod'].suspended_until = datetime.utcnow() + timedelta(days=3)
    db.session.commit()
    assert not issues.can_delete_issue(w['mod'], w['mod_issue'])
    assert not issues.can_hide_proposal(w['mod'], w['p_mod'])
    assert not issues.can_delete_proposal(w['mod'], w['p_mod'])


# ── The two settings ──────────────────────────────────────────────────────

def test_settings_default_and_flip_behaviour(db, world):
    w = world
    assert issues.delete_setting(SENIOR_KEY) is False and issues.delete_setting(MOD_KEY) is True

    # A senior moderator may delete any issue once the first setting is on.
    assert not issues.can_delete_issue(w['senior'], w['mod_issue'])
    _set(db, SENIOR_KEY, True)
    assert issues.delete_setting(SENIOR_KEY) is True
    assert issues.can_delete_issue(w['senior'], w['mod_issue']) and issues.can_delete_issue(w['senior'], w['plain_issue'])
    assert not issues.can_delete_issue(w['mod'], w['plain_issue'])  # a moderator is not affected

    # A moderator may no longer delete proposals on the own issue once the second is off; hiding stays.
    assert issues.can_delete_proposal(w['mod'], w['p_mod'])
    _set(db, MOD_KEY, False)
    assert issues.delete_setting(MOD_KEY) is False
    assert not issues.can_delete_proposal(w['mod'], w['p_mod'])
    assert issues.can_hide_proposal(w['mod'], w['p_mod'])
    assert issues.can_delete_proposal(w['senior'], w['p_mod'])  # a senior moderator is not affected
    _set(db, MOD_KEY, True)
    assert issues.can_delete_proposal(w['mod'], w['p_mod'])


# ── Hiding and the audit log ──────────────────────────────────────────────

def test_hide_and_unhide_set_state_and_log(db, world):
    w = world
    proposal = w['p_mod']
    assert not proposal.is_hidden and proposal.hidden_at is None
    assert issues.hide(proposal, w['mod']) is True
    db.session.commit()
    assert proposal.is_hidden and proposal.hidden_at is not None and proposal.hidden_by_id == w['mod'].id
    assert issues.hide(proposal, w['mod']) is False  # already hidden: nothing changes, nothing logged
    assert issues.unhide(proposal, w['senior']) is True
    db.session.commit()
    assert not proposal.is_hidden and proposal.hidden_at is None and proposal.hidden_by_id is None
    rows = ModerationAction.query.filter_by(target_type='proposal', target_id=proposal.id).order_by(ModerationAction.id).all()
    assert [(r.action, r.actor_id, r.snapshot) for r in rows] == [
        ('hide', w['mod'].id, 'Voorstel bij de moderator.'),
        ('unhide', w['senior'].id, 'Voorstel bij de moderator.')]
    assert all(r.created_at is not None for r in rows)


def test_hide_routes_are_guarded_and_log(app, db, world):
    w = world
    pid, iid = w['p_mod'].id, w['mod_issue'].id
    hide, show = f'/vraagstuk/{iid}/voorstel/{pid}/verbergen', f'/vraagstuk/{iid}/voorstel/{pid}/zichtbaar'
    # Not allowed: the author, another moderator, a moderator on someone else's issue. Anonymous: to the login.
    for who in ('author', 'other_mod', 'contributor'):
        assert _req(_client(app, w[who]), 'POST', hide).status_code == 403, who
    assert _req(app.test_client(), 'POST', hide).status_code == 302
    assert _req(_client(app, w['mod']), 'POST', f'/vraagstuk/{w["plain_issue"].id}/voorstel/{w["p_plain"].id}/verbergen').status_code == 403
    assert db.session.get(IssueProposal, pid).hidden_at is None
    # Only POST.
    assert _req(_client(app, w['mod']), 'GET', hide).status_code == 405
    # The moderator of the issue hides it, a senior moderator shows it again; both logged.
    r = _req(_client(app, w['mod']), 'POST', hide)
    assert r.status_code == 302 and r.headers['Location'].endswith(f'/vraagstuk/{iid}/voorstel/{pid}')
    db.session.expire_all()
    assert db.session.get(IssueProposal, pid).hidden_at is not None
    assert _req(_client(app, w['senior']), 'POST', show).status_code == 302
    db.session.expire_all()
    assert db.session.get(IssueProposal, pid).hidden_at is None
    actions = [a for (a,) in db.session.query(ModerationAction.action).filter_by(target_id=pid, target_type='proposal')]
    assert actions == ['hide', 'unhide']


# ── The delete routes ─────────────────────────────────────────────────────

def test_moderator_deletes_own_issue_and_it_is_logged(app, db, world):
    w = world
    own, other = w['mod_issue'], w['plain_issue']
    assert _req(_client(app, w['mod']), 'GET', f'/vraagstuk/{other.id}/verwijderen').status_code == 403
    assert _req(_client(app, w['mod']), 'POST', f'/vraagstuk/{other.id}/verwijderen',
                data={'confirm_title': other.title}).status_code == 403
    issue_id = own.id
    assert _req(_client(app, w['mod']), 'GET', f'/vraagstuk/{issue_id}/verwijderen').status_code == 200
    r = _req(_client(app, w['mod']), 'POST', f'/vraagstuk/{issue_id}/verwijderen',
             data={'confirm_title': 'Vraagstuk van de moderator'})
    assert r.status_code == 302
    db.session.expire_all()
    assert db.session.get(Issue, issue_id) is None
    row = ModerationAction.query.filter_by(action='delete_issue', target_id=issue_id).one()
    assert (row.actor_id, row.target_type, row.snapshot) == (w['mod'].id, 'issue', 'Vraagstuk van de moderator')


def test_senior_moderator_deletes_any_issue_only_with_the_setting(app, db, world):
    w = world
    path = f'/vraagstuk/{w["plain_issue"].id}/verwijderen'
    assert _req(_client(app, w['senior']), 'GET', path).status_code == 403
    _set(db, SENIOR_KEY, True)
    assert _req(_client(app, w['senior']), 'GET', path).status_code == 200


def test_author_deletes_own_proposal_only_without_reactions(app, db, world):
    w = world
    issue, proposal = w['plain_issue'], w['p_plain']
    path = f'/vraagstuk/{issue.id}/voorstel/{proposal.id}/verwijderen'
    _react(db, issue, proposal, w['contributor'])
    assert _req(_client(app, w['author']), 'GET', path).status_code == 403
    assert _req(_client(app, w['author']), 'POST', path, data={'confirm_title': proposal.statement}).status_code == 403
    assert db.session.get(IssueProposal, proposal.id) is not None

    fresh = _proposal(db, issue, w['author'], 'Nog zonder reacties.')
    path, pid = f'/vraagstuk/{issue.id}/voorstel/{fresh.id}/verwijderen', fresh.id
    assert _req(_client(app, w['author']), 'GET', path).status_code == 200
    r = _req(_client(app, w['author']), 'POST', path, data={'confirm_title': 'Nog zonder reacties.'})
    assert r.status_code == 302
    db.session.expire_all()
    assert db.session.get(IssueProposal, pid) is None
    row = ModerationAction.query.filter_by(action='delete_proposal', target_id=pid).one()
    assert (row.actor_id, row.target_type, row.snapshot) == (w['author'].id, 'proposal', 'Nog zonder reacties.')


def test_moderator_proposal_delete_follows_the_setting_and_removes_reactions(app, db, world):
    w = world
    issue, proposal = w['mod_issue'], w['p_mod']
    _react(db, issue, proposal, w['contributor'])
    path = f'/vraagstuk/{issue.id}/voorstel/{proposal.id}/verwijderen'
    _set(db, MOD_KEY, False)
    assert _req(_client(app, w['mod']), 'GET', path).status_code == 403
    _set(db, MOD_KEY, True)
    pid = proposal.id
    r = _req(_client(app, w['mod']), 'POST', path, data={'confirm_title': 'Voorstel bij de moderator.'})
    assert r.status_code == 302
    db.session.expire_all()
    assert db.session.get(IssueProposal, pid) is None
    assert Post.query.filter_by(issue_proposal_id=pid).count() == 0  # the reactions go with it
    assert ModerationAction.query.filter_by(action='delete_proposal', target_id=pid).count() == 1


def test_detached_user_leaves_log_and_hidden_state_without_name(db, world):
    from app import issue_proposals as ip
    w = world
    issues.hide(w['p_mod'], w['mod'])
    db.session.commit()
    ip.detach_user(w['mod'].id)
    db.session.commit()
    db.session.refresh(w['p_mod'])
    assert w['p_mod'].hidden_at is not None and w['p_mod'].hidden_by_id is None
    assert ModerationAction.query.filter_by(target_id=w['p_mod'].id, action='hide').one().actor_id is None
