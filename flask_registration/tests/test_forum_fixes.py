"""
Aanpassingen na een gebruikerstest (de nummers volgen de bevindingen):
(2) toelichting onder "optioneel", (3) registratie zonder het wachtwoord
opnieuw te typen, (4) spelregels compact in het formulier, (6) geen uitleg
over vertalen, (7) niet reageren op je eigen bericht, (8) statuspoll voor het
kwaliteitsoordeel, (9) afgewezen bijdragen verborgen voor anderen.

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


# ── (9) Afgewezen bijdragen verborgen voor anderen ───────────────────────

class TestDeclinedHidden:
    def test_hidden_for_others_visible_for_author_and_moderator(self, app, world):
        anna, bram, mod = world['user'](), world['user'](), world['user']('moderator')
        t = world['thread']()
        declined = world['post'](anna, t, status='afgewezen')
        pending = world['post'](anna, t)  # nog niet beoordeeld: gewoon zichtbaar
        for viewer in (None, bram):
            html = _page(app, viewer, t)
            assert f'id="post-{declined.id}"' not in html and declined.body not in html
            assert f'id="post-{pending.id}"' in html
        own = _page(app, anna, t)
        assert declined.body in own and 'Alleen zichtbaar voor jou en moderators' in own
        assert 'Te weinig onderbouwing.' in own
        moderated = _page(app, mod, t)
        assert declined.body in moderated and 'Afgewezen en verborgen voor andere lezers.' in moderated

    def test_placeholder_when_others_replied(self, app, world):
        anna, bram = world['user'](), world['user']()
        t = world['thread']()
        declined = world['post'](anna, t, status='afgewezen')
        reply = world['post'](bram, t, parent=declined)
        html = _page(app, world['user'](), t)
        assert 'Bijdrage verborgen wegens onvoldoende kwaliteit.' in html
        assert declined.body not in html and anna.username not in html
        assert reply.body in html
        # De reactie staat nog onder de placeholder (structuur blijft leesbaar).
        assert html.index(f'id="post-{declined.id}"') < html.index(f'id="post-{reply.id}"')

    def test_hidden_posts_not_counted(self, app, world):
        anna, bram = world['user'](), world['user']()
        t = world['thread']()
        root = world['post'](bram, t)
        world['post'](anna, t, parent=root, status='afgewezen')
        world['post'](anna, t, parent=root)
        reader = world['user']()
        html = _page(app, reader, t)
        assert '>2 reacties<' in html.replace('\n', '').replace('  ', '') or '2 reacties' in html
        assert 'data-label-hide="1 reactie verbergen"' in html  # alleen de zichtbare reactie
        index = _req(_client(app, reader), 'GET', '/forum').get_data(as_text=True)
        row = index[index.index(f'/forum/{t.id}"'):]
        assert re.search(r'2\s*reacties', row[:1500])

    def test_hidden_post_unreachable_for_others(self, app, world):
        anna, bram = world['user'](), world['user']()
        t = world['thread']()
        declined = world['post'](anna, t, status='afgewezen')
        client = _client(app, bram)
        assert _req(client, 'POST', f'/forum/stance/{declined.id}', data={'stance': 'eens'}).status_code == 404
        assert _req(client, 'POST', f'/forum/translate/{declined.id}').status_code == 404
        assert _req(client, 'GET', f'/forum/kwaliteit/{declined.id}/status').status_code == 404
        resp = _req(client, 'POST', f'/forum/{t.id}', data={'body': 'op verborgen', 'parent_id': declined.id})
        assert resp.status_code == 302 and Post.query.filter_by(body='op verborgen').count() == 0


class TestDeclinedOpeningPostStaysVisible:
    """Aanvulling op punt 9: een afgewezen openingsbericht blijft voor iedereen
    zichtbaar (zonder ruitje); een afgewezen reactie blijft verborgen."""

    def test_declined_opening_post_visible_declined_reply_hidden(self, app, world):
        anna, bram = world['user'](), world['user']()
        t = world['thread']()
        op = world['post'](anna, t, status='afgewezen', reason='Geen nieuw argument.', is_op=True)
        reply = world['post'](bram, t, parent=op, status='afgewezen')
        for viewer in (None, world['user']()):
            html = _page(app, viewer, t)
            part = html[html.index(f'id="post-{op.id}"') - 200:]
            part = part[:part.find('</article>')]
            assert op.body in part and 'gu-post--declined' not in part
            assert 'gu-quality__mark' not in part and 'Geen nieuw argument.' not in part
            assert f'id="post-{reply.id}"' not in html and reply.body not in html
        own = _page(app, anna, t)
        assert 'Als openingsbericht blijft het zichtbaar voor iedereen' in own and 'Geen nieuw argument.' in own
        assert 'Alleen zichtbaar voor jou en moderators' not in own.split(f'id="post-{op.id}"')[1].split('</article>')[0]

    def test_poll_and_counts_for_declined_opening_post(self, app, world):
        anna, bram = world['user'](), world['user']()
        t = world['thread']()
        op = world['post'](anna, t, status='afgewezen', is_op=True)
        data = _req(_client(app, bram), 'GET', f'/forum/kwaliteit/{op.id}/status').get_json()
        assert data['status'] == 'afgewezen' and data['reason'] is None  # geen reden voor anderen
        own = _req(_client(app, anna), 'GET', f'/forum/kwaliteit/{op.id}/status').get_json()
        assert own['reason'] == 'Te weinig onderbouwing.'
        index = _req(_client(app, bram), 'GET', '/forum').get_data(as_text=True)
        row = index[index.index(f'/forum/{t.id}"'):]
        assert re.search(r'1\s*reactie\b', row[:1500])


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
    def test_pending_then_awarded_and_declined(self, app, db, world):
        anna, mod = world['user'](), world['user']('moderator')
        t = world['thread']()
        p = world['post'](anna, t)
        client = _client(app, anna)
        assert _req(client, 'GET', f'/forum/kwaliteit/{p.id}/status').get_json() == {'status': 'pending'}
        db.session.add(QualityAward(post_id=p.id, source='ai', status='toegekend', criterion='K1',
                                    ai_criterion='K1', post_lang='nl', explanation_nl='Nieuw argument.'))
        db.session.commit()
        data = _req(client, 'GET', f'/forum/kwaliteit/{p.id}/status').get_json()
        assert data['status'] == 'toegekend' and data['criterion'] == 'K1' and data['reason'] is None
        assert '<details class="gu-quality">' in data['html'] and 'Nieuw argument.' in data['html']
        assert f'/forum/{t.id}#post-' not in data['html'] or 'status' not in data['html']

        q = world['post'](anna, t, status='afgewezen', reason='Stond er al.')
        data = _req(client, 'GET', f'/forum/kwaliteit/{q.id}/status').get_json()
        assert data['status'] == 'afgewezen' and data['reason'] == 'Stond er al.'
        assert 'Alleen zichtbaar voor jou en moderators' in data['html']
        mod_data = _req(_client(app, mod), 'GET', f'/forum/kwaliteit/{q.id}/status').get_json()
        assert mod_data['reason'] == 'Stond er al.'

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
        for r in ('R1', 'R2', 'R3', 'R4', 'R5'):
            assert f'>{r}<' in block
        assert 'gu-rules--compact' in block and 'Over het idee, niet de persoon' in block
        assert "form.addEventListener('submit'" in html and 'id="rulesError"' in html

    def test_rules_page_without_intro(self, app):
        html = _req(_client(app), 'GET', '/spelregels').get_data(as_text=True)
        assert 'Global Union Forum is een plek voor een serieus gesprek' not in html
        assert '>R5<' in html

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
