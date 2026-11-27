"""Background figures, step 3a: the adapter for Eurostat (docs/achtergrondcijfers.md,
Bronnen punt 2).

refresh_catalog() keeps a local catalogue of Eurostat's main tables (type
"table" in the table of contents) and, since step 3b, the datasets with
yearly figures only (data start and end a bare year); search_catalog()
searches it per term (step 3c), with fixed places for main tables and
datasets. is_measure() and is_total() sort the dimensions (step 3c). fetch(code)
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
_YEAR = re.compile(r'^\d{4}$')
TYPES = ('table', 'dataset')


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
    type: str = 'table'


# ── The catalogue ────────────────────────────────────────────────────────

def _date(text):
    try:
        return datetime.strptime((text or '').strip(), '%d.%m.%Y').date()
    except ValueError:
        return None


def _keep(kind, start, end):
    """Main tables always; datasets only with yearly figures (step 3b: data
    start and end both a bare year such as 2025, not 2026-Q3 or 2026-09)."""
    return kind == 'table' or (kind == 'dataset' and bool(_YEAR.match(start)) and bool(_YEAR.match(end)))


def parse_toc(text):
    """The main tables and the yearly datasets of the table of contents:
    [dict], one per code (the first time it appears). Raises
    SourceError('unsupported') when the header is not the one we know."""
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
        code, kind = row[at['code']].strip(), row[at['type']].strip()
        start, end = row[at['data start']].strip(), row[at['data end']].strip()
        if not code or code in seen or not _keep(kind, start, end):
            continue
        seen.add(code)
        tables.append({'code': code[:40], 'title': row[at['title']].strip()[:500], 'type': kind,
                       'last_update': _date(row[at['last update of data']]),
                       'data_start': start[:10] or None, 'data_end': end[:10] or None})
    return tables


def refresh_catalog(http=None, now=None):
    """Replace the catalogue by the main tables and yearly datasets of the
    table of contents, in one transaction. The number of entries, or None
    when fetching or reading failed (the old catalogue stays)."""
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
        if not any(t['type'] == 'table' for t in tables):
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
    counts = catalog_counts()
    current_app.logger.info('Eurostat catalogue: %d main tables, %d yearly datasets', counts['table'],
                            counts['dataset'])
    return len(tables)


def catalog_counts():
    """{'table': n, 'dataset': n} in the catalogue."""
    from app import db
    from app.models import EurostatCatalog
    counts = dict(db.session.query(EurostatCatalog.type, db.func.count(EurostatCatalog.id))
                  .group_by(EurostatCatalog.type).all())
    return {kind: counts.get(kind, 0) for kind in TYPES}


def catalog_due(now=None):
    """Whether the catalogue is empty or older than seven days."""
    from app import db
    from app.models import EurostatCatalog
    last = db.session.query(db.func.max(EurostatCatalog.refreshed_at)).scalar()
    return last is None or last < (now or datetime.utcnow()) - CATALOG_MAX_AGE


_WORD = re.compile(r'[a-z0-9]+')

# Step 3c: words that do not count when searching (docs/achtergrondcijfers.md,
# Bronnen punt 2): small words, and words that sit in the titles of many
# unrelated tables ("per capita" hit packaging waste in a search on housing).
STOPWORDS = frozenset((
    'a', 'an', 'and', 'as', 'at', 'by', 'for', 'from', 'in', 'into', 'of', 'on', 'or', 'over', 'per', 'the',
    'to', 'vs', 'with', 'without',
    'annual', 'capita', 'country', 'countries', 'data', 'eu', 'europe', 'european', 'indicator', 'indicators',
    'level', 'levels', 'number', 'numbers', 'percent', 'percentage', 'rate', 'rates', 'ratio', 'share', 'shares',
    'statistics', 'total', 'totals', 'year', 'years', 'yearly',
))
# Fixed places among the candidates of a round: an empty place for datasets
# is not given to tables, nor the other way round.
SEARCH_TABLES = 10
SEARCH_DATASETS = 5


def _words(text):
    return set(_WORD.findall((text or '').lower()))


def _content_words(text):
    return _words(text) - STOPWORDS


def _ranked(entries, term):
    """The entries for one term, best first: a title with every word of the
    term (stop words left out) before one with some, then more words hit,
    then the title with fewer words ("Housing cost overburden rate" before
    "Housing cost overburden rate by income quintile"), then by title."""
    wanted = _content_words(term)
    if not wanted:
        return []
    scored = []
    for row, words in entries:
        hits = len(wanted & words)
        if hits:
            scored.append((hits < len(wanted), -hits, len(words), row.title.lower(), row.code, row))
    scored.sort(key=lambda item: item[:5])
    return [item[-1] for item in scored]


def _merge(lists, places, exclude):
    """Turn by turn from each list (the first of every term, then the second,
    ...), without doubles and without the excluded codes, at most `places`."""
    out, seen = [], set(exclude)
    for turn in range(max((len(rows) for rows in lists), default=0)):
        for rows in lists:
            if len(out) >= places:
                return out
            if turn < len(rows) and rows[turn].code not in seen:
                seen.add(rows[turn].code)
                out.append(rows[turn])
    return out


def search_catalog(terms, tables=SEARCH_TABLES, datasets=SEARCH_DATASETS, exclude=(), now=None):
    """Catalogue entries for the search terms (step 3c: per term): each term
    on its own ranks the entries whose English title has its words (whole
    words, case insensitive, STOPWORDS left out; see _ranked); the lists are
    merged turn by turn per term, without doubles. At most `tables` main
    tables, then at most `datasets` yearly datasets. Only entries whose data
    end at most five years ago; codes in `exclude` (the memories of the
    round) are skipped, the next entry takes their place. terms: a string or
    a list of strings."""
    from app.models import EurostatCatalog
    if isinstance(terms, str):
        terms = [terms]
    terms = [t for t in terms or [] if isinstance(t, str) and _content_words(t)]
    if not terms:
        return []
    oldest = (now or datetime.utcnow()).year - CATALOG_MAX_DATA_AGE_YEARS
    groups = {'table': [], 'dataset': []}
    for row in EurostatCatalog.query:
        year = (row.data_end or '')[:4]
        if year.isdigit() and int(year) >= oldest and row.type in groups:
            groups[row.type].append((row, _words(row.title)))
    found = []
    for kind, places in (('table', tables), ('dataset', datasets)):
        found += _merge([_ranked(groups[kind], term) for term in terms], places, exclude)
    return [CatalogHit(code=r.code, title=r.title, data_start=r.data_start or '', data_end=r.data_end or '',
                       type=r.type) for r in found]


# ── Dimensions: measures and breakdowns (step 3c) ────────────────────────

MEASURE_DIMENSIONS = ('unit', 'currency', 's_adj', 'na_item', 'freq')
TOTAL_CODES = ('T', 'TOTAL', 'TOT')


def is_measure(dim_id):
    """Whether a dimension says what is measured (free to choose: unit,
    currency, seasonal adjustment, national accounts item, ids starting with
    indic, and freq, which is reduced to A); every other dimension is a
    breakdown (sex, age, income group, category), where the choice must be
    the total."""
    dim_id = (dim_id or '').lower()
    return dim_id in MEASURE_DIMENSIONS or dim_id.startswith('indic')


def is_total(code, label):
    """Whether a value of a breakdown is its total: the code T, TOTAL or TOT,
    or a label starting with "Total"."""
    return (code or '').upper() in TOTAL_CODES or (label or '').strip().lower().startswith('total')


def totals_of(dim):
    """The codes of a dimension that are a total."""
    return [code for code, label in dim.values if is_total(code, label)]


def without_total(dims):
    """The breakdowns with more than one value and no total: a table with one
    falls away ('no_total')."""
    return [d for d in dims if not is_measure(d.id) and len(d.values) > 1 and not totals_of(d)]


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
    # Step 3c: the label of every flag used, for "Notes" (through the translation route).
    flag_labels = ((data.get('extension') or {}).get('status') or {}).get('label') or {}
    used = sorted({flag for per_year in flags.values() for flag in per_year.values()})
    labels = {f'flag:{flag}': str(flag_labels.get(flag) or flag).strip()[:200] for flag in used}
    unit_code, unit_label = next(((d.values[0]) for d in dims if d.id == 'unit'), ('', ''))
    return ParsedChart(
        title=title, subtitle='', unit=unit_label, short_unit=_short_unit(unit_code, unit_label),
        definition=_definition(data, title),
        citation=f'Eurostat ({code}), last update {_updated(data)}',
        citation_short=f'Eurostat ({code})',
        source_url=BROWSER_URL.format(code=code),
        column_name=code,
        years=sorted({y for vals in series.values() for y in vals}),
        series=series, names=names, flags=flags, license=LICENSE, labels=labels,
    )


# Dimensions left out of "Selection": shown elsewhere under "About these figures"
# (the unit has its own line).
SELECTION_SKIP = ('unit',)


def selection_text(data, filters):
    """The English labels of the chosen value per dimension (step 3c), in
    the order of the table's dimensions, joined by "; "; '' without filters.
    Only the dimensions that had several values (the keys of filters), and
    not the unit (SELECTION_SKIP), which has its own line."""
    ids = data.get('id') or []
    filters = {k: v for k, v in (filters or {}).items() if k not in SELECTION_SKIP}
    out = []
    for dim in [d for d in ids if d in filters] + [d for d in filters if d not in ids]:
        labels = dict(_labels(data, dim))
        out.append(str(labels.get(filters[dim]) or filters[dim]).strip())
    return '; '.join(t for t in out if t)[:300]


def last_update(metadata_raw):
    """The date of the last update of a table ("updated" of the JSON-stat),
    or None."""
    try:
        data = json.loads(metadata_raw) if isinstance(metadata_raw, str) else (metadata_raw or {})
        return datetime.strptime(str(data.get('updated') or '')[:10], '%Y-%m-%d').date()
    except (ValueError, TypeError, AttributeError):
        return None


def metadata_of(data):
    """What a snapshot keeps as metadata_raw: the JSON-stat without the values."""
    return {k: v for k, v in data.items() if k not in ('value', 'status')}
