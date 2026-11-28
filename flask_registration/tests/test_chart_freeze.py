"""Background figures, step 1g: the snapshot is frozen when the voting phase
starts (issue_lifecycle.set_phase), and a link laid by the AI carries by_ai
("Linked by AI"; the proposer may undo it, and it is remembered).

Charts are published the way a selection round does (tests of step 1d);
never network.
"""
import uuid
from datetime import datetime, timedelta

from app import background_figures as bf, issue_lifecycle as lifecycle, issue_proposals as ip
from app.models import ChartBlock, ChartSnapshot, IssueProposalFact
from app.sources import owid
from tests.test_background_figures import (FakeClient, FakeHttp, _answer, _chart, _charts, _issue, _proposal,  # noqa: F401
                                           made, started, watched)
from tests.test_chart_page import _get
from tests.test_delete_issue import _clean_session  # noqa: F401  (autouse fixture)
from tests.test_issue_polish import _client, _req, make_user  # noqa: F401  (fixture)


def _published(db, make_user, made, proposals=0, owner=None):
    from app.models import ChartSelectionRun
    owner = owner or make_user()
    issue = _issue(db, owner, made)
    props = [_proposal(db, issue, owner) for _ in range(proposals)]
    first = ChartSelectionRun.query.filter_by(issue_id=issue.id, trigger='issue').first()
    assert bf.run(first.id, client=FakeClient(_answer(_chart(relevant=[p.id for p in props]))),
                  http=FakeHttp()) == 'done'
    (chart,) = _charts(issue)
    return owner, issue, chart, props


def _frozen(chart):
    return ChartSnapshot.query.filter_by(chart_id=chart.id).order_by(ChartSnapshot.version.desc()).first().frozen_at


# ── Freezing ─────────────────────────────────────────────────────────────

def test_the_tick_freezes_at_the_voting_phase(app, db, make_user, made, started, watched):
    _owner, issue, chart, _props = _published(db, make_user, made)
    assert _frozen(chart) is None
    assert 'Wordt bevroren als de stemfase begint' in _get(app, issue, chart).get_data(as_text=True)
    issue.phase, issue.phase_deadline = 'bijstellen', datetime.utcnow() - timedelta(minutes=1)
    db.session.commit()
    assert lifecycle.advance(issue) and issue.phase == 'stemmen'
    db.session.commit()
    db.session.expire_all()
    assert _frozen(chart) == issue.phase_started_at      # frozen when voting started
    page = _get(app, issue, chart).get_data(as_text=True)
    assert 'Bevroren op' in page and 'toen de stemfase begon' in page
    assert 'Wordt bevroren als de stemfase begint' not in page


def test_moving_on_by_hand_freezes(app, db, make_user, made, started, watched):
    owner, issue, chart, _props = _published(db, make_user, made)
    issue.phase = 'bijstellen'
    db.session.commit()
    resp = _req(_client(app, owner), 'POST', f'/vraagstuk/{issue.id}/volgende-fase')
    assert resp.status_code in (302, 303)
    db.session.expire_all()
    assert issue.phase == 'stemmen' and _frozen(chart) is not None


def test_a_frozen_snapshot_keeps_its_date_and_reopening_does_not_thaw(app, db, make_user, made, started, watched):
    _owner, issue, chart, _props = _published(db, make_user, made)
    first = datetime(2026, 11, 20, 12, 0)
    lifecycle.set_phase(issue, 'stemmen', first)       # frozen at the start of the voting phase
    db.session.commit()
    db.session.expire_all()
    assert _frozen(chart) == first
    lifecycle.set_phase(issue, 'stemmen', datetime.utcnow())   # a second transition
    db.session.commit()
    db.session.expire_all()
    assert _frozen(chart) == first
    lifecycle.reopen(issue)
    db.session.commit()
    db.session.expire_all()
    assert issue.phase == 'feiten' and _frozen(chart) == first
    page = _get(app, issue, chart).get_data(as_text=True)
    assert 'Bevroren op 20 nov 2026' in page


def test_freezing_touches_only_this_issue_and_the_current_snapshot(app, db, make_user, made, started, watched):
    _owner, issue, chart, _props = _published(db, make_user, made)
    _owner2, other, other_chart, _p = _published(db, make_user, made)
    # A newer snapshot next to the first: only the newest is frozen; a new one
    # never overwrites (create_snapshot takes the next version).
    parsed = owid.parse(_sipri_csv(), _sipri_meta())
    newer = owid.create_snapshot(chart, parsed, _sipri_meta(), 2016, 2025)
    db.session.commit()
    assert newer.version == 2
    bf.freeze_for_issue(issue.id)
    db.session.commit()
    db.session.expire_all()
    versions = {s.version: s.frozen_at for s in ChartSnapshot.query.filter_by(chart_id=chart.id)}
    assert versions[1] is None and versions[2] is not None
    assert _frozen(other_chart) is None


def _sipri_csv():
    from tests.test_background_figures import _read, SIPRI
    return _read(f'{SIPRI}.csv')


def _sipri_meta():
    import json
    from tests.test_background_figures import _read, SIPRI
    return json.loads(_read(f'{SIPRI}.metadata.json'))


# ── Linked by AI ─────────────────────────────────────────────────────────

def test_an_ai_link_says_so_and_the_proposer_may_undo_it(app, db, make_user, made, started, watched):
    owner, issue, chart, (proposal,) = _published(db, make_user, made, proposals=1)
    link = IssueProposalFact.query.filter_by(fact_post_id=chart.post_id, proposal_id=proposal.id).one()
    assert link.by_ai is True and link.linked_by_id is None
    page = _req(_client(app, owner), 'GET', f'/vraagstuk/{issue.id}/voorstel/{proposal.id}').get_data(as_text=True)
    assert 'Gekoppeld door AI' in page and '@deleted_user' not in page
    assert ip.can_unlink(link, owner)
    resp = _req(_client(app, owner), 'POST', f'/vraagstuk/{issue.id}/voorstel/{proposal.id}/ontkoppelen/{link.id}')
    assert resp.status_code in (302, 303)
    assert ChartBlock.query.filter_by(issue_id=issue.id, kind='link', proposal_id=proposal.id,
                                      fact_post_id=chart.post_id).count() == 1


def test_a_link_of_a_deleted_account_is_no_ai_link(app, db, make_user, made, started, watched):
    owner, issue, chart, (proposal,) = _published(db, make_user, made, proposals=1)
    IssueProposalFact.query.filter_by(fact_post_id=chart.post_id).delete()
    member = make_user()
    link = IssueProposalFact(proposal_id=proposal.id, fact_post_id=chart.post_id, linked_by_id=member.id)
    db.session.add(link)
    db.session.commit()
    # The member deletes their account: the link loses its person (users.detach).
    link.linked_by_id = None
    db.session.commit()
    assert link.by_ai is False
    page = _req(_client(app, owner), 'GET', f'/vraagstuk/{issue.id}/voorstel/{proposal.id}').get_data(as_text=True)
    assert 'Gekoppeld door Voormalig account' in page and '@deleted_user' not in page
    assert 'Gekoppeld door AI' not in page
    assert not ip.can_unlink(link, owner)
    moderator = make_user(role='moderator')
    assert ip.can_unlink(link, moderator)
    _req(_client(app, moderator), 'POST', f'/vraagstuk/{issue.id}/voorstel/{proposal.id}/ontkoppelen/{link.id}')
    assert IssueProposalFact.query.filter_by(id=link.id).count() == 0
    assert ChartBlock.query.filter_by(issue_id=issue.id, kind='link').count() == 0


def test_link_existing_sets_by_ai(app, db, make_user, made, started, watched):
    owner, issue, chart, _props = _published(db, make_user, made)
    proposal = _proposal(db, issue, owner, f'Een nieuw voorstel {uuid.uuid4().hex[:6]}.')
    answer = _answer(links=[{'fact_post_id': chart.post_id, 'proposal_ids': [proposal.id]}])
    from app.models import ChartSelectionRun
    run_row = ChartSelectionRun.query.filter_by(issue_id=issue.id, trigger='proposal').first()
    assert bf.run(run_row.id, client=FakeClient(answer), http=FakeHttp()) == 'done'
    link = IssueProposalFact.query.filter_by(fact_post_id=chart.post_id, proposal_id=proposal.id).one()
    assert link.by_ai is True
