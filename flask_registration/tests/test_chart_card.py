"""Background figures, step 1e: the chart card in the facts column
(issues/_fact_card.html), the report flag, and removal by a moderator.

The charts are published the way a selection round does (tests of step 1d:
a fake AI client and a fake http with the OWID fixtures); never network.
"""
import re
import uuid

import pytest

from app import background_figures as bf, content_i18n
from app.models import ChartBlock, FactChart, ModerationAction, Post, PostReport
from tests.test_background_figures import (FakeClient, FakeHttp, _answer, _chart, _charts, _issue, _run_for,  # noqa: F401
                                           made, started, watched)
from tests.test_delete_issue import _clean_session  # noqa: F401  (autouse fixture)
from tests.test_issue_polish import _client, _fact, _req, make_user  # noqa: F401  (fixture)


def _published(db, make_user, made, chart=None):
    owner = make_user()
    issue = _issue(db, owner, made)
    assert bf.run(_run_for(issue).id, client=FakeClient(_answer(chart or _chart())), http=FakeHttp()) == 'done'
    (fact_chart,) = _charts(issue)
    return issue, fact_chart


def _page(app, issue, user=None, lang='nl', query=''):
    return _req(_client(app, user, lang), 'GET', f'/vraagstuk/{issue.id}{query}').get_data(as_text=True)


def _card(page, post_id):
    """The column's card of one fact (the first one in the page)."""
    start = page.index(f'id="aisel-{post_id}"')
    end = page.find('gu-fact__topline', start)
    return page[page.rindex('gu-fact__topline--chart', 0, start):end if end > 0 else len(page)]


@pytest.mark.parametrize('lang, france, tip, source', [
    ('nl', 'Frankrijk', 'Door AI geselecteerd uit Our World in Data op', 'Bron'),
    ('de', 'Frankreich', 'Von KI ausgewählt aus Our World in Data am', 'Quelle'),
    ('fr', 'France', 'Sélectionné par IA dans Our World in Data le', 'Source'),
])
def test_the_card_in_three_languages(app, db, make_user, made, started, watched, lang, france, tip, source):
    issue, chart = _published(db, make_user, made)
    page = _page(app, issue, lang=lang)
    assert 'css/charts.css' in page
    card = _card(page, chart.post_id)
    assert 'class="gu-chart gu-chart--small"' in card and 'role="img"' in card
    assert france in card and tip in card
    assert {'nl': 'AI-selectie', 'de': 'KI-Auswahl', 'fr': 'Sélection par IA'}[lang] in card
    assert f'{source}: <a href="https://ourworldindata.org/grapher/military-spending-as-a-share-of-gdp-sipri"' in card
    assert 'Stockholm International Peace Research Institute (2026)' in card
    assert '\u2013' not in card and '\u2014' not in card
    # The period line and the legend with the last value.
    assert {'nl': '2016 tot 2025 · 4 landen', 'de': '2016 bis 2025 · 4 Länder',
            'fr': 'De 2016 à 2025 · 4 pays'}[lang] in card
    assert card.count('class="gu-chart-legend__value"') == 4
    # No text of the fact (the reason comes on the chart page), no "your fact".
    assert 'It shows what each country spends' not in card


def test_country_names_per_language(app, db, make_user, made, started, watched):
    issue, chart = _published(db, make_user, made)
    from app.sources import countries
    assert countries.display_name('NL', 'nl') == 'Nederland'            # our list
    assert countries.display_name('NL', 'de') == 'Niederlande'          # Babel
    assert countries.display_name('NL', 'es') == 'Países Bajos'
    assert countries.display_name('NL', 'fr') == 'Pays-Bas'
    label = re.search(r'aria-label="(Lijngrafiek van [^"]*)"', _page(app, issue)).group(1)
    assert label.startswith('Lijngrafiek van Military spending as a share of GDP, 2016 tot 2025, voor Polen, ')
    assert label.endswith('Duitsland en Nederland.')


def test_the_average_name_is_translated_with_english_as_fallback(app, db, make_user, made, started, watched):
    issue, chart = _published(db, make_user, made,
                              _chart(slug='regions-chart', countries=['Netherlands'], average='World'))
    assert chart.average_name == 'World'
    card = _card(_page(app, issue), chart.post_id)
    assert '1 land en World' in card and 'met World.' in _page(app, issue)
    content_i18n.set_translation(chart, 'average_name', 'nl', 'Wereld', is_machine=True)
    db.session.commit()
    card = _card(_page(app, issue), chart.post_id)
    assert '1 land en Wereld' in card and 'gu-chart-swatch--average' in card
    legend = card[card.index('<ul class="gu-chart-legend">'):]
    assert 'Wereld' in legend[:legend.index('</ul>')]


def test_the_average_name_goes_through_the_translation_route(app, db, make_user, made, started, watched):
    from app import content_jobs
    issue, chart = _published(db, make_user, made,
                              _chart(slug='regions-chart', countries=['Netherlands'], average='World'))
    post = db.session.get(Post, chart.post_id)
    missing = {(content_i18n.object_type(t), f) for t, f, _lang in content_jobs.missing(post)}
    assert ('fact_charts', 'average_name') in missing and ('posts', 'fact_title') in missing
    assert content_i18n.source_lang(chart) == 'en'


def test_no_edit_or_delete_for_a_participant_and_a_flag_for_another_member(app, db, make_user, made, started,
                                                                            watched):
    issue, chart = _published(db, make_user, made)
    member = make_user()
    page = _page(app, issue, member, query='?vergroot=feiten')
    assert f'/forum/feit/{chart.post_id}/bewerken' not in page
    assert f'/forum/feit/{chart.post_id}/verwijderen' not in page
    assert f'/forum/feit/{chart.post_id}/grafiek-verwijderen' not in page
    assert 'Jouw feit' not in _card(page, chart.post_id)
    assert f'action="/forum/bijdrage/{chart.post_id}/melden"' in page
    # A visitor gets no flag.
    assert f'/forum/bijdrage/{chart.post_id}/melden' not in _page(app, issue)


def test_a_member_reports_a_chart_and_it_reaches_the_moderators(app, db, make_user, made, started, watched):
    issue, chart = _published(db, make_user, made)
    member, moderator = make_user(), make_user(role='moderator')
    page = _page(app, issue, member, query='?vergroot=feiten')
    action = f'/forum/bijdrage/{chart.post_id}/melden'
    assert f'action="{action}"' in page
    reason = f'Klopt niet {uuid.uuid4().hex[:6]}'
    _req(_client(app, member), 'POST', action, data={'reason': reason, 'next': f'/vraagstuk/{issue.id}'})
    assert PostReport.query.filter_by(post_id=chart.post_id, reporter_id=member.id).count() == 1
    dashboard = _req(_client(app, moderator), 'GET', '/moderator?alle=1')
    assert dashboard.status_code == 200 and reason in dashboard.get_data(as_text=True)
    PostReport.query.filter_by(post_id=chart.post_id).delete()
    db.session.commit()


def test_a_moderator_removes_a_chart(app, db, make_user, made, started, watched):
    issue, chart = _published(db, make_user, made)
    member, moderator = make_user(), make_user(role='moderator')
    post_id = chart.post_id
    path = f'/forum/feit/{post_id}/grafiek-verwijderen'
    assert path in _page(app, issue, moderator, query='?vergroot=feiten')
    assert _req(_client(app, member), 'POST', path, data={'return_to': issue.id}).status_code == 403
    resp = _req(_client(app, moderator), 'POST', path, data={'return_to': issue.id, 'in_page': '1'})
    assert resp.status_code in (302, 303) and 'vergroot=feiten' in resp.headers['Location']
    assert db.session.get(Post, post_id) is None and FactChart.query.filter_by(post_id=post_id).count() == 0
    assert ChartBlock.query.filter_by(issue_id=issue.id, kind='slug', slug=chart.slug).count() == 1
    action = ModerationAction.query.filter_by(action='delete_chart', target_id=post_id, actor_id=moderator.id).one()
    assert action.issue_id == issue.id and action.actor_id == moderator.id
    # The manage screen (an admin may open it) shows the logged action.
    manage = _req(_client(app, make_user(role='senior_moderator', admin=True)), 'GET', f'/moderator/vraagstuk/{issue.id}/beheer')
    assert manage.status_code == 200 and 'Military spending as a share' in manage.get_data(as_text=True)
    ModerationAction.query.filter_by(action='delete_chart', target_id=post_id).delete()
    db.session.commit()


def test_an_ordinary_fact_cannot_go_through_the_chart_route(app, db, make_user, made, started):
    owner, moderator = make_user(), make_user(role='moderator')
    issue = _issue(db, owner, made)
    fact = _fact(db, issue, owner, body=f'Een gewoon feit {uuid.uuid4().hex[:6]}.')
    resp = _req(_client(app, moderator), 'POST', f'/forum/feit/{fact.id}/grafiek-verwijderen',
                data={'return_to': issue.id})
    assert resp.status_code == 403 and db.session.get(Post, fact.id) is not None


def test_stale_data_falls_away(app, db, make_user, made, started, watched, monkeypatch):
    monkeypatch.setattr(bf, 'MAX_DATA_AGE_YEARS', 0)          # the fixture ends in 2025
    issue = _issue(db, make_user(), made)
    run_row = _run_for(issue)
    assert bf.run(run_row.id, client=FakeClient(_answer(_chart())), http=FakeHttp()) == 'nothing'
    assert 'stale (the last data is from 2025)' in run_row.__class__.query.get(run_row.id).error
    assert bf.MAX_DATA_AGE_YEARS == 0 and _charts(issue) == []


def test_recent_enough_data_stays(app, db, make_user, made, started, watched):
    issue, chart = _published(db, make_user, made)
    assert chart.year_to == 2025


def test_the_prompt_has_the_sharper_rules():
    assert 'Prefer one chart. Add a second only if it covers a different aspect' in bf._SELECTION_SYSTEM
    assert 'Do not repeat or paraphrase the positions or wording of any proposal' in bf._SELECTION_SYSTEM
    assert bf.MAX_DATA_AGE_YEARS == 5
