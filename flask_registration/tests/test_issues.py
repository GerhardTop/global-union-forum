"""
Herontwerp, bouwstap 1: het vraagstuk (app/issues.py, het model Issue en de
routes in app/routes/issues.py).

Dekt: de backfill pakt een bestaand voorstel in een eigen vraagstuk en is
idempotent; een nieuw voorstel via het oude startformulier krijgt meteen een
vraagstuk; het verwijderen van het gesprek ruimt dat vraagstuk op; het
startformulier maakt een vraagstuk (online en event) en weigert ongeldige
invoer; de lijst, de vraagstukpagina en het menu-item Gesprekken.

Gedeelde conftest-app (CSRF uit).
"""
import uuid
from datetime import date, datetime, timedelta

import pytest
from flask import g
from flask_babel import refresh

from app import bcrypt, issues, proposals
from app.models import Issue, Post, Proposal, Thread, User


@pytest.fixture
def make_user(db):
    made = []

    def _make():
        name = f'i_{uuid.uuid4().hex[:8]}'
        u = User(username=name, email=f'{name}@example.com', verified=True,
                 password_hash=bcrypt.generate_password_hash('Sterk1!ww').decode('utf-8'))
        u.set_role('contributor')
        u.accept_rules()
        db.session.add(u)
        db.session.commit()
        made.append(u.id)
        return u

    yield _make
    from app import quality
    from app.routes.forum import _delete_thread
    for tid in [t.id for t in Thread.query.join(Post).filter(Post.user_id.in_(made)).distinct()]:
        _delete_thread(db.session.get(Thread, tid))
    Issue.query.filter(Issue.created_by_id.in_(made)).delete(synchronize_session=False)
    db.session.commit()
    for uid in made:
        quality.detach_user(uid)
        db.session.delete(db.session.get(User, uid))
    db.session.commit()


def _client(app, user=None, lang='nl'):
    c = app.test_client()
    with c.session_transaction() as s:
        s['lang'] = lang
        if user is not None:
            s['_user_id'] = str(user.id)
            s['_fresh'] = True
    return c


def _req(client, method, path, **kw):
    g.pop('_login_user', None)
    refresh()
    return client.open(path, method=method, **kw)


def _old_proposal(db, user, title, kind='publiek', minutes=None):
    """Een voorstel zoals het vóór het herontwerp bestond: zonder vraagstuk."""
    if kind == 'publiek' and minutes is None:
        minutes = {'feiten': 1440, 'bijstellen': 1440, 'stemmen': 1440}
    thread = Thread(eyebrow_nl='Forum · voorstel', eyebrow_en='Forum · proposal',
                    title_prefix_nl=title, title_prefix_en=title)
    db.session.add(thread)
    db.session.flush()
    proposal = proposals.create(thread, kind, user, minutes=minutes, threshold=60, show_live_tally=True)
    db.session.add(Post(thread_id=thread.id, user_id=user.id, author_name='', author_username=user.username,
                        body='Het eerste bericht.', source_lang='nl', is_op=True))
    db.session.commit()
    return proposal


def test_backfill_wraps_existing_proposal_and_is_idempotent(db, make_user):
    user = make_user()
    proposal = _old_proposal(db, user, 'Moet de stad autovrij?',
                             minutes={'feiten': 3 * 1440, 'bijstellen': 36 * 60, 'stemmen': 2 * 1440})
    assert proposal.issue_id is None

    assert issues.backfill() >= 1
    db.session.refresh(proposal)
    issue = proposal.issue
    assert issue is not None
    assert issue.title == 'Moet de stad autovrij?'
    assert issue.intro == 'Het eerste bericht.'
    assert issue.kind == 'online'
    assert issue.phase == 'feiten'
    assert issue.phase_deadline == proposal.feiten_end
    assert issue.voting_deadline == proposal.stemmen_end.date()
    assert (issue.days_facts, issue.days_adjust, issue.days_vote) == (3, 2, 2)  # 36 uur wordt 2 dagen
    assert issue.proposal_threshold_pct == 60
    assert issue.show_interim is True
    assert issue.created_by_id == user.id
    assert issue.lang == 'nl'
    assert proposal in issue.proposals

    assert issues.backfill() == 0
    assert Issue.query.filter_by(created_by_id=user.id).count() == 1


def test_backfill_event_proposal(db, make_user):
    user = make_user()
    proposal = _old_proposal(db, user, 'Een event', kind='event')
    issues.backfill()
    db.session.refresh(proposal)
    assert proposal.issue.kind == 'event'
    assert proposal.issue.phase_deadline is None
    assert proposal.issue.days_facts == 21


def test_old_start_form_wraps_new_proposal(app, db, make_user):
    user = make_user()
    r = _req(_client(app, user), 'POST', '/forum/nieuw',
             data={'title': 'Nieuw voorstel', 'body': 'Toelichting.', 'type': 'voorstel', 'kind': 'event'})
    assert r.status_code == 302
    proposal = Proposal.query.join(Thread).filter(Thread.title_prefix_nl == 'Nieuw voorstel').one()
    assert proposal.issue is not None
    assert proposal.issue.title == 'Nieuw voorstel'
    assert proposal.issue.intro == 'Toelichting.'


def test_deleting_the_thread_removes_its_wrapper_issue(db, make_user):
    from app.routes.forum import _delete_thread
    user = make_user()
    proposal = _old_proposal(db, user, 'Weg ermee')
    issues.backfill()
    issue_id = proposal.issue_id
    _delete_thread(proposal.thread)
    assert db.session.get(Issue, issue_id) is None


def test_new_issue_online(app, db, make_user):
    user = make_user()
    r = _req(_client(app, user), 'POST', '/gesprekken/nieuw', data={
        'title': 'Hoe verdelen we de ruimte?', 'intro': 'Context.', 'kind': 'online',
        'days_facts': '10', 'days_adjust': '3', 'days_vote': '4',
        'support_threshold': '8', 'proposal_threshold_pct': '66', 'final_vote': '1'})
    assert r.status_code == 302
    issue = Issue.query.filter_by(created_by_id=user.id).one()
    assert r.headers['Location'].endswith(f'/vraagstuk/{issue.id}')
    assert (issue.kind, issue.phase, issue.lang) == ('online', 'feiten', 'nl')
    assert (issue.days_facts, issue.days_adjust, issue.days_vote) == (10, 3, 4)
    assert (issue.support_threshold, issue.proposal_threshold_pct) == (8, 66)
    assert issue.show_interim is False and issue.final_vote is True
    assert issue.phase_deadline - issue.phase_started_at == timedelta(days=10)
    assert issue.voting_deadline == (issue.created_at + timedelta(days=17)).date()
    assert issue.event_date is None and issue.event_location is None


def test_new_issue_event(app, db, make_user):
    user = make_user()
    r = _req(_client(app, user), 'POST', '/gesprekken/nieuw', data={
        'title': 'Bijeenkomst over de haven', 'intro': 'Kom langs.', 'kind': 'event',
        'event_date': '2026-12-01T19:30', 'event_location': 'Buurthuis', 'voting_deadline': '2026-12-08'})
    assert r.status_code == 302
    issue = Issue.query.filter_by(created_by_id=user.id).one()
    assert issue.kind == 'event'
    assert issue.event_date == datetime(2026, 12, 1, 19, 30)
    assert issue.event_location == 'Buurthuis'
    assert issue.voting_deadline == date(2026, 12, 8)
    assert (issue.days_facts, issue.support_threshold, issue.proposal_threshold_pct) == (21, 5, 50)


@pytest.mark.parametrize('extra', [
    {'kind': 'event'},                                   # geen datum bij een event
    {'kind': 'online', 'days_facts': '0'},
    {'kind': 'online', 'proposal_threshold_pct': '40'},
    {'kind': 'online', 'support_threshold': 'veel'},
    {'kind': 'iets'},
    {'kind': 'online', 'title': ''},
])
def test_new_issue_rejects_invalid_input(app, db, make_user, extra):
    user = make_user()
    data = {'title': 'Een vraag', 'intro': 'Context.', **extra}
    r = _req(_client(app, user), 'POST', '/gesprekken/nieuw', data=data)
    assert r.status_code == 303
    assert Issue.query.filter_by(created_by_id=user.id).count() == 0


def test_new_issue_requires_login(app):
    r = _req(_client(app), 'GET', '/gesprekken/nieuw')
    assert r.status_code == 302 and '/login' in r.headers['Location']


def test_form_renders_in_both_languages(app, db, make_user):
    user = make_user()
    nl = _req(_client(app, user, 'nl'), 'GET', '/gesprekken/nieuw').get_data(as_text=True)
    en = _req(_client(app, user, 'en'), 'GET', '/gesprekken/nieuw').get_data(as_text=True)
    assert 'Nieuw vraagstuk starten' in nl and 'Stemregels' in nl
    assert 'Start a new issue' in en and 'Voting rules' in en


def test_list_and_issue_page(app, db, make_user):
    user = make_user()
    issue = issues.create({'title': 'Wat doen we met het plein?', 'intro': 'Uitleg.', 'kind': 'event',
                           'event_date': datetime(2026, 11, 20, 20, 0), 'event_location': 'Stadhuis'}, user)
    db.session.commit()

    page = _req(_client(app, lang='nl'), 'GET', '/gesprekken').get_data(as_text=True)
    assert 'Wat doen we met het plein?' in page
    assert 'Stadhuis' in page and 'Event' in page
    assert 'Fase: Feiten en discussie' in page

    show = _req(_client(app, lang='en'), 'GET', f'/vraagstuk/{issue.id}')
    assert show.status_code == 200
    body = show.get_data(as_text=True)
    assert 'Wat doen we met het plein?' in body
    assert 'Issue' in body and 'aria-current="step"' in body
    assert 'Uitleg.' in body


def test_issue_page_404(app):
    assert _req(_client(app), 'GET', '/vraagstuk/999999').status_code == 404


def test_nav_item_is_conversations(app):
    nl = _req(_client(app, lang='nl'), 'GET', '/gesprekken').get_data(as_text=True)
    en = _req(_client(app, lang='en'), 'GET', '/gesprekken').get_data(as_text=True)
    assert '>Gesprekken</a>' in nl and '>Forum</a>' not in nl
    assert '>Conversations</a>' in en
