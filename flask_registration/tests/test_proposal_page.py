"""
The proposal screen built to docs/design/voorstel.html: the context rail
(facts and background / proposals, ten shown, the current one highlighted),
the head row with "Voorstel aanpassen" for the proposer only, and the
reactions grouped per version.
"""
from datetime import datetime

from app import issue_proposals as ip
from app.models import FACT_KIND, Post
from tests.test_issue_polish import _client, _get, _issue, _req, make_user  # noqa: F401  (fixture)
from tests.test_forum_conduct import _fresh_rate_limits, fake_ai  # noqa: F401  (autouse)


def _fact(db, issue, user, body):
    fact = Post(thread_id=issue.thread_id, user_id=user.id, author_name='', author_username=user.username,
                body=body, source_lang='nl', kind=FACT_KIND, fact_direction='voor', fact_source='https://example.org')
    db.session.add(fact)
    return fact


def _detail(issue, proposal):
    return f'/vraagstuk/{issue.id}/voorstel/{proposal.id}'


def test_rail_lists_facts_and_proposals_with_totals(app, db, make_user):
    host = make_user()
    issue = _issue(db, host)
    for i in range(12):
        _fact(db, issue, host, f'Feit nummer {i}.')
    proposals = [ip.create(issue, host, f'Stelling {i}.') for i in range(11)]
    db.session.commit()
    current = proposals[3]
    page = _get(app, _detail(issue, current), host)
    rail = page.split('class="gu-pv__rail"')[1].split('class="gu-pv__main"')[0]
    # The tab counts are the totals; both lists render in full (the script shows ten, then "Toon meer").
    assert 'Feiten en achtergrond <span class="gu-pv__cnt">12</span>' in rail
    assert 'Voorstellen <span class="gu-pv__cnt">11</span>' in rail
    assert rail.count('Feit nummer') == 12 and rail.count('gu-pv__pitemtxt') == 11
    assert rail.count('data-rail-more') == 2 and 'Toon meer' in rail
    # The current proposal is highlighted; each item has its status and version.
    current_item = rail.split('is-current')[1].split('</a>')[0]
    assert 'Stelling 3.' in current_item and 'aria-current="page"' in current_item and 'v1' in current_item
    assert 'Verzamelt steun' in current_item
    assert f'href="/vraagstuk/{issue.id}/voorstellen"' in rail and 'Alle voorstellen' in rail


def test_rail_without_show_more_when_ten_or_fewer(app, db, make_user):
    host = make_user()
    issue = _issue(db, host)
    proposal = ip.create(issue, host, 'Eén stelling.')
    db.session.commit()
    rail = _get(app, _detail(issue, proposal), host).split('class="gu-pv__rail"')[1].split('class="gu-pv__main"')[0]
    assert 'data-rail-more' not in rail and 'Nog geen feiten' in rail


def test_adjust_button_only_for_the_proposer(app, db, make_user):
    host, other = make_user(), make_user()
    issue = _issue(db, host)
    proposal = ip.create(issue, other, 'Stelling van een ander.')
    issue.phase = 'bijstellen'
    db.session.commit()
    mine = _get(app, _detail(issue, proposal), other)
    head = mine.split('class="gu-pv__headrow"')[1].split('id="bijstellen"')[0]
    assert '>Voorstel aanpassen</a>' in head and 'href="#bijstellen"' in head
    assert f'door {other.public_name}' in head and '0 steunbetuigingen' in head
    for viewer in (host, make_user(), None):
        page = _get(app, _detail(issue, proposal), viewer)
        assert '>Voorstel aanpassen</a>' not in page and 'id="bijstellen"' not in page


def test_reaction_without_version_goes_with_the_current_version(app, db, make_user):
    host, anna = make_user(), make_user()
    issue = _issue(db, host)
    proposal = ip.create(issue, host, 'Eerste.')
    issue.phase = 'bijstellen'
    db.session.commit()
    ip.revise(proposal, 'Tweede.', None)
    db.session.add(Post(thread_id=issue.thread_id, user_id=anna.id, author_name='', author_username=anna.username,
                        body='Reactie zonder versie.', source_lang='nl', issue_proposal_id=proposal.id,
                        created_at=datetime.utcnow()))
    db.session.commit()
    page = _get(app, _detail(issue, proposal), anna)
    assert 'Reactie zonder versie.' in page.split('id="reacties"')[1]
    older = page.split('id="versies"')[1].split('class="gu-pv__curhead"')[0]
    assert 'Reactie zonder versie.' not in older and '>Eerste.</p>' in older


def test_page_in_english_and_mobile_stacking_css(app, db, make_user):
    host = make_user()
    issue = _issue(db, host)
    proposal = ip.create(issue, host, 'Plant trees.')
    db.session.commit()
    page = _get(app, _detail(issue, proposal), host, 'en')
    for text in ('Back to the issue', 'Have your say', '<span aria-current="page">Proposal</span>',
                 'Facts and background', 'All proposals', 'Current version', 'Reactions to version 1',
                 '0 supporters', 'Write a reaction'):
        assert text in page
    css = _req(_client(app), 'GET', '/static/css/kit.css').get_data(as_text=True)
    phone = css.split('/* Phone: the rail stacks above the main column. */')[1]
    assert '.gu-pv__layout { grid-template-columns: minmax(0, 1fr)' in phone
