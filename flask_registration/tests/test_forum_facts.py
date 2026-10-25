"""
Fase E, stap 2: de feitenfase (feiten met richting en bron bij een voorstel).

Dekt: een feit toevoegen met richting en bron in de feitenfase; geweigerd
buiten de feitenfase en bij een gewoon gesprek; eigen feit bewerken en
verwijderen; de richtinglabels en de bron; geen auteur in beeld; buiten de
discussie (tellingen, reageren, eens/oneens); geen keurmerk en niet voor de
verdiende status; dezelfde handhaving als bij berichten (verplaatsen,
verbergen, achtergrondpagina); geen extra query's per feit.

Nep-Anthropic en fixtures uit test_forum_conduct.py; gedeelde conftest-app.
"""
import re
import uuid

import pytest
from sqlalchemy import event

from app import proposals
from app.models import ConductCase, Post, QualityAward, Thread
from tests.test_forum_conduct import (SPAM, VIOLATION, _client, _fresh_rate_limits, _get, _req,  # noqa: F401
                                      _verdict, fake_ai, make_thread, make_user)


@pytest.fixture
def proposal_thread(db, make_thread, make_user):
    def _make(phase='feiten'):
        host = make_user()
        t = make_thread(op_user=host, title=f'Stelling {uuid.uuid4().hex[:6]}')
        proposals.create(t, 'event', host)
        t.proposal.phase = phase
        db.session.commit()
        return t
    return _make


def _add(app, user, thread, text='Het minimumloon steeg in 2024 met 3 procent.', direction='voor',
         source='https://example.org/cbs-2024'):
    return _req(_client(app, user), 'POST', f'/forum/{thread.id}/feiten',
                data={'text': text, 'direction': direction, 'source': source})


def _fact(text):
    return Post.query.filter_by(body=text, kind='feit').one()


def _facts_block(html):
    start = html.index('id="feiten"')
    return html[start:html.index('</section>', start)]


class TestAddFact:
    def test_add_with_direction_and_source(self, app, db, make_user, proposal_thread, fake_ai):
        t = proposal_thread()
        anna = make_user()
        resp = _add(app, anna, t)
        fact = _fact('Het minimumloon steeg in 2024 met 3 procent.')
        assert resp.status_code == 302 and resp.headers['Location'].endswith(f'#post-{fact.id}')
        assert (fact.kind, fact.fact_direction, fact.fact_source, fact.user_id, fact.parent_id) == \
            ('feit', 'voor', 'https://example.org/cbs-2024', anna.id, None)
        html = _get(app, make_user(), f'/forum/{t.id}').get_data(as_text=True)
        block = _facts_block(html)
        assert 'Feiten <span class="gu-facts__count">(1)</span>' in block
        assert '<span class="gu-fact__dir gu-fact__dir--voor">Voor</span>' in block
        assert ('<a href="https://example.org/cbs-2024" target="_blank" rel="nofollow ugc noopener noreferrer">'
                'https://example.org/cbs-2024</a>') in block
        # Geen auteur in beeld, intern wel bewaard.
        assert anna.username not in block
        # Boven de discussie, en niet in de discussieboom of de tellingen.
        assert html.index('id="feiten"') < html.index('class="gu-thread"')
        assert f'id="node-{fact.id}"' not in html
        head = html[html.index('gu-forum__threadmeta'):html.index('</div>', html.index('gu-forum__threadmeta'))]
        assert re.search(r'1\s*reactie\b', head)
        assert 'gu-quality__mark' not in block and 'gu-stance' not in block

    @pytest.mark.parametrize('direction,label,lang', [('tegen', 'Tegen', 'nl'), ('neutraal', 'Neutraal', 'nl'),
                                                      ('voor', 'For', 'en'), ('tegen', 'Against', 'en'),
                                                      ('neutraal', 'Neutral', 'en')])
    def test_direction_labels(self, app, db, make_user, proposal_thread, fake_ai, direction, label, lang):
        t = proposal_thread()
        _add(app, make_user(), t, text=f'Feit {direction} {lang}', direction=direction, source='CBS Statline 2024')
        block = _facts_block(_get(app, None, f'/forum/{t.id}', lang).get_data(as_text=True))
        assert f'<span class="gu-fact__dir gu-fact__dir--{direction}">{label}</span>' in block
        # Een bronvermelding zonder URL staat als tekst, niet als link.
        assert 'CBS Statline 2024' in block and 'href="CBS' not in block

    @pytest.mark.parametrize('field,value,message', [
        ('source', '', 'Geef een bron.'),
        ('direction', 'misschien', 'Kies een richting.'),
        ('text', '', 'Beschrijf het feit (maximaal 1000 tekens).'),
    ])
    def test_validation(self, app, db, make_user, proposal_thread, fake_ai, field, value, message):
        t = proposal_thread()
        anna = make_user()
        data = {'text': 'Een feit.', 'direction': 'voor', 'source': 'Bron', field: value}
        client = _client(app, anna)
        resp = _req(client, 'POST', f'/forum/{t.id}/feiten', data=data)
        assert resp.status_code == 303 and Post.query.filter_by(thread_id=t.id, kind='feit').count() == 0
        html = _req(client, 'GET', f'/forum/{t.id}').get_data(as_text=True)
        assert message in _facts_block(html)

    def test_refused_outside_facts_phase_and_read_only(self, app, db, make_user, proposal_thread, fake_ai):
        t = proposal_thread()
        anna = make_user()
        _add(app, anna, t, text='Vast feit.')
        fact = _fact('Vast feit.')
        t.proposal.phase = 'bijstellen'
        db.session.commit()
        assert _add(app, anna, t, text='Te laat.').status_code == 403
        assert Post.query.filter_by(body='Te laat.').count() == 0
        assert _req(_client(app, anna), 'POST', f'/forum/feit/{fact.id}/bewerken',
                    data={'text': 'Toch anders.', 'direction': 'voor', 'source': 'x'}).status_code == 403
        assert _req(_client(app, anna), 'POST', f'/forum/feit/{fact.id}/verwijderen').status_code == 403
        block = _facts_block(_get(app, anna, f'/forum/{t.id}').get_data(as_text=True))
        assert 'Vast feit.' in block and 'De feitenfase is voorbij; de feiten staan vast.' in block
        assert 'action="/forum/' not in block  # geen formulieren meer

    def test_not_on_a_regular_conversation(self, app, db, make_user, make_thread, fake_ai):
        t = make_thread(op_user=make_user())
        assert _add(app, make_user(), t).status_code == 404
        assert 'id="feiten"' not in _get(app, None, f'/forum/{t.id}').get_data(as_text=True)

    def test_rules_must_be_accepted(self, app, db, proposal_thread, fake_ai):
        from app import bcrypt
        from app.models import User
        t = proposal_thread()
        name = f'n_{uuid.uuid4().hex[:6]}'
        fresh = User(username=name, email=f'{name}@example.com', verified=True,
                     password_hash=bcrypt.generate_password_hash('Sterk1!ww').decode('utf-8'))
        db.session.add(fresh)
        db.session.commit()
        try:
            resp = _add(app, fresh, t)
            assert resp.status_code == 302 and '/spelregels' in resp.headers['Location']
        finally:
            db.session.delete(fresh)
            db.session.commit()


class TestOwnFact:
    def test_edit_and_delete_own_fact(self, app, db, make_user, proposal_thread, fake_ai):
        t = proposal_thread()
        anna = make_user()
        _add(app, anna, t, text='Eerste versie van het feit.')
        fact = _fact('Eerste versie van het feit.')
        n_judge = len(fake_ai['judge'])
        assert _req(_client(app, make_user()), 'POST', f'/forum/feit/{fact.id}/bewerken',
                    data={'text': 'Van een ander.', 'direction': 'tegen', 'source': 'x'}).status_code == 403
        assert _req(_client(app, make_user('moderator')), 'POST', f'/forum/feit/{fact.id}/verwijderen').status_code == 403
        resp = _req(_client(app, anna), 'POST', f'/forum/feit/{fact.id}/bewerken',
                    data={'text': 'Bijgewerkt feit.', 'direction': 'tegen', 'source': 'https://example.org/nieuw'})
        assert resp.status_code == 302
        db.session.expire_all()
        fact = db.session.get(Post, fact.id)
        assert (fact.body, fact.fact_direction, fact.fact_source) == ('Bijgewerkt feit.', 'tegen', 'https://example.org/nieuw')
        assert fact.edited_at is not None and len(fake_ai['judge']) == n_judge + 1  # opnieuw beoordeeld
        block = _facts_block(_get(app, anna, f'/forum/{t.id}').get_data(as_text=True))
        assert 'Bewerken' in block and 'Verwijderen' in block
        assert _req(_client(app, anna), 'POST', f'/forum/feit/{fact.id}/verwijderen').status_code == 302
        db.session.expire_all()
        assert db.session.get(Post, fact.id) is None

    def test_message_routes_do_not_apply_to_facts(self, app, db, make_user, proposal_thread, fake_ai):
        t = proposal_thread()
        anna, bram = make_user(), make_user()
        _add(app, anna, t, text='Feit zonder reacties.')
        fact = _fact('Feit zonder reacties.')
        assert _req(_client(app, anna), 'POST', f'/forum/post/{fact.id}/bewerken', data={'body': 'x'}).status_code == 403
        assert _req(_client(app, anna), 'POST', f'/forum/post/{fact.id}/verwijderen').status_code == 403
        assert _req(_client(app, bram), 'POST', f'/forum/stance/{fact.id}', data={'stance': 'eens'}).status_code == 404
        _req(_client(app, bram), 'POST', f'/forum/{t.id}', data={'body': 'Reactie op feit', 'parent_id': fact.id})
        assert Post.query.filter_by(body='Reactie op feit').count() == 0
        assert _req(_client(app, make_user('moderator')), 'POST', f'/forum/kwaliteit/{fact.id}/besluit',
                    data={'action': 'toekennen', 'criterion': 'K1'}).status_code == 400


class TestNoQualityMark:
    def test_fact_gets_no_medal_and_does_not_count(self, app, db, make_user, proposal_thread, fake_ai):
        fake_ai['verdict'] = _verdict(suggested_criterion='K2', has_source=True, source_is_new=True)
        t = proposal_thread()
        anna = make_user()
        _add(app, anna, t, text='Feit met bron.')
        fact = _fact('Feit met bron.')
        award = QualityAward.query.filter_by(post_id=fact.id).one()
        assert (award.status, award.criterion) == ('afgewezen', None)
        content = fake_ai['judge'][-1]['messages'][0]['content']
        assert 'This contribution is a fact for the facts block' in content
        assert 'in favour of' in content and '<source>https://example.org/cbs-2024</source>' in content
        assert _req(_client(app), 'GET', f'/forum/kwaliteit/{fact.id}').status_code == 404
        from app.status import quality_counts
        assert quality_counts(anna.id) == (0, 0)


class TestConduct:
    def test_violating_fact_is_moved_like_a_post(self, app, db, make_user, proposal_thread, fake_ai):
        fake_ai['verdict'] = _verdict(**VIOLATION)
        t = proposal_thread()
        anna = make_user()
        _add(app, anna, t, text='Feit met een persoonlijke aanval.')
        fact = _fact('Feit met een persoonlijke aanval.')
        case = ConductCase.query.filter_by(post_id=fact.id).one()
        assert (case.action, case.rule, case.on_background) == ('verplaatst', 'respect', True)
        other = _facts_block(_get(app, make_user(), f'/forum/{t.id}').get_data(as_text=True))
        assert 'Feit verplaatst wegens de gedragsregels' in other and f'#zaak-{case.id}' in other
        assert 'persoonlijke aanval' not in other and '(0)' in other
        own = _facts_block(_get(app, anna, f'/forum/{t.id}').get_data(as_text=True))
        assert 'persoonlijke aanval' in own and 'Verplaatst naar de achtergrondpagina' in own
        assert 'Bezwaar maken tegen dit besluit' in own
        bg = _get(app, None, '/achtergrond').get_data(as_text=True)
        card = bg[bg.index(f'id="zaak-{case.id}"'):]
        card = card[:card.index('</li>')]
        assert 'Feit met een persoonlijke aanval.' in card and '>Feit<' in card and anna.username not in card
        # De statuspoll meldt het de inbrenger, net als bij een bericht.
        data = _req(_client(app, anna), 'GET', f'/forum/kwaliteit/{fact.id}/status').get_json()
        assert data['conduct']['action'] == 'verplaatst'

    def test_spam_fact_is_hidden_and_not_on_background(self, app, db, make_user, proposal_thread, fake_ai):
        fake_ai['verdict'] = _verdict(**SPAM)
        t = proposal_thread()
        anna = make_user()
        _add(app, anna, t, text='Koop goedkope horloges.', source='https://spam.example')
        fact = _fact('Koop goedkope horloges.')
        assert ConductCase.query.filter_by(post_id=fact.id).one().action == 'verborgen'
        other = _facts_block(_get(app, make_user(), f'/forum/{t.id}').get_data(as_text=True))
        assert 'horloges' not in other and f'id="post-{fact.id}"' not in other
        own = _facts_block(_get(app, anna, f'/forum/{t.id}').get_data(as_text=True))
        assert 'horloges' in own and 'Verborgen voor andere lezers als spam of misbruik.' in own
        assert 'horloges' not in _get(app, None, '/achtergrond').get_data(as_text=True)

    def test_moderator_can_move_a_fact(self, app, db, make_user, proposal_thread, fake_ai):
        t = proposal_thread()
        _add(app, make_user(), t, text='Feit naast het onderwerp.')
        fact = _fact('Feit naast het onderwerp.')
        mod = make_user('moderator')
        block = _facts_block(_get(app, mod, f'/forum/{t.id}').get_data(as_text=True))
        assert f'/forum/handhaving/{fact.id}/verplaatsen' in block
        assert _req(_client(app, mod), 'POST', f'/forum/handhaving/{fact.id}/verplaatsen',
                    data={'rule': 'onderwerp', 'note': 'Gaat niet over de stelling.'}).status_code == 302
        assert ConductCase.query.filter_by(post_id=fact.id).one().action == 'verplaatst'


class TestQueries:
    def test_no_extra_queries_per_fact(self, app, db, make_user, proposal_thread, fake_ai):
        t = proposal_thread()
        anna = make_user()
        _add(app, anna, t, text='Feit nummer 0.')
        from app import db as _db

        def count():
            seen = []
            listener = lambda *a: seen.append(1)  # noqa: E731
            event.listen(_db.engine, 'before_cursor_execute', listener)
            try:
                _get(app, None, f'/forum/{t.id}')
            finally:
                event.remove(_db.engine, 'before_cursor_execute', listener)
            return len(seen)

        _get(app, None, f'/forum/{t.id}')
        one = count()
        for i in range(1, 5):
            _add(app, anna, t, text=f'Feit nummer {i}.')
        assert count() == one
