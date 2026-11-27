"""Background figures, step 3a: the Eurostat adapter (app/sources/eurostat.py):
the catalogue of main tables, fetching JSON-stat and reading it into a
ParsedChart, and the snapshot with Eurostat's flags.

Never network: a fake http answers from tests/data/eurostat/ (fetched once
live on 26 November 2026, see the README there).
"""
import json
import os
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
import requests

from app import background_figures as bf
from app.models import EurostatCatalog
from app.sources import SourceError, countries, eurostat, owid

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'eurostat')
NOW = datetime(2026, 11, 26, 12, 0)


def _read(name):
    with open(os.path.join(DATA, name), encoding='utf-8') as f:
        return f.read()


def _json(name):
    return json.loads(_read(name))


class FakeHttp:
    """The table of contents from toc.txt, a table from <code>.json; or a
    status, an exception or a body of our choice."""

    def __init__(self, status=200, body=None, raises=None, toc=None):
        self.calls = []
        self.status, self.body, self.raises, self.toc = status, body, raises, toc

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params, headers))
        if self.raises is not None:
            raise self.raises
        if url == eurostat.TOC_URL:
            content = (self.toc if self.toc is not None else _read('toc.txt')).encode()
        elif self.body is not None:
            content = self.body
        else:
            content = _read(f'{url.rsplit("/", 1)[1].lower()}.json').encode()
        return SimpleNamespace(status_code=self.status, content=content)


@pytest.fixture
def catalog(db):
    """The catalogue is shared: start and end empty."""
    EurostatCatalog.query.delete()
    db.session.commit()
    yield
    EurostatCatalog.query.delete()
    db.session.commit()


# ── The catalogue ────────────────────────────────────────────────────────

def test_the_table_of_contents_keeps_only_main_tables():
    tables = eurostat.parse_toc(_read('toc.txt'))
    codes = [t['code'] for t in tables]
    assert len(codes) == len(set(codes))                      # tqoe3b4 is in two folders, once here
    assert 'tqoe3b4' in codes and 'ei_bssi_m_r2' not in codes and 'data' not in codes  # a dataset, a folder
    assert {'tespm140', 'tps00203', 'ei_hppi_q', 'tipsun20'} <= set(codes)
    row = next(t for t in tables if t['code'] == 'tespm140')
    assert row == {'code': 'tespm140', 'title': 'Housing cost overburden rate', 'type': 'table',
                   'last_update': datetime(2026, 10, 29).date(), 'data_start': '2014', 'data_end': '2025'}
    blank = next(t for t in tables if t['code'] == 'ten00001')
    assert blank['data_start'] is None and blank['data_end'] is None
    with pytest.raises(SourceError) as exc:
        eurostat.parse_toc('"name"\t"kind"\n"x"\t"y"\n')
    assert exc.value.reason == 'unsupported'


def test_refresh_replaces_the_catalogue(app, db, catalog):
    EurostatCatalog.query.delete()
    db.session.add(EurostatCatalog(code='gone001', title='A table that is gone', type='table', refreshed_at=NOW))
    db.session.commit()
    count = eurostat.refresh_catalog(http=FakeHttp(), now=NOW)
    assert count == len(eurostat.parse_toc(_read('toc.txt'))) == EurostatCatalog.query.count()
    assert EurostatCatalog.query.filter_by(code='gone001').count() == 0
    assert {r.refreshed_at for r in EurostatCatalog.query} == {NOW}


@pytest.mark.parametrize('http', [
    FakeHttp(status=500), FakeHttp(raises=requests.ConnectionError('down')), FakeHttp(raises=requests.Timeout()),
    FakeHttp(toc='"name"\t"kind"\n'), FakeHttp(toc=_read('toc.txt').split('\n')[0] + '\n'),
])
def test_a_failed_refresh_keeps_the_old_catalogue(app, db, catalog, http):
    assert eurostat.refresh_catalog(http=FakeHttp(), now=NOW) > 0
    before = sorted(r.code for r in EurostatCatalog.query)
    assert eurostat.refresh_catalog(http=http, now=NOW + timedelta(days=8)) is None
    assert sorted(r.code for r in EurostatCatalog.query) == before
    assert {r.refreshed_at for r in EurostatCatalog.query} == {NOW}


def test_search_ranks_by_words_hit_and_leaves_old_tables_out(app, db, catalog):
    eurostat.refresh_catalog(http=FakeHttp(), now=NOW)
    hits = eurostat.search_catalog(['Unemployment RATE by sex'], now=NOW)
    codes = [h.code for h in hits]
    # Four words hit: "Unemployment rate by sex" (two tables), then three, then two.
    assert codes[:2] == ['teilm020', 'tesem120']
    assert set(codes[2:]) >= {'tps00203', 'tipsun20'}
    assert hits[0].title == 'Unemployment rate by sex' and hits[0].data_end == '2026-10'
    # Old data (tai10 up to 2016, tqoe3b4 up to 2010) and unknown data end (ten00001) fall away.
    assert eurostat.search_catalog('soil loss erosion', now=NOW) == []
    assert eurostat.search_catalog('flexible work schedule', now=NOW) == []
    assert eurostat.search_catalog('water resources', now=NOW) == []
    assert [h.code for h in eurostat.search_catalog('soil loss erosion', now=datetime(2019, 1, 1))] == ['tai10']
    # A tie: the title with fewer other words first.
    assert [h.code for h in eurostat.search_catalog('unemployment rate', now=NOW)][:2] == ['tps00203', 'tipsun20']
    assert eurostat.search_catalog('', now=NOW) == [] and eurostat.search_catalog([], now=NOW) == []
    assert len(eurostat.search_catalog('rate', limit=2, now=NOW)) == 2


# ── Reading JSON-stat ────────────────────────────────────────────────────

KEYS = {'DEU', 'FRA', 'NLD', 'POL', 'ESP', 'GRC', 'eurostat:EU27_2020'}


def test_parse_housing_cost_overburden():
    parsed = eurostat.parse(_json('tespm140.json'), 'tespm140')
    assert isinstance(parsed, owid.ParsedChart)
    assert parsed.title == 'Housing cost overburden rate'
    assert (parsed.unit, parsed.short_unit) == ('Percentage', '%')
    assert parsed.definition.startswith('Percentage of the population living in a household where total housing')
    assert parsed.citation == 'Eurostat (tespm140), last update 2026-10-29'
    assert parsed.source_url == 'https://ec.europa.eu/eurostat/databrowser/view/tespm140/default/table'
    assert parsed.license == 'CC BY 4.0' and parsed.column_name == 'tespm140'
    assert parsed.years == list(range(2016, 2026))
    assert set(parsed.series) == KEYS
    assert parsed.names['GRC'] == 'Greece' and parsed.names['eurostat:EU27_2020'] == \
        'European Union - 27 countries (from 2020)'
    assert parsed.series['eurostat:EU27_2020'][2016] == 10.9 and parsed.series['NLD'][2025] == 6.5   # positions 0 and 59
    # Flags: estimated values of the EU and a break in series for Germany.
    assert parsed.flags['eurostat:EU27_2020'][2016] == 'e'
    assert parsed.flags['DEU'][2020] == 'b'
    assert all(flag in ('b', 'e') for per_key in parsed.flags.values() for flag in per_key.values())


def test_parse_unemployment_after_choosing_the_unit():
    parsed = eurostat.parse(_json('tps00203_pc_act.json'), 'tps00203')
    assert parsed.title == 'Unemployment rate'
    assert parsed.unit == 'Percentage of population in the labour force' and parsed.short_unit == '%'
    assert '<p>' not in parsed.definition and parsed.definition.startswith('Unemployed persons as a percentage')
    assert set(parsed.series) == KEYS and parsed.years[-1] == 2025
    assert {f for per_key in parsed.flags.values() for f in per_key.values()} == {'b', 'd'}


def test_more_than_one_value_needs_a_choice():
    with pytest.raises(SourceError) as exc:
        eurostat.parse(_json('tps00203.json'), 'tps00203')
    assert exc.value.reason == 'needs_choice'
    dims = {d.id: d for d in exc.value.dimensions}
    assert set(dims) == {'freq', 'age', 'unit', 'sex'}
    assert [c for c, _l in dims['unit'].values] == ['PC_ACT', 'PC_POP', 'THS_PER']
    assert dims['unit'].label == 'Unit of measure'
    assert [len(d.values) for d in exc.value.dimensions if d.id != 'unit'] == [1, 1, 1]


def test_quarterly_only_is_unsupported():
    data = _json('ei_hppi_q.json')
    with pytest.raises(SourceError) as exc:
        eurostat.describe_dimensions(data)
    assert exc.value.reason == 'unsupported'
    with pytest.raises(SourceError) as exc:
        eurostat.parse(data, 'ei_hppi_q')
    assert exc.value.reason == 'unsupported'


def test_annual_is_picked_when_there_are_more_frequencies():
    data = _json('tespm140.json')
    freq = data['dimension']['freq']['category']
    freq['index'], freq['label'] = {'Q': 0, 'A': 1}, {'Q': 'Quarterly', 'A': 'Annual'}
    data['size'][0] = 2
    # The annual values move to the second half of the positions; the first half stays empty.
    half = 1
    for n in data['size'][1:]:
        half *= n
    data['value'] = {str(int(k) + half): v for k, v in data['value'].items()}
    data['status'] = {str(int(k) + half): v for k, v in data['status'].items()}
    dims = eurostat.describe_dimensions(data)
    assert next(d for d in dims if d.id == 'freq').values == [('A', 'Annual')]
    parsed = eurostat.parse(data, 'tespm140')
    assert parsed.series == eurostat.parse(_json('tespm140.json'), 'tespm140').series


def test_entity_keys_and_our_country_list():
    assert eurostat.entity_key('EL') == 'GRC' and eurostat.entity_key('UK') == 'GBR'
    assert eurostat.entity_key('NL') == 'NLD' and eurostat.entity_key('EU27_2020') == 'eurostat:EU27_2020'
    assert eurostat.entity_key('EA20') == 'eurostat:EA20'
    assert countries.iso2_for('GRC') == 'GR' and countries.iso2_for('NLD') == 'NL'
    assert countries.iso2_for('eurostat:EU27_2020') is None and countries.iso2_for('OWID_EU27') is None


# ── Fetching ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize('code', ['', 'tps 00203', '../tps00203', 'x' * 41, None, 'tps-00203'])
def test_an_invalid_code_makes_no_request(code):
    http = FakeHttp()
    with pytest.raises(SourceError) as exc:
        eurostat.fetch(code, http=http)
    assert exc.value.reason == 'invalid_slug' and http.calls == []


def test_fetch_asks_for_json_in_english_with_filters():
    http = FakeHttp()
    data = eurostat.fetch('TESPM140', filters={'geo': ['NL', 'DE'], 'unit': 'PC'}, since=2016, http=http)
    assert data['label'] == 'Housing cost overburden rate'
    url, params, headers = http.calls[0]
    assert url == 'https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/TESPM140'
    assert params == [('format', 'JSON'), ('lang', 'EN'), ('sinceTimePeriod', '2016'), ('geo', 'NL'),
                      ('geo', 'DE'), ('unit', 'PC')]
    assert headers['User-Agent'] == owid.USER_AGENT


@pytest.mark.parametrize('http, reason', [
    (FakeHttp(status=404, body=b'{"error": [{"status": 404}]}'), 'not_found'),
    (FakeHttp(status=400, body=b'{"error": [{"status": 400}]}'), 'not_found'),
    (FakeHttp(status=403, body=b''), 'forbidden'),
    (FakeHttp(status=503, body=b''), 'network'),
    (FakeHttp(raises=requests.Timeout()), 'network'),
    (FakeHttp(raises=requests.ConnectionError('down')), 'network'),
    (FakeHttp(body=b'not json'), 'unsupported'),
    (FakeHttp(body=b'{"error": []}'), 'unsupported'),
    (FakeHttp(body=b' ' * (eurostat.MAX_BYTES + 1)), 'unsupported'),
])
def test_fetch_errors(http, reason):
    with pytest.raises(SourceError) as exc:
        eurostat.fetch('tespm140', http=http)
    assert exc.value.reason == reason


def test_no_values_is_no_data():
    data = _json('tespm140.json')
    data['value'] = {}
    with pytest.raises(SourceError) as exc:
        eurostat.parse(data, 'tespm140')
    assert exc.value.reason == 'no_data'


# ── The snapshot ─────────────────────────────────────────────────────────

def test_snapshot_data_carries_the_flags():
    parsed = eurostat.parse(_json('tespm140.json'), 'tespm140')
    data = owid.snapshot_data(parsed, 2016, 2025)
    assert data['years'] == list(range(2016, 2026))
    assert set(data['flags']) == set(data['series']) == KEYS
    eu = data['flags']['eurostat:EU27_2020']
    assert len(eu) == len(data['years']) and eu[0] == 'e' and eu[-1] is None
    assert data['flags']['NLD'] == [None] * 10 or all(f in (None, 'b', 'e') for f in data['flags']['NLD'])
    # OWID has no flags: its snapshot data stays as it was.
    sipri = os.path.join(os.path.dirname(DATA), 'owid', 'military-spending-as-a-share-of-gdp-sipri')
    with open(sipri + '.csv', encoding='utf-8') as f_csv, open(sipri + '.metadata.json', encoding='utf-8') as f_meta:
        owid_parsed = owid.parse(f_csv.read(), json.load(f_meta))
    assert 'flags' not in owid.snapshot_data(owid_parsed, 2016, 2025)


def test_create_snapshot_for_eurostat(app, db):
    from app.models import FactChart
    parsed = eurostat.parse(_json('tespm140.json'), 'tespm140')
    chart = FactChart(source='eurostat', slug='tespm140', column_name='tespm140', year_from=2016, year_to=2025)
    chart.filters = json.dumps({'unit': 'PC'})
    snapshot = owid.create_snapshot(chart, parsed, eurostat.metadata_of(_json('tespm140.json')), 2016, 2025)
    assert snapshot.license == 'CC BY 4.0' and snapshot.citation.startswith('Eurostat (tespm140)')
    assert snapshot.values['flags']['eurostat:EU27_2020'][0] == 'e'
    raw = json.loads(snapshot.metadata_raw)
    assert 'value' not in raw and 'status' not in raw and raw['extension']['status']['label']['e'] == 'estimated'
    assert json.loads(chart.filters) == {'unit': 'PC'}


# ── The tick ─────────────────────────────────────────────────────────────

def test_the_tick_refreshes_only_when_needed_and_only_with_the_switch(app, db, catalog, monkeypatch):
    seen = []
    monkeypatch.setattr(eurostat, 'refresh_catalog', lambda http=None, now=None: seen.append(now) or 7)
    monkeypatch.setitem(app.config, 'BACKGROUND_FIGURES', False)
    assert bf.refresh_eurostat_catalog(now=NOW) is None and seen == []
    monkeypatch.setitem(app.config, 'BACKGROUND_FIGURES', True)
    assert bf.refresh_eurostat_catalog(now=NOW) == 7 and len(seen) == 1          # empty catalogue
    db.session.add(EurostatCatalog(code='tespm140', title='Housing cost overburden rate', type='table',
                                   refreshed_at=NOW - timedelta(days=6)))
    db.session.commit()
    assert bf.refresh_eurostat_catalog(now=NOW) == 'fresh' and len(seen) == 1   # six days old
    assert bf.refresh_eurostat_catalog(now=NOW + timedelta(days=2)) == 7 and len(seen) == 2


def test_a_failed_refresh_in_the_tick(app, db, catalog, monkeypatch):
    monkeypatch.setitem(app.config, 'BACKGROUND_FIGURES', True)
    monkeypatch.setattr(eurostat, 'refresh_catalog', lambda http=None, now=None: None)
    assert bf.refresh_eurostat_catalog(now=NOW) == 'failed'


def test_the_tick_route_reports_the_catalogue(app, db, monkeypatch):
    monkeypatch.setattr(bf, 'backfill', lambda: (0, 0))
    monkeypatch.setattr(bf, 'refresh_eurostat_catalog', lambda: 947)
    from app import digest
    monkeypatch.setattr(digest, 'tick', lambda: {})
    monkeypatch.setenv('TICK_SECRET', 'geheim')
    result = app.test_client().post('/intern/tik', headers={'Authorization': 'Bearer geheim'}).get_json()
    assert result['eurostat_catalog'] == 947


def test_the_cli_refreshes(app, db, catalog, monkeypatch):
    monkeypatch.setattr(eurostat, 'requests', SimpleNamespace(get=FakeHttp().get, RequestException=Exception))
    result = app.test_cli_runner().invoke(args=['eurostat-catalogus-verversen'])
    assert result.exit_code == 0 and f'{EurostatCatalog.query.count()} main tables' in result.output
