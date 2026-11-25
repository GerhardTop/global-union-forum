"""Background figures, step 1f: the chart page (issues.chart_page,
issues/chart.html and issues/_chart_block.html) and the short source line.

Charts are published the way a selection round does (a fake AI client and a
fake http with the OWID fixtures); never network.
"""
import re
import uuid
from types import SimpleNamespace

import pytest

from app import background_figures as bf, conduct, issue_proposals as ip
from app.models import IssueProposalFact
from tests.test_background_figures import (FakeClient, FakeHttp, _answer, _chart, _charts, _issue, _proposal,  # noqa: F401
                                           _run_for, made, started, watched)
from tests.test_delete_issue import _clean_session  # noqa: F401  (autouse fixture)
from tests.test_issue_polish import _client, _fact, _req, make_user  # noqa: F401  (fixture)
from tests.test_proposal_queries import _count_queries, _shape  # noqa: F401


def _published(db, make_user, made, chart=None, proposals=0, owner=None):
    owner = owner or make_user()
    issue = _issue(db, owner, made)
    props = [_proposal(db, issue, owner) for _ in range(proposals)]
    from app.models import ChartSelectionRun
    first = ChartSelectionRun.query.filter_by(issue_id=issue.id, trigger='issue').first()
    assert bf.run(first.id, client=FakeClient(_answer(chart or _chart(relevant=[p.id for p in props]))),
                  http=FakeHttp()) == 'done'
    (fact_chart,) = _charts(issue)
    return issue, fact_chart, props


def _get(app, issue, chart, user=None, query='', lang='nl'):
    return _req(_client(app, user, lang), 'GET', f'/vraagstuk/{issue.id}/cijfer/{chart.post_id}{query}')


# ── The short source ─────────────────────────────────────────────────────

@pytest.mark.parametrize('short, full, expected', [
    ('Stockholm International Peace Research Institute (2026) \u2013 with minor processing by Our World in Data',
     'X', 'Stockholm International Peace Research Institute (2026)'),
    ('SIPRI (2026) - with major processing by Our World in Data', 'X', 'SIPRI (2026)'),
    ('SIPRI (2026) \u2014 with minor processing', 'X', 'SIPRI (2026)'),
    ('World Bank (2025) \u2013', 'X', 'World Bank (2025)'),
    ('World Bank (2025) -', 'X', 'World Bank (2025)'),
    ('Self-reported data (2024)', 'X', 'Self-reported data (2024)'),
    ('\u2013 with minor processing', 'Full citation (2026)', 'Full citation (2026)'),
    ('', 'Full citation (2026)', 'Full citation (2026)'),
    (None, 'Full citation (2026)', 'Full citation (2026)'),
])
def test_short_citation(short, full, expected):
    assert bf.short_citation(SimpleNamespace(citation_short=short, citation=full)) == expected


def test_the_card_shows_the_short_source_and_the_enlarge_link(app, db, make_user, made, started, watched):
    issue, chart, _props = _published(db, make_user, made)
    page = _req(_client(app), 'GET', f'/vraagstuk/{issue.id}').get_data(as_text=True)
    assert 'Stockholm International Peace Research Institute (2026), via Our World in Data</a>' in page
    assert 'with minor processing' not in page
    assert f'href="/vraagstuk/{issue.id}/cijfer/{chart.post_id}"' in page and 'Grafiek uitvergroten' in page


# ── Who sees the page ────────────────────────────────────────────────────

def test_a_visitor_sees_the_page(app, db, make_user, made, started, watched):
    issue, chart, _props = _published(db, make_user, made)
    resp = _get(app, issue, chart)
    assert resp.status_code == 200
    page = resp.get_data(as_text=True)
    assert 'css/charts.css' in page and 'js/chart_page.js' in page
    assert '<h1' in page and 'Military spending as a share of GDP' in page
    assert 'class="gu-chart gu-chart--large"' in page
    assert '2016 tot 2025 · 4 van 4 landen' in page
    assert 'Over deze cijfers' in page and 'Waarom deze grafiek' in page
    assert 'Total military expenditure divided by gross domestic product' in page          # the definition
    assert 'https://creativecommons.org/licenses/by/4.0/' in page
    assert 'It shows what each country spends on defence' in page                          # the reason
    assert 'Terug naar het vraagstuk' in page
    assert f'/vraagstuk/{issue.id}?vergroot=feiten#post-{chart.post_id}' in page
    assert 'Stockholm International Peace Research Institute (2026), via Our World in Data' in page
    for gone in ('Koppelen aan een voorstel', 'grafiek-verwijderen', 'Gekoppeld aan', 'Spanje toevoegen',
                 'Land toevoegen'):
        assert gone not in page
    assert '\u2013' not in page and '\u2014' not in page


def test_not_found(app, db, make_user, made, started, watched, monkeypatch):
    issue, chart, _props = _published(db, make_user, made)
    other = _issue(db, make_user(), made)
    assert _req(_client(app), 'GET', f'/vraagstuk/{other.id}/cijfer/{chart.post_id}').status_code == 404
    fact = _fact(db, issue, make_user(), body=f'Een gewoon feit {uuid.uuid4().hex[:6]}.')
    assert _req(_client(app), 'GET', f'/vraagstuk/{issue.id}/cijfer/{fact.id}').status_code == 404
    assert _req(_client(app), 'GET', f'/vraagstuk/{issue.id}/cijfer/999999').status_code == 404
    monkeypatch.setattr(conduct, 'is_removed_for', lambda post, user: post.id == chart.post_id)
    assert _get(app, issue, chart).status_code == 404


def test_a_deleted_chart_is_gone(app, db, make_user, made, started, watched):
    issue, chart, _props = _published(db, make_user, made)
    moderator = make_user(role='moderator')
    _req(_client(app, moderator), 'POST', f'/forum/feit/{chart.post_id}/grafiek-verwijderen',
         data={'return_to': issue.id})
    assert _get(app, issue, chart).status_code == 404
    from app.models import ModerationAction
    ModerationAction.query.filter_by(action='delete_chart', actor_id=moderator.id).delete()   # clean up
    db.session.commit()


# ── The query string ─────────────────────────────────────────────────────

def _pressed(page, value):
    return re.search(rf'value="{re.escape(value)}"[^>]*aria-pressed="true"', page) is not None


def test_query_parameters(app, db, make_user, made, started, watched):
    issue, chart, _props = _published(db, make_user, made)
    page = _get(app, issue, chart, query='?weergave=tabel&periode=5').get_data(as_text=True)
    assert '<table>' in page and 'gu-chart--large' not in page
    assert _pressed(page, 'weergave:tabel') and _pressed(page, 'periode:5')
    assert page.count('<th scope="row">') == 5
    # Invalid values become the defaults; "alles" does not exist with ten years.
    page = _get(app, issue, chart, query='?weergave=taart&periode=alles&verberg=').get_data(as_text=True)
    assert 'gu-chart--large' in page and _pressed(page, 'periode:10') and 'periode:alles' not in page
    # Unknown keys fall away; a known one hides its series (still in the legend).
    page = _get(app, issue, chart, query='?verberg=XXX,NLD,,NLD').get_data(as_text=True)
    assert _pressed(page, 'wissel:POL') and not _pressed(page, 'wissel:NLD')
    assert 'name="verberg" value="NLD"' in page and 'data-series="NLD"' not in page
    assert '4 van 4' not in page and '3 van 4 landen' in page
    # Everything hidden: a short text instead of the chart.
    page = _get(app, issue, chart, query='?verberg=POL,FRA,DEU,NLD').get_data(as_text=True)
    assert 'Alles is verborgen' in page and 'gu-chart--large' not in page


def test_all_years_only_with_more_than_ten(app, db, make_user, made, started, watched):
    issue, chart, _props = _published(db, make_user, made, _chart(years=16))
    page = _get(app, issue, chart, query='?periode=alles').get_data(as_text=True)
    assert _pressed(page, 'periode:alles') and '2010 tot 2025' in page


def test_a_button_without_javascript_redirects_to_the_plain_url(app, db, make_user, made, started, watched):
    issue, chart, _props = _published(db, make_user, made)
    resp = _get(app, issue, chart, query='?weergave=grafiek&periode=10&verberg=&zet=wissel:FRA')
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith(f'/vraagstuk/{issue.id}/cijfer/{chart.post_id}?verberg=FRA')
    resp = _get(app, issue, chart, query='?verberg=FRA&zet=wissel:FRA')
    assert resp.headers['Location'].endswith(f'/cijfer/{chart.post_id}')


def test_the_fragment_is_only_the_block(app, db, make_user, made, started, watched):
    issue, chart, _props = _published(db, make_user, made)
    resp = _get(app, issue, chart, query='?weergave=grafiek&periode=10&verberg=&zet=weergave:tabel&fragment=1')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True).strip()
    assert html.startswith('<div class="gu-chartpage__block" data-chart-block')
    assert '<html' not in html and 'Over deze cijfers' not in html and '<h1' not in html
    assert 'data-announce="Tabel getoond"' in html
    assert f'data-url="/vraagstuk/{issue.id}/cijfer/{chart.post_id}?weergave=tabel"' in html
    html = _get(app, issue, chart, query='?zet=wissel:NLD&fragment=1').get_data(as_text=True)
    assert 'data-announce="Nederland verborgen"' in html


def test_the_table_has_empty_cells_with_a_hidden_text_and_no_dashes(app, db, make_user, made, started, watched):
    issue, chart, _props = _published(db, make_user, made,
                                      _chart(slug='regions-chart', countries=['Netherlands'], average='World'))
    page = _get(app, issue, chart, query='?weergave=tabel').get_data(as_text=True)
    table = page[page.index('<table>'):page.index('</table>')]
    assert '<span class="gu-sr-only">geen waarde</span>' in table
    assert '\u2013' not in table and '\u2014' not in table and '>-<' not in table
    assert 'World, zoals de bron het levert' in page


# ── Linked to, linking, deleting ─────────────────────────────────────────

def test_linked_to_shows_ai_and_manual_links(app, db, make_user, made, started, watched):
    owner = make_user()
    issue, chart, props = _published(db, make_user, made, proposals=2, owner=owner)
    first, second = props
    IssueProposalFact.query.filter_by(fact_post_id=chart.post_id, proposal_id=second.id).delete()
    db.session.commit()
    page = _get(app, issue, chart).get_data(as_text=True)
    linked = page[page.index('Gekoppeld aan'):page.index('Gekozen door AI op')]
    assert first.statement in linked and second.statement not in linked
    db.session.add(IssueProposalFact(proposal_id=second.id, fact_post_id=chart.post_id, linked_by_id=owner.id))
    db.session.commit()
    linked = _get(app, issue, chart).get_data(as_text=True)
    linked = linked[linked.index('Gekoppeld aan'):linked.index('Gekozen door AI op')]
    assert first.statement in linked and second.statement in linked
    assert f'href="/vraagstuk/{issue.id}/voorstel/{second.id}"' in linked
    IssueProposalFact.query.filter_by(fact_post_id=chart.post_id).delete()
    db.session.commit()
    assert 'Gekoppeld aan' not in _get(app, issue, chart).get_data(as_text=True)


def test_link_to_a_proposal_through_the_existing_route(app, db, make_user, made, started, watched):
    owner, member = make_user(), make_user()
    issue, chart, props = _published(db, make_user, made, _chart(relevant=[]), proposals=2, owner=owner)
    assert 'Koppelen aan een voorstel' not in _get(app, issue, chart).get_data(as_text=True)     # a visitor
    page = _get(app, issue, chart, member, query='?weergave=tabel').get_data(as_text=True)
    assert 'Koppelen aan een voorstel' in page
    assert f'action="/vraagstuk/{issue.id}/koppelen"' in page
    back = f'/vraagstuk/{issue.id}/cijfer/{chart.post_id}?weergave=tabel'
    resp = _req(_client(app, member), 'POST', f'/vraagstuk/{issue.id}/koppelen',
                data={'proposal_id': props[1].id, 'fact_ids': chart.post_id, 'next': back})
    assert resp.status_code in (302, 303) and resp.headers['Location'].endswith(back)
    row = IssueProposalFact.query.filter_by(fact_post_id=chart.post_id, proposal_id=props[1].id).one()
    assert row.linked_by_id == member.id
    # A proposal of another issue is not found; voting phase: no linking.
    other = _issue(db, make_user(), made)
    other_proposal = _proposal(db, other, owner)
    assert _req(_client(app, member), 'POST', f'/vraagstuk/{issue.id}/koppelen',
                data={'proposal_id': other_proposal.id, 'fact_ids': chart.post_id}).status_code == 404
    issue.phase = 'stemmen'
    db.session.commit()
    assert 'Koppelen aan een voorstel' not in _get(app, issue, chart, member).get_data(as_text=True)
    assert ip.can_link(props[0], member) is False


def test_delete_only_for_moderators(app, db, make_user, made, started, watched):
    issue, chart, _props = _published(db, make_user, made)
    path = f'/forum/feit/{chart.post_id}/grafiek-verwijderen'
    assert path not in _get(app, issue, chart, make_user()).get_data(as_text=True)
    assert path in _get(app, issue, chart, make_user(role='moderator')).get_data(as_text=True)


def test_a_closed_issue_still_shows_the_page(app, db, make_user, made, started, watched):
    issue, chart, _props = _published(db, make_user, made)
    issue.is_closed = True
    db.session.commit()
    assert _get(app, issue, chart).status_code == 200


# ── A fixed number of queries ────────────────────────────────────────────

@pytest.mark.parametrize('who', ['visitor', 'member'])
def test_queries_do_not_grow_with_linked_proposals(app, db, make_user, made, started, watched, who):
    counts, shapes = {}, {}
    for n in (1, 5, 20):
        owner = make_user()
        issue, chart, props = _published(db, make_user, made, proposals=n, owner=owner)
        assert IssueProposalFact.query.filter_by(fact_post_id=chart.post_id).count() == n
        user = make_user() if who == 'member' else None
        path = f'/vraagstuk/{issue.id}/cijfer/{chart.post_id}'
        client = _client(app, user)
        _req(client, 'GET', path)
        db.session.commit()
        db.session.expunge_all()
        with _count_queries() as queries:
            resp = _req(client, 'GET', path)
        assert resp.status_code == 200
        counts[n], shapes[n] = len(queries), [_shape(q) for q in queries]
    print(f'\nchart page query counts ({who}):', counts)
    assert counts[1] == counts[5] == counts[20], counts
