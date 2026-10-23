"""
Forum fase C1 (herzien): automatisch kwaliteitskeurmerk met zichtbare prompt
(app/quality.py en de kwaliteitsroutes in app/routes/forum.py). Kwaliteit is
uitsluitend een beloning: zonder keurmerk blijft een bijdrage gewoon zichtbaar.

Dekt: het keurmerk verschijnt automatisch (Haiku 4.5) zonder moderatoractie;
zonder K1/K2 geen medaille, geen melding en geen reden, voor niemand; de
gebruikte prompt staat op de discussiepagina van een toegekend keurmerk; de AI
oordeelt één keer op de Engelse vertaling met stijl en spelling uitgesloten;
een moderator voegt een keurmerk handmatig toe of haalt het weg; er is geen
bezwaarstroom meer; faalt de AI, dan geen oordeel en de post blijft staan;
melden door anderen; los van eens/oneens; niets op je eigen bijdrage; de
migratie van oude 'voorgesteld'-rijen; democontent.

De Anthropic-client is nep (geen echte API-aanroepen). Gedeelde conftest-app
(CSRF uit); _req() wist de in `g` gecachte current_user en locale.
"""
import json
import uuid
from types import SimpleNamespace

import pytest
from flask import g
from flask_babel import refresh
from sqlalchemy import text

from app import bcrypt, quality
from app.models import Post, PostLike, QualityAward, QualityObjection, QualityReport, Thread, User


# ── Nep-Anthropic ────────────────────────────────────────────────────────

def _verdict(**over):
    data = {'has_source': False, 'source_is_new': False, 'argument_is_new': True,
            'suggested_criterion': 'K1', 'k3_possible': False,
            'explanation': 'Voegt een nieuw argument toe.'}
    data.update(over)
    return data


@pytest.fixture
def fake_ai(monkeypatch, app):
    state = {'translate': [], 'judge': [], 'verdict': _verdict(), 'fail': None, 'refuse': False}

    def _text(payload):
        return SimpleNamespace(stop_reason='end_turn',
                               content=[SimpleNamespace(type='text', text=json.dumps(payload))])

    class Messages:
        def create(self, **kw):
            if 'output_config' not in kw:  # vertaling
                state['translate'].append(kw)
                src = kw['messages'][0]['content'].split('\n\n', 1)[1]
                return SimpleNamespace(content=[SimpleNamespace(type='text', text=f'EN: {src}')])
            state['judge'].append(kw)  # kwaliteitsoordeel
            if state['fail']:
                raise state['fail']
            if state['refuse']:
                return SimpleNamespace(stop_reason='refusal', content=[])
            return _text(state['verdict'])

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
        for o in QualityObjection.query.filter((QualityObjection.author_id == uid) |
                                               (QualityObjection.decided_by_id == uid)):
            if o.author_id == uid:
                o.author_id = None
            if o.decided_by_id == uid:
                o.decided_by_id = None
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
        for r in list(award.reports) + list(award.objections):
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


def _award(post_id):
    return QualityAward.query.filter_by(post_id=post_id).one_or_none()


def _article(html, post):
    start = html.index(f'id="post-{post.id}"')
    ends = [i for i in (html.find('<article', start), html.find('<script', start)) if i != -1]
    return html[start:min(ends) if ends else len(html)]


def _mod_decide(app, mod, post, action, criterion='K2', explanation=''):
    """Moderator voegt een keurmerk handmatig toe of haalt het weg."""
    return _req(_client(app, mod), 'POST', f'/forum/kwaliteit/{post.id}/besluit',
                data={'action': action, 'criterion': criterion, 'explanation': explanation})


# ── Automatisch oordeel ──────────────────────────────────────────────────

class TestAutomaticJudgement:
    def test_judgement_appears_without_moderator(self, app, db, make_user, thread, fake_ai):
        post = _reply(app, make_user(), thread, 'Een nieuw argument over handel.')
        award = _award(post.id)
        assert (award.status, award.criterion, award.source) == ('toegekend', 'K1', 'ai')
        assert award.decided_by_id is None and award.model == 'claude-haiku-4-5' and award.post_lang == 'nl'
        part = _article(_req(_client(app), 'GET', f'/forum/{thread.id}').get_data(as_text=True), post)
        assert 'gu-quality__mark' in part and 'Automatisch beoordeeld door de AI.' in part
        assert f'/forum/kwaliteit/{post.id}' in part  # link naar de discussiepagina

    def test_no_criterion_stays_visible_without_medal_or_message(self, app, db, make_user, thread, fake_ai):
        fake_ai['verdict'] = _verdict(suggested_criterion='none', argument_is_new=False,
                                      explanation='Dit argument stond er al.')
        anna = make_user()
        post = _reply(app, anna, thread, 'Een herhaald argument.')
        assert _award(post.id).status == 'afgewezen'  # intern: geen keurmerk
        # Voor iedereen gewoon zichtbaar: anderen, anoniem, de auteur en moderators.
        mod = make_user('moderator')
        for viewer in (make_user(), None, anna, mod):
            html = _req(_client(app, viewer), 'GET', f'/forum/{thread.id}').get_data(as_text=True)
            part = _article(html, post)
            assert 'Een herhaald argument.' in part
            assert 'gu-quality__mark' not in part and 'gu-quality__medal' not in part
            for gone in ('afgewezen', 'Afgewezen', 'onvoldoende kwaliteit', 'bezwaar', 'verborgen'):
                assert gone not in part, (viewer, gone)
            # Geen melding en geen reden, ook niet voor de auteur. (Alleen het
            # beoordelingspaneel van een moderator toont de AI-bevindingen.)
            if viewer is not mod:
                assert 'Dit argument stond er al.' not in part

    def test_judges_english_translation_with_style_excluded_once(self, app, db, make_user, thread, fake_ai):
        post = _reply(app, make_user(), thread, 'Dit is een Nederlandse bijdrage.')
        judge = fake_ai['judge'][0]
        assert judge['model'] == 'claude-haiku-4-5'
        assert 'EN: Dit is een Nederlandse bijdrage.' in judge['messages'][0]['content']
        assert 'spelling' in judge['system'] and 'fluency' in judge['system']
        assert 'effort' not in judge['output_config'] and 'extra_body' not in judge
        assert quality.judge_post(post.id) is None and len(fake_ai['judge']) == 1

    def test_ai_only_flags_k3(self, app, db, make_user, thread, fake_ai):
        fake_ai['verdict'] = _verdict(suggested_criterion='none', k3_possible=True)
        post = _reply(app, make_user(), thread, 'Ik geef de tegenpartij gelijk op dit punt.')
        award = _award(post.id)
        assert award.criterion is None and award.ai_k3_possible is True and award.status == 'afgewezen'


# ── Taal: volledig in de taal van de bijdrage ────────────────────────────

NL_RULE = 'Schrijf je volledige antwoord in het Nederlands. Gebruik geen Engelse woorden of zinsdelen. Meng geen talen.'
EN_RULE = 'Write your entire response in English. Do not use Dutch words or phrases. Do not mix languages.'


def _reply_in(app, user, thread, body, lang):
    resp = _req(_client(app, user, lang=lang), 'POST', f'/forum/{thread.id}', data={'body': body})
    assert resp.status_code == 302
    return Post.query.filter_by(thread_id=thread.id, body=body).one()


class TestResponseLanguage:
    def test_dutch_post_gets_dutch_rule_and_dutch_explanation(self, app, db, make_user, thread, fake_ai):
        post = _reply_in(app, make_user(), thread, 'Een Nederlandse bijdrage.', 'nl')
        judge = fake_ai['judge'][0]
        assert judge['system'].startswith(NL_RULE) and EN_RULE not in judge['system']
        assert NL_RULE in judge['messages'][0]['content'] and 'in Dutch' in judge['messages'][0]['content']
        assert 'explanation_en' not in json.dumps(judge['output_config'])
        award = _award(post.id)
        assert (award.explanation_nl, award.explanation_en) == ('Voegt een nieuw argument toe.', None)
        assert award.prompt_text.startswith('[system]\n' + NL_RULE)  # regel ook in de getoonde prompt

    def test_english_post_gets_english_rule_and_english_explanation(self, app, db, make_user, thread, fake_ai):
        fake_ai['verdict'] = _verdict(explanation='Adds a new argument.')
        post = _reply_in(app, make_user(), thread, 'An English contribution.', 'en')
        judge = fake_ai['judge'][0]
        assert judge['system'].startswith(EN_RULE) and NL_RULE not in judge['system']
        assert 'in English' in judge['messages'][0]['content']
        award = _award(post.id)
        assert (award.explanation_nl, award.explanation_en, award.post_lang) == (None, 'Adds a new argument.', 'en')


# ── Vertaalknop op de discussiepagina (zelfde patroon als bij posts) ─────

class TestTranslateButtons:
    def _setup(self, app, make_user, thread, fake_ai):
        anna = make_user()
        return anna, _reply_in(app, anna, thread, 'Een Nederlandse bijdrage.', 'nl')

    @staticmethod
    def _buttons(html):
        import re
        return set(re.findall(r'class="gu-post__action gu-post__action--ghost gu-post__translate-btn"\s*'
                              r'data-target-id="([^"]+)"', html))

    def test_button_only_when_site_language_differs(self, app, db, make_user, thread, fake_ai):
        anna, post = self._setup(app, make_user, thread, fake_ai)
        award = _award(post.id)
        path = f'/forum/kwaliteit/{post.id}'
        other = _req(_client(app, make_user(), lang='en'), 'GET', path).get_data(as_text=True)
        assert self._buttons(other) == {f'expl-{award.id}'}
        same = _req(_client(app, make_user(), lang='nl'), 'GET', path).get_data(as_text=True)
        assert self._buttons(same) == set()
        # Net als bij posts alleen voor ingelogde lezers.
        assert self._buttons(_req(_client(app, lang='en'), 'GET', path).get_data(as_text=True)) == set()
        # De oorspronkelijke tekst staat er, in de taal van de bijdrage.
        assert f'id="expl-{award.id}" lang="nl"' in other and 'Voegt een nieuw argument toe.' in other
        assert 'el.textContent = p;' in other  # vertaling als platte tekst

    def test_translate_routes_cache_and_keep_original(self, app, db, make_user, thread, fake_ai):
        anna, post = self._setup(app, make_user, thread, fake_ai)
        reader = _client(app, make_user(), lang='en')
        resp = _req(reader, 'POST', f'/forum/kwaliteit/{post.id}/vertaal')
        assert resp.get_json() == {'translation': 'EN: Voegt een nieuw argument toe.'}
        n_calls = len(fake_ai['translate'])
        _req(reader, 'POST', f'/forum/kwaliteit/{post.id}/vertaal')  # uit de cache
        assert len(fake_ai['translate']) == n_calls
        db.session.expire_all()
        award = _award(post.id)
        assert award.explanation_nl == 'Voegt een nieuw argument toe.'  # origineel blijft
        assert award.explanation_en == 'EN: Voegt een nieuw argument toe.'  # alleen cache
        # De pagina toont nog steeds het origineel, met de knop.
        page = _req(reader, 'GET', f'/forum/kwaliteit/{post.id}').get_data(as_text=True)
        assert self._buttons(page) == {f'expl-{award.id}'}
        explanation = page[page.index(f'id="expl-{award.id}"'):page.index('gu-qthread__findings')]
        assert 'Voegt een nieuw argument toe.' in explanation and 'EN: ' not in explanation

    def test_no_translation_in_same_language_or_anonymous(self, app, db, make_user, thread, fake_ai):
        anna, post = self._setup(app, make_user, thread, fake_ai)
        assert _req(_client(app, make_user(), lang='nl'), 'POST', f'/forum/kwaliteit/{post.id}/vertaal').status_code == 400
        resp = _req(_client(app, lang='en'), 'POST', f'/forum/kwaliteit/{post.id}/vertaal')
        assert resp.status_code == 302 and '/login' in resp.headers['Location']


# ── Transparantie: prompt zichtbaar op de discussiepagina ────────────────

class TestPromptVisible:
    def test_prompt_shown_with_criterion_findings_and_explanation(self, app, db, make_user, thread, fake_ai):
        post = _reply(app, make_user(), thread, 'Bijdrage met een argument.')
        page = _req(_client(app), 'GET', f'/forum/kwaliteit/{post.id}').get_data(as_text=True)
        assert 'Gebruikte prompt' in page
        assert 'Judge the substance only.' in page  # uit de systeemprompt
        assert 'EN: Bijdrage met een argument.' in page  # de beoordeelde (vertaalde) tekst
        assert 'K1 Nieuw argument' in page and 'Voegt een nieuw argument toe.' in page
        assert 'Bron aanwezig' in page and 'claude-haiku-4-5' in page
        assert _award(post.id).prompt_text.startswith('[system]')

    def test_page_in_english(self, app, db, make_user, thread, fake_ai):
        post = _reply(app, make_user(), thread, 'English page.')
        page = _req(_client(app, lang='en'), 'GET', f'/forum/kwaliteit/{post.id}').get_data(as_text=True)
        # Pagina in het Engels; de uitleg staat in de taal van de bijdrage (NL).
        assert 'Prompt used' in page and 'Judgement by the AI' in page and 'Voegt een nieuw argument toe.' in page

    def test_no_page_without_quality_mark_for_anyone(self, app, db, make_user, thread, fake_ai):
        """Zonder keurmerk is er geen discussiepagina, ook niet voor de auteur
        of een moderator: er is geen reden om te tonen en geen bezwaar."""
        fake_ai['verdict'] = _verdict(suggested_criterion='none')
        anna = make_user()
        post = _reply(app, anna, thread, 'Geen keurmerk.')
        path = f'/forum/kwaliteit/{post.id}'
        for viewer in (None, make_user(), anna, make_user('moderator')):
            assert _req(_client(app, viewer), 'GET', path).status_code == 404
            assert _req(_client(app, viewer or make_user()), 'POST', f'{path}/vertaal').status_code == 404

    def test_no_judgement_no_page(self, app, db, make_user, thread):
        post = _reply(app, make_user(), thread, 'Nooit beoordeeld.')
        assert _req(_client(app), 'GET', f'/forum/kwaliteit/{post.id}').status_code == 404


# ── Moderator: keurmerk handmatig toevoegen of weghalen ─────────────────

class TestModeratorManual:
    def test_remove_mark_keeps_post_visible_without_medal(self, app, db, make_user, thread, fake_ai):
        anna, mod = make_user(), make_user('moderator')
        post = _reply(app, anna, thread, 'Eerst met keurmerk.')
        # Weghalen kan zonder reden: de auteur krijgt geen melding.
        assert _mod_decide(app, mod, post, 'afwijzen').status_code == 302
        db.session.expire_all()
        assert (_award(post.id).status, _award(post.id).decided_by_id) == ('afgewezen', mod.id)
        for viewer in (make_user(), anna):
            part = _article(_req(_client(app, viewer), 'GET', f'/forum/{thread.id}').get_data(as_text=True), post)
            assert 'Eerst met keurmerk.' in part and 'gu-quality__mark' not in part
        assert _req(_client(app), 'GET', f'/forum/kwaliteit/{post.id}').status_code == 404

    def test_add_mark_back_with_current_status_on_page(self, app, db, make_user, thread, fake_ai):
        fake_ai['verdict'] = _verdict(suggested_criterion='none')
        anna, mod = make_user(), make_user('moderator')
        post = _reply(app, anna, thread, 'Later alsnog met keurmerk.')
        assert _mod_decide(app, mod, post, 'toekennen', 'K2', 'Er is wel een bron.').status_code == 302
        db.session.expire_all()
        assert (_award(post.id).status, _award(post.id).criterion) == ('toegekend', 'K2')
        part = _article(_req(_client(app), 'GET', f'/forum/{thread.id}').get_data(as_text=True), post)
        assert 'gu-quality__mark' in part and 'Er is wel een bron.' in part
        page = _req(_client(app), 'GET', f'/forum/kwaliteit/{post.id}').get_data(as_text=True)
        current = page[page.index('Huidige stand'):page.index('id="oordeel"')]
        assert 'K2 Onderbouwing met nieuw feit en bron' in current and 'Er is wel een bron.' in current
        assert f'Door @{mod.username}' in current

    def test_moderator_rules(self, app, db, make_user, thread, fake_ai):
        mod = make_user('moderator')
        post = _reply(app, mod, thread, 'Bijdrage van de moderator zelf.')
        assert _mod_decide(app, mod, post, 'afwijzen').status_code == 403  # eigen bijdrage
        assert _mod_decide(app, make_user(), post, 'afwijzen').status_code == 403  # geen moderator
        controls = _req(_client(app, make_user('moderator')), 'GET', f'/forum/{thread.id}').get_data(as_text=True)
        assert 'Keurmerk weghalen' in controls and 'Afwijzen' not in controls


class TestNoObjectionFlow:
    def test_objection_routes_and_ui_are_gone(self, app, db, make_user, thread, fake_ai):
        fake_ai['verdict'] = _verdict(suggested_criterion='none')
        anna = make_user()
        post = _reply(app, anna, thread, 'Geen bezwaar mogelijk.')
        assert _req(_client(app, anna), 'POST', f'/forum/kwaliteit/{post.id}/bezwaar',
                    data={'text': 'Oneens.'}).status_code == 404
        assert _req(_client(app, make_user('moderator')), 'POST', '/forum/kwaliteit/bezwaar/1/besluit',
                    data={'decision': 'handhaven'}).status_code == 404
        dash = _req(_client(app, make_user('moderator')), 'GET', '/moderator').get_data(as_text=True)
        assert 'bezwaren van auteurs' not in dash and 'Meldingen: geen kwaliteit' in dash


# ── De AI faalt of weigert: plaatsen gaat altijd door ────────────────────

class TestAiFailure:
    @pytest.mark.parametrize('failure', [RuntimeError('API down'), TimeoutError('hangt')])
    def test_post_is_placed_without_judgement(self, app, db, make_user, thread, fake_ai, failure):
        fake_ai['fail'] = failure
        post = _reply(app, make_user(), thread, f'Bijdrage terwijl de AI faalt {failure}.')
        assert post.id and _award(post.id) is None
        dash = _req(_client(app, make_user('moderator')), 'GET', '/moderator').get_data(as_text=True)
        assert post.body in dash  # bij "Zonder oordeel", handmatig te beoordelen

    def test_refusal_gives_no_judgement(self, app, db, make_user, thread, fake_ai):
        fake_ai['refuse'] = True
        post = _reply(app, make_user(), thread, 'Bijdrage die de AI weigert.')
        assert _award(post.id) is None

    def test_no_ai_calls_in_tests_without_inline_flag(self, app, db, make_user, thread, monkeypatch):
        called = []
        monkeypatch.setattr(quality, 'judge_post', lambda pid: called.append(pid))
        _reply(app, make_user(), thread, 'Geen achtergrondthread in tests.')
        assert called == []

    def test_moderator_can_judge_manually(self, app, db, make_user, thread):
        post = _reply(app, make_user(), thread, 'Zonder AI beoordeeld.')
        _req(_client(app, make_user('moderator')), 'POST', f'/forum/kwaliteit/{post.id}/besluit',
             data={'action': 'toekennen', 'criterion': 'K3', 'explanation': 'Eerlijk de andere kant benoemd.'})
        db.session.expire_all()
        assert (_award(post.id).status, _award(post.id).criterion, _award(post.id).source) == \
            ('toegekend', 'K3', 'moderator')


# ── Melden door anderen ──────────────────────────────────────────────────

class TestReporting:
    @pytest.mark.parametrize('decision,final_status', [('intrekken', 'afgewezen'), ('handhaven', 'toegekend')])
    def test_report_goes_to_moderator_and_decision_is_recorded(self, app, db, make_user, thread, fake_ai,
                                                               decision, final_status):
        anna = make_user()
        post = _reply(app, anna, thread, 'Automatisch toegekend.')
        reporter, mod = make_user(), make_user('moderator')
        client = _client(app, reporter)
        assert _req(client, 'POST', f'/forum/kwaliteit/{post.id}/melden').status_code == 302
        _req(client, 'POST', f'/forum/kwaliteit/{post.id}/melden')  # dubbel telt niet
        reports = QualityReport.query.filter_by(award_id=_award(post.id).id).all()
        assert len(reports) == 1 and reports[0].reporter_id == reporter.id
        dash = _req(_client(app, mod), 'GET', '/moderator').get_data(as_text=True)
        assert f'Gemeld door @{reporter.username}' in dash
        _req(_client(app, mod), 'POST', f'/forum/kwaliteit/melding/{reports[0].id}/besluit',
             data={'decision': decision, 'note': 'Toch geen nieuw argument.'})
        db.session.expire_all()
        r = db.session.get(QualityReport, reports[0].id)
        assert (r.decision, r.decided_by_id) == (decision, mod.id) and r.decided_at
        assert _award(post.id).status == final_status

    def test_cannot_report_own(self, app, db, make_user, thread, fake_ai):
        anna = make_user()
        post = _reply(app, anna, thread, 'Eigen bijdrage.')
        assert _req(_client(app, anna), 'POST', f'/forum/kwaliteit/{post.id}/melden').status_code == 403


# ── Los van eens/oneens ──────────────────────────────────────────────────

class TestIndependence:
    def test_quality_and_stance_do_not_affect_each_other(self, app, db, make_user, thread, fake_ai):
        anna, bram, mod = make_user(), make_user(), make_user('moderator')
        post = _reply(app, anna, thread, 'Onafhankelijk.')
        _req(_client(app, bram), 'POST', f'/forum/stance/{post.id}', data={'stance': 'oneens'})
        before = [(r.user_id, r.stance) for r in PostLike.query.filter_by(post_id=post.id)]
        _mod_decide(app, mod, post, 'toekennen', 'K2')
        assert [(r.user_id, r.stance) for r in PostLike.query.filter_by(post_id=post.id)] == before
        _req(_client(app, bram), 'POST', f'/forum/stance/{post.id}', data={'stance': 'eens'})
        db.session.expire_all()
        assert (_award(post.id).status, _award(post.id).criterion) == ('toegekend', 'K2')
        assert PostLike.query.filter_by(post_id=post.id).one().stance == 'eens'


# ── Migratie en democontent ──────────────────────────────────────────────

class TestMigrationAndDemo:
    def test_old_proposed_rows_become_automatic_outcome(self, app, db, make_user, thread):
        from app import _migrate_columns
        a = _reply(app, make_user(), thread, 'Oud voorstel met criterium.')
        b = _reply(app, make_user(), thread, 'Oud voorstel zonder criterium.')
        db.session.add_all([QualityAward(post_id=a.id, status='voorgesteld', source='ai', criterion='K1'),
                            QualityAward(post_id=b.id, status='voorgesteld', source='ai', criterion=None)])
        db.session.commit()  # geeft de verbinding vrij; objecten blijven aan de sessie
        a_id, b_id = a.id, b.id
        with db.engine.connect() as conn:  # oud schema: zonder de nieuwe kolommen
            conn.execute(text('ALTER TABLE quality_awards DROP COLUMN prompt_text'))
            conn.execute(text('ALTER TABLE quality_awards DROP COLUMN model'))
            conn.execute(text('ALTER TABLE quality_awards DROP COLUMN moderator_note'))
            conn.commit()
        db.engine.dispose()  # SQLite: verse verbindingen na DROP COLUMN
        _migrate_columns()
        db.session.expire_all()
        rows = dict(db.session.execute(text('SELECT post_id, status FROM quality_awards WHERE post_id IN (:a, :b)'),
                                       {'a': a_id, 'b': b_id}).all())
        assert rows == {a_id: 'toegekend', b_id: 'afgewezen'}
        assert _award(a_id).prompt_text is None and _award(a_id).moderator_note is None  # terug, geen backfill

    def test_demo_awards_visible_and_vote_count_gone(self, app):
        quality.seed_demo_awards()
        html = _req(_client(app), 'GET', '/forum/3').get_data(as_text=True)
        assert 'gu-quality__mark' in html and 'gu-post__votes' not in html and '▲' not in html

    def test_seed_demo_awards_is_idempotent(self, app):
        quality.seed_demo_awards()
        n = QualityAward.query.filter_by(source='demo').count()
        quality.seed_demo_awards()
        assert QualityAward.query.filter_by(source='demo').count() == n >= 1
