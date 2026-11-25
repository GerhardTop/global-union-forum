"""Background figures, step 1b: the adapter for Our World in Data (OWID).

fetch(slug) gets the chart's CSV and metadata from the grapher API;
parse(csv_text, metadata) turns them into a ParsedChart (pure, no network);
snapshot_data(parsed, year_from, year_to) makes the data of a ChartSnapshot
(all entities over the period); create_snapshot makes the snapshot itself.
Nothing here calls the AI, starts a background task or touches a page. Spec:
docs/achtergrondcijfers.md (Bronnen, Momentopname en stemmen).

What the format looks like (checked live, 25 November 2026):
- The CSV with useColumnShortNames=true has the header
  entity,code,year,<short name>[,owid_region]: lower case, the data column by
  its short name (share_gdp), and often an extra column owid_region (the
  continent of a country, metadata type "Continent"). That column is an
  annotation, not data; it is skipped.
- The metadata keys its columns by their long title, with the short name in
  "shortName"; a column is found by either.
- Countries have their ISO3 code (NLD), regions and Kosovo a code of OWID's
  own (OWID_WRL, OWID_EU27, OWID_KOS); some regions have an empty code.
- An old slug answers with a redirect to the current one (requests follows
  it); the current address is in chart.originalChartUrl.
"""
import csv
import io
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime

import requests

from app.sources import SourceError

BASE = 'https://ourworldindata.org/grapher/'
USER_AGENT = 'GlobalUnionForum/1.0 (+https://globalunionforum.org)'
TIMEOUT = 20
SLUG_RE = re.compile(r'^[a-z0-9-]{1,200}$')
DEFAULT_LICENSE = 'CC BY 4.0'

_KEY_COLUMNS = ('entity', 'code', 'year')
# Columns that annotate an entity instead of measuring something (metadata type).
_ANNOTATION_TYPES = ('Continent',)
_ANNOTATION_COLUMNS = ('owid_region',)


@dataclass
class ParsedChart:
    title: str = ''
    subtitle: str = ''
    unit: str = ''
    short_unit: str = ''
    definition: str = ''
    citation: str = ''
    citation_short: str = ''
    source_url: str = ''
    column_name: str = ''
    years: list = field(default_factory=list)       # every year with at least one value, sorted
    series: dict = field(default_factory=dict)      # {key: {year: value}}
    names: dict = field(default_factory=dict)       # {key: name in the CSV (English)}


def csv_url(slug):
    return f'{BASE}{slug}.csv?csvType=full&useColumnShortNames=true'


def metadata_url(slug):
    return f'{BASE}{slug}.metadata.json'


def entity_key(name, code):
    """The OWID code (NLD, OWID_WRL) when there is one, else "name:" + the name."""
    code = (code or '').strip()
    return code if code else f'name:{name.strip()}'


# ── Fetching ─────────────────────────────────────────────────────────────

def _get(http, url):
    try:
        resp = http.get(url, headers={'User-Agent': USER_AGENT}, timeout=TIMEOUT)
    except requests.RequestException as exc:   # time-out, connection, too many redirects
        raise SourceError('network', f'{type(exc).__name__}: {url}') from exc
    status = resp.status_code
    if status in (404, 410):
        raise SourceError('not_found', url)
    if status in (401, 403):
        raise SourceError('forbidden', url)
    if status != 200:
        raise SourceError('network', f'HTTP {status}: {url}')
    return resp


def fetch(slug, http=None):
    """(csv_text, metadata_dict) of one chart. No request at all for an
    invalid slug. http: anything with .get like requests (for the tests)."""
    if not isinstance(slug, str) or not SLUG_RE.match(slug):
        raise SourceError('invalid_slug', repr(slug))
    http = http or requests
    csv_text = _get(http, csv_url(slug)).content.decode('utf-8-sig')
    resp = _get(http, metadata_url(slug))
    try:
        metadata = json.loads(resp.content.decode('utf-8'))
    except ValueError as exc:
        raise SourceError('unsupported', 'metadata is no JSON') from exc
    if not isinstance(metadata, dict):
        raise SourceError('unsupported', 'metadata is no object')
    return csv_text, metadata


# ── Searching ────────────────────────────────────────────────────────────

SEARCH_URL = 'https://ourworldindata.org/api/search'


@dataclass
class SearchHit:
    slug: str
    title: str
    subtitle: str
    entities: list      # availableEntities: English names


def search(query, countries=None, hits=20, http=None):
    """Charts that match `query` in the Search API of OWID (version 0.1.0,
    still in development): only type 'chart' (no explorer views or
    multi-dimensional views). countries: English entity names that the
    charts must have (OWID joins them with ~). A failure or an answer of an
    unexpected shape is a SourceError ('network' or 'unsupported')."""
    params = {'q': query, 'type': 'charts', 'hitsPerPage': int(hits)}
    if countries:
        params['countries'] = '~'.join(countries)
    http = http or requests
    try:
        resp = http.get(SEARCH_URL, params=params, headers={'User-Agent': USER_AGENT}, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise SourceError('network', f'{type(exc).__name__}: search') from exc
    if resp.status_code != 200:
        raise SourceError('network', f'HTTP {resp.status_code}: search')
    try:
        answer = json.loads(resp.content.decode('utf-8'))
    except ValueError as exc:
        raise SourceError('unsupported', 'search answer is no JSON') from exc
    results = answer.get('results') if isinstance(answer, dict) else None
    if not isinstance(results, list):
        raise SourceError('unsupported', 'search answer without results')
    out = []
    for row in results:
        if not isinstance(row, dict) or row.get('type') != 'chart':
            continue
        slug = row.get('slug')
        if not isinstance(slug, str) or not SLUG_RE.match(slug):
            continue
        entities = row.get('availableEntities')
        out.append(SearchHit(slug=slug, title=str(row.get('title') or ''), subtitle=str(row.get('subtitle') or ''),
                             entities=[e for e in entities if isinstance(e, str)] if isinstance(entities, list) else []))
    return out


# ── Parsing ──────────────────────────────────────────────────────────────

def _column_meta(metadata, column_name):
    columns = metadata.get('columns') or {}
    if column_name in columns:
        return columns[column_name]
    for meta in columns.values():
        if isinstance(meta, dict) and meta.get('shortName') == column_name:
            return meta
    return {}


def _number(cell):
    try:
        value = float(cell)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def parse(csv_text, metadata, slug=None):
    """A ParsedChart from the CSV and metadata of one chart. Pure: no network.
    Exactly one data column next to entity, code and year (annotation columns
    such as owid_region aside), else 'unsupported' (also for day instead of
    year); empty or non-numeric cells are skipped; no cells at all is
    'no_data'."""
    reader = csv.reader(io.StringIO(csv_text or ''))
    header = next(reader, None)
    if not header:
        raise SourceError('no_data', 'empty CSV')
    lower = [h.strip().lower() for h in header]
    if 'day' in lower:
        raise SourceError('unsupported', 'daily data (day instead of year)')
    if any(k not in lower for k in _KEY_COLUMNS):
        raise SourceError('unsupported', f'columns {header}')
    data_cols = [i for i, h in enumerate(lower) if h not in _KEY_COLUMNS and h not in _ANNOTATION_COLUMNS
                 and _column_meta(metadata, header[i]).get('type') not in _ANNOTATION_TYPES]
    if len(data_cols) != 1:
        raise SourceError('unsupported', f'{len(data_cols)} data columns')
    col = data_cols[0]
    i_entity, i_code, i_year = (lower.index(k) for k in _KEY_COLUMNS)

    series, names = {}, {}
    for row in reader:
        if len(row) <= max(col, i_entity, i_code, i_year):
            continue
        value, year = _number(row[col]), _number(row[i_year])
        if value is None or year is None or not row[i_entity].strip():
            continue
        key = entity_key(row[i_entity], row[i_code])
        series.setdefault(key, {})[int(year)] = value
        names.setdefault(key, row[i_entity].strip())
    if not series:
        raise SourceError('no_data', 'no values')

    column_name = header[col]
    meta = _column_meta(metadata, column_name)
    chart = metadata.get('chart') or {}
    source_url = chart.get('originalChartUrl') or (f'{BASE}{slug}' if slug else '')
    return ParsedChart(
        title=chart.get('title') or meta.get('titleShort') or '',
        subtitle=chart.get('subtitle') or '',
        unit=meta.get('unit') or '',
        short_unit=meta.get('shortUnit') or '',
        definition=meta.get('descriptionShort') or '',
        citation=chart.get('citation') or '',
        citation_short=meta.get('citationShort') or '',
        source_url=source_url,
        column_name=column_name,
        years=sorted({y for values in series.values() for y in values}),
        series=series,
        names=names,
    )


# ── Helpers for the selection (step 1d) ──────────────────────────────────

def last_year(parsed):
    """The last year with at least one value."""
    return parsed.years[-1] if parsed.years else None


def has_values(parsed, key, year_from, year_to):
    """Whether entity `key` has at least one value in the period."""
    return any(year_from <= y <= year_to for y in parsed.series.get(key, {}))


# ── Snapshot ─────────────────────────────────────────────────────────────

def snapshot_data(parsed, year_from, year_to):
    """The data of a ChartSnapshot: the years in the period in which at least
    one entity has a value; for EVERY entity with a value in the period a row
    of values (None where a year is missing); the names. An empty period is
    'no_data'."""
    keys = [k for k in sorted(parsed.series) if has_values(parsed, k, year_from, year_to)]
    years = sorted({y for k in keys for y in parsed.series[k] if year_from <= y <= year_to})
    if not years:
        raise SourceError('no_data', f'no values in {year_from} to {year_to}')
    return {
        'years': years,
        'series': {k: [parsed.series[k].get(y) for y in years] for k in keys},
        'names': {k: parsed.names[k] for k in keys},
    }


def create_snapshot(chart, parsed, metadata_raw, year_from, year_to):
    """A new ChartSnapshot (fetched now) for `chart`: version 1, or the next
    version when the chart has one already. Never overwrites a snapshot (a
    frozen one stays as it is). No commit."""
    from app.models import ChartSnapshot
    if not isinstance(metadata_raw, str):
        metadata_raw = json.dumps(metadata_raw, ensure_ascii=False)
    version = max((s.version or 0 for s in chart.snapshots), default=0) + 1 if chart.id is not None else 1
    snapshot = ChartSnapshot(
        version=version, fetched_at=datetime.utcnow(),
        title=parsed.title[:300], subtitle=parsed.subtitle, unit=parsed.unit[:120],
        short_unit=parsed.short_unit[:40], definition=parsed.definition, citation=parsed.citation,
        citation_short=parsed.citation_short, source_url=parsed.source_url[:500], license=DEFAULT_LICENSE,
        data=json.dumps(snapshot_data(parsed, year_from, year_to)), metadata_raw=metadata_raw)
    chart.snapshots.append(snapshot)
    return snapshot
