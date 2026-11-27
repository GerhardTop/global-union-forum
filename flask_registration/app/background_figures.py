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
REJECT_MEMORY_DAYS = 30     # a chart rejected for a lasting reason stays out of the candidates this long
AGGREGATE_TOTAL_FACTOR = 2  # an "average" this many times the largest chosen country in every year is a total
# needs_choice: a Eurostat table that still has a dimension with several values
# after the filters of the dimension choice (step 3b).
LASTING_REASONS = ('unsupported', 'forbidden', 'no_data', 'stale', 'needs_choice', 'no_total')
# Step 3b, with EUROSTAT_FIGURES on: candidates from both sources.
MAX_OWID_CANDIDATES = 25
MAX_EUROSTAT_CANDIDATES = 15   # eurostat.SEARCH_TABLES + SEARCH_DATASETS (step 3c: fixed places)
EUROSTAT_AVERAGE = 'eurostat:EU27_2020'     # the only average of a Eurostat chart, as the source supplies it
SOURCES = ('owid', 'eurostat')
PENDING_AFTER = timedelta(minutes=10)
SEARCH_HITS = 20

# Rule 4b: fixed sets per region (for now in code; later in the admin), as
# ISO2 codes of our country list. The world set has the EU as its average.
EUROPE_SET = ('DE', 'FR', 'NL', 'PL')
WORLD_SET = ('US', 'CN', 'IN')
WORLD_AVERAGE = 'European Union (27)'
# Step 1l: the third fixed set, world blocks (compare "blocks"): the EU as an
# ordinary line next to the United States, China and India; never an average.
EU_BLOCK = 'European Union (27)'          # as OWID writes it (key OWID_EU27)
BLOCK_SET = ('US', 'CN', 'IN')
COMPARES = ('countries', 'blocks')
# European countries (for the choice of region): the EU, the EEA, the UK,
# Switzerland, the Balkans and the east of Europe.
EUROPEAN = frozenset((
    'AT', 'BE', 'BG', 'HR', 'CY', 'CZ', 'DK', 'EE', 'FI', 'FR', 'DE', 'GR', 'HU', 'IE', 'IT', 'LV', 'LT', 'LU',
    'MT', 'NL', 'PL', 'PT', 'RO', 'SK', 'SI', 'ES', 'SE', 'IS', 'LI', 'NO', 'CH', 'GB', 'AL', 'BA', 'ME', 'MK',
    'RS', 'XK', 'MD', 'UA', 'BY', 'AD', 'MC', 'SM', 'VA', 'TR', 'GE', 'AM', 'AZ', 'RU'))
# Words for "the world" in the five languages: they make the region the world.
_WORLD_WORDS = re.compile(r'(?<!\w)(?:world|global|globally|wereld|wereldwijd|mondiaal|welt|weltweit|global|'
                          r'mundo|mundial|monde|mondial|mondiale)(?!\w)', flags=re.I)
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


def eurostat_enabled(app=None):
    """Step 3b: Eurostat as a second source (EUROSTAT_FIGURES, off by default
    until the display of step 3c), only with BACKGROUND_FIGURES on."""
    return enabled(app) and bool((app or current_app).config.get('EUROSTAT_FIGURES'))


def memory_key(source, slug):
    """The key of a chart in the memories (ChartBlock.slug, owid_rejected_slugs,
    the charts an issue has): the OWID slug as it always was, "eurostat:<code>"
    for Eurostat (step 3b), so the two sources never collide."""
    return f'eurostat:{slug}' if source == 'eurostat' else slug


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
    key = memory_key(target.source, target.slug)
    exists = connection.execute(table.select().where(table.c.issue_id == target.issue_id)
                                .where(table.c.kind == 'slug').where(table.c.slug == key)).first()
    if exists is None:
        connection.execute(table.insert().values(issue_id=target.issue_id, kind='slug', slug=key,
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
    "indicators (spending, shares, rates, counts, opinion). Prefer terms for a single measure per country "
    "(for example 'share of electricity from coal', 'wind power generation'), not for a breakdown or mix of "
    "several sources. Use per capita or share terms for comparing countries, and 'share of global' terms for "
    "comparing world blocks. Answer with JSON only: "
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
    "2. Only charts from the candidate list (by slug). Choose charts that show one measure per country. Avoid "
    "charts that show a mix or breakdown (by source, by sector, by type), because the app can only draw one "
    "measure. For example, choose 'Share of electricity from coal' rather than 'Electricity mix' or "
    "'Electricity by source'. The kind of measure decides what you compare. To compare countries, choose a per "
    "capita or share measure and compare the named countries (otherwise the fixed set) with the average the "
    "source supplies. To show how large each part of the world is, compare world blocks (European Union (27), "
    "United States, China, India); prefer their share of the world total (for example 'share of global CO2 "
    "emissions', in %) over absolute totals; absolute totals only between these blocks and never with an "
    "average. For questions about responsibility or contribution, one chart of each kind can both be useful, "
    "as two different aspects.\n"
    "3. Balance: together the charts are relevant to several proposals or sides of the issue, not "
    "only to one. For each chart, say for which proposals it is relevant. Example (European defence, "
    "with proposals about an EU army, the NATO spending target, and diplomacy and development aid): "
    "not only defence spending (the frame of the NATO target), but also support for a common defence "
    "policy and development aid.\n"
    "4. Countries: a) first the countries named in the issue or the proposals; b) otherwise a fixed set "
    "per region: Europe: Germany, France, Netherlands, Poland; world: United States, China, India, "
    "European Union (27); c) never choose countries to support a position, and never look for "
    f"outliers. At most {MAX_COUNTRIES} countries, written exactly as in the candidate's entities. "
    "d) Or world blocks: European Union (27), United States, China, India, with compare \"blocks\": then only "
    "these four, the European Union (27) as an ordinary line, and no average. Never mix blocks with other "
    "countries, and never put the European Union (27) as a line in a countries chart.\n"
    "5. Average (compare \"countries\" only): only an average or total that is in the source's own data (for example European "
    "Union (27) or World), written exactly as in the entities. If there is none, use null. Never "
    "compute one.\n"
    "6. Period: the last ten years (years: 10), unless a longer period is needed to see the point; "
    "explain a longer period in the reason.\n"
    "7. Reason: one or two sentences in plain English that say what the chart shows and which question "
    "about the issue it helps to answer. Do not repeat or paraphrase the positions or wording of any "
    "proposal, and do not suggest which proposal is better. Each reason must stand on its own. Do not refer "
    "to other charts, since some may be dropped by the checks.\n\n"
    "Answer with JSON only, in this form:\n"
    '{"charts": [{"slug": "...", "compare": "countries" or "blocks", "countries": ["..."], '
    '"average": "..." or null, "years": 10, '
    '"reason": "...", "relevant_for": [proposal ids]}], '
    '"link_existing": [{"fact_post_id": 0, "proposal_ids": [0]}], '
    '"none_reason": "..." or null}'
)


# Step 3b, with EUROSTAT_FIGURES on: the same prompts with two sources, the
# candidates by id ("owid:<slug>", "eurostat:<code>").
_TERMS_SYSTEM_BOTH = _TERMS_SYSTEM.replace(
    'The figures come from Our World in Data (OWID).',
    'The figures come from Our World in Data (OWID) and Eurostat.').replace('terms (one to four words each) for OWID '
                                                                            'charts', 'terms (one to four words '
                                                                            'each) for charts')
_EUROSTAT_RULE = (
    "Candidates come from two sources. For questions about the EU or European countries, prefer Eurostat when it "
    "has the same measure (official and often more recent); use Our World in Data for the world, for world blocks "
    "and for countries outside Europe. Eurostat covers EU, EFTA and candidate countries only and cannot be used to "
    "compare world blocks. ")
_SELECTION_SYSTEM_BOTH = (_SELECTION_SYSTEM
                          .replace('the figures come straight from Our World in Data (OWID) and the platform',
                                   'the figures come straight from Our World in Data (OWID) or Eurostat and the '
                                   'platform')
                          .replace('2. Only charts from the candidate list (by slug). ',
                                   '2. Only charts from the candidate list (by id). ' + _EUROSTAT_RULE)
                          .replace('{"charts": [{"slug": "...",', '{"charts": [{"id": "...",'))

# Step 3b: the choice per dimension of the Eurostat tables of a round.
_DIMENSION_SYSTEM = (
    "You choose, for tables of Eurostat that were chosen as background figures for an issue on Global Union "
    "Forum, one value for each dimension that has several. For each dimension choose exactly one code from the "
    "list. For breakdown dimensions you must choose the total; if there is no total, answer null for that "
    "table. For the unit choose a relative measure "
    "(percentage, rate, per capita, share) over an absolute number, and for an index the most recent base year. "
    "Answer as JSON: {table_id: {dimension_id: code}}."
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
    mentioned: list = field(default_factory=list)       # English (OWID) names of the countries named
    named: list = field(default_factory=list)           # ISO2 of the countries named in the texts
    fixed: list = field(default_factory=list)           # ISO2 of the region's fixed set
    region: str = 'Europe'                              # 'Europe' or 'World'

    @property
    def allowed(self):
        """Rule 4: the countries named; if none, the fixed set of the region;
        with fewer than four named, the fixed set may fill up to four."""
        if not self.named:
            return list(self.fixed)
        fill = [c for c in self.fixed if c not in self.named] if len(self.named) < MAX_COUNTRIES else []
        return list(self.named) + fill


def _text(obj, field_name):
    from app import content_i18n
    original = getattr(obj, field_name) or ''
    english = content_i18n.text_for(obj, field_name, 'en')   # the original itself when it is English
    return original, english


def owid_name(iso2):
    """The name OWID gives one of our countries (its entity name)."""
    from app.sources import countries
    return _OWID_NAMES.get(iso2) or countries.english_name(iso2)


def _country_names():
    """{iso2: set of names} for our country list: nl and en from our list,
    de, es and fr from Babel, and the name OWID uses. Country names only:
    adjectives ("Duitse", "Polish") are not reliable enough to match."""
    from babel import Locale
    from app.sources import countries
    rows = countries._our_rows()
    babel = {lang: Locale.parse(lang).territories for lang in ('de', 'es', 'fr')}
    out = {}
    for iso2, names in rows.items():
        found = {n for n in names.values() if n}
        found.update(t[iso2] for t in babel.values() if t.get(iso2))
        if owid_name(iso2):
            found.add(owid_name(iso2))
        out[iso2] = found
    return out


def named_countries(texts):
    """ISO2 codes of the countries of our list named in the texts: whole
    words, any case, in nl, en, de, es or fr."""
    blob = ' '.join(t for t in texts if t)
    out = []
    for iso2, names in _country_names().items():
        if any(re.search(rf'(?<!\w){re.escape(name)}(?!\w)', blob, flags=re.I) for name in names):
            out.append(iso2)
    return sorted(out)


def region_for(texts, named):
    """'World' when the texts name a country outside Europe or the world
    itself ("wereld", "world", "global" ...); else 'Europe'."""
    blob = ' '.join(t for t in texts if t)
    if any(code not in EUROPEAN for code in named) or _WORLD_WORDS.search(blob):
        return 'World'
    return 'Europe'


def _mentioned_countries(texts):
    """English (OWID) names of the countries named in the texts."""
    return sorted(filter(None, (owid_name(code) for code in named_countries(texts))))


def _all_texts(issue, proposals):
    """The originals and every translation there is of the issue's title and
    introduction and of the statements and explanations of its proposals."""
    from app import content_i18n, i18n
    content_i18n.preload([issue], 'title', 'intro')
    content_i18n.preload(proposals, 'statement', 'explanation')
    texts = []
    for obj, fields in [(issue, ('title', 'intro'))] + [(p, ('statement', 'explanation')) for p in proposals]:
        for name in fields:
            texts.append(getattr(obj, name) or '')
            for lang in i18n.enabled_codes():
                texts.append(content_i18n.text_for(obj, name, lang) or '')
    return texts


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
    texts = _all_texts(issue, ctx.proposals)
    ctx.named = named_countries(texts)
    ctx.mentioned = sorted(filter(None, (owid_name(code) for code in ctx.named)))
    ctx.region = region_for(texts, ctx.named)
    ctx.fixed = list(EUROPE_SET if ctx.region == 'Europe' else WORLD_SET)
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
        lines.append('Charts already chosen: ' + ', '.join(memory_key(c.source, c.slug) for c in ctx.charts))
    return '\n'.join(lines)


def _block_names():
    return [EU_BLOCK] + [owid_name(c) for c in BLOCK_SET]


def _relevant_entities(hit, ctx):
    """The entities of a candidate the AI may use: the allowed countries
    (rule 4, by their OWID names) and what can serve as an average."""
    wanted = {owid_name(code) for code in ctx.allowed} | {WORLD_AVERAGE, EU_BLOCK} | {owid_name(c) for c in BLOCK_SET}
    out = []
    for name in hit.entities:
        if name in wanted or name in _AVERAGE_NAMES or name.startswith(_AVERAGE_PREFIXES):
            out.append(name)
    return out


def _eurostat_entities(ctx):
    """What the AI may choose in a Eurostat table: the allowed countries
    (rule 4) that Eurostat covers, and the EU-27 as the average."""
    return [owid_name(code) for code in ctx.allowed if code in EUROPEAN] + [WORLD_AVERAGE]


def _candidate_lines(ctx, hit, both):
    if not both:
        entities = ', '.join(_relevant_entities(hit, ctx)) or '(none of the usual ones)'
        return f'- slug: {hit.slug}\n  title: {hit.title}\n  subtitle: {hit.subtitle}\n  entities: {entities}'
    if hit.source == 'eurostat':
        kind = 'main table' if hit.kind == 'table' else 'dataset'
        return (f'- id: {hit.id}\n  source: Eurostat ({kind})\n  title: {hit.title}\n  period: {hit.period}\n'
                f'  entities: {", ".join(_eurostat_entities(ctx))} (Eurostat: EU, EFTA and candidate countries only)')
    entities = ', '.join(_relevant_entities(hit, ctx)) or '(none of the usual ones)'
    return (f'- id: {hit.id}\n  source: Our World in Data\n  title: {hit.title}\n  subtitle: {hit.subtitle}\n'
            f'  entities: {entities}')


def selection_prompt(ctx, candidates, both=False):
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
    # Rule 4, decided by the app: only these countries may be chosen.
    named = [owid_name(c) for c in ctx.named]
    fill = [owid_name(c) for c in ctx.allowed if c not in ctx.named]
    if named:
        lines.append('Countries you may choose (named in the issue or the proposals): ' + ', '.join(named))
        if fill:
            lines.append(f'Only to fill up to {MAX_COUNTRIES} countries, from the fixed set of the region '
                         f'({ctx.region}): ' + ', '.join(fill))
    else:
        lines.append(f'No country is named. Countries you may choose (the fixed set of the region, {ctx.region}): '
                     + ', '.join(fill))
    if ctx.region == 'World':
        lines.append(f'For the world, the average may be {WORLD_AVERAGE} if the source has it.')
    lines.append('Any other country is dropped by the app.')
    lines.append('Or, with compare "blocks", only the world blocks: ' + ', '.join(_block_names())
                 + ' (no average).')
    if ctx.charts:
        lines.append(f'Charts already chosen ({len(ctx.charts)} of at most {MAX_CHARTS}):')
        for chart in ctx.charts:
            title = chart.post.fact_title if chart.post is not None else chart.slug
            snapshot = chart.current_snapshot
            names = snapshot.values.get('names', {}) if snapshot else {}
            countries = ', '.join(names.get(k, k) for k in chart.entity_keys)
            lines.append(f'- fact_post_id {chart.post_id}: {memory_key(chart.source, chart.slug)} "{title}" '
                         f'({countries}); '
                         f'linked to proposals {_links_of(chart)}')
    lines.append('Candidates:')
    for hit in candidates:
        lines.append(_candidate_lines(ctx, hit, both))
    return '\n'.join(lines)


def dimension_prompt(needing, candidates):
    """The Eurostat tables of a round with a dimension that has several values:
    per table its id, title and those dimensions (id, label, code = label)."""
    lines = []
    for cid, (dims, _since) in needing.items():
        lines.append(f'Table {cid}: {candidates[cid].title}')
        for dim in dims:
            if len(dim.values) > 1:
                codes = '; '.join(f'{code} = {label}' for code, label in dim.values)
                lines.append(f'- dimension {dim.id} ({dim.label}): {codes}')
    return '\n'.join(lines)


# ── Candidates (step 3b: from both sources) ──────────────────────────────

@dataclass
class Candidate:
    id: str                      # "owid:<slug>" or "eurostat:<code>"
    source: str                  # 'owid' or 'eurostat'
    slug: str                    # the OWID slug or the Eurostat code
    title: str
    subtitle: str = ''
    entities: list = field(default_factory=list)    # OWID: the entities the search reports
    period: str = ''             # Eurostat: data start to data end in the catalogue
    kind: str = ''               # Eurostat: 'table' or 'dataset'

    @property
    def key(self):
        return memory_key(self.source, self.slug)


def _owid_candidate(hit):
    return Candidate(id=f'owid:{hit.slug}', source='owid', slug=hit.slug, title=hit.title, subtitle=hit.subtitle,
                     entities=list(hit.entities or []))


def _eurostat_candidate(hit):
    period = f'{hit.data_start} to {hit.data_end}' if hit.data_start else hit.data_end
    return Candidate(id=f'eurostat:{hit.code}', source='eurostat', slug=hit.code, title=hit.title, period=period,
                     kind=hit.type)


def _item_id(item):
    """The candidate id of a chart in an answer: "id" (two sources) or
    "slug" (OWID only); a bare slug is an OWID one."""
    raw = (item.get('id') or item.get('slug')) if isinstance(item, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        return None
    raw = raw.strip()
    return raw if raw.startswith(('owid:', 'eurostat:')) else f'owid:{raw}'


def _label(cid):
    """How a chart is named in the notes and the memories: the OWID slug,
    or "eurostat:<code>"."""
    if cid is None:
        return None
    return cid.split(':', 1)[1] if cid.startswith('owid:') else cid


def _years(item):
    try:
        return max(1, int(item.get('years') or DEFAULT_YEARS))
    except (TypeError, ValueError):
        return DEFAULT_YEARS


# ── The choice per dimension (step 3b, Eurostat) ─────────────────────────

@dataclass
class Prepared:
    data: dict                   # the JSON-stat, with the filters of the choice
    filters: dict                # {dimension: chosen code}; {} when nothing had to be chosen


def _choice_problem(choice, dims):
    """Why a choice per dimension is not valid, or None: every dimension with
    several values has exactly one code from its list; a code for any other
    dimension must be in its list too; for a breakdown it is the total (step
    3c; a measure such as the unit is free)."""
    from app.sources import eurostat
    if not isinstance(choice, dict):
        return 'no choice for this table'
    known = {d.id: [code for code, _label in d.values] for d in dims}
    for dim_id, code in choice.items():
        if dim_id not in known:
            return f'{dim_id!r} is no dimension of this table'
        if not isinstance(code, str) or code not in known[dim_id]:
            return f'{code!r} is not in the list of {dim_id}'
    missing = [d.id for d in dims if len(d.values) > 1 and d.id not in choice]
    if missing:
        return 'no code for ' + ', '.join(missing)
    # Step 3c: a breakdown (not a measure) must be its total.
    for dim in dims:
        if len(dim.values) > 1 and not eurostat.is_measure(dim.id) and choice[dim.id] not in eurostat.totals_of(dim):
            return f'{choice[dim.id]!r} is not the total of the breakdown {dim.id}'
    return None


def prepare_eurostat(items, ctx, candidates, client, http, out, now=None):
    """Before the checks: for every chosen Eurostat table the data without
    dimension filters (since the period), its dimensions, and, when one or more
    have several values, one extra call of SELECTION_MODEL for all tables of the
    round together. {candidate id: Prepared or (reason, detail)}. A Eurostat
    chart for the world or for blocks falls away at once ('source_scope'); a
    table with a breakdown of several values and no total too ('no_total',
    step 3c, remembered like unsupported)."""
    from app.sources import SourceError, eurostat
    result, needing = {}, {}
    year = (now or datetime.utcnow()).year
    for item in items:
        cid = _item_id(item)
        if cid is None or not cid.startswith('eurostat:') or cid not in candidates or cid in result or cid in needing:
            continue
        if ctx.region == 'World' or (item.get('compare') or 'countries') == 'blocks':
            result[cid] = ('source_scope', 'Eurostat covers EU, EFTA and candidate countries only, no world '
                                           'and no blocks')
            continue
        since = year - _years(item) - MAX_DATA_AGE_YEARS
        try:
            data = eurostat.fetch(candidates[cid].slug, since=since, http=http)
            dims = eurostat.describe_dimensions(data)
        except SourceError as exc:
            if exc.reason == 'network':
                raise
            result[cid] = (exc.reason, exc.detail)
            continue
        lacking = eurostat.without_total(dims)
        if lacking:   # step 3c: no breakdown without a total
            result[cid] = ('no_total', 'no total in ' + ', '.join(d.id for d in lacking))
            continue
        if all(len(d.values) == 1 for d in dims):
            result[cid] = Prepared(data=data, filters={})
        else:
            needing[cid] = (dims, since)
    if not needing:
        return result
    user_msg = dimension_prompt(needing, candidates)
    out.prompts.append((_DIMENSION_SYSTEM, user_msg))
    text = _ask(client, SELECTION_MODEL, _DIMENSION_SYSTEM, user_msg, 1500)
    out.responses.append(text)
    answer = _parse_json(text)
    out.dimension_answers.append(answer)
    for cid, (dims, since) in needing.items():
        code = candidates[cid].slug
        choice = answer.get(cid, answer.get(code))
        problem = _choice_problem(choice, dims)
        if problem:
            result[cid] = ('dimension_choice', problem)
            continue
        filters = {d.id: choice[d.id] for d in dims if len(d.values) > 1}
        out.dimension_choices[cid] = filters
        try:
            data = eurostat.fetch(code, filters=filters, since=since, http=http)
        except SourceError as exc:
            if exc.reason == 'network':
                raise
            result[cid] = (exc.reason, exc.detail)
            continue
        result[cid] = Prepared(data=data, filters=filters)
    return result


def _eurostat_names(parsed):
    """{name: key} of a Eurostat table: its own English labels, and for our
    countries the names the AI sees (as for OWID), the EU-27 also as
    "European Union (27)"."""
    from app.sources import countries
    by_name = {}
    for key, name in parsed.names.items():
        by_name.setdefault(name, key)
        iso2 = countries.iso2_for(key)
        if iso2:
            for other in (owid_name(iso2), countries.english_name(iso2)):
                if other:
                    by_name.setdefault(other, key)
        if key == EUROSTAT_AVERAGE:
            by_name[WORLD_AVERAGE] = key
    return by_name


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
    compare: str = 'countries'
    source: str = 'owid'
    filters: dict = None         # Eurostat: the choice per dimension

    @property
    def key(self):
        return memory_key(self.source, self.slug)


def _int_list(values):
    out = []
    for value in values if isinstance(values, list) else []:
        try:
            out.append(int(value))
        except (TypeError, ValueError):
            continue
    return out


def _is_block(key, name):
    """Whether an entity of the data is one of the world blocks."""
    from app.sources import countries
    if key is None:
        return False
    return name == EU_BLOCK or key == 'OWID_EU27' or countries.iso2_for(key) in BLOCK_SET


def aggregate_is_total(parsed, average_key, keys, year_from, year_to):
    """Whether the chosen average is really a sum: in every year of the
    period in which it and a chosen country have a value, it is at least
    AGGREGATE_TOTAL_FACTOR times the largest country (absolute values). OWID
    gives a regional total for an absolute measure (tonnes, people); that
    line would dwarf the countries."""
    average = parsed.series.get(average_key, {})
    compared = 0
    for year, value in average.items():
        if not year_from <= year <= year_to:
            continue
        found = [abs(parsed.series[k][year]) for k in keys if parsed.series.get(k, {}).get(year) is not None]
        if not found or value is None:
            continue
        compared += 1
        if abs(value) < AGGREGATE_TOTAL_FACTOR * max(found):
            return False
    return compared > 0


def _fell_away(label, reason, notes, reasons, detail=''):
    notes.append(f'{label}: {reason}' + (f' ({detail})' if detail and reason in ('dimension_choice', 'source_scope',
                                                                                'needs_choice', 'no_total')
                                         else ''))
    remember_rejected(label, reason)
    if reasons is not None:
        reasons[label] = reason


def check_chart(item, ctx, candidates, http, notes, now=None, reasons=None, prepared=None):
    """An Accepted chart, or None (with the reason in notes). Nothing is
    repaired or guessed: a country that fails falls away; an average that
    fails means no average. candidates: {id: Candidate}; prepared: the
    Eurostat tables after the choice per dimension (prepare_eurostat)."""
    from app.sources import SourceError, countries, eurostat, owid
    cid = _item_id(item)
    slug = _label(cid)
    if cid not in candidates:
        notes.append(f'{slug}: not one of the candidates')
        return None
    candidate = candidates[cid]
    filters = None
    if candidate.source == 'eurostat':
        ready = (prepared or {}).get(cid)
        if ready is None:
            ready = ('dimension_choice', 'not prepared')
        if isinstance(ready, tuple):
            _fell_away(slug, ready[0], notes, reasons, ready[1])
            return None
        try:
            parsed = eurostat.parse(ready.data, candidate.slug)
        except SourceError as exc:
            # needs_choice here: still several values after the filters of the choice.
            _fell_away(slug, exc.reason, notes, reasons, exc.detail)
            return None
        metadata, filters = eurostat.metadata_of(ready.data), ready.filters
        selection = eurostat.selection_text(ready.data, filters)   # step 3c: "Selection", without the unit
        if selection:
            parsed.labels = dict(parsed.labels or {}, selection=selection)
        by_name = _eurostat_names(parsed)
    else:
        try:
            csv_text, metadata = owid.fetch(candidate.slug, http=http)
            parsed = owid.parse(csv_text, metadata, slug=candidate.slug)
        except SourceError as exc:
            if exc.reason == 'network':
                raise
            _fell_away(slug, exc.reason, notes, reasons)
            return None
        by_name = {name: key for key, name in parsed.names.items()}
    compare = item.get('compare') or 'countries'
    if compare not in COMPARES:
        notes.append(f'{slug}: compare {compare!r} is neither countries nor blocks')
        return None
    chosen = (item.get('countries') or [])[:MAX_COUNTRIES] if isinstance(item.get('countries'), list) else []
    if compare == 'blocks':
        others = [name for name in chosen if not _is_block(by_name.get(name), name)]
        if others:
            notes.append(f'{slug}: blocks_mixed_with_countries ({", ".join(map(repr, others))})')
            return None
    elif EU_BLOCK in chosen:
        notes.append(f'{slug}: eu_line_in_countries_chart (the European Union (27) is only a line with '
                     f'compare "blocks"; in a countries chart it is the average)')
        return None
    keys = []
    for name in chosen:
        key = by_name.get(name)
        if key is None:
            notes.append(f'{slug}: country {name!r} is not in the data')
        elif compare == 'blocks':
            if key not in keys:
                keys.append(key)
        elif countries.iso2_for(key) is None:
            notes.append(f'{slug}: {name!r} is not a country of our list')
        elif countries.iso2_for(key) not in ctx.allowed:
            notes.append(f'{slug}: {name!r} country_not_allowed (rule 4)')
        elif key not in keys:
            keys.append(key)
    if not keys:
        notes.append(f'{slug}: no country left')
        return None
    years = _years(item)
    year_to = max(max(parsed.series[k]) for k in keys)
    if (now or datetime.utcnow()).year - year_to > MAX_DATA_AGE_YEARS:
        notes.append(f'{slug}: stale (the last data is from {year_to})')
        remember_rejected(slug, 'stale')
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
    if average and compare == 'blocks':
        notes.append(f'{slug}: average {average!r} with compare "blocks", so no average')
    elif average:
        key = by_name.get(average)
        if key is None:
            notes.append(f'{slug}: average {average!r} is not in the data, so no average')
        elif candidate.source == 'eurostat' and key != EUROSTAT_AVERAGE:
            notes.append(f'{slug}: average {average!r} is not the EU-27 of Eurostat, so no average')
        elif not owid.has_values(parsed, key, year_from, year_to):
            notes.append(f'{slug}: average {average!r} has no data in the period, so no average')
        elif aggregate_is_total(parsed, key, kept, year_from, year_to):
            notes.append(f'{slug}: average {average!r} aggregate_is_total (at least {AGGREGATE_TOTAL_FACTOR} times '
                         f'the largest country in every year), so no average')
        else:
            average_key = key
    open_ids = {p.id for p in ctx.proposals}
    proposal_ids = [pid for pid in dict.fromkeys(_int_list(item.get('relevant_for'))) if pid in open_ids]
    reason = str(item.get('reason') or '').strip()
    if not reason:
        notes.append(f'{slug}: no reason')
        return None
    return Accepted(slug=candidate.slug, parsed=parsed, metadata=metadata, keys=kept, average_key=average_key,
                    year_from=year_from, year_to=year_to, reason=reason, proposal_ids=proposal_ids,
                    compare=compare, source=candidate.source, filters=filters)


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


# ── Charts no issue can use (step 1i) ────────────────────────────────────

def remember_rejected(slug, reason, now=None):
    """A chart fell away for a lasting reason (unsupported, forbidden,
    no_data, stale, and for Eurostat needs_choice after the filters): remember
    it for every issue, so the search leaves it out for REJECT_MEMORY_DAYS.
    Network errors never get here. Does not commit. The table is
    owid_rejected_slugs for both sources: an OWID chart by its slug, a
    Eurostat table as "eurostat:<code>" (memory_key, step 3b)."""
    from app.models import OwidRejectedSlug
    if reason not in LASTING_REASONS or not slug:
        return
    row = OwidRejectedSlug.query.filter_by(slug=slug).first()
    if row is None:
        row = OwidRejectedSlug(slug=slug)
        db.session.add(row)
    row.reason, row.checked_at = reason, now or datetime.utcnow()


def recently_rejected(now=None):
    """The slugs rejected for a lasting reason in the last REJECT_MEMORY_DAYS."""
    from app.models import OwidRejectedSlug
    since = (now or datetime.utcnow()) - timedelta(days=REJECT_MEMORY_DAYS)
    return {slug for (slug,) in db.session.query(OwidRejectedSlug.slug).filter(OwidRejectedSlug.checked_at >= since)}


# ── Freezing at the voting phase (step 1g) ──────────────────────────────

def freeze_for_issue(issue_id, now=None):
    """Set frozen_at on the current snapshot of every chart of this issue
    that is not frozen yet, in one UPDATE (issue_lifecycle.set_phase, when the
    voting phase starts). A frozen snapshot never changes again, also not
    when the issue is reopened; a newer one could only come next to it
    (create_snapshot takes the next version). Does not commit."""
    from sqlalchemy import and_, select, update
    from app.models import ChartSnapshot, FactChart
    newer = ChartSnapshot.__table__.alias('newer')
    current = (select(ChartSnapshot.id).join(FactChart, FactChart.id == ChartSnapshot.chart_id)
               .where(FactChart.issue_id == issue_id)
               .where(~select(newer.c.id).where(and_(newer.c.chart_id == ChartSnapshot.chart_id,
                                                     newer.c.version > ChartSnapshot.version)).exists()))
    db.session.execute(update(ChartSnapshot)
                       .where(ChartSnapshot.id.in_(current.scalar_subquery()))
                       .where(ChartSnapshot.frozen_at.is_(None))
                       .values(frozen_at=now or datetime.utcnow())
                       .execution_options(synchronize_session=False))


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
    source_name: str = 'Our World in Data'   # step 3c: the name of the source (source_name)


_OWID_SUFFIX = re.compile(r'(?:^|\s+)[\u2013\u2014-]\s*with\b.*$', flags=re.S)
_TRAILING_DASH = re.compile(r'(?:^|\s+)[\u2013\u2014-]\s*$')


# Step 3c: the name of each source as the page shows it (a proper name, no gettext).
SOURCE_NAMES = {'owid': 'Our World in Data', 'eurostat': 'Eurostat'}


def source_name(chart):
    return SOURCE_NAMES.get(chart.source, chart.source or '')


def eurostat_citation(chart):
    """The short source of a Eurostat chart: "Eurostat (<code>)"."""
    return f'Eurostat ({chart.slug})'


def short_citation(snapshot):
    """The short source of a chart without OWID's suffix: citationShort
    ("Stockholm International Peace Research Institute (2026) \u2013 with minor
    processing by Our World in Data") cut before " \u2013 with" or " - with",
    and a dash left at the end removed. Empty after that: the full citation.
    The caller adds ", via Our World in Data"; no dash reaches the page."""
    short = _OWID_SUFFIX.sub('', (snapshot.citation_short or '').strip())
    short = _TRAILING_DASH.sub('', short).strip()
    return short or (snapshot.citation or '').strip()


def display_names(chart, snapshot, lang):
    """({key: name in the reader's language}, name of the average or ''):
    countries from our list or Babel, the average through the translation
    route (English until it is translated). The caller preloads average_name."""
    from app import content_i18n
    from app.sources import countries
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
    elif chart.average_name:
        # World blocks (step 1l): the EU is an ordinary line; its name goes through the translation route.
        for key in chart.entity_keys:
            if key not in names and key in data.get('names', {}):
                names[key] = content_i18n.display(chart, 'average_name', lang)
    return names, average


# ── The chart page (step 1f) ─────────────────────────────────────────────

VIEWS = ('grafiek', 'tabel')
DEFAULT_VIEW = 'grafiek'
DEFAULT_PERIOD = '10'
PERIODS = {'5': 5, '10': 10, 'alles': None}


@dataclass
class PageState:
    """What the reader chose on the chart page (?weergave, ?periode, ?verberg)."""
    view: str = DEFAULT_VIEW
    period: str = DEFAULT_PERIOD
    default_period: str = DEFAULT_PERIOD           # 'alles' when the AI chose more than ten years
    hidden: list = field(default_factory=list)
    periods: tuple = ('5', '10')
    keys: list = field(default_factory=list)       # every key that can be switched, in legend order
    change: tuple = None                           # (what, value) of ?zet=, for the announcement

    def params(self, **override):
        """The query parameters of this state (defaults left out)."""
        view, period = override.get('view', self.view), override.get('period', self.period)
        hidden = override.get('hidden', self.hidden)
        out = {}
        if view != DEFAULT_VIEW:
            out['weergave'] = view
        if period != self.default_period:
            out['periode'] = period
        if hidden:
            out['verberg'] = ','.join(hidden)
        return out


def switchable_keys(chart, snapshot):
    """The entity keys the legend switches: the shown countries (at most the
    large chart's eight) and the average, as far as they are in the data."""
    from app import charts
    series = snapshot.values.get('series', {})
    keys = [k for k in chart.entity_keys if k in series and k != chart.average_key]
    keys = keys[:charts._SIZES[charts.LARGE]['max_series']]
    if chart.average_key and chart.average_key in series:
        keys.append(chart.average_key)
    return keys


def page_state(args, chart, snapshot):
    """The state from the query string, strictly checked: an invalid value
    becomes the default, unknown keys fall away. ?zet= applies one change
    (weergave:tabel, periode:5, wissel:<key>) on top of the state."""
    keys = switchable_keys(chart, snapshot)
    periods = ('5', '10', 'alles') if len(snapshot.values.get('years', [])) > 10 else ('5', '10')
    # The AI chose a longer period (its reason may count on it): open on all years.
    default = 'alles' if 'alles' in periods and chart.year_to - chart.year_from + 1 > 10 else DEFAULT_PERIOD
    view = args.get('weergave') if args.get('weergave') in VIEWS else DEFAULT_VIEW
    period = args.get('periode') if args.get('periode') in periods else default
    hidden = [k for k in dict.fromkeys((args.get('verberg') or '').split(',')) if k in keys]
    change = None
    what, _sep, value = (args.get('zet') or '').partition(':')
    if what == 'weergave' and value in VIEWS:
        view, change = value, ('view', value)
    elif what == 'periode' and value in periods:
        period, change = value, ('period', value)
    elif what == 'wissel' and value in keys:
        hidden = [k for k in hidden if k != value] if value in hidden else hidden + [value]
        change = ('hidden' if value in hidden else 'shown', value)
    hidden = [k for k in keys if k in hidden]   # legend order
    return PageState(view=view, period=period, default_period=default, hidden=hidden, periods=periods, keys=keys,
                     change=change)


def page_view(chart, snapshot, state, lang):
    """The large ChartView for the chart page in this state."""
    from app import charts
    names, _average = display_names(chart, snapshot, lang)
    return charts.layout(snapshot.values, chart.entity_keys, chart.average_key, charts.LARGE, lang,
                         PERIODS[state.period], set(state.hidden), names, snapshot.short_unit or '')


def chart_cards(posts, lang):
    """{post_id: ChartCard} for the chart facts among `posts`: two queries for
    the charts and snapshots and one for the translated average names,
    however many facts there are."""
    from babel.lists import format_list
    from flask_babel import format_date

    from app import charts, content_i18n
    found = charts_for(posts)
    if not found:
        return {}
    content_i18n.preload([chart for chart, _s in found.values()], 'average_name')
    by_id = {p.id: p for p in posts if p is not None}
    out = {}
    for post_id, (chart, snapshot) in found.items():
        data = snapshot.values
        names, average = display_names(chart, snapshot, lang)
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
            # OWID: citation_short without its suffix (and its dash); Eurostat: "Eurostat (<code>)".
            citation=eurostat_citation(chart) if chart.source == 'eurostat' else short_citation(snapshot),
            source_url=snapshot.source_url or '', source_name=source_name(chart),
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
    retried: bool = False                                # one retry after every chart was unsupported
    retry_answer: dict = field(default_factory=dict)
    # Step 3b (Eurostat): the answers of the choice per dimension and the valid choices.
    dimension_answers: list = field(default_factory=list)
    dimension_choices: dict = field(default_factory=dict)   # {candidate id: {dimension: code}}
    prepared: dict = field(default_factory=dict)            # {candidate id: Prepared or (reason, detail)}


def select(ctx, client, http=None):
    """The round without saving: terms, candidates, choice, checks."""
    from app import quality
    from app.sources import SourceError, eurostat, owid
    out = Outcome()
    both = eurostat_enabled()
    terms_system = _TERMS_SYSTEM_BOTH if both else _TERMS_SYSTEM
    selection_system = _SELECTION_SYSTEM_BOTH if both else _SELECTION_SYSTEM
    user_msg = terms_prompt(ctx)
    out.prompts.append((terms_system, user_msg))
    text = _ask(client, quality.JUDGE_MODEL, terms_system, user_msg, 300)
    out.responses.append(text)
    terms = [t.strip() for t in _parse_json(text).get('terms') or [] if isinstance(t, str) and t.strip()]
    out.terms = terms[:MAX_TERMS]
    if not out.terms:
        out.none_reason = 'no search terms'
        return out

    # Blocked (removed here) and rejected (for every issue) charts by their
    # memory key: the OWID slug, or "eurostat:<code>".
    used = {memory_key(c.source, c.slug) for c in ctx.charts} | recently_rejected() | set(ctx.blocked_slugs)
    candidates, errors = {}, 0
    max_owid = MAX_OWID_CANDIDATES if both else MAX_CANDIDATES
    for term in out.terms:
        try:
            hits = owid.search(term, hits=SEARCH_HITS, http=http)
        except SourceError as exc:
            errors += 1
            out.notes.append(f'search {term!r}: {exc.reason}')
            continue
        for hit in hits:
            candidate = _owid_candidate(hit)
            if candidate.key in used or candidate.id in candidates:
                continue
            if len(candidates) < max_owid:
                candidates[candidate.id] = candidate
    if errors == len(out.terms):
        raise SourceError('network', 'every search failed')
    if both:
        # Step 3c: per term, at most 10 main tables and 5 yearly datasets (fixed places).
        exclude = {key.split(':', 1)[1] for key in used if key.startswith(eurostat.AGGREGATE_PREFIX)}
        for hit in eurostat.search_catalog(out.terms, exclude=exclude):
            candidate = _eurostat_candidate(hit)
            candidates.setdefault(candidate.id, candidate)
    out.candidates = list(candidates.values())
    if not out.candidates and not ctx.charts:
        out.none_reason = 'no candidates'
        return out

    user_msg = selection_prompt(ctx, out.candidates, both)
    out.prompts.append((selection_system, user_msg))
    text = _ask(client, SELECTION_MODEL, selection_system, user_msg, 2000)
    out.responses.append(text)
    out.answer = _parse_json(text)
    out.none_reason = str(out.answer.get('none_reason') or '')

    room = MAX_CHARTS - len(ctx.charts)
    items = out.answer.get('charts') if isinstance(out.answer.get('charts'), list) else []
    reasons = {}
    prepared = prepare_eurostat(items, ctx, candidates, client, http, out) if both else {}
    out.prepared.update(prepared)
    _check_items(items, ctx, candidates, http, out, room, reasons, prepared)
    # One retry in the same round: every new chart fell away because it shows
    # several measures. Same candidates minus those; link_existing stays as
    # the first answer gave it.
    tried = [_label(_item_id(item)) for item in items if isinstance(item, dict)]
    if items and not out.accepted and tried and all(reasons.get(label) == 'unsupported' for label in tried):
        remaining = [hit for hit in out.candidates if hit.key not in reasons]
        user_msg = selection_prompt(ctx, remaining, both) + (
            '\n\nThese charts could not be used because they show several measures: '
            + ', '.join(tried) + '. Choose again, or choose none.')
        out.prompts.append((selection_system, user_msg))
        text = _ask(client, SELECTION_MODEL, selection_system, user_msg, 2000)
        out.responses.append(text)
        out.retried = True
        retry = _parse_json(text)
        out.retry_answer = retry
        if retry.get('none_reason'):
            out.none_reason = str(retry.get('none_reason'))
        again = retry.get('charts') if isinstance(retry.get('charts'), list) else []
        allowed = {hit.id: hit for hit in remaining}
        prepared = prepare_eurostat(again, ctx, allowed, client, http, out) if both else {}
        out.prepared.update(prepared)
        _check_items(again, ctx, allowed, http, out, room, {}, prepared)
    out.links = check_links(out.answer, ctx, out.notes)
    out.status = 'done' if out.accepted or out.links else 'nothing'
    return out


def _check_items(items, ctx, candidates, http, out, room, reasons, prepared=None):
    """Check the charts of one answer into out.accepted (at most `room`)."""
    for item in items:
        label = _label(_item_id(item))
        if len(out.accepted) >= room:
            out.notes.append(f'{label if label is not None else item}: more than {MAX_CHARTS} charts for this issue')
            continue
        if label is not None and any(a.key == label for a in out.accepted):
            continue
        accepted = check_chart(item, ctx, candidates, http, out.notes, reasons=reasons, prepared=prepared)
        if accepted is not None:
            out.accepted.append(accepted)


def publish(ctx, outcome):
    """The facts, charts, snapshots and links of a round. Does not commit."""
    from app import content_jobs
    from app.models import (FACT_KIND, SYSTEM_NAME, SYSTEM_ROLE, SYSTEM_USERNAME, FactChart, IssueProposalFact,
                            Post)
    from app.sources import countries, eurostat, owid
    for item in outcome.accepted:
        title = item.parsed.title or item.slug
        if len(title) > 100:
            title = title[:99].rstrip() + '…'
        default_url = (f'https://ourworldindata.org/grapher/{item.slug}' if item.source == 'owid'
                       else eurostat.BROWSER_URL.format(code=item.slug))
        post = Post(thread_id=ctx.issue.thread_id, user_id=None, author_name=SYSTEM_NAME,
                    author_username=SYSTEM_USERNAME, author_role=SYSTEM_ROLE, body=item.reason, source_lang='en',
                    kind=FACT_KIND, fact_type='cijfer', fact_direction='neutraal', fact_title=title,
                    fact_source=(item.parsed.source_url or default_url)[:500])
        db.session.add(post)
        db.session.flush()
        # Eurostat (step 3b): the code as slug, the one value column, the choice per dimension.
        chart = FactChart(post_id=post.id, issue_id=ctx.issue.id, source=item.source, slug=item.slug,
                          column_name=('value' if item.source == 'eurostat' else item.parsed.column_name)[:300],
                          average_key=item.average_key, year_from=item.year_from, year_to=item.year_to)
        chart.entity_keys = item.keys
        if item.source == 'eurostat':
            chart.filters = json.dumps(item.filters or {}, sort_keys=True)
        # The name of the average (or, with blocks, of the EU line) as the source writes it
        # (English); translated with the fact.
        chart.average_name = item.parsed.names.get(item.average_key) if item.average_key else None
        if item.compare == 'blocks':
            chart.average_name = next((item.parsed.names[k] for k in item.keys if countries.iso2_for(k) is None),
                                      None)
        db.session.add(chart)
        owid.create_snapshot(chart, item.parsed, item.metadata, item.year_from, item.year_to)
        for pid in item.proposal_ids:
            db.session.add(IssueProposalFact(proposal_id=pid, fact_post_id=post.id, linked_by_id=None, by_ai=True))
        content_jobs.schedule(post)   # title, reason and average name into the other languages; no judgement
    for post_id, pid in outcome.links:
        db.session.add(IssueProposalFact(proposal_id=pid, fact_post_id=post_id, linked_by_id=None, by_ai=True))


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


_SEPARATOR = '\n\n========\n\n'


def _record(run_row, outcome):
    from app import quality
    # Search terms, the choice and (if any) the retry, one after the other.
    run_row.prompt_text = _SEPARATOR.join(quality._prompt_record(system, user) for system, user in outcome.prompts)
    run_row.response_text = _SEPARATOR.join(outcome.responses)
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


def refresh_eurostat_catalog(now=None, http=None):
    """The tick (step 3a): refresh the catalogue of Eurostat's main tables
    when it is empty or older than seven days. None with the switch off,
    'fresh' when not needed, the number of tables, or 'failed' (the old
    catalogue stays)."""
    from app.sources import eurostat
    if not enabled():
        return None
    if not eurostat.catalog_due(now):
        return 'fresh'
    count = eurostat.refresh_catalog(http=http, now=now)
    return 'failed' if count is None else count


def refresh_selection_labels():
    """At start (idempotent): the "selection" label of every Eurostat
    snapshot computed again from the chart's filters and the snapshot's
    metadata_raw (since 3c without the unit). A changed label loses its
    translations, so the translation route translates it again; an empty
    one is removed (no line "Selection"). A snapshot whose filters or
    metadata cannot be read stays as it is. The number changed."""
    from app import content_i18n
    from app.models import FactChart
    from app.sources import eurostat
    changed = 0
    for chart in FactChart.query.filter_by(source='eurostat'):
        try:
            filters = json.loads(chart.filters) if chart.filters else {}
        except ValueError:
            continue
        if not isinstance(filters, dict):
            continue
        for snapshot in chart.snapshots:
            try:
                metadata = json.loads(snapshot.metadata_raw) if snapshot.metadata_raw else None
            except ValueError:
                metadata = None
            if not isinstance(metadata, dict) or not metadata.get('id'):
                continue
            labels = snapshot.label_texts
            new = eurostat.selection_text(metadata, filters)
            if labels.get('selection', '') == new:
                continue
            if new:
                labels['selection'] = new
            else:
                labels.pop('selection', None)
            snapshot.labels = json.dumps(labels, ensure_ascii=False, sort_keys=True) if labels else None
            content_i18n.invalidate(snapshot, 'label:selection')
            changed += 1
    if changed:
        db.session.commit()
        log.info('Background figures: %d Eurostat selection labels computed again', changed)
    return changed


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
