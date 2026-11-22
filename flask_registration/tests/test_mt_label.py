"""Multilingual phase 2b: the label for a text in a machine translation
(app/templates/_mt.html), on issues, proposals, versions, the follow-up and
posts. Facts: tests/test_mt_facts.py. The swapping itself runs in a real browser in
tests/test_mt_toggle.py.
"""
import html
import re

import pytest

from app import content_i18n, db, issue_lifecycle, issue_proposals as ip
from tests.test_delete_issue import _clean_session, _cleanup  # noqa: F401  (autouse fixture)
from tests.test_issue_polish import _client, _fact, _issue, _req, make_user  # noqa: F401  (fixture)

LABEL = {'en': 'Machine-translated from Dutch', 'nl': 'Machinaal vertaald uit het Engels',
         'de': 'Maschinell übersetzt, Originalsprache: Niederländisch',
         'es': 'Traducido automáticamente del neerlandés',
         'fr': 'Traduction automatique, langue d\'origine : néerlandais'}


def _page(app, user, lang, path):
    return _req(_client(app, user, lang), 'GET', path).get_data(as_text=True)


def _box(page, text):
    """The component (div.gu-mt) that holds `text`."""
    for match in re.finditer(r'<div class="gu-mt[^"]*" data-mt.*?class="gu-mt__note.*?</p>\s*</div>', page, re.S):
        box = html.unescape(match.group(0))
        if text in box:
            return box
    return None


@pytest.fixture
def world(db, make_user):
    user = make_user()
    issue = _issue(db, user, 'Hoe houden we het water schoon?')
    content_i18n.set_translation(issue, 'title', 'en', 'How do we keep the water clean?', is_machine=True)
    db.session.commit()
    yield user, issue
    _cleanup(issue.id)


def test_a_machine_translation_shows_the_label_and_carries_the_original(app, world):
    user, issue = world
    page = _page(app, user, 'en', f'/vraagstuk/{issue.id}')
    box = _box(page, 'How do we keep the water clean?')
    assert box is not None
    assert LABEL['en'] in box
    # The translation is shown, in the reader's language; the original is in the page, hidden, in Dutch.
    assert re.search(r'<h1 class="gu-forum__title" lang="en" data-mt-translation>\s*How do we keep', box)
    assert re.search(r'<h1 class="gu-forum__title" lang="nl" data-mt-original hidden>\s*Hoe houden we', box)
    # A real button, with its state.
    assert '<button type="button" class="gu-mt__toggle" aria-pressed="false">Show original</button>' in box
    assert 'data-btn-translation="Show translation"' in box and 'data-label-original="Original in Dutch"' in box


@pytest.mark.parametrize('lang', ['de', 'es', 'fr'])
def test_the_label_in_every_language(app, world, lang):
    user, issue = world
    content_i18n.set_translation(issue, 'title', lang, f'[{lang}] titel', is_machine=True)
    db.session.commit()
    page = _page(app, user, lang, f'/vraagstuk/{issue.id}')
    assert LABEL[lang] in _box(page, f'[{lang}] titel')


def test_no_label_for_a_handwritten_translation_or_the_readers_own_language(app, world):
    user, issue = world
    content_i18n.set_translation(issue, 'title', 'en', 'Keeping the water clean', is_machine=False)
    db.session.commit()
    page = _page(app, user, 'en', f'/vraagstuk/{issue.id}')
    assert 'Keeping the water clean' in page and _box(page, 'Keeping the water clean') is None
    assert 'Machine-translated' not in page.split('gu-cockpit__about')[0]
    nl = _page(app, user, 'nl', f'/vraagstuk/{issue.id}')
    assert 'Machinaal vertaald' not in nl and 'Vertaling volgt' not in nl


def test_proposal_versions_and_follow_up_carry_the_label(app, world):
    user, issue = world
    proposal = ip.create(issue, user, 'Plaats filters bij de inlaat.', 'Want het helpt.', lang='nl')
    db.session.commit()
    ip.revise(proposal, 'Plaats betere filters bij de inlaat.', 'Want het helpt meer.')
    db.session.commit()
    first, second = proposal.version_rows
    for obj, field, text in ((proposal, 'statement', 'Place better filters at the inlet.'),
                             (proposal, 'explanation', 'Because it helps more.'),
                             (first, 'statement', 'Place filters at the inlet.')):
        content_i18n.set_translation(obj, field, 'en', text, is_machine=True)
    db.session.commit()
    page = _page(app, user, 'en', f'/vraagstuk/{issue.id}/voorstel/{proposal.id}')
    for text in ('Place better filters at the inlet.', 'Because it helps more.', 'Place filters at the inlet.'):
        assert LABEL['en'] in _box(page, text), text

    issue.phase = 'vervolg'
    update = issue_lifecycle.add_followup(issue, 'We hebben het voorstel ingediend.', user, lang='nl')
    db.session.commit()
    content_i18n.set_translation(update, 'body', 'en', 'We submitted the proposal.', is_machine=True)
    db.session.commit()
    page = _page(app, user, 'en', f'/vraagstuk/{issue.id}')
    assert LABEL['en'] in _box(page, 'We submitted the proposal.')


def _post(db, issue, user, body):
    """An ordinary contribution in the issue's conversation (translated on request)."""
    from app.models import Post
    post = Post(thread_id=issue.thread_id, user_id=user.id, author_name='', author_username=user.username,
                body=body, source_lang='nl')
    db.session.add(post)
    db.session.commit()
    return post


def test_a_post_offers_translation_on_request_and_shows_the_label_once_translated(app, world, make_user):
    user, issue = world
    reader = make_user()
    reader.auto_translate = False
    post = _post(db, issue, user, 'Het water is schoner dan in 2010.')
    path = f'/forum/{issue.thread_id}'
    page = _page(app, reader, 'en', path)
    box = _box(page, 'Het water is schoner dan in 2010.')
    # Not translated yet: the original, "Original in Dutch" and a translate button (no banner, no loose button).
    assert 'Original in Dutch' in box and 'data-mt-url="/forum/translate/' in box
    assert '<button type="button" class="gu-mt__toggle">' in box and 'data-mt-auto' not in box
    assert 'class="gu-post__foreignbanner"' not in page and 'gu-post__translate-btn"' not in page
    # A visitor sees where it comes from, without a button.
    visitor = _page(app, None, 'en', path)
    assert 'gu-mt__toggle' not in _box(visitor, 'Het water is schoner dan in 2010.')

    content_i18n.set_translation(post, 'body', 'en', 'The water is cleaner than in 2010.', is_machine=True)
    db.session.commit()
    box = _box(_page(app, reader, 'en', path), 'The water is cleaner than in 2010.')
    # Translated before, "always translate" off: it starts on the original; the swap needs no request.
    assert 'data-mt-url' not in box and 'aria-pressed="true">Show translation</button>' in box
    reader.auto_translate = True
    db.session.commit()
    box = _box(_page(app, reader, 'en', path), 'The water is cleaner than in 2010.')
    assert LABEL['en'] in box and 'aria-pressed="false">Show original</button>' in box


def test_a_post_with_always_translate_fetches_at_once(app, world, make_user):
    user, issue = world
    reader = make_user()
    reader.auto_translate = True
    db.session.commit()
    _post(db, issue, user, 'Een bijdrage zonder vertaling.')
    box = _box(_page(app, reader, 'en', f'/forum/{issue.thread_id}'), 'Een bijdrage zonder vertaling.')
    assert 'data-mt-auto' in box and 'data-mt-url' in box


def test_a_fact_is_not_translated_on_request(app, world, make_user):
    """Facts are translated right away (tests/test_mt_facts.py): no translate
    button, "Translation pending" until the background job is done."""
    user, issue = world
    fact = _fact(db, issue, user, body='Een feit zonder vertaling.')
    page = _page(app, make_user(), 'en', f'/vraagstuk/{issue.id}')
    assert f'/forum/translate/{fact.id}"' not in page
    assert 'Translation pending' in _box(page, 'Een feit zonder vertaling.')
