"""
Forum fase C1: het kwaliteitssignaal (K1/K2/K3), app/quality.py en de
kwaliteitsroutes in app/routes/forum.py.

Dekt: een AI-suggestie komt in de wachtrij en wordt niet automatisch
toegekend; de AI oordeelt op de Engelse vertaling met stijl en spelling
uitgesloten, één keer per bijdrage; een moderator kent toe en wijst af; een
falende of weigerende AI blokkeert het plaatsen niet; melden en het besluit
met naam; de auteur ziet de reden en kan laten meekijken; kwaliteit staat los
van eens/oneens; niets op je eigen bijdrage; democontent en het verdwenen ▲.

De Anthropic-client is nep (geen echte API-aanroepen). Gedeelde conftest-app
(CSRF uit); _req() wist de in `g` gecachte current_user en locale.
"""
import json
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from flask import g
from flask_babel import refresh

from app import bcrypt, quality
from app.models import Post, PostLike, QualityAward, QualityReport, Thread, User


# ── Nep-Anthropic ────────────────────────────────────────────────────────

def _verdict(**over):
    data = {'has_source': False, 'source_is_new': False, 'argument_is_new': True,
            'suggested_criterion': 'K1', 'k3_possible': False,
            'explanation_nl': 'Voegt een nieuw argument toe.', 'explanation_en': 'Adds a new argument.'}
    data.update(over)
    return data


@pytest.fixture
def fake_ai(monkeypatch, app):
    state = {'translate': [], 'judge': [], 'verdict': _verdict(), 'fail': None, 'refuse': False}

    class Messages:
        def create(self, **kw):
            if 'output_config' not in kw:  # vertaling
                state['translate'].append(kw)
                text = kw['messages'][0]['content'].split('\n\n', 1)[1]
                return SimpleNamespace(content=[SimpleNamespace(type='text', text=f'EN: {text}')])
            state['judge'].append(kw)  # kwaliteitsbeoordeling
            if state['fail']:
                raise state['fail']
            if state['refuse']:
                return SimpleNamespace(stop_reason='refusal', content=[])
            return SimpleNamespace(stop_reason='end_turn',
                                   content=[SimpleNamespace(type='text', text=json.dumps(state['verdict']))])

    class FakeClient:
        def __init__(self, api_key=None, **kw):
            self.messages = Messages()

    import anthropic
    monkeypatch.setattr(anthropic, 'Anthropic', FakeClient)
    monkeypatch.setitem(app.config, 'QUALITY_AI_INLINE', True)
    return state


# ── Fixtures en helpers ──────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _fresh_rate_limits(app):
    # De gedeelde conftest-app houdt de limiter aan; de per-IP-limiet op
    # reageren (100/uur) telt over de hele testsessie. Hier niet relevant.
    from app import limiter
    limiter.reset()
    yield

@pytest.fixture
def make_user(db):
    created = []

    def _make(role='contributor'):
        name = f'q_{uuid.uuid4().hex[:8]}'
        u = User(username=name, email=f'{name}@example.com', verified=True,
                 password_hash=bcrypt.generate_password_hash('Sterk1!ww').decode('utf-8'))
        u.set_role(role)
        u.accept_rules()
        db.session.add(u)
        db.session.commit()
        created.append(u.id)
        return u

    yield _make
    for uid in created:
        for r in QualityReport.query.filter_by(reporter_id=uid):
            db.session.delete(r)
        for r in QualityReport.query.filter_by(decided_by_id=uid):
            r.decided_by_id = None
        for a in QualityAward.query.filter_by(decided_by_id=uid):
            a.decided_by_id = None
        for p in Post.query.filter_by(user_id=uid):
            p.user_id = None
        for like in PostLike.query.filter_by(user_id=uid):
            db.session.delete(like)
        u = db.session.get(User, uid)
        if u:
            db.session.delete(u)
    db.session.commit()


@pytest.fixture
def thread(db):
    t = Thread(eyebrow_nl='T', eyebrow_en='T', title_prefix_nl='Kwaliteit', title_prefix_en='Quality')
    db.session.add(t)
    db.session.flush()
    db.session.add(Post(thread_id=t.id, author_name='', author_username='op', body='Opening', is_op=True,
                        source_lang='nl', is_demo=True))
    db.session.commit()
    yield t
    # Via de ORM (niet bulk): anders houdt de sessie verouderde objecten
    # waarvan SQLite de ids daarna hergebruikt.
    ids = [p.id for p in Post.query.filter_by(thread_id=t.id)]
    for award in QualityAward.query.filter(QualityAward.post_id.in_(ids)):
        for r in award.reports:
            db.session.delete(r)
        db.session.delete(award)
    for like in PostLike.query.filter(PostLike.post_id.in_(ids)):
        db.session.delete(like)
    db.session.flush()
    for p in Post.query.filter_by(thread_id=t.id):
        db.session.delete(p)
    db.session.delete(t)
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


def _reply(app, user, thread, body):
    resp = _req(_client(app, user), 'POST', f'/forum/{thread.id}', data={'body': body})
    assert resp.status_code == 302
    return Post.query.filter_by(thread_id=thread.id, body=body).one()


def _decide(app, mod, post, action, criterion='K1', explanation=''):
    return _req(_client(app, mod), 'POST', f'/forum/kwaliteit/{post.id}/besluit',
                data={'action': action, 'criterion': criterion, 'explanation': explanation})


def _award(post_id):
    return QualityAward.query.filter_by(post_id=post_id).one_or_none()


# ── AI-suggestie en wachtrij ─────────────────────────────────────────────

class TestAiSuggestion:
    def test_suggestion_goes_to_queue_not_awarded(self, app, db, make_user, thread, fake_ai):
        anna, mod = make_user(), make_user('moderator')
        post = _reply(app, anna, thread, 'Een nieuw argument over handel.')
        award = _award(post.id)
        assert award.status == 'voorgesteld' and award.source == 'ai' and award.criterion == 'K1'
        assert award.decided_by_id is None and award.post_lang == 'nl'
        page = _req(_client(app), 'GET', f'/forum/{thread.id}').get_data(as_text=True)
        assert 'gu-quality__mark' not in page  # niet automatisch toegekend
        dash = _req(_client(app, mod), 'GET', '/moderator').get_data(as_text=True)
        assert 'Een nieuw argument over handel.' in dash and 'Voegt een nieuw argument toe.' in dash

    def test_judges_english_translation_with_style_excluded_once(self, app, db, make_user, thread, fake_ai):
        post = _reply(app, make_user(), thread, 'Dit is een Nederlandse bijdrage.')
        assert len(fake_ai['translate']) == 1 and 'English' in fake_ai['translate'][0]['messages'][0]['content']
        judge = fake_ai['judge'][0]
        assert 'EN: Dit is een Nederlandse bijdrage.' in judge['messages'][0]['content']
        assert 'spelling' in judge['system'] and 'fluency' in judge['system']
        assert judge['model'] == quality.JUDGE_MODEL
        assert judge['output_config']['format']['type'] == 'json_schema'
        assert 'extra_body' not in judge and 'betas' not in judge
        db.session.expire_all()
        assert db.session.get(Post, post.id).body_en == 'EN: Dit is een Nederlandse bijdrage.'
        # Eén keer per bijdrage: geen tweede API-aanroep.
        assert quality.judge_post(post.id) is None
        assert len(fake_ai['judge']) == 1

    def test_ai_cannot_award_k3_only_flag_it(self, app, db, make_user, thread, fake_ai):
        fake_ai['verdict'] = _verdict(suggested_criterion='none', k3_possible=True,
                                      explanation_nl='Geen nieuw argument, wel eerlijk.',
                                      explanation_en='No new argument, but fair.')
        post = _reply(app, make_user(), thread, 'Ik geef de tegenpartij gelijk op dit punt.')
        award = _award(post.id)
        assert award.criterion is None and award.ai_k3_possible is True and award.status == 'voorgesteld'

    def test_moderator_awards_and_declines(self, app, db, make_user, thread, fake_ai):
        anna, mod = make_user(), make_user('moderator')
        good = _reply(app, anna, thread, 'Goede bijdrage.')
        weak = _reply(app, anna, thread, 'Zwakke bijdrage.')
        assert _decide(app, mod, good, 'toekennen', 'K2').status_code == 302
        assert _decide(app, mod, weak, 'afwijzen', explanation='Geen bron voor de bewering.').status_code == 302
        db.session.expire_all()
        a, w = _award(good.id), _award(weak.id)
        assert (a.status, a.criterion, a.decided_by_id) == ('toegekend', 'K2', mod.id) and a.decided_at
        assert (w.status, w.explanation_nl, w.decided_by_id) == ('afgewezen', 'Geen bron voor de bewering.', mod.id)
        page = _req(_client(app), 'GET', f'/forum/{thread.id}').get_data(as_text=True)
        assert 'gu-quality__mark' in page and 'Onderbouwing met nieuw feit en bron' in page
        assert f'Bevestigd door @{mod.username}' in page

    def test_moderator_can_award_without_suggestion(self, app, db, make_user, thread):
        post = _reply(app, make_user(), thread, 'Zonder AI beoordeeld.')
        assert _award(post.id) is None
        _decide(app, make_user('moderator'), post, 'toekennen', 'K3', 'Eerlijk de andere kant benoemd.')
        db.session.expire_all()
        award = _award(post.id)
        assert (award.status, award.criterion, award.source) == ('toegekend', 'K3', 'moderator')

    def test_non_moderator_cannot_decide(self, app, db, make_user, thread):
        post = _reply(app, make_user(), thread, 'Iets.')
        assert _decide(app, make_user(), post, 'toekennen').status_code == 403
        assert _award(post.id) is None


# ── De AI faalt of weigert: plaatsen gaat altijd door ────────────────────

class TestAiFailure:
    @pytest.mark.parametrize('failure', [RuntimeError('API down'), TimeoutError('hangt')])
    def test_post_is_placed_without_suggestion(self, app, db, make_user, thread, fake_ai, failure):
        fake_ai['fail'] = failure
        post = _reply(app, make_user(), thread, f'Bijdrage terwijl de AI faalt {failure}.')
        assert post.id and _award(post.id) is None
        dash = _req(_client(app, make_user('moderator')), 'GET', '/moderator').get_data(as_text=True)
        assert post.body in dash  # bij "Zonder AI-suggestie", handmatig te beoordelen

    def test_refusal_gives_no_suggestion(self, app, db, make_user, thread, fake_ai):
        fake_ai['refuse'] = True
        post = _reply(app, make_user(), thread, 'Bijdrage die de AI weigert.')
        assert _award(post.id) is None

    def test_no_ai_calls_in_tests_without_inline_flag(self, app, db, make_user, thread, monkeypatch):
        called = []
        monkeypatch.setattr(quality, 'judge_post', lambda pid: called.append(pid))
        _reply(app, make_user(), thread, 'Geen achtergrondthread in tests.')
        assert called == []


# ── Melden ───────────────────────────────────────────────────────────────

class TestReporting:
    def _awarded(self, app, db, make_user, thread):
        anna, mod = make_user(), make_user('moderator')
        post = _reply(app, anna, thread, 'Toegekende bijdrage.')
        _decide(app, mod, post, 'toekennen', 'K1', 'Nieuw argument.')
        return anna, mod, post

    @pytest.mark.parametrize('decision,final_status', [('intrekken', 'afgewezen'), ('handhaven', 'toegekend')])
    def test_report_goes_to_moderator_and_decision_is_recorded(self, app, db, make_user, thread, decision, final_status):
        anna, mod, post = self._awarded(app, db, make_user, thread)
        reporter, mod2 = make_user(), make_user('moderator')
        client = _client(app, reporter)
        assert _req(client, 'POST', f'/forum/kwaliteit/{post.id}/melden').status_code == 302
        _req(client, 'POST', f'/forum/kwaliteit/{post.id}/melden')  # dubbel telt niet
        reports = QualityReport.query.filter_by(award_id=_award(post.id).id).all()
        assert len(reports) == 1 and reports[0].reporter_id == reporter.id and reports[0].decision is None
        page = _req(client, 'GET', f'/forum/{thread.id}').get_data(as_text=True)
        assert 'Je melding is ontvangen.' in page
        dash = _req(_client(app, mod2), 'GET', '/moderator').get_data(as_text=True)
        assert f'Gemeld door @{reporter.username}' in dash

        resp = _req(_client(app, mod2), 'POST', f'/forum/kwaliteit/melding/{reports[0].id}/besluit',
                    data={'decision': decision, 'note': 'Toch geen nieuw argument.'})
        assert resp.status_code == 302
        db.session.expire_all()
        r = db.session.get(QualityReport, reports[0].id)
        assert (r.decision, r.decided_by_id) == (decision, mod2.id) and r.decided_at
        assert _award(post.id).status == final_status

    def test_cannot_report_own_or_unawarded(self, app, db, make_user, thread):
        anna, mod, post = self._awarded(app, db, make_user, thread)
        assert _req(_client(app, anna), 'POST', f'/forum/kwaliteit/{post.id}/melden').status_code == 403
        other = _reply(app, anna, thread, 'Niet toegekend.')
        assert _req(_client(app, make_user()), 'POST', f'/forum/kwaliteit/{other.id}/melden').status_code == 403

    def test_anonymous_cannot_report(self, app, db, make_user, thread):
        _, _, post = self._awarded(app, db, make_user, thread)
        resp = _req(_client(app), 'POST', f'/forum/kwaliteit/{post.id}/melden')
        assert resp.status_code == 302 and '/login' in resp.headers['Location']


# ── Auteur-ervaring ──────────────────────────────────────────────────────

class TestAuthorExperience:
    def test_author_sees_reason_and_can_ask_for_review(self, app, db, make_user, thread):
        anna, mod = make_user(), make_user('moderator')
        post = _reply(app, anna, thread, 'Bijdrage zonder bron.')
        _decide(app, mod, post, 'afwijzen', explanation='De bewering mist een bron.')
        own = _req(_client(app, anna), 'GET', f'/forum/{thread.id}').get_data(as_text=True)
        assert 'Niet aangemerkt als kwaliteitsbijdrage. De bewering mist een bron.' in own
        assert 'Laat een moderator meekijken' in own
        other = _req(_client(app, make_user()), 'GET', f'/forum/{thread.id}').get_data(as_text=True)
        assert 'De bewering mist een bron.' not in other

        assert _req(_client(app, anna), 'POST', f'/forum/kwaliteit/{post.id}/meekijken').status_code == 302
        db.session.expire_all()
        assert _award(post.id).review_requested_at is not None
        own = _req(_client(app, anna), 'GET', f'/forum/{thread.id}').get_data(as_text=True)
        assert 'Een moderator kijkt nog een keer mee.' in own
        dash = _req(_client(app, make_user('moderator')), 'GET', '/moderator').get_data(as_text=True)
        assert 'De auteur vraagt om een tweede blik.' in dash

    def test_only_author_can_ask_for_review(self, app, db, make_user, thread):
        anna, mod = make_user(), make_user('moderator')
        post = _reply(app, anna, thread, 'Afgewezen bijdrage.')
        _decide(app, mod, post, 'afwijzen', explanation='Reden.')
        assert _req(_client(app, make_user()), 'POST', f'/forum/kwaliteit/{post.id}/meekijken').status_code == 403

    def test_awarded_shows_filled_diamond_with_detail(self, app, db, make_user, thread):
        anna, mod = make_user(), make_user('moderator')
        post = _reply(app, anna, thread, 'Kwaliteit.')
        _decide(app, mod, post, 'toekennen', 'K1', 'Nieuw argument in dit gesprek.')
        html = _req(_client(app, lang='en'), 'GET', f'/forum/{thread.id}').get_data(as_text=True)
        start = html.index(f'id="post-{post.id}"')
        part = html[start:html.find('<article', start)]
        assert 'gu-quality__mark' in part and '<details class="gu-quality">' in part
        assert 'Quality contribution: New argument' in part and 'Nieuw argument in dit gesprek.' in part
        for not_allowed in ('✓', '★', '👍', '&#10003;'):
            assert not_allowed not in part


# ── Los van eens/oneens en niet op je eigen bijdrage ─────────────────────

class TestIndependenceAndOwnPosts:
    def test_quality_and_stance_do_not_affect_each_other(self, app, db, make_user, thread):
        anna, bram, mod = make_user(), make_user(), make_user('moderator')
        post = _reply(app, anna, thread, 'Onafhankelijk.')
        _req(_client(app, bram), 'POST', f'/forum/stance/{post.id}', data={'stance': 'oneens'})
        before = [(r.user_id, r.stance) for r in PostLike.query.filter_by(post_id=post.id)]
        _decide(app, mod, post, 'toekennen', 'K1')
        assert [(r.user_id, r.stance) for r in PostLike.query.filter_by(post_id=post.id)] == before
        _req(_client(app, bram), 'POST', f'/forum/stance/{post.id}', data={'stance': 'eens'})
        db.session.expire_all()
        award = _award(post.id)
        assert (award.status, award.criterion) == ('toegekend', 'K1')
        assert PostLike.query.filter_by(post_id=post.id).one().stance == 'eens'

    def test_moderator_cannot_decide_own_post(self, app, db, make_user, thread):
        mod = make_user('moderator')
        post = _reply(app, mod, thread, 'Van de moderator zelf.')
        assert _decide(app, mod, post, 'toekennen').status_code == 403
        page = _req(_client(app, mod), 'GET', f'/forum/{thread.id}').get_data(as_text=True)
        start = page.index(f'id="post-{post.id}"')
        assert 'gu-quality-mod' not in page[start:page.find('<article', start)]


# ── Democontent en opruimen ──────────────────────────────────────────────

class TestDemoAndCleanup:
    def test_demo_awards_visible_and_vote_count_gone(self, app):
        quality.seed_demo_awards()
        html = _req(_client(app), 'GET', '/forum/3').get_data(as_text=True)
        assert 'gu-quality__mark' in html
        assert 'gu-post__votes' not in html and '▲' not in html

    def test_seed_demo_awards_is_idempotent(self, app):
        quality.seed_demo_awards()
        n = QualityAward.query.filter_by(source='demo').count()
        quality.seed_demo_awards()
        assert QualityAward.query.filter_by(source='demo').count() == n >= 1
