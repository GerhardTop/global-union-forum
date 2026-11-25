"""Background figures, step 1c: the chart standard in code (docs/achtergrondcijfers.md,
"Grafiekstandaard"; design docs/design/achtergrondcijfers-kolom.html and
achtergrondcijfers-uitgeklapt.html).

Pure Python: no database, no request. layout() takes the data of a
ChartSnapshot and what the reader sees, and returns a ChartView that the
macros in app/templates/charts/_chart.html draw without any arithmetic.

Coordinates: x as a percentage of the width of the plot area (the chart
stretches with the width), y in pixels within the fixed height of the figure
(column 136 px, large 340 px, as in the design references). The plot area
leaves room at the start for the y labels and (large) at the end for the end
labels; charts.css sets those gutters.
"""
import math
from dataclasses import dataclass, field

from babel import Locale
from babel.numbers import format_decimal, format_percent

SMALL, LARGE = 'kolom', 'groot'

COLORS = ('#1b74b8', '#e09400', '#108a4e', '#7048c8', '#0f8b8d', '#b4541a', '#c2378a', '#5b6b7d')
AVERAGE_COLOR = '#6c7683'
GRID_COLOR = '#e4e9ef'
ZERO_COLOR = '#a7b1bd'

# Per size: figure height, top and bottom of the plot (px), the most series
# (without the average), line widths, dash pattern of the average, grid lines.
_SIZES = {
    SMALL: dict(height=136, top=20, bottom=112, max_series=4, width=2, avg_width=1.75, dash='5 4',
                min_lines=3, max_lines=3, x_label_top=120, label_half=7),
    LARGE: dict(height=340, top=40, bottom=300, max_series=8, width=2.5, avg_width=2, dash='6 5',
                min_lines=4, max_lines=6, x_label_top=318, label_half=8),
}
END_LABEL_GAP = 17          # px between end labels (large)
MAX_X_LABELS = 6
_NICE = (1, 2, 2.5, 5)


@dataclass
class GridLine:
    y: float
    label: str
    zero: bool          # the darker line at 0


@dataclass
class XLabel:
    x: float
    label: str
    align: str          # 'start', 'middle' or 'end'


@dataclass
class Line:
    key: str
    color: str
    dashed: bool
    width: float
    dasharray: str
    segments: list      # "x,y x,y ..." per unbroken run of two or more points
    dots: list          # (x, y) of single points between gaps
    end: tuple = None   # (x, y) of the last value (large: the end point)


@dataclass
class EndLabel:
    key: str
    y: float            # top of the label, px
    name: str
    value: str
    color: str


@dataclass
class LegendItem:
    key: str
    name: str
    color: str
    dashed: bool
    value: str          # the last value in the period, formatted ('' when none)
    hidden: bool


@dataclass
class ChartView:
    size: str
    height: int
    grid: list = field(default_factory=list)
    x_labels: list = field(default_factory=list)
    lines: list = field(default_factory=list)
    end_labels: list = field(default_factory=list)
    legend: list = field(default_factory=list)
    table_columns: list = field(default_factory=list)   # [(key, name)]
    table_rows: list = field(default_factory=list)      # [(year, [formatted value or None])]
    years: list = field(default_factory=list)
    y_min: float = 0
    y_max: float = 0
    step: float = 0
    label_half: int = 7

    @property
    def empty(self):
        return not self.years


# ── Numbers ──────────────────────────────────────────────────────────────

def _decimals_for_step(step):
    for d in range(0, 4):
        if abs(round(step, d) - step) < 1e-9:
            return d
    return 3


def format_value(value, locale, short_unit='', decimals=1):
    """A number in the notation of the reader's language. A percentage with
    Babel's percent pattern of that language (nl "4,1%", fr "4,1 %"); else a
    decimal, with the short unit after it when it is short (at most 4)."""
    if value is None:
        return ''
    if short_unit == '%':
        pattern = Locale.parse(locale).percent_formats[None].pattern
        if decimals:
            pattern = pattern.replace('0', '0.' + '0' * decimals, 1)
        return format_percent(value / 100, format=pattern, locale=locale)
    pattern = '#,##0' + ('.' + '0' * decimals if decimals else '')
    text = format_decimal(value, format=pattern, locale=locale)
    if short_unit and len(short_unit) <= 4:
        return f'{text} {short_unit}' if short_unit[0].isalnum() else f'{text}{short_unit}'
    return text


# ── The axes ─────────────────────────────────────────────────────────────

def nice_step(raw):
    """The smallest step of 1, 2, 2.5 or 5 times a power of ten that is at
    least `raw`."""
    if raw <= 0:
        return 1
    power = 10 ** math.floor(math.log10(raw))
    for base in _NICE + (10,):
        if base * power >= raw - 1e-12:
            return base * power
    return 10 * power


def _bigger_step(step):
    power = 10 ** math.floor(math.log10(step) + 1e-9)
    base = round(step / power, 6)
    for nice in _NICE + (10,):
        if nice > base + 1e-9:
            return nice * power
    return 10 * power


def y_scale(lo, hi, min_lines, max_lines):
    """(bottom, top, step) of the y axis: round steps, from 0 when nothing is
    negative, with a round lower bound below the lowest negative value, and
    between min_lines and max_lines grid lines; the top line on or just above
    the maximum."""
    lo, hi = min(lo, 0), max(hi, 0)
    if hi == lo:
        hi = lo + 1
    best = None
    for intervals in range(min_lines - 1, max_lines):
        step = nice_step((hi - lo) / intervals)
        while True:
            bottom = math.floor(lo / step + 1e-9) * step
            top = math.ceil(hi / step - 1e-9) * step
            n = round((top - bottom) / step)
            if n <= max_lines - 1:
                break
            step = _bigger_step(step)
        while n < min_lines - 1:          # pad to the minimum number of lines
            if bottom < 0 and hi <= 0:
                bottom -= step
            else:
                top += step
            n += 1
        span = top - bottom
        if best is None or span < best[1] - 1e-9 or (abs(span - best[1]) < 1e-9 and n > best[3]):
            best = (bottom, span, step, n)
    bottom, span, step, _n = best
    return bottom, bottom + span, step


def x_label_years(years, size):
    """Column: the first and the last year. Large: a fixed step from the last
    year back, at most MAX_X_LABELS labels, the last year always among them;
    a step that also lands on the first year when there is one (2015, 2018,
    2021, 2024) with at least three labels."""
    if not years:
        return []
    first, last = years[0], years[-1]
    if size == SMALL or first == last:
        return sorted({first, last})
    span = last - first
    step = max(1, math.ceil(span / (MAX_X_LABELS - 1)))
    for candidate in range(step, span // 2 + 1):
        if span % candidate == 0:
            step = candidate
            break
    out, year = [], last
    while year >= first:
        out.append(year)
        year -= step
    return sorted(out)


def _spread(items, gap, low, high):
    """Positions (sorted by wish) at least `gap` apart, within [low, high]:
    push down where they collide, then shift the group up when it runs out
    below the area."""
    out = []
    for wish in items:
        y = wish if not out else max(wish, out[-1] + gap)
        out.append(y)
    overflow = out[-1] - high if out else 0
    if overflow > 0:
        out = [y - overflow for y in out]
        for i in range(len(out) - 2, -1, -1):       # keep the gap after shifting up
            out[i] = min(out[i], out[i + 1] - gap)
    if out and out[0] < low:
        shift = low - out[0]
        out = [y + shift for y in out]
    return out


# ── The layout ───────────────────────────────────────────────────────────

def layout(data, keys, average_key=None, size=LARGE, locale='nl', period=None, hidden=(), names=None,
           short_unit=''):
    """The ChartView of one chart.

    data: ChartSnapshot.data ({"years", "series", "names"}); keys: the shown
    entities in colour order (more than the size allows: the first n); the
    average as the source supplies it (or None); size 'kolom' or 'groot';
    locale of the reader; period 5, 10 or None (all); hidden keys (still in
    the legend, not in the lines or the table); display names per key (the
    caller translates; a missing name falls back to the English name in the
    data); short_unit of the indicator."""
    conf = _SIZES[size]
    series, data_names = data.get('series') or {}, data.get('names') or {}
    names = names or {}
    hidden = set(hidden or ())
    keys = [k for k in keys if k in series and k != average_key][:conf['max_series']]
    average_key = average_key if average_key in series else None
    shown = keys + ([average_key] if average_key else [])

    def name(key):
        return names.get(key) or data_names.get(key) or key

    all_years = list(data.get('years') or [])
    with_data = [i for i, _y in enumerate(all_years) if any(series[k][i] is not None for k in shown)]
    if period:
        with_data = with_data[-period:]
    years = [all_years[i] for i in with_data]
    view = ChartView(size=size, height=conf['height'], years=years, label_half=conf['label_half'])
    if not years:
        return view
    values = {k: [series[k][i] for i in with_data] for k in shown}

    visible = [k for k in shown if k not in hidden]
    numbers = [v for k in shown for v in values[k] if v is not None]
    decimals = 1 if max(abs(v) for v in numbers) < 100 else 0
    scale_numbers = [v for k in visible for v in values[k] if v is not None] or numbers
    bottom_value, top_value, step = y_scale(min(scale_numbers), max(scale_numbers),
                                            conf['min_lines'], conf['max_lines'])
    view.y_min, view.y_max, view.step = bottom_value, top_value, step
    top_px, bottom_px = conf['top'], conf['bottom']

    def y_of(value):
        return round(bottom_px - (value - bottom_value) / (top_value - bottom_value) * (bottom_px - top_px), 1)

    first, last = years[0], years[-1]

    def x_of(year):
        return 50.0 if first == last else round((year - first) / (last - first) * 100, 2)

    axis_decimals = _decimals_for_step(step)
    n_lines = round((top_value - bottom_value) / step)
    for i in range(n_lines + 1):
        value = round(bottom_value + i * step, 9)
        view.grid.append(GridLine(y=y_of(value), label=format_value(value, locale, short_unit, axis_decimals),
                                  zero=abs(value) < 1e-9))
    if bottom_value < 0 < top_value and not any(g.zero for g in view.grid):
        view.grid.append(GridLine(y=y_of(0), label='', zero=True))

    label_years = x_label_years(years, size)
    for year in label_years:
        if size == SMALL and len(label_years) > 1:
            align = 'start' if year == first else 'end'
        else:
            align = 'middle'
        view.x_labels.append(XLabel(x=x_of(year), label=str(year), align=align))

    colors = {k: COLORS[i % len(COLORS)] for i, k in enumerate(keys)}
    if average_key:
        colors[average_key] = AVERAGE_COLOR

    def last_value(key):
        for year, value in zip(reversed(years), reversed(values[key])):
            if value is not None:
                return year, value
        return None, None

    for key in visible:
        dashed = key == average_key
        segments, dots, run = [], [], []
        for year, value in zip(years, values[key]):
            if value is None:
                if run:
                    (segments if len(run) > 1 else dots).append(run)
                run = []
            else:
                run.append((x_of(year), y_of(value)))
        if run:
            (segments if len(run) > 1 else dots).append(run)
        year, value = last_value(key)
        view.lines.append(Line(
            key=key, color=colors[key], dashed=dashed,
            width=conf['avg_width'] if dashed else conf['width'],
            dasharray=conf['dash'] if dashed else '',
            segments=[' '.join(f'{x},{y}' for x, y in seg) for seg in segments],
            dots=[seg[0] for seg in dots],
            end=(x_of(year), y_of(value)) if value is not None else None))

    if size == LARGE:
        wishes = []
        for key in visible:
            _year, value = last_value(key)
            if value is not None:
                wishes.append((y_of(value) - conf['label_half'], key, value))
        wishes.sort(key=lambda w: w[0])
        placed = _spread([w[0] for w in wishes], END_LABEL_GAP, 0, conf['height'] - 2 * conf['label_half'])
        for y, (_wish, key, value) in zip(placed, wishes):
            view.end_labels.append(EndLabel(key=key, y=round(y, 1), name=name(key), color=colors[key],
                                            value=format_value(value, locale, short_unit, decimals)))

    for key in shown:
        _year, value = last_value(key)
        view.legend.append(LegendItem(key=key, name=name(key), color=colors[key], dashed=key == average_key,
                                      value=format_value(value, locale, short_unit, decimals),
                                      hidden=key in hidden))

    view.table_columns = [(k, name(k)) for k in visible]
    for i in range(len(years) - 1, -1, -1):
        cells = [format_value(values[k][i], locale, short_unit, decimals) if values[k][i] is not None else None
                 for k in visible]
        view.table_rows.append((years[i], cells))
    return view
