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
