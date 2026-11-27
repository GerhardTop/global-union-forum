"""Background figures, step 1l: measures per person or a share in the
prompts, an "average" that is really a total falls away
(AGGREGATE_TOTAL_FACTOR), and compact labels for large numbers (the table
keeps the full number).

Never network, never the real AI.
"""
import uuid
from types import SimpleNamespace

import pytest

from app import background_figures as bf, charts
from app.sources import owid

SLUG = 'co2-emissions'


def _csv(rows):
    lines = ['entity,code,year,emissions']
    for name, code, values in rows:
        lines += [f'{name},{code},{year},{value}' for year, value in values.items()]
    return '\n'.join(lines) + '\n'


COUNTRIES = [
    ('Germany', 'DEU', {2021: 760e6, 2022: 734e6, 2023: 674e6}),
    ('Poland', 'POL', {2021: 322e6, 2022: 318e6, 2023: 287e6}),
    ('Netherlands', 'NLD', {2021: 146e6, 2022: 138e6, 2023: 125e6}),
]


def _check(monkeypatch, average_values):
    rows = COUNTRIES + [('European Union (27)', 'OWID_EU27', average_values)]
    monkeypatch.setattr(owid, 'fetch', lambda slug, http=None: (_csv(rows), {}))
    ctx = bf.Context(issue=None, named=['DE', 'PL', 'NL'], fixed=list(bf.EUROPE_SET))
    notes = []
    item = {'slug': SLUG, 'countries': ['Germany', 'Poland', 'Netherlands'], 'average': 'European Union (27)',
            'years': 3, 'reason': 'Emissions per country.'}
    candidates = {f'owid:{SLUG}': bf.Candidate(id=f'owid:{SLUG}', source='owid', slug=SLUG, title=SLUG)}
    accepted = bf.check_chart(item, ctx, candidates, None, notes, now=bf.datetime(2025, 1, 1))
    assert accepted is not None and accepted.keys == ['DEU', 'POL', 'NLD']
    return accepted, notes


def test_the_prompts_ask_for_per_capita_or_world_shares():
    assert ("The kind of measure decides what you compare. To compare countries, choose a per capita or share "
            "measure and compare the named countries (otherwise the fixed set) with the average the source "
            "supplies. To show how large each part of the world is, compare world blocks (European Union (27), "
            "United States, China, India); prefer their share of the world total (for example 'share of global "
            "CO2 emissions', in %) over absolute totals; absolute totals only between these blocks and never with "
            "an average. For questions about responsibility or contribution, one chart of each kind can both be "
            "useful, as two different aspects.") in bf._SELECTION_SYSTEM
    assert ("Use per capita or share terms for comparing countries, and 'share of global' terms for comparing "
            "world blocks.") in bf._TERMS_SYSTEM
    assert '"compare": "countries" or "blocks"' in bf._SELECTION_SYSTEM
    assert bf.AGGREGATE_TOTAL_FACTOR == 2


def test_a_total_as_average_falls_away_and_the_chart_stays(monkeypatch):
    accepted, notes = _check(monkeypatch, {2021: 2.6e9, 2022: 2.5e9, 2023: 2.4e9})
    assert accepted.average_key is None
    assert any("average 'European Union (27)' aggregate_is_total" in n for n in notes)


def test_a_real_average_between_the_countries_stays(monkeypatch):
    accepted, notes = _check(monkeypatch, {2021: 300e6, 2022: 290e6, 2023: 270e6})
    assert accepted.average_key == 'OWID_EU27' and notes == []


def test_above_one_country_but_not_twice_the_largest_stays(monkeypatch):
    # Above Germany, but less than 2 x Germany in every year.
    accepted, _notes = _check(monkeypatch, {2021: 1.4e9, 2022: 1.4e9, 2023: 1.3e9})
    assert accepted.average_key == 'OWID_EU27'


def test_twice_the_largest_in_only_some_years_stays(monkeypatch):
    accepted, _notes = _check(monkeypatch, {2021: 2.6e9, 2022: 1.0e9, 2023: 2.4e9})
    assert accepted.average_key == 'OWID_EU27'


def test_exactly_twice_counts_as_a_total(monkeypatch):
    accepted, _notes = _check(monkeypatch, {2021: 2 * 760e6, 2022: 2 * 734e6, 2023: 2 * 674e6})
    assert accepted.average_key is None


# ── Compact numbers ──────────────────────────────────────────────────────

LARGE_DATA = {'years': [2021, 2022, 2023],
              'series': {'DEU': [760e6, 734e6, 674e6], 'POL': [322e6, 318e6, 287e6], 'NLD': [146e6, 138e6, 125e6]},
              'names': {'DEU': 'Germany', 'POL': 'Poland', 'NLD': 'Netherlands'}}
NBSP = '\xa0'


def _axis(lang, top):
    data = {'years': [2022, 2023], 'series': {'A': [0.0, top]}, 'names': {}}
    view = charts.layout(data, ['A'], None, charts.LARGE, lang, None, (), {}, 't')
    return [g.label.replace(NBSP, ' ') for g in view.grid], view.legend[0].value.replace(NBSP, ' ')


# One magnitude per chart, the suffix from Babel (checked 26 November 2026, Babel 2.18).
@pytest.mark.parametrize('lang, top, axis, legend', [
    ('nl', 1e6, ['0 t', '0,2 mln. t', '0,4 mln. t', '0,6 mln. t', '0,8 mln. t', '1 mln. t'], '1,0 mln. t'),
    ('nl', 3e9, ['0 t', '1 mld. t', '2 mld. t', '3 mld. t'], '3,0 mld. t'),
    ('de', 1e6, ['0 t', '0,2 Mio. t', '0,4 Mio. t', '0,6 Mio. t', '0,8 Mio. t', '1 Mio. t'], '1,0 Mio. t'),
    ('de', 3e9, ['0 t', '1 Mrd. t', '2 Mrd. t', '3 Mrd. t'], '3,0 Mrd. t'),
    # Spanish has no short form for a billion: Babel writes "1000 M", so the axis stays in millions.
    ('es', 1e6, ['0 t', '0,2 M t', '0,4 M t', '0,6 M t', '0,8 M t', '1 M t'], '1,0 M t'),
    ('es', 3e9, ['0 t', '1.000 M t', '2.000 M t', '3.000 M t'], '3.000 M t'),
    ('fr', 1e6, ['0 t', '0,2 M t', '0,4 M t', '0,6 M t', '0,8 M t', '1 M t'], '1,0 M t'),
    ('fr', 3e9, ['0 t', '1 Md t', '2 Md t', '3 Md t'], '3,0 Md t'),
])
def test_one_magnitude_per_axis(lang, top, axis, legend):
    assert _axis(lang, top) == (axis, legend)


def test_smaller_numbers_keep_the_magnitude():
    data = {'years': [2022, 2023], 'series': {'A': [0.0, 1.25e6], 'B': [250_000.0, 250_000.0]}, 'names': {}}
    view = charts.layout(data, ['A', 'B'], None, charts.LARGE, 'nl', None, (), {}, 't')
    labels = [g.label.replace(NBSP, ' ') for g in view.grid]
    assert '0,25 mln. t' in labels and '1,25 mln. t' in labels and not any('K' in label for label in labels)
    # One decimal under 100 in the chart's magnitude; Babel rounds half to even (1,25 to 1,2).
    assert [i.value.replace(NBSP, ' ') for i in view.legend] == ['1,2 mln. t', '0,2 mln. t']
    assert [e.value.replace(NBSP, ' ') for e in view.end_labels] == ['1,2 mln. t', '0,2 mln. t']
    assert charts.magnitude(999_999, 'nl') is None and charts.magnitude(1e6, 'nl') == (10 ** 6, NBSP + 'mln.')
    assert charts.magnitude(3e9, 'es') == (10 ** 6, NBSP + 'M')


def test_axis_steps_keep_two_decimals():
    data = {'years': [2022, 2023], 'series': {'DEU': [0.0, 1.25e9]}, 'names': {}}
    view = charts.layout(data, ['DEU'], None, charts.LARGE, 'nl', None, (), {}, 't')
    labels = [g.label.replace(NBSP, ' ') for g in view.grid]
    assert '1,25 mld. t' in labels or '1 mld. t' in labels
    assert all(not label.startswith('1,3') for label in labels)


def test_below_a_million_stays_as_it_was():
    data = {'years': [2022, 2023], 'series': {'NLD': [950_000.0, 999_000.0]}, 'names': {}}
    view = charts.layout(data, ['NLD'], None, charts.LARGE, 'nl', None, (), {}, 't')
    assert view.legend[0].value.replace(NBSP, ' ') == '999.000 t'
    assert charts.format_value(4.5, 'nl', '%') == '4,5%'


# ── World blocks (compare "blocks") ──────────────────────────────────────

from app import content_i18n  # noqa: E402
from app.models import Post  # noqa: E402
from tests.test_background_figures import (FakeClient, FakeHttp, _answer, _charts, _issue, _run_for,  # noqa: E402,F401
                                           made, started, watched)
from tests.test_delete_issue import _clean_session  # noqa: E402,F401  (autouse fixture)
from tests.test_issue_polish import make_user  # noqa: E402,F401  (fixture)

SHARE, PER_CAPITA = 'share-of-global-co2-emissions', 'co-emissions-per-capita'
_YEARS = range(2016, 2025)
_SERIES = {
    SHARE: [('European Union (27)', 'OWID_EU27', 7.5), ('United States', 'USA', 13.5), ('China', 'CHN', 31.0),
            ('India', 'IND', 7.8), ('Netherlands', 'NLD', 0.4), ('World', 'OWID_WRL', 100.0)],
    PER_CAPITA: [('Germany', 'DEU', 8.0), ('France', 'FRA', 4.6), ('Netherlands', 'NLD', 7.1), ('Poland', 'POL', 7.9),
                 ('European Union (27)', 'OWID_EU27', 5.9), ('United States', 'USA', 14.3)],
}


class BlockHttp(FakeHttp):
    def get(self, url, params=None, headers=None, timeout=None):
        slug = url.split('/grapher/')[1].split('.')[0] if '/grapher/' in url else None
        if slug in _SERIES:
            self.calls.append((url, params))
            if url.endswith('.metadata.json'):
                return SimpleNamespace(status_code=200, content=b'{"chart": {"title": "%s"}}' % slug.encode())
            rows = [(name, code, {y: base + (y - 2016) * 0.1 for y in _YEARS}) for name, code, base in _SERIES[slug]]
            return SimpleNamespace(status_code=200, content=_csv(rows).encode())
        return super().get(url, params=params, headers=headers, timeout=timeout)


@pytest.fixture
def co2_hits(monkeypatch):
    found = [owid.SearchHit(slug=slug, title=slug, subtitle='', entities=[n for n, _c, _b in _SERIES[slug]])
             for slug in (SHARE, PER_CAPITA)]
    monkeypatch.setattr(owid, 'search', lambda *a, **kw: found)
    return found


def _co2_issue(db, make_user, made):
    return _issue(db, make_user(), made, title=f'CO2 per land {uuid.uuid4().hex[:6]}',
                  intro='Hoeveel moet elk Europees land zijn CO2-uitstoot nog verminderen?')


def _blocks(names=('European Union (27)', 'United States', 'China', 'India'), average=None):
    return {'slug': SHARE, 'compare': 'blocks', 'countries': list(names), 'average': average, 'years': 5,
            'reason': 'It shows how large each part of the world is in global emissions.', 'relevant_for': []}


def _per_capita(average='European Union (27)', names=('Germany', 'France', 'Netherlands', 'Poland')):
    return {'slug': PER_CAPITA, 'compare': 'countries', 'countries': list(names), 'average': average, 'years': 5,
            'reason': 'It shows emissions per person in each country.', 'relevant_for': []}


def test_blocks_publish_without_an_average_and_the_eu_as_a_line(app, db, make_user, made, started, watched,
                                                               co2_hits):
    issue = _co2_issue(db, make_user, made)
    answer = _answer(_blocks(average='World'))
    assert bf.run(_run_for(issue).id, client=FakeClient(answer), http=BlockHttp()) == 'done'
    (chart,) = _charts(issue)
    assert chart.entity_keys == ['OWID_EU27', 'USA', 'CHN', 'IND'] and chart.average_key is None
    assert chart.average_name == 'European Union (27)'          # the EU line's name, for the translation route
    assert "average 'World' with compare \"blocks\", so no average" in _run_for(issue).error
    snapshot = chart.current_snapshot
    content_i18n.set_translation(chart, 'average_name', 'nl', 'Europese Unie (27)', is_machine=True)
    db.session.commit()
    names, average = bf.display_names(chart, snapshot, 'nl')
    assert average == '' and names['OWID_EU27'] == 'Europese Unie (27)' and names['USA'] == 'Verenigde Staten'
    view = charts.layout(snapshot.values, chart.entity_keys, chart.average_key, charts.LARGE, 'nl', None, (), names)
    assert not any(line.dashed for line in view.lines) and len(view.lines) == 4
    assert len({line.color for line in view.lines}) == 4 and charts.AVERAGE_COLOR not in {l.color for l in view.lines}
    assert [i.name for i in view.legend][0] == 'Europese Unie (27)'


def test_blocks_with_the_netherlands_fall_away(app, db, make_user, made, started, watched, co2_hits):
    issue = _co2_issue(db, make_user, made)
    answer = _answer(_blocks(names=('European Union (27)', 'United States', 'China', 'Netherlands')))
    assert bf.run(_run_for(issue).id, client=FakeClient(answer), http=BlockHttp()) == 'nothing'
    assert _charts(issue) == []
    assert "blocks_mixed_with_countries ('Netherlands')" in _run_for(issue).error


def test_a_countries_chart_with_the_eu_as_a_line_falls_away(app, db, make_user, made, started, watched, co2_hits):
    issue = _co2_issue(db, make_user, made)
    answer = _answer(_per_capita(average=None, names=('Germany', 'France', 'European Union (27)')))
    assert bf.run(_run_for(issue).id, client=FakeClient(answer), http=BlockHttp()) == 'nothing'
    assert 'eu_line_in_countries_chart' in _run_for(issue).error


def test_countries_with_an_average_as_before(app, db, make_user, made, started, watched, co2_hits):
    issue = _co2_issue(db, make_user, made)
    assert bf.run(_run_for(issue).id, client=FakeClient(_answer(_per_capita())), http=BlockHttp()) == 'done'
    (chart,) = _charts(issue)
    assert chart.entity_keys == ['DEU', 'FRA', 'NLD', 'POL'] and chart.average_key == 'OWID_EU27'
    view = charts.layout(chart.current_snapshot.values, chart.entity_keys, chart.average_key, charts.LARGE, 'nl')
    assert [line.dashed for line in view.lines] == [False, False, False, False, True]


def test_one_issue_gets_a_blocks_and_a_countries_chart(app, db, make_user, made, started, watched, co2_hits):
    issue = _co2_issue(db, make_user, made)
    answer = _answer(_per_capita(), _blocks())
    assert bf.run(_run_for(issue).id, client=FakeClient(answer), http=BlockHttp()) == 'done'
    found = {c.slug: (c.entity_keys, c.average_key) for c in _charts(issue)}
    assert found == {PER_CAPITA: (['DEU', 'FRA', 'NLD', 'POL'], 'OWID_EU27'),
                     SHARE: (['OWID_EU27', 'USA', 'CHN', 'IND'], None)}
    assert {db.session.get(Post, c.post_id).fact_type for c in _charts(issue)} == {'cijfer'}


def test_an_unknown_compare_falls_away(app, db, make_user, made, started, watched, co2_hits):
    issue = _co2_issue(db, make_user, made)
    item = _blocks()
    item['compare'] = 'regions'
    assert bf.run(_run_for(issue).id, client=FakeClient(_answer(item)), http=BlockHttp()) == 'nothing'
    assert "compare 'regions' is neither countries nor blocks" in _run_for(issue).error


def test_the_prompt_offers_the_blocks(app, db, make_user, made, started, watched, co2_hits):
    issue = _co2_issue(db, make_user, made)
    client = FakeClient(_answer())
    bf.run(_run_for(issue).id, client=client, http=BlockHttp())
    prompt = client.calls[1]['messages'][0]['content']
    assert ('Or, with compare "blocks", only the world blocks: European Union (27), United States, China, India '
            '(no average).') in prompt
