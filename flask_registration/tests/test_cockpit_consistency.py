"""Consistency of the issue page and the proposal page
(docs/design/voorstel.html, docs/design/voorstel-vergroot.html).

- Every column enlarges in the page on the proposal page (also the middle
  proposal block), with the same shared mechanism as the issue page
  (static/js/cockpit.js); no enlarge button links out to another page.
- The enlarged proposals: every proposal in rows, the filter and sort bar only
  above ip.FILTER_MIN (10), and filtering and sorting give the same result as
  /voorstellen. ?vergroot=voorstellen reopens it enlarged.
- The voting rules: their own card, collapsed by default, above the proposals,
  on both pages.
"""
import re

import pytest

from app import issue_proposals as ip
from app.models import IssueProposalSupport
from tests.test_delete_issue import _clean_session, _cleanup  # noqa: F401  (autouse fixture)
from tests.test_issue_polish import _client, _get, _issue, _req, make_user  # noqa: F401  (fixture)


def _pages(issue, proposal):
    return {'issue': f'/vraagstuk/{issue.id}', 'proposal': f'/vraagstuk/{issue.id}/voorstel/{proposal.id}'}


def _panel(page, panel_id):
    return page.split(f'id="{panel_id}"')[1].split('</section>')[0]


def _bigbox(page):
    """The enlarged proposals (the proposals column's big box)."""
    return _panel(page, 'panel-proposals').split('class="gu-cockpit__box gu-cockpit__bigbox"')[1]


@pytest.fixture
def small(db, make_user):
    host = make_user()
    issue = _issue(db, host, 'Klein vraagstuk')
    proposals = [ip.create(issue, host, f'Klein voorstel {i}.') for i in range(3)]
    db.session.commit()
    issue_id = issue.id
    yield issue, proposals, host
    _cleanup(issue_id)


def _many(db, host, n):
    """An issue with n proposals; some with enough support for the voting list."""
    issue = _issue(db, host, 'Groot vraagstuk')
    supporters = [make_user_direct(db) for _ in range(issue.support_threshold)]
    proposals = []
    for i in range(n):
        p = ip.create(issue, host, f'Voorstel nummer {i:02d}.')
        proposals.append(p)
    db.session.commit()
    for i, p in enumerate(proposals):
        for s in supporters[:(i % (issue.support_threshold + 1))]:  # 0, 1, ..., threshold supporters
            db.session.add(IssueProposalSupport(proposal_id=p.id, user_id=s.id))
    db.session.commit()
    return issue, proposals


def make_user_direct(db):
    import uuid
    from app import bcrypt
    from app.models import User
    n = f'u_{uuid.uuid4().hex[:8]}'
    u = User(username=n, email=f'{n}@example.com', verified=True,
             password_hash=bcrypt.generate_password_hash('Sterk1!ww').decode('utf-8'))
    u.set_role('contributor')
    u.accept_rules()
    db.session.add(u)
    db.session.commit()
    return u


# ── 1. Every column enlarges in the page ────────────────────────────────────

def test_proposal_page_enlarges_all_three_columns_in_the_page(app, db, small):
    issue, proposals, host = small
    page = _get(app, _pages(issue, proposals[0])['proposal'], host)
    for panel, name in (('panel-facts', 'facts'), ('panel-proposal', 'proposal'), ('panel-proposals', 'proposals')):
        head = _panel(page, panel).split('class="gu-cockpit__box')[0]
        assert f'data-expand="{name}"' in head and 'data-collapse' in head, panel
        # The enlarge control is a button, not a link to another page.
        assert 'gu-iconbtn" href=' not in head and '/feiten"' not in head and '/voorstellen"' not in head, panel
    # The middle block has its own head with the title and the enlarge button top right (artboard).
    middle = _panel(page, 'panel-proposal')
    assert middle.index('class="gu-cockpit__panelhead"') < middle.index('class="gu-cockpit__box gu-pv__body"')
    assert '<h2 class="gu-cockpit__title" id="proposalTitle">Voorstel</h2>' in middle
    # The phase bar sits above the grid, outside the panels, so it stays while a column is enlarged.
    assert page.index('class="gu-pv__phase"') < page.index('class="gu-cockpit__grid"')


def test_both_pages_use_the_one_shared_cockpit_script(app, db, small):
    issue, proposals, host = small
    pages = _pages(issue, proposals[0])
    for key, default in (('issue', 'facts'), ('proposal', 'proposal')):
        page = _get(app, pages[key], host)
        assert '/static/js/cockpit.js' in page and f"GU.cockpit(root, {{defaultTab: '{default}'}})" in page, key
        # No second copy of the tabs or the enlarge logic in the page itself.
        assert 'function expand(' not in page and 'function collapse(' not in page, key
        assert "querySelectorAll('.gu-cockpit__tab')" not in page, key
    js = _req(_client(app), 'GET', '/static/js/cockpit.js').get_data(as_text=True)
    for piece in ('GU.cockpit = function', 'function expand(', 'function collapse(', "e.key === 'Escape'",
                  'gu-cockpit--expanded', '[data-disclosure]', 'root.dataset.expanded'):
        assert piece in js, piece


# ── 2. The enlarged proposals ───────────────────────────────────────────────

def test_enlarged_proposals_rows_without_filter_at_ten_or_fewer(app, db, small):
    issue, proposals, host = small
    for key, path in _pages(issue, proposals[0]).items():
        big = _bigbox(_get(app, path, host))
        assert big.count('class="gu-bigp__row') == 3, key
        assert 'gu-bigp__bar' not in big and 'name="sorteer"' not in big, key
        # Everything is already there: no "show all" and no link to a separate proposals page.
        assert 'data-more' not in big and 'Toon meer' not in big and '/voorstellen"' not in big, key
    # The status, the version and the reaction count per row (artboard).
    row = _bigbox(_get(app, _pages(issue, proposals[0])['issue'], host)).split('class="gu-bigp__row')[1]
    assert 'gu-ip-status' in row and '>v1</span>' in row and '0 reacties' in row


def test_enlarged_proposals_filter_bar_above_ten_and_same_result_as_voorstellen(app, db):
    host = make_user_direct(db)
    issue, proposals = _many(db, host, ip.FILTER_MIN + 1)
    try:
        assert ip.FILTER_MIN == 10
        statements = lambda html, cls: re.findall(rf'class="{cls}"[^>]*>([^<]+)<', html)  # noqa: E731
        for key, path in _pages(issue, proposals[0]).items():
            big = _bigbox(_get(app, path, host))
            assert 'gu-bigp__bar' in big and 'name="sorteer"' in big, key
            counts = re.findall(r'<span class="gu-bigp__c">(\d+)</span>', big)
            on_list = sum(1 for p, c in ip.listing(issue) if ip.on_list(p, c))
            assert counts == [str(len(proposals)), str(on_list), str(len(proposals) - on_list)], key
            # Each filter and sort: the same proposals in the same order as /voorstellen.
            for which in ('alle', 'stemlijst', 'steun'):
                for order in ('stemlijst', 'steun', 'nieuwste'):
                    q = f'?filter={which}&sorteer={order}'
                    enlarged = statements(_bigbox(_get(app, path + q + '&vergroot=voorstellen', host)), 'gu-bigp__txt')
                    listed = statements(_get(app, f'/vraagstuk/{issue.id}/voorstellen{q}', host), 'gu-iplist__statement')
                    assert enlarged == listed and enlarged, (key, which, order)
            # The filter links and the sort form reload this page enlarged.
            page = _get(app, path + '?filter=stemlijst&vergroot=voorstellen', host)
            assert 'data-expanded="proposals"' in page, key
            assert re.search(r'href="[^"]*filter=steun[^"]*vergroot=voorstellen[^"]*#panel-proposals"', _bigbox(page)), key
            assert '<input type="hidden" name="vergroot" value="voorstellen">' in _bigbox(page), key
            # Without the parameter the page is not enlarged.
            assert 'data-expanded=' not in _get(app, path, host), key
    finally:
        _cleanup(issue.id)


def test_enlarged_defaults_to_voting_list_first_and_applies_without_a_button(app, db):
    host = make_user_direct(db)
    issue, proposals = _many(db, host, ip.FILTER_MIN + 1)
    try:
        statements = lambda html, cls: re.findall(rf'class="{cls}"[^>]*>([^<]+)<', html)  # noqa: E731
        compact = [p.statement for p, _c in ip.listing(issue, order='stemlijst')]
        for key, path in _pages(issue, proposals[0]).items():
            # No sort chosen: the same order as the compact column, "Voting list first" selected.
            for q in ('', '?vergroot=voorstellen'):
                big = _bigbox(_get(app, path + q, host))
                assert statements(big, 'gu-bigp__txt') == compact, (key, q)
                assert '<option value="stemlijst" selected>' in big, (key, q)
            # The filter links carry that order on.
            assert re.search(r'href="[^"]*filter=steun[^"]*sorteer=stemlijst', big) or \
                re.search(r'href="[^"]*sorteer=stemlijst[^"]*filter=steun', big), key
            # Filter and sort apply at once: links and a select that submits on change, no button.
            for lang in ('nl', 'en'):
                big = _bigbox(_get(app, path, host, lang))
                assert 'data-autosubmit' in big and '<button' not in big, (key, lang)
                assert 'Toepassen' not in big and 'Apply' not in big, (key, lang)
        # /voorstellen itself keeps its own default.
        listed = statements(_get(app, f'/vraagstuk/{issue.id}/voorstellen', host), 'gu-iplist__statement')
        assert listed == [p.statement for p, _c in ip.listing(issue, order=ip.DEFAULT_ORDER)]
    finally:
        _cleanup(issue.id)


def test_enlarged_view_hides_the_compact_list_and_the_side_cards(app):
    css = _req(_client(app), 'GET', '/static/css/kit.css').get_data(as_text=True)
    assert '.gu-cockpit__bigbox { display: none; }' in css
    assert '.gu-cockpit--expanded .is-expanded > .gu-cockpit__compact { display: none; }' in css
    assert '.gu-cockpit--expanded .gu-rules, .gu-cockpit--expanded .gu-cockpit__side { display: none !important; }' in css


# ── 3. The voting rules card ────────────────────────────────────────────────

def test_rules_card_collapsed_above_the_proposals_on_both_pages(app, db, small):
    issue, proposals, host = small
    for key, path in _pages(issue, proposals[0]).items():
        for lang, title in (('nl', 'Stemregels'), ('en', 'Voting rules')):
            page = _get(app, path, host, lang)
            assert page.count('id="stemregels"') == 1, key
            card = page.split('id="stemregels"')[1].split('</section>')[0]
            # A button header, collapsed by default (chevron down), the rules text in the hidden body.
            assert 'aria-expanded="false" aria-controls="rulesBody" data-disclosure' in card, key
            assert f'<span class="gu-cockpit__title">{title}</span>' in card and 'points="6 9 12 15 18 9"' in card
            body = card.split('id="rulesBody"')[1]
            assert body.split('>')[0].rstrip().endswith('hidden'), key
            rules = 'Alleen over voorstellen met genoeg steun wordt gestemd.' if lang == 'nl' else 'Only proposals with enough support are voted on.'
            assert rules in body, key
            # Above the proposals in the right column.
            assert page.index('id="stemregels"') < page.index('id="panel-proposals"'), key
    issue_page = _get(app, f'/vraagstuk/{issue.id}', host)
    assert 'gu-cockpit__rules"' not in issue_page  # the old card at the bottom of the right column is gone
    css = _req(_client(app), 'GET', '/static/css/kit.css').get_data(as_text=True)
    assert '.gu-rules__head[aria-expanded="true"] .gu-rules__chev { transform: rotate(180deg); }' in css
    # The card drops the old rules list's margin (top in line with the columns), and sits 6px above the proposals.
    assert '.gu-cockpit__panel.gu-rules { margin: 0; display: block; }' in css
    wide = css.split('@media (min-width: 1024px) {\n  .gu-cockpit__grid {')[1].split('\n}\n')[0]
    assert '.gu-cockpit__panel.gu-rules { margin-bottom: -14px; }' in wide
    assert '.gu-rules { list-style: none; margin: 24px 0;' in css  # the rules list elsewhere is untouched
    assert '"facts discussion rules" "facts discussion proposals" "facts discussion rights"' in css
