"""New issue form: the schedule times are filled in by default (00:00 for
every start, 23:59 for the close of voting); an empty time field gets the
same default and 24:00 (the end of the day) still works. The phase bar and
the schedule on the issue page show the right last day with a close at 23:59.
"""
from datetime import date, datetime, timedelta

from werkzeug.datastructures import MultiDict

from app import issues
from app.models import Issue
from tests.test_issue_polish import _client, _get, _req, make_user  # noqa: F401  (fixture)

TODAY = date(2027, 3, 1)


def _form(**over):
    data = {'title': 'Een planning', 'intro': 'Uitleg.', 'kind': 'online'}
    for name, (day, time) in issues.default_schedule(TODAY).items():
        data[f'{name}_date'], data[f'{name}_time'] = day, time
    data.update(over)
    return MultiDict(data)


def _moments(values):
    return [issues.to_local(values[name]) for name, _t in issues.SCHEDULE_FIELDS]


def test_the_default_schedule_has_the_times_filled_in():
    assert issues.SCHEDULE_FIELDS == (('starts_at', '00:00'), ('adjust_starts_at', '00:00'),
                                      ('vote_starts_at', '00:00'), ('vote_ends_at', '23:59'))
    assert issues.default_schedule(TODAY) == {'starts_at': ('2027-03-01', '00:00'),
                                              'adjust_starts_at': ('2027-03-22', '00:00'),
                                              'vote_starts_at': ('2027-03-27', '00:00'),
                                              'vote_ends_at': ('2027-04-02', '23:59')}


def test_the_default_schedule_keeps_the_durations():
    values, errors = issues.parse_form(_form())
    assert errors == {}
    start, adjust, vote, end = _moments(values)
    # The same moments as before (bijstellen and stemmen at 00:00 of the next day instead of 24:00 the day
    # before), and voting closes one minute before the old 24:00.
    assert (start, adjust, vote) == (datetime(2027, 3, 1), datetime(2027, 3, 22), datetime(2027, 3, 27))
    assert end == datetime(2027, 4, 2, 23, 59)
    assert (values['days_facts'], values['days_adjust'], values['days_vote']) == (21, 5, 7)


def test_empty_and_filled_in_give_the_same_moment():
    filled, _e = issues.parse_form(_form())
    empty, errors = issues.parse_form(_form(starts_at_time='', adjust_starts_at_time='', vote_starts_at_time='',
                                            vote_ends_at_time=''))
    assert errors == {}
    assert _moments(empty) == _moments(filled)


def test_24_00_still_works():
    values, errors = issues.parse_form(_form(adjust_starts_at_date='2027-03-21', adjust_starts_at_time='24:00',
                                             vote_ends_at_date='2027-04-02', vote_ends_at_time='24:00'))
    assert errors == {}
    _start, adjust, _vote, end = _moments(values)
    assert adjust == datetime(2027, 3, 22) and end == datetime(2027, 4, 3)


def test_the_order_and_past_checks_still_work():
    _v, errors = issues.parse_form(_form(vote_starts_at_date='2027-03-22', vote_starts_at_time='00:00'))
    assert errors.get('vote_starts_at') == 'order'
    _v, errors = issues.parse_form(_form(vote_ends_at_date='2027-03-27', vote_ends_at_time=''))
    assert errors == {}                                       # 27 March 23:59 is after 27 March 00:00
    _v, errors = issues.parse_form(_form(vote_ends_at_date='2027-03-26', vote_ends_at_time=''))
    assert errors.get('vote_ends_at') == 'order'
    past = date.today() - timedelta(days=40)
    _v, errors = issues.parse_form(MultiDict({**_form().to_dict(), **{
        f'{name}_date': (past + timedelta(days=i)).isoformat() for i, (name, _t) in enumerate(issues.SCHEDULE_FIELDS)}}))
    assert errors.get('vote_ends_at') == 'past'


def test_the_form_shows_the_times_and_the_short_hint(app, db, make_user):
    page = _get(app, '/gesprekken/nieuw', make_user())
    assert 'De tijden zijn in Nederlandse tijd.</p>' in page and '24:00' not in page
    assert page.count('value="00:00"') == 3 and page.count('value="23:59"') == 1


def test_the_phase_bar_and_the_schedule_show_the_last_day(app, db, make_user):
    host = make_user()
    start = issues.to_local(datetime.utcnow()).date()
    form = _form(title=f'Planning tot 23:59 {host.id}')
    for name, (day, time) in issues.default_schedule(start).items():
        form[f'{name}_date'], form[f'{name}_time'] = day, time
    r = _req(_client(app, host), 'POST', '/gesprekken/nieuw', data=form)
    assert r.status_code == 302
    issue = Issue.query.filter_by(created_by_id=host.id).one()
    end = start + timedelta(days=21 + 5 + 7 - 1)
    page = _get(app, f'/vraagstuk/{issue.id}', host)
    # The schedule of the next phases: revising ends the day before voting starts (00:00); voting ends on
    # its last day (23:59), not the day after.
    from babel.dates import format_date
    adjust_end = format_date(start + timedelta(days=25), 'd MMM y', locale='nl')
    assert f'tot {adjust_end}' in page
    assert f'tot {format_date(end, "d MMM y", locale="nl")}' in page
    assert format_date(end + timedelta(days=1), 'd MMM y', locale='nl') not in page
    # The phase bar on the last day of voting: the date, without 23:59.
    issue.phase, issue.phase_deadline = 'stemmen', issues.to_utc(datetime.combine(end, datetime.min.time())
                                                                 + timedelta(hours=23, minutes=59))
    db.session.commit()
    with app.test_request_context(headers={'Accept-Language': 'nl'}):
        from flask import render_template
        html = render_template('issues/_steps.html', issue=issue, remaining=(5, 'hours'))
    assert f'tot {format_date(end, "d MMM y", locale="nl")}' in html and '23:59' not in html
