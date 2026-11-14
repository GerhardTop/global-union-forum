"""The cockpit has no links to separate pages: enlarging a column is the full
view (issue page and proposal page).

- No visible link to /feiten or /voorstellen in the cockpit columns (the
  <noscript> fallbacks stay).
- The enlarged facts column, on the model of the enlarged proposals: every
  fact as a card, a filter bar and sort only above ip.FILTER_MIN facts,
  filtering and sorting the same as /feiten, applied at once (no button) and
  reloading with ?vergroot=feiten. ?vergroot=voorstellen and ?vergroot=feiten
  keep their own ?filter= and ?sorteer=.
"""
import re
from datetime import datetime, timedelta

import pytest

from app import issue_proposals as ip
from app.models import FACT_KIND, Post
from tests.test_delete_issue import _clean_session, _cleanup  # noqa: F401  (autouse fixture)
from tests.test_issue_polish import _get, _issue, make_user  # noqa: F401  (fixture)

DIRECTIONS = ('voor', 'tegen', 'neutraal')


def _add_facts(db, issue, user, n):
    start = datetime.utcnow() - timedelta(days=1)
    for i in range(n):
        db.session.add(Post(thread_id=issue.thread_id, user_id=user.id, author_name='', author_username=user.username,
                            body=f'Feit nummer {i:02d}.', source_lang='nl', kind=FACT_KIND,
                            fact_direction=DIRECTIONS[i % 3], created_at=start + timedelta(minutes=i),
                            # Some with a link as source, so "quality first" differs from "newest".
                            fact_source='https://example.org' if i % 4 == 0 else 'Een boek'))
    db.session.commit()


def _world(db, host, n):
    issue = _issue(db, host, 'Feitenvraagstuk')
    _add_facts(db, issue, host, n)
    proposal = ip.create(issue, host, 'Een voorstel.')
    db.session.commit()
    return issue, proposal


def _pages(issue, proposal):
    return {'issue': f'/vraagstuk/{issue.id}', 'proposal': f'/vraagstuk/{issue.id}/voorstel/{proposal.id}'}


def _panel(page, panel_id):
    return page.split(f'id="{panel_id}"')[1].split('</section>')[0]


def _bigfacts(page):
    return _panel(page, 'panel-facts').split('class="gu-cockpit__box gu-cockpit__bigbox"')[1]


def _texts(html):
    return re.findall(r'class="gu-fact__text"[^>]*>([^<]+)<', html)


@pytest.fixture
def small(db, make_user):
    host = make_user()
    issue, proposal = _world(db, host, 4)
    yield issue, proposal, host
    _cleanup(issue.id)


@pytest.fixture
def many(db, make_user):
    host = make_user()
    issue, proposal = _world(db, host, ip.FILTER_MIN + 2)
    yield issue, proposal, host
    _cleanup(issue.id)


# ── 1. No links to separate pages in the cockpit ───────────────────────────

def test_no_separate_page_links_in_the_cockpit(app, db, many):
    issue, proposal, host = many
    for key, path in _pages(issue, proposal).items():
        for lang in ('nl', 'en'):
            page = _get(app, path, host, lang)
            grid = page.split('class="gu-cockpit__grid"')[1].split('</main>')[0]
            visible = re.sub(r'<noscript>.*?</noscript>', '', grid, flags=re.S)
            for text in ('Facts page with filters and sorting', 'Feitenpagina met filters',
                         'All facts, research and media', 'Alle feiten, onderzoek en media',
                         'Alle voorstellen', 'All proposals'):
                assert text not in visible, (key, lang, text)
            assert 'gu-cockpit__fullpage' not in visible and 'gu-pv__railmore' not in visible, (key, lang)
            assert f'href="/vraagstuk/{issue.id}/feiten"' not in visible, (key, lang)
            assert f'href="/vraagstuk/{issue.id}/voorstellen"' not in visible, (key, lang)
    # The no-script fallback on the issue page stays.
    page = _get(app, f'/vraagstuk/{issue.id}', host)
    assert re.search(rf'<noscript><a class="gu-cockpit__more" href="/vraagstuk/{issue.id}/feiten">', page)


# ── 2. The enlarged facts column ───────────────────────────────────────────

def test_facts_column_has_a_compact_box_and_a_big_box(app, db, small):
    issue, proposal, host = small
    for key, path in _pages(issue, proposal).items():
        facts = _panel(_get(app, path, host), 'panel-facts')
        assert facts.index('gu-cockpit__box gu-cockpit__compact') < facts.index('gu-cockpit__box gu-cockpit__bigbox'), key
        big = _bigfacts(_get(app, path, host))
        # Every fact as a card, no filter at or below the threshold, no "show more", no link out.
        assert big.count('<li class="gu-fact ') == 4, key
        assert 'gu-bigp__bar' not in big and 'name="sorteer"' not in big, key
        assert 'data-more-list' not in big and not re.search(r'data-more[\s>]', big), key  # (per-fact more/less is fine)
        assert 'Toon meer' not in big and '/feiten"' not in big, key
    # On the proposal page the small "With this proposal / All" switch is in the compact box only.
    facts = _panel(_get(app, _pages(issue, proposal)['proposal'], host), 'panel-facts')
    compact = facts.split('gu-cockpit__box gu-cockpit__bigbox')[0]
    assert 'gu-pv__subseg' in compact and 'gu-pv__subseg' not in _bigfacts(_get(app, _pages(issue, proposal)['proposal'], host))


def test_enlarged_facts_filter_above_threshold_same_as_feiten(app, db, many):
    issue, proposal, host = many
    n = ip.FILTER_MIN + 2
    for key, path in _pages(issue, proposal).items():
        big = _bigfacts(_get(app, path, host))
        assert 'gu-bigp__bar' in big and 'name="sorteer"' in big, key
        counts = re.findall(r'<span class="gu-bigp__c">(\d+)</span>', big)
        assert counts == [str(n)] + [str(sum(1 for i in range(n) if DIRECTIONS[i % 3] == d)) for d in DIRECTIONS], key
        for lang, labels in (('nl', ('Alle', 'Pleit voor', 'Tegen', 'Neutraal')), ('en', ('All', 'Argues for', 'Against', 'Neutral'))):
            seg = _bigfacts(_get(app, path, host, lang)).split('gu-bigp__seg')[1].split('</nav>')[0]
            assert re.findall(r'>([^<>]+?) <span class="gu-bigp__c">', seg) == list(labels), (key, lang)
        # Each filter and sort: the same facts in the same order as /feiten.
        for which in ('alle',) + DIRECTIONS:
            for order in ('kwaliteit', 'nieuwste'):
                q = f'filter={which}&sorteer={order}&vergroot=feiten'
                enlarged = _texts(_bigfacts(_get(app, f'{path}?{q}', host)))
                richting = '' if which == 'alle' else f'richting={which}&'
                listed = _texts(_get(app, f'/vraagstuk/{issue.id}/feiten?{richting}sorteer={order}', host))
                assert enlarged == listed and enlarged, (key, which, order)
        assert _texts(_bigfacts(_get(app, path + '?sorteer=kwaliteit&vergroot=feiten', host))) != \
            _texts(_bigfacts(_get(app, path + '?sorteer=nieuwste&vergroot=feiten', host))), key


def test_enlarged_facts_apply_at_once_and_reopen_enlarged(app, db, many):
    issue, proposal, host = many
    for key, path in _pages(issue, proposal).items():
        page = _get(app, path + '?filter=tegen&vergroot=feiten', host)
        assert 'data-expanded="facts"' in page, key
        big = _bigfacts(page)
        # Filter links and the sort form reload this page enlarged; no button.
        assert re.search(r'href="[^"]*filter=voor[^"]*vergroot=feiten[^"]*#panel-facts"', big), key
        assert '<input type="hidden" name="vergroot" value="feiten">' in big, key
        assert '<input type="hidden" name="filter" value="tegen">' in big, key
        assert 'data-autosubmit' in big and '<button' not in big.split('gu-bigp__bar')[1].split('gu-bigf__list')[0], key
        for lang in ('nl', 'en'):
            bar = _bigfacts(_get(app, path, host, lang)).split('gu-bigp__bar')[1].split('</form>')[0]
            assert 'Toepassen' not in bar and 'Apply' not in bar, (key, lang)
        # Without the parameter the page is not enlarged.
        assert 'data-expanded=' not in _get(app, path, host), key


def test_facts_and_proposals_keep_their_own_parameters(app, db, many):
    issue, proposal, host = many
    for key, path in _pages(issue, proposal).items():
        # Enlarged facts with a facts filter: the proposals keep their defaults.
        page = _get(app, path + '?filter=tegen&sorteer=nieuwste&vergroot=feiten', host)
        props = _panel(page, 'panel-proposals').split('gu-cockpit__bigbox')[1]
        assert 'data-expanded="facts"' in page and 'data-expanded="proposals"' not in page, key
        assert 'Feit nummer' not in props, key
        facts_big = _bigfacts(page)
        assert '<option value="nieuwste" selected>' in facts_big, key
        assert all(f'Feit nummer {i:02d}.' in facts_big for i in range(ip.FILTER_MIN + 2) if DIRECTIONS[i % 3] == 'tegen')
        assert not any(f'Feit nummer {i:02d}.' in facts_big for i in range(ip.FILTER_MIN + 2) if DIRECTIONS[i % 3] != 'tegen')
        # Enlarged proposals with a proposals filter: the facts stay unfiltered and sorted on quality.
        page = _get(app, path + '?filter=stemlijst&sorteer=nieuwste&vergroot=voorstellen', host)
        assert 'data-expanded="proposals"' in page, key
        facts_big = _bigfacts(page)
        assert len(_texts(facts_big)) == ip.FILTER_MIN + 2 and '<option value="kwaliteit" selected>' in facts_big, key
