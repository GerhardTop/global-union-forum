"""Background figures, step 1i: standalone reasons, the memory of charts no
issue can use (owid_rejected_slugs), the full period on the chart page when
the AI chose more than ten years, and the definition and unit translated.

Never network, never the real AI (the fakes of tests/test_background_figures.py).
"""
from datetime import datetime, timedelta

import pytest

from app import background_figures as bf, content_i18n, content_jobs
from app.models import ChartSelectionRun, OwidRejectedSlug, Post
from tests.test_background_figures import (FakeClient, FakeHttp, _answer, _chart, _charts, _issue, _run_for,  # noqa: F401
                                           made, started, watched)
from tests.test_chart_page import _get, _pressed
from tests.test_delete_issue import _clean_session  # noqa: F401  (autouse fixture)
from tests.test_issue_polish import _client, _req, make_user  # noqa: F401  (fixture)


@pytest.fixture
def no_rejections(db):
    """The rejected-slug memory is shared by all issues: start and end empty."""
    OwidRejectedSlug.query.delete()
    db.session.commit()
    yield
    OwidRejectedSlug.query.delete()
    db.session.commit()


def _candidates(client):
    prompt = client.calls[1]['messages'][0]['content'] if len(client.calls) > 1 else ''
    return prompt


def test_reasons_stand_on_their_own():
    assert 'Each reason must stand on its own. Do not refer to other charts, since some may be dropped by the checks.' \
        in bf._SELECTION_SYSTEM


# ── Rejected slugs ───────────────────────────────────────────────────────

def test_a_lasting_rejection_is_remembered_and_left_out(app, db, make_user, made, started, watched, no_rejections):
    issue = _issue(db, make_user(), made)
    answer = _answer(_chart(slug='two-columns-chart'), _chart(slug='military-spending-sipri'), _chart())
    assert bf.run(_run_for(issue).id, client=FakeClient(answer), http=FakeHttp()) == 'done'
    rows = {r.slug: r.reason for r in OwidRejectedSlug.query}
    assert rows == {'two-columns-chart': 'unsupported', 'military-spending-sipri': 'forbidden'}
    # Another issue: those two are no candidates any more.
    other = _issue(db, make_user(), made, title='Een ander vraagstuk')
    client = FakeClient(_answer())
    bf.run(_run_for(other).id, client=client, http=FakeHttp())
    prompt = _candidates(client)
    assert 'two-columns-chart' not in prompt and 'military-spending-sipri' not in prompt and 'regions-chart' in prompt


def test_after_thirty_days_a_rejected_slug_comes_back(app, db, make_user, made, started, watched, no_rejections):
    bf.remember_rejected('two-columns-chart', 'unsupported', now=datetime.utcnow() - timedelta(days=31))
    bf.remember_rejected('military-spending-sipri', 'stale', now=datetime.utcnow() - timedelta(days=29))
    db.session.commit()
    assert bf.recently_rejected() == {'military-spending-sipri'}
    issue = _issue(db, make_user(), made)
    client = FakeClient(_answer())
    bf.run(_run_for(issue).id, client=client, http=FakeHttp())
    prompt = _candidates(client)
    assert 'two-columns-chart' in prompt and 'military-spending-sipri' not in prompt


def test_stale_is_remembered_too(app, db, make_user, made, started, watched, no_rejections, monkeypatch):
    monkeypatch.setattr(bf, 'MAX_DATA_AGE_YEARS', 0)
    issue = _issue(db, make_user(), made)
    bf.run(_run_for(issue).id, client=FakeClient(_answer(_chart())), http=FakeHttp())
    assert OwidRejectedSlug.query.filter_by(slug='military-spending-as-a-share-of-gdp-sipri').one().reason == 'stale'


def test_a_network_error_writes_nothing(app, db, make_user, made, started, watched, no_rejections):
    issue = _issue(db, make_user(), made)
    http = FakeHttp(network_on={'military-spending-as-a-share-of-gdp-sipri'})
    assert bf.run(_run_for(issue).id, client=FakeClient(_answer(_chart())), http=http) == 'failed'
    assert OwidRejectedSlug.query.count() == 0
    # A dry run saves nothing either.
    issue2 = _issue(db, make_user(), made, title='Nog een vraagstuk')
    bf.dry_run(issue2, client=FakeClient(_answer(_chart(slug='two-columns-chart'))), http=FakeHttp())
    assert OwidRejectedSlug.query.count() == 0


def test_only_lasting_reasons_are_remembered(app, db, no_rejections):
    bf.remember_rejected('x-chart', 'network')
    bf.remember_rejected('y-chart', 'not_found')
    bf.remember_rejected('', 'unsupported')
    db.session.commit()
    assert OwidRejectedSlug.query.count() == 0
    bf.remember_rejected('z-chart', 'unsupported', now=datetime(2026, 1, 1))
    bf.remember_rejected('z-chart', 'no_data')
    db.session.commit()
    row = OwidRejectedSlug.query.filter_by(slug='z-chart').one()
    assert row.reason == 'no_data' and row.checked_at > datetime(2026, 1, 1)


# ── The period on the chart page ─────────────────────────────────────────

def test_a_longer_period_opens_on_all_years(app, db, make_user, made, started, watched, no_rejections):
    issue = _issue(db, make_user(), made)
    bf.run(_run_for(issue).id, client=FakeClient(_answer(_chart(years=16))), http=FakeHttp())
    (chart,) = _charts(issue)
    page = _get(app, issue, chart).get_data(as_text=True)
    assert _pressed(page, 'periode:alles') and '2010 tot 2025' in page
    page = _get(app, issue, chart, query='?periode=10').get_data(as_text=True)
    assert _pressed(page, 'periode:10') and '2016 tot 2025' in page
    page = _get(app, issue, chart, query='?periode=5').get_data(as_text=True)
    assert _pressed(page, 'periode:5')
    # The plain URL is all years; 10 is now a parameter.
    resp = _get(app, issue, chart, query='?zet=periode:10')
    assert resp.headers['Location'].endswith('?periode=10')
    resp = _get(app, issue, chart, query='?periode=10&zet=periode:alles')
    assert resp.headers['Location'].endswith(f'/cijfer/{chart.post_id}')


def test_ten_years_or_less_opens_on_ten(app, db, make_user, made, started, watched, no_rejections):
    issue = _issue(db, make_user(), made)
    bf.run(_run_for(issue).id, client=FakeClient(_answer(_chart(years=10))), http=FakeHttp())
    (chart,) = _charts(issue)
    page = _get(app, issue, chart).get_data(as_text=True)
    assert _pressed(page, 'periode:10') and 'periode:alles' not in page


# ── Definition and unit translated ───────────────────────────────────────

def test_definition_and_unit_go_through_the_translation_route(app, db, make_user, made, started, watched,
                                                              no_rejections):
    issue = _issue(db, make_user(), made)
    bf.run(_run_for(issue).id, client=FakeClient(_answer(_chart())), http=FakeHttp())
    (chart,) = _charts(issue)
    post = db.session.get(Post, chart.post_id)
    snapshot = chart.current_snapshot
    missing = {(content_i18n.object_type(t), f) for t, f, _lang in content_jobs.missing(post)}
    assert {('chart_snapshots', 'definition'), ('chart_snapshots', 'unit')} <= missing
    assert content_i18n.source_lang(snapshot) == 'en'
    assert content_i18n.field_kind(snapshot, 'unit') == 'short'
    assert content_i18n.field_kind(snapshot, 'definition') == 'text'
    page = _get(app, issue, chart).get_data(as_text=True)
    assert 'Total military expenditure divided by gross domestic product' in page and '% of GDP' in page
    content_i18n.set_translation(snapshot, 'definition', 'nl', 'De militaire uitgaven gedeeld door het bbp.',
                                 is_machine=True)
    content_i18n.set_translation(snapshot, 'unit', 'nl', '% van het bbp', is_machine=True)
    db.session.commit()
    page = _get(app, issue, chart).get_data(as_text=True)
    about = page[page.index('Over deze cijfers'):page.index('Waarom deze grafiek')]
    assert 'De militaire uitgaven gedeeld door het bbp.' in about and '% van het bbp' in about
    assert 'Machinaal vertaald uit het Engels' in about
    # short_unit stays as it is (the numbers keep "%").
    assert '4,5%' in page
