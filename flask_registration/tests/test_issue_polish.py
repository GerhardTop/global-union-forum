"""
Polijstronde van het vraagstuk-model (P1 tot en met P6), bovenop bouwstap 1
tot en met 7.

Dekt per onderdeel:
- P1: het menu-item "Denk mee", het overzicht met filters en aantallen, het
  kruimelpad, de term "samenvattend voorstel", de namen van de
  feitenverzameling, en LinkedIn als optioneel profielveld.
- P2: het AI-kwaliteitsmerk op feiten (alle vijf punten nodig, geen keurmerk,
  de prompt sluit betrouwbaarheid uit), het media-label en -type, en het
  sorteren met het merk eerst.

Gedeelde conftest-app (CSRF uit).
"""
import uuid
from datetime import datetime, timedelta

import pytest
from flask import g
from flask_babel import refresh

from app import bcrypt, issue_lifecycle as lifecycle, issue_proposals as ip, issues
from app.models import (Issue, IssueProposal, IssueProposalFact, IssueProposalSupport, IssueProposalVote, Post,
                        Thread, User)
from tests.test_forum_conduct import _fresh_rate_limits, _verdict, fake_ai  # noqa: F401  (autouse)


@pytest.fixture
def make_user(db):
    made = []

    def _make(role='contributor', admin=False):
        name = f'p_{uuid.uuid4().hex[:8]}'
        u = User(username=name, email=f'{name}@example.com', verified=True, is_admin=admin,
                 password_hash=bcrypt.generate_password_hash('Sterk1!ww').decode('utf-8'))
        u.set_role(role)
        u.accept_rules()
        db.session.add(u)
        db.session.commit()
        made.append(u.id)
        return u

    yield _make
    from app import quality
    from app.routes.forum import _delete_thread
    db.session.rollback()
    issue_ids = [i for (i,) in db.session.query(Issue.id).filter(Issue.created_by_id.in_(made))]
    proposal_ids = [p for (p,) in db.session.query(IssueProposal.id).filter(IssueProposal.issue_id.in_(issue_ids))] \
        if issue_ids else []
    if proposal_ids:
        for model in (IssueProposalVote, IssueProposalFact, IssueProposalSupport):
            model.query.filter(model.proposal_id.in_(proposal_ids)).delete(synchronize_session=False)
        IssueProposal.query.filter(IssueProposal.id.in_(proposal_ids)).delete(synchronize_session=False)
    db.session.commit()
    for tid in [t.id for t in Thread.query.join(Post).filter(Post.user_id.in_(made)).distinct()]:
        _delete_thread(db.session.get(Thread, tid))
    if issue_ids:
        Issue.query.filter(Issue.id.in_(issue_ids)).delete(synchronize_session=False)
    db.session.commit()
    for uid in made:
        quality.detach_user(uid)
        db.session.delete(db.session.get(User, uid))
    db.session.commit()
    db.session.expunge_all()  # SQLite hergebruikt id's; geen achtergebleven objecten


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


def _get(app, path, user=None, lang='nl'):
    return _req(_client(app, user, lang), 'GET', path).get_data(as_text=True)


def _issue(db, user, title='Een vraagstuk', **values):
    issue = issues.create({'title': title, 'intro': 'Toelichting.', 'kind': 'online', **values}, user)
    db.session.commit()
    return issue


def _fact(db, issue, user, body='Een feit.', direction='voor', source='https://example.org/bron'):
    from app.models import FACT_KIND
    fact = Post(thread_id=issue.thread_id, user_id=user.id, author_name='', author_username=user.username,
                body=body, source_lang='nl', kind=FACT_KIND, fact_direction=direction, fact_source=source)
    db.session.add(fact)
    db.session.commit()
    return fact


# ── P1: naamgeving, navigatie, overzicht, termen, profiel ────────────────

def test_overview_filters_and_counts(app, db, make_user):
    host, other = make_user(), make_user()
    online = _issue(db, host, 'Online vraag')
    event = issues.create({'title': 'Event vraag', 'intro': 'Uitleg.', 'kind': 'event',
                           'event_date': datetime(2026, 12, 1, 19, 0), 'event_location': 'Buurthuis'}, host)
    done = _issue(db, host, 'Afgeronde vraag')
    done.phase = 'vervolg'
    db.session.commit()
    ip.create(online, other, 'Een voorstel.')
    ip.create(online, host, 'Nog een voorstel.')
    db.session.commit()

    page = _get(app, '/gesprekken')
    row = page.split('Online vraag')[1].split('</li>')[0]
    assert '2 voorstellen' in row and '2 deelnemers' in row and 'Feiten en discussie' in row
    assert 'Buurthuis' in page and 'Nieuw vraagstuk' not in page  # niet ingelogd: geen knop
    assert 'Event vraag' not in _get(app, '/gesprekken?filter=online')
    assert 'Online vraag' not in _get(app, '/gesprekken?filter=event')
    assert 'Afgeronde vraag' not in _get(app, '/gesprekken?filter=lopend')
    finished = _get(app, '/gesprekken?filter=afgerond')
    assert 'Afgeronde vraag' in finished and 'Online vraag' not in finished
    assert 'Nieuw vraagstuk' in _get(app, '/gesprekken', host)
    assert event.id and 'Have your say' in _get(app, '/gesprekken?filter=onzin', lang='en')


def test_breadcrumb_names_the_issue(app, db, make_user):
    host = make_user()
    issue = _issue(db, host, 'Wat doen we met het plein?')
    proposal = ip.create(issue, host, 'Bomen planten.')
    db.session.commit()
    cockpit = _get(app, f'/vraagstuk/{issue.id}')
    crumbs = cockpit.split('gu-forum__crumbs gu-issue-crumbs')[1].split('</nav>')[0]
    assert '>Denk mee</a>' in crumbs and 'aria-current="page"' in crumbs and 'Wat doen we met het plein?' in crumbs
    for path in (f'/vraagstuk/{issue.id}/feiten', f'/vraagstuk/{issue.id}/voorstellen',
                 f'/vraagstuk/{issue.id}/voorstel/{proposal.id}'):
        crumbs = _get(app, path).split('gu-forum__crumbs gu-issue-crumbs')[1].split('</nav>')[0]
        assert '>Denk mee</a>' in crumbs and f'href="/vraagstuk/{issue.id}"' in crumbs
    assert 'Feiten, onderzoek en media' in _get(app, f'/vraagstuk/{issue.id}/feiten')


def test_summary_proposal_wording(app, db, make_user):
    host = make_user()
    issue = _issue(db, host)
    issue.phase = 'vervolg'
    db.session.commit()
    nl = _get(app, f'/vraagstuk/{issue.id}', host)
    assert 'Samenvattend voorstel' in nl and 'ltiem' not in nl
    en = _get(app, '/gesprekken/nieuw', host, 'en')
    assert 'End with a summary proposal' in en and 'ltimate' not in en
    assert _req(_client(app, host), 'GET', f'/vraagstuk/{issue.id}/samenvattend-voorstel').status_code == 200


def test_linkedin_is_optional_in_profile(app, db, make_user):
    user = make_user()
    user.linkedin_url = 'https://www.linkedin.com/in/iemand'
    db.session.commit()
    page = _get(app, '/profile', user)
    assert 'LinkedIn' in page and 'optioneel' in page.split('for="linkedin_url"')[1].split('</label>')[0]
    r = _req(_client(app, user), 'POST', '/profile', data={'action': 'linkedin', 'linkedin_url': ''},
             headers={'X-Requested-With': 'XMLHttpRequest'})
    assert r.status_code == 200 and r.get_json()['ok']
    db.session.refresh(user)
    assert user.linkedin_url is None


# ── P2: het kwaliteitsmerk op feiten en media ────────────────────────────

FACT_OK = dict(fact_has_source=True, fact_link_findable=True, fact_has_location=True, fact_on_topic=True,
               fact_is_new=True)


def _add_fact(app, user, issue, text, source='https://cbs.example/rapport#tabel-3', kind='feit'):
    return _req(_client(app, user), 'POST', f'/forum/{issue.thread_id}/feiten',
                data={'text': text, 'direction': 'voor', 'source': source, 'type': kind,
                      'return_to': str(issue.id)})


def test_fact_gets_the_mark_when_all_points_hold(app, db, make_user, fake_ai):
    from app.models import QualityAward
    from app.status import quality_counts
    host, anna = make_user(), make_user()
    issue = _issue(db, host)
    fake_ai['verdict'] = _verdict(**FACT_OK)
    _add_fact(app, anna, issue, 'Het verkeer daalde met 30 procent.')
    fact = Post.query.filter_by(body='Het verkeer daalde met 30 procent.').one()
    award = QualityAward.query.filter_by(post_id=fact.id).one()
    assert award.fact_mark and award.status == 'afgewezen' and award.criterion is None
    assert quality_counts(anna.id) == (0, 0)  # het merk is geen keurmerk
    call = fake_ai['judge'][-1]
    content = call['messages'][0]['content']
    assert 'Do NOT judge whether the source is reliable' in content and 'fact_has_location' in content
    assert 'fact_is_new' in call['output_config']['format']['schema']['required']
    page = _get(app, f'/vraagstuk/{issue.id}/feiten')
    item = page.split(f'id="post-{fact.id}"')[1].split('</li>')[0]
    assert 'gu-factmark' in item and 'Het merk zegt niets over hoe betrouwbaar de bron is.' in item
    cockpit = _get(app, f'/vraagstuk/{issue.id}')
    assert 'gu-factmark' in cockpit.split('id="panel-facts"')[1].split('id="panel-discussion"')[0]


def test_no_mark_when_a_point_fails_or_the_source_is_no_link(app, db, make_user, fake_ai):
    from app.models import QualityAward
    host, anna = make_user(), make_user()
    issue = _issue(db, host)
    fake_ai['verdict'] = _verdict(**{**FACT_OK, 'fact_has_location': False})
    _add_fact(app, anna, issue, 'Zonder vindplaats.')
    fake_ai['verdict'] = _verdict(**FACT_OK)  # de AI zegt ja, maar de bron is geen link
    _add_fact(app, anna, issue, 'Zonder link.', source='Jaarverslag 2024, blz. 12')
    for text in ('Zonder vindplaats.', 'Zonder link.'):
        fact = Post.query.filter_by(body=text).one()
        assert not QualityAward.query.filter_by(post_id=fact.id).one().fact_mark
    assert 'gu-factmark' not in _get(app, f'/vraagstuk/{issue.id}/feiten').split('<main')[1].split('id="toevoegen"')[0]


def test_ordinary_post_gets_no_fact_fields(app, db, make_user, fake_ai):
    from app.models import QualityAward
    host = make_user()
    issue = _issue(db, host)
    fake_ai['verdict'] = _verdict(argument_is_new=True, suggested_criterion='K1')
    _req(_client(app, host), 'POST', f'/forum/{issue.thread_id}', data={'body': 'Een gewone bijdrage.'})
    post = Post.query.filter_by(body='Een gewone bijdrage.').one()
    award = QualityAward.query.filter_by(post_id=post.id).one()
    assert award.fact_mark is None and award.status == 'toegekend'
    assert 'fact_is_new' not in fake_ai['judge'][-1]['output_config']['format']['schema']['properties']


def test_media_type_and_label(app, db, make_user):
    host, anna = make_user(), make_user()
    issue = _issue(db, host)
    _add_fact(app, anna, issue, 'Een podcast over het plein.', kind='media')
    _add_fact(app, anna, issue, 'Een onderzoek.', kind='onzin')
    media = Post.query.filter_by(body='Een podcast over het plein.').one()
    assert media.fact_type == 'media' and media.is_media
    assert Post.query.filter_by(body='Een onderzoek.').one().fact_type == 'feit'
    page = _get(app, f'/vraagstuk/{issue.id}/feiten', anna)
    assert 'gu-fact__media' in page.split(f'id="post-{media.id}"')[1].split('</li>')[0]
    assert 'Media: nieuws, podcast, video' in page  # het soort in het formulier
    _req(_client(app, anna), 'POST', f'/forum/feit/{media.id}/bewerken',
         data={'text': 'Een podcast over het plein.', 'direction': 'voor', 'source': 'https://x.example',
               'type': 'feit', 'return_to': str(issue.id)})
    db.session.refresh(media)
    assert media.fact_type == 'feit'
