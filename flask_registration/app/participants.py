"""Representativeness: who votes (docs/representativiteit.md).

Step 1b: the voluntary answers of a user (birth year, gender, education, and
"Mijn land" on users.country), the section About me in the profile, clearing,
and the cleanup when an account or issue goes. Behind the switch
PARTICIPANT_PROFILE (config, environment variable, off by default).

The answers are stored as fixed codes; the labels come from gettext in the
functions below, never from the constants. Nobody but the user sees the
answers (K10); step 1d uses them in one place, the frozen totals.
Nothing here commits; the caller does.
"""
from datetime import date, datetime

from flask import abort, current_app
from flask_babel import gettext, pgettext

from app import db
from app.models import IssueParticipationStats, ParticipantProfile
from app.sources import countries

# The codes. "na" is "prefer not to say".
PREFER_NOT = 'na'
GENDERS = ('f', 'm', 'x', PREFER_NOT)
EDUCATION_LEVELS = ('basic', 'middle', 'higher', PREFER_NOT)
# A birth year of 0 means "prefer not to say" (an empty one: not answered).
BIRTH_YEAR_PREFER_NOT = 0
# The oldest choice, shown as "1920 or earlier".
OLDEST_YEAR = 1920
# The youngest voters are 16 (the year in which you turn 16), the minimum
# age of signing up.
YOUNGEST_AGE = 16

# The fixed age ladder (K4): every level consists exactly of groups of the
# finer one. (low, high) in years, both included; high None is "or older".
AGE_LADDER = {
    'fine': ((16, 24), (25, 34), (35, 44), (45, 54), (55, 64), (65, 74), (75, None)),
    'middle': ((16, 24), (25, 44), (45, 64), (65, None)),
    'coarse': ((16, 44), (45, None)),
}
AGE_LEVELS = ('fine', 'middle', 'coarse')

FIELDS = ('birth_year', 'gender', 'education', 'country')


def enabled(app=None):
    return bool((app or current_app).config.get('PARTICIPANT_PROFILE'))


def get(user):
    """The answers of `user`, or None."""
    if user is None or getattr(user, 'id', None) is None:
        return None
    return ParticipantProfile.query.filter_by(user_id=user.id).first()


def should_ask(user):
    """Step 1c: the card after the first vote is due (K1, K2): the switch on,
    a logged-in user that is not deleted, and the card was never shown."""
    if not enabled() or not getattr(user, 'is_authenticated', False) or getattr(user, 'is_deleted', False):
        return False
    profile = get(user)
    return profile is None or profile.asked_at is None


def mark_asked(user, now=None):
    """The card has been shown: it does not come back. Makes the row when
    there is none. Commit niet zelf."""
    profile = get(user)
    if profile is None:
        profile = ParticipantProfile(user_id=user.id)
        db.session.add(profile)
    if profile.asked_at is None:
        profile.asked_at = now or datetime.utcnow()
    return profile


def birth_year_choices(today=None):
    """The birth years to choose from, newest first: from the year in which
    you turn 16 back to 1921, then 1920 ("1920 or earlier")."""
    today = today or date.today()
    return list(range(today.year - YOUNGEST_AGE, OLDEST_YEAR - 1, -1))


# ── Labels (gettext; never in the constants) ──────────────────────────────

def age_group_label(low, high):
    if high is None:
        # TRANSLATORS: an age group in the representativeness block ("65 or older"); %(age)s is an age in years.
        return gettext('%(age)s or older', age=low)
    # TRANSLATORS: an age group in years, both limits included ("25 to 44" means 25 up to and including 44).
    return gettext('%(low)s to %(high)s', low=low, high=high)


def prefer_not_label():
    # TRANSLATORS: the answer to a voluntary question (birth year, gender, education, country) for someone who does not want to answer.
    return gettext('Prefer not to say')


def gender_label(code):
    if code == 'f':
        # TRANSLATORS: an answer to "What is your gender?" (voluntary question).
        return gettext('Woman')
    if code == 'm':
        # TRANSLATORS: an answer to "What is your gender?" (voluntary question).
        return gettext('Man')
    if code == 'x':
        # TRANSLATORS: an answer to "What is your gender?": another gender than woman or man.
        return pgettext('gender', 'Other')
    return prefer_not_label()


def education_label(code):
    """(name, explanation) of an education level."""
    if code == 'basic':
        return (
            # TRANSLATORS: education level, ISCED 0 to 2 (the first of three; "Basic", "Secondary", "Higher").
            gettext('Basic'),
            # TRANSLATORS: the explanation under "Basic" (ISCED 0 to 2). Give an example readers in your language recognise; never the Dutch names.
            gettext('Primary school or the first years of secondary school'))
    if code == 'middle':
        return (
            # TRANSLATORS: education level, ISCED 3 and 4 (the second of three; "Basic", "Secondary", "Higher").
            gettext('Secondary'),
            # TRANSLATORS: the explanation under "Secondary" (ISCED 3 and 4). Give an example readers in your language recognise (a vocational training of your country); never the Dutch names mbo or hbo.
            gettext('Completed secondary school, or vocational training'))
    if code == 'higher':
        return (
            # TRANSLATORS: education level, ISCED 5 to 8 (the third of three; "Basic", "Secondary", "Higher").
            gettext('Higher'),
            # TRANSLATORS: the explanation under "Higher" (ISCED 5 to 8). Give an example readers in your language recognise; never the Dutch names mbo or hbo.
            gettext('College or university'))
    return (prefer_not_label(), '')


def country_choices(lang):
    """[(iso2, name)] of our country list, sorted by the name in `lang`."""
    import unicodedata

    def key(item):
        plain = unicodedata.normalize('NFKD', item[1])
        return ''.join(c for c in plain if not unicodedata.combining(c)).casefold()
    return sorted(((code, countries.display_name(code, lang)) for code in countries.all_codes()), key=key)


def form_context(user, lang, today=None):
    """What the questions partial (participants/_questions.html) needs."""
    profile = get(user)
    example = AGE_LADDER['middle'][1]
    return {
        'profile': profile,
        'country': getattr(user, 'country', None),
        'years': birth_year_choices(today),
        'oldest_year': OLDEST_YEAR,
        'prefer_not': PREFER_NOT,
        'genders': [(code, gender_label(code)) for code in GENDERS],
        'education': [(code,) + education_label(code) for code in EDUCATION_LEVELS],
        'countries': country_choices(lang),
        'example_group': age_group_label(*example),
        'answered_at': profile.answered_at if profile else None,
    }


# ── Saving and clearing ───────────────────────────────────────────────────

def _parse(form, today):
    """The four answers from `form` as stored values, and whether at least
    one question was answered ("prefer not to say" counts). abort(400) on a
    value outside the choices."""
    raw = {name: (form.get(name) or '').strip() for name in FIELDS}
    values = {}
    year = raw['birth_year']
    if not year:
        values['birth_year'] = None
    elif year == PREFER_NOT:
        values['birth_year'] = BIRTH_YEAR_PREFER_NOT
    elif year.isdigit() and OLDEST_YEAR <= int(year) <= today.year - YOUNGEST_AGE:
        values['birth_year'] = int(year)
    else:
        abort(400)
    for name, allowed in (('gender', GENDERS), ('education', EDUCATION_LEVELS)):
        if raw[name] and raw[name] not in allowed:
            abort(400)
        values[name] = raw[name] or None
    country = raw['country']
    if country and country != PREFER_NOT and country not in countries.all_codes():
        abort(400)
    # users.country is only a country: "prefer not to say" is stored empty.
    values['country'] = country if country and country != PREFER_NOT else None
    return values, any(raw.values())


def save(user, form, today=None, now=None):
    """Save the answers of `user` from `form` (birth_year, gender, education,
    country). Empty is not answered; "na" is prefer not to say. A value
    outside the choices: abort(400), nothing saved."""
    values, answered = _parse(form, today or date.today())
    profile = get(user)
    if profile is None:
        profile = ParticipantProfile(user_id=user.id)
        db.session.add(profile)
    profile.birth_year = values['birth_year']
    profile.gender = values['gender']
    profile.education = values['education']
    profile.answered_at = (now or datetime.utcnow()) if answered else None
    user.country = values['country']
    return profile


def clear(user):
    """Clear the answers and "Mijn land". asked_at stays: the card after the
    first vote (step 1c) does not come back."""
    profile = get(user)
    if profile is not None:
        profile.birth_year = profile.gender = profile.education = profile.answered_at = None
    user.country = None


def delete_for_user(user_id):
    ParticipantProfile.query.filter_by(user_id=user_id).delete(synchronize_session=False)


def delete_for_issue(issue_id):
    IssueParticipationStats.query.filter_by(issue_id=issue_id).delete(synchronize_session=False)


# ── Step 1d: freezing the totals and the block "Who voted" ────────────────
# docs/representativiteit.md K4, K7, K8. Only totals after the thresholds,
# as codes; the display translates them. The thresholds apply when freezing,
# so what is stored is already safe to show.

MIN_VOTERS = 30     # fewer: only the sentence "Too few participants ..."
MIN_GROUP = 10      # a group of 1 to 9 people is too small to show
NA = 'na'           # not filled in, prefer not to say, or merged in
SMALL = 'small'     # "Smaller groups combined" (gender, education)
OTHER = 'other'     # "Other countries combined"
TRAITS = ('age', 'gender', 'education', 'country')


def _too_small(n):
    return 0 < n < MIN_GROUP


def age_code(low, high):
    return f'{low}_plus' if high is None else f'{low}_{high}'


def parse_age_code(code):
    """(low, high) of an age group code ("25_44", "65_plus")."""
    low, high = code.split('_')
    return int(low), (None if high == 'plus' else int(high))


def count_ages(birth_years, year, voters):
    """(age_level, [[code, n], ...]) for the ages at `year`: the finest level of
    AGE_LADDER on which no group has 1 to 9 people (a group of 0 is fine),
    then "na" for everyone without a birth year. No level: ('none', [])."""
    ages = [max(year - b, YOUNGEST_AGE) for b in birth_years if b]   # 0 and empty: not filled in
    for level in AGE_LEVELS:
        groups = [[age_code(low, high), sum(1 for a in ages if a >= low and (high is None or a <= high))]
                  for low, high in AGE_LADDER[level]]
        if not any(_too_small(n) for _code, n in groups):
            return level, groups + [[NA, voters - len(ages)]]
    return 'none', []


def count_codes(values, order, voters):
    """[[code, n], ...] in the fixed order of `order`, then "small", then "na".
    Groups of 1 to 9 go together in "small"; is "small" then 1 to 9, it goes
    into "na". "na" (prefer not to say) and empty count as not filled in."""
    counts = {code: sum(1 for v in values if v == code) for code in order if code != NA}
    small = sum(n for n in counts.values() if _too_small(n))
    rows = [[code, n] for code, n in counts.items() if not _too_small(n)]
    if small and not _too_small(small):
        rows.append([SMALL, small])
    return rows + [[NA, voters - sum(n for _code, n in rows)]]


def count_countries(values, voters):
    """[[iso2, n], ...] for the countries with at least 10 voters (most first,
    then by code), then "other" with the rest (into "na" when 1 to 9), then "na"."""
    counts = {}
    for v in values:
        if v:
            counts[v] = counts.get(v, 0) + 1
    rows = sorted(([code, n] for code, n in counts.items() if n >= MIN_GROUP), key=lambda r: (-r[1], r[0]))
    other = sum(n for n in counts.values() if n < MIN_GROUP)
    if other and not _too_small(other):
        rows.append([OTHER, other])
    return rows + [[NA, voters - sum(n for _code, n in rows)]]


def compute(rows, year):
    """The frozen totals from the answers of the voters, one row per voter:
    (birth_year, gender, education, answered_at, country). Returns
    (voters, answered, age_level, counts). Nothing per person."""
    voters = len(rows)
    answered = sum(1 for r in rows if r[3] is not None)
    if voters < MIN_VOTERS:
        return voters, answered, 'none', {}
    age_level, ages = count_ages([r[0] for r in rows], year, voters)
    return voters, answered, age_level, {
        'age': ages,
        'gender': count_codes([r[1] for r in rows], GENDERS, voters),
        'education': count_codes([r[2] for r in rows], EDUCATION_LEVELS, voters),
        'country': count_countries([r[4] for r in rows], voters),
    }


def _voter_answers(issue_id):
    """One row per voter of the issue (not the summary proposal, not deleted
    accounts), with their answers, in one query."""
    from app.models import IssueProposal, IssueProposalVote, User
    return (db.session.query(IssueProposalVote.user_id, ParticipantProfile.birth_year, ParticipantProfile.gender,
                             ParticipantProfile.education, ParticipantProfile.answered_at, User.country)
            .join(IssueProposal, IssueProposal.id == IssueProposalVote.proposal_id)
            .join(User, User.id == IssueProposalVote.user_id)
            .outerjoin(ParticipantProfile, ParticipantProfile.user_id == IssueProposalVote.user_id)
            .filter(IssueProposal.issue_id == issue_id, IssueProposalVote.user_id.isnot(None),
                    db.or_(IssueProposal.is_final.is_(None), IssueProposal.is_final.is_(False)))
            .distinct().all())


def freeze_for_issue(issue, now=None):
    """Freeze who voted, once per issue, at the end of the voting phase
    (issue_lifecycle.set_phase). Only with the switch on; an existing row
    stays (also after reopening and voting again). Commit niet zelf."""
    import json
    if not enabled():
        return None
    if IssueParticipationStats.query.filter_by(issue_id=issue.id).first() is not None:
        return None
    now = now or datetime.utcnow()
    rows = [r[1:] for r in _voter_answers(issue.id)]
    voters, answered, age_level, counts = compute(rows, now.year)
    stats = IssueParticipationStats(issue_id=issue.id, frozen_at=now, voters=voters, answered=answered,
                                    age_level=age_level, counts=json.dumps(counts))
    db.session.add(stats)
    return stats


def stats_for(issue_id):
    """The frozen row of an issue, or None (one query; none with the switch off)."""
    if not enabled():
        return None
    return IssueParticipationStats.query.filter_by(issue_id=issue_id).first()


# ── The display (labels at showing time, in the reader's language) ────────

def trait_label(trait):
    if trait == 'age':
        # TRANSLATORS: a heading in the block "Who voted" (the age groups of the voters).
        return gettext('Age')
    if trait == 'gender':
        # TRANSLATORS: a heading in the block "Who voted".
        return gettext('Gender')
    if trait == 'education':
        # TRANSLATORS: a heading in the block "Who voted" (the level of education of the voters).
        return gettext('Education')
    # TRANSLATORS: a heading in the block "Who voted" (the country where the voters live).
    return gettext('Country')


def group_label(trait, code, lang):
    if code == NA:
        # TRANSLATORS: in the block "Who voted": nothing filled in, "prefer not to say", or merged in.
        return gettext('Not filled in')
    if code == SMALL:
        # TRANSLATORS: in the block "Who voted": groups smaller than 10 people, shown together.
        return gettext('Smaller groups combined')
    if code == OTHER:
        # TRANSLATORS: in the block "Who voted": the countries with fewer than 10 voters, together.
        return gettext('Other countries combined')
    if trait == 'age':
        return age_group_label(*parse_age_code(code))
    if trait == 'gender':
        return gender_label(code)
    if trait == 'education':
        return education_label(code)[0]
    return countries.display_name(code, lang)


def percent(n, total):
    """A whole percentage, rounded half up."""
    return (n * 200 + total) // (2 * total) if total else 0


def stats_view(stats, lang):
    """What the block "Who voted" needs, translated for the reader."""
    import json
    from app.charts import format_value
    from babel.numbers import format_decimal

    def pct_text(n):
        return format_value(percent(n, stats.voters), lang, '%', decimals=0)

    view = {'stats': stats, 'voters': stats.voters, 'answered': format_decimal(stats.answered, locale=lang),
            'answered_pct': pct_text(stats.answered), 'too_few': stats.voters < MIN_VOTERS, 'traits': []}
    if view['too_few']:
        return view
    counts = json.loads(stats.counts or '{}')
    for trait in TRAITS:
        rows = [{'label': group_label(trait, code, lang), 'pct': percent(n, stats.voters), 'pct_text': pct_text(n),
                 'n': format_decimal(n, locale=lang), 'na': code == NA}
                for code, n in counts.get(trait, [])]
        view['traits'].append({'key': trait, 'title': trait_label(trait), 'rows': rows,
                               'not_enough': trait == 'age' and stats.age_level == 'none'})
    return view
