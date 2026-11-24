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


def _world(db, make_user, n):
    """An issue in the voting phase with n proposals; each has 2 reactions, 2
    supports (so it is on the list), 2 votes and 1 linked fact."""
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
    issue.phase = 'stemmen'
    db.session.commit()
    return issue, ip.regular_query(issue).order_by('id').all(), a


def _measure(app, db, make_user, n, who):
    issue, proposals, member = _world(db, make_user, n)
    user = member if who == 'member' else None
    issue_id, pages = issue.id, PAGES(issue, proposals)   # before the session is emptied below
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
