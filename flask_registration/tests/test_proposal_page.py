"""
The proposal screen built to docs/design/voorstel.html: the context rail
(facts and background with "Bij dit voorstel" / "Alle" and fact linking, or
proposals, ten shown, the current one highlighted), the head row with a
quiet link to the issue's voting rules and "Voorstel aanpassen" for the
proposer only, pinned top right,
and the reactions grouped per version.
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
    all_facts = rail.split('data-fact-panel="all"')[1].split('id="feiten-koppelen"')[0]
    assert all_facts.count('Feit nummer') == 12 and rail.count('gu-pv__pitemtxt') == 11
    assert rail.count('data-rail-more') == 2 and 'Toon meer' in rail  # all facts and proposals; none linked yet
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
                 'Facts and background', 'With this proposal', 'Voting rules for this issue', 'All proposals', 'Current version', 'Reactions to version 1',
                 '0 supporters', 'Write a reaction'):
        assert text in page
    css = _req(_client(app), 'GET', '/static/css/kit.css').get_data(as_text=True)
    phone = css.split('/* Phone: the rail stacks above the main column. */')[1]
    assert '.gu-pv__layout { grid-template-columns: minmax(0, 1fr)' in phone


def _rail(page):
    return page.split('class="gu-pv__rail"')[1].split('class="gu-pv__main"')[0]


def _main(page):
    return page.split('class="gu-pv__main"')[1].split('</main>')[0]


def test_facts_sub_choice_with_this_proposal_and_all(app, db, make_user):
    host = make_user()
    issue = _issue(db, host)
    linked = _fact(db, issue, host, 'Gekoppeld feit.')
    _fact(db, issue, host, 'Los feit.')
    proposal = ip.create(issue, host, 'Een stelling.')
    db.session.commit()
    ip.link_ids(proposal, [linked.id], host)
    db.session.commit()
    page = _get(app, _detail(issue, proposal), host)
    rail = _rail(page)
    # The choice with both counts, "Bij dit voorstel" first and pressed.
    seg = rail.split('class="gu-pv__subseg"')[1].split('</div>')[0]
    assert 'Bij dit voorstel <span class="gu-pv__c">1</span>' in seg and 'Alle <span class="gu-pv__c">2</span>' in seg
    assert seg.index('data-fact-view="linked" aria-pressed="true"') < seg.index('data-fact-view="all" aria-pressed="false"')
    # The facts tab is the default, as on the artboard.
    assert 'data-rail-tab="facts" aria-selected="true"' in rail
    mine = rail.split('data-fact-panel="linked"')[1].split('data-fact-panel="all"')[0]
    assert 'Gekoppeld feit.' in mine and 'Los feit.' not in mine
    everything = rail.split('data-fact-panel="all"')[1].split('id="feiten-koppelen"')[0]
    assert 'Gekoppeld feit.' in everything and 'Los feit.' in everything
    # The middle column has neither the facts block nor a vote card any more.
    main = _main(page)
    assert 'Gekoppeld feit.' not in main and 'id="stemming"' not in main and 'Waarop dit voorstel' not in main


def test_link_and_unlink_from_the_rail(app, db, make_user):
    host = make_user()
    issue = _issue(db, host)
    fact = _fact(db, issue, host, 'Te koppelen feit.')
    proposal = ip.create(issue, host, 'Een stelling.')
    db.session.commit()
    rail = _rail(_get(app, _detail(issue, proposal), host))
    assert 'Feiten koppelen' in rail
    item = rail.split('data-fact-panel="all"')[1].split('Te koppelen feit.')[1].split('</li>')[0]
    action = f'/vraagstuk/{issue.id}/voorstel/{proposal.id}/koppelen'
    assert f'action="{action}"' in item and f'name="fact_ids" value="{fact.id}"' in item
    # That button links the fact; it then shows as linked, with "Ontkoppelen".
    r = _req(_client(app, host), 'POST', action, data={'fact_ids': str(fact.id)})
    assert r.status_code == 302 and r.headers['Location'].endswith('#basis')
    rail = _rail(_get(app, _detail(issue, proposal), host))
    item = rail.split('data-fact-panel="all"')[1].split('Te koppelen feit.')[1].split('</li>')[0]
    assert 'Gekoppeld' in item and 'Ontkoppelen' in item and 'name="fact_ids"' not in item
    assert 'Te koppelen feit.' in rail.split('data-fact-panel="linked"')[1].split('data-fact-panel="all"')[0]
    # Without the right to link: no link or unlink controls.
    anon = _rail(_get(app, _detail(issue, proposal)))
    assert 'Feiten koppelen' not in anon and 'Ontkoppelen' not in anon and 'name="fact_ids"' not in anon
    assert 'Gekoppeld' in anon.split('data-fact-panel="all"')[1]


def test_voting_rules_link_grey_support_and_pinned_button(app, db, make_user):
    host = make_user()
    issue = _issue(db, host)
    proposal = ip.create(issue, host, 'Een stelling.')
    issue.phase = 'bijstellen'
    db.session.commit()
    page = _get(app, _detail(issue, proposal), host)
    # No rules block in the rail any more; a quiet link right after the support count.
    assert 'id="stemregels"' not in page and 'Hoe werkt stemmen?' not in page
    meta = page.split('class="gu-pv__statusrow"')[1].split('gu-pv__revisebtn')[0]
    assert meta.index('steunbetuigingen') < meta.index('class="gu-pv__ruleslink"')
    assert f'class="gu-pv__ruleslink" href="/vraagstuk/{issue.id}#stemregels">Stemregels bij dit vraagstuk</a>' in meta
    # The separator travels with the support count, so a line break never strands it.
    assert '<span class="gu-pv__metaitem"><span class="gu-pv__sep" aria-hidden="true">&middot;</span>' in meta
    item = meta.split('gu-pv__metaitem')[1].split('</span></span>')[0]
    assert 'steunbetuigingen' in item
    en = _get(app, _detail(issue, proposal), host, 'en')
    assert '>Voting rules for this issue</a>' in en
    css = _req(_client(app), 'GET', '/static/css/kit.css').get_data(as_text=True)
    rule = lambda sel: css.split(sel + ' {')[1].split('}')[0]  # noqa: E731
    # Support in the normal grey meta colour, not green.
    assert 'color: var(--gu-fg-2)' in rule('.gu-pv__steun') and 'oceania' not in rule('.gu-pv__steun')
    # The header row does not wrap; the meta takes the room and wraps inside; the button never shrinks.
    assert 'flex-wrap: nowrap' in rule('.gu-pv__headrow')
    assert 'flex: 1 1 auto' in rule('.gu-pv__statusrow') and 'flex-wrap: wrap' in rule('.gu-pv__statusrow')
    assert 'flex: 0 0 auto' in rule('.gu-pv__revisebtn')
    assert 'white-space: nowrap' in rule('.gu-pv__metaitem')
    assert '.gu-pv__raildet' not in css


def test_issue_page_has_the_voting_rules_block(app, db, make_user):
    host, voter = make_user(), make_user('voter')
    issue = _issue(db, host)
    for viewer in (host, voter, None):  # also for whoever already has the right to vote
        page = _get(app, f'/vraagstuk/{issue.id}', viewer)
        block = page.split('id="stemregels"')[1].split('</section>')[0]
        assert 'data-panel="proposals"' in block and '>Stemregels</h2>' in block
        assert 'Alleen over voorstellen met genoeg steun wordt gestemd.' in block
        assert f'Aangenomen bij meer dan {issue.proposal_threshold_pct}% voor van de geldige stemmen.' in block
        assert ('samenvattend voorstel' in block) is bool(issue.final_vote)
        assert 'Onthoudingen' not in page
    issue.final_vote = False
    db.session.commit()
    block = _get(app, f'/vraagstuk/{issue.id}', host, 'en').split('id="stemregels"')[1].split('</section>')[0]
    assert '>Voting rules</h2>' in block and 'Only proposals with enough support are voted on.' in block
    assert f'more than {issue.proposal_threshold_pct}% in favour of the valid votes' in block
    assert 'summary proposal' not in block and 'Abstentions' not in block
