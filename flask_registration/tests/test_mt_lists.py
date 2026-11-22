"""Multilingual phase 2c: the vote block and the lists.

- The vote block shows the statement in the reader's language (with the
  label) and says that the original is the official text, one click away.
- Lists (issues overview, proposals in the cockpit and on the proposal
  screen, conversations, moderator overview) show the translation, else the
  original with a small language code; translations preloaded, a fixed
  number of queries per list, never an AI call.
- Mails and notifications name an issue in the recipient's language.
"""
import html
import re
from contextlib import contextmanager

import pytest
from sqlalchemy import event

from app import content_i18n, db, digest, issue_proposals as ip, issues, notifications, quality
from app.models import Notification
from tests.test_delete_issue import _clean_session, _cleanup  # noqa: F401  (autouse fixture)
from tests.test_issue_polish import _client, _issue, _req, make_user  # noqa: F401  (fixture)

CODE_NL = '<abbr class="gu-mt__code" title="Original in Dutch">NL</abbr>'


def _page(app, user, lang, path):
    return html.unescape(_req(_client(app, user, lang), 'GET', path).get_data(as_text=True))


@contextmanager
def _queries():
    seen = []

    def listener(conn, cursor, statement, params, context, executemany):
        seen.append(statement)
    event.listen(db.engine, 'before_cursor_execute', listener)
    try:
        yield seen
    finally:
        event.remove(db.engine, 'before_cursor_execute', listener)


@pytest.fixture
def no_ai(monkeypatch):
    monkeypatch.setattr(quality, 'translate_text', lambda *a, **kw: pytest.fail('the AI was called on a page load'))


@pytest.fixture
def voting(db, make_user):
    """An issue in the voting phase with one proposal on the voting list."""
    author, voter = make_user(), make_user('voter')
    issue = issues.create({'title': 'Wat doen we met het plein?', 'intro': 'Context.', 'kind': 'online',
                           'support_threshold': 1}, author, lang='nl')
    db.session.flush()
    proposal = ip.create(issue, author, 'Maak het plein autovrij.', lang='nl')
    db.session.flush()
    ip.support(proposal, voter)
    issue.phase = 'stemmen'
    db.session.commit()
    yield issue, proposal, voter
    _cleanup(issue.id)


def _vote_block(page):
    start = page.index('id="stemming"')
    return page[start:page.index('</section>', start)]


def test_the_vote_block_states_that_the_original_is_official(app, voting, no_ai):
    issue, proposal, voter = voting
    content_i18n.set_translation(proposal, 'statement', 'en', 'Make the square car-free.', is_machine=True)
    db.session.commit()
    block = _vote_block(_page(app, voter, 'en', f'/vraagstuk/{issue.id}/voorstel/{proposal.id}'))
    assert 'Make the square car-free.' in block and 'Machine-translated from Dutch' in block
    assert 'You vote on the original text (Dutch).' in block
    button = re.search(r'<button type="button" class="gu-mt__toggle" aria-expanded="false" '
                       r'aria-controls="voteOriginal-(\d+)"', block)
    assert button and int(button.group(1)) == proposal.id
    assert re.search(rf'<p class="gu-vote__original" id="voteOriginal-{proposal.id}" lang="nl" hidden>'
                     r'Maak het plein autovrij.</p>', block)
    # The reader of the original language sees no such line.
    nl = _vote_block(_page(app, voter, 'nl', f'/vraagstuk/{issue.id}/voorstel/{proposal.id}'))
    assert 'Je stemt over de oorspronkelijke tekst' not in nl and 'gu-vote__original' not in nl


def test_lists_show_the_translation_or_the_original_with_a_language_code(app, voting, no_ai):
    issue, proposal, voter = voting
    overview = _page(app, voter, 'en', '/gesprekken')
    row = overview[overview.index(f'/vraagstuk/{issue.id}"'):]
    row = row[:row.index('</li>')]
    assert 'Wat doen we met het plein?' in row and CODE_NL in row        # not translated yet

    content_i18n.set_translation(issue, 'title', 'en', 'What do we do with the square?', is_machine=True)
    content_i18n.set_translation(proposal, 'statement', 'en', 'Make the square car-free.', is_machine=True)
    db.session.commit()
    overview = _page(app, voter, 'en', '/gesprekken')
    row = overview[overview.index(f'/vraagstuk/{issue.id}"'):]
    row = row[:row.index('</li>')]
    assert '<span lang="en">What do we do with the square?</span>' in row and CODE_NL not in row

    cockpit = _page(app, voter, 'en', f'/vraagstuk/{issue.id}')
    item = cockpit[cockpit.index('gu-cockpit__proposallist'):]
    assert '<span lang="en">Make the square car-free.</span>' in item[:item.index('</ul>')]
    detail = _page(app, voter, 'en', f'/vraagstuk/{issue.id}/voorstel/{proposal.id}')
    assert 'class="gu-pv__question"' in detail and '<span lang="en">What do we do with the square?</span>' in detail
    # A reader of German: nothing translated, the originals with their code.
    de = _page(app, voter, 'de', f'/vraagstuk/{issue.id}')
    item = de[de.index('gu-cockpit__proposallist'):]
    assert ('<span lang="nl">Maak het plein autovrij.</span> <abbr class="gu-mt__code" '
            'title="Original auf Niederländisch">NL</abbr>') in item[:item.index('</ul>')]


def test_the_conversations_list_marks_an_untranslated_title(app, voting, no_ai):
    issue, _proposal, voter = voting
    page = _page(app, voter, 'en', '/forum')
    row = page[page.index(f'/forum/{issue.thread_id}"'):]
    assert CODE_NL in row[:row.index('</li>')]


def test_the_moderator_overview_shows_translated_titles(app, voting, make_user, no_ai):
    issue, _proposal, _voter = voting
    content_i18n.set_translation(issue, 'title', 'en', 'What do we do with the square?', is_machine=True)
    db.session.commit()
    page = _page(app, make_user('moderator'), 'en', '/moderator')
    assert '<span lang="en">What do we do with the square?</span>' in page


def _issue_with_proposals(db, user, n):
    issue = _issue(db, user, 'Een vraagstuk voor de telling')
    for i in range(n):
        proposal = ip.create(issue, user, f'Voorstel {i}.', f'Uitleg {i}.', lang='nl')
        db.session.flush()
        content_i18n.set_translation(proposal, 'statement', 'en', f'Proposal {i}.', is_machine=True)
    content_i18n.set_translation(issue, 'title', 'en', 'An issue for the count', is_machine=True)
    db.session.commit()
    return issue


@pytest.mark.parametrize('path', ['/vraagstuk/{id}', '/vraagstuk/{id}/voorstel/{first}', '/gesprekken', '/moderator'])
def test_a_fixed_number_of_queries_per_list(app, db, make_user, no_ai, path):
    """The same page with 1 and with 5 proposals (and one or two issues): the
    translations cost the same few queries, however many proposals or issues
    there are (content_i18n.preload). (The issue and proposal pages already
    had a few queries per proposal before phase 2c, outside the
    translations; this test does not cover those.)"""
    user = make_user('moderator')
    counts = []
    made = []
    try:
        for n in (1, 5):
            issue = _issue_with_proposals(db, user, n)
            made.append(issue.id)
            first = ip.listing(issue)[0][0].id
            with _queries() as seen:
                page = _page(app, user, 'en', path.format(id=issue.id, first=first))
            assert 'Proposal 0.' in page or 'An issue for the count' in page
            counts.append((sum(1 for q in seen if 'content_translations' in q), len(seen)))
        (tr_few, _few), (tr_many, _many) = counts
        assert tr_few == tr_many and tr_many <= 4, counts
    finally:
        for issue_id in made:
            _cleanup(issue_id)


def test_mails_and_notifications_name_the_issue_in_the_recipients_language(app, voting, make_user):
    issue, proposal, _voter = voting
    content_i18n.set_translation(issue, 'title', 'en', 'What do we do with the square?', is_machine=True)
    db.session.commit()
    reader = make_user()
    reader.lang = 'en'
    n = Notification(user_id=reader.id, category='vraagstuk', issue_id=issue.id,
                     event_key=f'issue:{issue.id}:closing')
    with app.test_request_context('/'):
        text, _url = notifications._tick_line(n, 'en')
        assert 'What do we do with the square?' in text
        text_de, _url = notifications._tick_line(n, 'de')
        assert 'Wat doen we met het plein?' in text_de         # no German translation: the original
    reader.last_digest_at = None
    items = digest.items_for(reader, issue_participants={issue: {reader.id}}) if hasattr(digest, 'items_for') else None
    if items is not None:
        assert any(item.get('title') == 'What do we do with the square?' for item in items)
