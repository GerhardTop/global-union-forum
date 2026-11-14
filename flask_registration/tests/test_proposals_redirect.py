"""Page consolidation step 1: the separate proposals list (/voorstellen) is
gone. The enlarged proposals column in the cockpit is the one view; the old
URL redirects there with its ?filter= and ?sorteer=. Making a proposal and
viewing one stay.
"""
import os
import re
from urllib.parse import parse_qs, urlsplit

import pytest

from app import issue_proposals as ip
from app.models import IssueProposal
from tests.test_delete_issue import _clean_session, _cleanup  # noqa: F401  (autouse fixture)
from tests.test_issue_polish import _client, _get, _issue, _req, make_user  # noqa: F401  (fixture)

TEMPLATES = os.path.join(os.path.dirname(__file__), '..', 'app', 'templates')


@pytest.fixture
def world(db, make_user):
    host = make_user()
    issue = _issue(db, host, 'Bladerlijst weg')
    proposal = ip.create(issue, host, 'Een voorstel om te bekijken.')
    db.session.commit()
    yield issue, proposal, host
    _cleanup(issue.id)


def _location(resp):
    parts = urlsplit(resp.headers['Location'])
    return parts.path, {k: v[0] for k, v in parse_qs(parts.query).items()}, parts.fragment


def test_old_list_redirects_to_the_enlarged_proposals(app, db, world):
    issue, _p, host = world
    c = _client(app, host)
    for query, expected in (('', {}), ('?filter=stemlijst', {'filter': 'stemlijst'}),
                            ('?sorteer=nieuwste', {'sorteer': 'nieuwste'}),
                            ('?filter=steun&sorteer=steun', {'filter': 'steun', 'sorteer': 'steun'})):
        resp = _req(c, 'GET', f'/vraagstuk/{issue.id}/voorstellen{query}')
        assert resp.status_code == 302, query
        path, args, anchor = _location(resp)
        assert path == f'/vraagstuk/{issue.id}' and anchor == 'panel-proposals', query
        assert args == dict(expected, vergroot='voorstellen'), query
    # It lands enlarged, with the filter applied.
    page = _req(c, 'GET', f'/vraagstuk/{issue.id}/voorstellen?filter=stemlijst', follow_redirects=True).get_data(as_text=True)
    assert 'data-expanded="proposals"' in page
    # An unknown issue still gives a 404, not a redirect.
    assert _req(c, 'GET', '/vraagstuk/999999/voorstellen').status_code == 404


def test_the_list_template_is_gone_and_nothing_links_to_the_list():
    assert not os.path.exists(os.path.join(TEMPLATES, 'issues', 'proposals.html'))
    for folder, _dirs, files in os.walk(TEMPLATES):
        for name in files:
            if name.endswith('.html'):
                with open(os.path.join(folder, name), encoding='utf-8') as f:
                    text = f.read()
                assert not re.search(r"url_for\('issues\.proposals'", text), name


def test_links_go_to_the_cockpit(app, db, world):
    issue, proposal, host = world
    enlarged = f'href="/vraagstuk/{issue.id}?vergroot=voorstellen#panel-proposals"'
    detail = _get(app, f'/vraagstuk/{issue.id}/voorstel/{proposal.id}', host)
    assert f'{enlarged}>Voorstellen</a>' in detail.split('class="gu-pv__crumbs"')[1].split('</nav>')[0]
    new = _get(app, f'/vraagstuk/{issue.id}/voorstellen/nieuw', host)
    crumbs = new.split('gu-forum__crumbs gu-issue-crumbs')[1].split('</nav>')[0]
    assert enlarged in crumbs
    assert re.search(rf'<a href="/vraagstuk/{issue.id}" class="gu-btn gu-btn--ghost">Annuleer</a>', new)
    assert re.search(rf'<a href="/vraagstuk/{issue.id}" class="gu-btn gu-btn--ghost">Cancel</a>',
                     _get(app, f'/vraagstuk/{issue.id}/voorstellen/nieuw', host, 'en'))
    for page in (detail, new, _get(app, f'/vraagstuk/{issue.id}', host)):
        assert f'/vraagstuk/{issue.id}/voorstellen"' not in page and f'/vraagstuk/{issue.id}/voorstellen?' not in page


def test_noscript_more_link_opens_the_enlarged_view_server_side(app, db, make_user):
    host = make_user()
    issue = _issue(db, host, 'Veel voorstellen')
    for i in range(ip.FILTER_MIN + 1):
        ip.create(issue, host, f'Voorstel {i:02d}.')
    db.session.commit()
    try:
        page = _get(app, f'/vraagstuk/{issue.id}', host)
        assert (f'<noscript><a class="gu-cockpit__more" href="/vraagstuk/{issue.id}?vergroot=voorstellen#panel-proposals">'
                in page)
        # The server marks the enlarged state itself, so it also works without JavaScript.
        for path in (f'/vraagstuk/{issue.id}', f'/vraagstuk/{issue.id}/voorstel/{IssueProposal.query.filter_by(issue_id=issue.id).first().id}'):
            enlarged = _get(app, path + '?vergroot=voorstellen', host)
            assert re.search(r'<main class="[^"]*gu-cockpit--expanded', enlarged), path
            assert re.search(r'<section class="gu-cockpit__panel gu-cockpit__proposals[^"]* is-expanded" id="panel-proposals"', enlarged), path
            assert 'gu-bigp__bar' in enlarged, path
            plain = _get(app, path, host)
            assert 'gu-cockpit--expanded' not in plain.split('<main')[1].split('>')[0] and ' is-expanded"' not in plain, path
    finally:
        _cleanup(issue.id)


def test_making_and_viewing_a_proposal_still_work(app, db, world):
    issue, _p, host = world
    c = _client(app, host)
    resp = _req(c, 'POST', f'/vraagstuk/{issue.id}/voorstellen/nieuw',
                data={'statement': 'Nieuw voorstel hier.', 'rationale': 'Omdat het kan.'})
    assert resp.status_code == 302
    made = IssueProposal.query.filter_by(issue_id=issue.id, statement='Nieuw voorstel hier.').one()
    assert resp.headers['Location'].endswith(f'/vraagstuk/{issue.id}/voorstel/{made.id}')
    assert '/voorstellen' not in resp.headers['Location']
    page = _req(c, 'GET', resp.headers['Location']).get_data(as_text=True)
    assert 'Nieuw voorstel hier.' in page
