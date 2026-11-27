"""Representativeness 1b (docs/representativiteit.md): the data model, About
me in the profile, clearing, and the cleanup when an account or an issue goes.
Everything behind PARTICIPANT_PROFILE (off by default).

Shared conftest app (CSRF off); every test makes its own users and issues.
"""
import re
import uuid
from datetime import date

import pytest
from sqlalchemy import inspect as sa_inspect, text

from app import participants
from app.models import IssueParticipationStats, ParticipantProfile, User
from app.sources import countries
from tests.test_delete_issue import _clean_session, _cleanup  # noqa: F401  (autouse fixture)
from tests.test_issue_polish import _client, _issue, _req, make_user  # noqa: F401  (fixture)

TODAY = date.today()


@pytest.fixture
def switch_on(app, monkeypatch):
    monkeypatch.setitem(app.config, 'PARTICIPANT_PROFILE', True)


def _profile(user_id):
    return ParticipantProfile.query.filter_by(user_id=user_id).first()


def _post(app, user, data, lang='nl'):
    return _req(_client(app, user, lang), 'POST', '/profiel/over-mij', data=data)


def _section(page):
    start = page.index('id="over-mij"')
    return page[start:page.index('</section>', start)]


# ── The switch ────────────────────────────────────────────────────────────

def test_switch_off_no_section_and_404(app, db, make_user):
    user = make_user()
    page = _req(_client(app, user), 'GET', '/profile').get_data(as_text=True)
    assert 'id="over-mij"' not in page and 'pp-birth-year' not in page
    r = _post(app, user, {'action': 'save', 'birth_year': '1987'})
    assert r.status_code == 404
    assert _profile(user.id) is None


def test_switch_on_section_with_four_questions(app, db, make_user, switch_on):
    user = make_user()
    page = _req(_client(app, user), 'GET', '/profile').get_data(as_text=True)
    section = _section(page)
    assert 'Over mij' in section and 'Wie stemt er mee?' in section and 'Vrijwillig' in section
    for name in ('name="birth_year"', 'name="gender"', 'name="education"', 'name="country"'):
        assert name in section
    assert 'Hbo of universiteit' in section and 'Antwoorden wissen' in section
    assert 'Ingevuld op' not in section
    # The section sits after Language & translations, before the summary by email.
    assert page.index('guTranslateForm') < page.index('id="over-mij"') < page.index('id="samenvatting"')


# ── Saving ────────────────────────────────────────────────────────────────

def test_save_valid_values(app, db, make_user, switch_on):
    user = make_user()
    r = _post(app, user, {'action': 'save', 'birth_year': '1987', 'gender': 'f',
                          'education': 'higher', 'country': 'NL'})
    assert r.status_code == 303 and r.headers['Location'].endswith('/profile#over-mij')
    db.session.expire_all()
    p = _profile(user.id)
    assert (p.birth_year, p.gender, p.education) == (1987, 'f', 'higher')
    assert p.answered_at is not None and p.asked_at is None
    assert db.session.get(User, user.id).country == 'NL'
    page = _req(_client(app, user), 'GET', '/profile').get_data(as_text=True)
    section = _section(page)
    assert '<option value="1987" selected>1987</option>' in section
    assert 'value="higher" checked' in section and '<option value="NL" selected>' in section
    assert 'Ingevuld op' in section


def test_prefer_not_to_say(app, db, make_user, switch_on):
    user = make_user()
    _post(app, user, {'action': 'save', 'birth_year': 'na', 'gender': 'na', 'education': 'na', 'country': 'na'})
    db.session.expire_all()
    p = _profile(user.id)
    assert (p.birth_year, p.gender, p.education) == (0, 'na', 'na')
    assert db.session.get(User, user.id).country is None
    assert p.answered_at is not None   # "prefer not to say" is an answer


def test_empty_stays_none(app, db, make_user, switch_on):
    user = make_user()
    _post(app, user, {'action': 'save', 'gender': 'm'})
    db.session.expire_all()
    p = _profile(user.id)
    assert (p.birth_year, p.gender, p.education) == (None, 'm', None)
    assert p.answered_at is not None
    _post(app, user, {'action': 'save', 'birth_year': '', 'gender': '', 'education': '', 'country': ''})
    db.session.expire_all()
    p = _profile(user.id)
    assert (p.birth_year, p.gender, p.education, p.answered_at) == (None, None, None, None)


@pytest.mark.parametrize('year', [TODAY.year - 17, 1920])
def test_year_limits_allowed(app, db, make_user, switch_on, year):
    user = make_user()
    assert _post(app, user, {'action': 'save', 'birth_year': str(year)}).status_code == 303
    db.session.expire_all()
    assert _profile(user.id).birth_year == year


@pytest.mark.parametrize('field,value', [
    ('birth_year', str(TODAY.year - 16)), ('birth_year', '1919'), ('birth_year', 'abc'),
    ('gender', 'q'), ('education', 'phd'), ('country', 'ZZ'), ('country', 'nl'),
])
def test_value_outside_the_choices_is_400(app, db, make_user, switch_on, field, value):
    user = make_user()
    data = {'action': 'save', 'birth_year': '1987', 'gender': 'f', 'education': 'basic', 'country': 'NL'}
    data[field] = value
    assert _post(app, user, data).status_code == 400
    db.session.expire_all()
    assert _profile(user.id) is None
    assert db.session.get(User, user.id).country is None


def test_unknown_action_is_400(app, db, make_user, switch_on):
    user = make_user()
    assert _post(app, user, {'action': 'x'}).status_code == 400


def test_login_required(app, db, switch_on):
    r = _req(_client(app), 'POST', '/profiel/over-mij', data={'action': 'save'})
    assert r.status_code in (302, 401)


def test_birth_year_choices():
    years = participants.birth_year_choices(date(2026, 11, 27))
    assert years[0] == 2009 and years[-1] == 1920 and 1921 in years
    assert years == sorted(years, reverse=True) and len(years) == len(set(years))


# ── Clearing ──────────────────────────────────────────────────────────────

def test_clear_keeps_asked_at(app, db, make_user, switch_on):
    user = make_user()
    _post(app, user, {'action': 'save', 'birth_year': '1990', 'gender': 'x', 'education': 'middle', 'country': 'BE'})
    db.session.expire_all()
    p = _profile(user.id)
    p.asked_at = p.answered_at
    db.session.commit()
    r = _post(app, user, {'action': 'clear'})
    assert r.status_code == 303 and r.headers['Location'].endswith('/profile#over-mij')
    db.session.expire_all()
    p = _profile(user.id)
    assert (p.birth_year, p.gender, p.education, p.answered_at) == (None, None, None, None)
    assert p.asked_at is not None
    assert db.session.get(User, user.id).country is None


# ── Display ───────────────────────────────────────────────────────────────

def test_birth_year_is_a_bare_number_in_dutch(app, db, make_user, switch_on):
    user = make_user()
    page = _req(_client(app, user, 'nl'), 'GET', '/profile').get_data(as_text=True)
    section = _section(page)
    assert '>1987</option>' in section and '1.987' not in section
    assert '1920 of eerder' in section and '1.920' not in section


def test_sensitive_area_with_own_name_in_german(app, db, make_user, switch_on):
    user = make_user()
    section = _section(_req(_client(app, user, 'de'), 'GET', '/profile').get_data(as_text=True))
    assert '<option value="PS">Palästina</option>' in section
    assert 'Autonomiegebiete' not in section
    assert 'Über mich' in section and 'Möchte ich nicht sagen' in section


@pytest.mark.parametrize('lang', ['nl', 'de', 'fr'])
def test_countries_sorted_by_the_name_in_the_language(app, db, make_user, switch_on, lang):
    user = make_user()
    section = _section(_req(_client(app, user, lang), 'GET', '/profile').get_data(as_text=True))
    select = section[section.index('id="pp-country"'):]
    select = select[:select.index('</select>')]
    shown = re.findall(r'<option value="([A-Z]{2})"[^>]*>([^<]+)</option>', select)
    assert len(shown) == len(countries.all_codes())
    assert [code for code, _ in shown] == [code for code, _ in participants.country_choices(lang)]
    names = [countries.display_name(code, lang) for code, _ in shown]
    import html
    assert [html.unescape(n) for _, n in shown] == names
    # In German, Deutschland comes before Niederlande; in Dutch, Duitsland before Nederland.
    pos = {code: i for i, (code, _) in enumerate(shown)}
    assert pos['DE'] < pos['NL']
    if lang == 'fr':
        # Allemagne before Autriche; an accent sorts with its letter (Égypte before Espagne).
        assert pos['DE'] < pos['AT'] and pos['EG'] < pos['ES']


def test_age_group_labels(app):
    from flask_babel import force_locale
    with app.test_request_context():
        with force_locale('nl'):
            assert participants.age_group_label(25, 44) == '25 tot en met 44'
            assert participants.age_group_label(65, None) == '65 of ouder'
        with force_locale('de'):
            assert participants.age_group_label(25, 44) == '25 bis 44'


def test_age_ladder_levels_nest():
    """Every group of a coarser level is exactly a union of finer groups."""
    for finer, coarser in (('fine', 'middle'), ('middle', 'coarse')):
        bounds = {low for low, _ in participants.AGE_LADDER[finer]}
        for low, high in participants.AGE_LADDER[coarser]:
            assert low in bounds
            if high is not None:
                assert high + 1 in bounds
    for level in participants.AGE_LEVELS:
        assert participants.AGE_LADDER[level][0][0] == 17
        assert participants.AGE_LADDER[level][-1][1] is None


# ── Deleting ──────────────────────────────────────────────────────────────

def _lone_user(db):
    """A user that the test deletes itself (make_user would delete it again)."""
    from app import bcrypt
    name = f'pp_{uuid.uuid4().hex[:8]}'
    user = User(username=name, email=f'{name}@example.com', verified=True,
                password_hash=bcrypt.generate_password_hash('Sterk1!ww').decode('utf-8'))
    user.set_role('contributor')
    user.accept_rules()
    db.session.add(user)
    db.session.commit()
    return user


def _answered(db, user):
    p = ParticipantProfile(user_id=user.id, birth_year=1955, gender='m', education='basic')
    user.country = 'DE'
    db.session.add(p)
    db.session.commit()


def test_self_delete_removes_the_row(app, db):
    user = _lone_user(db)
    _answered(db, user)
    uid = user.id
    r = _req(_client(app, user), 'POST', '/account/verwijderen')
    assert r.status_code in (200, 302)
    db.session.expire_all()
    assert db.session.get(User, uid) is None
    assert _profile(uid) is None


def test_admin_hard_delete_removes_the_row(app, db, make_user):
    admin, user = make_user(admin=True), _lone_user(db)
    _answered(db, user)
    uid = user.id
    r = _req(_client(app, admin), 'POST', f'/admin/gebruiker/{uid}/verwijder')
    assert r.status_code == 302
    db.session.expire_all()
    assert db.session.get(User, uid) is None
    assert _profile(uid) is None


def test_anonymise_removes_the_row_and_the_country(app, db, make_user):
    from app import users
    admin, user = make_user(admin=True), make_user()
    _answered(db, user)
    users.anonymise(user, admin, 'test')
    db.session.commit()
    db.session.expire_all()
    assert _profile(user.id) is None
    assert db.session.get(User, user.id).country is None


def test_purge_removes_the_frozen_totals(app, db, make_user):
    from app import issues
    host = make_user()
    issue = _issue(db, host, f'Q{uuid.uuid4().hex[:6]}')
    issue_id = issue.id
    try:
        db.session.add(IssueParticipationStats(issue_id=issue_id, voters=40, answered=30, age_level='coarse',
                                               counts='{"gender": {"f": 12}}'))
        db.session.commit()
        issues.purge(issue)
        assert IssueParticipationStats.query.filter_by(issue_id=issue_id).count() == 0
    finally:
        _cleanup(issue_id)


# ── Nobody else sees the answers (K10) ────────────────────────────────────

def test_answers_not_on_moderator_and_admin_pages(app, db, make_user, switch_on):
    admin, user = make_user(admin=True), make_user()
    year = '1931'
    _post(app, user, {'action': 'save', 'birth_year': year, 'gender': 'x', 'education': 'basic', 'country': 'IS'})
    db.session.expire_all()
    assert _profile(user.id).birth_year == 1931
    client = _client(app, admin)
    for path in (f'/moderator/gebruiker/{user.id}', '/admin', '/moderator/rechten'):
        r = _req(client, 'GET', path)
        assert r.status_code == 200, path
        page = r.get_data(as_text=True)
        assert year not in page, path
        assert 'IJsland' not in page, path
    # The admin's own profile shows the admin's own (empty) answers, not the user's.
    section = _section(_req(client, 'GET', '/profile').get_data(as_text=True))
    assert f'<option value="{year}" selected>' not in section
    assert '<option value="IS" selected>' not in section


# ── Migration ─────────────────────────────────────────────────────────────

def test_migration_twice_adds_users_country(app, db):
    from app import _migrate_columns
    db.session.close()
    with db.engine.connect() as conn:
        conn.execute(text('ALTER TABLE users DROP COLUMN country'))
        conn.commit()
    db.engine.dispose()
    for _ in range(2):
        _migrate_columns()
        assert 'country' in {c['name'] for c in sa_inspect(db.engine).get_columns('users')}
    tables = set(sa_inspect(db.engine).get_table_names())
    assert {'participant_profiles', 'issue_participation_stats'} <= tables
