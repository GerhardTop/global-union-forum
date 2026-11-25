"""Background figures, step 1c: the chart standard (app/charts.py) and the
macros in app/templates/charts/_chart.html. Pure: no database.
"""
import json
import os
import re

import pytest

from app import charts
from app.sources import owid

DATA = os.path.join(os.path.dirname(__file__), 'data', 'owid')
SLUG = 'military-spending-as-a-share-of-gdp-sipri'
KEYS = ['POL', 'FRA', 'DEU', 'NLD']


def _read(name):
    with open(os.path.join(DATA, name), encoding='utf-8') as f:
        return f.read()


@pytest.fixture(scope='module')
def sipri():
    parsed = owid.parse(_read(f'{SLUG}.csv'), json.loads(_read(f'{SLUG}.metadata.json')))
    return owid.snapshot_data(parsed, 2010, 2025)


def _data(series, years=None, names=None):
    years = years or list(range(2015, 2015 + len(next(iter(series.values())))))
    return {'years': years, 'series': series, 'names': names or {k: k for k in series}}


def _grid_values(view):
    return [round(view.y_min + i * view.step, 9) for i in range(round((view.y_max - view.y_min) / view.step) + 1)]


# ── The y axis ───────────────────────────────────────────────────────────

def _is_nice(step):
    power = 10 ** __import__('math').floor(__import__('math').log10(step))
    return any(abs(step - base * power) < 1e-9 for base in (1, 2, 2.5, 5))


def test_round_steps_from_zero():
    small = charts.layout(_data({'A': [1.0, 4.1]}), ['A'], size=charts.SMALL)
    large = charts.layout(_data({'A': [1.0, 4.1]}), ['A'], size=charts.LARGE)
    assert (small.y_min, small.y_max, small.step) == (0, 5, 2.5)
    assert (large.y_min, large.y_max, large.step) == (0, 5, 1)
    for view in (small, large):
        assert _is_nice(view.step) and view.y_max >= 4.1 and view.y_max - view.step < 4.1


@pytest.mark.parametrize('values', [[0.3, 0.7], [1.0, 4.1], [12, 87], [0, 0], [5, 5], [250, 1960], [0.01, 0.034],
                                    [33, 1234567], [100, 100]])
def test_grid_lines_per_size(values):
    small = charts.layout(_data({'A': values}), ['A'], size=charts.SMALL)
    large = charts.layout(_data({'A': values}), ['A'], size=charts.LARGE)
    assert len(small.grid) == 3
    assert 4 <= len(large.grid) <= 6
    for view in (small, large):
        assert view.y_min == 0 and _is_nice(view.step) and view.y_max >= max(values)
        assert view.grid[0].zero and sum(g.zero for g in view.grid) == 1
        if max(values) > 0:
            assert view.y_max - view.step < max(values)          # on or just above the maximum


def test_negative_values_take_a_round_lower_bound():
    data = _data({'A': [2.1, -3.9, 6.2], 'B': [1.0, -5.6, 3.0]})
    for size in (charts.SMALL, charts.LARGE):
        view = charts.layout(data, ['A', 'B'], size=size)
        assert view.y_min <= -5.6 and view.y_min % view.step == 0 and view.y_max >= 6.2
        zero = [g for g in view.grid if g.zero]
        assert len(zero) == 1 and zero[0].y == pytest.approx(charts._SIZES[size]['bottom'] - (0 - view.y_min) / (
            view.y_max - view.y_min) * (charts._SIZES[size]['bottom'] - charts._SIZES[size]['top']), abs=0.2)
    assert len(charts.layout(data, ['A', 'B'], size=charts.SMALL).grid) == 3


# ── The x axis ───────────────────────────────────────────────────────────

def test_x_labels(sipri):
    small = charts.layout(sipri, KEYS, size=charts.SMALL, period=10)
    assert [x.label for x in small.x_labels] == ['2016', '2025']
    assert [x.align for x in small.x_labels] == ['start', 'end']
    for period in (5, 10, None):
        large = charts.layout(sipri, KEYS, size=charts.LARGE, period=period)
        labels = [int(x.label) for x in large.x_labels]
        assert len(labels) <= 6 and labels[-1] == 2025 == large.years[-1]
        steps = {b - a for a, b in zip(labels, labels[1:])}
        assert len(steps) == 1
    assert [x.label for x in charts.layout(sipri, KEYS, size=charts.LARGE, period=10).x_labels] == [
        '2016', '2019', '2022', '2025']
    for years in (list(range(2018, 2026)), list(range(1950, 2026)), list(range(2003, 2024))):
        labels = charts.x_label_years(years, charts.LARGE)
        assert len(labels) <= 6 and labels[-1] == years[-1]


def test_periods(sipri):
    assert charts.layout(sipri, KEYS, period=5).years == list(range(2021, 2026))
    assert charts.layout(sipri, KEYS, period=10).years == list(range(2016, 2026))
    assert charts.layout(sipri, KEYS, period=None).years == list(range(2010, 2026))
    # Years without any value of the shown series fall away.
    data = _data({'A': [None, 1.0, None, 2.0], 'B': [None, None, None, 3.0]})
    assert charts.layout(data, ['A', 'B']).years == [2016, 2018]


# ── Lines ────────────────────────────────────────────────────────────────

def test_a_gap_breaks_the_line():
    data = _data({'A': [1.0, 2.0, None, 2.5, 3.0], 'B': [1.0, None, 2.0, None, 1.5]})
    view = charts.layout(data, ['A', 'B'], size=charts.LARGE)
    a, b = view.lines
    assert len(a.segments) == 2 and a.dots == []
    assert a.segments[0].count(',') == 2 and a.segments[1].count(',') == 2
    assert b.segments == [] and len(b.dots) == 3                 # lonely points, never joined
    assert a.end == (100.0, a.end[1])


def test_colours_and_the_average(sipri):
    data = dict(sipri, series=dict(sipri['series'], OWID_EU27=sipri['series']['NLD']),
                names=dict(sipri['names'], OWID_EU27='European Union (27)'))
    keys = ['POL', 'FRA', 'DEU', 'NLD', 'ESP', 'USA', 'OWID_KOS']
    view = charts.layout(data, keys, 'OWID_EU27', charts.LARGE)
    assert [line.color for line in view.lines] == list(charts.COLORS[:7]) + ['#6c7683']
    assert charts.COLORS == ('#1b74b8', '#e09400', '#108a4e', '#7048c8', '#0f8b8d', '#b4541a', '#c2378a', '#5b6b7d')
    average = view.lines[-1]
    assert (average.key, average.dashed, average.width, average.dasharray) == ('OWID_EU27', True, 2, '6 5')
    assert all(not line.dashed and line.width == 2.5 for line in view.lines[:-1])
    small = charts.layout(data, keys, 'OWID_EU27', charts.SMALL)
    assert [line.key for line in small.lines] == ['POL', 'FRA', 'DEU', 'NLD', 'OWID_EU27']
    assert (small.lines[-1].width, small.lines[-1].dasharray, small.lines[0].width) == (1.75, '5 4', 2)
    assert small.legend[-1].dashed and small.legend[-1].color == '#6c7683'
    assert [g for g in view.grid if not g.zero] and charts.GRID_COLOR == '#e4e9ef' and charts.ZERO_COLOR == '#a7b1bd'


def test_the_most_series_per_size():
    series = {f'K{i}': [float(i), float(i + 1)] for i in range(12)}
    keys = list(series)
    assert [l.key for l in charts.layout(_data(series), keys, 'K11', charts.SMALL).lines] == ['K0', 'K1', 'K2', 'K3',
                                                                                             'K11']
    assert len(charts.layout(_data(series), keys, None, charts.LARGE).lines) == 8
    # Keys that are not in the data fall away; so does an unknown average.
    view = charts.layout(_data(series), ['XX', 'K0'], 'YY', charts.LARGE)
    assert [l.key for l in view.lines] == ['K0']


# ── End labels ───────────────────────────────────────────────────────────

def _assert_apart_and_inside(view):
    ys = sorted(e.y for e in view.end_labels)
    assert all(b - a >= charts.END_LABEL_GAP - 1e-6 for a, b in zip(ys, ys[1:]))
    assert ys[0] >= 0 and ys[-1] <= view.height - 2 * view.label_half


def test_end_labels_do_not_overlap(sipri):
    view = charts.layout(sipri, KEYS + ['ESP', 'USA'], size=charts.LARGE, period=10)
    assert len(view.end_labels) == 6
    _assert_apart_and_inside(view)
    near = _data({'A': [1.0, 2.00], 'B': [1.0, 2.01], 'C': [1.0, 1.99], 'D': [1.0, 0.0], 'E': [1.0, 0.01]})
    view = charts.layout(near, list('ABCDE'), size=charts.LARGE)
    _assert_apart_and_inside(view)
    # All at the bottom: the group moves up and stays inside the figure.
    low = _data({k: [5.0, 0.0] for k in 'ABCDEFGH'})
    view = charts.layout(low, list('ABCDEFGH'), size=charts.LARGE)
    assert len(view.end_labels) == 8
    _assert_apart_and_inside(view)
    assert charts.layout(near, list('ABC'), size=charts.SMALL).end_labels == []


# ── Hidden series ────────────────────────────────────────────────────────

def test_a_hidden_series_stays_in_the_legend_only(sipri):
    shown = charts.layout(sipri, KEYS, size=charts.LARGE, period=10)
    hidden = charts.layout(sipri, KEYS, size=charts.LARGE, period=10, hidden={'POL'})
    assert 'POL' not in [l.key for l in hidden.lines] and 'POL' not in [k for k, _n in hidden.table_columns]
    assert [i.key for i in hidden.legend] == KEYS and [i.hidden for i in hidden.legend] == [True, False, False, False]
    assert all(len(cells) == 3 for _year, cells in hidden.table_rows)
    assert 'POL' not in [e.key for e in hidden.end_labels]
    # The scale follows the visible series (Poland is the highest).
    assert hidden.y_max < shown.y_max
    # Colours stay with their country.
    assert [l.color for l in hidden.lines] == list(charts.COLORS[1:4])


# ── Numbers ──────────────────────────────────────────────────────────────

def test_percentages_per_language():
    # What Babel gives (checked): no space in nl and en, a no break space in de, es and fr.
    expected = {'nl': '4,1%', 'en': '4.1%', 'de': '4,1 %', 'es': '4,1 %', 'fr': '4,1 %'}
    assert {lang: charts.format_value(4.1, lang, '%') for lang in expected} == expected
    assert charts.format_value(0, 'nl', '%', 0) == '0%'
    assert charts.format_value(2.5, 'nl', '%', 1) == '2,5%'


def test_large_numbers_are_grouped_without_decimals():
    data = _data({'A': [1234567.0, 2345678.9]})
    view = charts.layout(data, ['A'], size=charts.LARGE, locale='nl')
    assert view.legend[0].value == '2.345.679'
    assert view.table_rows[0] == (2016, ['2.345.679'])
    assert charts.layout(data, ['A'], size=charts.LARGE, locale='en').legend[0].value == '2,345,679'
    assert charts.layout(data, ['A'], size=charts.LARGE, locale='fr').legend[0].value == '2 345 679'
    # A short unit goes after the number; a long one stays in the subtitle.
    assert charts.format_value(12.34, 'nl', 't', 1) == '12,3 t'
    assert charts.format_value(12.34, 'nl', 'tonnes', 1) == '12,3'
    assert charts.format_value(12.34, 'nl', '$', 1) == '12,3$'


def test_one_decimal_below_one_hundred_and_axis_labels_without_needless_zeros(sipri):
    view = charts.layout(sipri, KEYS, size=charts.SMALL, locale='nl', period=10, short_unit='%')
    assert view.legend[0].value == '4,5%'
    assert [g.label for g in view.grid] == ['0%', '2,5%', '5%']
    large = charts.layout(sipri, KEYS, size=charts.LARGE, locale='de', period=10, short_unit='%')
    assert [g.label for g in large.grid] == ['0 %', '1 %', '2 %', '3 %', '4 %', '5 %']


def test_the_table(sipri):
    data = _data({'A': [1.0, None, 3.0], 'B': [2.0, 2.5, None]}, names={'A': 'Alpha', 'B': 'Beta'})
    view = charts.layout(data, ['B'], 'A', charts.LARGE, names={'B': 'Bèta'})
    assert view.table_columns == [('B', 'Bèta'), ('A', 'Alpha')]           # the average last
    assert view.table_rows == [(2017, [None, '3,0']), (2016, ['2,5', None]), (2015, ['2,0', '1,0'])]


# ── The macros ───────────────────────────────────────────────────────────

def test_the_macros_render(app, sipri):
    from flask import render_template_string
    view_small = charts.layout(sipri, KEYS, size=charts.SMALL, period=10, short_unit='%')
    view_large = charts.layout(dict(sipri, series=dict(sipri['series'], X=[None] * len(sipri['years']))),
                               KEYS, size=charts.LARGE, period=10, hidden={'FRA'}, short_unit='%')
    gap = charts.layout(_data({'A': [1.0, None, 3.0, 2.0], 'B': [1.0, 2.0, None, 2.0]}), ['A', 'B'],
                        size=charts.LARGE)
    with app.test_request_context('/'):
        html = render_template_string(
            '{% from "charts/_chart.html" import chart_small, chart_large, chart_table %}'
            '{{ chart_small(s, "Kolom") }}{{ chart_large(l, "Groot") }}{{ chart_table(l, "Tabel", "Jaar", "leeg") }}'
            '{{ chart_large(g, "Gat") }}{{ chart_table(g, "Tabel", "Jaar", "geen waarde") }}',
            s=view_small, l=view_large, g=gap)
    assert html.count('<figure') == 3 and html.count('role="img"') == 3
    assert 'aria-label="Kolom"' in html and 'aria-label="Groot"' in html
    assert html.count('preserveAspectRatio="none"') == 3
    assert html.count('aria-hidden="true"') >= 9
    for tag in re.findall(r'<(?:polyline|line)\b[^>]*>', html):
        assert 'vector-effect="non-scaling-stroke"' in tag
    assert 'stroke-dasharray' not in html.split('aria-label="Groot"')[0]       # no average in the column
    assert '<span class="gu-sr-only">geen waarde</span>' in html
    assert 'Polen' not in html and 'Poland' in html
    assert '\u2013' not in html and '\u2014' not in html
    # Inline styles only for positions and the series colour.
    for style in re.findall(r'style="([^"]*)"', html):
        for decl in filter(None, (d.strip() for d in style.split(';'))):
            assert re.fullmatch(r'(top:-?[\d.]+px|left:-?[\d.]+%|--c:#[0-9a-f]{6})', decl), decl


def test_an_empty_chart():
    view = charts.layout(_data({'A': [None, None]}), ['A'])
    assert view.empty and view.lines == [] and view.legend == []
