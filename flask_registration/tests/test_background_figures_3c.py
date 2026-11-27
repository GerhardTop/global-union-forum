"""Background figures, step 3c: the catalogue search per term (stop words,
fixed places for main tables and datasets), no breakdown without a total,
and the display of a Eurostat chart (source per source, "About these
figures" with Selection, licence, disclaimer and Notes, flags in the table).

Never network, never the real AI: the fakes of tests/test_background_figures_3b.py.
"""
import json
import uuid

import pytest

from app import background_figures as bf, content_i18n
from app.models import OwidRejectedSlug
from app.sources import eurostat
from tests.test_background_figures import _charts, _issue, _run_for, made, started, watched  # noqa: F401
from tests.test_background_figures_3b import (NOW, BothHttp, Client, _answer, _eurostat_chart,  # noqa: F401
                                              _eurostat_json, both, catalog)
from tests.test_delete_issue import _clean_session  # noqa: F401  (autouse fixture)
from tests.test_eurostat_source import FakeHttp as TocHttp
from tests.test_issue_polish import make_user  # noqa: F401  (fixture)


@pytest.fixture
def full_catalog(app, db):
    """The whole fixture table of contents (tests/data/eurostat/toc.txt),
    with the housing and waste tables; cleared afterwards."""
    from app.models import EurostatCatalog
    EurostatCatalog.query.delete()
    db.session.commit()
    assert eurostat.refresh_catalog(http=TocHttp(), now=NOW)
    yield
    EurostatCatalog.query.delete()
    db.session.commit()


# ── A1: searching per term ───────────────────────────────────────────────

HOUSING_TERMS = ['housing cost overburden rate', 'house prices per capita', 'residential construction share of GDP',
                 'housing costs per capita']


def test_housing_search_puts_the_general_table_first_and_no_waste(app, db, full_catalog):
    hits = eurostat.search_catalog(HOUSING_TERMS, now=NOW)
    codes = [h.code for h in hits]
    assert codes[0] == 'tespm140'
    assert not [c for c in codes if c.startswith('cei_pc')]           # packaging waste per capita stays out
    assert 'tipsna50' in codes and codes.index('tipsna50') < codes.index('tessi162')   # turn by turn per term


def test_every_word_of_the_term_ranks_above_some():
    rows = [type('Row', (), {'code': c, 'title': t}) for c, t in (
        ('a1', 'Housing cost overburden rate by income quintile'), ('a2', 'Housing cost overburden rate'),
        ('a3', 'Housing deprivation rate'), ('a4', 'Overburden of something else entirely'))]
    entries = [(r, eurostat._words(r.title)) for r in rows]
    assert [r.code for r in eurostat._ranked(entries, 'housing cost overburden')] == ['a2', 'a1', 'a3', 'a4']
    assert eurostat._ranked(entries, 'rate per capita') == []
    assert {'per', 'capita', 'rate', 'share', 'total', 'number', 'data', 'by', 'and'} <= eurostat.STOPWORDS


def test_fixed_places_and_turn_by_turn(app, db, full_catalog):
    hits = eurostat.search_catalog(['unemployment', 'housing cost overburden'], tables=2, now=NOW)
    assert [h.code for h in hits] == ['tps00203', 'tespm140', 'ilc_lvho07a']
    # Empty places for datasets do not go to tables (only one yearly dataset here).
    hits = eurostat.search_catalog(['unemployment', 'housing cost overburden'], now=NOW)
    assert sum(h.type == 'table' for h in hits) <= eurostat.SEARCH_TABLES
    assert [h.code for h in hits if h.type == 'dataset'] == ['ilc_lvho07a']
    many = eurostat.search_catalog(['unemployment', 'housing', 'construction', 'confidence', 'current account'],
                                   now=NOW)
    assert sum(h.type == 'table' for h in many) == eurostat.SEARCH_TABLES
    assert len({h.code for h in many}) == len(many)                     # no doubles


def test_excluded_codes_make_room_for_the_next(app, db, full_catalog):
    hits = eurostat.search_catalog(['housing cost overburden'], tables=1, exclude={'tespm140'}, now=NOW)
    assert [h.code for h in hits if h.type == 'table'] == ['tessi162']


def test_the_round_has_at_most_ten_tables_and_five_datasets(app, db, make_user, made, both, watched, full_catalog):
    issue = _issue(db, make_user(), made)
    client = Client(_answer(), terms=HOUSING_TERMS)
    bf.run(_run_for(issue).id, client=client, http=BothHttp())
    (prompt,) = client.prompt(bf._SELECTION_SYSTEM_BOTH)
    assert 'eurostat:tespm140' in prompt and 'eurostat:cei_pc040' not in prompt
    assert prompt.count('source: Eurostat (main table)') <= eurostat.SEARCH_TABLES
    assert prompt.count('source: Eurostat (dataset)') <= eurostat.SEARCH_DATASETS


# ── A2: no breakdown without a total ─────────────────────────────────────

def test_measures_and_totals_are_recognised():
    assert all(eurostat.is_measure(d) for d in ('unit', 'currency', 's_adj', 'na_item', 'indic_il', 'indic_de'))
    assert not any(eurostat.is_measure(d) for d in ('sex', 'age', 'quant_inc', 'nace_r2', 'hhtyp'))
    assert eurostat.is_total('T', 'Total') and eurostat.is_total('TOTAL', 'All activities')
    assert eurostat.is_total('TOT', '') and eurostat.is_total('Y_GE16', 'Total 16 years or over')
    assert not eurostat.is_total('QU1', 'First quintile') and not eurostat.is_total('M', 'Males')


def test_a_breakdown_without_total_falls_away_and_is_remembered(app, db, make_user, made, both, watched,
                                                               full_catalog):
    issue = _issue(db, make_user(), made)
    client = Client(_answer(_eurostat_chart('tessi162')), dimensions={'eurostat:tessi162': {'quant_inc': 'QU1'}},
                    terms=['housing cost overburden income quintile'])
    assert bf.run(_run_for(issue).id, client=client, http=BothHttp()) == 'nothing'
    assert 'eurostat:tessi162: no_total (no total in quant_inc)' in _run_for(issue).error
    assert client.prompt(bf._DIMENSION_SYSTEM) == []                    # no choice asked for
    assert {r.slug: r.reason for r in OwidRejectedSlug.query} == {'eurostat:tessi162': 'no_total'}


def test_the_unit_stays_free(app, db, make_user, made, both, watched, catalog):
    dims = eurostat.describe_dimensions(_eurostat_json('tps00203.json'))
    assert eurostat.without_total(dims) == []
    for unit in ('PC_ACT', 'PC_POP', 'THS_PER'):
        assert bf._choice_problem({'unit': unit}, dims) is None
    issue = _issue(db, make_user(), made)
    client = Client(_answer(_eurostat_chart()), dimensions={'eurostat:tps00203': {'unit': 'PC_ACT'}})
    assert bf.run(_run_for(issue).id, client=client, http=BothHttp()) == 'done'
    assert json.loads(_charts(issue)[0].filters) == {'unit': 'PC_ACT'}


def test_a_breakdown_choice_must_be_the_total():
    sex = eurostat.Dimension('sex', 'Sex', [('F', 'Females'), ('M', 'Males'), ('T', 'Total')])
    unit = eurostat.Dimension('unit', 'Unit', [('PC', 'Percentage'), ('THS', 'Thousand')])
    assert bf._choice_problem({'sex': 'T', 'unit': 'THS'}, [sex, unit]) is None
    assert bf._choice_problem({'sex': 'F', 'unit': 'PC'}, [sex, unit]) == "'F' is not the total of the breakdown sex"
    assert eurostat.without_total([sex, unit]) == []
    quintile = eurostat.Dimension('quant_inc', 'Income', [('QU1', 'First quintile'), ('QU2', 'Second quintile')])
    assert eurostat.without_total([quintile, unit]) == [quintile]


def test_the_dimension_prompt_asks_for_totals():
    assert ('For breakdown dimensions you must choose the total; if there is no total, answer null for that table.'
            in bf._DIMENSION_SYSTEM)


# ── B: the display of a Eurostat chart ───────────────────────────────────

def _eurostat_published(db, make_user, made, code='tps00203', countries=('Poland', 'Germany'), proposals=0):
    from tests.test_background_figures import _proposal
    owner = make_user()
    issue = _issue(db, owner, made)
    props = [_proposal(db, issue, owner) for _ in range(proposals)]
    item = _eurostat_chart(code, countries=countries)
    item['relevant_for'] = [p.id for p in props]
    dimensions = {'eurostat:tps00203': {'unit': 'PC_ACT'}} if code == 'tps00203' else None
    assert bf.run(_run_for(issue).id, client=Client(_answer(item), dimensions=dimensions),
                  http=BothHttp()) == 'done'
    (chart,) = _charts(issue)
    return issue, chart, props


def _chart_page(app, issue, chart, query='', lang='nl', user=None):
    from tests.test_issue_polish import _client, _req
    return _req(_client(app, user, lang), 'GET', f'/vraagstuk/{issue.id}/cijfer/{chart.post_id}{query}')


def test_the_snapshot_keeps_the_selection_and_the_flag_labels(app, db, make_user, made, both, watched, catalog):
    _issue_row, chart, _props = _eurostat_published(db, make_user, made)
    snapshot = chart.current_snapshot
    # Only the unit was chosen, and the unit has its own line: no selection.
    assert snapshot.label_texts == {'flag:b': 'break in time series', 'flag:d': 'definition differs (see metadata)'}
    assert content_i18n.label_fields(snapshot) == ['label:flag:b', 'label:flag:d']
    assert content_i18n.original(snapshot, 'label:flag:b') == 'break in time series'
    assert content_i18n.field_kind(snapshot, 'label:selection') == 'short'
    # They go through the translation route with the fact, as short fields.
    from app import content_jobs
    parts = {(type(obj).__name__, field) for obj, field in content_jobs._parts(db.session.get(
        type(chart.post), chart.post_id))}
    assert {('ChartSnapshot', 'label:flag:b'), ('ChartSnapshot', 'label:flag:d')} <= parts


def test_the_card_names_eurostat(app, db, make_user, made, both, watched, catalog):
    from tests.test_chart_card import _card, _page
    issue, chart, _props = _eurostat_published(db, make_user, made)
    card = _card(_page(app, issue), chart.post_id)
    assert 'Door AI geselecteerd uit Eurostat op' in card
    assert ('Bron: <a href="https://ec.europa.eu/eurostat/databrowser/view/tps00203/default/table" target="_blank" '
            'rel="noopener noreferrer">Eurostat (tps00203)</a>') in card
    assert 'Our World in Data' not in card
    assert '\u2013' not in card and '\u2014' not in card


def test_the_chart_page_for_eurostat(app, db, make_user, made, both, watched, catalog):
    issue, chart, _props = _eurostat_published(db, make_user, made)
    page = _chart_page(app, issue, chart).get_data(as_text=True)
    assert 'Door AI geselecteerd uit Eurostat op' in page
    # The source line under the chart and under "About these figures".
    assert '>Eurostat (tps00203), laatst bijgewerkt op 6 nov 2026</a> <span aria-hidden="true">·</span> Opgehaald op' \
        in page
    assert '<dt>Bron</dt><dd>Eurostat (tps00203), laatst bijgewerkt op 6 nov 2026</dd>' in page
    # Only the unit was chosen: it stands under "Unit", there is no line "Selection".
    assert '<dt>Selectie</dt>' not in page and 'Percentage of population in the labour force</p>' in page
    assert 'zoals de bron het levert' in page
    assert 'CC BY 4.0, met bronvermelding' in page
    assert 'Eurostat is niet verantwoordelijk voor vertalingen of bewerkingen van zijn cijfers op deze site.' in page
    assert 'Our World in Data' not in page and 'eigen voorwaarden' not in page
    assert '\u2013' not in page and '\u2014' not in page


def test_no_selection_without_a_choice(app, db, make_user, made, both, watched, catalog):
    issue, chart, _props = _eurostat_published(db, make_user, made, code='tespm140')
    assert 'selection' not in chart.current_snapshot.label_texts
    page = _chart_page(app, issue, chart).get_data(as_text=True)
    assert '<dt>Selectie</dt>' not in page and '<dt>Bron</dt><dd>Eurostat (tespm140)' in page


def test_flags_in_the_table_and_the_notes(app, db, make_user, made, both, watched, catalog):
    issue, chart, _props = _eurostat_published(db, make_user, made, countries=('Poland', 'Germany', 'France'))
    page = _chart_page(app, issue, chart, '?weergave=tabel').get_data(as_text=True)
    table = page[page.index('<table>'):page.index('</table>')]
    # Germany 2020 and Poland 2019 have b, France 2021 to 2025 d.
    assert table.count('<sup class="gu-chart-flag" aria-hidden="true">b</sup>') == 2
    assert table.count('<sup class="gu-chart-flag" aria-hidden="true">d</sup>') == 5
    assert '<span class="gu-sr-only"> (break in time series)</span>' in table
    assert '<dt>Opmerkingen</dt>' in page
    notes = page[page.index('<dt>Opmerkingen</dt>'):]
    notes = notes[:notes.index('</dd>')]
    assert 'break in time series' in notes and 'definition differs (see metadata)' in notes
    # No flags in the chart itself.
    chart_page = _chart_page(app, issue, chart).get_data(as_text=True)
    figure = chart_page[chart_page.index('gu-chart--large'):chart_page.index('gu-chartpage__foot')]
    assert 'gu-chart-flag' not in figure
    # The fragment brings the notes along for the script.
    fragment = _chart_page(app, issue, chart, '?weergave=tabel&fragment=1').get_data(as_text=True)
    assert '<template data-chart-notes-fresh>' in fragment and 'Opmerkingen' in fragment


def test_no_notes_without_flags_in_the_period(app, db, make_user, made, both, watched, catalog):
    issue, chart, _props = _eurostat_published(db, make_user, made)       # Poland and Germany: b in 2019, 2020
    page = _chart_page(app, issue, chart, '?weergave=tabel&periode=5').get_data(as_text=True)
    assert 'gu-chart-flag' not in page and '<dt>Opmerkingen</dt>' not in page
    page = _chart_page(app, issue, chart, '?weergave=tabel').get_data(as_text=True)
    notes = page[page.index('<dt>Opmerkingen</dt>'):]
    assert 'break in time series' in notes[:notes.index('</dd>')]
    assert 'definition differs' not in notes[:notes.index('</dd>')]      # only the letters used


def test_the_notes_and_selection_in_a_translation(app, db, make_user, made, both, watched, catalog):
    _issue_row, chart, _props = _eurostat_published(db, make_user, made)
    snapshot = chart.current_snapshot
    content_i18n.set_translation(snapshot, 'label:flag:b', 'de', 'Bruch in der Zeitreihe', is_machine=True)
    db.session.commit()
    issue = chart.issue
    page = _chart_page(app, issue, chart, '?weergave=tabel', lang='de').get_data(as_text=True)
    assert '<span class="gu-sr-only"> (Bruch in der Zeitreihe)</span>' in page
    assert 'Bruch in der Zeitreihe' in page
    assert 'Eurostat (tps00203), letzte Aktualisierung' in page


def test_the_disclaimer_only_for_eurostat(app, db, make_user, made, started, watched):
    from tests.test_chart_page import _get, _published
    issue, chart, _props = _published(db, make_user, made)
    page = _get(app, issue, chart).get_data(as_text=True)
    assert 'Eurostat' not in page and '<dt>Selectie</dt>' not in page and 'Opmerkingen' not in page
    assert 'via Our World in Data' in page and 'eigen voorwaarden' in page


@pytest.mark.parametrize('who', ['visitor', 'member'])
def test_queries_of_a_eurostat_chart_page_do_not_grow(app, db, make_user, made, both, watched, catalog, who):
    from tests.test_issue_polish import _client, _req
    from tests.test_proposal_queries import _count_queries
    counts = {}
    for n in (1, 5, 20):
        OwidRejectedSlug.query.delete()
        issue, chart, _props = _eurostat_published(db, make_user, made, proposals=n)
        user = make_user() if who == 'member' else None
        path = f'/vraagstuk/{issue.id}/cijfer/{chart.post_id}?weergave=tabel'
        client = _client(app, user)
        _req(client, 'GET', path)
        db.session.commit()
        db.session.expunge_all()
        with _count_queries() as queries:
            assert _req(client, 'GET', path).status_code == 200
        counts[n] = len(queries)
    assert counts[1] == counts[5] == counts[20], counts


# ── Selection without the unit ───────────────────────────────────────────

def _table(dims):
    """A minimal JSON-stat structure: {dimension: [(code, label)]}."""
    return {'id': list(dims) + ['geo', 'time'],
            'dimension': {d: {'label': d, 'category': {'index': {c: i for i, (c, _l) in enumerate(v)},
                                                       'label': dict(v)}} for d, v in dims.items()}}


def test_the_selection_leaves_the_unit_out():
    data = _table({'freq': [('A', 'Annual')], 'indic_bt': [('PRC_PRR', 'Producer prices')],
                   'sex': [('T', 'Total')], 'unit': [('I21', 'Index, 2021=100')]})
    assert eurostat.selection_text(data, {'unit': 'I21'}) == ''
    assert eurostat.selection_text(data, {'unit': 'I21', 'indic_bt': 'PRC_PRR'}) == 'Producer prices'
    assert eurostat.selection_text(data, {'sex': 'T', 'unit': 'I21', 'indic_bt': 'PRC_PRR'}) == \
        'Producer prices; Total'
    assert eurostat.selection_text(data, {}) == ''


def test_existing_selections_are_computed_again(app, db, make_user, made, both, watched, catalog):
    from app.models import FactChart
    # Two charts published before this change, with the unit in their selection.
    _i1, only_unit, _p = _eurostat_published(db, make_user, made)
    _i2, with_sex, _p = _eurostat_published(db, make_user, made)
    with_sex.filters = json.dumps({'sex': 'T', 'unit': 'PC_ACT'})
    for chart in (only_unit, with_sex):
        snapshot = chart.current_snapshot
        snapshot.labels = json.dumps(dict(snapshot.label_texts,
                                          selection='Percentage of population in the labour force'))
        db.session.flush()
        content_i18n.set_translation(snapshot, 'label:selection', 'nl', 'Percentage van de beroepsbevolking',
                                     is_machine=True)
        content_i18n.set_translation(snapshot, 'label:flag:b', 'nl', 'breuk in tijdreeks', is_machine=True)
    db.session.commit()
    ids = {only_unit.id, with_sex.id}
    assert bf.refresh_selection_labels() >= 2
    db.session.expire_all()
    content_i18n.clear_cache()
    charts = {c.id: c for c in FactChart.query.filter(FactChart.id.in_(ids))}
    first, second = charts[only_unit.id].current_snapshot, charts[with_sex.id].current_snapshot
    assert 'selection' not in first.label_texts and 'flag:b' in first.label_texts
    assert second.label_texts['selection'] == 'Total'
    # The old translation of the selection is gone (translated again by the route), the flags' stay.
    for snapshot in (first, second):
        assert content_i18n.text_for(snapshot, 'label:selection', 'nl') is None
        assert content_i18n.text_for(snapshot, 'label:flag:b', 'nl') == 'breuk in tijdreeks'
    from app import content_jobs
    assert (second, 'label:selection', 'nl') in content_jobs.missing(db.session.get(type(with_sex.post),
                                                                                      with_sex.post_id))
    # The page: no line for the first, only the other dimension for the second.
    page = _chart_page(app, charts[only_unit.id].issue, charts[only_unit.id]).get_data(as_text=True)
    assert '<dt>Selectie</dt>' not in page
    page = _chart_page(app, charts[with_sex.id].issue, charts[with_sex.id]).get_data(as_text=True)
    selection = page[page.index('<dt>Selectie</dt>'):]
    assert 'Total</p>' in selection[:selection.index('</dd>')]
    assert 'Percentage of population' not in selection[:selection.index('</dd>')]
    # Idempotent.
    assert bf.refresh_selection_labels() == 0


def test_owid_snapshots_are_left_alone(app, db, make_user, made, started, watched):
    from tests.test_chart_page import _get, _published
    issue, chart, _props = _published(db, make_user, made)
    bf.refresh_selection_labels()
    db.session.expire_all()
    assert chart.current_snapshot.labels is None
    page = _get(app, issue, chart).get_data(as_text=True)
    assert '<dt>Selectie</dt>' not in page and 'via Our World in Data' in page
