"""
Aanpassingen na een gebruikerstest (de nummers volgen de bevindingen):
(2) toelichting onder "optioneel", (3) registratie zonder het wachtwoord
opnieuw te typen, (4) spelregels compact in het formulier, (6) geen uitleg
over vertalen, (7) niet reageren op je eigen bericht, (8) statuspoll voor het
kwaliteitsoordeel. Punt (9), afgewezen bijdragen verbergen, is teruggedraaid:
kwaliteit is uitsluitend een beloning, een bijdrage zonder keurmerk blijft voor
iedereen zichtbaar.

Gedeelde conftest-app (CSRF uit); _req() wist de in `g` gecachte current_user
en locale.
"""
import re
import uuid
from datetime import datetime, timedelta

import pytest
from flask import g
from flask_babel import refresh

from app import bcrypt
from app.models import Post, PostLike, QualityAward, Thread, User


@pytest.fixture(autouse=True)
def _fresh_limits(app):
    from app import limiter
    limiter.reset()
    yield


@pytest.fixture
def world(db):
    made = {'users': [], 'threads': []}

    def user(role='contributor'):
        name = f'f_{uuid.uuid4().hex[:8]}'
        u = User(username=name, email=f'{name}@example.com', verified=True,
                 password_hash=bcrypt.generate_password_hash('Sterk1!ww').decode('utf-8'))
        u.set_role(role)
        u.accept_rules()
        db.session.add(u)
        db.session.commit()
        made['users'].append(u.id)
        return u

    def thread():
        t = Thread(eyebrow_nl='T', eyebrow_en='T', title_prefix_nl='Fixes', title_prefix_en='Fixes')
        db.session.add(t)
        db.session.commit()
        made['threads'].append(t.id)
        return t

    def post(u, t, parent=None, status=None, reason='Te weinig onderbouwing.', created_at=None, is_op=False):
        p = Post(thread_id=t.id, user_id=u.id, parent_id=parent.id if parent else None, author_name='',
                 author_username=u.username, body=f'tekst {uuid.uuid4().hex[:6]}', source_lang='nl',
                 created_at=created_at or datetime.utcnow(), is_op=is_op)
        db.session.add(p)
        db.session.flush()
        if status:
            db.session.add(QualityAward(post_id=p.id, source='ai', status=status, post_lang='nl',
                                        criterion='K1' if status == 'toegekend' else None,
                                        ai_criterion='K1' if status == 'toegekend' else None,
                                        explanation_nl=reason))
        db.session.commit()
        return p

    yield {'user': user, 'thread': thread, 'post': post}
    for tid in made['threads']:
        ids = [p.id for p in Post.query.filter_by(thread_id=tid)]
        for a in QualityAward.query.filter(QualityAward.post_id.in_(ids)):
            db.session.delete(a)
        for like in PostLike.query.filter(PostLike.post_id.in_(ids)):
            db.session.delete(like)
        db.session.flush()
        for p in Post.query.filter_by(thread_id=tid):
            p.parent_id = None
        db.session.flush()
        for p in Post.query.filter_by(thread_id=tid):
            db.session.delete(p)
        db.session.delete(db.session.get(Thread, tid))
    db.session.commit()
    for uid in made['users']:
        u = db.session.get(User, uid)
        if u:
            db.session.delete(u)
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


def _page(app, user, t, lang='nl'):
    return _req(_client(app, user, lang), 'GET', f'/forum/{t.id}').get_data(as_text=True)


# ── (7) Niet reageren op je eigen bericht ────────────────────────────────

class TestNoReplyToOwnPost:
    def test_server_refuses_reply_to_own_post(self, app, world):
        anna = world['user']()
        t = world['thread']()
        own = world['post'](anna, t)
        resp = _req(_client(app, anna), 'POST', f'/forum/{t.id}', data={'body': 'op mezelf', 'parent_id': own.id})
        assert resp.status_code == 302 and resp.headers['Location'].endswith(f'#post-{own.id}')
        assert Post.query.filter_by(body='op mezelf').count() == 0

    def test_reply_to_someone_else_still_works_and_button_hidden_on_own(self, app, world):
        anna, bram = world['user'](), world['user']()
        t = world['thread']()
        own, other = world['post'](anna, t), world['post'](bram, t)
        _req(_client(app, anna), 'POST', f'/forum/{t.id}', data={'body': 'op bram', 'parent_id': other.id})
        assert Post.query.filter_by(body='op bram', parent_id=other.id).count() == 1
        html = _page(app, anna, t)
        assert f'id="reply-form-{own.id}"' not in html and f'id="reply-form-{other.id}"' in html


# ── Kwaliteit alleen als beloning: niets wordt op kwaliteit verborgen ────

def _part(html, post):
    """De HTML van één bijdrage (tot de volgende bijdrage of het script)."""
    start = html.index(f'id="post-{post.id}"')
    ends = [k for k in (html.find('<article', start), html.find('<script', start)) if k != -1]
    return html[start:min(ends) if ends else len(html)]


DECLINE_TEXTS = ('Bijdrage verborgen wegens onvoldoende kwaliteit', 'Alleen zichtbaar voor jou en moderators',
                 'Afgewezen en verborgen voor andere lezers', 'Als openingsbericht blijft het zichtbaar',
                 'Niet aangemerkt als kwaliteitsbijdrage', 'maak bezwaar', 'gu-post--declined',
                 'gu-post--hidden', 'gu-quality--declined')


class TestNotAwardedStaysVisible:
    def test_visible_for_everyone_without_medal_or_message(self, app, world):
        anna, bram, mod = world['user'](), world['user'](), world['user']('moderator')
        t = world['thread']()
        declined = world['post'](anna, t, status='afgewezen')
        for viewer in (None, bram, anna, mod):
            html = _page(app, viewer, t)
            part = _part(html, declined)
            assert declined.body in part
            assert 'gu-quality__mark' not in part and 'gu-quality__medal' not in part
            for text in DECLINE_TEXTS:
                assert text not in html, (viewer, text)
            if viewer is not mod:  # alleen het moderatorpaneel toont de AI-bevindingen
                assert 'Te weinig onderbouwing.' not in part

    def test_replies_under_it_show_normally(self, app, world):
        anna, bram = world['user'](), world['user']()
        t = world['thread']()
        declined = world['post'](anna, t, status='afgewezen')
        reply = world['post'](bram, t, parent=declined)
        html = _page(app, world['user'](), t)
        assert declined.body in html and reply.body in html
        assert html.index(f'id="post-{declined.id}"') < html.index(f'id="post-{reply.id}"')

    def test_counted_everywhere(self, app, world):
        anna, bram = world['user'](), world['user']()
        t = world['thread']()
        root = world['post'](bram, t)
        world['post'](anna, t, parent=root, status='afgewezen')
        world['post'](anna, t, parent=root)
        reader = world['user']()
        html = _page(app, reader, t)
        assert re.search(r'3\s*reacties', html)
        assert f'id="collapsed-{root.id}" data-count="2"' in html
        index = _req(_client(app, reader), 'GET', '/forum').get_data(as_text=True)
        row = index[index.index(f'/forum/{t.id}"'):]
        assert re.search(r'3\s*reacties', row[:1500])

    def test_others_can_interact_with_it(self, app, world):
        anna, bram = world['user'](), world['user']()
        t = world['thread']()
        declined = world['post'](anna, t, status='afgewezen')
        client = _client(app, bram)
        assert _req(client, 'POST', f'/forum/stance/{declined.id}', data={'stance': 'eens'}).status_code == 200
        assert _req(client, 'POST', f'/forum/translate/{declined.id}').status_code != 404
        assert _req(client, 'GET', f'/forum/kwaliteit/{declined.id}/status').status_code == 200
        resp = _req(client, 'POST', f'/forum/{t.id}', data={'body': 'op niet-toegekend', 'parent_id': declined.id})
        assert resp.status_code == 302
        assert Post.query.filter_by(body='op niet-toegekend', parent_id=declined.id).count() == 1

    def test_opening_post_without_mark_is_just_visible(self, app, world):
        anna = world['user']()
        t = world['thread']()
        op = world['post'](anna, t, status='afgewezen', reason='Geen nieuw argument.', is_op=True)
        for viewer in (None, world['user'](), anna):
            part = _part(_page(app, viewer, t), op)
            assert op.body in part and 'gu-quality__mark' not in part
            assert 'Geen nieuw argument.' not in part
            for text in DECLINE_TEXTS:
                assert text not in part

    def test_awarded_keeps_medal_and_discussion_page(self, app, world):
        anna = world['user']()
        t = world['thread']()
        awarded = world['post'](anna, t, status='toegekend', reason='Nieuw argument over handel.')
        for viewer in (None, world['user']()):
            part = _part(_page(app, viewer, t), awarded)
            assert 'gu-quality__mark' in part and f'/forum/kwaliteit/{awarded.id}' in part
            page = _req(_client(app, viewer), 'GET', f'/forum/kwaliteit/{awarded.id}')
            assert page.status_code == 200 and 'Nieuw argument over handel.' in page.get_data(as_text=True)


# ── (5) Geen afbeeldingen meer bijvoegen ─────────────────────────────────

class TestNoImageUpload:
    def test_no_file_inputs_in_post_forms(self, app, world):
        anna, bram = world['user'](), world['user']()
        t = world['thread']()
        world['post'](bram, t)
        for path in (f'/forum/{t.id}', '/forum/nieuw'):
            html = _req(_client(app, anna), 'GET', path).get_data(as_text=True)
            assert 'type="file"' not in html and 'gu-upload-label' not in html, path

    def test_uploaded_file_is_ignored(self, app, world):
        import io
        anna = world['user']()
        t = world['thread']()
        image = (io.BytesIO(b'\x89PNG fake'), 'foto.png')
        _req(_client(app, anna), 'POST', f'/forum/{t.id}', data={'body': 'met foto', 'image': image},
             content_type='multipart/form-data')
        p = Post.query.filter_by(body='met foto').one()
        assert p.image_url is None
        image = (io.BytesIO(b'\x89PNG fake'), 'foto.png')
        _req(_client(app, anna), 'POST', f'/forum/{t.id}', data={'body': '', 'image': image},
             content_type='multipart/form-data')
        assert Post.query.filter_by(thread_id=t.id, body=None).count() == 0


# ── (8) Statuspoll ───────────────────────────────────────────────────────

class TestStatusPoll:
    def test_pending_then_awarded_or_no_mark(self, app, db, world):
        anna, mod = world['user'](), world['user']('moderator')
        t = world['thread']()
        p = world['post'](anna, t)
        client = _client(app, anna)
        assert _req(client, 'GET', f'/forum/kwaliteit/{p.id}/status').get_json() == {'status': 'pending'}
        db.session.add(QualityAward(post_id=p.id, source='ai', status='toegekend', criterion='K1',
                                    ai_criterion='K1', post_lang='nl', explanation_nl='Nieuw argument.'))
        db.session.commit()
        data = _req(client, 'GET', f'/forum/kwaliteit/{p.id}/status').get_json()
        assert data['status'] == 'toegekend' and data['criterion'] == 'K1' and 'reason' not in data
        assert '<details class="gu-quality">' in data['html'] and 'Nieuw argument.' in data['html']
        assert f'/forum/{t.id}#post-' not in data['html'] or 'status' not in data['html']

        # Geen keurmerk: alleen "geen medaille", geen reden, ook niet voor
        # de auteur of een moderator.
        q = world['post'](anna, t, status='afgewezen', reason='Stond er al.')
        for viewer in (anna, mod):
            data = _req(_client(app, viewer), 'GET', f'/forum/kwaliteit/{q.id}/status').get_json()
            assert data['status'] == 'geen' and data['criterion'] is None and 'reason' not in data
            assert data['html'].strip() == '' and 'Stond er al.' not in data['html']

    def test_login_required(self, app, world):
        p = world['post'](world['user'](), world['thread']())
        resp = _req(_client(app), 'GET', f'/forum/kwaliteit/{p.id}/status')
        assert resp.status_code == 302 and '/login' in resp.headers['Location']

    def test_poll_marker_only_on_own_fresh_unjudged_posts(self, app, world):
        anna, bram = world['user'](), world['user']()
        t = world['thread']()
        fresh = world['post'](anna, t)
        old = world['post'](anna, t, created_at=datetime.utcnow() - timedelta(minutes=10))
        judged = world['post'](anna, t, status='toegekend')
        others = world['post'](bram, t)
        html = _page(app, anna, t)
        polled = set(re.findall(r'data-quality-poll="/forum/kwaliteit/(\d+)/status"', html))
        assert polled == {str(fresh.id)}
        assert str(old.id) not in polled and str(judged.id) not in polled and str(others.id) not in polled
        # Vanuit Bram gezien: alleen zijn eigen verse bijdrage, niet die van Anna.
        bram_polled = set(re.findall(r'data-quality-poll="/forum/kwaliteit/(\d+)/status"', _page(app, bram, t)))
        assert bram_polled == {str(others.id)}
        assert "if (Date.now() - started > 60000) return;" in html  # stopt na ongeveer 60 seconden


# ── (6) Geen uitleg over vertalen ────────────────────────────────────────

class TestNoTranslateHint:
    def test_texts_removed_button_kept(self, app, world):
        anna, bram = world['user'](), world['user']()
        t = world['thread']()
        p = Post(thread_id=t.id, user_id=bram.id, author_name='', author_username=bram.username,
                 body='An English post.', source_lang='en')
        from app import db
        db.session.add(p)
        db.session.commit()
        html = _page(app, anna, t)
        assert 'Reacties in andere talen kun je vertalen' not in html
        assert 'Je post wordt op verzoek vertaald voor andere lezers' not in html
        assert 'gu-post__translate-btn' in html


# ── (2), (3), (4) Registratie ────────────────────────────────────────────

class TestRegistrationForm:
    def test_privacy_note_rules_block_and_client_validation(self, app):
        html = _req(_client(app), 'GET', '/aanmelden').get_data(as_text=True)
        assert 'Deze gegevens blijven privé.' in html
        block = html[html.index('class="gu-join__rules"'):html.index('id="accept_rules"')]
        for anchor in ('gedrag-1', 'gedrag-2', 'gedrag-3', 'kwaliteit-1', 'kwaliteit-2'):
            assert f'id="{anchor}"' in block
        assert 'gu-rules--compact' in block and 'Over het idee, niet de persoon' in block
        assert "form.addEventListener('submit'" in html and 'id="rulesError"' in html

    def test_rules_page_without_intro(self, app):
        html = _req(_client(app), 'GET', '/spelregels').get_data(as_text=True)
        assert 'Global Union Forum is een plek voor een serieus gesprek' not in html
        assert 'id="gedragsregels"' in html and 'id="kwaliteitsrichtlijnen"' in html

    def test_server_error_keeps_fields_but_not_password(self, app):
        client = _client(app)
        data = {'username': f'r_{uuid.uuid4().hex[:6]}', 'email': f'{uuid.uuid4().hex[:6]}@example.com',
                'first_name': 'Sara', 'last_name': 'De Vries', 'linkedin_url': 'https://www.linkedin.com/in/sara',
                'password': 'ValidPassword123!', 'password_confirm': 'ValidPassword123!'}
        assert _req(client, 'POST', '/aanmelden', data=data).status_code == 303
        html = _req(client, 'GET', '/aanmelden').get_data(as_text=True)
        for key in ('username', 'email', 'first_name', 'last_name', 'linkedin_url'):
            assert f'value="{data[key]}"' in html
        assert 'ValidPassword123!' not in html
        assert User.query.filter_by(email=data['email']).count() == 0


# ── Spelregels in twee groepen, en registratieteksten ────────────────────

def _section(html, sid):
    start = html.index(f'id="{sid}"')
    end = html.find('</section>', start)
    return html[start:end]


RULES = {
    'nl': {'conduct': ('Gedragsregels', 'Deze regels gelden voor iedereen in het forum.',
                       ['Over het idee, niet de persoon', 'Bij het onderwerp', 'Eén account per persoon']),
           'quality': ('Kwaliteitsrichtlijnen',
                       'Dit is geen verplichting. Volg je ze, dan verdient je bijdrage eerder het kwaliteitskeurmerk.',
                       ['Onderbouwd', 'Concreet']),
           'texts': ['Bekritiseer argumenten, geen mensen.', 'Reageer op de vraag van het gesprek.',
                     'Iedereen spreekt met één stem.', 'Een feit heeft een bron.',
                     'Maak je punt zo specifiek mogelijk']},
    'en': {'conduct': ('Conduct rules', 'These rules apply to everyone in the forum.',
                       ['About the idea, not the person', 'On topic', 'One account per person']),
           'quality': ('Quality guidelines',
                       'This is not an obligation. Follow them and your contribution is more likely to earn the quality mark.',
                       ['Substantiated', 'Concrete']),
           'texts': ['Criticise arguments, not people.', 'Respond to the question of the conversation.',
                     'Everyone speaks with one voice.', 'A fact has a source.',
                     'Make your point as specific as possible']},
}


class TestRulesInTwoGroups:
    @pytest.mark.parametrize('lang', ['nl', 'en'])
    @pytest.mark.parametrize('path', ['/spelregels', '/aanmelden'])
    def test_two_groups_with_intro_and_existing_texts(self, app, lang, path):
        html = _req(_client(app, lang=lang), 'GET', path).get_data(as_text=True)
        expected = RULES[lang]
        for sid, key, other in (('gedragsregels', 'conduct', 'quality'), ('kwaliteitsrichtlijnen', 'quality', 'conduct')):
            title, intro, rules = expected[key]
            part = _section(html, sid)
            assert title in part and intro in part
            for rule in rules:
                assert f'>{rule}</' in part, (lang, path, rule)
            for rule in expected[other][2]:
                assert f'>{rule}</' not in part, (lang, path, rule)
        for text in expected['texts']:
            assert text in html
        rules_html = html[html.index('id="gedragsregels"'):html.index('</section>', html.index('id="kwaliteitsrichtlijnen"'))]
        assert '\u2014' not in rules_html and '\u2013' not in rules_html  # geen lange of korte streepjes

    def test_other_language_does_not_leak(self, app):
        nl = _req(_client(app, lang='nl'), 'GET', '/aanmelden').get_data(as_text=True)
        en = _req(_client(app, lang='en'), 'GET', '/aanmelden').get_data(as_text=True)
        for text in ('Conduct rules', 'Quality guidelines', 'I accept the conduct rules',
                     'You must be at least 16 years old.', 'Password: 8 to 72 characters'):
            assert text not in nl and text in en
        assert 'Deze gegevens blijven privé.' in nl  # de catalogus is compleet, geen terugval op Engels

    @pytest.mark.parametrize('lang,moderators,old', [
        ('nl', 'Moderators houden toezicht op de gedragsregels en kunnen een gesprek sluiten.', 'bewerken of verwijderen'),
        ('en', 'Moderators oversee the conduct rules and can close a conversation.', 'edit or remove'),
    ])
    def test_moderator_sentence(self, app, lang, moderators, old):
        html = _req(_client(app, lang=lang), 'GET', '/spelregels').get_data(as_text=True)
        assert moderators in html and old not in html

    @pytest.mark.parametrize('lang,texts', [
        ('nl', ('Je moet minstens 16 jaar zijn.',
                'Wachtwoord: 8 tot 72 tekens, met minstens een hoofdletter, een cijfer en een speciaal teken.',
                'Ik ga akkoord met de gedragsregels')),
        ('en', ('You must be at least 16 years old.',
                'Password: 8 to 72 characters, with at least one capital letter, one number and one special character.',
                'I accept the conduct rules')),
    ])
    def test_registration_texts(self, app, lang, texts):
        html = _req(_client(app, lang=lang), 'GET', '/aanmelden').get_data(as_text=True)
        for text in texts:
            assert text in html
        assert 'Password must be between' not in html and 'Wachtwoord moet tussen' not in html
        # Het vinkje volgt op beide groepen: je accepteert de gedragsregels, de
        # richtlijnen zijn alleen informatie.
        assert html.index('id="kwaliteitsrichtlijnen"') < html.index('id="accept_rules"')

    def test_missing_checkbox_error_mentions_conduct_rules(self, app):
        data = {'username': f'r_{uuid.uuid4().hex[:6]}', 'email': f'{uuid.uuid4().hex[:6]}@example.com',
                'password': 'ValidPassword123!', 'password_confirm': 'ValidPassword123!'}
        client = _client(app)
        _req(client, 'POST', '/aanmelden', data=data)
        html = _req(client, 'GET', '/aanmelden').get_data(as_text=True)
        assert 'Ga akkoord met de gedragsregels om verder te gaan.' in html

    def test_accept_button_and_accepted_note_on_rules_page(self, app, world, db):
        name = f'f_{uuid.uuid4().hex[:8]}'
        fresh = User(username=name, email=f'{name}@example.com', verified=True,
                     password_hash=bcrypt.generate_password_hash('Sterk1!ww').decode('utf-8'))
        db.session.add(fresh)
        db.session.commit()
        try:
            html = _req(_client(app, fresh), 'GET', '/spelregels').get_data(as_text=True)
            assert 'Om mee te doen aan het gesprek ga je eerst akkoord met de gedragsregels.' in html
            assert 'Ik ga akkoord met de gedragsregels' in html
            accepted = _req(_client(app, world['user']()), 'GET', '/spelregels').get_data(as_text=True)
            assert 'Je hebt de gedragsregels geaccepteerd op' in accepted
            anon = _req(_client(app, lang='en'), 'GET', '/spelregels').get_data(as_text=True)
            assert 'When you sign up, you agree to the conduct rules.' in anon
        finally:
            db.session.delete(db.session.get(User, fresh.id))
            db.session.commit()


# ── Wachtwoordhint en forummelding gelijkgetrokken ───────────────────────

PW_HINT = {
    'nl': 'Wachtwoord: 8 tot 72 tekens, met minstens een hoofdletter, een cijfer en een speciaal teken.',
    'en': 'Password: 8 to 72 characters, with at least one capital letter, one number and one special character.',
}
OLD_PW_HINT = ('Password must be between', 'Wachtwoord moet tussen')


class TestAlignedTexts:
    @pytest.mark.parametrize('lang', ['nl', 'en'])
    def test_password_hint_on_profile_and_reset_page(self, app, world, lang):
        from app.routes.auth import _make_reset_token
        user = world['user']()
        pages = {'profile': _req(_client(app, user, lang), 'GET', '/profile').get_data(as_text=True),
                 'reset': _req(_client(app, lang=lang), 'GET',
                               f'/wachtwoord-reset/{_make_reset_token(user.email)}').get_data(as_text=True),
                 'aanmelden': _req(_client(app, lang=lang), 'GET', '/aanmelden').get_data(as_text=True)}
        for name, html in pages.items():
            assert PW_HINT[lang] in html, name
            for old in OLD_PW_HINT:
                assert old not in html, (name, old)

    @pytest.mark.parametrize('lang', ['nl', 'en'])
    def test_server_validation_message(self, app, lang):
        from flask import session
        from wtforms.validators import ValidationError
        from app.forms import _validate_password_strong
        with app.test_request_context('/'):
            session['lang'] = lang
            refresh()
            with pytest.raises(ValidationError) as exc:
                _validate_password_strong(None, type('F', (), {'data': 'zwak'})())
        assert str(exc.value) == PW_HINT[lang]

    @pytest.mark.parametrize('lang,text', [('nl', 'Accepteer eerst de gedragsregels om mee te doen.'),
                                           ('en', 'Accept the conduct rules first to take part.')])
    def test_forum_notice_mentions_conduct_rules(self, app, world, db, lang, text):
        name = f'f_{uuid.uuid4().hex[:8]}'
        fresh = User(username=name, email=f'{name}@example.com', verified=True,
                     password_hash=bcrypt.generate_password_hash('Sterk1!ww').decode('utf-8'))
        db.session.add(fresh)
        db.session.commit()
        try:
            client = _client(app, fresh, lang)
            resp = _req(client, 'GET', '/forum/nieuw')
            assert resp.status_code == 302 and '/spelregels' in resp.headers['Location']
            page = _req(client, 'GET', resp.headers['Location']).get_data(as_text=True)
            assert text in page and 'spelregels om mee te doen aan het gesprek' not in page
            # De pagina zelf heet nog steeds Spelregels / Ground rules.
            assert ('<h1 class="gu-profile__title">Spelregels</h1>' if lang == 'nl'
                    else '<h1 class="gu-profile__title">Ground rules</h1>') in page
        finally:
            db.session.delete(db.session.get(User, fresh.id))
            db.session.commit()


# ── Alle acceptatieteksten verwijzen naar de gedragsregels ───────────────

class TestAcceptanceTextsConsistent:
    @pytest.mark.parametrize('lang,text,old', [
        ('nl', 'Accepteer de gedragsregels om mee te doen', 'Accepteer de spelregels'),
        ('en', 'Accept the conduct rules to join in', 'Accept the ground rules'),
    ])
    def test_thread_and_forum_index_for_unaccepted_user(self, app, world, db, lang, text, old):
        name = f'f_{uuid.uuid4().hex[:8]}'
        fresh = User(username=name, email=f'{name}@example.com', verified=True,
                     password_hash=bcrypt.generate_password_hash('Sterk1!ww').decode('utf-8'))
        db.session.add(fresh)
        db.session.commit()
        t = world['thread']()
        world['post'](world['user'](), t)
        try:
            thread_html = _page(app, fresh, t, lang)
            locked = thread_html[thread_html.index('gu-composer--locked'):]
            locked = locked[:locked.index('</div>')]
            assert f'{text}</a>.' in locked and '/spelregels' in locked
            index = _req(_client(app, fresh, lang), 'GET', '/forum').get_data(as_text=True)
            assert text in index
            for html in (thread_html, index):
                assert old not in html
                # De pagina zelf heet nog steeds Spelregels / Ground rules (footerlink).
                assert ('>Spelregels</a>' if lang == 'nl' else '>Ground rules</a>') in html
        finally:
            db.session.delete(db.session.get(User, fresh.id))
            db.session.commit()


# ── Privacyverklaring ────────────────────────────────────────────────────

PRIVACY = {
    'nl': ['Privacyverklaring Global Union Forum', 'Laatst bijgewerkt: 24 oktober 2026',
           'Stichting Global Union Forum (in oprichting)', 'Welke gegevens we verzamelen',
           'Anthropic (automatische vertaling en het kwaliteitsoordeel)',
           'standaardcontractbepalingen van de Europese Commissie',
           'Technische loggegevens bewaren we zes maanden.',
           'Je moet minstens 16 jaar zijn om een account aan te maken.',
           'Autoriteit Persoonsgegevens', 'De datum bovenaan laat zien wanneer we dat voor het laatst deden.'],
    'en': ['Privacy statement Global Union Forum', 'Last updated: 24 October 2026',
           'Stichting Global Union Forum (in formation)', 'What data we collect',
           'Anthropic (automatic translation and the quality assessment)',
           "European Commission's standard contractual clauses",
           'We keep technical log data for six months.',
           'You must be at least 16 years old to create an account.',
           'Dutch Data Protection Authority (Autoriteit Persoonsgegevens)',
           'The date at the top shows when we last did so.'],
}


class TestPrivacyPage:
    @pytest.mark.parametrize('lang', ['nl', 'en'])
    def test_page_loads_in_both_languages(self, app, lang):
        resp = _req(_client(app, lang=lang), 'GET', '/privacy')
        html = resp.get_data(as_text=True)
        assert resp.status_code == 200
        for text in PRIVACY[lang]:
            assert text in html, (lang, text)
        other = PRIVACY['en' if lang == 'nl' else 'nl']
        assert other[0] not in html and other[1] not in html
        assert html.count('href="mailto:privacy@globalunionforum.org"') == 2
        main = html[html.index('gu-profile__head'):html.index('<footer')]
        assert '—' not in main and '–' not in main

    @pytest.mark.parametrize('lang', ['nl', 'en'])
    def test_link_on_registration_page_and_in_footer(self, app, lang):
        html = _req(_client(app, lang=lang), 'GET', '/aanmelden').get_data(as_text=True)
        form = html[html.index('class="gu-join__panel"'):html.index('</form>')]
        assert '<a href="/privacy" class="gu-join__privacyLink">Privacy</a>' in form
        footer = html[html.index('<footer'):]
        rules = '>Spelregels</a>' if lang == 'nl' else '>Ground rules</a>'
        after_rules = footer[footer.index(rules):]
        assert after_rules.index('<a href="/privacy">Privacy</a>') < after_rules.index('</ul>')
