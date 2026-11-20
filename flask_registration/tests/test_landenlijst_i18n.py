"""Multilingual phase 0 step 6e: the country list from ISO codes.

Names come from Babel per language (with the site's own names where it
uses one), the level and 'invite' are codes with gettext labels, remarks
per language with English as the fallback, downloads per language with the
English files as the fallback.
"""
import importlib
import json
import os

from babel import Locale

# The module (app.routes.main as an attribute is the blueprint).
main_routes = importlib.import_module('app.routes.main')

DATA = os.path.join(os.path.dirname(__file__), '..', 'app', 'data', 'landenlijst_data.json')


def _rows():
    with open(DATA, encoding='utf-8') as f:
        return json.load(f)


def test_every_country_has_a_known_iso_code():
    rows = _rows()
    codes = [r['iso'] for r in rows]
    assert len(rows) == 183 and len(set(codes)) == 183
    english = Locale('en').territories
    assert all(code in english for code in codes)
    assert all(r['niveau'] in main_routes.LEVEL_LABELS and r['uitnodigen'] in main_routes.INVITE_LABELS for r in rows)
    assert not any(k.endswith(('_nl', '_en')) for r in rows for k in r)


def test_names_per_language():
    by_iso = {r['iso']: r for r in _rows()}
    assert main_routes.country_name(by_iso['NO'], 'nl') == 'Noorwegen'
    assert main_routes.country_name(by_iso['NO'], 'de') == 'Norwegen'      # any language via Babel
    assert main_routes.country_name(by_iso['PS'], 'nl') == 'Palestina'      # the site's own name
    assert main_routes.country_name(by_iso['PS'], 'en') == 'Palestine'
    assert main_routes.country_name(by_iso['CZ'], 'en') == 'Czech Republic'
    assert main_routes.country_name(by_iso['NO'], 'xx') == 'Norway'         # unknown: English


def test_remarks_and_downloads_fall_back_to_english():
    taiwan = next(r for r in _rows() if r['iso'] == 'TW')
    assert main_routes.country_remark(taiwan, 'nl').startswith('Geen VN-erkenning')
    assert main_routes.country_remark(taiwan, 'de').startswith('No UN recognition')
    assert main_routes.landen_downloads('nl') == ('downloads/bijlage_a_landenoverzicht_NL.xlsx', 'downloads/landenlijst.pdf')
    assert main_routes.landen_downloads('de') == main_routes.landen_downloads('en')


def test_page_in_both_languages(app):
    for lang, name, level, xlsx in (('nl', 'Noorwegen', 'Op niveau', 'bijlage_a_landenoverzicht_NL.xlsx'),
                                    ('en', 'Norway', 'Meets standard', 'appendix_a_country_overview_EN.xlsx')):
        client = app.test_client()
        with client.session_transaction() as s:
            s['lang'] = lang
        from flask_babel import refresh
        refresh()
        html = client.get('/manifest/landen').get_data(as_text=True)
        assert name in html and level in html and xlsx in html
