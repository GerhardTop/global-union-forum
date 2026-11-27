"""The issue page (also with the proposals enlarged) and the proposal page do a
fixed number of database queries, however many proposals the issue has (no
N+1): each proposal with reactions, support, votes and a linked fact. Counted
at 1, 5 and 20 proposals, for a visitor and for a member, in the voting phase,
each page with a fresh database session as in production.

Before the fix: one query per proposal for its versions on every page (lazy
version_rows), and on the proposal page for a member two (the read mark's
commit expired the rail's proposals, which were then reloaded one by one).

Shared conftest app (CSRF off).
"""
import re
from collections import Counter
from contextlib import contextmanager

import pytest
from sqlalchemy import event

from app import db as _db
from app import issue_proposals as ip
from app.models import IssueProposalFact, IssueProposalSupport, IssueProposalVote, Post
from tests.test_delete_issue import _clean_session, _cleanup  # noqa: F401  (autouse fixture)
from tests.test_issue_polish import _client, _fact, _issue, _req, make_user  # noqa: F401  (fixture)

SIZES = (1, 5, 20)


def PAGES(issue, proposals):
    return (('issue', f'/vraagstuk/{issue.id}'),
            ('issue enlarged', f'/vraagstuk/{issue.id}?vergroot=voorstellen'),
            ('proposal', f'/vraagstuk/{issue.id}/voorstel/{proposals[0].id}'))


@contextmanager
def _count_queries():
    statements = []

    def _listener(conn, cursor, statement, params, context, executemany):
        statements.append(re.sub(r'\s+', ' ', statement))

    event.listen(_db.engine, 'before_cursor_execute', _listener)
    try:
        yield statements
    finally:
        event.remove(_db.engine, 'before_cursor_execute', _listener)


def _shape(statement):
    """A statement without its literal values, to group repeats."""
    return re.sub(r"\b\d+\b|'[^']*'|\(\?(, \?)*\)|IN \(__\[POSTCOMPILE_[a-z_0-9]+\]\)", '?', statement)[:160]


def _chart_facts(issue, proposals):
    """Background figures (step 1e): two chart facts on the issue from the
    OWID fixture, one with an average, and (step 3c) one from the Eurostat
    fixture, all linked to the first proposals by the AI. Published the way a
    selection round does, without AI or network."""
    import json
    import os
    from app import background_figures as bf
    from app.sources import owid
    folder = os.path.join(os.path.dirname(__file__), 'data', 'owid')
    slug = 'military-spending-as-a-share-of-gdp-sipri'
    with open(os.path.join(folder, f'{slug}.csv'), encoding='utf-8') as f:
        csv_text = f.read()
    with open(os.path.join(folder, f'{slug}.metadata.json'), encoding='utf-8') as f:
        metadata = json.load(f)
    accepted = []
    for i, average in enumerate((None, 'OWID_EU27')):
        parsed = owid.parse(csv_text, metadata, slug=slug)
        if average:
            parsed.series[average] = dict(parsed.series['ESP'])
            parsed.names[average] = 'European Union (27)'
        accepted.append(bf.Accepted(slug=f'{slug}-{i}', parsed=parsed, metadata=metadata,
                                    keys=['POL', 'FRA', 'DEU', 'NLD'], average_key=average, year_from=2016,
                                    year_to=2025, reason=f'Reason {i}.', proposal_ids=[p.id for p in proposals[:2]]))
    # Step 3c: a Eurostat chart too, with a selection and flags (short labels in the snapshot).
    from app.sources import eurostat
    with open(os.path.join(os.path.dirname(__file__), 'data', 'eurostat', 'tps00203_pc_act.json'),
              encoding='utf-8') as f:
        data = json.load(f)
    parsed = eurostat.parse(data, 'tps00203')
    parsed.labels = dict(parsed.labels, selection='Total')   # as for a dimension other than the unit
    accepted.append(bf.Accepted(slug='tps00203', parsed=parsed, metadata=eurostat.metadata_of(data),
                                keys=['POL', 'FRA', 'DEU'], average_key='eurostat:EU27_2020', year_from=2016,
                                year_to=2025, reason='Reason eurostat.', proposal_ids=[p.id for p in proposals[:2]],
                                source='eurostat', filters={'unit': 'PC_ACT'}))
    bf.publish(bf.Context(issue=issue), bf.Outcome(status='done', accepted=accepted))


def _world(db, make_user, n, charts=False):
    """An issue in the voting phase with n proposals; each has 2 reactions, 2
    supports (so it is on the list), 2 votes and 1 linked fact. With charts:
    also two chart facts linked to the first proposals."""
    owner, a, b = make_user(), make_user(), make_user()
    issue = _issue(db, owner, f'Een vraagstuk met {n} voorstellen')
    facts = [_fact(db, issue, owner, body=f'Feit {i}.') for i in range(n)]
    for i in range(n):
        p = ip.create(issue, owner, f'Stelling {i}.', f'Toelichting {i}.', lang='nl')
        db.session.flush()
        for j, who in enumerate((a, b)):
            db.session.add(Post(thread_id=issue.thread_id, user_id=who.id, author_name='',
                                author_username=who.username, body=f'Reactie {i}.{j}', source_lang='nl',
                                issue_proposal_id=p.id, proposal_version=1))
            db.session.add(IssueProposalSupport(proposal_id=p.id, user_id=who.id))
            db.session.add(IssueProposalVote(proposal_id=p.id, user_id=who.id, choice=('voor', 'tegen')[j]))
        db.session.add(IssueProposalFact(proposal_id=p.id, fact_post_id=facts[i].id, linked_by_id=owner.id))
    if charts:
        db.session.flush()
        _chart_facts(issue, ip.regular_query(issue).order_by('id').all())
    issue.phase = 'stemmen'
    db.session.commit()
    return issue, ip.regular_query(issue).order_by('id').all(), a


def _measure(app, db, make_user, n, who, charts=False):
    issue, proposals, member = _world(db, make_user, n, charts)
    user = member if who == 'member' else None
    issue_id, pages = issue.id, PAGES(issue, proposals)   # before the session is emptied below
    if charts:
        pages += (('issue facts enlarged', f'/vraagstuk/{issue.id}?vergroot=feiten'),)
    clients = {page: _client(app, user, 'nl') for page, _path in pages}
    out = {}
    for page, path in pages:
        client = clients[page]
        _req(client, 'GET', path)                       # warm up (first-visit writes, caches)
        # A fresh session, as in production (one per request): in the tests the
        # session outlives the request, so objects from the warm-up would hide
        # lazy loads.
        db.session.commit()
        db.session.expunge_all()
        with _count_queries() as queries:
            resp = _req(client, 'GET', path)
        assert resp.status_code == 200, (page, resp.status_code)
        if charts:
            # The charts show (the column and the enlarged facts are both in the page), Eurostat's too.
            assert resp.get_data(as_text=True).count('class="gu-chart gu-chart--small"') >= 3, page
        out[page] = queries
    return issue_id, out


@pytest.mark.parametrize('who', ['visitor', 'member'])
def test_queries_do_not_grow_with_the_number_of_proposals(app, db, make_user, who):
    counts, shapes, made = {}, {}, []
    try:
        for n in SIZES:
            issue_id, out = _measure(app, db, make_user, n, who)
            made.append(issue_id)
            counts[n] = {page: len(q) for page, q in out.items()}
            shapes[n] = {page: Counter(_shape(s) for s in q) for page, q in out.items()}
    finally:
        for issue_id in made:
            _cleanup(issue_id)
    repeated = {page: [(s, c) for s, c in shapes[20][page].most_common(8) if c > shapes[1][page][s]]
                for page in counts[1]}
    # The statements that grew show where an N+1 crept in.
    assert counts[1] == counts[5] == counts[20], (counts, repeated)


CHART_COUNTS = {}   # filled by the test below, for the report


@pytest.mark.parametrize('who', ['visitor', 'member'])
def test_queries_with_chart_facts_do_not_grow(app, db, make_user, who):
    """Background figures (step 1e): with two chart facts on the issue, linked
    to proposals, the pages still do a fixed number of queries: the charts and
    snapshots of all facts are loaded together (background_figures.charts_for)."""
    counts, shapes, made = {}, {}, []
    try:
        for n in SIZES:
            issue_id, out = _measure(app, db, make_user, n, who, charts=True)
            made.append(issue_id)
            counts[n] = {page: len(q) for page, q in out.items()}
            shapes[n] = {page: Counter(_shape(s) for s in q) for page, q in out.items()}
    finally:
        for issue_id in made:
            _cleanup(issue_id)
    CHART_COUNTS[who] = counts
    print(f'\nchart query counts ({who}):', counts)
    repeated = {page: [(s, c) for s, c in shapes[20][page].most_common(8) if c > shapes[1][page][s]]
                for page in counts[1]}
    assert counts[1] == counts[5] == counts[20], (counts, repeated)


# Representativeness 1c: the extra queries of the proposal page when the card
# after the first vote is due (the session flag): the answers row (read),
# making it with asked_at (insert, in the page's existing commit), and the
# answers again for the questions (the commit expired them).
PARTICIPANT_CARD_QUERIES = 3


def test_queries_with_the_participant_card(app, db, make_user, monkeypatch):
    """Without the flag the proposal page does exactly the queries it did
    before step 1c, with the switch off and on; with the flag a fixed number
    more (PARTICIPANT_CARD_QUERIES), and the card shows."""
    issue, proposals, member = _world(db, make_user, 5)
    member.set_role('voter')
    issue.support_threshold = 1      # on the voting list: the vote block with the buttons
    db.session.commit()
    issue_id, path, pid, member_id = issue.id, PAGES(issue, proposals)[2][1], proposals[0].id, member.id

    def measure(flag):
        from app.models import User
        client = _client(app, db.session.get(User, member_id), 'nl')
        _req(client, 'GET', path)
        db.session.commit()
        db.session.expunge_all()
        if flag:
            with client.session_transaction() as s:
                s['pp_ask'] = pid
        with _count_queries() as queries:
            resp = _req(client, 'GET', path)
        assert resp.status_code == 200
        return len(queries), resp.get_data(as_text=True)

    try:
        off, page = measure(False)
        assert 'gu-ppcard' not in page
        monkeypatch.setitem(app.config, 'PARTICIPANT_PROFILE', True)
        on, page = measure(False)
        assert 'gu-ppcard' not in page
        with_flag, page = measure(True)
        assert 'class="gu-ppcard"' in page
        print(f'\nproposal page queries: switch off {off}, on {on}, with the card {with_flag}')
        assert on == off
        assert with_flag == off + PARTICIPANT_CARD_QUERIES
    finally:
        _cleanup(issue_id)


# Representativeness 1d: with the switch on, the issue page reads the frozen
# row of "Who voted" with one query, and the proposal page after voting one
# (the line under the result), at 1 and at 5 proposals. (In the follow-up
# phase these pages already grow with the number of proposals, switch off
# too; that is older than this step and measured here only as the base.)
PARTICIPATION_QUERIES = 1


@pytest.mark.parametrize('who', ['visitor', 'member'])
def test_queries_with_who_voted(app, db, make_user, monkeypatch, who):
    import json
    from app.models import IssueParticipationStats
    counts, made = {}, []
    try:
        for n in (1, 5):
            issue, proposals, member = _world(db, make_user, n)
            issue.phase, issue.support_threshold = 'vervolg', 1   # voting is over: the result and its line
            db.session.add(IssueParticipationStats(issue_id=issue.id, voters=40, answered=20, age_level='coarse',
                                                   counts=json.dumps({'age': [['17_44', 20], ['45_plus', 10], ['na', 10]],
                                                                      'gender': [['na', 40]], 'education': [['na', 40]],
                                                                      'country': [['na', 40]]})))
            db.session.commit()
            made.append(issue.id)
            user_id = member.id if who == 'member' else None
            pages = PAGES(issue, proposals)
            for switch in (False, True):
                monkeypatch.setitem(app.config, 'PARTICIPANT_PROFILE', switch)
                for page, path in pages:
                    from app.models import User
                    client = _client(app, db.session.get(User, user_id) if user_id else None, 'nl')
                    _req(client, 'GET', path)
                    db.session.commit()
                    db.session.expunge_all()
                    with _count_queries() as queries:
                        resp = _req(client, 'GET', path)
                    html = resp.get_data(as_text=True)
                    assert resp.status_code == 200
                    shown = 'id="wie-stemde-mee"' in html if page != 'proposal' else 'deelnemers in dit vraagstuk' in html
                    assert shown == switch, (page, switch)
                    counts[(n, page, switch)] = len(queries)
    finally:
        for issue_id in made:
            _cleanup(issue_id)
    print(f'\nwho voted query counts ({who}):', counts)
    for n in (1, 5):
        for page in ('issue', 'issue enlarged', 'proposal'):
            assert counts[(n, page, True)] == counts[(n, page, False)] + PARTICIPATION_QUERIES, (n, page, counts)


# After voting (the follow-up phase) every proposal shows its result. The
# votes of all proposals on the page are counted in one query
# (issue_proposals.tallies), so the pages do a fixed number of queries.
AFTER_VOTING_SIZES = (2, 6)
AFTER_VOTING_COUNTS = {}   # filled by the test below, for the report
# The fixed counts per page (before: 2 more per proposal, 40 and 48 on the
# issue page for a visitor at 2 and 6 proposals).
AFTER_VOTING_EXPECTED = {
    'visitor': {'issue': 35, 'issue enlarged': 35, 'proposal': 40, 'proposal without votes': 38},
    'member': {'issue': 49, 'issue enlarged': 49, 'proposal': 55, 'proposal without votes': 53},
}


def _after_voting_world(db, make_user, n):
    """n proposals with two votes each, plus one on the voting list without any
    vote (its result: no valid votes), in the follow-up phase."""
    issue, proposals, member = _world(db, make_user, n)
    empty = ip.create(issue, issue.created_by, 'Stelling zonder stemmen.', 'Toelichting.', lang='nl')
    db.session.flush()
    db.session.add(IssueProposalSupport(proposal_id=empty.id, user_id=member.id))
    issue.phase, issue.support_threshold = 'vervolg', 1
    db.session.commit()
    return issue, proposals, member, empty


@pytest.mark.parametrize('who', ['visitor', 'member'])
def test_queries_after_voting_do_not_grow(app, db, make_user, who):
    from app.models import User
    counts, made = {}, []
    try:
        for n in AFTER_VOTING_SIZES:
            issue, proposals, member, empty = _after_voting_world(db, make_user, n)
            made.append(issue.id)
            user_id = member.id if who == 'member' else None
            pages = PAGES(issue, proposals) + (('proposal without votes', f'/vraagstuk/{issue.id}/voorstel/{empty.id}'),)
            for page, path in pages:
                client = _client(app, db.session.get(User, user_id) if user_id else None, 'nl')
                _req(client, 'GET', path)
                db.session.commit()
                db.session.expunge_all()
                with _count_queries() as queries:
                    resp = _req(client, 'GET', path)
                html = resp.get_data(as_text=True)
                assert resp.status_code == 200
                # The results are right: the proposals with votes 50% for, the empty one without valid votes.
                assert 'Verworpen, geen geldige stemmen' in html or page == 'proposal', page
                if page == 'proposal without votes':
                    assert 'Er zijn geen geldige stemmen uitgebracht.' in html
                if page == 'proposal':
                    assert '50% voor, 50% tegen (van de geldige stemmen)' in html
                counts[(n, page)] = len(queries)
    finally:
        for issue_id in made:
            _cleanup(issue_id)
    AFTER_VOTING_COUNTS[who] = counts
    print(f'\nafter voting query counts ({who}):', counts)
    for page in ('issue', 'issue enlarged', 'proposal', 'proposal without votes'):
        assert counts[(2, page)] == counts[(6, page)], (page, counts)
        assert counts[(2, page)] == AFTER_VOTING_EXPECTED[who][page], (page, counts)
