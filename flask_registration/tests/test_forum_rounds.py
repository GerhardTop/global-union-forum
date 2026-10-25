"""
Fase E, stap 5c: een voorstel opnieuw openstellen voor het publiek.

Dekt: de indiener of een admin opent in het vervolg een nieuwe publieke
stemronde; een ander (ook een moderator) niet, en niet buiten het vervolg of
terwijl er een ronde loopt; een eigen stemperiode binnen het maximum; één
stem per persoon per ronde; sluiten op datum via de tik en bij het bekijken;
een eigen uitslag per ronde, allemaal gelabeld zichtbaar; het statuslabel
volgt de lopende ronde en daarna de laatste uitslag; een derde ronde; de
samenvatting voor volgers.

Gedeelde conftest-app (CSRF uit).
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app import digest, proposals
from app.models import Digest, Post, ProposalFollow, ProposalRound, ProposalVote, RoundVote
from tests.test_forum_proposal_phases import make_proposal  # noqa: F401
from tests.test_forum_conduct import _fresh_rate_limits  # noqa: F401  (limiter per test resetten)
from tests.test_forum_proposals import _client, _req, make_user  # noqa: F401
from tests.test_forum_voting import _page, _proposal, _vote, voter, voting  # noqa: F401


@pytest.fixture
def finished_event(app, db, make_user, voter, voting):
    """Een event waarvan ronde 1 is afgerond: 2 voor, 1 tegen (aangenomen)."""
    def _make():
        host = make_user()
        t = voting(host=host)
        for choice in ('voor', 'voor', 'tegen'):
            _vote(app, voter(), t, choice)
        _req(_client(app, host), 'POST', f'/forum/{t.id}/voorstel/fase', data={'direction': 'volgende'})
        assert _proposal(t.id).result == 'aangenomen'
        return host, t
    return _make


def _open(app, user, thread, duur='7', eenheid='dagen'):
    return _req(_client(app, user), 'POST', f'/forum/{thread.id}/voorstel/ronde',
                data={'duur_ronde': duur, 'eenheid_ronde': eenheid})


def _status(html):
    start = html.index('class="gu-proposal-status')
    return html[start:html.index('</span>', start)]


def _round(thread_id, number):
    from app import db
    db.session.expire_all()
    return ProposalRound.query.filter_by(proposal_id=_proposal(thread_id).id, number=number).one()


class TestOpening:
    def test_proposer_opens_public_round_from_follow_up(self, app, db, finished_event):
        host, t = finished_event()
        page = _page(app, host, t)
        assert 'Nieuwe publieke stemronde openen' in page and 'Maximaal 14 dagen' in page
        before = datetime.utcnow()
        assert _open(app, host, t).status_code == 302
        rnd = _round(t.id, 2)
        assert rnd.opened_by_id == host.id and rnd.voting_minutes == 7 * 1440 and rnd.closed_at is None
        assert before + timedelta(days=7) <= rnd.voting_end <= datetime.utcnow() + timedelta(days=7)
        html = _page(app, None, t)
        assert 'Stemmen open tot ' in _status(html)
        assert 'Ronde 2 (publiek) · Stemmen open tot ' in html
        # De vorige uitslag blijft gelabeld staan; feiten en discussie ook.
        assert 'Ronde 1 (event)' in html and '66,7% voor, 33,3% tegen' in html
        assert ProposalFollow.query.filter_by(proposal_id=rnd.proposal_id, user_id=host.id).one().active
        _req(_client(app, host), 'POST', f'/forum/{t.id}', data={'body': 'Reactie tijdens ronde 2'})
        assert Post.query.filter_by(thread_id=t.id, body='Reactie tijdens ronde 2').count() == 1

    def test_admin_may_open_others_may_not(self, app, db, make_user, finished_event):
        host, t = finished_event()
        other, mod = make_user(), make_user()
        mod.set_role('moderator')
        db.session.commit()
        for user in (other, mod):
            assert _open(app, user, t).status_code == 403
            assert 'Nieuwe publieke stemronde openen' not in _page(app, user, t)
        admin = make_user(admin=True)
        assert _open(app, admin, t, duur='48', eenheid='uren').status_code == 302
        assert _round(t.id, 2).voting_minutes == 48 * 60

    def test_not_outside_follow_up_or_during_a_round(self, app, db, make_user, voting, finished_event):
        host = make_user()
        t = voting(host=host)  # nog in de stemfase
        assert _open(app, host, t).status_code == 403
        host2, t2 = finished_event()
        assert _open(app, host2, t2).status_code == 302
        assert _open(app, host2, t2).status_code == 403  # er loopt al een ronde

    @pytest.mark.parametrize('duur,eenheid,message', [
        ('15', 'dagen', 'Voor deze fase maximaal 14 dagen.'),
        ('0', 'dagen', 'Vul een geheel getal groter dan nul in.'),
        ('drie', 'uren', 'Vul een geheel getal groter dan nul in.'),
    ])
    def test_voting_period_within_maximum(self, app, db, finished_event, duur, eenheid, message):
        host, t = finished_event()
        client = _client(app, host)
        resp = _req(client, 'POST', f'/forum/{t.id}/voorstel/ronde', data={'duur_ronde': duur, 'eenheid_ronde': eenheid})
        assert resp.status_code == 303
        assert ProposalRound.query.filter_by(proposal_id=_proposal(t.id).id).count() == 0
        assert message in _req(client, 'GET', f'/forum/{t.id}').get_data(as_text=True)


class TestVotingAndResult:
    def test_one_vote_per_person_per_round(self, app, db, make_user, voter, finished_event):
        host, t = finished_event()
        _open(app, host, t)
        rnd = _round(t.id, 2)
        anna = voter()
        assert _vote(app, anna, t, 'voor').status_code == 302
        assert _vote(app, anna, t, 'tegen').status_code == 302   # wijzigen kan
        assert [(v.user_id, v.choice) for v in RoundVote.query.filter_by(round_id=rnd.id)] == [(anna.id, 'tegen')]
        assert ProposalVote.query.filter_by(proposal_id=rnd.proposal_id).count() == 3  # ronde 1 onveranderd
        db.session.add(RoundVote(round_id=rnd.id, user_id=anna.id, choice='voor'))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
        assert _vote(app, make_user(), t, 'voor').status_code == 403  # zonder stemrecht

    def test_voter_from_round_one_votes_again(self, app, db, voter, voting, make_user):
        host = make_user()
        t = voting(host=host)
        anna = voter()
        _vote(app, anna, t, 'voor')
        _req(_client(app, host), 'POST', f'/forum/{t.id}/voorstel/fase', data={'direction': 'volgende'})
        _open(app, host, t)
        assert _vote(app, anna, t, 'tegen').status_code == 302
        assert RoundVote.query.filter_by(user_id=anna.id).one().choice == 'tegen'
        assert ProposalVote.query.filter_by(user_id=anna.id).one().choice == 'voor'

    def test_round_closes_on_date_via_tick_with_own_result(self, app, db, voter, finished_event):
        host, t = finished_event()
        _open(app, host, t)
        for choice in ('tegen', 'tegen', 'voor', 'onthouding'):
            _vote(app, voter(), t, choice)
        rnd = _round(t.id, 2)
        rnd.voting_end = datetime.utcnow() - timedelta(minutes=1)
        db.session.commit()
        assert proposals.advance_due() >= 1
        rnd = _round(t.id, 2)
        assert rnd.closed_at is not None and (rnd.result, rnd.result_pct) == ('verworpen', 33.3)
        assert (rnd.votes_for, rnd.votes_against, rnd.votes_abstain) == (1, 2, 1)
        p = _proposal(t.id)
        assert (p.result, p.result_pct) == ('aangenomen', 66.7)  # ronde 1 blijft
        html = _page(app, None, t)
        assert 'verworpen">Verworpen</span>' in _status(html) + '</span>'
        results = html[html.index('id="uitslag"'):]
        assert 'Uitslagen per ronde' in results
        r1 = results[results.index('id="ronde-1"'):results.index('id="ronde-2"')]
        r2 = results[results.index('id="ronde-2"'):]
        assert 'Ronde 1 (event)' in r1 and '>Aangenomen<' in r1 and '66,7% voor' in r1
        assert 'Ronde 2 (publiek)' in r2 and '>Verworpen<' in r2 and '33,3% voor, 66,7% tegen' in r2
        assert 'Uitgebrachte stemmen: 4' in r2
        index = _req(_client(app), 'GET', '/forum').get_data(as_text=True)
        row = index[index.index(f'/forum/{t.id}"'):]
        assert 'gu-proposal-status--verworpen">Verworpen</span>' in row[:row.index('</li>')]
        en = _page(app, None, t, 'en')
        assert 'Round 1 (event)' in en and 'Round 2 (public)' in en and 'Results per round' in en

    def test_round_closes_when_viewed_and_third_round(self, app, db, voter, finished_event):
        host, t = finished_event()
        _open(app, host, t)
        _vote(app, voter(), t, 'voor')
        rnd = _round(t.id, 2)
        rnd.voting_end = datetime.utcnow() - timedelta(minutes=1)
        db.session.commit()
        html = _page(app, None, t)
        assert _round(t.id, 2).result == 'aangenomen'
        assert 'aangenomen">Aangenomen</span>' in _status(html) + '</span>'
        assert _open(app, host, t, duur='2').status_code == 302
        assert _round(t.id, 3).number == 3 and 'Stemmen open tot ' in _status(_page(app, None, t))
        assert _vote(app, voter(), t, 'voor').status_code == 302


class TestDigest:
    def test_followers_hear_opening_and_result(self, app, db, monkeypatch, voter, finished_event):
        import app.mail as mail_module
        monkeypatch.setattr(mail_module, 'send_email', lambda *a, **k: True)
        host, t = finished_event()
        host.last_digest_at = datetime.utcnow() - timedelta(days=2)
        db.session.commit()
        _open(app, host, t)
        follow = ProposalFollow.query.filter_by(user_id=host.id).one()
        follow.created_at = datetime.utcnow() - timedelta(days=3)
        db.session.commit()
        items = digest.build(host)
        # De uitslag van ronde 1 viel ook binnen de periode; de opening van ronde 2 staat erbij.
        types = [(c['type'], c.get('number')) for c in items[0]['changes']]
        assert ('ronde_open', 2) in types and ('uitslag', None) in types
        with app.test_request_context():
            digest.send_for(host)
        page = _req(_client(app, host), 'GET', '/meldingen').get_data(as_text=True)
        assert 'Ronde 2 geopend, stemmen tot ' in page
        # Na sluiten: de uitslag van de ronde.
        _vote(app, voter(), t, 'voor')
        rnd = _round(t.id, 2)
        rnd.voting_end = datetime.utcnow() - timedelta(minutes=1)
        db.session.commit()
        proposals.advance_due()
        later = datetime.utcnow() + timedelta(days=1)
        host_db = db.session.get(type(host), host.id)
        items = digest.build(host_db, later)
        assert {'type': 'ronde_uitslag', 'number': 2, 'result': 'aangenomen', 'pct': 100.0} in items[0]['changes']
        with app.test_request_context():
            digest.send_for(host_db, later)
        assert Digest.query.filter_by(user_id=host.id).count() == 2
        page = _req(_client(app, host), 'GET', '/meldingen').get_data(as_text=True)
        assert 'Ronde 2: aangenomen met 100% voor' in page
