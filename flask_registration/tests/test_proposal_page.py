"""
The proposal screen built to docs/design/voorstel.html, in the issue page's
three-column cockpit frame with the same widths: facts and background on the
left (with "Bij dit voorstel" / "Alle" and fact linking), the proposal in the
middle (a quiet link to the issue's voting rules, "Voorstel aanpassen" for the
proposer only, top right, the reactions grouped per version), the proposals on
the right (ten shown, the current one highlighted, each with its reaction
count). On a phone the cockpit's tabs, with the proposal open first.
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


def _facts_col(page):
    return page.split('id="panel-facts"')[1].split('id="panel-proposal"')[0]


def _main(page):
    return page.split('id="panel-proposal"')[1].split('id="panel-proposals"')[0]


def _proposals_col(page):
    return page.split('id="panel-proposals"')[1].split('</main>')[0]


def _css(app):
    return _req(_client(app), 'GET', '/static/css/kit.css').get_data(as_text=True)


def test_side_columns_list_facts_and_proposals_with_totals(app, db, make_user):
    host = make_user()
    issue = _issue(db, host)
    for i in range(12):
        _fact(db, issue, host, f'Feit nummer {i}.')
    proposals = [ip.create(issue, host, f'Stelling {i}.') for i in range(11)]
    db.session.commit()
    current = proposals[3]
    page = _get(app, _detail(issue, current), host)
    facts, plist = _facts_col(page), _proposals_col(page)
    # The column heads carry the totals; both lists render in full (the script shows ten, then "Toon meer").
    assert 'Feiten en achtergrond <span class="gu-facts__count">12</span>' in facts
    assert 'Voorstellen <span class="gu-facts__count">11</span>' in plist
    all_facts = facts.split('data-fact-panel="all"')[1].split('id="feiten-koppelen"')[0]
    assert all_facts.count('Feit nummer') == 12 and plist.count('gu-pv__pitemtxt') == 11
    assert facts.count('data-rail-more') == 1 and plist.count('data-rail-more') == 1 and 'Toon meer' in plist
    # The current proposal is highlighted; each item has its status, version and reaction count.
    current_item = plist.split('is-current')[1].split('</a>')[0]
    assert 'Stelling 3.' in current_item and 'aria-current="page"' in current_item and 'v1' in current_item
    assert 'Verzamelt steun' in current_item and '<span class="gu-rcount">0 reacties</span>' in current_item
    assert f'href="/vraagstuk/{issue.id}/voorstellen"' in plist and 'Alle voorstellen' in plist


def test_side_columns_without_show_more_when_ten_or_fewer(app, db, make_user):
    host = make_user()
    issue = _issue(db, host)
    proposal = ip.create(issue, host, 'Eén stelling.')
    db.session.commit()
    page = _get(app, _detail(issue, proposal), host)
    facts, plist = _facts_col(page), _proposals_col(page)
    assert 'data-rail-more' not in facts + plist and 'Nog geen feiten' in facts


def test_adjust_button_only_for_the_proposer(app, db, make_user):
    host, other = make_user(), make_user()
    issue = _issue(db, host)
    proposal = ip.create(issue, other, 'Stelling van een ander.')
    issue.phase = 'bijstellen'
    db.session.commit()
    mine = _get(app, _detail(issue, proposal), other)
    head = _main(mine).split('id="bijstellen"')[0]
    # The button on its own row at the top of the middle column, the meta below it.
    top = head.split('class="gu-pv__top"')[1].split('class="gu-pv__statusrow"')[0]
    assert '>Voorstel aanpassen</a>' in top and 'href="#bijstellen"' in top
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


def test_page_in_english_and_mobile_tabs(app, db, make_user):
    host = make_user()
    issue = _issue(db, host)
    proposal = ip.create(issue, host, 'Plant trees.')
    db.session.commit()
    page = _get(app, _detail(issue, proposal), host, 'en')
    for text in ('Back to the issue', 'Have your say', '<span aria-current="page">Proposal</span>',
                 'Facts and background', 'With this proposal', 'Voting rules for this issue', 'All proposals', 'Current version', 'Reactions to version 1',
                 '0 supporters', 'Write a reaction', '0 reactions'):
        assert text in page
    # Phone: the cockpit's tabs in the order of the columns, the proposal selected by default.
    tabs = page.split('class="gu-cockpit__tabs"')[1].split('</div>')[0]
    assert tabs.index('data-tab="facts"') < tabs.index('data-tab="proposal"') < tabs.index('data-tab="proposals"')
    assert 'data-tab="proposal" aria-selected="true"' in tabs and 'data-tab="facts" aria-selected="false"' in tabs
    assert "show(tabPanel ? tabPanel.dataset.panel : 'proposal')" in page
    css = _css(app)
    assert '.gu-cockpit--tabs [data-panel]:not(.is-shown) { display: none; }' in css


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
    rail = _facts_col(page)
    # The choice with both counts, "Bij dit voorstel" first and pressed.
    seg = rail.split('class="gu-pv__subseg"')[1].split('</div>')[0]
    assert 'Bij dit voorstel <span class="gu-pv__c">1</span>' in seg and 'Alle <span class="gu-pv__c">2</span>' in seg
    assert seg.index('data-fact-view="linked" aria-pressed="true"') < seg.index('data-fact-view="all" aria-pressed="false"')
    # Facts are their own column now: no Feiten/Voorstellen toggle any more.
    assert 'data-rail-tab' not in page and 'gu-pv__railtab' not in page
    mine = rail.split('data-fact-panel="linked"')[1].split('data-fact-panel="all"')[0]
    assert 'Gekoppeld feit.' in mine and 'Los feit.' not in mine
    everything = rail.split('data-fact-panel="all"')[1].split('id="feiten-koppelen"')[0]
    assert 'Gekoppeld feit.' in everything and 'Los feit.' in everything
    # The middle column has neither the facts block nor a vote card any more.
    main = _main(page)
    assert 'Gekoppeld feit.' not in main and 'id="stemming"' not in main and 'Waarop dit voorstel' not in main


def test_link_and_unlink_from_the_facts_column(app, db, make_user):
    host = make_user()
    issue = _issue(db, host)
    fact = _fact(db, issue, host, 'Te koppelen feit.')
    proposal = ip.create(issue, host, 'Een stelling.')
    db.session.commit()
    rail = _facts_col(_get(app, _detail(issue, proposal), host))
    assert 'Feiten koppelen' in rail
    item = rail.split('data-fact-panel="all"')[1].split('Te koppelen feit.')[1].split('</li>')[0]
    action = f'/vraagstuk/{issue.id}/voorstel/{proposal.id}/koppelen'
    assert f'action="{action}"' in item and f'name="fact_ids" value="{fact.id}"' in item
    # That button links the fact; it then shows as linked, with "Ontkoppelen".
    r = _req(_client(app, host), 'POST', action, data={'fact_ids': str(fact.id)})
    assert r.status_code == 302 and r.headers['Location'].endswith('#basis')
    rail = _facts_col(_get(app, _detail(issue, proposal), host))
    item = rail.split('data-fact-panel="all"')[1].split('Te koppelen feit.')[1].split('</li>')[0]
    assert 'Gekoppeld' in item and 'Ontkoppelen' in item and 'name="fact_ids"' not in item
    assert 'Te koppelen feit.' in rail.split('data-fact-panel="linked"')[1].split('data-fact-panel="all"')[0]
    # Without the right to link: no link or unlink controls.
    anon = _facts_col(_get(app, _detail(issue, proposal)))
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
    meta = page.split('class="gu-pv__statusrow"')[1].split('id="bijstellen"')[0]
    assert meta.index('steunbetuigingen') < meta.index('class="gu-pv__ruleslink"')
    assert f'class="gu-pv__ruleslink" href="/vraagstuk/{issue.id}#stemregels">Stemregels bij dit vraagstuk</a>' in meta
    # The separator travels with the support count, so a line break never strands it.
    assert '<span class="gu-pv__metaitem"><span class="gu-pv__sep" aria-hidden="true">&middot;</span>' in meta
    item = meta.split('gu-pv__metaitem')[1].split('</span></span>')[0]
    assert 'steunbetuigingen' in item
    en = _get(app, _detail(issue, proposal), host, 'en')
    assert '>Voting rules for this issue</a>' in en
    css = _css(app)
    rule = lambda sel: css.split(sel + ' {')[1].split('}')[0]  # noqa: E731
    # Support in the normal grey meta colour, not green.
    assert 'color: var(--gu-fg-2)' in rule('.gu-pv__steun') and 'oceania' not in rule('.gu-pv__steun')
    # The button sits top right on its own row; the meta wraps; the button never shrinks.
    assert 'justify-content: flex-end' in rule('.gu-pv__top')
    assert 'flex-wrap: wrap' in rule('.gu-pv__statusrow')
    assert 'flex: 0 0 auto' in rule('.gu-pv__revisebtn')
    assert 'white-space: nowrap' in rule('.gu-pv__metaitem')
    assert '.gu-pv__raildet' not in css and '.gu-pv__layout' not in css and '.gu-pv__rail ' not in css


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


def test_same_three_column_frame_and_widths_as_the_issue_page(app, db, make_user):
    host = make_user()
    issue = _issue(db, host)
    proposal = ip.create(issue, host, 'Een stelling.')
    db.session.commit()
    page = _get(app, _detail(issue, proposal), host)
    show = _get(app, f'/vraagstuk/{issue.id}', host)
    # Both pages use the cockpit container and grid (one shared CSS rule, no own widths).
    for html in (page, show):
        assert 'class="gu-cockpit__inner"' in html and 'class="gu-cockpit__grid"' in html
    # Left facts, middle the proposal, right the proposals, in that order.
    assert page.index('id="panel-facts"') < page.index('id="panel-proposal"') < page.index('id="panel-proposals"')
    assert 'gu-cockpit__panel gu-cockpit__facts' in page and 'gu-cockpit__panel gu-cockpit__main' in page
    assert 'gu-cockpit__panel gu-cockpit__proposals' in page
    css = _css(app)
    assert '.gu-cockpit__inner { max-width: 1280px;' in css
    wide = css.split('@media (min-width: 1024px) {\n  .gu-cockpit__grid {')[1].split('}')[0]
    assert 'grid-template-columns: 280px minmax(0, 1fr) 280px;' in wide
    assert '.gu-cockpit__grid { display: grid; grid-template-columns: minmax(0, 1fr); gap: 20px; }' in css
    assert '.gu-cockpit__discussion, .gu-cockpit__main { grid-area: discussion; }' in css


def test_statement_is_the_smaller_artboard_size(app, db, make_user):
    css = _css(app)
    rule = css.split('.gu-pv__statement {')[1].split('}')[0]
    assert 'font-size: 1.25rem' in rule and '1.875rem' not in rule
    # No larger size on a phone either.
    assert css.count('.gu-pv__statement {') == 1


def test_reaction_counts_on_both_pages(app, db, make_user):
    host, anna = make_user(), make_user()
    issue = _issue(db, host)
    busy = ip.create(issue, host, 'Druk besproken.')
    quiet = ip.create(issue, host, 'Stil voorstel.')
    issue.phase = 'bijstellen'
    db.session.commit()
    ip.revise(busy, 'Druk besproken, versie 2.', None)
    db.session.commit()
    for i, version in enumerate((1, 2, 2)):  # the total spans all versions
        db.session.add(Post(thread_id=issue.thread_id, user_id=anna.id, author_name='', author_username=anna.username,
                            body=f'Reactie {i}.', source_lang='nl', issue_proposal_id=busy.id,
                            proposal_version=version, created_at=datetime.utcnow()))
    db.session.commit()
    _get(app, _detail(issue, busy), host)  # opened after the reactions: calm grey (the marker is tested below)
    plist = _proposals_col(_get(app, _detail(issue, quiet), host))
    busy_item = plist.split('Druk besproken, versie 2.')[1].split('</a>')[0]
    quiet_item = plist.split('Stil voorstel.')[1].split('</a>')[0]
    assert '<span class="gu-rcount">3 reacties</span>' in busy_item and 'v2' in busy_item
    assert '<span class="gu-rcount">0 reacties</span>' in quiet_item
    # The issue page's Voorstellen column shows the same counts.
    show = _get(app, f'/vraagstuk/{issue.id}', host)
    col = show.split('id="panel-proposals"')[1].split('</section>')[0]
    assert '<span class="gu-rcount">3 reacties</span>' in col.split('Druk besproken, versie 2.')[1].split('</li>')[0]
    assert '<span class="gu-rcount">0 reacties</span>' in col.split('Stil voorstel.')[1].split('</li>')[0]
    en = _get(app, f'/vraagstuk/{issue.id}', host, 'en')
    assert '<span class="gu-rcount">3 reactions</span>' in en
    # Calm grey for now (the "new discussion" marker is a later step).
    rule = _css(app).split('.gu-rcount {')[1].split('}')[0]
    assert 'var(--gu-fg-3)' in rule


def _count_in(html, statement):
    """The reaction-count span of the proposal with this statement in a proposals column."""
    return html.split(statement)[1].split('class="gu-rcount', 1)[1].split('</span></')[0]


def test_new_discussion_marker_like_unread_email(app, db, make_user):
    from app.models import IssueProposalRead
    host, anna, reader = make_user(), make_user(), make_user()
    issue = _issue(db, host)
    proposal = ip.create(issue, host, 'Gemarkeerd voorstel.')
    other = ip.create(issue, host, 'Ander voorstel.')
    db.session.commit()
    db.session.add(Post(thread_id=issue.thread_id, user_id=anna.id, author_name='', author_username=anna.username,
                        body='Eerste reactie.', source_lang='nl', issue_proposal_id=proposal.id,
                        proposal_version=1, created_at=datetime(2026, 1, 1)))
    db.session.commit()
    show = lambda viewer, lang='nl': _get(app, f'/vraagstuk/{issue.id}', viewer, lang).split('id="panel-proposals"')[1].split('</section>')[0]  # noqa: E731

    # Never opened, with a reaction: active (globe-deep, bold, the dot), on both pages.
    count = _count_in(show(reader), 'Gemarkeerd voorstel.')
    assert count.startswith(' gu-rcount--new"') and 'gu-rcount__dot' in count and '1 reactie' in count
    rail = _proposals_col(_get(app, _detail(issue, other), reader))
    assert 'gu-rcount--new' in _count_in(rail, 'Gemarkeerd voorstel.')
    assert 'gu-rcount--new' not in _count_in(rail, 'Ander voorstel.')  # no reactions: never new
    en = _count_in(show(reader, 'en'), 'Gemarkeerd voorstel.')
    assert 'gu-rcount--new' in en and '1 reaction' in en
    assert IssueProposalRead.query.filter_by(user_id=reader.id, issue_proposal_id=proposal.id).count() == 0

    # Opening the proposal: grey, also in its own right rail on that very request.
    page = _get(app, _detail(issue, proposal), reader)
    own = _count_in(_proposals_col(page), 'Gemarkeerd voorstel.')
    assert own.startswith('">') and '1 reactie' in own and 'gu-rcount__dot' not in own
    assert 'gu-rcount--new' not in _count_in(show(reader), 'Gemarkeerd voorstel.')
    row = IssueProposalRead.query.filter_by(user_id=reader.id, issue_proposal_id=proposal.id).one()
    first_seen = row.last_seen_at
    _get(app, _detail(issue, proposal), reader)  # opening again updates the one row
    assert IssueProposalRead.query.filter_by(user_id=reader.id).count() == 2  # this one and "Ander voorstel."
    db.session.refresh(row)
    assert row.last_seen_at >= first_seen

    # A reaction placed after that: active again.
    _req(_client(app, anna), 'POST', f'/forum/{issue.thread_id}',
         data={'body': 'Nieuwe reactie.', 'issue_proposal_id': proposal.id, 'return_proposal': '1'})
    count = _count_in(show(reader), 'Gemarkeerd voorstel.')
    assert 'gu-rcount--new' in count and '2 reacties' in count

    # Anonymous: always grey, and nothing is written.
    reads = IssueProposalRead.query.count()
    assert 'gu-rcount--new' not in show(None)
    assert 'gu-rcount--new' not in _get(app, _detail(issue, other), None)
    assert IssueProposalRead.query.count() == reads

    # The active style follows the artboard (.rnew / .nd) with the repo token.
    css = _css(app)
    new_rule = css.split('.gu-rcount--new {')[1].split('}')[0]
    dot_rule = css.split('.gu-rcount__dot {')[1].split('}')[0]
    assert 'color: var(--gu-globe-deep)' in new_rule and 'font-weight: 600' in new_rule
    assert 'width: 7px; height: 7px' in dot_rule and 'background: var(--gu-globe-deep)' in dot_rule


def test_reads_go_with_a_deleted_proposal(app, db, make_user):
    from app import issues as issues_mod
    from app.models import IssueProposalRead
    host = make_user()
    issue = _issue(db, host)
    proposal = ip.create(issue, host, 'Weg ermee.')
    db.session.commit()
    _get(app, _detail(issue, proposal), host)
    assert IssueProposalRead.query.filter_by(issue_proposal_id=proposal.id).count() == 1
    issues_mod.purge_proposal(proposal)
    assert IssueProposalRead.query.count() == 0
