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
# The youngest voters are 17 (the year in which you turn 17).
YOUNGEST_AGE = 17

# The fixed age ladder (K4): every level consists exactly of groups of the
# finer one. (low, high) in years, both included; high None is "or older".
AGE_LADDER = {
    'fine': ((17, 24), (25, 34), (35, 44), (45, 54), (55, 64), (65, 74), (75, None)),
    'middle': ((17, 24), (25, 44), (45, 64), (65, None)),
    'coarse': ((17, 44), (45, None)),
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
    you turn 17 back to 1921, then 1920 ("1920 or earlier")."""
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
