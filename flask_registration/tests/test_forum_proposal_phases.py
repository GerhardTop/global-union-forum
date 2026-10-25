"""
Fase E, stap 3: faseovergangen en het bijstellen van de stelling.

Dekt: een publiek voorstel schuift door zodra een fase-einddatum bereikt is,
via het commando "flask voorstellen-tick" (idempotent) en via het bekijken
van het voorstel of het forumoverzicht; nooit terug; een event schuift
alleen via de host-knoppen (vooruit en terug), alleen de host mag dat; de
indiener stelt de stelling één keer bij in de bijstelfase en daarna niet
meer; een ander kan dat niet; het statuslabel volgt de fase; de migratie.

Gedeelde conftest-app (CSRF uit).
"""
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import inspect as sa_inspect, text

from app import proposals
from app.models import Post, Proposal, Thread
from tests.test_forum_proposals import _client, _req, make_user  # noqa: F401

HOUR = {'feiten': 60, 'bijstellen': 60, 'stemmen': 60}


@pytest.fixture
def make_proposal(db, make_user):
    def _make(kind='publiek', started_minutes_ago=0, host=None, title=None):
        host = host or make_user()
        title = title or f'Stelling {uuid.uuid4().hex[:6]}'
        t = Thread(eyebrow_nl='Forum · voorstel', eyebrow_en='Forum · proposal',
                   title_prefix_nl=title, title_prefix_en=title)
        db.session.add(t)
        db.session.flush()
        db.session.add(Post(thread_id=t.id, user_id=host.id, author_name='', author_username=host.username,
                            body='Toelichting.', is_op=True, source_lang='nl'))
        start = datetime.utcnow() - timedelta(minutes=started_minutes_ago)
        proposals.create(t, kind, host, minutes=HOUR if kind == 'publiek' else None, start=start)
        db.session.commit()
        return t
    return _make


def _phase(thread_id):
    from app import db
    db.session.expire_all()
    return Proposal.query.filter_by(thread_id=thread_id).one().phase


def _page(app, user, thread, lang='nl'):
    return _req(_client(app, user, lang), 'GET', f'/forum/{thread.id}').get_data(as_text=True)


def _status(html):
    start = html.index('class="gu-proposal-status')
    return html[start:html.index('</span>', start)]


class TestPublicTransitions:
    def test_command_advances_due_proposals_idempotently(self, app, db, make_proposal):
        due = make_proposal(started_minutes_ago=90)        # feiten voorbij, nu bijstellen
        later = make_proposal(started_minutes_ago=0)       # nog in de feitenfase
        runner = app.test_cli_runner()
        result = runner.invoke(args=['voorstellen-tick'])
        assert result.exit_code == 0 and '1 voorstel(len) doorgeschoven.' in result.output
        assert _phase(due.id) == 'bijstellen' and _phase(later.id) == 'feiten'
        assert Proposal.query.filter_by(thread_id=due.id).one().phase_changed_at is not None
        again = runner.invoke(args=['voorstellen-tick'])
        assert '0 voorstel(len) doorgeschoven.' in again.output and _phase(due.id) == 'bijstellen'

    def test_skips_several_phases_at_once_and_never_goes_back(self, app, db, make_proposal):
        old = make_proposal(started_minutes_ago=4 * 60)
        assert proposals.advance_due() >= 1 and _phase(old.id) == 'vervolg'
        ahead = make_proposal(started_minutes_ago=0)
        p = Proposal.query.filter_by(thread_id=ahead.id).one()
        p.phase = 'stemmen'
        db.session.commit()
        assert proposals.advance(p) is False and _phase(ahead.id) == 'stemmen'

    def test_viewing_the_proposal_advances_it(self, app, db, make_proposal):
        t = make_proposal(started_minutes_ago=150)  # feiten en bijstellen voorbij: stemmen
        html = _page(app, None, t)
        assert _phase(t.id) == 'stemmen'
        assert 'Stemmen open tot ' in _status(html)
        current = html[html.index('is-current'):]
        assert 'Stemmen' in current[:current.index('</li>')]

    def test_viewing_the_overview_advances_it(self, app, db, make_proposal):
        t = make_proposal(started_minutes_ago=90)
        index = _req(_client(app), 'GET', '/forum').get_data(as_text=True)
        assert _phase(t.id) == 'bijstellen'
        row = index[index.index(f'/forum/{t.id}"'):]
        assert 'Wordt bijgesteld' in row[:row.index('</li>')]

    def test_status_label_follows_the_phase(self, app, db, make_proposal):
        # In het vervolg de uitslag; zonder stemmen is dat verworpen.
        for minutes, label in ((0, 'In discussie'), (90, 'Wordt bijgesteld'), (150, 'Stemmen open tot '),
                               (240, 'Verworpen')):
            t = make_proposal(started_minutes_ago=minutes)
            assert label in _status(_page(app, None, t)), (minutes, label)
        t = make_proposal(started_minutes_ago=240)
        assert 'Rejected' in _status(_page(app, None, t, 'en'))


class TestEventTransitions:
    def _step(self, app, user, thread, direction):
        return _req(_client(app, user), 'POST', f'/forum/{thread.id}/voorstel/fase', data={'direction': direction})

    def test_only_host_buttons_move_an_event(self, app, db, make_user, make_proposal):
        host = make_user()
        t = make_proposal(kind='event', host=host)
        assert proposals.advance_due() == 0 and _phase(t.id) == 'feiten'   # nooit vanzelf
        _page(app, None, t)
        assert _phase(t.id) == 'feiten'
        page = _page(app, host, t)
        assert 'Feiten sluiten en bijstellen openen' in page and 'Terug naar' not in page
        assert 'Feiten sluiten en bijstellen openen' not in _page(app, make_user(), t)
        assert self._step(app, make_user(), t, 'volgende').status_code == 403
        assert self._step(app, None, t, 'volgende').status_code == 302 and _phase(t.id) == 'feiten'  # naar inloggen
        expected = [('bijstellen', 'Bijstellen sluiten en stemmen openen', 'Wordt bijgesteld'),
                    ('stemmen', 'Stemmen sluiten en naar vervolg', 'Stemmen open'),
                    ('vervolg', None, 'Verworpen')]  # geen stemmen: verworpen
        for phase, next_label, status in expected:
            assert self._step(app, host, t, 'volgende').status_code == 302
            assert _phase(t.id) == phase
            page = _page(app, host, t)
            assert status in _status(page)
            if next_label:
                assert next_label in page
        assert 'Stemmen open tot' not in page  # een event heeft geen datums
        assert self._step(app, host, t, 'volgende').status_code == 400  # vervolg is de laatste fase
        assert 'Terug naar Stemmen' in page
        assert self._step(app, host, t, 'vorige').status_code == 302 and _phase(t.id) == 'stemmen'

    def test_moderator_is_also_host(self, app, db, make_user, make_proposal):
        t = make_proposal(kind='event')
        mod = make_user()
        mod.set_role('moderator')
        db.session.commit()
        assert self._step(app, mod, t, 'volgende').status_code == 302 and _phase(t.id) == 'bijstellen'
        assert self._step(app, mod, t, 'vorige').status_code == 302 and _phase(t.id) == 'feiten'
        assert self._step(app, mod, t, 'vorige').status_code == 400  # niet voor de eerste fase

    def test_public_proposal_has_no_host_buttons(self, app, db, make_user, make_proposal):
        host = make_user()
        t = make_proposal(host=host)
        assert 'Feiten sluiten en bijstellen openen' not in _page(app, host, t)
        assert self._step(app, host, t, 'volgende').status_code == 400 and _phase(t.id) == 'feiten'


class TestRevision:
    def _revise(self, app, user, thread, statement):
        return _req(_client(app, user), 'POST', f'/forum/{thread.id}/voorstel/bijstellen', data={'statement': statement})

    def test_proposer_revises_once_in_revision_phase(self, app, db, make_user, make_proposal):
        host = make_user()
        t = make_proposal(started_minutes_ago=90, host=host, title='Oorspronkelijke stelling')
        _page(app, None, t)  # schuift door naar bijstellen
        assert _phase(t.id) == 'bijstellen'
        page = _page(app, host, t)
        assert 'Stelling bijstellen' in page and 'Je kunt de stelling één keer bijstellen.' in page
        assert self._revise(app, host, t, 'Bijgestelde stelling').status_code == 302
        db.session.expire_all()
        thread = db.session.get(Thread, t.id)
        p = thread.proposal
        assert (thread.title_prefix_nl, thread.title_prefix_en) == ('Bijgestelde stelling', 'Bijgestelde stelling')
        assert p.original_statement == 'Oorspronkelijke stelling' and p.revised_at is not None
        page = _page(app, None, t)
        assert 'Bijgestelde stelling' in page and '<del>Oorspronkelijke stelling</del>' in page
        assert 'De stelling is bijgesteld op ' in page and '>Bijgesteld<' in page
        # Maar één keer: daarna staat de stelling vast.
        assert self._revise(app, host, t, 'Nog een keer anders').status_code == 403
        assert 'Stelling bijstellen' not in _page(app, host, t)

    def test_others_and_other_phases_cannot_revise(self, app, db, make_user, make_proposal):
        host = make_user()
        t = make_proposal(host=host)  # feitenfase
        assert self._revise(app, host, t, 'Te vroeg').status_code == 403
        t2 = make_proposal(started_minutes_ago=90, host=host)
        _page(app, None, t2)
        other = make_user()
        mod = make_user()
        mod.set_role('moderator')
        db.session.commit()
        for user in (other, mod):
            assert self._revise(app, user, t2, 'Door een ander').status_code == 403
            assert 'Stelling bijstellen' not in _page(app, user, t2)
        assert db.session.get(Thread, t2.id).title_prefix_nl != 'Door een ander'

    def test_invalid_statement_and_discussion_keeps_working(self, app, db, make_user, make_proposal):
        host = make_user()
        t = make_proposal(started_minutes_ago=90, host=host, title='Huidige stelling')
        _page(app, None, t)
        client = _client(app, host)
        for value in ('', 'Huidige stelling', 'x' * 301):
            resp = _req(client, 'POST', f'/forum/{t.id}/voorstel/bijstellen', data={'statement': value})
            assert resp.status_code == 303
            page = _req(client, 'GET', f'/forum/{t.id}').get_data(as_text=True)
            assert 'Vul een stelling in (maximaal 300 tekens) die verschilt van de huidige.' in page
        assert Proposal.query.filter_by(thread_id=t.id).one().revised_at is None
        # De gewone discussie blijft werken in de bijstelfase.
        bram = make_user()
        _req(_client(app, bram), 'POST', f'/forum/{t.id}', data={'body': 'Reactie tijdens bijstellen'})
        assert Post.query.filter_by(thread_id=t.id, body='Reactie tijdens bijstellen').count() == 1

    def test_edit_page_cannot_change_statement_after_facts_phase(self, app, db, make_user, make_proposal):
        host = make_user()
        t = make_proposal(started_minutes_ago=90, host=host, title='Vaste stelling')
        _page(app, None, t)
        resp = _req(_client(app, host), 'POST', f'/forum/{t.id}/bewerken', data={'title': 'Via bewerken', 'body': 'x'})
        assert resp.status_code == 302
        assert db.session.get(Thread, t.id).title_prefix_nl == 'Vaste stelling'
        fresh = make_proposal(host=host, title='Nog in feitenfase')
        assert _req(_client(app, host), 'GET', f'/forum/{fresh.id}/bewerken').status_code == 200


class TestMigration:
    def test_columns_added_idempotently(self, app, db):
        from app import _migrate_columns
        db.session.close()
        with db.engine.connect() as conn:
            for col in ('phase_changed_at', 'revised_at', 'original_statement'):
                conn.execute(text(f'ALTER TABLE proposals DROP COLUMN {col}'))
            conn.commit()
        db.engine.dispose()
        for _ in range(2):
            _migrate_columns()
            cols = {c['name'] for c in sa_inspect(db.engine).get_columns('proposals')}
            assert {'phase_changed_at', 'revised_at', 'original_statement'} <= cols
