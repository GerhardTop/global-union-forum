"""Background figures, step 1c: a preview of the chart standard, for developers.

    python scripts/chart_preview.py [--screenshots]

Renders the charts of app/charts.py with the macros in
app/templates/charts/_chart.html and app/static/css/charts.css, using the
Our World in Data fixture in tests/data/owid/ (and regions.csv for an average
and a gap, plus made up series with negative values and with large numbers
in tonnes for the compact labels): both sizes in nl, de
and fr, with and without a hidden series, with period 5 and 10. Writes one
HTML file to $TMPDIR/guf-chart-preview/ and prints its path. With
--screenshots it also takes Playwright screenshots at 1280 and 375 px wide
(needs the playwright package and its Chromium).

Not part of the app or the test suite. Compare the result with
docs/design/achtergrondcijfers-kolom.html and -uitgeklapt.html.
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from jinja2 import Environment, FileSystemLoader  # noqa: E402

from app import charts  # noqa: E402
from app.sources import countries, owid  # noqa: E402

DATA = os.path.join(ROOT, 'tests', 'data', 'owid')
SLUG = 'military-spending-as-a-share-of-gdp-sipri'
OUT = os.path.join(tempfile.gettempdir(), 'guf-chart-preview')

# Fixed texts of the preview (the app passes translated texts from 1e on).
TEXTS = {
    'nl': ('Lijngrafiek', 'Tabelweergave', 'Jaar', 'geen waarde'),
    'de': ('Liniendiagramm', 'Tabelle', 'Jahr', 'kein Wert'),
    'fr': ('Graphique', 'Tableau', 'Année', 'aucune valeur'),
}


def _read(name):
    with open(os.path.join(DATA, name), encoding='utf-8') as f:
        return f.read()


def _country_names(lang):
    with open(os.path.join(ROOT, 'app', 'data', 'landenlijst_data.json'), encoding='utf-8') as f:
        by_iso2 = {row['iso']: row['names'] for row in json.load(f)}

    def name(key):
        iso2 = countries.iso2_for(key)
        names = by_iso2.get(iso2) or {}
        return names.get(lang) or names.get('en')
    return name


def _data():
    meta = json.loads(_read(f'{SLUG}.metadata.json'))
    sipri = owid.parse(_read(f'{SLUG}.csv'), meta)
    regions = owid.parse(_read('regions.csv'), {})
    negative = {'years': list(range(2014, 2025)),
                'series': {'NLD': [1.9, 2.2, 2.1, 2.9, 2.4, 2.0, -3.9, 6.2, 5.0, 0.1, 0.9],
                           'DEU': [2.2, 1.5, 2.2, 2.7, 1.0, 1.1, -4.1, 3.7, 1.4, -0.3, -0.2],
                           'OWID_EU27': [1.6, 2.3, 2.0, 2.8, 2.1, 1.8, -5.6, 6.0, 3.5, 0.4, 1.0]},
                'names': {'NLD': 'Netherlands', 'DEU': 'Germany', 'OWID_EU27': 'European Union (27)'}}
    large = {'years': list(range(2014, 2024)),
             'series': {'DEU': [902e6, 896e6, 905e6, 889e6, 856e6, 799e6, 718e6, 760e6, 734e6, 674e6],
                        'POL': [301e6, 307e6, 318e6, 330e6, 328e6, 305e6, 289e6, 322e6, 318e6, 287e6],
                        'FRA': [313e6, 318e6, 323e6, 321e6, 309e6, 302e6, 266e6, 293e6, 286e6, 268e6],
                        'NLD': [168e6, 176e6, 177e6, 174e6, 169e6, 160e6, 145e6, 146e6, 138e6, 125e6]},
             'names': {'DEU': 'Germany', 'POL': 'Poland', 'FRA': 'France', 'NLD': 'Netherlands'}}
    return (owid.snapshot_data(sipri, 2010, 2025), owid.snapshot_data(regions, 2015, 2022), negative,
            large)


def _legend(view):
    items = ''.join(
        f'<li><span class="gu-chart-swatch{" gu-chart-swatch--average" if i.dashed else ""}" style="--c:{i.color}">'
        f'</span>{i.name}{" (hidden)" if i.hidden else ""}<b class="gu-chart-legend__value">{i.value}</b></li>'
        for i in view.legend)
    return f'<ul class="gu-chart-legend">{items}</ul>'


def build():
    env = Environment(loader=FileSystemLoader(os.path.join(ROOT, 'app', 'templates')), autoescape=True)
    macros = env.get_template('charts/_chart.html').module
    sipri, regions, negative, large = _data()
    keys = ['POL', 'FRA', 'DEU', 'NLD']
    css = [os.path.join(ROOT, 'app', 'static', 'css', n) for n in ('colors_and_type.css', 'charts.css')]
    parts = []
    for lang in ('nl', 'de', 'fr'):
        label, caption, year, empty = TEXTS[lang]
        names = {k: _country_names(lang)(k) for k in sipri['series']}
        cases = [
            ('kolom, 10 jaar', charts.layout(sipri, keys, None, charts.SMALL, lang, 10, (), names, '%')),
            ('kolom, 10 jaar, Frankrijk verborgen',
             charts.layout(sipri, keys, None, charts.SMALL, lang, 10, {'FRA'}, names, '%')),
        ]
        for title, view in cases:
            parts.append(f'<section class="pv-small"><h2>{lang}: {title}</h2>{macros.chart_small(view, label)}'
                         f'{_legend(view)}</section>')
        for title, period, hidden in (('groot, 10 jaar', 10, ()), ('groot, 5 jaar', 5, ()),
                                      ('groot, 10 jaar, Polen verborgen', 10, {'POL'})):
            view = charts.layout(sipri, keys + ['ESP', 'USA'], None, charts.LARGE, lang, period, hidden, names, '%')
            parts.append(f'<section class="pv-large"><h2>{lang}: {title}</h2>{macros.chart_large(view, label)}'
                         f'{_legend(view)}{macros.chart_table(view, caption, year, empty)}</section>')
    view = charts.layout(regions, ['NLD', 'name:Europe (SIPRI)'], 'OWID_WRL', charts.LARGE, 'nl', None, (), {}, '%')
    parts.append(f'<section class="pv-large"><h2>nl: groot, gemiddelde (World) en een gat (NLD 2021)</h2>'
                 f'{macros.chart_large(view, "Lijngrafiek")}{_legend(view)}'
                 f'{macros.chart_table(view, "Tabelweergave", "Jaar", "geen waarde")}</section>')
    for size, macro in ((charts.LARGE, macros.chart_large), (charts.SMALL, macros.chart_small)):
        view = charts.layout(negative, ['NLD', 'DEU'], 'OWID_EU27', size, 'nl', None, (), {}, '%')
        parts.append(f'<section class="pv-{"large" if size == charts.LARGE else "small"}"><h2>nl: {size}, '
                     f'negatieve waarden (verzonnen)</h2>{macro(view, "Lijngrafiek")}{_legend(view)}</section>')
    for lang in ('nl', 'de', 'fr'):
        names = {k: _country_names(lang)(k) for k in large['series']}
        for size, macro in ((charts.LARGE, macros.chart_large), (charts.SMALL, macros.chart_small)):
            view = charts.layout(large, ['DEU', 'POL', 'FRA', 'NLD'], None, size, lang, None, (), names, 't')
            table = macros.chart_table(view, TEXTS[lang][1], TEXTS[lang][2], TEXTS[lang][3]) \
                if size == charts.LARGE else ''
            parts.append(f'<section class="pv-{"large" if size == charts.LARGE else "small"}"><h2>{lang}: {size}, '
                         f'grote getallen in tonnen (verzonnen)</h2>{macro(view, TEXTS[lang][0])}{_legend(view)}'
                         f'{table}</section>')
    links = ''.join(f'<link rel="stylesheet" href="file://{p}">' for p in css)
    html = f"""<!doctype html>
<html lang="nl"><head><meta charset="utf-8"><title>Chart preview</title>{links}
<style>
body{{margin:0;background:#eef1f5;font-family:var(--gu-font-sans);color:#141b22}}
main{{display:flex;flex-wrap:wrap;gap:24px;padding:24px;align-items:flex-start}}
section{{background:#fff;border:1px solid #e4e9ef;border-radius:14px;padding:16px;display:flex;flex-direction:column;gap:12px;box-sizing:border-box}}
.pv-small{{inline-size:284px;max-inline-size:100%}}
.pv-large{{inline-size:800px;max-inline-size:100%}}
h2{{margin:0;font-size:13px;font-weight:600;color:#1b74b8}}
.gu-sr-only{{position:absolute;inline-size:1px;block-size:1px;overflow:hidden;clip:rect(0,0,0,0)}}
</style></head><body><main>{''.join(parts)}</main></body></html>"""
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, 'index.html')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html)
    return path


def screenshots(path):
    from playwright.sync_api import sync_playwright
    out = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for width in (1280, 375):
            page = browser.new_page(viewport={'width': width, 'height': 900})
            page.goto(f'file://{path}')
            page.wait_for_timeout(800)
            shot = os.path.join(OUT, f'preview-{width}.png')
            page.screenshot(path=shot, full_page=True)
            out.append(shot)
        browser.close()
    return out


if __name__ == '__main__':
    result = build()
    print(result)
    if '--screenshots' in sys.argv:
        for shot in screenshots(result):
            print(shot)
