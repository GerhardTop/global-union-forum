"""Background figures, step 3a: the adapter for Eurostat (docs/achtergrondcijfers.md,
Bronnen punt 2).

refresh_catalog() keeps a local catalogue of Eurostat's main tables (type
"table" in the table of contents); search_catalog() searches it. fetch(code)
gets one table as JSON-stat 2.0 from the Statistics API; parse(jsonstat,
code) turns it into a ParsedChart, the same type as for Our World in Data,
so snapshot_data and create_snapshot of app/sources/owid.py work for both.
Nothing here calls the AI or touches a page; the choice (3b) and the display
(3c) come later.

What the format looks like (checked live, 26 November 2026):
- The table of contents (catalogue/toc/txt?lang=en) is tab separated, every
  field between double quotes, with the header "title" "code" "type" "last
  update of data" "last table structure change" "data start" "data end"
  "values". The title is indented with spaces to show the tree. Types are
  folder, dataset and table; the same table often appears in several
  folders (1543 table lines, 947 codes). Dates of the last update as
  dd.mm.yyyy; data start and end as 2025, 2026-09, 2026-Q3 or 2025-S1, and
  a single space when unknown.
- The data endpoint answers JSON-stat 2.0: id (the dimensions in order,
  geo and time near the end), size, dimension (per id a label and
  category.index and category.label), value and status as objects keyed by
  the flat position (row major over id), a missing position is no value.
  status holds Eurostat's flags (b break in series, e estimated, p
  provisional, d definition differs, ...), their labels in
  extension.status.label. extension.description is the definition, at
  times as HTML. updated is the time of the last update.
- Countries have Eurostat's ISO2 codes, with EL for Greece and UK for the
  United Kingdom; aggregates have codes of their own (EU27_2020, EA20).
- An unknown code answers 404 with {"error": [...]}.
"""
import csv
import io
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import requests

from app.sources import SourceError
from app.sources.owid import USER_AGENT, TIMEOUT, ParsedChart

DATA_URL = 'https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/'
TOC_URL = 'https://ec.europa.eu/eurostat/api/dissemination/catalogue/toc/txt?lang=en'
BROWSER_URL = 'https://ec.europa.eu/eurostat/databrowser/view/{code}/default/table'
CODE_RE = re.compile(r'^[a-z0-9_]{1,40}$', flags=re.I)
MAX_BYTES = 20 * 1024 * 1024
LICENSE = 'CC BY 4.0'
CATALOG_MAX_AGE = timedelta(days=7)
CATALOG_MAX_DATA_AGE_YEARS = 5
AGGREGATE_PREFIX = 'eurostat:'
# Eurostat's own ISO2 codes that differ from ISO 3166.
_GEO_ISO2 = {'EL': 'GR', 'UK': 'GB'}
_SKIP_DIMS = ('geo', 'time')
_HTML_TAG = re.compile(r'<[^>]+>')


class NeedsChoice(SourceError):
    """More than one value in a dimension (step 3b chooses one per dimension)."""

    def __init__(self, dimensions):
        super().__init__('needs_choice', ', '.join(d.id for d in dimensions if len(d.values) > 1))
        self.dimensions = dimensions


@dataclass
class Dimension:
    id: str
    label: str
    values: list = field(default_factory=list)      # [(code, label)]


@dataclass
class CatalogHit:
    code: str
    title: str
    data_start: str
    data_end: str


# ── The catalogue ────────────────────────────────────────────────────────

def _date(text):
    try:
        return datetime.strptime((text or '').strip(), '%d.%m.%Y').date()
    except ValueError:
        return None


def parse_toc(text):
    """The main tables of the table of contents: [dict], one per code (the
    first time it appears). Raises SourceError('unsupported') when the
    header is not the one we know."""
    reader = csv.reader(io.StringIO(text or ''), delimiter='\t', quotechar='"')
    header = [h.strip().lower() for h in next(reader, [])]
    needed = ('title', 'code', 'type', 'last update of data', 'data start', 'data end')
    if any(n not in header for n in needed):
        raise SourceError('unsupported', f'table of contents header {header}')
    at = {n: header.index(n) for n in needed}
    tables, seen = [], set()
    for row in reader:
        if len(row) <= max(at.values()):
            continue
        code = row[at['code']].strip()
        if row[at['type']].strip() != 'table' or not code or code in seen:
            continue
        seen.add(code)
        tables.append({'code': code[:40], 'title': row[at['title']].strip()[:500], 'type': 'table',
                       'last_update': _date(row[at['last update of data']]),
                       'data_start': row[at['data start']].strip()[:10] or None,
                       'data_end': row[at['data end']].strip()[:10] or None})
    return tables


def refresh_catalog(http=None, now=None):
    """Replace the catalogue by the main tables of the table of contents, in
    one transaction. The number of tables, or None when fetching or reading
    failed (the old catalogue stays)."""
    from flask import current_app
    from app import db
    from app.models import EurostatCatalog
    http = http or requests
    now = now or datetime.utcnow()
    try:
        resp = http.get(TOC_URL, headers={'User-Agent': USER_AGENT}, timeout=TIMEOUT * 3)
        if resp.status_code != 200:
            raise SourceError('network', f'HTTP {resp.status_code}: {TOC_URL}')
        tables = parse_toc(resp.content.decode('utf-8-sig'))
        if not tables:
            raise SourceError('no_data', 'no tables in the table of contents')
    except (requests.RequestException, SourceError, UnicodeDecodeError) as exc:
        current_app.logger.warning('Eurostat catalogue not refreshed: %s', exc)
        return None
    try:
        EurostatCatalog.query.delete()
        db.session.add_all(EurostatCatalog(refreshed_at=now, **t) for t in tables)
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Eurostat catalogue not saved')
        return None
    current_app.logger.info('Eurostat catalogue: %d main tables', len(tables))
    return len(tables)


def catalog_due(now=None):
    """Whether the catalogue is empty or older than seven days."""
    from app import db
    from app.models import EurostatCatalog
    last = db.session.query(db.func.max(EurostatCatalog.refreshed_at)).scalar()
    return last is None or last < (now or datetime.utcnow()) - CATALOG_MAX_AGE


_WORD = re.compile(r'[a-z0-9]+')


def _words(text):
    return set(_WORD.findall((text or '').lower()))


def search_catalog(terms, limit=20, now=None):
    """Main tables whose English title has the most words of the terms (case
    insensitive, whole words); on a tie the title with fewer other words
    first ("Unemployment rate" before "Long-term unemployment rate by sex"),
    then by title. Only tables whose data end at most five years ago. terms:
    a string or a list of strings."""
    from app.models import EurostatCatalog
    if isinstance(terms, str):
        terms = [terms]
    wanted = set().union(*(_words(t) for t in terms or [])) if terms else set()
    if not wanted:
        return []
    oldest = (now or datetime.utcnow()).year - CATALOG_MAX_DATA_AGE_YEARS
    scored = []
    for row in EurostatCatalog.query:
        year = (row.data_end or '')[:4]
        if not year.isdigit() or int(year) < oldest:
            continue
        words = _words(row.title)
        hits = len(wanted & words)
        if hits:
            scored.append((-hits, len(words), row.title.lower(), row.code, row))
    scored.sort()
    return [CatalogHit(code=r.code, title=r.title, data_start=r.data_start or '', data_end=r.data_end or '')
            for *_k, r in scored[:limit]]


# ── Fetching ─────────────────────────────────────────────────────────────

def data_url(code):
    return f'{DATA_URL}{code}'


def fetch(code, filters=None, since=None, http=None):
    """The JSON-stat of one table (format JSON, English labels). filters:
    {dimension: value or [values]}; since: the first period (sinceTimePeriod).
    No request at all for an invalid code."""
    if not isinstance(code, str) or not CODE_RE.match(code):
        raise SourceError('invalid_slug', repr(code))
    params = [('format', 'JSON'), ('lang', 'EN')]
    if since is not None:
        params.append(('sinceTimePeriod', str(since)))
    for dim, values in (filters or {}).items():
        for value in values if isinstance(values, (list, tuple)) else [values]:
            params.append((dim, str(value)))
    url = data_url(code)
    http = http or requests
    try:
        resp = http.get(url, params=params, headers={'User-Agent': USER_AGENT}, timeout=TIMEOUT)
    except requests.RequestException as exc:   # time-out, connection
        raise SourceError('network', f'{type(exc).__name__}: {url}') from exc
    status = resp.status_code
    if status in (404, 410):
        raise SourceError('not_found', url)
    if status == 400:   # an unknown dimension value: the table exists, the choice does not
        raise SourceError('not_found', f'HTTP 400: {url}')
    if status in (401, 403):
        raise SourceError('forbidden', url)
    if status != 200:
        raise SourceError('network', f'HTTP {status}: {url}')
    if len(resp.content or b'') > MAX_BYTES:
        raise SourceError('unsupported', f'more than {MAX_BYTES} bytes')
    try:
        data = json.loads(resp.content.decode('utf-8'))
    except ValueError as exc:
        raise SourceError('unsupported', 'no JSON') from exc
    if not isinstance(data, dict) or 'id' not in data or 'dimension' not in data:
        raise SourceError('unsupported', 'no JSON-stat dataset')
    return data


# ── Reading ──────────────────────────────────────────────────────────────

def _labels(data, dim):
    """[(code, label)] of a dimension in the order of its index."""
    category = (data.get('dimension', {}).get(dim) or {}).get('category') or {}
    index = category.get('index') or {}
    labels = category.get('label') or {}
    if isinstance(index, list):
        codes = index
    else:
        codes = sorted(index, key=lambda c: index[c]) if index else list(labels)
    return [(c, labels.get(c, c)) for c in codes]


def _structure(data):
    ids, sizes = data.get('id'), data.get('size')
    if not isinstance(ids, list) or not isinstance(sizes, list) or len(ids) != len(sizes):
        raise SourceError('unsupported', 'no JSON-stat dimensions')
    if 'geo' not in ids or 'time' not in ids:
        raise SourceError('unsupported', f'dimensions {ids} without geo or time')
    return ids, sizes


def describe_dimensions(data):
    """The dimensions apart from geo and time: [Dimension] with the list of
    (code, label). A dimension freq is reduced to A (annual); a table
    without annual data is 'unsupported' (only yearly figures for now)."""
    ids, _sizes = _structure(data)
    out = []
    for dim in ids:
        if dim in _SKIP_DIMS:
            continue
        values = _labels(data, dim)
        if dim == 'freq':
            values = [v for v in values if v[0] == 'A']
            if not values:
                raise SourceError('unsupported', 'no annual data (freq without A)')
        label = (data['dimension'].get(dim) or {}).get('label') or dim
        out.append(Dimension(id=dim, label=label, values=values))
    return out


def entity_key(geo):
    """A country of our list as its ISO3 code (as OWID writes it, EL and UK
    as GRC and GBR); anything else as "eurostat:<code>" (EU27_2020)."""
    from app.sources import countries
    iso2 = _GEO_ISO2.get(geo, geo)
    if len(iso2) == 2:
        iso3 = countries.iso3_for(iso2)
        if iso3:
            return iso3
    return f'{AGGREGATE_PREFIX}{geo}'


def _definition(data, title):
    text = ((data.get('extension') or {}).get('description') or '').strip()
    text = re.sub(r'\s+', ' ', _HTML_TAG.sub(' ', text)).strip()
    return text or title


def _updated(data):
    raw = (data.get('updated') or '').strip()
    return raw[:10] if re.match(r'\d{4}-\d{2}-\d{2}', raw) else raw


def _short_unit(code, label):
    if (code or '').upper().startswith('PC') or (label or '').lower().startswith('percentage'):
        return '%'
    return ''


def _cell(container, position):
    if isinstance(container, dict):
        return container.get(str(position))
    if isinstance(container, list) and position < len(container):
        return container[position]
    return None


def parse(data, code):
    """A ParsedChart from the JSON-stat of one table. Every dimension apart
    from geo and time must have exactly one value (freq reduced to A), else
    NeedsChoice (reason 'needs_choice') with the dimensions; yearly periods
    only. Values that are missing are skipped; no values at all is
    'no_data'. flags: {key: {year: flag}} where Eurostat gives one."""
    ids, sizes = _structure(data)
    dims = describe_dimensions(data)
    if any(len(d.values) != 1 for d in dims):
        raise NeedsChoice(dims)
    # The position of the one value per dimension (freq A may not be the first).
    fixed = {d.id: [c for c, _l in _labels(data, d.id)].index(d.values[0][0]) for d in dims}
    strides, step = [0] * len(ids), 1
    for i in range(len(ids) - 1, -1, -1):
        strides[i] = step
        step *= sizes[i]
    geos, times = _labels(data, 'geo'), _labels(data, 'time')
    i_geo, i_time = ids.index('geo'), ids.index('time')
    base = sum(fixed[d] * strides[i] for i, d in enumerate(ids) if d in fixed)
    years = {}
    for t, (period, _label) in enumerate(times):
        if not re.fullmatch(r'\d{4}', period):
            raise SourceError('unsupported', f'period {period!r} is no year')
        years[t] = int(period)
    values, statuses = data.get('value'), data.get('status') or {}
    series, names, flags = {}, {}, {}
    for g, (geo, label) in enumerate(geos):
        key = entity_key(geo)
        for t, year in years.items():
            position = base + g * strides[i_geo] + t * strides[i_time]
            value = _cell(values, position)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            series.setdefault(key, {})[year] = float(value)
            names.setdefault(key, label)
            flag = _cell(statuses, position)
            if flag:
                flags.setdefault(key, {})[year] = flag
    if not series:
        raise SourceError('no_data', 'no values')
    title = (data.get('label') or code).strip()
    unit_code, unit_label = next(((d.values[0]) for d in dims if d.id == 'unit'), ('', ''))
    return ParsedChart(
        title=title, subtitle='', unit=unit_label, short_unit=_short_unit(unit_code, unit_label),
        definition=_definition(data, title),
        citation=f'Eurostat ({code}), last update {_updated(data)}',
        citation_short=f'Eurostat ({code})',
        source_url=BROWSER_URL.format(code=code),
        column_name=code,
        years=sorted({y for vals in series.values() for y in vals}),
        series=series, names=names, flags=flags, license=LICENSE,
    )


def metadata_of(data):
    """What a snapshot keeps as metadata_raw: the JSON-stat without the values."""
    return {k: v for k, v in data.items() if k not in ('value', 'status')}
