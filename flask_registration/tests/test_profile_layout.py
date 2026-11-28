"""
The new layout of /profile (docs/design/profiel.html): Account (with "How do
you appear in the forum?"), Optional (only with PARTICIPANT_PROFILE on),
Settings, Change password, Danger zone. The status in one row with a link to
the FAQ question about the right to vote, which explains the steps of the
ladder with the thresholds of status.get_thresholds(). And one word for the
name you take part under: "username", nowhere "pseudonym", in all five
languages.

Shared conftest app.
"""
import re

import pytest

from app import status
from tests.test_delete_issue import _clean_session, _cleanup  # noqa: F401  (autouse fixture)
from tests.test_issue_polish import _client, _req, make_user  # noqa: F401  (fixture)
from tests.test_no_dutch_interface import ANONYMOUS_PAGES, _content, _interface_texts, world  # noqa: F401  (fixture)


def _page(app, user, lang='nl', path='/profile'):
    resp = _req(_client(app, user, lang), 'GET', path)
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


@pytest.fixture
def switch_on(app, monkeypatch):
    monkeypatch.setitem(app.config, 'PARTICIPANT_PROFILE', True)


# ── The order of the sections ────────────────────────────────────────────

def test_sections_in_order_with_participant_profile(app, make_user, switch_on):
    page = _page(app, make_user())
    marks = ['>Account</p>', 'id="guStatus"', 'id="guNameDisplay"', '>Optioneel</p>', 'id="over-mij"',
             '>Instellingen</p>', '>Taal</h2>', '>Automatisch vertalen</h2>', '>Samenvatting per e-mail</h2>',
             '>Wachtwoord wijzigen</p>', 'Gevarenzone']
    found = [page.index(m) for m in marks]
    assert found == sorted(found)


def test_without_participant_profile_no_optional_block(app, make_user):
    page = _page(app, make_user())
    assert 'Optioneel' not in page and 'id="over-mij"' not in page
    assert page.index('id="guNameDisplay"') < page.index('>Instellingen</p>') < page.index('>Wachtwoord wijzigen</p>')


@pytest.mark.parametrize('lang,gone', [
    ('nl', ('Niet verplicht', 'Verdiend', 'Taal &amp; vertalingen', 'Ingevuld op', 'Niet ingevuld')),
    ('en', ('Not required', 'Earned', 'Language &amp; Translations', 'Filled in on', 'Not provided')),
])
def test_old_rows_are_gone(app, make_user, switch_on, lang, gone):
    page = _page(app, make_user(), lang)
    assert 'gu-statusladder' not in page
    for text in gone:
        assert text not in page, text


def test_how_does_this_work_links_to_the_voting_right_question(app, make_user):
    page = _page(app, make_user())
    row = page.split('id="guStatus"')[1].split('</div>')[0]
    assert '<a href="/faq#faq-3-5" id="guStatusHow">Hoe werkt dit?</a>' in row
    faq = _page(app, None, path='/faq')
    item = faq.split('id="faq-3-5"')[1].split('</details>')[0]
    assert 'Hoe verdien ik stemrecht?' in item
    # A few lines of JavaScript open the item the hash points to.
    assert 'item.open = true' in faq and "addEventListener('hashchange'" in faq


def test_granted_status_above_earned_keeps_the_contributions_line(app, make_user):
    page = _page(app, make_user('voter'))
    row = page.split('id="guStatus"')[1].split('id="guNameDisplay"')[0]
    assert 'Stemmer' in row and '· stemrecht' in row and '0 kwaliteitsbijdragen in 0 onderwerpen' in row


# ── The FAQ answer with the steps of the ladder ──────────────────────────

def test_voting_right_answer_uses_the_thresholds(app, monkeypatch):
    monkeypatch.setattr(status, 'get_thresholds', lambda: {
        'voter_min': 7, 'moderator_min': 21, 'moderator_min_topics': 4, 'senior_min': 222})
    for lang, words in (('nl', ('lezer', 'bijdrager', 'stemmer', 'moderator', 'senior')),
                        ('en', ('reader', 'contributor', 'voter', 'moderator', 'senior'))):
        faq = _page(app, None, lang, '/faq')
        answer = faq.split('id="faq-3-5"')[1].split('</details>')[0]
        for number in ('7', '21', '4', '222'):
            assert re.search(rf'\b{number}\b', answer), (lang, number)
        for word in words:
            assert word in answer, (lang, word)
        assert '%(' not in answer


# ── One word: username ───────────────────────────────────────────────────

PSEUDONYM = re.compile(r'pseudon|seud[oó]nim', re.I)


@pytest.mark.parametrize('lang', ['nl', 'en', 'de', 'es', 'fr'])
def test_no_pseudonym_in_the_interface(app, db, world, lang):
    admin, pages = world
    content = _content(db)
    found = {}
    for user, paths in ((admin, pages), (None, ANONYMOUS_PAGES)):
        client = _client(app, user, lang)
        for path in paths:
            resp = _req(client, 'GET', path)
            assert resp.status_code == 200, (lang, path, resp.status_code)
            for text in _interface_texts(resp.get_data(as_text=True), content):
                if PSEUDONYM.search(text):
                    found.setdefault(path, []).append(text)
    assert found == {}, found


@pytest.mark.parametrize('lang', ['nl', 'en', 'de', 'es', 'fr'])
def test_no_pseudonym_in_the_catalog(lang):
    from babel.messages.pofile import read_po
    import os
    path = os.path.join(os.path.dirname(__file__), '..', 'app', 'translations', lang, 'LC_MESSAGES', 'messages.po')
    with open(path, 'rb') as f:
        catalog = read_po(f)
    hits = [m.id for m in catalog if m.id and PSEUDONYM.search(
        ' '.join(m.string) if isinstance(m.string, tuple) else (m.string or ''))]
    assert hits == []


# ── The progress line to the right to vote ───────────────────────────────

def test_progress_line_above_the_bar_without_the_right_to_vote(app, make_user):
    row = _page(app, make_user()).split('id="guStatus"')[1].split('id="guNameDisplay"')[0]
    line = '<span class="gu-profile__status-meta gu-profile__progress-note" id="guVoteProgress">' \
           'Op weg naar stemrecht: 0 van 3 kwaliteitsbijdragen.</span>'
    assert line in row
    assert row.index(line) < row.index('role="progressbar"')
    assert 'aria-describedby="guVoteProgress"' in row


def test_no_progress_line_with_the_right_to_vote(app, make_user):
    row = _page(app, make_user('voter')).split('id="guStatus"')[1].split('id="guNameDisplay"')[0]
    assert 'Op weg naar stemrecht' not in row and 'progressbar' not in row and 'guVoteProgress' not in row


# ── "Who votes?" instead of "About me" ───────────────────────────────────

ABOUT_ME = re.compile(r'About me|Over mij|Über mich|Sobre mí|À propos de moi', re.I)
WHO_VOTES = {'nl': 'Wie stemt er mee?', 'en': 'Who votes?', 'de': 'Wer stimmt mit ab?', 'es': '¿Quién vota?',
             'fr': 'Qui vote ?'}


@pytest.mark.parametrize('lang', ['nl', 'en', 'de', 'es', 'fr'])
def test_card_after_the_first_vote_and_privacy_refer_to_who_votes(app, db, make_user, monkeypatch, lang):
    import html
    import uuid
    from app import issue_proposals as ip, issues
    monkeypatch.setitem(app.config, 'PARTICIPANT_PROFILE', True)
    author, backer, voter = make_user(), make_user('voter'), make_user('voter')
    issue = issues.create({'title': f'Q{uuid.uuid4().hex[:6]}', 'intro': f'I{uuid.uuid4().hex[:6]}',
                           'kind': 'online', 'support_threshold': 1}, author, lang='nl')
    db.session.flush()
    proposal = ip.create(issue, author, f'S{uuid.uuid4().hex[:6]}', f'E{uuid.uuid4().hex[:6]}', lang='nl')
    db.session.flush()
    ip.support(proposal, backer)
    issue.phase = 'stemmen'
    db.session.commit()
    path = f'/vraagstuk/{issue.id}/voorstel/{proposal.id}'
    who = WHO_VOTES[lang]
    try:
        client = _client(app, voter, lang)
        _req(client, 'POST', path + '/stemmen', data={'choice': 'voor'})
        page = _req(client, 'GET', path).get_data(as_text=True)
        start = page.index('class="gu-ppcard"')
        card = html.unescape(page[start:page.index('</form>', start)])
        assert not ABOUT_ME.search(card)
        assert f'"<a href="/profile#over-mij">{who}</a>"' in card
        _req(client, 'POST', '/profiel/over-mij', data={'action': 'save', 'next': path + '#stemming', 'gender': 'f'})
        saved = _req(client, 'GET', path).get_data(as_text=True)
        start = saved.index('gu-ppcard__saved')
        line = html.unescape(saved[start:saved.index('</p>', start)])
        assert not ABOUT_ME.search(line) and f'"{who}"' in line
    finally:
        _cleanup(issue.id)
    privacy = html.unescape(_page(app, None, lang, '/privacy'))
    assert not ABOUT_ME.search(privacy) and f'"{who}"' in privacy
