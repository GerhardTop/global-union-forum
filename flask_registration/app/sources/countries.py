"""Entity keys of a source to the ISO2 codes of our own country list
(app/data/landenlijst_data.json). Our World in Data uses ISO3 codes for
countries (NLD) and codes of its own for regions (OWID_WRL) and for Kosovo
(OWID_KOS). app/data/iso3_iso2.json was generated once from pycountry, plus
OWID_KOS; pycountry is no runtime dependency."""
import json
import os
from functools import lru_cache

_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')


@lru_cache(maxsize=1)
def _iso3_iso2():
    with open(os.path.join(_DATA, 'iso3_iso2.json'), encoding='utf-8') as f:
        return json.load(f)


@lru_cache(maxsize=1)
def _our_countries():
    with open(os.path.join(_DATA, 'landenlijst_data.json'), encoding='utf-8') as f:
        return frozenset(row['iso'] for row in json.load(f))


def iso2_for(key):
    """The ISO2 code of an entity key, only when it is a country of our own
    list; else None (regions, averages, countries we do not list)."""
    iso2 = _iso3_iso2().get(key or '')
    return iso2 if iso2 in _our_countries() else None


def iso3_for(iso2):
    """The entity key of one of our countries (for the tests and step 2)."""
    for iso3, code in _iso3_iso2().items():
        if code == iso2:
            return iso3
    return None


@lru_cache(maxsize=1)
def _our_rows():
    """{iso2: {lang: name}} of our country list."""
    with open(os.path.join(_DATA, 'landenlijst_data.json'), encoding='utf-8') as f:
        return {row['iso']: dict(row.get('names') or {}) for row in json.load(f)}


def english_name(iso2):
    """The English name of one of our countries, as our list writes it."""
    return (_our_rows().get(iso2) or {}).get('en')


def display_name(iso2, lang):
    """The name of one of our countries in `lang`: our own list first (every
    country in nl and en, the sensitive areas in every enabled language),
    else Babel's territory name, else English."""
    names = _our_rows().get(iso2) or {}
    own = names.get(lang)
    if own:
        return own
    try:
        from babel import Locale
        babel_name = Locale.parse(lang).territories.get(iso2)
    except Exception:   # an unknown locale: fall back
        babel_name = None
    return babel_name or names.get('en') or iso2
