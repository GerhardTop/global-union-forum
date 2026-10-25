"""
Fase E, stap 5b: de periodieke taak en de dagelijkse samenvatting.

Dekt: POST /intern/tik weigert zonder geldig geheim (ook als TICK_SECRET
niet gezet is) en werkt met een geldig geheim; volgen en ontvolgen; de
indiener volgt zijn publieke voorstel; automatisch volgen bij meedoen
(reageren, feit, stem), maar niet na bewust ontvolgen; de samenvatting vat
de juiste veranderingen samen (fase, stemmen sluit binnenkort, uitslag,
activiteit van anderen) en komt hoogstens één keer per kalenderdag; e-mail
uit laat de samenvatting in het forum staan; een event levert geen
samenvatting op; de melding in beeld; de migratie.

Gedeelde conftest-app (CSRF uit). De e-mail wordt onderschept.
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import inspect as sa_inspect, text

from app import digest, proposals
from app.models import Digest, Post, Proposal, ProposalFollow, User
from tests.test_forum_proposal_phases import make_proposal  # noqa: F401
from tests.test_forum_proposals import PUBLIC, _client, _req, _start, _thread, make_user  # noqa: F401

SECRET = 'tik-geheim-voor-tests'


@pytest.fixture
def mails(monkeypatch):
    sent = []

    def _fake(to, subject, html):
        sent.append({'to': to, 'subject': subject, 'html': html})
        return True

    import app.mail
    monkeypatch.setattr(app.mail, 'send_email', _fake)
    return sent


@pytest.fixture
def secret(monkeypatch):
    monkeypatch.setenv('TICK_SECRET', SECRET)


def _tick(app, header=None):
    headers = {'Authorization': header} if header is not None else {}
    return app.test_client().post('/intern/tik', headers=headers)


def _follow_row(proposal, user):
    from app import db
    db.session.expire_all()
    return ProposalFollow.query.filter_by(proposal_id=proposal.id, user_id=user.id).first()


def _reply_as(app, user, thread, body):
    _req(_client(app, user), 'POST', f'/forum/{thread.id}', data={'body': body})


class TestEndpoint:
    def test_disabled_without_tick_secret(self, app, monkeypatch):
        monkeypatch.delenv('TICK_SECRET', raising=False)
        assert _tick(app).status_code == 403
        assert _tick(app, 'Bearer ').status_code == 403
        assert _tick(app, f'Bearer {SECRET}').status_code == 403

    def test_refuses_missing_or_wrong_secret(self, app, secret):
        assert _tick(app).status_code == 403
        assert _tick(app, 'Bearer verkeerd').status_code == 403
        assert _tick(app, SECRET).status_code == 403          # zonder "Bearer"
        assert app.test_client().get('/intern/tik').status_code == 405

    def test_runs_transitions_and_digest_with_valid_secret(self, app, db, secret, make_proposal, mails):
        t = make_proposal(started_minutes_ago=90)  # feitenfase voorbij
        resp = _tick(app, f'Bearer {SECRET}')
        assert resp.status_code == 200
        data = resp.get_json()
        assert set(data) == {'moved', 'digests'} and data['moved'] >= 1
        assert Proposal.query.filter_by(thread_id=t.id).one().phase == 'bijstellen'
        again = _tick(app, f'Bearer {SECRET}').get_json()   # idempotent
        assert again['moved'] == 0


class TestFollowing:
    def test_follow_and_unfollow(self, app, db, make_user, make_proposal):
        anna = make_user()
        t = make_proposal()
        client = _client(app, anna)
        page = _req(client, 'GET', f'/forum/{t.id}').get_data(as_text=True)
        assert 'Volg dit voorstel' in page
        assert _req(client, 'POST', f'/forum/{t.id}/voorstel/volgen', data={'action': 'volgen'}).status_code == 302
        assert _follow_row(t.proposal, anna).active is True
        page = _req(client, 'GET', f'/forum/{t.id}').get_data(as_text=True)
        assert 'Je volgt dit voorstel. Je krijgt hoogstens één samenvatting per dag.' in page and 'Ontvolgen' in page
        _req(client, 'POST', f'/forum/{t.id}/voorstel/volgen', data={'action': 'ontvolgen'})
        assert _follow_row(t.proposal, anna).active is False
        assert 'Volg dit voorstel' in _req(client, 'GET', f'/forum/{t.id}').get_data(as_text=True)
        assert ProposalFollow.query.filter_by(proposal_id=t.proposal.id, user_id=anna.id).count() == 1

    def test_no_following_for_events_or_anonymous(self, app, db, make_user, make_proposal):
        t = make_proposal(kind='event')
        anna = make_user()
        assert _req(_client(app, anna), 'POST', f'/forum/{t.id}/voorstel/volgen', data={'action': 'volgen'}).status_code == 404
        assert 'Volg dit voorstel' not in _req(_client(app, anna), 'GET', f'/forum/{t.id}').get_data(as_text=True)
        public = make_proposal()
        assert 'Volg dit voorstel' not in _req(_client(app), 'GET', f'/forum/{public.id}').get_data(as_text=True)
        resp = _req(_client(app), 'POST', f'/forum/{public.id}/voorstel/volgen', data={'action': 'volgen'})
        assert resp.status_code == 302 and '/login' in resp.headers['Location']

    def test_proposer_follows_own_public_proposal(self, app, db, make_user):
        anna = make_user()
        _start(app, anna, 'Volg eigen publiek', **PUBLIC)
        _start(app, anna, 'Volg eigen event', type='voorstel', kind='event')
        assert _follow_row(_thread('Volg eigen publiek').proposal, anna).active is True
        assert _follow_row(_thread('Volg eigen event').proposal, anna) is None

    def test_participation_follows_automatically_but_not_after_unfollow(self, app, db, make_user, make_proposal):
        t = make_proposal()
        bram, carla, dirk = make_user(), make_user(), make_user()
        _reply_as(app, bram, t, 'Reactie van Bram.')
        assert _follow_row(t.proposal, bram).active is True
        _req(_client(app, carla), 'POST', f'/forum/{t.id}/feiten',
             data={'text': 'Een feit.', 'direction': 'neutraal', 'source': 'CBS'})
        assert _follow_row(t.proposal, carla).active is True
        p = t.proposal
        p.phase = 'stemmen'
        db.session.commit()
        dirk.quality_status = 'voter'
        db.session.commit()
        _req(_client(app, dirk), 'POST', f'/forum/{t.id}/voorstel/stem', data={'choice': 'voor'})
        assert _follow_row(t.proposal, dirk).active is True
        # Bewust ontvolgd: opnieuw meedoen maakt je geen volger.
        _req(_client(app, bram), 'POST', f'/forum/{t.id}/voorstel/volgen', data={'action': 'ontvolgen'})
        _reply_as(app, bram, t, 'Nog een reactie van Bram.')
        assert _follow_row(t.proposal, bram).active is False


class TestDigest:
    def _setup(self, db, make_user, make_proposal, minutes_ago=0, lang=None):
        anna = make_user()
        anna.lang = lang
        t = make_proposal(started_minutes_ago=minutes_ago)
        digest.follow(t.proposal, anna)
        db.session.commit()
        f = ProposalFollow.query.filter_by(proposal_id=t.proposal.id, user_id=anna.id).one()
        f.created_at = datetime.utcnow() - timedelta(days=2)
        anna.last_digest_at = datetime.utcnow() - timedelta(days=1)
        db.session.commit()
        return anna, t

    @pytest.mark.parametrize('lang,expected,absent', [
        ('nl', ('Je dagelijkse samenvatting', 'Het stemmen is geopend, tot ', 'Nieuwe reacties: 1'),
         ('Your daily summary', 'Voting has opened', 'New replies')),
        ('en', ('Your daily summary', 'Voting has opened, until ', 'New replies: 1'),
         ('Je dagelijkse samenvatting', 'Het stemmen is geopend', 'Nieuwe reacties')),
        # No stored preference yet: Dutch.
        (None, ('Je dagelijkse samenvatting', 'Het stemmen is geopend, tot ', 'Nieuwe reacties: 1'),
         ('Your daily summary', 'Voting has opened', 'New replies')),
    ])
    def test_summarises_phase_change_and_activity(self, app, db, make_user, make_proposal, mails,
                                                  lang, expected, absent):
        anna, t = self._setup(db, make_user, make_proposal, minutes_ago=150, lang=lang)  # nu stemmen
        bram = make_user()
        _reply_as(app, bram, t, 'Nieuwe reactie van Bram.')
        _reply_as(app, anna, t, 'Eigen reactie telt niet.')
        with app.test_request_context(base_url='https://globalunionforum.org'):
            result = digest.tick()
        assert result['digests'] >= 1
        d = Digest.query.filter_by(user_id=anna.id).one()
        item = next(i for i in d.items if i['thread_id'] == t.id)
        assert [c['type'] for c in item['changes']] == ['stemmen_open'] and item['changes'][0]['until']
        assert (item['new_posts'], item['new_facts']) == (1, 0)
        db.session.expire_all()
        anna = db.session.get(User, anna.id)
        assert anna.has_unread_digest is True and anna.last_digest_at is not None
        mail = next(m for m in mails if m['to'] == anna.email)
        # One language only: the user's stored language, Dutch by default.
        text = mail['subject'] + mail['html']
        assert mail['subject'] == expected[0]
        for snippet in expected:
            assert snippet in text, (lang, snippet)
        for snippet in absent:
            assert snippet not in text, (lang, snippet)
        # In the forum the summary uses the same language, whatever the session language.
        other = 'nl' if lang == 'en' else 'en'
        page = _req(_client(app, anna, other), 'GET', '/meldingen').get_data(as_text=True)
        assert expected[1] in page and absent[1] not in page
        # Absolute links (de host komt uit SERVER_NAME of het verzoek).
        assert f'/forum/{t.id}"' in mail['html'] and 'href="https://' in mail['html']
        assert d.emailed_at is not None

    def test_at_most_once_per_calendar_day(self, app, db, make_user, make_proposal, mails):
        anna, t = self._setup(db, make_user, make_proposal, minutes_ago=90)  # bijstellen
        now = datetime.utcnow()
        with app.test_request_context():
            digest.tick(now)
            first = Digest.query.filter_by(user_id=anna.id).count()
            assert first == 1
            # Later die dag: een nieuwe verandering, maar geen tweede bericht.
            p = Proposal.query.filter_by(thread_id=t.id).one()
            p.phase, p.phase_changed_at = 'stemmen', now + timedelta(minutes=5)
            db.session.commit()
            digest.tick(now + timedelta(minutes=10))
            assert Digest.query.filter_by(user_id=anna.id).count() == 1
            assert len([m for m in mails if m['to'] == anna.email]) == 1
            # De volgende dag wel.
            digest.tick(now + timedelta(days=1))
            assert Digest.query.filter_by(user_id=anna.id).count() == 2

    def test_only_activity_is_no_reason_for_a_message(self, app, db, make_user, make_proposal, mails):
        anna, t = self._setup(db, make_user, make_proposal)
        _reply_as(app, make_user(), t, 'Alleen activiteit.')
        with app.test_request_context():
            digest.tick()
        assert Digest.query.filter_by(user_id=anna.id).count() == 0

    def test_closing_soon_and_result(self, app, db, make_user, make_proposal, mails):
        anna, t = self._setup(db, make_user, make_proposal)
        p = Proposal.query.filter_by(thread_id=t.id).one()
        now = datetime.utcnow()
        p.phase, p.phase_changed_at, p.stemmen_end = 'stemmen', now - timedelta(days=3), now + timedelta(hours=5)
        db.session.commit()
        items = digest.build(db.session.get(User, anna.id), now)
        assert [c['type'] for c in items[0]['changes']] == ['sluit_binnenkort']
        p.phase, p.phase_changed_at = 'vervolg', now
        p.result, p.result_pct, p.result_at = 'aangenomen', 66.7, now
        db.session.commit()
        items = digest.build(db.session.get(User, anna.id), now + timedelta(minutes=1))
        assert {'type': 'uitslag', 'result': 'aangenomen', 'pct': 66.7} in items[0]['changes']
        with app.test_request_context():
            digest.send_for(db.session.get(User, anna.id), now + timedelta(minutes=1))
        page = _req(_client(app, anna), 'GET', '/meldingen').get_data(as_text=True)
        assert 'Uitslag: aangenomen met 66,7% voor' in page and 'Samenvatting van ' in page

    def test_email_off_keeps_in_app_digest(self, app, db, make_user, make_proposal, mails):
        anna, t = self._setup(db, make_user, make_proposal, minutes_ago=90)
        assert _req(_client(app, anna), 'POST', '/profiel/samenvatting', data={}).status_code == 302
        db.session.expire_all()
        assert db.session.get(User, anna.id).digest_email is False
        with app.test_request_context():
            digest.tick()
        assert Digest.query.filter_by(user_id=anna.id).count() == 1
        assert not [m for m in mails if m['to'] == anna.email]
        _req(_client(app, anna), 'POST', '/profiel/samenvatting', data={'digest_email': '1'})
        db.session.expire_all()
        assert db.session.get(User, anna.id).digest_email is True

    def test_event_gives_no_digest(self, app, db, make_user, make_proposal, mails):
        anna = make_user()
        t = make_proposal(kind='event', host=anna)
        assert digest.follow(t.proposal, anna) is None
        # Zelfs met een (handmatige) volgrij en een faseovergang: geen samenvatting.
        db.session.add(ProposalFollow(proposal_id=t.proposal.id, user_id=anna.id, active=True,
                                      created_at=datetime.utcnow() - timedelta(days=2)))
        proposals.step(t.proposal, 'volgende')
        db.session.commit()
        with app.test_request_context():
            digest.tick()
        assert Digest.query.filter_by(user_id=anna.id).count() == 0

    def test_banner_until_summary_is_opened(self, app, db, make_user, make_proposal, mails):
        anna, t = self._setup(db, make_user, make_proposal, minutes_ago=90)
        with app.test_request_context():
            digest.tick()
        home = _req(_client(app, anna), 'GET', '/forum').get_data(as_text=True)
        assert 'Je hebt een nieuwe dagelijkse samenvatting.' in home and 'href="/meldingen"' in home
        page = _req(_client(app, anna), 'GET', '/meldingen').get_data(as_text=True)
        assert 'Het voorstel wordt bijgesteld' in page
        assert 'Je hebt een nieuwe dagelijkse samenvatting.' not in _req(_client(app, anna), 'GET', '/forum').get_data(as_text=True)


class TestMigration:
    COLUMNS = ('digest_email', 'last_digest_at', 'has_unread_digest', 'lang')

    def test_user_columns_added_idempotently(self, app, db):
        from app import _migrate_columns
        db.session.close()
        with db.engine.connect() as conn:
            for col in self.COLUMNS:
                conn.execute(text(f'ALTER TABLE users DROP COLUMN {col}'))
            conn.commit()
        db.engine.dispose()
        for _ in range(2):
            _migrate_columns()
            cols = {c['name'] for c in sa_inspect(db.engine).get_columns('users')}
            assert set(self.COLUMNS) <= cols
        tables = set(sa_inspect(db.engine).get_table_names())
        assert {'proposal_follows', 'digests'} <= tables
