"""Background figures, step 3b: Eurostat in the selection, behind
EUROSTAT_FIGURES. Candidates from both sources (ids "owid:<slug>" and
"eurostat:<code>"), the choice per dimension (one extra AI call), the checks
for a Eurostat chart (source_scope, the EU-27 as the only average), publishing
with source and filters, and the yearly datasets in the catalogue.

Never network, never the real AI: a fake client, and a fake http that answers
from tests/data/owid/ and tests/data/eurostat/.
"""
import copy
import json
import os
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest

from app import background_figures as bf, quality
from app.models import ChartSelectionRun, EurostatCatalog, OwidRejectedSlug, Post
from app.sources import eurostat, owid
from tests.test_background_figures import (SIPRI, FakeHttp, _charts, _issue, _run_for,  # noqa: F401
                                           made, started, watched)
from tests.test_delete_issue import _clean_session  # noqa: F401  (autouse fixture)
from tests.test_eurostat_source import FakeHttp as TocHttp
from tests.test_issue_polish import make_user  # noqa: F401  (fixture)

EUROSTAT_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'eurostat')
NOW = datetime(2026, 11, 27, 12, 0)


def _eurostat_json(name):
    with open(os.path.join(EUROSTAT_DATA, name), encoding='utf-8') as f:
        return json.load(f)


class BothHttp(FakeHttp):
    """OWID as in step 1d; Eurostat's data endpoint from the fixtures:
    tps00203 without a unit filter has three units, with unit=PC_ACT one.
    bodies: {code: JSON-stat} to answer instead of the fixture."""

    def __init__(self, bodies=None, **kw):
        super().__init__(**kw)
        self.bodies = bodies or {}
        self.eurostat_calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        if url.startswith(eurostat.DATA_URL):
            code = url.rsplit('/', 1)[1]
            self.eurostat_calls.append((code, list(params or [])))
            filters = {k: v for k, v in (params or []) if k not in ('format', 'lang', 'sinceTimePeriod')}
            if code in self.bodies:
                data = self.bodies[code]
            elif code == 'tps00203' and filters.get('unit') == 'PC_ACT':
                data = _eurostat_json('tps00203_pc_act.json')
            else:
                data = _eurostat_json(f'{code}.json')
            return SimpleNamespace(status_code=200, content=json.dumps(data).encode())
        return super().get(url, params=params, headers=headers, timeout=timeout)


class Client:
    """Search terms, then the selection answers in order, and the answer of
    the choice per dimension (its own system prompt)."""

    def __init__(self, *selections, dimensions=None, terms=('unemployment rate', 'housing cost overburden')):
        self.selections = list(selections)
        self.terms = list(terms)
        self.dimensions = dimensions
        self.calls = []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        if kw['model'] == quality.JUDGE_MODEL:
            text = json.dumps({'terms': self.terms})
        elif kw['system'] == bf._DIMENSION_SYSTEM:
            text = json.dumps(self.dimensions)
        else:
            text = json.dumps(self.selections.pop(0))
        return SimpleNamespace(content=[SimpleNamespace(text=text)])

    def prompt(self, system):
        return [c['messages'][0]['content'] for c in self.calls if c['system'] == system]


@pytest.fixture
def both(app, monkeypatch, started):
    """BACKGROUND_FIGURES (via started) and EUROSTAT_FIGURES on; the OWID
    search finds the SIPRI chart; the catalogue holds the fixture tables."""
    monkeypatch.setitem(app.config, 'EUROSTAT_FIGURES', True)
    hits = [owid.SearchHit(slug=SIPRI, title='Military spending as a share of GDP', subtitle='',
                           entities=['Poland', 'Germany', 'France', 'Netherlands'])]
    monkeypatch.setattr(owid, 'search', lambda *a, **kw: hits)
    return started


@pytest.fixture
def catalog(db):
    """The catalogue is shared: the fixture tables, cleared afterwards."""
    EurostatCatalog.query.delete()
    for code, title, kind, start, end in (
            ('tps00203', 'Unemployment rate', 'table', '2014', '2025'),
            ('tespm140', 'Housing cost overburden rate', 'table', '2014', '2025'),
            ('ei_hppi_q', 'House price index (2025 = 100) - quarterly data', 'table', '2005-Q1', '2026-Q2'),
            ('ilc_lvho07a', 'Housing cost overburden rate by age, sex and poverty status', 'dataset', '2003', '2025')):
        db.session.add(EurostatCatalog(code=code, title=title, type=kind, data_start=start, data_end=end,
                                       refreshed_at=NOW))
    db.session.commit()
    yield
    EurostatCatalog.query.delete()
    db.session.commit()


def _eurostat_chart(code='tps00203', countries=('Poland', 'Germany'), average='European Union (27)', years=10,
                    compare='countries'):
    return {'id': f'eurostat:{code}', 'compare': compare, 'countries': list(countries), 'average': average,
            'years': years, 'reason': 'It shows the share of people without work in each country.',
            'relevant_for': []}


def _answer(*charts):
    return {'charts': list(charts), 'link_existing': [], 'none_reason': None}


# ── The switch ───────────────────────────────────────────────────────────

def test_switch_off_only_owid(app, db, make_user, made, started, watched, catalog, monkeypatch):
    hits = [owid.SearchHit(slug=SIPRI, title='Military spending', subtitle='', entities=['Poland'])]
    monkeypatch.setattr(owid, 'search', lambda *a, **kw: hits)
    monkeypatch.setattr(eurostat, 'search_catalog', lambda *a, **kw: pytest.fail('searched Eurostat'))
    assert app.config['EUROSTAT_FIGURES'] is False
    issue = _issue(db, make_user(), made)
    client = Client(_answer())
    bf.run(_run_for(issue).id, client=client, http=BothHttp())
    (prompt,) = client.prompt(bf._SELECTION_SYSTEM)
    assert f'- slug: {SIPRI}' in prompt and 'eurostat:' not in prompt and 'owid:' not in prompt


# ── Candidates ───────────────────────────────────────────────────────────

def test_candidates_from_both_sources(app, db, make_user, made, both, watched, catalog):
    issue = _issue(db, make_user(), made)
    client = Client(_answer())
    bf.run(_run_for(issue).id, client=client, http=BothHttp())
    (prompt,) = client.prompt(bf._SELECTION_SYSTEM_BOTH)
    assert f'- id: owid:{SIPRI}\n  source: Our World in Data' in prompt
    assert '- id: eurostat:tps00203\n  source: Eurostat (main table)\n  title: Unemployment rate\n  period: 2014 to 2025' \
        in prompt
    assert '- id: eurostat:ilc_lvho07a\n  source: Eurostat (dataset)' in prompt
    # Tables before datasets; the quarterly table is a candidate too (the checks drop it).
    assert prompt.index('eurostat:tespm140') < prompt.index('eurostat:ilc_lvho07a')
    assert 'Candidates come from two sources.' in bf._SELECTION_SYSTEM_BOTH
    assert '{"charts": [{"id": "...",' in bf._SELECTION_SYSTEM_BOTH
    assert 'Candidates come from two sources.' not in bf._SELECTION_SYSTEM


def test_a_rejected_eurostat_id_does_not_come_back(app, db, make_user, made, both, watched, catalog):
    issue = _issue(db, make_user(), made)
    http = BothHttp()
    # Quarterly only: unsupported, so the one retry of step 1j follows (it chooses nothing).
    terms = ('house price index', 'unemployment rate')
    client = Client(_answer(_eurostat_chart('ei_hppi_q', average=None)), _answer(), terms=terms)
    assert bf.run(_run_for(issue).id, client=client, http=http) == 'nothing'
    retry = client.prompt(bf._SELECTION_SYSTEM_BOTH)[1]
    assert retry.endswith('These charts could not be used because they show several measures: eurostat:ei_hppi_q. '
                          'Choose again, or choose none.')
    assert {r.slug: r.reason for r in OwidRejectedSlug.query} == {'eurostat:ei_hppi_q': 'unsupported'}
    other = _issue(db, make_user(), made, title=f'Nog een vraagstuk {uuid.uuid4().hex[:6]}')
    client = Client(_answer(), terms=terms)
    bf.run(_run_for(other).id, client=client, http=http)
    (prompt,) = client.prompt(bf._SELECTION_SYSTEM_BOTH)
    assert 'eurostat:ei_hppi_q' not in prompt and 'eurostat:tps00203' in prompt


# ── The choice per dimension and publishing ──────────────────────────────

def test_a_eurostat_chart_with_a_dimension_choice_publishes(app, db, make_user, made, both, watched, catalog):
    issue = _issue(db, make_user(), made)
    client = Client(_answer(_eurostat_chart()), dimensions={'eurostat:tps00203': {'unit': 'PC_ACT'}})
    http = BothHttp()
    run_row = _run_for(issue)
    assert bf.run(run_row.id, client=client, http=http) == 'done'
    # First without filters, then with the chosen unit.
    first, second = http.eurostat_calls
    assert first[0] == second[0] == 'tps00203' and 'unit' not in dict(first[1])
    assert ('unit', 'PC_ACT') in second[1] and dict(second[1])['sinceTimePeriod'] == dict(first[1])['sinceTimePeriod']
    (dimension_prompt,) = client.prompt(bf._DIMENSION_SYSTEM)
    assert 'Table eurostat:tps00203: Unemployment rate' in dimension_prompt
    assert ('- dimension unit (Unit of measure): PC_ACT = Percentage of population in the labour force; '
            'PC_POP = Percentage of total population; THS_PER = Thousand persons') in dimension_prompt
    assert '- dimension age' not in dimension_prompt and '- dimension sex' not in dimension_prompt  # one value
    (chart,) = _charts(issue)
    assert (chart.source, chart.slug, chart.column_name) == ('eurostat', 'tps00203', 'value')
    assert json.loads(chart.filters) == {'unit': 'PC_ACT'}
    assert chart.entity_keys == ['POL', 'DEU'] and chart.average_key == 'eurostat:EU27_2020'
    assert chart.average_name == 'European Union - 27 countries (from 2020)'
    post = db.session.get(Post, chart.post_id)
    assert post.fact_source == 'https://ec.europa.eu/eurostat/databrowser/view/tps00203/default/table'
    snapshot = chart.current_snapshot
    assert snapshot.citation.startswith('Eurostat (tps00203), last update') and snapshot.license == 'CC BY 4.0'
    assert 'flags' in snapshot.values and snapshot.unit == 'Percentage of population in the labour force'
    row = db.session.get(ChartSelectionRun, run_row.id)
    assert row.prompt_text.count('[system]') == 3 and row.prompt_text.count('========') == 2
    assert row.response_text.count('========') == 2 and '"PC_ACT"' in row.response_text


@pytest.mark.parametrize('choice, problem', [
    ({'unit': 'XYZ'}, "'XYZ' is not in the list of unit"),
    ({}, 'no code for unit'),
    ({'unit': 'PC_ACT', 'colour': 'RED'}, "'colour' is no dimension of this table"),
    (None, 'no choice for this table'),
])
def test_an_invalid_dimension_choice_falls_away(app, db, make_user, made, both, watched, catalog, choice, problem):
    issue = _issue(db, make_user(), made)
    dimensions = {'eurostat:tps00203': choice} if choice is not None else {}
    http = BothHttp()
    assert bf.run(_run_for(issue).id, client=Client(_answer(_eurostat_chart()), dimensions=dimensions),
                  http=http) == 'nothing'
    assert f'eurostat:tps00203: dimension_choice ({problem})' in _run_for(issue).error
    assert len(http.eurostat_calls) == 1                 # no second fetch
    assert OwidRejectedSlug.query.count() == 0           # not a lasting reason


def test_still_several_values_after_filtering_falls_away_and_is_remembered(app, db, make_user, made, both, watched,
                                                                          catalog):
    issue = _issue(db, make_user(), made)
    # The source ignores the filter: three units again.
    http = BothHttp(bodies={'tps00203': _eurostat_json('tps00203.json')})
    assert bf.run(_run_for(issue).id, client=Client(_answer(_eurostat_chart()),
                                                    dimensions={'eurostat:tps00203': {'unit': 'PC_ACT'}}),
                  http=http) == 'nothing'
    assert 'eurostat:tps00203: needs_choice' in _run_for(issue).error
    assert {r.slug: r.reason for r in OwidRejectedSlug.query} == {'eurostat:tps00203': 'needs_choice'}


def test_no_choice_needed_means_no_extra_call(app, db, make_user, made, both, watched, catalog):
    issue = _issue(db, make_user(), made)
    client = Client(_answer(_eurostat_chart('tespm140')))
    assert bf.run(_run_for(issue).id, client=client, http=BothHttp()) == 'done'
    assert client.prompt(bf._DIMENSION_SYSTEM) == []
    (chart,) = _charts(issue)
    assert json.loads(chart.filters) == {} and chart.average_key == 'eurostat:EU27_2020'


# ── The checks for a Eurostat chart ──────────────────────────────────────

def test_a_eurostat_chart_for_the_world_falls_away(app, db, make_user, made, both, watched, catalog):
    issue = _issue(db, make_user(), made, intro='Hoe verlagen we de werkloosheid wereldwijd?')
    http = BothHttp()
    assert bf.run(_run_for(issue).id, client=Client(_answer(_eurostat_chart(countries=('United States',)))),
                  http=http) == 'nothing'
    assert 'eurostat:tps00203: source_scope' in _run_for(issue).error
    assert http.eurostat_calls == []                     # no fetch for a chart that cannot be used


def test_a_eurostat_chart_with_blocks_falls_away(app, db, make_user, made, both, watched, catalog):
    issue = _issue(db, make_user(), made)
    answer = _answer(_eurostat_chart(compare='blocks', countries=('European Union (27)',), average=None))
    assert bf.run(_run_for(issue).id, client=Client(answer), http=BothHttp()) == 'nothing'
    assert 'eurostat:tps00203: source_scope' in _run_for(issue).error


def test_the_eu_average_falls_away_when_it_is_a_total(app, db, make_user, made, both, watched, catalog):
    data = _eurostat_json('tespm140.json')
    total = copy.deepcopy(data)
    # The EU is the first geo: positions 0 to 9 (one value per year) times 50.
    for pos in range(10):
        if str(pos) in total['value']:
            total['value'][str(pos)] *= 50
    issue = _issue(db, make_user(), made)
    assert bf.run(_run_for(issue).id, client=Client(_answer(_eurostat_chart('tespm140'))),
                  http=BothHttp(bodies={'tespm140': total})) == 'done'
    (chart,) = _charts(issue)
    assert chart.average_key is None and chart.average_name is None
    assert "average 'European Union (27)' aggregate_is_total" in _run_for(issue).error


def test_only_the_eu27_is_an_average_for_eurostat(app, db, make_user, made, both, watched, catalog):
    issue = _issue(db, make_user(), made)
    assert bf.run(_run_for(issue).id, client=Client(_answer(_eurostat_chart('tespm140', average='Spain'))),
                  http=BothHttp()) == 'done'
    (chart,) = _charts(issue)
    assert chart.average_key is None
    assert "average 'Spain' is not the EU-27 of Eurostat" in _run_for(issue).error


def test_an_owid_and_a_eurostat_chart_in_one_round(app, db, make_user, made, both, watched, catalog):
    issue = _issue(db, make_user(), made)
    owid_chart = {'id': f'owid:{SIPRI}', 'compare': 'countries', 'countries': ['Poland', 'Germany'], 'average': None,
                  'years': 10, 'reason': 'It shows what each country spends on defence.', 'relevant_for': []}
    assert bf.run(_run_for(issue).id, client=Client(_answer(owid_chart, _eurostat_chart('tespm140'))),
                  http=BothHttp()) == 'done'
    assert {(c.source, c.slug) for c in _charts(issue)} == {('owid', SIPRI), ('eurostat', 'tespm140')}
    assert next(c for c in _charts(issue) if c.source == 'owid').filters is None


# ── The catalogue ────────────────────────────────────────────────────────

def test_yearly_datasets_in_the_catalogue_quarterly_not(app, db):
    tables = eurostat.parse_toc(TocHttp().get(eurostat.TOC_URL).content.decode())
    kinds = {t['code']: t['type'] for t in tables}
    assert kinds['ilc_lvho07a'] == 'dataset' and kinds['tespm140'] == 'table'
    assert 'ei_bsin_q_r2' not in kinds and 'ei_bssi_m_r2' not in kinds     # quarterly and monthly datasets
    assert kinds['ei_hppi_q'] == 'table'                                      # a quarterly main table stays


def test_tables_before_datasets_when_searching(app, db, catalog):
    hits = eurostat.search_catalog('housing cost overburden rate by age sex poverty status', now=NOW)
    # The dataset hits every word, the tables fewer; the tables still come first.
    assert [h.code for h in hits] == ['tespm140', 'tps00203', 'ilc_lvho07a']
    assert [h.type for h in hits] == ['table', 'table', 'dataset']
    assert [h.code for h in eurostat.search_catalog('housing cost overburden', limit=1, now=NOW)] == ['tespm140']
