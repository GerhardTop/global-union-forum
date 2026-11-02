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
- P3: de fase-indicator met iconen, de uitklapbare toelichting, volgen (ook
  in de meldingen), vergroten, de "+"-knoppen en "Posten".
- P4: het startformulier (planning in momenten, eventdagen, steundrempel 0,
  feiten bij het aanmaken, de bewaarde invoer), en dat bijdragen alleen
  kunnen als het platform open is.
- P5: het voorstelformulier (kopje Vraagstuk, "Voorstel posten", de
  steunregel alleen bij een drempel van minstens 1), meerdere bijstellingen
  met de versie-tijdlijn, reacties gekoppeld aan een versie, het merk bij
  gekoppelde feiten en de fase-chip.
- P7: de rustiger cockpit (geen kruimelpad of detailregel, de toelichting
  met een pijltje, geen tekst over de vroege sluiting, volgen met een
  belletje, "toon meer" alleen als er meer is), stemrecht alleen zonder
  stemrecht, de statusladder in het profiel, "Fasen beheren" alleen voor de
  initiatiefnemer (moderators via hun weergave), niet reageren op je eigen
  bericht, "Bekijk als deelnemer" en de korte regelnaam bij een markering.
- P6: de fase samenvatten tussen stemmen en vervolg (alleen met final_vote),
  de tik erdoorheen, de stemming over het samenvattende voorstel in die
  fase, de vervolgfase als nazorg en de migratie van een lopende stemming.

Gedeelde conftest-app (CSRF uit).
"""
import uuid
from datetime import datetime, timedelta

import pytest
from flask import g
from flask_babel import refresh

from app import bcrypt, issue_lifecycle as lifecycle, issue_proposals as ip, issues
from app.models import (Issue, IssueFollow, IssueProposal, IssueProposalFact, IssueProposalSupport,
                        IssueProposalVote, Post, Thread, User)
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
        from app.models import IssueProposalVersion  # polijstronde P5
        IssueProposalVersion.query.filter(IssueProposalVersion.proposal_id.in_(proposal_ids)).delete(
            synchronize_session=False)
        Post.query.filter(Post.issue_proposal_id.in_(proposal_ids)).update(
            {Post.issue_proposal_id: None}, synchronize_session=False)
        IssueProposal.query.filter(IssueProposal.id.in_(proposal_ids)).delete(synchronize_session=False)
    db.session.commit()
    for tid in [t.id for t in Thread.query.join(Post).filter(Post.user_id.in_(made)).distinct()]:
        _delete_thread(db.session.get(Thread, tid))
    IssueFollow.query.filter(IssueFollow.user_id.in_(made)).delete(synchronize_session=False)
    if issue_ids:
        from app.models import IssueEventDay
        IssueEventDay.query.filter(IssueEventDay.issue_id.in_(issue_ids)).delete(synchronize_session=False)
        IssueFollow.query.filter(IssueFollow.issue_id.in_(issue_ids)).delete(synchronize_session=False)
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
    # P7: de cockpit heeft geen kruimelpad meer, en de feiten- en voorstellenpagina
    # (de volledige weergave) ook niet; het voorstel-detail en de formulieren wel.
    for path in (f'/vraagstuk/{issue.id}', f'/vraagstuk/{issue.id}/feiten', f'/vraagstuk/{issue.id}/voorstellen'):
        assert 'gu-issue-crumbs' not in _get(app, path)
    for path in (f'/vraagstuk/{issue.id}/voorstel/{proposal.id}', f'/vraagstuk/{issue.id}/voorstellen/nieuw'):
        crumbs = _get(app, path, host).split('gu-forum__crumbs gu-issue-crumbs')[1].split('</nav>')[0]
        assert '>Denk mee</a>' in crumbs and f'href="/vraagstuk/{issue.id}"' in crumbs
    assert 'Feiten, onderzoek en media' in _get(app, f'/vraagstuk/{issue.id}/feiten')


def test_summary_proposal_wording(app, db, make_user):
    host = make_user()
    issue = _issue(db, host)
    issue.phase = 'samenvatten'
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


# ── P3: de cockpit ───────────────────────────────────────────────────────

def test_cockpit_head_intro_phases_and_plus_buttons(app, db, make_user):
    host = make_user()
    issue = _issue(db, host, 'Hoe maken we de wijk groener?')
    body = _get(app, f'/vraagstuk/{issue.id}', host)
    assert 'id="toelichting"' in body and 'Toelichting in- of uitklappen' in body and 'Toelichting.' in body
    assert body.count('gu-phasebar__icon') == 5 and 'aria-current="step"' in body  # P6: met samenvatten
    assert 'Volgen' in body and 'gu-cockpit__follow' in body
    head = body.split('id="panel-proposals"')[1].split('</h2>')[1].split('</div>')[0]
    assert 'class="gu-iconbtn"' in head and 'title="Nieuw voorstel"' in head
    facts_head = body.split('id="panel-facts"')[1].split('</div>')[0]
    assert 'title="Feit toevoegen"' in facts_head and 'data-expand="facts"' in facts_head
    assert 'data-expand="discussion"' in body and 'data-collapse' in body
    assert 'Posten' in body and 'Plaats reactie' not in body
    assert 'data-follow-url' not in _get(app, f'/vraagstuk/{issue.id}')  # niet ingelogd


def test_cockpit_renders_extra_rows_for_the_large_view(app, db, make_user):
    host, anna = make_user(), make_user()
    issue = _issue(db, host)
    for i in range(5):
        _fact(db, issue, anna, f'Feit {i}')
    for i in range(7):
        ip.create(issue, anna, f'Voorstel {i}')
    db.session.commit()
    body = _get(app, f'/vraagstuk/{issue.id}')
    facts = body.split('id="panel-facts"')[1].split('id="panel-discussion"')[0]
    assert facts.count('gu-cockpit__extra') == 2 and 'Feit 0' in facts
    proposals = body.split('id="panel-proposals"')[1].split('gu-cockpit__rights')[0]
    assert proposals.count('gu-cockpit__extra') == 2


def test_follow_toggles_and_followers_get_updates(app, db, make_user):
    from app import digest
    host, fan = make_user(), make_user()
    issue = _issue(db, host)
    c = _client(app, fan)
    r = _req(c, 'POST', f'/vraagstuk/{issue.id}/volgen', headers={'X-Requested-With': 'XMLHttpRequest'})
    assert r.get_json() == {'following': True}
    assert lifecycle.is_following(issue, fan) and fan.id in lifecycle.recipients(issue)
    assert fan.id not in lifecycle.participants(issue)
    assert 'aria-pressed="true"' in _get(app, f'/vraagstuk/{issue.id}', fan)
    # Een faseovergang komt in de samenvatting van de volger.
    lifecycle.set_phase(issue, 'bijstellen', datetime.utcnow())
    db.session.commit()
    fan.last_digest_at = datetime.utcnow() - timedelta(hours=1)
    db.session.commit()
    items = digest.build(fan, datetime.utcnow() + timedelta(minutes=1))
    assert any(item.get('issue_id') == issue.id for item in items)
    # Zonder JavaScript: terug naar de cockpit; nogmaals is ontvolgen.
    r = _req(c, 'POST', f'/vraagstuk/{issue.id}/volgen')
    assert r.status_code == 302 and r.headers['Location'].endswith(f'/vraagstuk/{issue.id}')
    assert not lifecycle.is_following(issue, fan)
    assert _req(_client(app), 'POST', f'/vraagstuk/{issue.id}/volgen').status_code == 302  # naar inloggen
    assert IssueFollow.query.filter_by(issue_id=issue.id).count() == 0


def test_deleting_a_user_removes_follows(app, db, make_user):
    from app import quality
    host, fan = make_user(), make_user()
    issue = _issue(db, host)
    lifecycle.toggle_follow(issue, fan)
    db.session.commit()
    quality.detach_user(fan.id)
    db.session.commit()
    assert IssueFollow.query.filter_by(issue_id=issue.id).count() == 0


# ── P4: het startformulier ───────────────────────────────────────────────

def _schedule(**over):
    data = {f'{name}_{part}': value for name, (day, time) in issues.default_schedule().items()
            for part, value in (('date', day), ('time', time))}
    data.update(over)
    return data


def test_start_form_layout(app, db, make_user):
    user = make_user()
    page = _get(app, '/gesprekken/nieuw', user)
    assert 'Toelichting' in page and 'eerste bericht' not in page.lower()
    for text in ('Feiten en discussie beginnen', 'Stemmen sluit', 'leeg is 24:00', 'Naam van het event', 'Dag toevoegen',
                 'Online bijdragen open tussen de eerste en laatste dag', 'Locatie', 'Afsluiten met een samenvattend voorstel',
                 'name="fact_0_text"', 'data-draft-key', 'beforeunload', 'min="0"'):
        assert text in page, text
    # De stemregels staan vlak in het formulier, niet in een eigen kader.
    assert '<legend>Stemregels</legend>' not in page and 'gu-newissue__h2">Stemregels' in page


def test_default_schedule_times():
    sched = issues.default_schedule(datetime(2027, 3, 1).date())
    assert sched['starts_at'] == ('2027-03-01', '00:00')
    assert sched['adjust_starts_at'] == ('2027-03-21', '')       # 21 dagen: tot en met 21 maart 24:00
    assert sched['vote_starts_at'] == ('2027-03-26', '') and sched['vote_ends_at'] == ('2027-04-02', '')


def test_support_threshold_zero_puts_a_proposal_on_the_list(app, db, make_user):
    host, anna = make_user(), make_user()
    r = _req(_client(app, host), 'POST', '/gesprekken/nieuw', data={
        'title': 'Nul steun', 'intro': 'Uitleg.', 'kind': 'online', 'support_threshold': '0', **_schedule()})
    assert r.status_code == 302
    issue = Issue.query.filter_by(created_by_id=host.id).one()
    assert issue.support_threshold == 0
    proposal = ip.create(issue, anna, 'Meteen op de lijst.')
    db.session.commit()
    assert ip.on_list(proposal, 0) and ip.status(proposal, 0)[0] == 'stemlijst'


def test_facts_can_be_added_when_starting(app, db, make_user):
    host = make_user()
    r = _req(_client(app, host), 'POST', '/gesprekken/nieuw', data={
        'title': 'Met feiten', 'intro': 'Uitleg.', 'kind': 'online', **_schedule(),
        'fact_0_text': 'Eerste feit.', 'fact_0_direction': 'voor', 'fact_0_source': 'https://a.example',
        'fact_1_text': 'Tweede feit.', 'fact_1_direction': 'tegen', 'fact_1_source': 'https://b.example',
        'fact_1_type': 'media'})
    assert r.status_code == 302
    issue = Issue.query.filter_by(created_by_id=host.id).one()
    found = Post.query.filter_by(thread_id=issue.thread_id, kind='feit').order_by(Post.id).all()
    assert [(f.body, f.fact_direction, f.fact_type) for f in found] == [
        ('Eerste feit.', 'voor', 'feit'), ('Tweede feit.', 'tegen', 'media')]
    # Een half ingevuld feit houdt het vraagstuk tegen, met de invoer bewaard.
    r = _req(_client(app, host), 'POST', '/gesprekken/nieuw', data={
        'title': 'Half feit', 'intro': 'Uitleg.', 'kind': 'online', **_schedule(), 'fact_0_text': 'Zonder bron.'})
    assert r.status_code == 303 and Issue.query.filter_by(title='Half feit').count() == 0


def test_event_platform_is_open_only_on_the_days(app, db, make_user):
    from app.models import IssueEventDay
    host, anna = make_user(), make_user()
    issue = issues.create({'title': 'Eventvraag', 'intro': 'Uitleg.', 'kind': 'event', 'event_name': 'Avond'}, host)
    db.session.flush()
    now = datetime.utcnow()
    issue.event_days.append(IssueEventDay(starts_at=now + timedelta(days=1), ends_at=now + timedelta(days=1, hours=3)))
    issue.event_days.append(IssueEventDay(starts_at=now + timedelta(days=3), ends_at=now + timedelta(days=3, hours=3)))
    db.session.commit()
    assert not issues.is_open_now(issue)
    assert issues.is_open_now(issue, now + timedelta(days=1, hours=1))
    assert not issues.is_open_now(issue, now + timedelta(days=2))
    issue.event_online_between = True
    db.session.commit()
    assert issues.is_open_now(issue, now + timedelta(days=2))
    issue.event_online_between = False
    db.session.commit()
    # Dicht: geen bericht, geen feit, geen voorstel; de cockpit zegt het.
    r = _req(_client(app, anna), 'POST', f'/forum/{issue.thread_id}', data={'body': 'Te vroeg.'})
    assert r.status_code == 302 and Post.query.filter_by(body='Te vroeg.').count() == 0
    assert not ip.can_propose(issue, anna)
    r = _req(_client(app, anna), 'POST', f'/forum/{issue.thread_id}/feiten',
             data={'text': 'Te vroeg feit.', 'direction': 'voor', 'source': 'https://x.example'})
    assert r.status_code == 403
    assert 'Het platform is nu niet open voor bijdragen' in _get(app, f'/vraagstuk/{issue.id}', anna)


def test_online_times_are_shown_in_dutch_time(app, db, make_user):
    host = make_user()
    r = _req(_client(app, host), 'POST', '/gesprekken/nieuw', data={
        'title': 'Tijden', 'intro': 'Uitleg.', 'kind': 'online', **_schedule(
            starts_at_date='2027-01-04', adjust_starts_at_date='2027-01-13', vote_starts_at_date='2027-01-16',
            vote_ends_at_date='2027-01-20')})
    issue = Issue.query.filter_by(created_by_id=host.id).one()
    page = _get(app, f'/vraagstuk/{issue.id}', host)
    assert '4 jan. 2027 00:00' in page or '4 jan 2027 00:00' in page
    assert r.status_code == 302


# ── P5: voorstel doen en voorstel-detail ─────────────────────────────────

def test_proposal_form_wording(app, db, make_user):
    host = make_user()
    issue = _issue(db, host, 'Waar komt het fietspad?')
    page = _get(app, f'/vraagstuk/{issue.id}/voorstellen/nieuw', host)
    head = page.split('gu-forum__threadhead')[1].split('</header>')[0]
    assert 'gu-newproposal__issuelabel">Vraagstuk' in head and 'Waar komt het fietspad?' in head
    assert 'Voorstel posten' in page and 'Voorstel indienen' not in page
    form = page.split('Voorstel posten')[1]
    assert 'steunbetuigingen komt het op de stemlijst' in form  # onder de knop
    issue.support_threshold = 0
    db.session.commit()
    assert 'komt het op de stemlijst' not in _get(app, f'/vraagstuk/{issue.id}/voorstellen/nieuw', host)


def test_versions_timeline_and_reactions_per_version(app, db, make_user):
    host, anna = make_user(), make_user()
    issue = _issue(db, host)
    proposal = ip.create(issue, host, 'Eerste stelling.', 'Uitleg een.')
    db.session.commit()
    assert [v.number for v in proposal.version_rows] == [1]
    detail = f'/vraagstuk/{issue.id}/voorstel/{proposal.id}'
    r = _req(_client(app, anna), 'POST', f'/forum/{issue.thread_id}',
             data={'body': 'Reactie op de eerste versie.', 'issue_proposal_id': proposal.id, 'return_proposal': '1'})
    first = Post.query.filter_by(body='Reactie op de eerste versie.').one()
    assert (first.issue_proposal_id, first.proposal_version) == (proposal.id, 1)
    assert r.headers['Location'].endswith(f'{detail}#post-{first.id}')

    issue.phase = 'bijstellen'
    db.session.commit()
    c = _client(app, host)
    for statement, explanation in (('Tweede stelling.', 'Uitleg een.'), ('Tweede stelling.', 'Uitleg drie.')):
        assert _req(c, 'POST', f'{detail}/bijstellen', data={'statement': statement, 'explanation': explanation}).status_code == 302
    db.session.refresh(proposal)
    assert ip.current_version(proposal) == 3
    _req(_client(app, anna), 'POST', f'/forum/{issue.thread_id}',
         data={'body': 'Reactie op versie drie.', 'issue_proposal_id': proposal.id})
    assert Post.query.filter_by(body='Reactie op versie drie.').one().proposal_version == 3

    page = _get(app, detail, anna)
    assert 'Fase: Voorstel bijstellen' in page and 'Versie 3' in page
    timeline = page.split('id="versies"')[1].split('id="reacties"')[0]
    assert timeline.index('Versie 3') < timeline.index('Versie 2') < timeline.index('Versie 1')
    assert 'Toelichting gewijzigd.' in timeline and 'Stelling gewijzigd.' in timeline
    assert '>Eerste stelling.</del>' in timeline
    reactions = page.split('id="reacties"')[1]
    assert 'op versie 1' in reactions and 'op versie 3' in reactions and 'Jouw reactie op versie 3' in reactions
    # In de gedeelde discussie staat de reactie met het voorstel en de versie.
    cockpit = _get(app, f'/vraagstuk/{issue.id}', anna)
    assert 'Bij voorstel' in cockpit and 'op versie 1' in cockpit


def test_reaction_cannot_point_to_a_proposal_of_another_issue(app, db, make_user):
    host, anna = make_user(), make_user()
    issue, other = _issue(db, host), _issue(db, host, 'Ander vraagstuk')
    foreign = ip.create(other, host, 'Elders.')
    db.session.commit()
    _req(_client(app, anna), 'POST', f'/forum/{issue.thread_id}', data={'body': 'Verkeerd.', 'issue_proposal_id': foreign.id})
    post = Post.query.filter_by(body='Verkeerd.').one()
    assert post.issue_proposal_id is None and post.proposal_version is None


def test_old_proposal_without_version_rows(app, db, make_user):
    from app.models import IssueProposalVersion
    host = make_user()
    issue = _issue(db, host)
    proposal = ip.create(issue, host, 'Oud.', 'Oude uitleg.')
    db.session.commit()
    IssueProposalVersion.query.filter_by(proposal_id=proposal.id).delete()
    proposal.original_statement, proposal.original_explanation = 'Heel oud.', 'Heel oude uitleg.'
    proposal.revised_at = datetime.utcnow()
    db.session.commit()
    db.session.expire_all()
    assert [v.statement for v in ip.versions(proposal)] == ['Heel oud.', 'Oud.']
    ip.revise(proposal, 'Nieuw.', None)
    db.session.commit()
    assert [(v.number, v.statement) for v in proposal.version_rows] == [(1, 'Heel oud.'), (2, 'Oud.'), (3, 'Nieuw.')]


def test_linked_facts_show_the_mark(app, db, make_user):
    from app.models import QualityAward
    host = make_user()
    issue = _issue(db, host)
    fact = _fact(db, issue, host, 'Gekoppeld feit.')
    db.session.add(QualityAward(post_id=fact.id, status='afgewezen', source='ai', fact_mark=True))
    proposal = ip.create(issue, host, 'Met basis.')
    db.session.flush()
    ip.link(proposal, fact, host)
    db.session.commit()
    page = _get(app, f'/vraagstuk/{issue.id}/voorstel/{proposal.id}')
    basis = page.split('id="basis"')[1].split('</section>')[0]
    assert 'Gekoppeld feit.' in basis and 'gu-factmark' in basis


# ── P6: het fasemodel ────────────────────────────────────────────────────

def test_phases_four_or_five(db, make_user):
    host = make_user()
    with_summary, without = _issue(db, host, final_vote=True), _issue(db, host, final_vote=False)
    assert with_summary.phases == ('feiten', 'bijstellen', 'stemmen', 'samenvatten', 'vervolg')
    assert without.phases == ('feiten', 'bijstellen', 'stemmen', 'vervolg')
    with_summary.phase = without.phase = 'stemmen'
    assert lifecycle.next_phase(with_summary) == 'samenvatten' and lifecycle.next_phase(without) == 'vervolg'


def test_tick_runs_through_the_summary_phase(db, make_user):
    host = make_user()
    start = datetime.utcnow() - timedelta(days=30)
    issue = issues.create({'title': 'Vijf fasen', 'intro': 'x', 'kind': 'online', 'days_facts': 2, 'days_adjust': 2,
                           'days_vote': 2, 'final_vote': True}, host, now=start)
    db.session.commit()
    lifecycle.advance_due(start + timedelta(days=6, hours=1))
    db.session.refresh(issue)
    # Na het stemmen: de opsteltijd (days_adjust) voor het samenvattende voorstel.
    assert issue.phase == 'samenvatten' and issue.phase_started_at == start + timedelta(days=6)
    assert issue.phase_deadline == start + timedelta(days=8)
    # Een samenvattend voorstel verlengt de fase tot het einde van de stemming erover.
    final = ip.create_final(issue, host, 'Samen.')
    db.session.flush()
    final.created_at = start + timedelta(days=7)
    lifecycle.summary_made(issue, final)
    db.session.commit()
    assert issue.phase_deadline == ip.final_voting_end(final) == start + timedelta(days=9)
    assert ip.voting_open(final, now=start + timedelta(days=8)) and not ip.voting_over(final, now=start + timedelta(days=8))
    lifecycle.advance_due(start + timedelta(days=9, minutes=1))
    db.session.refresh(issue)
    assert issue.phase == 'vervolg' and ip.voting_over(final)
    assert lifecycle.advance(issue, start + timedelta(days=40)) is False  # uit het vervolg niet verder
    assert issue.phase == 'vervolg'


def test_summary_phase_without_a_summary_proposal_ends_after_the_drafting_time(db, make_user):
    host = make_user()
    issue = _issue(db, host)
    lifecycle.set_phase(issue, 'samenvatten', datetime.utcnow() - timedelta(days=issue.days_adjust, minutes=5))
    db.session.commit()
    assert lifecycle.advance_due() >= 1
    db.session.refresh(issue)
    assert issue.phase == 'vervolg'


def test_follow_up_is_aftercare(app, db, make_user):
    host = make_user()
    issue = _issue(db, host)
    lifecycle.set_phase(issue, 'vervolg', datetime.utcnow())
    db.session.commit()
    page = _get(app, f'/vraagstuk/{issue.id}', host)
    assert 'De nazorg na het beraad: lobby, media, uitvoering.' in page
    assert 'Samenvattend voorstel doen' not in page  # het samenvattende voorstel hoort niet meer bij het vervolg
    assert not ip.can_make_final(issue, host)


def test_running_summary_vote_moves_to_its_own_phase(db, make_user):
    host = make_user()
    issue = _issue(db, host)
    issue.phase = 'vervolg'  # zoals vóór P6: het samenvattende voorstel in de vervolgfase
    final = ip.create_final(issue, host, 'Lopend.')
    db.session.commit()
    assert lifecycle.migrate_summary_phase() == 1
    db.session.refresh(issue)
    assert issue.phase == 'samenvatten' and issue.phase_deadline == ip.final_voting_end(final)
    assert ip.voting_open(final)
    assert lifecycle.migrate_summary_phase() == 0  # idempotent


# ── P7: rustiger cockpit, rechten en moderator-weergave ──────────────────

def test_cockpit_is_calmer(app, db, make_user):
    host = make_user()
    issue = _issue(db, host, 'Een rustige cockpit?')
    issue.phase_started_at = datetime.utcnow() - timedelta(days=1)
    db.session.commit()
    body = _get(app, f'/vraagstuk/{issue.id}', host)
    head = body.split('gu-cockpit__head')[1].split('gu-cockpit__phase')[0]
    assert 'gu-issue-crumbs' not in body and 'gu-issue-kind' not in head  # geen kruimelpad, geen detailregel
    assert 'Lees de toelichting' not in body and 'gu-cockpit__abouttoggle' in head
    assert 'sluit eerder' not in body  # geen tekst over de vroege sluiting
    assert 'gu-cockpit__bell' in head and 'Volgen' in head
    # De koppen staan boven het kader, net als bij de discussie.
    facts = body.split('id="panel-facts"')[1].split('id="panel-discussion"')[0]
    assert facts.index('gu-cockpit__panelhead') < facts.index('gu-cockpit__box')
    assert 'Alle feiten' not in body and 'Alle voorstellen' not in body


def test_show_more_only_when_there_is_more(app, db, make_user):
    host, anna = make_user(), make_user()
    issue = _issue(db, host)
    for i in range(3):
        _fact(db, issue, anna, f'Feit {i}')
    body = _get(app, f'/vraagstuk/{issue.id}')
    facts = body.split('id="panel-facts"')[1].split('id="panel-discussion"')[0]
    assert 'data-more ' not in facts and 'data-more-list' in facts
    _fact(db, issue, anna, 'Feit 3')
    facts = _get(app, f'/vraagstuk/{issue.id}').split('id="panel-facts"')[1].split('id="panel-discussion"')[0]
    assert 'gu-showmore' in facts and 'Toon meer' in facts


def test_voting_right_block_only_without_the_right(app, db, make_user):
    host, voter = make_user(), make_user('voter')
    issue = _issue(db, host)
    assert 'Jouw stemrecht' in _get(app, f'/vraagstuk/{issue.id}', host)
    assert 'Jouw stemrecht' not in _get(app, f'/vraagstuk/{issue.id}', voter)


def test_profile_shows_status_ladder_and_progress(app, db, make_user):
    newbie, voter = make_user(), make_user('voter')
    page = _get(app, '/profile', newbie)
    ladder = page.split('gu-statusladder"')[1].split('</div>\n    </div>')[0]
    for label in ('Lezer', 'Bijdrager', 'Stemmer', 'Moderator', 'Senior'):
        assert label in ladder
    assert 'aria-current="step"' in ladder and 'Op weg naar stemrecht: 0 van 3' in ladder and 'progressbar' in ladder
    voter_page = _get(app, '/profile', voter)
    assert 'Je hebt stemrecht.' in voter_page and 'Op weg naar stemrecht' not in voter_page


def test_manage_phases_only_for_the_initiator(app, db, make_user):
    host, mod = make_user(), make_user('moderator')
    issue = _issue(db, host)
    assert 'Fasen beheren' in _get(app, f'/vraagstuk/{issue.id}', host)
    assert 'Fasen beheren' not in _get(app, f'/vraagstuk/{issue.id}', mod)
    # Moderators doen het via hun eigen weergave.
    dash = _get(app, '/moderator', mod)
    section = dash.split('id="vraagstukken"')[1].split('</section>')[0]
    assert issue.title in section and 'Naar de volgende fase' in section
    r = _req(_client(app, mod), 'POST', f'/vraagstuk/{issue.id}/volgende-fase', data={'next': '/moderator#vraagstukken'})
    assert r.status_code == 302 and r.headers['Location'].endswith('/moderator#vraagstukken')
    db.session.refresh(issue)
    assert issue.phase == 'bijstellen'


def test_no_reply_to_your_own_post(app, db, make_user):
    host, anna = make_user(), make_user()
    issue = _issue(db, host)
    _req(_client(app, anna), 'POST', f'/forum/{issue.thread_id}', data={'body': 'Mijn reactie.'})
    own = Post.query.filter_by(body='Mijn reactie.').one()
    page = _get(app, f'/vraagstuk/{issue.id}', anna)
    post = page.split(f'id="post-{own.id}"')[1].split('</article>')[0]
    assert 'gu-post__reply-btn' not in post and f'name="parent_id" value="{own.id}"' not in page
    r = _req(_client(app, anna), 'POST', f'/forum/{issue.thread_id}',
             data={'body': 'Op mezelf.', 'parent_id': own.id, 'return_to': issue.id})
    assert Post.query.filter_by(body='Op mezelf.').count() == 0
    assert r.headers['Location'].endswith(f'/vraagstuk/{issue.id}#post-{own.id}')
    # Wel op andermans reactie.
    _req(_client(app, host), 'POST', f'/forum/{issue.thread_id}', data={'body': 'Op anna.', 'parent_id': own.id})
    assert Post.query.filter_by(body='Op anna.').one().parent_id == own.id


def _doubtful_post(db, issue, author):
    from app.models import ConductCase
    post = Post(thread_id=issue.thread_id, user_id=author.id, author_name='', author_username=author.username,
                body='Een twijfelachtige bijdrage.', source_lang='nl')
    db.session.add(post)
    db.session.flush()
    db.session.add(ConductCase(post_id=post.id, action='twijfel', rule='respect', source='ai',
                               explanation='Dit klinkt persoonlijk.', explanation_lang='nl'))
    db.session.commit()
    return post


def test_marking_shows_a_short_rule_name_folded_for_moderators(app, db, make_user):
    host, anna, mod = make_user(), make_user(), make_user('moderator')
    issue = _issue(db, host)
    post = _doubtful_post(db, issue, anna)
    page = _get(app, f'/vraagstuk/{issue.id}', mod)
    slot = page.split(f'id="conduct-{post.id}"')[1].split('gu-quality-slot')[0]
    assert '<details class="gu-conduct gu-conduct--twijfel gu-conduct--fold">' in slot
    head = slot.split('gu-conduct__head">')[1].split('</p>')[0]
    visible = __import__('re').sub(r'<[^>]+>', '', head)  # de volledige naam staat alleen in de tooltip
    assert visible == 'Gemarkeerd voor een moderator · Respect' and 'title="Over het idee, niet de persoon"' in head
    assert 'Over het idee, niet de persoon' in slot and 'Dit klinkt persoonlijk.' in slot


def test_view_as_participant_hides_moderator_notes(app, db, make_user):
    from app.models import ConductCase
    host, anna, mod = make_user(), make_user(), make_user('moderator')
    issue = _issue(db, host)
    post = _doubtful_post(db, issue, anna)
    moved = Post(thread_id=issue.thread_id, user_id=anna.id, author_name='', author_username=anna.username,
                 body='Een verplaatste bijdrage.', source_lang='nl')
    db.session.add(moved)
    db.session.flush()
    db.session.add(ConductCase(post_id=moved.id, action='verplaatst', rule='respect', source='ai'))
    db.session.commit()
    c = _client(app, mod)
    page = _req(c, 'GET', f'/vraagstuk/{issue.id}').get_data(as_text=True)
    assert 'gu-conduct--fold' in page and 'Een verplaatste bijdrage.' in page and 'gu-conduct-mod' in page
    assert 'Bekijk als deelnemer' in page and 'aria-checked="false"' in page
    r = _req(c, 'POST', '/weergave/deelnemer', data={'next': f'/vraagstuk/{issue.id}'})
    assert r.status_code == 302 and r.headers['Location'].endswith(f'/vraagstuk/{issue.id}')
    page = _req(c, 'GET', f'/vraagstuk/{issue.id}').get_data(as_text=True)
    assert 'gu-conduct' not in page.split('id="panel-discussion"')[1].split('id="composer"')[0].replace('gu-conduct-slot', '')
    assert 'Een verplaatste bijdrage.' not in page and 'aria-checked="true"' in page
    assert 'Een twijfelachtige bijdrage.' in page  # de bijdrage zelf blijft gewoon staan
    _req(c, 'POST', '/weergave/deelnemer', data={'next': '/gesprekken'})
    assert 'gu-conduct--fold' in _req(c, 'GET', f'/vraagstuk/{issue.id}').get_data(as_text=True)
    # Alleen voor moderators.
    assert _req(_client(app, anna), 'POST', '/weergave/deelnemer').status_code == 403


# ── Opmaak: de volledige pagina's en de FAQ ──────────────────────────────

def test_full_pages_match_the_cockpit(app, db, make_user):
    host = make_user()
    issue = _issue(db, host, 'Waar komt de markt?')
    for path, anchor in ((f'/vraagstuk/{issue.id}/feiten', '#panel-facts'),
                         (f'/vraagstuk/{issue.id}/voorstellen', '#panel-proposals')):
        page = _get(app, path, host)
        head = page.split('gu-forum__threadhead')[1].split('</header>')[0]
        assert 'gu-issue-crumbs' not in page and 'gu-issue-kind' not in head
        assert f'href="/vraagstuk/{issue.id}">Waar komt de markt?</a>' in head
        assert f'/vraagstuk/{issue.id}{anchor}' in page and 'Verkleinen' in page


def test_sort_and_filter_only_with_more_than_five(app, db, make_user):
    host, anna = make_user(), make_user()
    issue = _issue(db, host)
    for i in range(5):
        _fact(db, issue, anna, f'Feit {i}')
        ip.create(issue, anna, f'Voorstel {i}')
    db.session.commit()
    for path in (f'/vraagstuk/{issue.id}/feiten', f'/vraagstuk/{issue.id}/voorstellen'):
        page = _get(app, path)
        assert 'gu-factspage__sort' not in page and 'gu-factspage__filter' not in page
    _fact(db, issue, anna, 'Feit 5')
    ip.create(issue, anna, 'Voorstel 5')
    db.session.commit()
    for path in (f'/vraagstuk/{issue.id}/feiten', f'/vraagstuk/{issue.id}/voorstellen'):
        page = _get(app, path)
        assert 'gu-factspage__sort' in page and 'gu-factspage__filter' in page
    # Bij de voorstellen telt het aantal vóór het filter: ook een leeg filter houdt de knoppen.
    page = _get(app, f'/vraagstuk/{issue.id}/voorstellen?filter=stemlijst')
    assert 'gu-factspage__filter' in page


def test_faq_has_no_semicolons_and_uses_the_collection_name():
    from app import faq
    answers = {lang: [str(a) for _title, items in faq.FAQ[lang] for _q, a in items] for lang in ('nl', 'en')}
    for lang, texts in answers.items():
        questions = [str(q) for _title, items in faq.FAQ[lang] for q, _a in items]
        assert not any(';' in t for t in texts + questions), lang
    assert any(t.startswith('Feiten, onderzoek en media zijn onderbouwde bijdragen met een bron.') for t in answers['nl'])
    assert any(t.startswith('Facts, research and media are well-founded contributions with a source.') for t in answers['en'])
