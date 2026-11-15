"""Page consolidation step 3 (docs/design/feit-acties.html): the actions of
/feiten on the fact card in the enlarged facts column, on the issue page and
the proposal page.

- Another's fact: "Dispute" (forum/_fact_signals.html, forum.fact_dispute),
  back here enlarged with the fact in view; once disputed it says so.
- Your own fact, while the facts phase is open: Edit (forum.fact_edit) and
  Delete (forum.fact_delete); an edit error reopens the form with the values.
- The compact column stays read-only. Reporting and the quality mark stay.
"""
import re
from urllib.parse import parse_qs, urlsplit

import pytest

from app import issue_proposals as ip
from app.models import FACT_KIND, FactDispute, Post, QualityAward
from tests.test_delete_issue import _clean_session, _cleanup  # noqa: F401  (autouse fixture)
from tests.test_issue_polish import _client, _get, _issue, _req, make_user  # noqa: F401  (fixture)


def _fact(db, issue, user, body, direction='voor'):
    fact = Post(thread_id=issue.thread_id, user_id=user.id, author_name='', author_username=user.username,
                body=body, source_lang='nl', kind=FACT_KIND, fact_direction=direction,
                fact_source='https://example.org/bron', fact_title=f'Kern: {body[:20]}', fact_type='feit')
    db.session.add(fact)
    db.session.commit()
    return fact


@pytest.fixture
def world(db, make_user):
    host, other = make_user(), make_user()
    issue = _issue(db, host, 'Feitacties in de pagina')
    proposal = ip.create(issue, host, 'Een voorstel.')
    theirs = _fact(db, issue, other, 'Een feit van een ander.')
    mine = _fact(db, issue, host, 'Mijn eigen feit.', 'tegen')
    db.session.add(QualityAward(post_id=theirs.id, status='afgewezen', source='ai', fact_mark=True))
    db.session.commit()
    yield {'issue': issue, 'proposal': proposal, 'host': host, 'other': other, 'theirs': theirs, 'mine': mine}
    _cleanup(issue.id)


def _pages(w):
    return {'issue': f'/vraagstuk/{w["issue"].id}', 'proposal': f'/vraagstuk/{w["issue"].id}/voorstel/{w["proposal"].id}'}


def _big_card(page, fact):
    big = page.split('id="panel-facts"')[1].split('</section>')[0].split('class="gu-cockpit__box gu-cockpit__bigbox"')[1]
    return big.split(f'id="feit-{fact.id}"')[1].split('<li class="gu-fact ')[0]


def _compact(page):
    facts = page.split('id="panel-facts"')[1].split('</section>')[0]
    return facts.split('class="gu-cockpit__box gu-cockpit__bigbox"')[0]


def _hidden(form_html, name):
    m = re.search(rf'<input type="hidden" name="{name}" value="([^"]*)">', form_html)
    return m.group(1) if m else None


def test_another_s_fact_can_be_disputed_in_the_enlarged_view(app, db, world):
    w = world
    for key, path in _pages(w).items():
        for lang, texts in (('nl', ('Betwisten', 'Wat klopt er niet aan dit feit?', 'Geen bron', 'Klopt niet', 'Niet relevant',
                                    'Misleidend', 'Naar een moderator sturen')),
                            ('en', ('Dispute', 'What is wrong with this fact?', 'No source', 'Not correct', 'Not relevant',
                                    'Misleading', 'Send to a moderator'))):
            card = _big_card(_get(app, path, w['host'], lang), w['theirs'])
            for text in texts:
                assert text in card, (key, lang, text)
            assert f'action="/forum/feit/{w["theirs"].id}/betwisten"' in card, key
            # Reporting and the quality mark are still on the card.
            assert 'gu-factreport' in card and 'gu-factmark' in card, (key, lang)
        card = _big_card(_get(app, path, w['host']), w['theirs'])
        nxt = _hidden(card.split('gu-fact__disputeform')[1], 'next')
        assert nxt == f'{path}?vergroot=feiten#feit-{w["theirs"].id}', key
    # Sending it: a dispute, back enlarged with the fact in view.
    path = _pages(w)['proposal']
    nxt = _hidden(_big_card(_get(app, path, w['host']), w['theirs']).split('gu-fact__disputeform')[1], 'next')
    resp = _req(_client(app, w['host']), 'POST', f'/forum/feit/{w["theirs"].id}/betwisten',
                data={'reason': 'klopt_niet', 'note': 'Het cijfer is oud.', 'next': nxt})
    assert resp.status_code == 302 and resp.headers['Location'].endswith(nxt)
    row = FactDispute.query.filter_by(fact_post_id=w['theirs'].id, user_id=w['host'].id).one()
    assert row.reason == 'klopt_niet' and row.note == 'Het cijfer is oud.' and row.status == 'open'
    for key, p in _pages(w).items():
        for lang, text in (('nl', 'Je hebt dit feit betwist'), ('en', 'You disputed this fact')):
            card = _big_card(_get(app, p + '?vergroot=feiten', w['host'], lang), w['theirs'])
            assert text in card and 'betwisten"' not in card, (key, lang)
    # Your own fact has no dispute.
    assert 'gu-fact__dispute' not in _big_card(_get(app, path, w['host']), w['mine'])


def test_your_own_fact_shows_edit_and_delete(app, db, world):
    w = world
    for key, path in _pages(w).items():
        for lang, texts in (('nl', ('Jouw feit', 'Bewerken', 'Verwijderen', 'Toelichting of conclusies')),
                            ('en', ('Your fact', 'Edit', 'Delete', 'Explanation or conclusions'))):
            card = _big_card(_get(app, path, w['host'], lang), w['mine'])
            for text in texts:
                assert text in card, (key, lang, text)
        card = _big_card(_get(app, path, w['host']), w['mine'])
        assert f'action="/forum/feit/{w["mine"].id}/bewerken"' in card and f'action="/forum/feit/{w["mine"].id}/verwijderen"' in card
        assert _hidden(card, 'in_page') == '1' and _hidden(card, 'return_to') == str(w['issue'].id)
        assert (_hidden(card, 'return_proposal') == str(w['proposal'].id)) == (key == 'proposal'), key
        # The edit form starts with the fact's values, closed.
        assert '<details class="gu-fact__edit">' in card and 'Mijn eigen feit.</textarea>' in card
        assert re.search(r'name="direction" value="tegen" checked', card)
        # Not on another's fact, and not for someone else.
        assert 'gu-fact__edit' not in _big_card(_get(app, path, w['host']), w['theirs'])
        assert 'gu-fact__edit' not in _big_card(_get(app, path, w['other']), w['mine'])
        assert 'gu-fact__edit' not in _big_card(_get(app, path), w['mine'])


def test_editing_and_deleting_your_own_fact_work(app, db, world):
    w = world
    mine_id = w['mine'].id
    base = {'title': 'Nieuwe kern', 'text': 'Mijn bewerkte feit.', 'direction': 'neutraal',
            'source': 'https://example.org/nieuw', 'type': 'video', 'return_to': str(w['issue'].id), 'in_page': '1'}
    c = _client(app, w['host'])
    resp = _req(c, 'POST', f'/forum/feit/{mine_id}/bewerken', data=dict(base, return_proposal=str(w['proposal'].id)))
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith(f'{_pages(w)["proposal"]}?vergroot=feiten#feit-{mine_id}')
    fact = db.session.get(Post, mine_id)
    db.session.refresh(fact)
    assert (fact.body, fact.fact_title, fact.fact_direction, fact.fact_type) == ('Mijn bewerkte feit.', 'Nieuwe kern', 'neutraal', 'video')
    assert 'Mijn bewerkte feit.' in _big_card(_get(app, _pages(w)['issue'] + '?vergroot=feiten', w['host']), fact)
    # Delete: gone, back on the issue page with the facts enlarged.
    resp = _req(c, 'POST', f'/forum/feit/{mine_id}/verwijderen', data={'return_to': str(w['issue'].id), 'in_page': '1'})
    assert resp.status_code == 302
    parts = urlsplit(resp.headers['Location'])
    assert parts.path == _pages(w)['issue'] and parse_qs(parts.query) == {'vergroot': ['feiten']}
    assert parts.fragment == 'panel-facts'
    assert db.session.get(Post, mine_id) is None
    assert 'Mijn bewerkte feit.' not in _get(app, _pages(w)['issue'] + '?vergroot=feiten', w['host'])


def test_an_edit_error_reopens_the_form_with_the_values(app, db, world):
    w = world
    mine = w['mine']
    for key, extra in (('issue', {}), ('proposal', {'return_proposal': str(w['proposal'].id)})):
        c = _client(app, w['host'])
        resp = _req(c, 'POST', f'/forum/feit/{mine.id}/bewerken',
                    data=dict({'title': 'Half bewerkt', 'text': 'Nog niet klaar.', 'direction': 'voor', 'source': '',
                               'type': 'podcast', 'return_to': str(w['issue'].id), 'in_page': '1'}, **extra))
        assert resp.status_code == 303, key
        assert resp.headers['Location'].endswith(f'{_pages(w)[key]}?vergroot=feiten#feit-{mine.id}'), key
        page = _req(c, 'GET', resp.headers['Location']).get_data(as_text=True)
        card = _big_card(page, mine)
        assert '<details class="gu-fact__edit" open>' in card, key
        assert 'value="Half bewerkt"' in card and 'Nog niet klaar.</textarea>' in card and 'Geef een bron.' in card, key
        assert re.search(r'value="podcast" checked', card), key
        # The add form stays closed, the error is not its own.
        assert re.search(r'id="nieuw-feit" data-fact-add-box hidden', page), key
        # Once: a reload shows the stored fact again.
        again = _big_card(_req(c, 'GET', resp.headers['Location']).get_data(as_text=True), mine)
        assert '<details class="gu-fact__edit">' in again and 'Mijn eigen feit.</textarea>' in again, key
    db.session.refresh(mine)
    assert mine.body == 'Mijn eigen feit.'


def test_no_own_actions_after_the_facts_phase(app, db, world):
    w = world
    w['issue'].phase = 'bijstellen'
    db.session.commit()
    for key, path in _pages(w).items():
        card = _big_card(_get(app, path, w['host']), w['mine'])
        assert 'gu-fact__edit' not in card and 'verwijderen"' not in card and 'Jouw feit' not in card, key


def test_the_compact_column_stays_read_only(app, db, world):
    w = world
    for key, path in _pages(w).items():
        for user in (w['host'], w['other']):
            compact = _compact(_get(app, path, user))
            assert 'Een feit van een ander.' in compact or key == 'proposal', key  # (the proposal rail lists linked facts)
            for text in ('gu-fact__actions', 'gu-fact__dispute', 'gu-fact__edit', 'gu-fact__delete', '/betwisten"',
                         '/bewerken"', '/verwijderen"', 'Jouw feit'):
                assert text not in compact, (key, text)
    # Reporting and the quality mark still show in the compact column of the issue page.
    compact = _compact(_get(app, _pages(w)['issue'], w['host']))
    card = compact.split('Een feit van een ander.')[0].rsplit('<li class="gu-fact ', 1)[1]
    assert 'gu-factreport' in card and 'gu-factmark' in card
