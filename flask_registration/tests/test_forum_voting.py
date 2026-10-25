"""
Fase E, stap 4: het stemmen.

Dekt: de drempel bij het starten (standaard 50, minimaal 50); een
stemgerechtigde stemt en wijzigt zijn stem in de stemfase; zonder stemrecht
kan het niet (met de melding); buiten de stemfase geweigerd; één stem per
persoon (unieke koppeling); de verborgen tussenstand toont alleen het aantal
stemmers, de live tussenstand de verdeling; de uitslag tegen de drempel met
onthoudingen buiten de teller; sluiten op datum en door de host; de
uitslag in het vervolg; terug uit het vervolg; de discussie blijft open;
de migratie.

Gedeelde conftest-app (CSRF uit).
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import inspect as sa_inspect, text
from sqlalchemy.exc import IntegrityError

from app import proposals
from app.models import Post, Proposal, ProposalVote, Thread
from tests.test_forum_proposal_phases import make_proposal  # noqa: F401
from tests.test_forum_proposals import PUBLIC, _client, _req, _start, _thread, make_user  # noqa: F401


@pytest.fixture
def voter(db, make_user):
    def _make():
        u = make_user()
        u.quality_status = 'voter'  # verdiend stemrecht
        db.session.commit()
        assert u.can_vote()
        return u
    return _make


@pytest.fixture
def voting(db, make_proposal):
    """Een event in de stemfase."""
    def _make(live=False, threshold=50, host=None):
        t = make_proposal(kind='event', host=host)
        p = t.proposal
        p.phase, p.show_live_tally, p.threshold_pct = 'stemmen', live, threshold
        db.session.commit()
        return t
    return _make


def _vote(app, user, thread, choice):
    return _req(_client(app, user), 'POST', f'/forum/{thread.id}/voorstel/stem', data={'choice': choice})


def _page(app, user, thread, lang='nl'):
    return _req(_client(app, user, lang), 'GET', f'/forum/{thread.id}').get_data(as_text=True)


def _proposal(thread_id):
    from app import db
    db.session.expire_all()
    return Proposal.query.filter_by(thread_id=thread_id).one()


class TestThreshold:
    def test_threshold_set_at_start(self, app, db, make_user):
        anna = make_user()
        assert _start(app, anna, 'Met drempel 60', drempel='60', **PUBLIC).status_code == 302
        assert _thread('Met drempel 60').proposal.threshold_pct == 60
        assert _start(app, anna, 'Zonder drempel', type='voorstel', kind='event').status_code == 302
        assert _thread('Zonder drempel').proposal.threshold_pct == 50
        page = _req(_client(app), 'GET', f'/forum/{_thread("Met drempel 60").id}').get_data(as_text=True)
        assert 'Aangenomen bij meer dan 60% voor' in page

    @pytest.mark.parametrize('value', ['49', '100', 'vijftig', '0'])
    def test_threshold_below_majority_refused(self, app, db, make_user, value):
        anna = make_user()
        client = _client(app, anna)
        resp = _req(client, 'POST', '/forum/nieuw', data={'title': 'Lage drempel', 'body': 'x',
                                                           'drempel': value, **PUBLIC})
        assert resp.status_code == 303 and Thread.query.filter_by(title_prefix_nl='Lage drempel').count() == 0
        html = _req(client, 'GET', '/forum/nieuw').get_data(as_text=True)
        assert 'Kies een drempel van 50 tot en met 99 procent.' in html and f'value="{value}"' in html

    def test_form_shows_threshold_field_with_default(self, app, db, make_user):
        html = _req(_client(app, make_user()), 'GET', '/forum/nieuw').get_data(as_text=True)
        assert 'name="drempel"' in html and 'min="50"' in html and 'value="50"' in html
        assert 'Drempel voor aannemen' in html


class TestCasting:
    def test_voter_votes_and_changes_vote(self, app, db, voter, voting):
        t = voting()
        anna = voter()
        assert _vote(app, anna, t, 'voor').status_code == 302
        assert _vote(app, anna, t, 'tegen').status_code == 302
        votes = ProposalVote.query.filter_by(proposal_id=t.proposal.id).all()
        assert [(v.user_id, v.choice) for v in votes] == [(anna.id, 'tegen')]
        html = _page(app, anna, t)
        assert 'Je stem: Tegen. Je kunt je stem wijzigen tot het stemmen sluit.' in html
        assert 'value="tegen" class="gu-vote__btn gu-vote__btn--tegen is-mine"' in html
        assert 'aria-pressed="true">Tegen</button>' in html
        assert _vote(app, anna, t, 'misschien').status_code == 400

    def test_one_vote_per_person_in_the_database(self, app, db, voter, voting):
        t = voting()
        anna = voter()
        _vote(app, anna, t, 'voor')
        db.session.add(ProposalVote(proposal_id=t.proposal.id, user_id=anna.id, choice='tegen'))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()

    def test_without_voting_rights(self, app, db, make_user, voting):
        t = voting()
        bram = make_user()  # bijdrager, geen stemrecht
        assert not bram.can_vote()
        assert _vote(app, bram, t, 'voor').status_code == 403
        assert ProposalVote.query.filter_by(proposal_id=t.proposal.id).count() == 0
        html = _page(app, bram, t)
        assert 'Je hebt nog geen stemrecht' in html
        assert 'Stemrecht verdien je met kwaliteitsbijdragen' in html and 'gu-vote__choices' not in html
        en = _page(app, bram, t, 'en')
        assert 'You do not have voting rights yet' in en
        assert 'Log in om te stemmen.' in _page(app, None, t)

    def test_manually_granted_voter_can_vote(self, app, db, make_user, voting):
        t = voting()
        u = make_user()
        u.set_role('voter')
        db.session.commit()
        assert _vote(app, u, t, 'onthouding').status_code == 302

    @pytest.mark.parametrize('phase', ['feiten', 'bijstellen', 'vervolg'])
    def test_refused_outside_voting_phase(self, app, db, voter, voting, phase):
        t = voting()
        p = t.proposal
        p.phase = phase
        db.session.commit()
        assert _vote(app, voter(), t, 'voor').status_code == 403
        assert 'gu-vote__choices' not in _page(app, voter(), t)

    def test_public_proposal_closes_on_date(self, app, db, voter, make_proposal):
        t = make_proposal(started_minutes_ago=150)  # stemmen loopt tot +180 min
        _req(_client(app), 'GET', f'/forum/{t.id}')
        assert _proposal(t.id).phase == 'stemmen'
        anna = voter()
        assert _vote(app, anna, t, 'voor').status_code == 302
        p = _proposal(t.id)
        p.stemmen_end = datetime.utcnow() - timedelta(minutes=1)
        db.session.commit()
        assert _vote(app, voter(), t, 'tegen').status_code == 403  # na de einddatum
        p = _proposal(t.id)
        assert p.phase == 'vervolg' and (p.result, p.votes_for, p.votes_against) == ('aangenomen', 1, 0)


class TestTally:
    def test_hidden_tally_shows_only_number_of_voters(self, app, db, voter, voting):
        t = voting(live=False)
        html = _page(app, None, t)
        assert 'Er is nog niet gestemd.' in html
        _vote(app, voter(), t, 'voor')
        assert 'Eén persoon heeft al gestemd.' in _page(app, None, t)
        _vote(app, voter(), t, 'tegen')
        _vote(app, voter(), t, 'onthouding')
        html = _page(app, None, t)
        assert '3 mensen hebben al gestemd.' in html and 'De verdeling volgt als het stemmen sluit.' in html
        assert 'gu-vote__count' not in html and 'Tussenstand<' not in html

    def test_live_tally_shows_distribution(self, app, db, voter, voting):
        t = voting(live=True)
        for choice in ('voor', 'voor', 'tegen', 'onthouding'):
            _vote(app, voter(), t, choice)
        html = _page(app, None, t)
        tally = html[html.index('gu-vote__tally'):]
        assert 'Tussenstand' in tally
        assert '<span>Voor</span> <strong>2</strong>' in tally
        assert '<span>Tegen</span> <strong>1</strong>' in tally
        assert '<span>Onthouding</span> <strong>1</strong>' in tally
        assert '66,7% voor van de geldige stemmen' in tally
        en = _page(app, None, t, 'en')
        assert '66.7% in favour of the valid votes' in en


class TestResult:
    @pytest.mark.parametrize('votes,threshold,result,pct', [
        ({'voor': 3, 'tegen': 2, 'onthouding': 4}, 50, 'aangenomen', 60.0),
        ({'voor': 3, 'tegen': 2, 'onthouding': 0}, 60, 'verworpen', 60.0),   # precies de drempel: niet meer dan
        ({'voor': 2, 'tegen': 2, 'onthouding': 5}, 50, 'verworpen', 50.0),
        ({'voor': 0, 'tegen': 0, 'onthouding': 3}, 50, 'verworpen', None),   # geen geldige stemmen
        ({'voor': 7, 'tegen': 3, 'onthouding': 0}, 69, 'aangenomen', 70.0),
    ])
    def test_result_against_threshold_without_abstentions(self, app, db, voter, voting, votes, threshold, result, pct):
        t = voting(threshold=threshold)
        for choice, n in votes.items():
            for _ in range(n):
                _vote(app, voter(), t, choice)
        p = _proposal(t.id)
        proposals.finalize(p)
        db.session.commit()
        p = _proposal(t.id)
        assert (p.result, p.result_pct) == (result, pct)
        assert (p.votes_for, p.votes_against, p.votes_abstain) == (votes['voor'], votes['tegen'], votes['onthouding'])
        assert p.eligible_count >= sum(votes.values()) and p.result_at is not None

    def test_host_closes_voting_and_full_result_is_shown(self, app, db, make_user, voter, voting):
        host = make_user()
        t = voting(live=False, host=host)
        for choice in ('voor', 'voor', 'voor', 'tegen', 'tegen', 'onthouding'):
            _vote(app, voter(), t, choice)
        assert _req(_client(app, host), 'POST', f'/forum/{t.id}/voorstel/fase',
                    data={'direction': 'volgende'}).status_code == 302
        p = _proposal(t.id)
        assert p.phase == 'vervolg' and p.result == 'aangenomen' and p.result_pct == 60.0
        html = _page(app, None, t)
        result = html[html.index('id="uitslag"'):]
        result = result[:result.index('</div>')]
        assert 'Uitslag' in result and '>Aangenomen<' in result
        assert '60% voor, 40% tegen (van de geldige stemmen)' in result
        assert '<span>Voor</span> <strong>3</strong>' in result and '<span>Onthouding</span> <strong>1</strong>' in result
        assert 'Uitgebrachte stemmen: 6' in result
        assert f'Opkomst: 6 van {p.eligible_count} stemgerechtigden' in result
        assert 'Drempel: meer dan 50% voor' in result
        # Na sluiten staat de stem vast.
        assert _vote(app, voter(), t, 'tegen').status_code == 403
        en = _page(app, None, t, 'en')
        assert '>Adopted<' in en and '60% for, 40% against (of the valid votes)' in en

    def test_rejected_and_back_to_voting(self, app, db, make_user, voter, voting):
        host = make_user()
        t = voting(host=host)
        anna = voter()
        _vote(app, anna, t, 'tegen')
        _req(_client(app, host), 'POST', f'/forum/{t.id}/voorstel/fase', data={'direction': 'volgende'})
        assert _proposal(t.id).result == 'verworpen'
        assert '>Verworpen<' in _page(app, None, t)
        # De host zet het stemmen weer open: de uitslag vervalt, de stemmen blijven.
        _req(_client(app, host), 'POST', f'/forum/{t.id}/voorstel/fase', data={'direction': 'vorige'})
        p = _proposal(t.id)
        assert p.phase == 'stemmen' and p.result is None and p.votes_for is None
        assert _vote(app, anna, t, 'voor').status_code == 302
        _req(_client(app, host), 'POST', f'/forum/{t.id}/voorstel/fase', data={'direction': 'volgende'})
        assert _proposal(t.id).result == 'aangenomen'

    @pytest.mark.parametrize('choices,status,label_nl,label_en', [
        (('voor', 'voor', 'tegen'), 'aangenomen', 'Aangenomen', 'Adopted'),
        (('voor', 'tegen', 'tegen'), 'verworpen', 'Verworpen', 'Rejected'),
    ])
    def test_status_label_shows_result(self, app, db, make_user, voter, voting, choices, status, label_nl, label_en):
        host = make_user()
        t = voting(host=host)
        for choice in choices:
            _vote(app, voter(), t, choice)
        _req(_client(app, host), 'POST', f'/forum/{t.id}/voorstel/fase', data={'direction': 'volgende'})
        label = f'gu-proposal-status--{status}">{label_nl}</span>'
        page = _page(app, None, t)
        assert label in page and '>Gesloten<' not in page
        assert f'gu-proposal-status--{status}">{label_en}</span>' in _page(app, None, t, 'en')
        index = _req(_client(app), 'GET', '/forum').get_data(as_text=True)
        row = index[index.index(f'/forum/{t.id}"'):]
        assert label in row[:row.index('</li>')]
        # De volledige uitslag blijft op de voorstelpagina.
        assert 'id="uitslag"' in page and 'Uitgebrachte stemmen: 3' in page

    def test_discussion_stays_open_while_voting(self, app, db, make_user, voting):
        t = voting()
        _req(_client(app, make_user()), 'POST', f'/forum/{t.id}', data={'body': 'Reactie tijdens het stemmen'})
        assert Post.query.filter_by(thread_id=t.id, body='Reactie tijdens het stemmen').count() == 1


class TestMigration:
    COLUMNS = ('result', 'result_pct', 'votes_for', 'votes_against', 'votes_abstain', 'eligible_count', 'result_at')

    def test_columns_added_idempotently(self, app, db):
        from app import _migrate_columns
        db.session.close()
        with db.engine.connect() as conn:
            for col in self.COLUMNS:
                conn.execute(text(f'ALTER TABLE proposals DROP COLUMN {col}'))
            conn.commit()
        db.engine.dispose()
        for _ in range(2):
            _migrate_columns()
            cols = {c['name'] for c in sa_inspect(db.engine).get_columns('proposals')}
            assert set(self.COLUMNS) <= cols
        insp = sa_inspect(db.engine)
        assert 'proposal_votes' in insp.get_table_names()
        assert any(set(u['column_names']) == {'proposal_id', 'user_id'} for u in insp.get_unique_constraints('proposal_votes'))
