"""Background figures, step 1d (app/background_figures.py): the OWID search,
the AI's choice, the checks, publishing and linking, the memory, behind the
switch BACKGROUND_FIGURES.

Never network, never the real AI: a fake client with fixed JSON and a fake
http with the fixtures in tests/data/owid/.
"""
import json
import os
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
import requests

from app import background_figures as bf, content_jobs, issue_proposals as ip, issues, quality
from app.models import (ChartBlock, ChartSelectionRun, ChartSnapshot, FactChart, Issue, IssueProposalFact,
                        Post, SYSTEM_USERNAME)
from app.sources import SourceError, owid
from app.utils import is_username_valid_format
from tests.test_delete_issue import _cleanup, _clean_session  # noqa: F401  (autouse fixture)
from tests.test_issue_polish import _client, _req, make_user  # noqa: F401  (fixture)

DATA = os.path.join(os.path.dirname(__file__), 'data', 'owid')
SIPRI = 'military-spending-as-a-share-of-gdp-sipri'


def _read(name):
    with open(os.path.join(DATA, name), encoding='utf-8') as f:
        return f.read()


# ── Fakes ────────────────────────────────────────────────────────────────

class FakeHttp:
    """The Search API answers search.json; the grapher answers per slug:
    the SIPRI fixture (also for the made up slugs extra-1 to extra-5),
    regions.csv, two data columns, or 403."""

    def __init__(self, search_status=200, network_on=()):
        self.calls = []
        self.search_status = search_status
        self.network_on = set(network_on)

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params))
        if url == owid.SEARCH_URL:
            return SimpleNamespace(status_code=self.search_status, content=_read('search.json').encode())
        slug = url.split('/grapher/')[1].split('.')[0]
        if slug in self.network_on:
            raise requests.ConnectionError('down')
        if slug == 'military-spending-sipri':
            return SimpleNamespace(status_code=403, content=b'')
        if url.endswith('.metadata.json'):
            body = _read(f'{SIPRI}.metadata.json') if slug != 'regions-chart' else '{"chart": {"title": "Regions"}}'
            return SimpleNamespace(status_code=200, content=body.encode())
        name = {'regions-chart': 'regions.csv', 'two-columns-chart': 'two-columns.csv'}.get(slug, f'{SIPRI}.csv')
        return SimpleNamespace(status_code=200, content=_read(name).encode())


class FakeClient:
    """Answers the search terms call and the selection call with fixed JSON;
    `on_select` runs just before the selection answer (to change the phase)."""

    def __init__(self, selection, terms=('military spending',), fail=None, on_select=None):
        self.selection = selection
        self.terms = list(terms)
        self.fail = fail
        self.on_select = on_select
        self.calls = []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        if self.fail is not None:
            raise self.fail
        if kw['model'] == quality.JUDGE_MODEL:
            text = json.dumps({'terms': self.terms})
        else:
            if self.on_select:
                self.on_select()
            text = self.selection if isinstance(self.selection, str) else json.dumps(self.selection)
        return SimpleNamespace(content=[SimpleNamespace(text=text)])


def _chart(slug=SIPRI, countries=('Poland', 'France', 'Germany', 'Netherlands'), average=None, relevant=(),
           years=10, reason='It shows what each country spends on defence, which several proposals turn on.'):
    return {'slug': slug, 'countries': list(countries), 'average': average, 'years': years, 'reason': reason,
            'relevant_for': list(relevant)}


def _answer(*charts, links=(), none_reason=None):
    return {'charts': list(charts), 'link_existing': list(links), 'none_reason': none_reason}


# ── Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def started(app, monkeypatch):
    """The switch on; the thread start after the commit captured."""
    monkeypatch.setitem(app.config, 'BACKGROUND_FIGURES', True)
    runs = []
    monkeypatch.setattr(bf, '_start', lambda app, ids: runs.extend(ids))
    return runs


@pytest.fixture
def watched(monkeypatch):
    """Records content_jobs.schedule of posts; quality.schedule_judgement must not run."""
    seen = []
    monkeypatch.setattr(content_jobs, 'schedule', lambda obj: seen.append(obj) if isinstance(obj, Post) else None)
    monkeypatch.setattr(quality, 'schedule_judgement', lambda post_id: pytest.fail('judged a background figure'))
    return seen


@pytest.fixture
def made(db):
    """Issues made in a test, purged afterwards (with everything on them)."""
    ids = []
    yield ids
    for issue_id in ids:
        _cleanup(issue_id)


def _issue(db, user, made, title=None, intro='Moeten Polen en Duitsland meer uitgeven aan defensie?'):
    issue = issues.create({'title': title or f'Europese defensie {uuid.uuid4().hex[:8]}', 'intro': intro,
                           'kind': 'online'}, user)
    db.session.commit()
    made.append(issue.id)
    return issue


def _proposal(db, issue, user, text=None):
    proposal = ip.create(issue, user, text or f'De EU bouwt een eigen leger op {uuid.uuid4().hex[:6]}.')
    db.session.commit()
    return proposal


def _run_for(issue):
    return ChartSelectionRun.query.filter_by(issue_id=issue.id).order_by(ChartSelectionRun.id.desc()).first()


def _charts(issue):
    return FactChart.query.filter_by(issue_id=issue.id).order_by(FactChart.id).all()


# ── The switch ───────────────────────────────────────────────────────────

def test_with_the_switch_off_nothing_starts(app, db, make_user, made, monkeypatch):
    assert app.config['BACKGROUND_FIGURES'] is False
    monkeypatch.setattr(bf, '_start', lambda app, ids: pytest.fail('started a round'))
    user = make_user()
    issue = _issue(db, user, made)
    _proposal(db, issue, user)
    assert ChartSelectionRun.query.filter_by(issue_id=issue.id).count() == 0
    old = ChartSelectionRun(issue_id=issue.id, trigger='issue', status='failed', attempts=1,
                            created_at=datetime.utcnow() - timedelta(hours=1))
    db.session.add(old)
    db.session.commit()
    assert bf.backfill(client=FakeClient(_answer()), http=FakeHttp()) == (0, 0)
    assert bf.run(old.id, client=FakeClient(_answer()), http=FakeHttp()) is None
    assert db.session.get(ChartSelectionRun, old.id).attempts == 1


def test_the_system_author_can_never_be_a_member():
    assert not is_username_valid_format(SYSTEM_USERNAME)
    assert len(SYSTEM_USERNAME) <= 30


# ── A round ──────────────────────────────────────────────────────────────

def test_a_round_publishes_a_chart_fact_and_links_it(app, db, make_user, made, started, watched):
    user = make_user()
    issue = _issue(db, user, made)
    assert started == [_run_for(issue).id]
    first = _proposal(db, issue, user)
    second = _proposal(db, issue, user)
    assert len(started) == 3          # one round per new proposal too
    http = FakeHttp()
    client = FakeClient(_answer(_chart(relevant=[first.id, second.id, 999999])))
    run_row = _run_for(issue)
    assert bf.run(run_row.id, client=client, http=http) == 'done'

    (chart,) = _charts(issue)
    post = db.session.get(Post, chart.post_id)
    assert (post.author_username, post.author_name, post.author_role, post.user_id) == (
        SYSTEM_USERNAME, 'Global Union Forum', 'system', None)
    assert post.public_author_name() == 'Global Union Forum'
    assert (post.kind, post.fact_type, post.fact_direction, post.source_lang) == ('feit', 'cijfer', 'neutraal', 'en')
    assert post.fact_kind == 'cijfer' and post.is_system
    assert post.fact_title == 'Military spending as a share of GDP'
    assert post.body.startswith('It shows what each country spends')
    assert post.fact_source == f'https://ourworldindata.org/grapher/{SIPRI}'
    assert post.thread_id == issue.thread_id
    assert chart.slug == SIPRI and chart.entity_keys == ['POL', 'FRA', 'DEU', 'NLD'] and chart.average_key is None
    assert (chart.year_from, chart.year_to) == (2016, 2025)
    snapshot = chart.current_snapshot
    assert snapshot.version == 1 and snapshot.values['years'] == list(range(2016, 2026))
    links = IssueProposalFact.query.filter_by(fact_post_id=post.id).order_by(IssueProposalFact.proposal_id).all()
    assert [(l.proposal_id, l.linked_by_id) for l in links] == [(first.id, None), (second.id, None)]
    assert watched == [post]
    row = db.session.get(ChartSelectionRun, run_row.id)
    assert (row.status, row.attempts) == ('done', 1) and row.finished_at is not None
    assert bf.SELECTION_MODEL in row.model and quality.JUDGE_MODEL in row.model
    assert '[system]' in row.prompt_text and 'Rules:' in row.prompt_text and 'Candidates:' in row.prompt_text
    assert SIPRI in row.response_text
    # The search: the explorer and the multi-dimensional view are no candidates.
    prompt = client.calls[1]['messages'][0]['content']
    assert 'some-explorer' not in prompt and 'some-mdim' not in prompt and SIPRI in prompt
    # Only the relevant entities of a candidate (named, the fixed sets, averages).
    assert 'Burkina Faso' not in prompt and 'Poland' in prompt
    assert client.calls[1]['model'] == 'claude-opus-5-5'


def test_a_round_with_nothing_to_choose(app, db, make_user, made, started, watched):
    issue = _issue(db, make_user(), made, intro='Hoe hervormen we de Veiligheidsraad van de VN?')
    client = FakeClient(_answer(none_reason='No OWID chart measures the reform of the Security Council.'))
    assert bf.run(_run_for(issue).id, client=client, http=FakeHttp()) == 'nothing'
    row = _run_for(issue)
    assert row.status == 'nothing' and 'Security Council' in row.response_text
    assert _charts(issue) == [] and watched == []


# ── The checks ───────────────────────────────────────────────────────────

def test_charts_that_fail_a_check_fall_away(app, db, make_user, made, started, watched):
    issue = _issue(db, make_user(), made)
    answer = _answer(
        _chart(slug='not-a-candidate'),                                   # not among the candidates
        _chart(slug='military-spending-sipri'),                           # 403
        _chart(slug='two-columns-chart'),                                 # unsupported
        _chart(slug='regions-chart', countries=['World', 'Spain']),       # no country left
        _chart(countries=['Atlantis', 'Kosovo', 'Spain', 'Poland'], average='World'))
    run_row = _run_for(issue)
    assert bf.run(run_row.id, client=FakeClient(answer), http=FakeHttp()) == 'done'
    (chart,) = _charts(issue)
    # Atlantis is not in the data; Kosovo and Spain are, but are not allowed (rule 4: the issue names
    # Poland and Germany, the fixed set of Europe fills up); World is not in this CSV: no average.
    assert chart.slug == SIPRI and chart.entity_keys == ['POL'] and chart.average_key is None
    notes = db.session.get(ChartSelectionRun, run_row.id).error
    for expected in ('not-a-candidate: not one of the candidates', 'military-spending-sipri: forbidden',
                     'two-columns-chart: unsupported', "regions-chart: 'World' is not a country of our list",
                     "'Spain' is not in the data", 'regions-chart: no country left',
                     "country 'Atlantis' is not in the data", "average 'World' is not in the data",
                     "'Kosovo' country_not_allowed", "'Spain' country_not_allowed"):
        assert expected in notes, expected


def test_an_average_the_source_supplies(app, db, make_user, made, started, watched):
    issue = _issue(db, make_user(), made)
    answer = _answer(_chart(slug='regions-chart', countries=['Netherlands'], average='World'))
    assert bf.run(_run_for(issue).id, client=FakeClient(answer), http=FakeHttp()) == 'done'
    (chart,) = _charts(issue)
    assert chart.entity_keys == ['NLD'] and chart.average_key == 'OWID_WRL'
    assert (chart.year_from, chart.year_to) == (2013, 2022)


def test_at_most_four_charts_per_issue(app, db, make_user, made, started, watched, monkeypatch):
    issue = _issue(db, make_user(), made)
    slugs = [f'extra-{i}' for i in range(1, 6)]
    hits = [owid.SearchHit(slug=s, title=s, subtitle='', entities=['Poland']) for s in slugs]
    monkeypatch.setattr(owid, 'search', lambda *a, **kw: hits)
    run_row = _run_for(issue)
    assert bf.run(run_row.id, client=FakeClient(_answer(*[_chart(slug=s) for s in slugs])), http=FakeHttp()) == 'done'
    assert [c.slug for c in _charts(issue)] == slugs[:4]
    assert 'extra-5: more than 4 charts' in db.session.get(ChartSelectionRun, run_row.id).error


# ── The memory ───────────────────────────────────────────────────────────

def test_a_removed_chart_does_not_come_back(app, db, make_user, made, started, watched):
    user = make_user()
    issue = _issue(db, user, made)
    assert bf.run(_run_for(issue).id, client=FakeClient(_answer(_chart())), http=FakeHttp()) == 'done'
    (chart,) = _charts(issue)
    # A moderator removes the fact (ORM delete; the chart goes along and is remembered).
    db.session.delete(db.session.get(Post, chart.post_id))
    db.session.commit()
    assert _charts(issue) == []
    assert ChartBlock.query.filter_by(issue_id=issue.id, kind='slug', slug=SIPRI).count() == 1
    _proposal(db, issue, user)
    client = FakeClient(_answer(_chart()))
    assert bf.run(_run_for(issue).id, client=client, http=FakeHttp()) == 'nothing'
    assert SIPRI not in client.calls[1]['messages'][0]['content']
    assert _charts(issue) == []


def test_bulk_removal_is_remembered_too(app, db, make_user, made, started, watched):
    issue = _issue(db, make_user(), made)
    bf.run(_run_for(issue).id, client=FakeClient(_answer(_chart())), http=FakeHttp())
    (chart,) = _charts(issue)
    quality.delete_for_posts([chart.post_id])
    db.session.commit()
    assert ChartBlock.query.filter_by(issue_id=issue.id, kind='slug', slug=SIPRI).count() == 1


def test_an_ai_link_undone_is_not_laid_again(app, db, make_user, made, started, watched):
    proposer, other, moderator = make_user(), make_user(), make_user(role='moderator')
    issue = _issue(db, proposer, made)
    proposal = _proposal(db, issue, proposer)
    run_row = ChartSelectionRun.query.filter_by(issue_id=issue.id, trigger='issue').first()
    bf.run(run_row.id, client=FakeClient(_answer(_chart(relevant=[proposal.id]))), http=FakeHttp())
    (chart,) = _charts(issue)
    link = IssueProposalFact.query.filter_by(fact_post_id=chart.post_id, proposal_id=proposal.id).one()
    assert link.linked_by_id is None
    assert ip.can_unlink(link, proposer) and ip.can_unlink(link, moderator)
    assert not ip.can_unlink(link, other)
    # The proposer undoes it through the route.
    page = _req(_client(app, proposer), 'POST',
                f'/vraagstuk/{issue.id}/voorstel/{proposal.id}/ontkoppelen/{link.id}')
    assert page.status_code in (302, 303)
    assert IssueProposalFact.query.filter_by(fact_post_id=chart.post_id).count() == 0
    assert ChartBlock.query.filter_by(issue_id=issue.id, kind='link', proposal_id=proposal.id,
                                      fact_post_id=chart.post_id).count() == 1
    # A new proposal: the AI wants the link back; it is not laid again.
    _proposal(db, issue, proposer)
    answer = _answer(links=[{'fact_post_id': chart.post_id, 'proposal_ids': [proposal.id]}])
    assert bf.run(_run_for(issue).id, client=FakeClient(answer), http=FakeHttp()) == 'nothing'
    assert IssueProposalFact.query.filter_by(fact_post_id=chart.post_id).count() == 0


def test_a_new_proposal_gets_existing_charts_linked(app, db, make_user, made, started, watched):
    user = make_user()
    issue = _issue(db, user, made)
    bf.run(_run_for(issue).id, client=FakeClient(_answer(_chart())), http=FakeHttp())
    (chart,) = _charts(issue)
    proposal = _proposal(db, issue, user)
    answer = _answer(links=[{'fact_post_id': chart.post_id, 'proposal_ids': [proposal.id]},
                            {'fact_post_id': 999999, 'proposal_ids': [proposal.id]}])
    client = FakeClient(answer)
    assert bf.run(_run_for(issue).id, client=client, http=FakeHttp()) == 'done'
    assert 'link_existing' in client.calls[1]['messages'][0]['content'] or 'existing charts' in \
        client.calls[1]['messages'][0]['content']
    link = IssueProposalFact.query.filter_by(fact_post_id=chart.post_id, proposal_id=proposal.id).one()
    assert link.linked_by_id is None
    assert len(_charts(issue)) == 1


# ── Phases ───────────────────────────────────────────────────────────────

def test_no_round_in_the_voting_phase_or_when_closed(app, db, make_user, made, started):
    user = make_user()
    issue = _issue(db, user, made)
    before = len(started)
    issue.phase = 'stemmen'
    db.session.commit()
    ip.create(issue, user, f'Een voorstel {uuid.uuid4().hex[:6]}.')
    db.session.commit()
    issue.phase, issue.is_closed = 'feiten', True
    db.session.commit()
    ip.create(issue, user, f'Nog een voorstel {uuid.uuid4().hex[:6]}.')
    db.session.commit()
    assert len(started) == before
    assert ChartSelectionRun.query.filter_by(issue_id=issue.id).count() == 1


def test_no_publication_when_the_phase_changes_during_the_round(app, db, make_user, made, started, watched):
    issue = _issue(db, make_user(), made)
    issue_id = issue.id

    def to_voting():
        Issue.query.filter_by(id=issue_id).update({Issue.phase: 'stemmen'})
        db.session.commit()

    run_row = _run_for(issue)
    assert bf.run(run_row.id, client=FakeClient(_answer(_chart()), on_select=to_voting), http=FakeHttp()) == 'nothing'
    assert _charts(issue) == [] and watched == []
    assert 'phase changed' in db.session.get(ChartSelectionRun, run_row.id).error


def test_a_round_whose_issue_left_the_phase_does_nothing(app, db, make_user, made, started):
    issue = _issue(db, make_user(), made)
    issue.phase = 'stemmen'
    db.session.commit()
    client = FakeClient(_answer(_chart()))
    assert bf.run(_run_for(issue).id, client=client, http=FakeHttp()) == 'nothing'
    assert client.calls == []


# ── Failures and the tick ────────────────────────────────────────────────

@pytest.mark.parametrize('client', [FakeClient('this is not json'), FakeClient(_answer(), fail=RuntimeError('overloaded'))])
def test_a_failure_publishes_nothing_and_the_tick_retries_three_times(app, db, make_user, made, started, watched,
                                                                       client):
    issue = _issue(db, make_user(), made)
    run_row = _run_for(issue)
    assert bf.run(run_row.id, client=client, http=FakeHttp()) == 'failed'
    row = db.session.get(ChartSelectionRun, run_row.id)
    assert (row.status, row.attempts) == ('failed', 1) and row.error
    assert _charts(issue) == [] and Post.query.filter_by(thread_id=issue.thread_id, fact_type='cijfer').count() == 0
    assert bf.backfill(client=client, http=FakeHttp()) == (0, 1)
    assert bf.backfill(client=client, http=FakeHttp()) == (0, 1)
    assert db.session.get(ChartSelectionRun, run_row.id).attempts == 3
    assert bf.backfill(client=client, http=FakeHttp()) == (0, 0)       # three attempts: done trying


def test_a_failing_search_fails_the_round(app, db, make_user, made, started, watched):
    issue = _issue(db, make_user(), made)
    assert bf.run(_run_for(issue).id, client=FakeClient(_answer(_chart())), http=FakeHttp(search_status=503)) == 'failed'
    assert 'every search failed' in _run_for(issue).error


def test_the_tick_takes_pending_rounds_after_ten_minutes(app, db, make_user, made, started, watched):
    issue = _issue(db, make_user(), made)
    client = FakeClient(_answer(_chart()))
    assert bf.backfill(client=client, http=FakeHttp()) == (0, 0)       # just made: the thread has it
    run_row = _run_for(issue)
    run_row.created_at = datetime.utcnow() - timedelta(minutes=11)
    db.session.commit()
    assert bf.backfill(client=client, http=FakeHttp()) == (1, 0)
    assert len(_charts(issue)) == 1


def test_one_round_per_issue_at_a_time(app, db, make_user, made, started, watched):
    user = make_user()
    issue = _issue(db, user, made)
    first = _run_for(issue)
    first.status = 'running'
    db.session.commit()
    _proposal(db, issue, user)
    second = _run_for(issue)
    assert bf.run(second.id, client=FakeClient(_answer(_chart())), http=FakeHttp()) is None
    assert db.session.get(ChartSelectionRun, second.id).status == 'pending'


def test_the_tick_route_reports_the_charts(app, db, monkeypatch):
    monkeypatch.setattr(bf, 'backfill', lambda: (2, 1))
    from app import digest
    monkeypatch.setattr(digest, 'tick', lambda: {})
    monkeypatch.setenv('TICK_SECRET', 'geheim')
    result = app.test_client().post('/intern/tik', headers={'Authorization': 'Bearer geheim'}).get_json()
    assert (result['charts'], result['charts_failed']) == (2, 1)


# ── The fact form and editing ────────────────────────────────────────────

def test_nobody_chooses_or_edits_a_chart_fact(app, db, make_user, made, started, monkeypatch):
    monkeypatch.setattr(quality, 'schedule_judgement', lambda post_id: None)   # the member's own fact
    user, moderator = make_user(), make_user(role='moderator')
    issue = _issue(db, user, made)
    bf.run(_run_for(issue).id, client=FakeClient(_answer(_chart())), http=FakeHttp())
    (chart,) = _charts(issue)
    form = {'text': f'Een feit {uuid.uuid4().hex[:6]}.', 'direction': 'voor', 'source': 'https://example.org',
            'type': 'cijfer', 'title': 'Titel'}
    _req(_client(app, user), 'POST', f'/forum/{issue.thread_id}/feiten', data=form)
    mine = Post.query.filter_by(thread_id=issue.thread_id, user_id=user.id, kind='feit').one()
    assert mine.fact_type == 'feit'
    for who in (user, moderator):
        page = _req(_client(app, who), 'POST', f'/forum/feit/{chart.post_id}/bewerken', data=form)
        assert page.status_code == 403
        page = _req(_client(app, who), 'POST', f'/forum/feit/{chart.post_id}/verwijderen')
        assert page.status_code == 403
    assert db.session.get(Post, chart.post_id).body.startswith('It shows')


# ── Purge ────────────────────────────────────────────────────────────────

def test_purge_leaves_nothing_behind(app, db, make_user, made, started, watched):
    user = make_user()
    issue = _issue(db, user, made)
    proposal = _proposal(db, issue, user)
    bf.run(ChartSelectionRun.query.filter_by(issue_id=issue.id, trigger='issue').first().id,
           client=FakeClient(_answer(_chart(relevant=[proposal.id]))), http=FakeHttp())
    (chart,) = _charts(issue)
    bf.block_slug(issue.id, 'something-removed')
    bf.block_link(issue.id, proposal.id, chart.post_id)
    db.session.commit()
    issue_id, chart_id, post_id = issue.id, chart.id, chart.post_id
    assert ChartSelectionRun.query.filter_by(issue_id=issue_id).count() == 2
    _cleanup(issue_id)
    made.remove(issue_id)
    assert FactChart.query.filter_by(issue_id=issue_id).count() == 0
    assert ChartSnapshot.query.filter_by(chart_id=chart_id).count() == 0
    assert ChartSelectionRun.query.filter_by(issue_id=issue_id).count() == 0
    assert ChartBlock.query.filter_by(issue_id=issue_id).count() == 0
    assert db.session.get(Post, post_id) is None


def test_purging_a_proposal_keeps_its_round(app, db, make_user, made, started):
    user = make_user()
    issue = _issue(db, user, made)
    proposal = _proposal(db, issue, user)
    run_id = _run_for(issue).id
    issues.purge_proposal(proposal)
    row = db.session.get(ChartSelectionRun, run_id)
    assert row is not None and row.proposal_id is None


# ── The search ───────────────────────────────────────────────────────────

def test_search_takes_only_charts():
    http = FakeHttp()
    hits = owid.search('military spending', countries=['Netherlands', 'Poland'], hits=7, http=http)
    assert [h.slug for h in hits] == [SIPRI, 'military-spending-sipri', 'two-columns-chart', 'regions-chart']
    assert hits[0].title == 'Military spending as a share of GDP' and 'Poland' in hits[0].entities
    url, params = http.calls[0]
    assert url == 'https://ourworldindata.org/api/search'
    assert params == {'q': 'military spending', 'type': 'charts', 'hitsPerPage': 7, 'countries': 'Netherlands~Poland'}


@pytest.mark.parametrize('status, body, reason', [
    (503, '{}', 'network'), (200, 'not json', 'unsupported'), (200, '{"hits": []}', 'unsupported'),
    (200, '[]', 'unsupported')])
def test_search_failures(status, body, reason):
    class Http:
        def get(self, url, params=None, headers=None, timeout=None):
            return SimpleNamespace(status_code=status, content=body.encode())
    with pytest.raises(SourceError) as err:
        owid.search('x', http=Http())
    assert err.value.reason == reason


def test_search_network_error():
    class Http:
        def get(self, url, params=None, headers=None, timeout=None):
            raise requests.Timeout('slow')
    with pytest.raises(SourceError) as err:
        owid.search('x', http=Http())
    assert err.value.reason == 'network'
