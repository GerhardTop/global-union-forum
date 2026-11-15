"""Page consolidation step 4: the separate facts list (/feiten) is gone. The
enlarged facts column in the cockpit is the one view; the old URL redirects
there (richting becomes filter, sorteer stays, n is dropped). fact_url and
every fact action land on the issue with the facts enlarged and the fact in
view. The fact routes themselves stay.
"""
import os
import re
from urllib.parse import parse_qs, urlsplit

import pytest

from app import facts as fact_signals
from app import issue_proposals as ip
from app.models import FACT_KIND, FactDispute, Post, PostReport
from tests.test_delete_issue import _clean_session, _cleanup  # noqa: F401  (autouse fixture)
from tests.test_issue_polish import _client, _get, _issue, _req, make_user  # noqa: F401  (fixture)

TEMPLATES = os.path.join(os.path.dirname(__file__), '..', 'app', 'templates')


def _fact(db, issue, user, body):
    fact = Post(thread_id=issue.thread_id, user_id=user.id, author_name='', author_username=user.username,
                body=body, source_lang='nl', kind=FACT_KIND, fact_direction='voor',
                fact_source='https://example.org/bron', fact_title='Kern', fact_type='feit')
    db.session.add(fact)
    db.session.commit()
    return fact


@pytest.fixture
def world(db, make_user):
    host, other = make_user(), make_user()
    issue = _issue(db, host, 'Bladerlijst feiten weg')
    proposal = ip.create(issue, host, 'Een voorstel.')
    db.session.commit()
    yield {'issue': issue, 'proposal': proposal, 'host': host, 'other': other}
    _cleanup(issue.id)


def _loc(resp):
    parts = urlsplit(resp.headers['Location'])
    return parts.path, {k: v[0] for k, v in parse_qs(parts.query).items()}, parts.fragment


def test_old_list_redirects_with_mapped_parameters(app, db, world):
    w = world
    issue = w['issue']
    c = _client(app, w['host'])
    for query, expected in (('', {}), ('?richting=tegen', {'filter': 'tegen'}),
                            ('?sorteer=nieuwste', {'sorteer': 'nieuwste'}),
                            ('?richting=neutraal&sorteer=kwaliteit&n=30', {'filter': 'neutraal', 'sorteer': 'kwaliteit'}),
                            ('?richting=onzin&sorteer=nuttig', {})):
        resp = _req(c, 'GET', f'/vraagstuk/{issue.id}/feiten{query}')
        assert resp.status_code == 302, query
        path, args, anchor = _loc(resp)
        assert path == f'/vraagstuk/{issue.id}' and anchor == 'panel-facts', query
        assert args == dict(expected, vergroot='feiten'), query
    # It lands enlarged with the filter applied.
    page = _req(c, 'GET', f'/vraagstuk/{issue.id}/feiten?richting=tegen', follow_redirects=True).get_data(as_text=True)
    assert 'data-expanded="facts"' in page
    assert _req(c, 'GET', '/vraagstuk/999999/feiten').status_code == 404


def test_the_list_template_is_gone_and_only_noscript_links_to_the_facts_view():
    assert not os.path.exists(os.path.join(TEMPLATES, 'issues', 'facts.html'))
    for folder, _dirs, files in os.walk(TEMPLATES):
        for name in files:
            if name.endswith('.html'):
                with open(os.path.join(folder, name), encoding='utf-8') as f:
                    text = f.read()
                assert not re.search(r"url_for\('issues\.facts'", text), name
                assert not re.search(r'/vraagstuk/[^"\']*/feiten', text), name
    # The no-script "Show more" goes to the enlarged facts in the cockpit.
    with open(os.path.join(TEMPLATES, 'issues', 'show.html'), encoding='utf-8') as f:
        show = f.read()
    assert ("<noscript><a class=\"gu-cockpit__more\" href=\"{{ url_for('issues.show', issue_id=issue.id, "
            "vergroot='feiten', _anchor='panel-facts') }}\">") in show


def test_noscript_show_more_on_the_issue_page(app, db, world):
    w = world
    for i in range(12):
        _fact(db, w['issue'], w['other'], f'Feit {i:02d}.')
    page = _get(app, f'/vraagstuk/{w["issue"].id}', w['host'])
    assert (f'<noscript><a class="gu-cockpit__more" href="/vraagstuk/{w["issue"].id}?vergroot=feiten#panel-facts">'
            in page)
    assert f'/vraagstuk/{w["issue"].id}/feiten' not in page


def test_fact_url_points_to_the_cockpit(app, db, world):
    w = world
    fact = _fact(db, w['issue'], w['other'], 'Een feit met een adres.')
    with app.test_request_context():
        assert fact_signals.fact_url(fact) == f'/vraagstuk/{w["issue"].id}?vergroot=feiten#post-{fact.id}'
    # The anchor exists in the enlarged facts.
    assert f'<li class="gu-fact gu-fact--voor gu-fact--titled" id="post-{fact.id}">' in \
        _get(app, f'/vraagstuk/{w["issue"].id}?vergroot=feiten', w['host'])


def test_after_every_fact_action_you_land_on_the_enlarged_facts(app, db, world, make_user):
    w = world
    issue = w['issue']
    show = f'/vraagstuk/{issue.id}'

    def lands(resp, fact_id=None, path=show):
        got_path, args, anchor = _loc(resp)
        assert got_path == path and args == {'vergroot': 'feiten'}, resp.headers['Location']
        assert anchor == (f'post-{fact_id}' if fact_id else 'panel-facts'), resp.headers['Location']

    # Add (with and without in_page).
    for i, extra in enumerate(({}, {'in_page': '1'})):
        resp = _req(_client(app, w['host']), 'POST', f'/forum/{issue.thread_id}/feiten',
                    data=dict({'title': 'Kern', 'text': f'Toegevoegd {i}.', 'direction': 'voor', 'source': 'https://x.example',
                               'type': 'feit', 'return_to': str(issue.id)}, **extra))
        lands(resp, Post.query.filter_by(body=f'Toegevoegd {i}.').one().id)
    # Edit.
    mine = Post.query.filter_by(body='Toegevoegd 0.').one()
    resp = _req(_client(app, w['host']), 'POST', f'/forum/feit/{mine.id}/bewerken',
                data={'title': 'Kern', 'text': 'Bewerkt.', 'direction': 'tegen', 'source': 'https://x.example',
                      'type': 'feit', 'return_to': str(issue.id)})
    lands(resp, mine.id)
    # Delete.
    resp = _req(_client(app, w['host']), 'POST', f'/forum/feit/{mine.id}/verwijderen', data={'return_to': str(issue.id)})
    lands(resp)
    assert db.session.get(Post, mine.id) is None
    # Dispute and report, with the next the enlarged card gives them (and without one).
    theirs = _fact(db, issue, w['other'], 'Een feit van een ander.')
    card = _get(app, show + '?vergroot=feiten', w['host']).split(f'id="post-{theirs.id}"')[1].split('<li class="gu-fact ')[0]
    dispute_next = re.search(r'gu-fact__disputeform.*?name="next" value="([^"]+)"', card, re.S).group(1)
    report_next = re.search(r'gu-report--fact.*?name="next" value="([^"]+)"', card, re.S).group(1)
    assert dispute_next == report_next == f'{show}?vergroot=feiten#post-{theirs.id}'
    resp = _req(_client(app, w['host']), 'POST', f'/forum/feit/{theirs.id}/betwisten',
                data={'reason': 'geen_bron', 'next': dispute_next})
    lands(resp, theirs.id)
    assert FactDispute.query.filter_by(fact_post_id=theirs.id).count() == 1
    third = make_user()
    resp = _req(_client(app, third), 'POST', f'/forum/feit/{theirs.id}/betwisten', data={'reason': 'klopt_niet'})
    lands(resp, theirs.id)  # without next: the same fallback
    report_url = re.search(r'<form method="POST" action="([^"]+)" class="gu-report gu-report--fact"', card).group(1)
    resp = _req(_client(app, w['host']), 'POST', report_url, data={'reason': 'Dit klopt niet.', 'next': report_next})
    lands(resp, theirs.id)
    assert PostReport.query.filter_by(post_id=theirs.id, reporter_id=w['host'].id).count() == 1
    # From the proposal page the actions come back there.
    resp = _req(_client(app, w['other']), 'POST', f'/forum/{issue.thread_id}/feiten',
                data={'title': 'Kern', 'text': 'Vanaf het voorstel.', 'direction': 'voor', 'source': 'https://x.example',
                      'return_to': str(issue.id), 'in_page': '1', 'return_proposal': str(w['proposal'].id)})
    lands(resp, Post.query.filter_by(body='Vanaf het voorstel.').one().id,
          path=f'/vraagstuk/{issue.id}/voorstel/{w["proposal"].id}')
