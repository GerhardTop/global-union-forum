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
    assert 'Wie stemt er mee?' in section and 'Vrijwillig' in section
    for name in ('name="birth_year"', 'name="gender"', 'name="education"', 'name="country"'):
        assert name in section
    assert 'Hbo of universiteit' in section and 'Antwoorden wissen' in section
    assert 'Ingevuld op' not in section
    # Under Optional, after Account and before Settings (docs/design/profiel.html).
    assert page.index('id="guNameDisplay"') < page.index('>Optioneel</p>') < page.index('id="over-mij"') \
        < page.index('>Instellingen</p>') < page.index('guTranslateForm')


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
    assert 'Ingevuld op' not in section  # the line "Filled in on" is gone (docs/design/profiel.html)


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


@pytest.mark.parametrize('year', [TODAY.year - 16, 1920])
def test_year_limits_allowed(app, db, make_user, switch_on, year):
    user = make_user()
    assert _post(app, user, {'action': 'save', 'birth_year': str(year)}).status_code == 303
    db.session.expire_all()
    assert _profile(user.id).birth_year == year


@pytest.mark.parametrize('field,value', [
    ('birth_year', str(TODAY.year - 15)), ('birth_year', '1919'), ('birth_year', 'abc'),
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
    assert years[0] == 2010 and years[-1] == 1920 and 1921 in years
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
    assert 'Wer stimmt mit ab?' in section and 'Möchte ich nicht sagen' in section


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
        assert participants.AGE_LADDER[level][0][0] == 16
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


# ── Step 1c: the card after the first vote ───────────────────────────────

CARD = 'class="gu-ppcard"'
THANKS = 'Dank je. Je antwoorden staan in je profiel, bij &#34;Wie stemt er mee?&#34;.'


@pytest.fixture
def voting(db, make_user):
    """An issue in the voting phase with two proposals on the voting list."""
    from app import issue_proposals as ip, issues
    made = []

    def _make():
        author, backer = make_user(), make_user('voter')
        issue = issues.create({'title': f'Q{uuid.uuid4().hex[:6]}', 'intro': 'I.', 'kind': 'online',
                               'support_threshold': 1}, author, lang='nl')
        db.session.flush()
        props = [ip.create(issue, author, f'S{uuid.uuid4().hex[:6]}', 'E.', lang='nl') for _ in range(2)]
        db.session.flush()
        for p in props:
            ip.support(p, backer)
        issue.phase = 'stemmen'
        db.session.commit()
        made.append(issue.id)
        return issue, props

    yield _make
    for issue_id in made:
        _cleanup(issue_id)


def _url(issue, proposal):
    return f'/vraagstuk/{issue.id}/voorstel/{proposal.id}'


def _vote(client, issue, proposal, choice='voor'):
    return _req(client, 'POST', _url(issue, proposal) + '/stemmen', data={'choice': choice})


def test_card_switch_off(app, db, make_user, voting):
    issue, (p1, _p2) = voting()
    voter = make_user('voter')
    client = _client(app, voter)
    before = _req(client, 'GET', _url(issue, p1)).get_data(as_text=True)
    r = _vote(client, issue, p1)
    assert r.status_code == 302
    with client.session_transaction() as s:
        assert 'pp_ask' not in s
    page = _req(client, 'GET', _url(issue, p1)).get_data(as_text=True)
    assert CARD not in page and 'pp-birth-year' not in page
    assert _profile(voter.id) is None
    assert before.count('gu-ppcard') == page.count('gu-ppcard') == 0


def test_card_once_after_the_first_vote(app, db, make_user, voting, switch_on):
    issue, (p1, p2) = voting()
    voter = make_user('voter')
    voter.country = 'BE'
    db.session.commit()
    client = _client(app, voter)
    r = _vote(client, issue, p1)
    assert r.status_code == 302 and r.headers['Location'].endswith(_url(issue, p1) + '#stemming')
    page = _req(client, 'GET', _url(issue, p1)).get_data(as_text=True)
    assert page.count(CARD) == 1
    card = page[page.index(CARD):page.index('</form>', page.index(CARD))]
    for name in ('name="birth_year"', 'name="gender"', 'name="education"', 'name="country"'):
        assert name in card
    assert 'Je stem is opgeslagen. Mogen we nog iets vragen?' in card and 'Je stem telt hoe dan ook.' in card
    assert '<option value="BE" selected>' in card            # Mijn land, filled in
    assert f'name="next" value="{_url(issue, p1)}#stemming"' in card
    assert 'href="/profile#over-mij"' in card and 'Overslaan' in card
    # The vote buttons still work next to the card.
    assert 'name="choice" value="tegen"' in page
    db.session.expire_all()
    assert _profile(voter.id).asked_at is not None
    # Not on a second visit, not after a vote on another proposal, not after changing the vote.
    assert CARD not in _req(client, 'GET', _url(issue, p1)).get_data(as_text=True)
    _vote(client, issue, p2)
    assert CARD not in _req(client, 'GET', _url(issue, p2)).get_data(as_text=True)
    _vote(client, issue, p1, 'tegen')
    assert CARD not in _req(client, 'GET', _url(issue, p1)).get_data(as_text=True)


def test_no_card_when_changing_a_vote(app, db, make_user, voting, switch_on):
    """A vote from before the card (asked_at empty) that is changed: no card."""
    from app.models import IssueProposalVote
    issue, (p1, _p2) = voting()
    voter = make_user('voter')
    db.session.add(IssueProposalVote(proposal_id=p1.id, user_id=voter.id, choice='voor'))
    db.session.commit()
    client = _client(app, voter)
    _vote(client, issue, p1, 'tegen')
    assert CARD not in _req(client, 'GET', _url(issue, p1)).get_data(as_text=True)
    assert _profile(voter.id) is None


def test_save_from_the_card(app, db, make_user, voting, switch_on):
    issue, (p1, _p2) = voting()
    voter = make_user('voter')
    client = _client(app, voter)
    _vote(client, issue, p1)
    _req(client, 'GET', _url(issue, p1))
    r = _req(client, 'POST', '/profiel/over-mij', data={
        'action': 'save', 'next': _url(issue, p1) + '#stemming',
        'birth_year': '1970', 'gender': 'f', 'education': 'middle', 'country': 'FR'})
    assert r.status_code == 303 and r.headers['Location'].endswith(_url(issue, p1) + '#stemming')
    db.session.expire_all()
    p = _profile(voter.id)
    assert (p.birth_year, p.gender, p.education) == (1970, 'f', 'middle') and p.asked_at is not None
    assert db.session.get(User, voter.id).country == 'FR'
    with client.session_transaction() as s:
        assert not s.get('_flashes')
    page = _req(client, 'GET', _url(issue, p1)).get_data(as_text=True)
    assert page.count(THANKS) == 1 and 'role="status">' in page and CARD not in page
    assert 'gu-flash-modal' not in page
    assert THANKS not in _req(client, 'GET', _url(issue, p1)).get_data(as_text=True)


def test_skip_from_the_card(app, db, make_user, voting, switch_on):
    issue, (p1, p2) = voting()
    voter = make_user('voter')
    client = _client(app, voter)
    _vote(client, issue, p1)
    _req(client, 'GET', _url(issue, p1))
    r = _req(client, 'POST', '/profiel/over-mij', data={'action': 'skip', 'next': _url(issue, p1) + '#stemming',
                                                        'birth_year': '1970', 'country': 'FR'})
    assert r.status_code == 303 and r.headers['Location'].endswith(_url(issue, p1) + '#stemming')
    db.session.expire_all()
    p = _profile(voter.id)
    assert (p.birth_year, p.gender, p.education, p.answered_at) == (None, None, None, None)
    assert p.asked_at is not None and db.session.get(User, voter.id).country is None
    with client.session_transaction() as s:
        assert not s.get('_flashes')
    page = _req(client, 'GET', _url(issue, p1)).get_data(as_text=True)
    assert THANKS not in page and CARD not in page
    _vote(client, issue, p2)
    assert CARD not in _req(client, 'GET', _url(issue, p2)).get_data(as_text=True)


def test_external_next_is_ignored(app, db, make_user, switch_on):
    user = make_user()
    r = _post(app, user, {'action': 'save', 'next': 'https://example.com/x', 'gender': 'm'})
    assert r.status_code == 303 and r.headers['Location'].endswith('/profile#over-mij')
    r = _post(app, user, {'action': 'skip', 'next': '//example.com'})
    assert r.status_code == 303 and r.headers['Location'].endswith('/profile#over-mij')


def test_card_only_for_the_voter(app, db, make_user, voting, switch_on):
    issue, (p1, _p2) = voting()
    voter, other = make_user('voter'), make_user('voter')
    _vote(_client(app, voter), issue, p1)
    page = _req(_client(app, other), 'GET', _url(issue, p1)).get_data(as_text=True)
    assert CARD not in page
    assert _profile(other.id) is None


@pytest.mark.parametrize('lang,expected', [
    ('de', ('Grundschule, Hauptschul- oder Realschulabschluss',
            'Was ist die höchste Ausbildung, die du abgeschlossen hast oder gerade machst?')),
    ('es', ('Secundaria completa, o formación profesional',)),
    ('fr', ('>De base<',)),
])
def test_translation_corrections(app, db, make_user, switch_on, lang, expected):
    user = make_user()
    section = _section(_req(_client(app, user, lang), 'GET', '/profile').get_data(as_text=True))
    for text_ in expected:
        assert text_ in section, text_
    assert 'Primaire' not in section


# ── Step 1d: freezing and the block "Who voted" ───────────────────────────

def _rows(n, birth_year=None, gender=None, education=None, country=None, answered=True):
    """n voter rows (birth_year, gender, education, answered_at, country)."""
    return [(birth_year, gender, education, TODAY if answered else None, country)] * n


def test_compute_too_few():
    assert participants.compute(_rows(29, 1980), 2026) == (29, 29, 'none', {})


def test_compute_age_finest_level():
    # 10 in each fine group (ages 20, 30, 40, 50, 60, 70, 80) and 12 without a year.
    rows = []
    for age in (20, 30, 40, 50, 60, 70, 80):
        rows += _rows(10, 2026 - age)
    rows += _rows(12, None, answered=False)
    voters, answered, level, counts = participants.compute(rows, 2026)
    assert (voters, answered, level) == (82, 70, 'fine')
    assert counts['age'] == [['16_24', 10], ['25_34', 10], ['35_44', 10], ['45_54', 10], ['55_64', 10],
                             ['65_74', 10], ['75_plus', 10], ['na', 12]]
    for trait in participants.TRAITS:
        assert sum(n for _c, n in counts[trait]) == voters


def test_compute_age_falls_back_to_middle_coarse_none():
    # 25 to 44 has 5 + 6 (too small fine), together 11; the rest 10 or more per middle group.
    rows = _rows(12, 2006) + _rows(5, 1996) + _rows(6, 1986) + _rows(10, 1976) + _rows(10, 1966) + _rows(10, 1956)
    level, ages = participants.count_ages([r[0] for r in rows], 2026, len(rows))
    assert level == 'middle'
    assert ages == [['16_24', 12], ['25_44', 11], ['45_64', 20], ['65_plus', 10], ['na', 0]]
    # Coarse: 16 to 44 has 25, 45 or older only 5.
    rows = _rows(25, 2000) + _rows(5, 1950) + _rows(5, 0)
    level, ages = participants.count_ages([r[0] for r in rows], 2026, len(rows))
    assert level == 'none' and ages == []
    # Too small in fine and middle (16 to 24 has 5), fine in coarse.
    rows = _rows(5, 2006) + _rows(5, 1996) + _rows(6, 1986) + _rows(6, 1976) + _rows(6, 1956) + _rows(4, 0)
    level, ages = participants.count_ages([r[0] for r in rows], 2026, len(rows))
    assert level == 'coarse' and ages == [['16_44', 16], ['45_plus', 12], ['na', 4]]
    # 1920 counts as 1920 (106 years old in 2026, in the oldest group).
    level, ages = participants.count_ages([1920] * 10 + [1990] * 10, 2026, 20)
    assert level == 'fine' and ages[-2] == ['75_plus', 10] and ages[2] == ['35_44', 10]


def test_compute_small_groups_and_countries():
    rows = (_rows(20, None, 'f', 'higher', 'NL') + _rows(15, None, 'm', 'middle', 'BE')
            + _rows(4, None, 'x', 'basic', 'DE') + _rows(3, None, 'na', 'na', 'FR')
            + _rows(2, None, None, None, None, answered=False))
    voters, _a, _l, counts = participants.compute(rows, 2026)
    assert voters == 44
    # x (4) is too small alone, "small" is 4 too: into na (4 + 3 + 2).
    assert counts['gender'] == [['f', 20], ['m', 15], ['na', 9]]
    # basic (4) is too small: into na too.
    assert counts['education'] == [['middle', 15], ['higher', 20], ['na', 9]]
    assert counts['country'] == [['NL', 20], ['BE', 15], ['na', 9]]
    # "small" of 10 or more stays as its own group.
    values = ['f'] * 20 + ['m'] * 6 + ['x'] * 5 + [None] * 3
    assert participants.count_codes(values, participants.GENDERS, 34) == [['f', 20], ['small', 11], ['na', 3]]
    # Countries: most first, then by code; "other" with 10 or more stays.
    values = ['DE'] * 12 + ['AT'] * 12 + ['NL'] * 15 + ['BE'] * 5 + ['FR'] * 6 + [None] * 2
    assert participants.count_countries(values, 52) == [['NL', 15], ['AT', 12], ['DE', 12], ['other', 11], ['na', 2]]


def test_compute_zero_group_is_not_too_small():
    values = ['f'] * 15 + ['m'] * 15
    assert participants.count_codes(values, participants.GENDERS, 30) == [['f', 15], ['m', 15], ['x', 0], ['na', 0]]


def _voters(db, n, make_voter, issue, proposal, answers=None):
    from app.models import IssueProposalVote
    made = []
    for i in range(n):
        u = make_voter()
        db.session.add(IssueProposalVote(proposal_id=proposal.id, user_id=u.id, choice='voor'))
        a = answers(i) if answers else None
        if a is not None:
            db.session.add(ParticipantProfile(user_id=u.id, birth_year=a.get('birth_year'), gender=a.get('gender'),
                                              education=a.get('education'), answered_at=TODAY))
            u.country = a.get('country')
        made.append(u)
    db.session.commit()
    return made


def _end_voting(db, issue):
    from datetime import datetime
    from app import issue_lifecycle
    issue_lifecycle.step_forward(issue, datetime(2026, 12, 12, 10, 0))
    db.session.commit()


def _stats(issue_id):
    return IssueParticipationStats.query.filter_by(issue_id=issue_id).first()


def test_freeze_at_the_end_of_voting_once(app, db, make_user, voting, switch_on):
    import json
    from datetime import datetime
    from app import issue_lifecycle
    from app.models import IssueProposalVote
    issue, (p1, p2) = voting()
    _voters(db, 32, make_user, issue, p1, lambda i: {'birth_year': 1980 + (i % 2) * 30, 'gender': 'fm'[i % 2],
                                                     'education': 'higher', 'country': 'NL'})
    # A second vote of a voter counts once; a vote of a deleted account (no user) does not count.
    first = IssueProposalVote.query.filter_by(proposal_id=p1.id).first()
    db.session.add(IssueProposalVote(proposal_id=p2.id, user_id=first.user_id, choice='tegen'))
    db.session.commit()
    assert _stats(issue.id) is None
    _end_voting(db, issue)
    row = _stats(issue.id)
    assert row is not None and (row.voters, row.answered) == (32, 32)
    assert row.frozen_at == datetime(2026, 12, 12, 10, 0)
    counts = json.loads(row.counts)
    assert counts['gender'] == [['f', 16], ['m', 16], ['x', 0], ['na', 0]]
    assert counts['country'] == [['NL', 32], ['na', 0]]
    assert 'user' not in row.counts and str(first.user_id) not in json.dumps(counts['country'])
    # Reopen, vote again, end voting again: the first row stays.
    issue_lifecycle.reopen(issue)
    db.session.commit()
    issue.phase = 'stemmen'
    db.session.commit()
    _voters(db, 3, make_user, issue, p1)
    _end_voting(db, issue)
    assert IssueParticipationStats.query.filter_by(issue_id=issue.id).count() == 1
    assert _stats(issue.id).voters == 32


def test_no_freeze_with_the_switch_off_or_on_reopen(app, db, make_user, voting):
    issue, (p1, _p2) = voting()
    _voters(db, 31, make_user, issue, p1)
    _end_voting(db, issue)
    assert _stats(issue.id) is None


def test_summary_proposal_votes_do_not_count(app, db, make_user, voting, switch_on):
    from app.models import IssueProposalVote
    issue, (p1, p2) = voting()
    _voters(db, 30, make_user, issue, p1)
    p2.is_final = True
    extra = make_user()
    db.session.add(IssueProposalVote(proposal_id=p2.id, user_id=extra.id, choice='voor'))
    db.session.commit()
    _end_voting(db, issue)
    assert _stats(issue.id).voters == 30


def _issue_page(app, user, issue, lang='nl'):
    return _req(_client(app, user, lang), 'GET', f'/vraagstuk/{issue.id}').get_data(as_text=True)


def _block(page):
    start = page.index('id="wie-stemde-mee"')
    return page[start:page.index('</section>\n', page.index('gu-pstats__foot', start) if 'gu-pstats__foot' in page[start:] else start)]


def test_block_on_the_issue_page(app, db, make_user, voting, switch_on):
    import json
    issue, _props = voting()
    counts = {'age': [['16_24', 34], ['25_44', 88], ['45_64', 79], ['65_plus', 33], ['na', 78]],
              'gender': [['f', 82], ['m', 151], ['na', 79]],
              'education': [['basic', 12], ['middle', 61], ['higher', 162], ['na', 77]],
              'country': [['NL', 121], ['BE', 28], ['DE', 22], ['other', 61], ['na', 80]]}
    from datetime import datetime
    db.session.add(IssueParticipationStats(issue_id=issue.id, voters=312, answered=241, age_level='middle',
                                           counts=json.dumps(counts), frozen_at=datetime(2026, 12, 12, 10)))
    issue.phase = 'vervolg'
    db.session.commit()
    viewer = make_user()
    page = _issue_page(app, viewer, issue)
    block = page[page.index('id="wie-stemde-mee"'):]
    block = block[:block.index('gu-pstats__foot')]
    assert '312 deelnemers stemden op minstens één voorstel.' in block
    assert '241 van hen (77%) vulden minstens één vraag in.' in block
    for text_ in ('Leeftijd', 'Geslacht', 'Opleiding', 'Land', '25 tot en met 44', '65 of ouder', 'Vrouw',
                  'Hoger', 'Nederland', 'België', 'Andere landen samen', 'Niet ingevuld', 'Balken', 'Tabel'):
        assert text_ in block, text_
    assert '<span style="inline-size: 28%"></span></span><strong class="gu-pstats__pct">28%</strong>' in block
    assert block.count('<caption>') == 4 and 'aria-hidden="true"' in block
    # The block sits under the phase, before the three columns.
    assert page.index('id="wie-stemde-mee"') < page.index('gu-cockpit__grid')
    assert 'Bevroren op 12 december 2026, aan het eind van de stemfase.' in page
    # French: a space before the percent sign; German: the country in German.
    fr = _issue_page(app, viewer, issue, 'fr')
    assert '77 %' in fr or '77\xa0%' in fr
    assert 'Niederlande' in _issue_page(app, viewer, issue, 'de')


def test_block_too_few_and_age_none(app, db, make_user, voting, switch_on):
    import json
    issue, _props = voting()
    stats = IssueParticipationStats(issue_id=issue.id, voters=12, answered=5, age_level='none', counts='{}')
    db.session.add(stats)
    db.session.commit()
    page = _issue_page(app, make_user(), issue)
    assert 'Te weinig deelnemers om te laten zien wie er meestemde (minder dan 30).' in page
    assert 'Zo blijft niemand herkenbaar.' in page and 'gu-pstats__groups' not in page
    stats.voters, stats.counts = 40, json.dumps({'age': [], 'gender': [['f', 20], ['m', 20], ['x', 0], ['na', 0]],
                                                 'education': [['na', 40]], 'country': [['na', 40]]})
    db.session.commit()
    page = _issue_page(app, make_user(), issue)
    assert 'Niet genoeg ingevuld om te tonen.' in page


def test_block_not_with_the_switch_off(app, db, make_user, voting):
    issue, _props = voting()
    db.session.add(IssueParticipationStats(issue_id=issue.id, voters=40, answered=1, age_level='none', counts='{}'))
    db.session.commit()
    assert 'wie-stemde-mee' not in _issue_page(app, make_user(), issue)


def test_result_line_on_the_proposal(app, db, make_user, voting, switch_on):
    issue, (p1, p2) = voting()
    _voters(db, 30, make_user, issue, p1)
    _end_voting(db, issue)
    page = _req(_client(app, make_user()), 'GET', _url(issue, p1)).get_data(as_text=True)
    assert '30 deelnemers in dit vraagstuk' in page
    assert f'href="/vraagstuk/{issue.id}#wie-stemde-mee">Wie stemde mee</a>' in page
    p2.is_final = True
    db.session.commit()
    page = _req(_client(app, make_user()), 'GET', _url(issue, p2)).get_data(as_text=True)
    assert 'deelnemers in dit vraagstuk' not in page


def test_compute_zero_group_does_not_block_fine():
    # Only two fine groups have people (10 each); the other five have 0.
    level, ages = participants.count_ages([2000] * 10 + [1970] * 10, 2026, 25)
    assert level == 'fine'
    assert [n for _c, n in ages] == [0, 10, 0, 0, 10, 0, 0, 5]


def test_compute_sums_codes_only():
    import json
    rows = (_rows(14, 1990, 'f', 'higher', 'NL') + _rows(12, 1960, 'm', 'basic', 'TW')
            + _rows(6, 2001, 'x', 'middle', 'BE') + _rows(5, None, None, None, None, answered=False))
    voters, answered, level, counts = participants.compute(rows, 2026)
    assert (voters, answered) == (37, 32)
    for trait in participants.TRAITS:
        assert sum(n for _c, n in counts[trait]) == voters, trait
        assert counts[trait][-1][0] == 'na'
    text = json.dumps(counts)
    for label in ('Vrouw', 'Woman', 'Nederland', 'Taiwan', 'Niet', 'Not'):
        assert label not in text
    codes = {c for trait in counts.values() for c, _n in trait}
    allowed = ({participants.age_code(*g) for lvl in participants.AGE_LADDER.values() for g in lvl}
               | set(participants.GENDERS) | set(participants.EDUCATION_LEVELS) | {'small', 'other', 'NL', 'TW'})
    assert codes <= allowed
    # Age without a usable level: an empty list, no "na" (the only exception to the sum).
    voters, _a, level, counts = participants.compute(_rows(25, 2000) + _rows(5, 1950), 2026)
    assert level == 'none' and counts['age'] == []


def test_freeze_via_advance_counts_each_voter_once(app, db, make_user, voting, switch_on):
    from datetime import datetime, timedelta
    from app import issue_lifecycle
    from app.models import IssueProposal, IssueProposalVote
    issue, (p1, p2) = voting()
    p3 = IssueProposal(issue_id=issue.id, statement=f'S{uuid.uuid4().hex[:6]}', created_by_id=issue.created_by_id)
    db.session.add(p3)
    db.session.commit()
    voters = _voters(db, 30, make_user, issue, p1, lambda i: {'gender': 'f'} if i < 12 else None)
    for p in (p2, p3):                                   # the first voter votes on three proposals
        db.session.add(IssueProposalVote(proposal_id=p.id, user_id=voters[0].id, choice='tegen'))
    db.session.add(IssueProposalVote(proposal_id=p2.id, user_id=None, choice='voor'))   # a deleted account
    other_issue, (o1, _o2) = voting()                    # a vote in another issue does not count
    db.session.add(IssueProposalVote(proposal_id=o1.id, user_id=make_user().id, choice='voor'))
    db.session.commit()
    now = datetime.utcnow()
    issue.kind, issue.phase_deadline = 'online', now - timedelta(minutes=1)
    db.session.commit()
    assert issue_lifecycle.advance(issue, now)
    db.session.commit()
    assert issue.phase != 'stemmen'
    row = _stats(issue.id)
    assert (row.voters, row.answered) == (30, 12)
    assert IssueParticipationStats.query.filter_by(issue_id=issue.id).count() == 1
    assert _stats(other_issue.id) is None


def test_wrapper_gets_no_row(app, db, make_user, voting, switch_on, monkeypatch):
    from app import issue_lifecycle
    issue, (p1, _p2) = voting()
    _voters(db, 30, make_user, issue, p1)
    monkeypatch.setattr(issue_lifecycle, 'is_wrapper', lambda i: True)
    issue_lifecycle.set_phase(issue, 'vervolg', TODAY_DT())
    db.session.commit()
    assert _stats(issue.id) is None


def TODAY_DT():
    from datetime import datetime
    return datetime.utcnow()


def test_too_few_frozen_shows_only_the_card(app, db, make_user, voting, switch_on):
    issue, (p1, _p2) = voting()
    _voters(db, 29, make_user, issue, p1, lambda i: {'gender': 'f'})
    _end_voting(db, issue)
    row = _stats(issue.id)
    assert (row.voters, row.answered, row.age_level, row.counts) == (29, 29, 'none', '{}')
    page = _issue_page(app, make_user(), issue)
    assert 'Te weinig deelnemers om te laten zien wie er meestemde (minder dan 30).' in page
    assert 'gu-pstats__groups' not in page and 'gu-pstats__table' not in page


def test_answers_changed_after_freezing_do_not_change_the_block(app, db, make_user, voting, switch_on):
    issue, (p1, _p2) = voting()
    voters = _voters(db, 30, make_user, issue, p1, lambda i: {'gender': 'f', 'country': 'NL'})
    _end_voting(db, issue)
    before = _stats(issue.id).counts
    for u in voters[:15]:
        participants.clear(u)
    for u in voters[15:]:
        participants.save(u, {'gender': 'm', 'country': 'BE'})
    db.session.commit()
    assert _stats(issue.id).counts == before
    page = _issue_page(app, make_user(), issue)
    assert 'Nederland' in page and 'België' not in page


def test_block_rows_order_language_and_sensitive_area(app, db, make_user, voting, switch_on):
    import json
    import html as html_lib
    issue, _props = voting()
    counts = {'age': [['16_44', 15], ['45_plus', 11], ['na', 10]],
              'gender': [['f', 12], ['m', 14], ['na', 10]],
              'education': [['basic', 0], ['middle', 13], ['higher', 13], ['na', 10]],
              'country': [['TW', 12], ['NL', 11], ['na', 13]]}
    db.session.add(IssueParticipationStats(issue_id=issue.id, voters=36, answered=26, age_level='coarse',
                                           counts=json.dumps(counts)))
    db.session.commit()
    viewer = make_user()
    page = _issue_page(app, viewer, issue)
    bars = page[page.index('data-pp-view="bars"'):page.index('data-pp-view="table"')]
    table = page[page.index('data-pp-view="table"'):page.index('gu-pstats__foot')]
    # Without JavaScript bars and table are both in the page; "Not filled in" last in each trait.
    for part, item in ((bars, '<li'), (table, '<tr')):
        for trait in part.split('gu-pstats__trait"' if item == '<li' else '<caption>')[1:]:
            last = trait[trait.rindex(item):]
            assert 'Niet ingevuld' in last
    de = _issue_page(app, viewer, issue, 'de')
    assert 'Taiwan' in de and 'Niederlande' in de
    fr = html_lib.unescape(_issue_page(app, viewer, issue, 'fr'))
    assert re.search(r'>33[ \xa0]%<', fr)        # 12 of 36, with a non-breaking space
    nl = _issue_page(app, viewer, issue, 'nl')
    assert '>33%<' in nl


def test_result_line_not_with_the_switch_off(app, db, make_user, voting):
    issue, (p1, _p2) = voting()
    db.session.add(IssueParticipationStats(issue_id=issue.id, voters=40, answered=1, age_level='none', counts='{}'))
    issue.phase = 'vervolg'
    db.session.commit()
    page = _req(_client(app, make_user()), 'GET', _url(issue, p1)).get_data(as_text=True)
    assert 'deelnemers in dit vraagstuk' not in page and 'wie-stemde-mee' not in page


# ── Step 1e: privacy statement, FAQ and the links ─────────────────────────

def _get_page(app, path, lang='nl', user=None):
    return _req(_client(app, user, lang), 'GET', path).get_data(as_text=True)


def test_privacy_text_only_with_the_switch(app, db, monkeypatch):
    off = _get_page(app, '/privacy')
    assert 'id="wie-stemde-mee"' not in off and 'Waarom we vragen wie er meestemt' not in off
    assert 'je geboortejaar, je geslacht' not in off
    assert 'Laatst bijgewerkt: 24 november 2026' in off
    monkeypatch.setitem(app.config, 'PARTICIPANT_PROFILE', True)
    on = _get_page(app, '/privacy')
    assert 'id="wie-stemde-mee"' in on and 'Waarom we vragen wie er meestemt' in on
    assert 'Als je dat zelf invult: je geboortejaar, je geslacht' in on
    assert 'daarin ben je niet terug te vinden.' in on
    assert 'Laatst bijgewerkt: 27 november 2026' in on
    # The new section sits after "What we use it for" and before "Who we share data with".
    assert on.index('id="gebruik"') < on.index('id="wie-stemde-mee"') < on.index('id="delen"')


def test_faq_question_only_with_the_switch(app, db, monkeypatch):
    question = 'Waarom vragen jullie na mijn eerste stem naar mijn leeftijd en opleiding?'
    assert question not in _get_page(app, '/faq')
    monkeypatch.setitem(app.config, 'PARTICIPANT_PROFILE', True)
    page = _get_page(app, '/faq')
    assert question in page
    assert '<a href="/privacy#wie-stemde-mee">privacypagina</a>' in page
    # Right after "Which data do you keep, and what for?".
    assert page.index('Welke gegevens') < page.index(question) < page.index('Wie kan zien wat ik schrijf?')


def test_why_we_ask_links_to_the_privacy_section(app, db, make_user, voting, switch_on):
    import json
    target = 'href="/privacy#wie-stemde-mee">Waarom we dit vragen</a>'
    user = make_user()
    assert target in _section(_get_page(app, '/profile', user=user))                    # About me (1b)
    issue, (p1, _p2) = voting()
    voter = make_user('voter')
    client = _client(app, voter)
    _vote(client, issue, p1)
    page = _req(client, 'GET', _url(issue, p1)).get_data(as_text=True)
    card = page[page.index(CARD):page.index('</form>', page.index(CARD))]
    assert target in card                                                                 # the card (1c)
    stats = IssueParticipationStats(issue_id=issue.id, voters=12, answered=1, age_level='none', counts='{}')
    db.session.add(stats)
    db.session.commit()
    assert target in _issue_page(app, user, issue)                                        # the block (1d), too few
    stats.voters, stats.counts = 40, json.dumps({'age': [], 'gender': [['na', 40]], 'education': [['na', 40]],
                                                 'country': [['na', 40]]})
    db.session.commit()
    assert target in _issue_page(app, user, issue)                                        # the block (1d)
