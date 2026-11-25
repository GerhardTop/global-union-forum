"""Background figures, step 1b (docs/achtergrondcijfers.md): the Our World in
Data adapter (app/sources/owid.py), the ISO3 to ISO2 link
(app/sources/countries.py) and the models FactChart and ChartSnapshot.

Never network: the fixtures in tests/data/owid/ and a fake http object.
"""
import json
import os
import uuid

import pytest
import requests

from app.models import ChartSnapshot, FactChart, Post
from app.sources import SourceError, countries, owid
from tests.test_delete_issue import _cleanup, _clean_session  # noqa: F401  (autouse fixture)
from tests.test_issue_polish import _fact, _issue, make_user  # noqa: F401  (fixture)

DATA = os.path.join(os.path.dirname(__file__), 'data', 'owid')
SLUG = 'military-spending-as-a-share-of-gdp-sipri'


def _read(name):
    with open(os.path.join(DATA, name), encoding='utf-8') as f:
        return f.read()


@pytest.fixture(scope='module')
def metadata():
    return json.loads(_read(f'{SLUG}.metadata.json'))


@pytest.fixture(scope='module')
def parsed(metadata):
    return owid.parse(_read(f'{SLUG}.csv'), metadata)


# ── A fake http ──────────────────────────────────────────────────────────

class _Response:
    def __init__(self, status, body=b''):
        self.status_code = status
        self.content = body


class FakeHttp:
    """Answers per URL suffix (.csv / .metadata.json) with a status and body,
    or raises an exception; records every request."""

    def __init__(self, csv=(200, b''), meta=(200, b'{}')):
        self.answers = {'csv': csv, 'meta': meta}
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append((url, headers, timeout))
        answer = self.answers['meta' if url.endswith('.metadata.json') else 'csv']
        if isinstance(answer, Exception):
            raise answer
        return _Response(*answer)


# ── parse ────────────────────────────────────────────────────────────────

def test_parse_reads_the_chart(parsed):
    assert parsed.title == 'Military spending as a share of GDP'
    assert parsed.subtitle.startswith('Military expenditure divided by gross domestic product')
    assert parsed.column_name == 'share_gdp'
    assert (parsed.unit, parsed.short_unit) == ('% of GDP', '%')
    assert parsed.definition.startswith('Total military expenditure divided by gross domestic product')
    assert parsed.citation == 'Stockholm International Peace Research Institute (2026)'
    assert parsed.citation_short.startswith('Stockholm International Peace Research Institute (2026)')
    assert parsed.source_url == f'https://ourworldindata.org/grapher/{SLUG}'
    # owid_region (the continent) is an annotation, not a second data column.
    assert set(parsed.series) == {'NLD', 'DEU', 'FRA', 'POL', 'ESP', 'USA', 'OWID_KOS'}
    assert parsed.names['NLD'] == 'Netherlands' and parsed.names['OWID_KOS'] == 'Kosovo'
    assert parsed.years == list(range(2010, 2026))
    assert owid.last_year(parsed) == 2025
    assert all(isinstance(v, float) for v in parsed.series['POL'].values())


def test_parse_keys_regions_by_code_or_name_and_skips_empty_cells():
    regions = owid.parse(_read('regions.csv'), {}, slug='some-chart')
    assert set(regions.series) == {'NLD', 'OWID_WRL', 'OWID_EU27', 'name:Europe (SIPRI)', 'name:Elsewhere'}
    assert regions.names['name:Europe (SIPRI)'] == 'Europe (SIPRI)'
    assert regions.series['NLD'] == {2020: 1.4, 2022: 1.6}          # 2023 is empty
    assert regions.series['OWID_WRL'] == {2020: 2.2, 2021: 2.3}     # 2022 is not a number
    assert regions.source_url == 'https://ourworldindata.org/grapher/some-chart'
    assert owid.has_values(regions, 'NLD', 2021, 2022) and not owid.has_values(regions, 'NLD', 2023, 2030)
    assert not owid.has_values(regions, 'OWID_XXX', 2000, 2030)


def test_unsupported_formats(metadata):
    for name in ('two-columns.csv', 'daily.csv'):
        with pytest.raises(SourceError) as err:
            owid.parse(_read(name), metadata)
        assert err.value.reason == 'unsupported', name
    with pytest.raises(SourceError) as err:
        owid.parse('entity,code,year,share_gdp\nNetherlands,NLD,2020,\n', metadata)
    assert err.value.reason == 'no_data'


# ── snapshot_data ────────────────────────────────────────────────────────

def test_snapshot_data_keeps_every_entity_in_the_period(parsed):
    data = owid.snapshot_data(parsed, 2015, 2024)
    assert data['years'] == list(range(2015, 2025))
    assert set(data['series']) == set(parsed.series)
    assert all(len(row) == 10 for row in data['series'].values())
    assert data['series']['NLD'][0] == parsed.series['NLD'][2015]
    assert data['names']['USA'] == 'United States'


def test_snapshot_data_null_for_missing_years_and_only_entities_with_values():
    regions = owid.parse(_read('regions.csv'), {})
    data = owid.snapshot_data(regions, 2020, 2023)
    assert data['years'] == [2020, 2021, 2022]                       # nobody has 2023
    assert data['series']['NLD'] == [1.4, None, 1.6]
    assert data['series']['OWID_EU27'] == [None, 1.5, None]
    assert 'name:Elsewhere' not in data['series'] and 'name:Elsewhere' not in data['names']
    with pytest.raises(SourceError) as err:
        owid.snapshot_data(regions, 1990, 2000)
    assert err.value.reason == 'no_data'


# ── fetch ────────────────────────────────────────────────────────────────

def test_fetch_gets_csv_and_metadata_with_our_user_agent(metadata):
    http = FakeHttp(csv=(200, _read(f'{SLUG}.csv').encode('utf-8')),
                    meta=(200, _read(f'{SLUG}.metadata.json').encode('utf-8')))
    csv_text, meta = owid.fetch(SLUG, http=http)
    assert csv_text.startswith('entity,code,year,share_gdp') and meta == metadata
    urls = [c[0] for c in http.calls]
    assert urls == [f'https://ourworldindata.org/grapher/{SLUG}.csv?csvType=full&useColumnShortNames=true',
                    f'https://ourworldindata.org/grapher/{SLUG}.metadata.json']
    assert all(c[1]['User-Agent'] == 'GlobalUnionForum/1.0 (+https://globalunionforum.org)' and c[2] == 20
               for c in http.calls)


@pytest.mark.parametrize('slug', ['', 'Military', 'a b', '../etc/passwd', 'x?y=1', 'a' * 201, None])
def test_an_invalid_slug_sends_no_request(slug):
    http = FakeHttp()
    with pytest.raises(SourceError) as err:
        owid.fetch(slug, http=http)
    assert err.value.reason == 'invalid_slug' and http.calls == []


@pytest.mark.parametrize('answer, reason', [
    ((404, b''), 'not_found'),
    ((403, b''), 'forbidden'),
    ((500, b''), 'network'),
    ((503, b''), 'network'),
    (requests.Timeout('slow'), 'network'),
    (requests.ConnectionError('down'), 'network'),
])
def test_http_failures(answer, reason):
    with pytest.raises(SourceError) as err:
        owid.fetch(SLUG, http=FakeHttp(csv=answer))
    assert err.value.reason == reason
    with pytest.raises(SourceError) as err:
        owid.fetch(SLUG, http=FakeHttp(csv=(200, b'entity,code,year,x\n'), meta=answer))
    assert err.value.reason == reason


def test_metadata_that_is_no_json():
    with pytest.raises(SourceError) as err:
        owid.fetch(SLUG, http=FakeHttp(csv=(200, b'entity,code,year,x\n'), meta=(200, b'<html>')))
    assert err.value.reason == 'unsupported'


# ── ISO ──────────────────────────────────────────────────────────────────

def test_every_country_of_our_list_has_an_iso3():
    with open(os.path.join(os.path.dirname(DATA), '..', '..', 'app', 'data', 'landenlijst_data.json'),
              encoding='utf-8') as f:
        ours = [row['iso'] for row in json.load(f)]
    for iso2 in ours:
        iso3 = countries.iso3_for(iso2)
        assert iso3, iso2
        assert countries.iso2_for(iso3) == iso2


def test_iso3_to_iso2():
    pairs = {'NLD': 'NL', 'DEU': 'DE', 'FRA': 'FR', 'POL': 'PL', 'ESP': 'ES', 'USA': 'US', 'CHN': 'CN',
             'IND': 'IN', 'OWID_KOS': 'XK'}
    assert {k: countries.iso2_for(k) for k in pairs} == pairs
    # Regions, averages and countries outside our list: None.
    for key in ('OWID_WRL', 'OWID_EU27', 'name:Europe (SIPRI)', 'ABW', '', None):
        assert countries.iso2_for(key) is None, key


# ── The models ───────────────────────────────────────────────────────────

def _chart(db, make_user, parsed, metadata):
    user = make_user()
    issue = _issue(db, user, f'Cijfers {uuid.uuid4().hex[:8]}')
    fact = _fact(db, issue, user, body=f'Een grafiek {uuid.uuid4().hex[:8]}.', direction='neutraal')
    chart = FactChart(post_id=fact.id, issue_id=issue.id, slug=SLUG, column_name=parsed.column_name,
                      year_from=2015, year_to=2024)
    chart.entity_keys = ['POL', 'FRA', 'DEU', 'NLD']
    db.session.add(chart)
    owid.create_snapshot(chart, parsed, metadata, 2015, 2024)
    db.session.commit()
    return issue, fact, chart


def _rows(chart_id):
    return (FactChart.query.filter_by(id=chart_id).count(),
            ChartSnapshot.query.filter_by(chart_id=chart_id).count())


def test_a_chart_with_its_snapshots(app, db, make_user, parsed, metadata):
    issue, fact, chart = _chart(db, make_user, parsed, metadata)
    try:
        assert chart.source == 'owid' and chart.entity_keys == ['POL', 'FRA', 'DEU', 'NLD']
        assert chart.average_key is None and fact.chart is chart
        first = chart.current_snapshot
        assert (first.version, first.license, first.frozen_at) == (1, 'CC BY 4.0', None)
        assert first.fetched_at is not None and first.title == parsed.title
        assert first.values['years'] == list(range(2015, 2025)) and set(first.values['series']) == set(parsed.series)
        assert json.loads(first.metadata_raw) == metadata
        newer = owid.create_snapshot(chart, parsed, metadata, 2015, 2024)
        newer.version = 2
        db.session.commit()
        assert chart.current_snapshot is newer and [s.version for s in chart.snapshots] == [1, 2]
    finally:
        _cleanup(issue.id)


def test_purging_the_issue_takes_the_chart_and_snapshots_along(app, db, make_user, parsed, metadata):
    issue, _fact_post, chart = _chart(db, make_user, parsed, metadata)
    chart_id = chart.id
    assert _rows(chart_id) == (1, 1)
    _cleanup(issue.id)
    assert _rows(chart_id) == (0, 0)


def test_deleting_the_fact_takes_the_chart_and_snapshots_along(app, db, make_user, parsed, metadata):
    issue, fact, chart = _chart(db, make_user, parsed, metadata)
    chart_id = chart.id
    try:
        db.session.delete(Post.query.get(fact.id))
        db.session.commit()
        assert _rows(chart_id) == (0, 0)
    finally:
        _cleanup(issue.id)


def test_bulk_deleting_facts_takes_the_chart_and_snapshots_along(app, db, make_user, parsed, metadata):
    from app import quality
    issue, fact, chart = _chart(db, make_user, parsed, metadata)
    chart_id = chart.id
    try:
        quality.delete_for_posts([fact.id])
        db.session.commit()
        assert _rows(chart_id) == (0, 0)
    finally:
        _cleanup(issue.id)
