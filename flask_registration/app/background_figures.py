"""Background figures, step 1d (docs/achtergrondcijfers.md): searching Our
World in Data, the AI's choice, the checks, publishing and linking.

Behind the switch BACKGROUND_FIGURES (config, environment variable, off by
default): when it is off nothing starts and the tick does nothing here.

    schedule(issue, proposal=None, trigger)
                    after creating an issue ('issue') or a proposal
                    ('proposal'): a ChartSelectionRun (pending), started in a
                    background thread after the commit (like content_jobs)
    run(run_id, client=None, http=None)
                    one round: search terms (JUDGE_MODEL), the Search API of
                    OWID, the choice (SELECTION_MODEL), the checks, and in one
                    transaction the facts, charts, snapshots and links
    backfill()      the hourly tick: pending rounds older than ten minutes and
                    failed rounds with fewer than three attempts
    dry_run(issue)  the round without saving anything (flask
                    achtergrondcijfers-proef)

The AI only chooses: an indicator, countries, an average the source supplies,
a period and a reason. The figures come from the source (app/sources/owid.py);
the app never computes an average and never fills in a number. Whatever fails
a check falls away; nothing is repaired or guessed. A removed chart and an AI
link a person undid are remembered (ChartBlock) and do not come back.
"""
import json
import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from flask import current_app
from sqlalchemy import event

from app import db

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)

SELECTION_MODEL = 'claude-opus-5-5'
MAX_CHARTS = 4              # per issue, existing ones counted
MAX_TERMS = 5
MAX_CANDIDATES = 40
MAX_COUNTRIES = 4
DEFAULT_YEARS = 10
MAX_ATTEMPTS = 3
MAX_DATA_AGE_YEARS = 5      # the last year with data at most this many years before now
PENDING_AFTER = timedelta(minutes=10)
SEARCH_HITS = 20

# Rule 4b: fixed sets per region (for now in code; later in the admin).
REGION_SETS = {
    'Europe': ('Germany', 'France', 'Netherlands', 'Poland'),
    'World': ('United States', 'China', 'India', 'European Union (27)'),
}
# Names of entities that can serve as an average or total, as OWID writes
# them (the source supplies them; we never compute one).
_AVERAGE_NAMES = ('World', 'European Union (27)', 'Europe', 'Africa', 'Asia', 'North America',
                  'South America', 'Oceania', 'Latin America and Caribbean', 'High-income countries',
                  'Low-income countries', 'Lower-middle-income countries', 'Upper-middle-income countries',
                  'Middle-income countries', 'OECD members', 'Euro area')
_AVERAGE_PREFIXES = ('World', 'European Union', 'Europe', 'Africa', 'Asia', 'North America', 'South America',
                     'Latin America', 'Oceania', 'High-income', 'Low-income', 'Lower-middle', 'Upper-middle',
                     'Middle-income', 'OECD', 'Euro area', 'East Asia', 'Middle East', 'Sub-Saharan')

# Where OWID writes a country differently from our list (checked 25 November 2026).
_OWID_NAMES = {'CG': 'Congo', 'CI': "Cote d'Ivoire", 'CZ': 'Czechia', 'CD': 'Democratic Republic of Congo',
               'TL': 'East Timor'}

_PENDING = 'background_figures_pending'


def enabled(app=None):
    return bool((app or current_app).config.get('BACKGROUND_FIGURES'))


# ── Scheduling ───────────────────────────────────────────────────────────

def _phase_open(issue):
    from app.issue_proposals import LINK_PHASES
    return issue is not None and not issue.is_closed and issue.phase in LINK_PHASES


def schedule(issue, proposal=None, trigger='issue'):
    """A selection round for this issue, started after the commit. Only with
    the switch on, an open issue in the facts or adjusting phase, and a
    conversation. Returns the run, or None. Does not commit."""
    if not enabled() or issue is None:
        return None
    if issue.id is None or issue.thread_id is None or (proposal is not None and proposal.id is None):
        db.session.flush()
    if issue.thread_id is None or not _phase_open(issue):
        return None
    from app.models import ChartSelectionRun
    run_row = ChartSelectionRun(issue_id=issue.id, proposal_id=proposal.id if proposal is not None else None,
                                trigger=trigger, status='pending', attempts=0, created_at=datetime.utcnow())
    db.session.add(run_row)
    db.session.flush()
    db.session.info.setdefault(_PENDING, []).append(run_row.id)
    return run_row


def _after_commit(session):
    ids = session.info.pop(_PENDING, None)
    if ids:
        _start(current_app._get_current_object(), list(ids))


def _after_rollback(session):
    session.info.pop(_PENDING, None)


def _start(app, run_ids):
    """Run the rounds in a thread. Tests replace this."""
    if not app.config.get('ANTHROPIC_API_KEY'):
        return   # no AI here: the tick tries again once there is
    threading.Thread(target=_run_in_thread, args=(app, run_ids), daemon=True,
                     name='background-figures').start()


def _run_in_thread(app, run_ids):
    with app.app_context():
        for run_id in run_ids:
            try:
                run(run_id)
            except Exception:   # never let the thread die loudly; the run row tells
                log.exception('Background figures: run %s crashed', run_id)
                db.session.rollback()


# ── The memory ───────────────────────────────────────────────────────────

def block_slug(issue_id, slug):
    """A removed chart: this slug does not come back for this issue. Does not commit."""
    from app.models import ChartBlock
    if issue_id is None or not slug:
        return
    if ChartBlock.query.filter_by(issue_id=issue_id, kind='slug', slug=slug).first() is None:
        db.session.add(ChartBlock(issue_id=issue_id, kind='slug', slug=slug, created_at=datetime.utcnow()))


def block_link(issue_id, proposal_id, fact_post_id):
    """An AI link a person undid: the AI does not lay it again. Does not commit."""
    from app.models import ChartBlock
    if ChartBlock.query.filter_by(issue_id=issue_id, kind='link', proposal_id=proposal_id,
                                  fact_post_id=fact_post_id).first() is None:
        db.session.add(ChartBlock(issue_id=issue_id, kind='link', proposal_id=proposal_id,
                                  fact_post_id=fact_post_id, created_at=datetime.utcnow()))


def _block_on_orm_delete(mapper, connection, target):
    """A chart deleted through the ORM (the cascade from its Post): remember
    the slug. Bulk deletes go through facts.delete_for_posts."""
    from app.models import ChartBlock
    table = ChartBlock.__table__
    exists = connection.execute(table.select().where(table.c.issue_id == target.issue_id)
                                .where(table.c.kind == 'slug').where(table.c.slug == target.slug)).first()
    if exists is None:
        connection.execute(table.insert().values(issue_id=target.issue_id, kind='slug', slug=target.slug,
                                                 created_at=datetime.utcnow()))


def delete_for_issue(issue_id):
    """Before an issue is purged: its rounds and its memory (no translated
    fields there). Does not commit."""
    from app.models import ChartBlock, ChartSelectionRun
    ChartSelectionRun.query.filter_by(issue_id=issue_id).delete(synchronize_session=False)
    ChartBlock.query.filter_by(issue_id=issue_id).delete(synchronize_session=False)


def detach_proposal(proposal_id):
    """Before a proposal is purged: the rounds it started stay, without it."""
    from app.models import ChartSelectionRun
    ChartSelectionRun.query.filter_by(proposal_id=proposal_id).update(
        {ChartSelectionRun.proposal_id: None}, synchronize_session=False)


# ── The prompts (English, fixed in code) ─────────────────────────────────

_TERMS_SYSTEM = (
    "You help Global Union Forum, a deliberation platform, find background figures for an issue that "
    "people discuss and vote on. The figures come from Our World in Data (OWID). Give at most "
    f"{MAX_TERMS} short English search terms (one to four words each) for OWID charts that would help "
    "people think well about this issue and about the different sides of it. Prefer measurable "
    "indicators (spending, shares, rates, counts, opinion). Answer with JSON only: "
    '{"terms": ["...", "..."]}'
)

_SELECTION_SYSTEM = (
    "You choose background figures for an issue on Global Union Forum, a deliberation platform where "
    "people discuss proposals and vote on them. You only choose; the figures come straight from Our "
    "World in Data (OWID) and the platform draws the charts itself. You never write a number.\n\n"
    "Rules:\n"
    "1. Prefer one chart per issue; at most four in total (the existing ones count), and only when "
    "each is useful. Do not add a chart that adds little. If no candidate fits, say so in none_reason. "
    "Prefer one chart. Add a second only if it covers a different aspect that the first does not, and "
    "say which aspect in its reason.\n"
    "2. Only charts from the candidate list (by slug).\n"
    "3. Balance: together the charts are relevant to several proposals or sides of the issue, not "
    "only to one. For each chart, say for which proposals it is relevant. Example (European defence, "
    "with proposals about an EU army, the NATO spending target, and diplomacy and development aid): "
    "not only defence spending (the frame of the NATO target), but also support for a common defence "
    "policy and development aid.\n"
    "4. Countries: a) first the countries named in the issue or the proposals; b) otherwise a fixed set "
    "per region: Europe: Germany, France, Netherlands, Poland; world: United States, China, India, "
    "European Union (27); c) never choose countries to support a position, and never look for "
    f"outliers. At most {MAX_COUNTRIES} countries, written exactly as in the candidate's entities.\n"
    "5. Average: only an average or total that is in the source's own data (for example European "
    "Union (27) or World), written exactly as in the entities. If there is none, use null. Never "
    "compute one.\n"
    "6. Period: the last ten years (years: 10), unless a longer period is needed to see the point; "
    "explain a longer period in the reason.\n"
    "7. Reason: one or two sentences in plain English that say what the chart shows and which question "
    "about the issue it helps to answer. Do not repeat or paraphrase the positions or wording of any "
    "proposal, and do not suggest which proposal is better.\n\n"
    "Answer with JSON only, in this form:\n"
    '{"charts": [{"slug": "...", "countries": ["..."], "average": "..." or null, "years": 10, '
    '"reason": "...", "relevant_for": [proposal ids]}], '
    '"link_existing": [{"fact_post_id": 0, "proposal_ids": [0]}], '
    '"none_reason": "..." or null}'
)


def _parse_json(text):
    """The JSON object in an AI answer (a code fence around it is fine)."""
    text = (text or '').strip()
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end <= start:
        raise ValueError('no JSON object in the answer')
    value = json.loads(text[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError('the answer is no JSON object')
    return value


def _ask(client, model, system, user_msg, max_tokens):
    response = client.messages.create(model=model, max_tokens=max_tokens, system=system,
                                      messages=[{'role': 'user', 'content': user_msg}])
    return ''.join(getattr(block, 'text', '') for block in response.content)


# ── Gathering ────────────────────────────────────────────────────────────

@dataclass
class Context:
    issue: object
    proposal: object = None
    trigger: str = 'issue'
    proposals: list = field(default_factory=list)       # open proposals of the issue
    charts: list = field(default_factory=list)          # existing FactCharts
    blocked_slugs: set = field(default_factory=set)
    blocked_links: set = field(default_factory=set)     # (proposal_id, fact_post_id)
    mentioned: list = field(default_factory=list)       # English names of countries named


def _text(obj, field_name):
    from app import content_i18n
    original = getattr(obj, field_name) or ''
    english = content_i18n.text_for(obj, field_name, 'en')   # the original itself when it is English
    return original, english


def _country_names():
    """{iso2: set of names in every language we know} for our country list."""
    from app.sources import countries
    rows = countries._our_rows()
    return {iso2: {n for n in names.values() if n} for iso2, names in rows.items()}


def _mentioned_countries(texts):
    """English names (as in our list) of the countries named in the texts."""
    from app.sources import countries
    blob = ' '.join(t for t in texts if t)
    out = []
    for iso2, names in _country_names().items():
        if any(re.search(rf'(?<!\w){re.escape(name)}(?!\w)', blob, flags=re.I) for name in names):
            english = _OWID_NAMES.get(iso2) or countries.english_name(iso2)
            if english:
                out.append(english)
    return sorted(out)


def gather(issue, proposal=None, trigger='issue'):
    from app.models import ChartBlock, FactChart, IssueProposal
    ctx = Context(issue=issue, proposal=proposal, trigger=trigger)
    ctx.proposals = (IssueProposal.query.filter_by(issue_id=issue.id, is_final=False)
                     .order_by(IssueProposal.id).all())
    ctx.charts = FactChart.query.filter_by(issue_id=issue.id).order_by(FactChart.id).all()
    for block in ChartBlock.query.filter_by(issue_id=issue.id):
        if block.kind == 'slug':
            ctx.blocked_slugs.add(block.slug)
        elif block.kind == 'link':
            ctx.blocked_links.add((block.proposal_id, block.fact_post_id))
    texts = [issue.title, issue.intro] + [p.statement for p in ctx.proposals] + [p.explanation for p in ctx.proposals]
    ctx.mentioned = _mentioned_countries(texts)
    return ctx


def _links_of(chart):
    from app.models import IssueProposalFact
    return sorted(row.proposal_id for row in IssueProposalFact.query.filter_by(fact_post_id=chart.post_id))


def _issue_block(ctx):
    title, title_en = _text(ctx.issue, 'title')
    intro, intro_en = _text(ctx.issue, 'intro')
    lines = [f'Issue title: {title}']
    if title_en and title_en != title:
        lines.append(f'Issue title in English: {title_en}')
    lines.append(f'Issue introduction: {intro}')
    if intro_en and intro_en != intro:
        lines.append(f'Issue introduction in English: {intro_en}')
    return lines


def terms_prompt(ctx):
    lines = _issue_block(ctx)
    for p in ctx.proposals:
        lines.append(f'Proposal {p.id}: {p.statement}')
    if ctx.charts:
        lines.append('Charts already chosen: ' + ', '.join(c.slug for c in ctx.charts))
    return '\n'.join(lines)


def _relevant_entities(hit, ctx):
    wanted = set(ctx.mentioned)
    for names in REGION_SETS.values():
        wanted.update(names)
    out = []
    for name in hit.entities:
        if name in wanted or name in _AVERAGE_NAMES or name.startswith(_AVERAGE_PREFIXES):
            out.append(name)
    return out


def selection_prompt(ctx, candidates):
    lines = _issue_block(ctx)
    if ctx.proposals:
        lines.append('Proposals:')
        lines.extend(f'- id {p.id}: {p.statement}' for p in ctx.proposals)
    else:
        lines.append('There are no proposals yet: balance the charts over the sides of the issue '
                     '(relevant_for stays empty).')
    if ctx.trigger == 'proposal' and ctx.proposal is not None:
        lines.append(f'A new proposal was just made (id {ctx.proposal.id}). Mainly say which existing charts '
                     'belong to it (link_existing); add a new chart only when it really adds something.')
    if ctx.mentioned:
        lines.append('Countries named in the issue or the proposals: ' + ', '.join(ctx.mentioned))
    if ctx.charts:
        lines.append(f'Charts already chosen ({len(ctx.charts)} of at most {MAX_CHARTS}):')
        for chart in ctx.charts:
            title = chart.post.fact_title if chart.post is not None else chart.slug
            snapshot = chart.current_snapshot
            names = snapshot.values.get('names', {}) if snapshot else {}
            countries = ', '.join(names.get(k, k) for k in chart.entity_keys)
            lines.append(f'- fact_post_id {chart.post_id}: {chart.slug} "{title}" ({countries}); '
                         f'linked to proposals {_links_of(chart)}')
    lines.append('Candidates:')
    for hit in candidates:
        entities = ', '.join(_relevant_entities(hit, ctx)) or '(none of the usual ones)'
        lines.append(f'- slug: {hit.slug}\n  title: {hit.title}\n  subtitle: {hit.subtitle}\n  entities: {entities}')
    return '\n'.join(lines)


# ── The checks ───────────────────────────────────────────────────────────

@dataclass
class Accepted:
    slug: str
    parsed: object
    metadata: dict
    keys: list
    average_key: str
    year_from: int
    year_to: int
    reason: str
    proposal_ids: list


def _int_list(values):
    out = []
    for value in values if isinstance(values, list) else []:
        try:
            out.append(int(value))
        except (TypeError, ValueError):
            continue
    return out


def check_chart(item, ctx, candidates, http, notes, now=None):
    """An Accepted chart, or None (with the reason in notes). Nothing is
    repaired or guessed: a country that fails falls away; an average that
    fails means no average."""
    from app.sources import SourceError, countries, owid
    slug = item.get('slug') if isinstance(item, dict) else None
    if slug not in candidates:
        notes.append(f'{slug}: not one of the candidates')
        return None
    try:
        csv_text, metadata = owid.fetch(slug, http=http)
        parsed = owid.parse(csv_text, metadata, slug=slug)
    except SourceError as exc:
        if exc.reason == 'network':
            raise
        notes.append(f'{slug}: {exc.reason}')
        return None
    by_name = {name: key for key, name in parsed.names.items()}
    keys = []
    for name in (item.get('countries') or [])[:MAX_COUNTRIES] if isinstance(item.get('countries'), list) else []:
        key = by_name.get(name)
        if key is None:
            notes.append(f'{slug}: country {name!r} is not in the data')
        elif countries.iso2_for(key) is None:
            notes.append(f'{slug}: {name!r} is not a country of our list')
        elif key not in keys:
            keys.append(key)
    if not keys:
        notes.append(f'{slug}: no country left')
        return None
    try:
        years = max(1, int(item.get('years') or DEFAULT_YEARS))
    except (TypeError, ValueError):
        years = DEFAULT_YEARS
    year_to = max(max(parsed.series[k]) for k in keys)
    if (now or datetime.utcnow()).year - year_to > MAX_DATA_AGE_YEARS:
        notes.append(f'{slug}: stale (the last data is from {year_to})')
        return None
    year_from = year_to - years + 1
    kept = [k for k in keys if owid.has_values(parsed, k, year_from, year_to)]
    for key in keys:
        if key not in kept:
            notes.append(f'{slug}: {parsed.names[key]!r} has no data in {year_from} to {year_to}')
    if not kept:
        notes.append(f'{slug}: no country left')
        return None
    average_key = None
    average = item.get('average')
    if average:
        key = by_name.get(average)
        if key is None:
            notes.append(f'{slug}: average {average!r} is not in the data, so no average')
        elif not owid.has_values(parsed, key, year_from, year_to):
            notes.append(f'{slug}: average {average!r} has no data in the period, so no average')
        else:
            average_key = key
    open_ids = {p.id for p in ctx.proposals}
    proposal_ids = [pid for pid in dict.fromkeys(_int_list(item.get('relevant_for'))) if pid in open_ids]
    reason = str(item.get('reason') or '').strip()
    if not reason:
        notes.append(f'{slug}: no reason')
        return None
    return Accepted(slug=slug, parsed=parsed, metadata=metadata, keys=kept, average_key=average_key,
                    year_from=year_from, year_to=year_to, reason=reason, proposal_ids=proposal_ids)


def check_links(answer, ctx, notes):
    """[(fact_post_id, proposal_id)] for link_existing: only charts of this
    issue, only open proposals of this issue, never a link a person undid,
    never one that exists."""
    from app.models import IssueProposalFact
    charts = {c.post_id for c in ctx.charts}
    open_ids = {p.id for p in ctx.proposals}
    out = []
    for item in answer.get('link_existing') or [] if isinstance(answer.get('link_existing'), list) else []:
        if not isinstance(item, dict):
            continue
        try:
            post_id = int(item.get('fact_post_id'))
        except (TypeError, ValueError):
            continue
        if post_id not in charts:
            notes.append(f'link: fact {post_id} is no chart of this issue')
            continue
        for pid in _int_list(item.get('proposal_ids')):
            if pid not in open_ids:
                notes.append(f'link: proposal {pid} is not open in this issue')
            elif (pid, post_id) in ctx.blocked_links:
                notes.append(f'link: fact {post_id} to proposal {pid} was undone before')
            elif IssueProposalFact.query.filter_by(proposal_id=pid, fact_post_id=post_id).first() is not None:
                continue
            elif (post_id, pid) not in out:
                out.append((post_id, pid))
    return out


# ── Showing the charts (step 1e) ─────────────────────────────────────────

def charts_for(posts):
    """{post_id: (FactChart, current ChartSnapshot)} for the chart facts among
    `posts`, in two queries however many there are (never per fact)."""
    from app.models import ChartSnapshot, FactChart
    ids = [p.id for p in posts if p is not None and p.fact_type == 'cijfer']
    if not ids:
        return {}
    charts = FactChart.query.filter(FactChart.post_id.in_(ids)).all()
    if not charts:
        return {}
    latest = {}
    for snapshot in ChartSnapshot.query.filter(ChartSnapshot.chart_id.in_([c.id for c in charts])):
        if snapshot.chart_id not in latest or snapshot.version > latest[snapshot.chart_id].version:
            latest[snapshot.chart_id] = snapshot
    return {c.post_id: (c, latest[c.id]) for c in charts if c.id in latest}


@dataclass
class ChartCard:
    """What the card in the facts column needs (issues/_fact_card.html)."""
    chart: object
    snapshot: object
    view: object             # charts.ChartView, size 'kolom'
    countries: list          # display names, in colour order
    countries_text: str      # joined in the reader's language ("Poland, France and Germany")
    average: str             # display name of the average, or ''
    start: int
    end: int
    citation: str
    source_url: str
    date: str                # the day of publishing, in the reader's notation


def chart_cards(posts, lang):
    """{post_id: ChartCard} for the chart facts among `posts`: two queries for
    the charts and snapshots and one for the translated average names,
    however many facts there are."""
    from babel.lists import format_list
    from flask_babel import format_date

    from app import charts, content_i18n
    from app.sources import countries
    found = charts_for(posts)
    if not found:
        return {}
    content_i18n.preload([chart for chart, _s in found.values()], 'average_name')
    by_id = {p.id: p for p in posts if p is not None}
    out = {}
    for post_id, (chart, snapshot) in found.items():
        data = snapshot.values
        names = {}
        for key in data.get('names', {}):
            iso2 = countries.iso2_for(key)
            if iso2:
                names[key] = countries.display_name(iso2, lang)
        average = ''
        if chart.average_key:
            average = (content_i18n.display(chart, 'average_name', lang) if chart.average_name
                       else data.get('names', {}).get(chart.average_key, ''))
            names[chart.average_key] = average
        view = charts.layout(data, chart.entity_keys, chart.average_key, charts.SMALL, lang, None, (), names,
                             snapshot.short_unit or '')
        if view.empty:
            continue
        shown = [names.get(k) or data['names'].get(k, k) for k in chart.entity_keys if k in data.get('series', {})]
        shown = shown[:charts._SIZES[charts.SMALL]['max_series']]
        post = by_id.get(post_id)
        out[post_id] = ChartCard(
            chart=chart, snapshot=snapshot, view=view, countries=shown,
            countries_text=format_list(shown, locale=lang) if shown else '',
            average=average, start=view.years[0], end=view.years[-1],
            citation=snapshot.citation or '',   # not citation_short: it has a dash
            source_url=snapshot.source_url or '',
            date=format_date(post.created_at, 'medium') if post is not None and post.created_at else '')
    return out


# ── The round ────────────────────────────────────────────────────────────

@dataclass
class Outcome:
    status: str = 'nothing'
    terms: list = field(default_factory=list)
    candidates: list = field(default_factory=list)
    prompts: list = field(default_factory=list)          # [(system, user)]
    responses: list = field(default_factory=list)
    answer: dict = field(default_factory=dict)
    accepted: list = field(default_factory=list)
    links: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    none_reason: str = ''


def select(ctx, client, http=None):
    """The round without saving: terms, candidates, choice, checks."""
    from app import quality
    from app.sources import SourceError, owid
    out = Outcome()
    user_msg = terms_prompt(ctx)
    out.prompts.append((_TERMS_SYSTEM, user_msg))
    text = _ask(client, quality.JUDGE_MODEL, _TERMS_SYSTEM, user_msg, 300)
    out.responses.append(text)
    terms = [t.strip() for t in _parse_json(text).get('terms') or [] if isinstance(t, str) and t.strip()]
    out.terms = terms[:MAX_TERMS]
    if not out.terms:
        out.none_reason = 'no search terms'
        return out

    used = {c.slug for c in ctx.charts}
    candidates, errors = {}, 0
    for term in out.terms:
        try:
            hits = owid.search(term, hits=SEARCH_HITS, http=http)
        except SourceError as exc:
            errors += 1
            out.notes.append(f'search {term!r}: {exc.reason}')
            continue
        for hit in hits:
            if hit.slug in ctx.blocked_slugs or hit.slug in used or hit.slug in candidates:
                continue
            if len(candidates) < MAX_CANDIDATES:
                candidates[hit.slug] = hit
    if errors == len(out.terms):
        raise SourceError('network', 'every search failed')
    out.candidates = list(candidates.values())
    if not out.candidates and not ctx.charts:
        out.none_reason = 'no candidates'
        return out

    user_msg = selection_prompt(ctx, out.candidates)
    out.prompts.append((_SELECTION_SYSTEM, user_msg))
    text = _ask(client, SELECTION_MODEL, _SELECTION_SYSTEM, user_msg, 2000)
    out.responses.append(text)
    out.answer = _parse_json(text)
    out.none_reason = str(out.answer.get('none_reason') or '')

    room = MAX_CHARTS - len(ctx.charts)
    items = out.answer.get('charts') if isinstance(out.answer.get('charts'), list) else []
    for item in items:
        if len(out.accepted) >= room:
            out.notes.append(f'{item.get("slug") if isinstance(item, dict) else item}: '
                             f'more than {MAX_CHARTS} charts for this issue')
            continue
        if isinstance(item, dict) and any(a.slug == item.get('slug') for a in out.accepted):
            continue
        accepted = check_chart(item, ctx, candidates, http, out.notes)
        if accepted is not None:
            out.accepted.append(accepted)
    out.links = check_links(out.answer, ctx, out.notes)
    out.status = 'done' if out.accepted or out.links else 'nothing'
    return out


def publish(ctx, outcome):
    """The facts, charts, snapshots and links of a round. Does not commit."""
    from app import content_jobs
    from app.models import (FACT_KIND, SYSTEM_NAME, SYSTEM_ROLE, SYSTEM_USERNAME, FactChart, IssueProposalFact,
                            Post)
    from app.sources import owid
    for item in outcome.accepted:
        title = item.parsed.title or item.slug
        if len(title) > 100:
            title = title[:99].rstrip() + '…'
        post = Post(thread_id=ctx.issue.thread_id, user_id=None, author_name=SYSTEM_NAME,
                    author_username=SYSTEM_USERNAME, author_role=SYSTEM_ROLE, body=item.reason, source_lang='en',
                    kind=FACT_KIND, fact_type='cijfer', fact_direction='neutraal', fact_title=title,
                    fact_source=(item.parsed.source_url or f'https://ourworldindata.org/grapher/{item.slug}')[:500])
        db.session.add(post)
        db.session.flush()
        chart = FactChart(post_id=post.id, issue_id=ctx.issue.id, source='owid', slug=item.slug,
                          column_name=item.parsed.column_name[:300], average_key=item.average_key,
                          year_from=item.year_from, year_to=item.year_to)
        chart.entity_keys = item.keys
        # The name of the average as the source writes it (English); translated with the fact.
        chart.average_name = item.parsed.names.get(item.average_key) if item.average_key else None
        db.session.add(chart)
        owid.create_snapshot(chart, item.parsed, item.metadata, item.year_from, item.year_to)
        for pid in item.proposal_ids:
            db.session.add(IssueProposalFact(proposal_id=pid, fact_post_id=post.id, linked_by_id=None))
        content_jobs.schedule(post)   # title, reason and average name into the other languages; no judgement
    for post_id, pid in outcome.links:
        db.session.add(IssueProposalFact(proposal_id=pid, fact_post_id=post_id, linked_by_id=None))


def _claim(run_row):
    """Mark the round running, unless another round of this issue runs."""
    from app.models import ChartSelectionRun
    busy = (ChartSelectionRun.query.filter(ChartSelectionRun.issue_id == run_row.issue_id,
                                           ChartSelectionRun.id != run_row.id,
                                           ChartSelectionRun.status == 'running').first())
    if busy is not None:
        return False
    claimed = (ChartSelectionRun.query.filter(ChartSelectionRun.id == run_row.id,
                                              ChartSelectionRun.status.in_(('pending', 'failed')))
               .update({ChartSelectionRun.status: 'running'}, synchronize_session=False))
    db.session.commit()
    return claimed == 1


def _record(run_row, outcome):
    from app import quality
    run_row.prompt_text = '\n\n'.join(quality._prompt_record(system, user) for system, user in outcome.prompts)
    run_row.response_text = '\n\n'.join(outcome.responses)
    if outcome.status == 'nothing' and outcome.none_reason:
        run_row.response_text = (run_row.response_text + '\n\n' if run_row.response_text else '') + \
            f'[none] {outcome.none_reason}'
    if outcome.notes:
        run_row.error = '\n'.join(outcome.notes)[:5000]


def run(run_id, client=None, http=None):
    """One selection round. Returns the status ('done', 'nothing', 'failed'),
    or None when it did not run (switch off, gone, another round busy)."""
    from app import quality
    from app.models import ChartSelectionRun, Issue, IssueProposal
    if not enabled():
        return None
    run_row = db.session.get(ChartSelectionRun, run_id)
    if run_row is None or run_row.status not in ('pending', 'failed') or not _claim(run_row):
        return None
    db.session.refresh(run_row)
    run_row.model = f'{quality.JUDGE_MODEL} + {SELECTION_MODEL}'
    issue = db.session.get(Issue, run_row.issue_id)
    outcome = Outcome()
    try:
        if issue is None or issue.thread_id is None or not _phase_open(issue):
            outcome.none_reason = 'the issue is no longer in the facts or adjusting phase'
        else:
            client = client or quality._client()
            if client is None:
                raise RuntimeError('no AI key')
            proposal = db.session.get(IssueProposal, run_row.proposal_id) if run_row.proposal_id else None
            ctx = gather(issue, proposal, run_row.trigger)
            outcome = select(ctx, client, http)
            db.session.refresh(issue)   # the phase may have changed during the round
            if outcome.status == 'done' and not _phase_open(issue):
                outcome.status = 'nothing'
                outcome.notes.append('the phase changed during the round: nothing published')
            elif outcome.status == 'done':
                publish(ctx, outcome)
    except Exception as exc:   # AI, network, unreadable JSON: nothing half published
        db.session.rollback()
        run_row = db.session.get(ChartSelectionRun, run_id)
        run_row.status = 'failed'
        run_row.attempts = (run_row.attempts or 0) + 1
        run_row.error = f'{type(exc).__name__}: {exc}'[:5000]
        _record_safe(run_row, outcome)
        run_row.finished_at = datetime.utcnow()
        db.session.commit()
        log.info('Background figures: run %s for issue %s failed (attempt %s): %s',
                 run_id, run_row.issue_id, run_row.attempts, run_row.error)
        return 'failed'
    _record(run_row, outcome)
    run_row.status = outcome.status
    run_row.attempts = (run_row.attempts or 0) + 1
    run_row.finished_at = datetime.utcnow()
    db.session.commit()
    log.info('Background figures: run %s for issue %s: %s, %d charts, %d links',
             run_id, run_row.issue_id, outcome.status, len(outcome.accepted), len(outcome.links))
    return outcome.status


def _record_safe(run_row, outcome):
    error = run_row.error
    _record(run_row, outcome)
    run_row.error = error + ('\n' + '\n'.join(outcome.notes) if outcome.notes else '')


def backfill(now=None, client=None, http=None):
    """The hourly tick: pending rounds older than ten minutes and failed
    rounds with fewer than three attempts, oldest first. (done, failed)."""
    from app.models import ChartSelectionRun
    if not enabled():
        return 0, 0
    now = now or datetime.utcnow()
    rows = (ChartSelectionRun.query
            .filter(db.or_(db.and_(ChartSelectionRun.status == 'pending',
                                   ChartSelectionRun.created_at <= now - PENDING_AFTER),
                           db.and_(ChartSelectionRun.status == 'failed',
                                   ChartSelectionRun.attempts < MAX_ATTEMPTS)))
            .order_by(ChartSelectionRun.created_at, ChartSelectionRun.id).all())
    done = failed = 0
    for run_id in [row.id for row in rows]:
        status = run(run_id, client=client, http=http)
        if status == 'failed':
            failed += 1
        elif status is not None:
            done += 1
    return done, failed


def dry_run(issue, client=None, http=None, trigger='issue'):
    """The round for flask achtergrondcijfers-proef: nothing is saved."""
    from app import quality
    client = client or quality._client()
    if client is None:
        raise RuntimeError('no ANTHROPIC_API_KEY')
    ctx = gather(issue, None, trigger)
    try:
        return select(ctx, client, http)
    finally:
        db.session.rollback()


def _register():
    from app.models import FactChart
    event.listen(db.session, 'after_commit', _after_commit)
    event.listen(db.session, 'after_rollback', _after_rollback)
    event.listen(FactChart, 'after_delete', _block_on_orm_delete)


_register()
