"""
Fase D: handhaving van de gedragsregels (app/conduct.py, het handhavings-
oordeel in quality.judge_post, de routes in app/routes/forum.py en de
achtergrondpagina).

Dekt: de AI-uitkomst leidt tot de juiste actie (geen, verplaatst, verborgen,
gemarkeerd); een verplaatste bijdrage is weg voor anderen maar zichtbaar voor
de auteur en moderators en staat met anonieme auteur op /achtergrond; spam of
misbruik staat daar niet; een twijfelgeval blijft zichtbaar en staat in het
dashboard; de bezwaarstroom (binnen zeven dagen, een senior moderator beslist,
terugplaatsen); moderatorhandelingen; de tellingen; de statuspoll; footer en
teksten; de migratie.

De Anthropic-client is nep. Gedeelde conftest-app (CSRF uit); _req() wist de
in `g` gecachte current_user en locale.
"""
import json
import re
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from flask import g
from flask_babel import refresh
from sqlalchemy import text

from app import bcrypt
from app.models import (ConductCase, ConductObjection, Post, PostLike, QualityAward, QualityObjection,
                        QualityReport, Thread, User)


def _verdict(**over):
    data = {'has_source': False, 'source_is_new': False, 'argument_is_new': False,
            'suggested_criterion': 'none', 'k3_possible': False,
            'explanation': 'Geen nieuw argument.',
            'conduct_verdict': 'none', 'conduct_rule': 'none',
            'conduct_explanation': 'Je houdt je aan de gedragsregels.'}
    data.update(over)
    return data


VIOLATION = dict(conduct_verdict='violation', conduct_rule='respect',
                 conduct_explanation='Je noemt de ander dom in plaats van op het argument in te gaan.')
SPAM = dict(conduct_verdict='spam', conduct_rule='none', conduct_explanation='Dit is reclame.')
DOUBT = dict(conduct_verdict='doubt', conduct_rule='onderwerp', conduct_explanation='Misschien buiten het onderwerp.')
DOUBT['conduct_rule'] = 'on_topic'


@pytest.fixture
def fake_ai(monkeypatch, app):
    state = {'translate': [], 'judge': [], 'verdict': _verdict(), 'fail': None}

    class Messages:
        def create(self, **kw):
            if 'output_config' not in kw:  # vertaling
                state['translate'].append(kw)
                src = kw['messages'][0]['content'].split('\n\n', 1)[1]
                return SimpleNamespace(content=[SimpleNamespace(type='text', text=f'EN: {src}')])
            state['judge'].append(kw)
            if state['fail']:
                raise state['fail']
            return SimpleNamespace(stop_reason='end_turn',
                                   content=[SimpleNamespace(type='text', text=json.dumps(state['verdict']))])

    class FakeClient:
        def __init__(self, api_key=None, **kw):
            self.messages = Messages()

    import anthropic
    monkeypatch.setattr(anthropic, 'Anthropic', FakeClient)
    monkeypatch.setitem(app.config, 'QUALITY_AI_INLINE', True)
    return state


@pytest.fixture(autouse=True)
def _fresh_rate_limits(app):
    # Eerdere testbestanden kunnen de limiter zonder opslag hebben
    # geïnitialiseerd (RATELIMIT_ENABLED uit); dan is er niets te resetten.
    from app import limiter
    try:
        limiter.reset()
    except AssertionError:
        pass
    yield


@pytest.fixture
def make_user(db):
    created = []

    def _make(role='contributor'):
        name = f'c_{uuid.uuid4().hex[:8]}'
        u = User(username=name, email=f'{name}@example.com', verified=True,
                 password_hash=bcrypt.generate_password_hash('Sterk1!ww').decode('utf-8'))
        u.set_role(role)
        u.accept_rules()
        db.session.add(u)
        db.session.commit()
        created.append(u.id)
        return u

    yield _make
    from app import quality
    for uid in created:
        quality.detach_user(uid)
        for p in Post.query.filter_by(user_id=uid):
            p.user_id = None
        for like in PostLike.query.filter_by(user_id=uid):
            db.session.delete(like)
        u = db.session.get(User, uid)
        if u:
            db.session.delete(u)
    db.session.commit()


@pytest.fixture
def make_thread(db):
    made = []

    def _make(op_user=None, title='Handhaving'):
        t = Thread(eyebrow_nl='T', eyebrow_en='T', title_prefix_nl=title, title_prefix_en=title)
        db.session.add(t)
        db.session.flush()
        db.session.add(Post(thread_id=t.id, user_id=op_user.id if op_user else None, author_name='',
                            author_username=op_user.username if op_user else 'op',
                            body=f'Opening {uuid.uuid4().hex[:6]}', is_op=True, source_lang='nl',
                            is_demo=op_user is None))
        db.session.commit()
        made.append(t.id)
        return t

    yield _make
    for tid in made:
        ids = [p.id for p in Post.query.filter_by(thread_id=tid)]
        from app import quality
        quality.delete_for_posts(ids)
        for like in PostLike.query.filter(PostLike.post_id.in_(ids)):
            db.session.delete(like)
        db.session.flush()
        db.session.expire_all()
        for p in Post.query.filter_by(thread_id=tid):
            p.parent_id = None
        db.session.flush()
        for p in Post.query.filter_by(thread_id=tid):
            db.session.delete(p)
        db.session.delete(db.session.get(Thread, tid))
    db.session.commit()


@pytest.fixture
def thread(make_thread):
    return make_thread()


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


def _get(app, user, path, lang='nl'):
    return _req(_client(app, user, lang), 'GET', path)


def _reply(app, user, thread, body, parent=None, lang='nl'):
    data = {'body': body}
    if parent is not None:
        data['parent_id'] = parent.id
    resp = _req(_client(app, user, lang), 'POST', f'/forum/{thread.id}', data=data)
    assert resp.status_code == 302
    return Post.query.filter_by(thread_id=thread.id, body=body).one()


def _case(post):
    return ConductCase.query.filter_by(post_id=post.id).one_or_none()


def _part(html, post):
    start = html.rindex('<article', 0, html.index(f'id="post-{post.id}"'))
    ends = [k for k in (html.find('<article', start + 1), html.find('<script', start)) if k != -1]
    return html[start:min(ends) if ends else len(html)]


# ── 1 en 2. Het AI-oordeel en wat ermee gebeurt ──────────────────────────

class TestAiVerdict:
    def test_no_violation_does_nothing(self, app, db, make_user, thread, fake_ai):
        post = _reply(app, make_user(), thread, 'Een gewone bijdrage.')
        assert _case(post) is None
        award = QualityAward.query.filter_by(post_id=post.id).one()
        assert award.conduct_verdict == 'none' and award.conduct_explanation == 'Je houdt je aan de gedragsregels.'
        assert 'Een gewone bijdrage.' in _get(app, make_user(), f'/forum/{thread.id}').get_data(as_text=True)

    def test_violation_is_moved(self, app, db, make_user, thread, fake_ai):
        fake_ai['verdict'] = _verdict(**VIOLATION)
        anna = make_user()
        post = _reply(app, anna, thread, 'Jij bent gewoon dom.')
        case = _case(post)
        assert (case.action, case.rule, case.source, case.on_background) == ('verplaatst', 'respect', 'ai', True)
        assert case.body_snapshot == 'Jij bent gewoon dom.' and case.acted_by_id is None
        assert case.explanation_lang == 'nl' and 'dom' in case.explanation

        other = _get(app, make_user(), f'/forum/{thread.id}').get_data(as_text=True)
        part = _part(other, post)
        assert 'Bijdrage verplaatst wegens de gedragsregels' in part
        assert f'href="/achtergrond#zaak-{case.id}"' in part
        assert 'Jij bent gewoon dom.' not in other and anna.username not in other

        own = _part(_get(app, anna, f'/forum/{thread.id}').get_data(as_text=True), post)
        assert 'Jij bent gewoon dom.' in own and 'gu-post--removed' in own
        assert 'Verplaatst naar de achtergrondpagina wegens de gedragsregel: Over het idee, niet de persoon.' in own
        assert case.explanation in own and 'Bezwaar maken tegen dit besluit' in own

        mod = _part(_get(app, make_user('moderator'), f'/forum/{thread.id}').get_data(as_text=True), post)
        assert 'Jij bent gewoon dom.' in mod and 'Terugplaatsen in het gesprek' in mod
        assert 'Bezwaar maken tegen dit besluit' not in mod

    def test_violation_without_clear_rule_becomes_doubt(self, app, db, make_user, thread, fake_ai):
        fake_ai['verdict'] = _verdict(conduct_verdict='violation', conduct_rule='none')
        post = _reply(app, make_user(), thread, 'Twijfelachtig.')
        assert _case(post).action == 'twijfel'

    def test_spam_is_hidden_and_not_on_background(self, app, db, make_user, thread, fake_ai):
        fake_ai['verdict'] = _verdict(**SPAM)
        anna = make_user()
        post = _reply(app, anna, thread, 'Koop nu goedkope horloges.')
        case = _case(post)
        assert (case.action, case.rule, case.on_background) == ('verborgen', None, False)
        other = _get(app, make_user(), f'/forum/{thread.id}').get_data(as_text=True)
        assert f'id="post-{post.id}"' not in other and 'horloges' not in other
        own = _part(_get(app, anna, f'/forum/{thread.id}').get_data(as_text=True), post)
        assert 'horloges' in own and 'Verborgen voor andere lezers als spam of misbruik.' in own
        assert 'horloges' not in _get(app, None, '/achtergrond').get_data(as_text=True)

    def test_hidden_post_with_visible_reply_gets_neutral_placeholder(self, app, db, make_user, thread, fake_ai):
        # Iemand reageerde al voordat de bijdrage verborgen werd (een nieuw
        # bericht is tijdens de beoordeling gewoon zichtbaar).
        from app import conduct
        anna, bram = make_user(), make_user()
        spam = _reply(app, anna, thread, 'Spam met reactie.')
        reply = _reply(app, bram, thread, 'Een reactie eronder.', parent=spam)
        conduct.hide(spam, 'Reclame.', 'nl', actor=make_user('moderator'))
        db.session.commit()
        html = _get(app, make_user(), f'/forum/{thread.id}').get_data(as_text=True)
        part = _part(html, spam)
        assert 'Bijdrage verborgen' in part and '/achtergrond' not in part and 'Spam met reactie.' not in part
        assert 'Een reactie eronder.' in html
        assert html.index(f'id="post-{spam.id}"') < html.index(f'id="post-{reply.id}"')

    def test_doubt_stays_visible_and_is_flagged_for_moderators(self, app, db, make_user, thread, fake_ai):
        fake_ai['verdict'] = _verdict(**DOUBT)
        anna = make_user()
        post = _reply(app, anna, thread, 'Iets over het weer.')
        assert (_case(post).action, _case(post).rule) == ('twijfel', 'onderwerp')
        for viewer in (None, make_user(), anna):
            part = _part(_get(app, viewer, f'/forum/{thread.id}').get_data(as_text=True), post)
            assert 'Iets over het weer.' in part
            assert 'Gemarkeerd voor een moderator' not in part and 'gu-post--removed' not in part
        mod = make_user('moderator')
        part = _part(_get(app, mod, f'/forum/{thread.id}').get_data(as_text=True), post)
        assert 'Gemarkeerd voor een moderator: mogelijke overtreding van Bij het onderwerp.' in part
        dash = _get(app, mod, '/moderator').get_data(as_text=True)
        assert 'Gedragsregels: twijfelgevallen (1)' in dash and 'Iets over het weer.' in dash
        assert 'Iets over het weer.' not in _get(app, None, '/achtergrond').get_data(as_text=True)

    def test_ai_failure_leaves_post(self, app, db, make_user, thread, fake_ai):
        fake_ai['fail'] = RuntimeError('API down')
        post = _reply(app, make_user(), thread, 'Terwijl de AI faalt.')
        assert _case(post) is None
        assert 'Terwijl de AI faalt.' in _get(app, None, f'/forum/{thread.id}').get_data(as_text=True)

    def test_one_call_with_original_text_and_restraint(self, app, db, make_user, thread, fake_ai):
        _reply(app, make_user(), thread, 'Een Nederlandse bijdrage met toon.')
        assert len(fake_ai['judge']) == 1
        call = fake_ai['judge'][0]
        content, system = call['messages'][0]['content'], call['system']
        assert '<original>\nEen Nederlandse bijdrage met toon.\n</original>' in content
        assert 'EN: Een Nederlandse bijdrage met toon.' in content
        assert 'be restrained' in system and 'If you have any doubt, choose' in system
        assert 'do not judge whether someone uses more than one account' in system
        assert 'substantiated or concrete' in system
        props = call['output_config']['format']['schema']['properties']
        assert props['conduct_verdict']['enum'] == ['none', 'violation', 'doubt', 'spam']
        assert props['conduct_rule']['enum'] == ['respect', 'on_topic', 'none']
        # De bestaande taalregel: alles in de taal van de bijdrage.
        assert system.startswith('Schrijf je volledige antwoord in het Nederlands.')

    def test_english_post_has_no_separate_original(self, app, db, make_user, thread, fake_ai):
        _reply(app, make_user(), thread, 'An English contribution.', lang='en')
        content = fake_ai['judge'][0]['messages'][0]['content']
        assert '<original>' not in content and 'conduct_explanation' in content

    def test_medal_not_shown_on_moved_post(self, app, db, make_user, thread, fake_ai):
        fake_ai['verdict'] = _verdict(suggested_criterion='K1', argument_is_new=True, **VIOLATION)
        anna = make_user()
        post = _reply(app, anna, thread, 'Nieuw argument maar grof.')
        own = _part(_get(app, anna, f'/forum/{thread.id}').get_data(as_text=True), post)
        assert 'gu-quality__mark' not in own
        assert _get(app, None, f'/forum/kwaliteit/{post.id}').status_code == 404
        from app.status import quality_counts
        assert quality_counts(anna.id) == (0, 0)


# ── 3. Weergave, tellingen en wat anderen niet meer kunnen ───────────────

class TestThreadView:
    def test_counts_exclude_moved_and_hidden_for_others(self, app, db, make_user, thread, fake_ai):
        anna, bram = make_user(), make_user()
        fake_ai['verdict'] = _verdict(**VIOLATION)
        _reply(app, anna, thread, 'Verplaatst.')
        fake_ai['verdict'] = _verdict(**SPAM)
        _reply(app, anna, thread, 'Verborgen.')
        fake_ai['verdict'] = _verdict()
        _reply(app, bram, thread, 'Gewoon.')
        op = Post.query.filter_by(thread_id=thread.id, is_op=True).one()
        reader = make_user()
        html = _get(app, reader, f'/forum/{thread.id}').get_data(as_text=True)
        head = html[html.index('gu-forum__threadhead'):html.index('class="gu-thread"')]
        assert re.search(r'2\s*reacties', head)
        assert f'id="collapsed-{op.id}" data-count="1"' in html
        index = _get(app, reader, '/forum').get_data(as_text=True)
        row = index[index.index(f'/forum/{thread.id}"'):]
        assert re.search(r'2\s*reacties', row[:1500])
        # De auteur en moderators zien alles.
        own = _get(app, anna, f'/forum/{thread.id}').get_data(as_text=True)
        assert f'id="collapsed-{op.id}" data-count="3"' in own

    def test_moved_opening_post_hides_thread_for_others(self, app, db, make_user, make_thread, fake_ai):
        from app import conduct
        anna = make_user()
        t = make_thread(op_user=anna)
        op = Post.query.filter_by(thread_id=t.id, is_op=True).one()
        conduct.move(op, 'onderwerp', 'Hoort in een ander gesprek.', 'nl')
        db.session.commit()
        for viewer in (None, make_user()):
            assert _get(app, viewer, f'/forum/{t.id}').status_code == 404
            assert f'/forum/{t.id}"' not in _get(app, viewer, '/forum').get_data(as_text=True)
        assert _get(app, anna, f'/forum/{t.id}').status_code == 200
        assert _get(app, make_user('moderator'), f'/forum/{t.id}').status_code == 200
        # Op de achtergrondpagina zonder link naar het (verborgen) gesprek.
        bg = _get(app, None, '/achtergrond').get_data(as_text=True)
        card = bg[bg.index(f'id="zaak-{op.conduct_case.id}"'):]
        card = card[:card.index('</li>')]
        assert op.body in card and 'In context bekijken' not in card

    def test_others_cannot_use_a_moved_post(self, app, db, make_user, thread, fake_ai):
        fake_ai['verdict'] = _verdict(**VIOLATION)
        anna = make_user()
        post = _reply(app, anna, thread, 'Verplaatst en onbruikbaar.')
        bram = make_user()
        client = _client(app, bram)
        assert _req(client, 'POST', f'/forum/stance/{post.id}', data={'stance': 'eens'}).status_code == 404
        assert _req(client, 'POST', f'/forum/translate/{post.id}').status_code == 404
        assert _req(client, 'GET', f'/forum/kwaliteit/{post.id}/status').status_code == 404
        fake_ai['verdict'] = _verdict()
        _req(client, 'POST', f'/forum/{thread.id}', data={'body': 'op verplaatst', 'parent_id': post.id})
        assert Post.query.filter_by(body='op verplaatst').count() == 0


# ── 4. Openbare achtergrondpagina ────────────────────────────────────────

class TestBackgroundPage:
    def _moved(self, app, make_user, thread, fake_ai, body='Jij snapt er niks van.'):
        fake_ai['verdict'] = _verdict(**VIOLATION)
        anna = make_user()
        post = _reply(app, anna, thread, body)
        return anna, post, _case(post)

    @pytest.mark.parametrize('lang', ['nl', 'en'])
    def test_card_with_anonymous_author(self, app, db, make_user, thread, fake_ai, lang):
        anna, post, case = self._moved(app, make_user, thread, fake_ai)
        html = _get(app, None, '/achtergrond', lang).get_data(as_text=True)
        card = html[html.index(f'id="zaak-{case.id}"'):]
        card = card[:card.index('</li>')]
        texts = {
            'nl': ('Over het idee, niet de persoon', 'Bezwaar mogelijk tot', 'Automatisch door de AI',
                   'Waarom verplaatst', 'In context bekijken'),
            'en': ('About the idea, not the person', 'Objection possible until', 'Automatically by the AI',
                   'Why it was moved', 'View in context'),
        }[lang]
        for t in texts:
            assert t in card, (lang, t)
        assert 'Jij snapt er niks van.' in card and case.explanation in card
        assert f'href="/forum/{thread.id}#post-{post.id}"' in card
        assert anna.username not in html and '@' + anna.username not in html
        intro, foot = (('We verwijderen niets', 'Pure spam en grof misbruik staan hier niet')
                       if lang == 'nl' else ('We delete nothing', 'Pure spam and gross abuse are not listed here'))
        assert intro in html and foot in html

    def test_statuses(self, app, db, make_user, thread, fake_ai):
        from app import conduct
        anna, post, case = self._moved(app, make_user, thread, fake_ai)
        senior = make_user('senior_moderator')
        case.acted_at = datetime.utcnow() - timedelta(days=8)
        db.session.commit()
        assert 'Bezwaartermijn verlopen' in _get(app, None, '/achtergrond').get_data(as_text=True)
        conduct.confirm(case, senior)
        db.session.commit()
        assert 'Besluit bevestigd door moderator' in _get(app, None, '/achtergrond').get_data(as_text=True)

    def test_moved_post_cannot_be_edited_and_snapshot_stays(self, app, db, make_user, thread, fake_ai):
        anna, post, case = self._moved(app, make_user, thread, fake_ai, body='Origineel grof.')
        resp = _req(_client(app, anna), 'POST', f'/forum/post/{post.id}/bewerken', data={'body': 'Netjes gemaakt.'})
        assert resp.status_code == 409 and resp.get_json()['error'] == 'removed'
        assert 'Je kunt wel bezwaar maken.' in resp.get_json()['message']
        html = _get(app, None, '/achtergrond').get_data(as_text=True)
        assert 'Origineel grof.' in html and 'Netjes gemaakt.' not in html


# ── 6. Bezwaar en moderatie ──────────────────────────────────────────────

class TestObjection:
    def _setup(self, app, make_user, thread, fake_ai):
        fake_ai['verdict'] = _verdict(**VIOLATION)
        anna = make_user()
        post = _reply(app, anna, thread, 'Bijdrage met bezwaar.')
        return anna, post, _case(post)

    def _object(self, app, user, post, body='Ik viel het argument aan, niet de persoon.'):
        return _req(_client(app, user), 'POST', f'/forum/handhaving/{post.id}/bezwaar', data={'text': body})

    def _decide(self, app, user, objection, decision, note=''):
        return _req(_client(app, user), 'POST', f'/forum/handhaving/bezwaar/{objection.id}/besluit',
                    data={'decision': decision, 'note': note})

    def test_author_objects_once_within_seven_days(self, app, db, make_user, thread, fake_ai):
        anna, post, case = self._setup(app, make_user, thread, fake_ai)
        n_calls = len(fake_ai['judge'])
        assert self._object(app, make_user(), post).status_code == 403
        assert self._object(app, anna, post, '   ').status_code == 400
        assert self._object(app, anna, post).status_code == 302
        assert self._object(app, anna, post).status_code == 409
        assert ConductObjection.query.filter_by(case_id=case.id).count() == 1
        assert len(fake_ai['judge']) == n_calls  # geen AI-reactie op een bezwaar
        own = _part(_get(app, anna, f'/forum/{thread.id}').get_data(as_text=True), post)
        assert 'Bezwaar ingediend, een moderator beslist' in own
        assert 'Bezwaar ingediend, een moderator beslist' in _get(app, None, '/achtergrond').get_data(as_text=True)

    def test_too_late(self, app, db, make_user, thread, fake_ai):
        anna, post, case = self._setup(app, make_user, thread, fake_ai)
        case.acted_at = datetime.utcnow() - timedelta(days=7, minutes=1)
        db.session.commit()
        assert self._object(app, anna, post).status_code == 400
        own = _part(_get(app, anna, f'/forum/{thread.id}').get_data(as_text=True), post)
        assert 'Bezwaar maken tegen dit besluit' not in own and 'Bezwaartermijn verlopen' in own

    def test_senior_restores_and_post_returns(self, app, db, make_user, thread, fake_ai):
        anna, post, case = self._setup(app, make_user, thread, fake_ai)
        self._object(app, anna, post)
        objection = ConductObjection.query.filter_by(case_id=case.id).one()
        dash = _get(app, make_user('moderator'), '/moderator').get_data(as_text=True)
        assert 'Gedragsregels: openstaande bezwaren (1)' in dash and 'Ik viel het argument aan' in dash
        assert self._decide(app, make_user('moderator'), objection, 'terugplaatsen').status_code == 403
        senior = make_user('senior_moderator')
        assert self._decide(app, senior, objection, 'terugplaatsen', 'Scherp maar over het argument.').status_code == 302
        db.session.expire_all()
        case = _case(post)
        objection = db.session.get(ConductObjection, objection.id)
        assert case.action == 'teruggeplaatst' and case.restored_by_id == senior.id
        assert (objection.decision, objection.decided_by_id) == ('terugplaatsen', senior.id)
        other = _part(_get(app, make_user(), f'/forum/{thread.id}').get_data(as_text=True), post)
        assert 'Bijdrage met bezwaar.' in other
        assert 'Bezwaar toegewezen, teruggeplaatst' in _get(app, None, '/achtergrond').get_data(as_text=True)

    def test_senior_keeps_decision(self, app, db, make_user, thread, fake_ai):
        anna, post, case = self._setup(app, make_user, thread, fake_ai)
        self._object(app, anna, post)
        objection = ConductObjection.query.filter_by(case_id=case.id).one()
        assert self._decide(app, make_user('senior_moderator'), objection, 'handhaven').status_code == 302
        db.session.expire_all()
        assert _case(post).action == 'verplaatst' and _case(post).confirmed_at is not None
        own = _part(_get(app, anna, f'/forum/{thread.id}').get_data(as_text=True), post)
        assert 'Bezwaar afgewezen, het besluit blijft staan.' in own

    def test_moderator_who_moved_cannot_decide(self, app, db, make_user, thread, fake_ai):
        from app import conduct
        senior, other_senior, anna = make_user('senior_moderator'), make_user('senior_moderator'), make_user()
        fake_ai['verdict'] = _verdict()
        post = _reply(app, anna, thread, 'Door een moderator verplaatst.')
        assert _req(_client(app, senior), 'POST', f'/forum/handhaving/{post.id}/verplaatsen',
                    data={'rule': 'onderwerp', 'note': 'Ander onderwerp.'}).status_code == 302
        self._object(app, anna, post)
        objection = ConductObjection.query.filter_by(case_id=_case(post).id).one()
        assert not conduct.can_decide_objection(_case(post), senior)
        assert self._decide(app, senior, objection, 'terugplaatsen').status_code == 403
        assert self._decide(app, other_senior, objection, 'handhaven').status_code == 302
        bg = _get(app, None, '/achtergrond').get_data(as_text=True)
        assert 'Door een moderator' in bg and 'Bij het onderwerp' in bg


class TestModeratorActions:
    def test_move_hide_restore_and_release(self, app, db, make_user, thread, fake_ai):
        anna, mod = make_user(), make_user('moderator')
        fake_ai['verdict'] = _verdict()
        post = _reply(app, anna, thread, 'Een bijdrage.')
        client = _client(app, mod)
        base = f'/forum/handhaving/{post.id}'
        assert _req(client, 'POST', f'{base}/verplaatsen', data={'rule': 'respect', 'note': ''}).status_code == 400
        assert _req(_client(app, make_user()), 'POST', f'{base}/verplaatsen',
                    data={'rule': 'respect', 'note': 'x'}).status_code == 403
        assert _req(client, 'POST', f'{base}/verplaatsen', data={'rule': 'respect', 'note': 'Persoonlijk.'}).status_code == 302
        db.session.expire_all()
        case = _case(post)
        assert (case.action, case.source, case.acted_by_id, case.explanation) == ('verplaatst', 'moderator', mod.id, 'Persoonlijk.')
        assert _req(client, 'POST', f'{base}/terugplaatsen').status_code == 302
        db.session.expire_all()
        assert _case(post).action == 'teruggeplaatst'
        assert _req(client, 'POST', f'{base}/verbergen').status_code == 302
        db.session.expire_all()
        assert (_case(post).action, _case(post).on_background) == ('verborgen', True)  # was al eens verplaatst
        assert _req(client, 'POST', f'{base}/terugplaatsen').status_code == 302
        # Twijfel vrijgeven.
        fake_ai['verdict'] = _verdict(**DOUBT)
        doubt = _reply(app, anna, thread, 'Twijfelgeval.')
        assert _req(client, 'POST', f'/forum/handhaving/{doubt.id}/vrijgeven').status_code == 302
        db.session.expire_all()
        assert _case(doubt).action == 'vrijgegeven'
        assert 'Gedragsregels: twijfelgevallen (0)' in _get(app, mod, '/moderator').get_data(as_text=True)

    def test_not_on_own_post(self, app, db, make_user, thread, fake_ai):
        mod = make_user('moderator')
        fake_ai['verdict'] = _verdict()
        post = _reply(app, mod, thread, 'Eigen bijdrage van de moderator.')
        assert _req(_client(app, mod), 'POST', f'/forum/handhaving/{post.id}/verbergen').status_code == 403


# ── Statuspoll na het plaatsen ───────────────────────────────────────────

class TestStatusPoll:
    def test_author_hears_outcome_without_reload(self, app, db, make_user, thread, fake_ai):
        fake_ai['verdict'] = _verdict(**VIOLATION)
        anna = make_user()
        post = _reply(app, anna, thread, 'Grof en vers.')
        page = _get(app, anna, f'/forum/{thread.id}').get_data(as_text=True)
        assert "data.conduct.html" in page
        data = _req(_client(app, anna), 'GET', f'/forum/kwaliteit/{post.id}/status').get_json()
        assert data['conduct']['action'] == 'verplaatst'
        assert 'Verplaatst naar de achtergrondpagina' in data['conduct']['html']
        assert VIOLATION['conduct_explanation'] in data['conduct']['html']
        assert _req(_client(app, make_user()), 'GET', f'/forum/kwaliteit/{post.id}/status').status_code == 404

    def test_no_conduct_for_normal_post(self, app, db, make_user, thread, fake_ai):
        anna = make_user()
        post = _reply(app, anna, thread, 'Gewoon vers.')
        assert _req(_client(app, anna), 'GET', f'/forum/kwaliteit/{post.id}/status').get_json()['conduct'] is None


# ── 5 en 7. Footer en teksten ────────────────────────────────────────────

class TestFooterAndTexts:
    @pytest.mark.parametrize('lang,label', [('nl', 'Achtergrond'), ('en', 'Background')])
    def test_footer_links(self, app, lang, label):
        html = _get(app, None, '/', lang).get_data(as_text=True)
        footer = html[html.index('<footer'):]
        for link in ('href="/spelregels"', 'href="/privacy"', f'<a href="/achtergrond">{label}</a>'):
            assert link in footer
        assert 'Privacy &amp; Cookies' not in footer

    def test_mobile_footer_keeps_participate_column(self):
        import os
        css = open(os.path.join(os.path.dirname(__file__), '..', 'app', 'static', 'css', 'kit.css')).read()
        mobile = css[css.index('@media (max-width: 767px) {\n  .gu-footer__grid'):]
        mobile = mobile[:mobile.index('\n}\n')]
        assert 'div:nth-child(3) { display: none; }' not in mobile
        assert 'span:last-child { display: none; }' not in mobile

    @pytest.mark.parametrize('lang,expect,old', [
        ('nl', 'niet verwijderd maar verplaatst naar de openbare', 'bijdragen verwijderen die niet voldoen'),
        ('en', 'not deleted but moved to the public', "remove contributions that don't meet"),
    ])
    def test_about_text(self, app, lang, expect, old):
        html = _get(app, None, '/about', lang).get_data(as_text=True)
        assert expect in html and old not in html and 'href="/achtergrond"' in html


# ── 8. Migratie ──────────────────────────────────────────────────────────

class TestMigration:
    def test_columns_added_idempotently(self, app, db):
        from app import _migrate_columns
        db.session.close()
        with db.engine.connect() as conn:
            for col in ('conduct_verdict', 'conduct_rule', 'conduct_explanation'):
                conn.execute(text(f'ALTER TABLE quality_awards DROP COLUMN {col}'))
            conn.commit()
        db.engine.dispose()  # SQLite: verse verbindingen na DROP COLUMN
        from sqlalchemy import inspect as sa_inspect
        for _ in range(2):  # twee keer opstarten
            _migrate_columns()
            cols = {c['name'] for c in sa_inspect(db.engine).get_columns('quality_awards')}
            assert {'conduct_verdict', 'conduct_rule', 'conduct_explanation'} <= cols
        assert {'conduct_cases', 'conduct_objections'} <= set(sa_inspect(db.engine).get_table_names())


# ── Bewerken en verwijderen ──────────────────────────────────────────────

def _edit(app, user, post, body, lang='nl'):
    return _req(_client(app, user, lang), 'POST', f'/forum/post/{post.id}/bewerken', data={'body': body})


class TestModeratorCannotEditOrDelete:
    def test_moderator_cannot_edit_or_delete_someone_elses_post(self, app, db, make_user, make_thread, fake_ai):
        anna = make_user()
        t = make_thread(op_user=anna)
        op = Post.query.filter_by(thread_id=t.id, is_op=True).one()
        # Een losse reactie van Anna in een ander gesprek (zonder reacties eronder).
        other_thread = make_thread()
        post = _reply(app, anna, other_thread, 'Van Anna, niet van de moderator.')
        for role in ('moderator', 'senior_moderator'):
            mod = make_user(role)
            assert _edit(app, mod, post, 'Door de moderator.').status_code == 403
            assert _req(_client(app, mod), 'POST', f'/forum/post/{post.id}/verwijderen').status_code == 403
            assert _req(_client(app, mod), 'POST', f'/forum/post/{op.id}/verwijderen').status_code == 403
            assert _req(_client(app, mod), 'GET', f'/forum/{t.id}/bewerken').status_code == 403
            assert _req(_client(app, mod), 'POST', f'/forum/{t.id}/bewerken',
                        data={'title': 'Moderatortitel', 'body': 'Moderatortekst'}).status_code == 403
        db.session.expire_all()
        assert db.session.get(Post, post.id).body == 'Van Anna, niet van de moderator.'
        assert db.session.get(Post, op.id).body == op.body and db.session.get(Thread, t.id).title_prefix_nl != 'Moderatortitel'


class TestAuthorEditing:
    def test_edit_without_replies_works_with_label(self, app, db, make_user, thread, fake_ai):
        anna = make_user()
        post = _reply(app, anna, thread, 'Eerste versie.')
        resp = _edit(app, anna, post, 'Tweede versie.')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['body'] == 'Tweede versie.' and data['edited'] == 'bewerkt zojuist'
        assert data['poll_url'] == f'/forum/kwaliteit/{post.id}/status'
        db.session.expire_all()
        assert db.session.get(Post, post.id).edited_at is not None
        for lang, label in (('nl', 'bewerkt zojuist'), ('en', 'edited just now')):
            part = _part(_get(app, make_user(), f'/forum/{thread.id}', lang).get_data(as_text=True), post)
            assert 'Tweede versie.' in part and label in part

    def test_edit_refused_once_there_is_a_reply(self, app, db, make_user, thread, fake_ai):
        anna, bram = make_user(), make_user()
        post = _reply(app, anna, thread, 'Met reactie.')
        _reply(app, bram, thread, 'Een reactie.', parent=post)
        resp = _edit(app, anna, post, 'Stiekem aangepast.')
        assert resp.status_code == 409 and resp.get_json()['error'] == 'replies'
        assert resp.get_json()['message'] == 'Dit bericht heeft al reacties en kan daarom niet meer worden bewerkt.'
        db.session.expire_all()
        assert db.session.get(Post, post.id).body == 'Met reactie.'
        own = _part(_get(app, anna, f'/forum/{thread.id}').get_data(as_text=True), post)
        assert 'gu-post__edit-btn' not in own and 'gu-post__delete-btn' in own  # verwijderen kan wel
        assert _req(_client(app, anna), 'POST', f'/forum/post/{post.id}/verwijderen').status_code == 302
        db.session.expire_all()
        assert db.session.get(Post, post.id).body == '[bericht verwijderd]'

    def test_a_moved_or_hidden_reply_also_counts(self, app, db, make_user, thread, fake_ai):
        from app import conduct
        anna, bram = make_user(), make_user()
        post = _reply(app, anna, thread, 'Met verborgen reactie.')
        reply = _reply(app, bram, thread, 'Spam eronder.', parent=post)
        conduct.hide(reply, 'Reclame.', 'nl', actor=make_user('moderator'))
        db.session.commit()
        assert _edit(app, anna, post, 'Toch aanpassen.').status_code == 409

    def test_opening_post_counts_any_post_in_thread(self, app, db, make_user, make_thread, fake_ai):
        anna = make_user()
        t = make_thread(op_user=anna)
        assert _req(_client(app, anna), 'GET', f'/forum/{t.id}/bewerken').status_code == 200
        _reply(app, make_user(), t, 'Een reactie via het reactieveld.')
        resp = _req(_client(app, anna), 'GET', f'/forum/{t.id}/bewerken')
        assert resp.status_code == 302
        op = Post.query.filter_by(thread_id=t.id, is_op=True).one()
        assert _edit(app, anna, op, 'Nieuwe opening.').status_code == 409

    def test_edit_reruns_quality_and_conduct(self, app, db, make_user, thread, fake_ai):
        anna = make_user()
        fake_ai['verdict'] = _verdict(suggested_criterion='K1', argument_is_new=True)
        post = _reply(app, anna, thread, 'Een goed argument.')
        assert QualityAward.query.filter_by(post_id=post.id).one().criterion == 'K1'
        n_judge = len(fake_ai['judge'])
        fake_ai['verdict'] = _verdict(**VIOLATION)
        assert _edit(app, anna, post, 'Jij bent dom.').status_code == 200
        assert len(fake_ai['judge']) == n_judge + 1
        call = fake_ai['judge'][-1]['messages'][0]['content']
        assert 'EN: Jij bent dom.' in call and '<original>\nJij bent dom.\n</original>' in call
        assert 'Een goed argument.' not in call.split('New contribution to assess:')[1]  # oude tekst niet beoordeeld
        db.session.expire_all()
        award = QualityAward.query.filter_by(post_id=post.id).one()
        assert award.criterion is None and award.conduct_verdict == 'violation'
        case = _case(post)
        assert case.action == 'verplaatst' and case.body_snapshot == 'Jij bent dom.'
        # De uitkomst via de statuspoll, zoals bij een nieuw bericht.
        data = _req(_client(app, anna), 'GET', f'/forum/kwaliteit/{post.id}/status').get_json()
        assert data['status'] == 'geen' and data['conduct']['action'] == 'verplaatst'
        assert 'Bijdrage verplaatst wegens de gedragsregels' in _get(app, make_user(), f'/forum/{thread.id}').get_data(as_text=True)

    def test_edit_clears_doubt_and_polls_again(self, app, db, make_user, thread, fake_ai):
        anna = make_user()
        fake_ai['verdict'] = _verdict(**DOUBT)
        post = _reply(app, anna, thread, 'Over het weer.')
        assert _case(post).action == 'twijfel'
        fake_ai['verdict'] = _verdict()
        fake_ai['fail'] = RuntimeError('even weg')  # de herbeoordeling blijft uit
        assert _edit(app, anna, post, 'Over het onderwerp.').status_code == 200
        db.session.expire_all()
        assert _case(post) is None and QualityAward.query.filter_by(post_id=post.id).count() == 0
        page = _get(app, anna, f'/forum/{thread.id}').get_data(as_text=True)
        assert f'data-quality-poll="/forum/kwaliteit/{post.id}/status"' in page
        assert _req(_client(app, anna), 'GET', f'/forum/kwaliteit/{post.id}/status').get_json() == {'status': 'pending'}
