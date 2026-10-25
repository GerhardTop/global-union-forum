"""
Fase E, stap 1: het voorstel als bijzonder soort gesprek (app/proposals.py,
het model Proposal en forum_nieuw).

Dekt: een voorstel aanmaken (publiek en event); de instellingen worden
bewaard; bij een publiek voorstel de juiste begin- en einddatums; de maxima
per fase (standaard en via de admin); de stappenbalk en het statuslabel met
de eerste fase, op de voorstelpagina en in het overzicht; de statuslabels
van latere fasen; en een gewoon gesprek gedraagt zich als voorheen.

Gedeelde conftest-app (CSRF uit).
"""
import re
import uuid
from datetime import datetime, timedelta

import pytest
from flask import g
from flask_babel import refresh

from app import bcrypt, proposals
from app.models import AppSetting, Post, Proposal, Thread, User


@pytest.fixture
def make_user(db):
    made = []

    def _make(admin=False):
        name = f'v_{uuid.uuid4().hex[:8]}'
        u = User(username=name, email=f'{name}@example.com', verified=True, is_admin=admin,
                 password_hash=bcrypt.generate_password_hash('Sterk1!ww').decode('utf-8'))
        u.set_role('contributor')
        u.accept_rules()
        db.session.add(u)
        db.session.commit()
        made.append(u.id)
        return u

    yield _make
    from app import quality
    for tid in [t.id for t in Thread.query.join(Post).filter(Post.user_id.in_(made)).distinct()]:
        thread = db.session.get(Thread, tid)
        ids = [p.id for p in Post.query.filter_by(thread_id=tid)]
        quality.delete_for_posts(ids)
        for p in Post.query.filter_by(thread_id=tid):
            db.session.delete(p)
        db.session.flush()
        db.session.delete(thread)  # het voorstel gaat mee (cascade)
    for key in list(proposals._SETTING_KEYS.values()):
        row = db.session.get(AppSetting, key)
        if row is not None:
            db.session.delete(row)
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


def _start(app, user, title, **extra):
    data = {'title': title, 'body': 'Toelichting bij het voorstel.', **extra}
    return _req(_client(app, user), 'POST', '/forum/nieuw', data=data)


PUBLIC = {'type': 'voorstel', 'kind': 'publiek',
          'duur_feiten': '7', 'eenheid_feiten': 'dagen',
          'duur_bijstellen': '48', 'eenheid_bijstellen': 'uren',
          'duur_stemmen': '5', 'eenheid_stemmen': 'dagen'}


def _thread(title):
    return Thread.query.filter_by(title_prefix_nl=title).one()


class TestCreate:
    def test_public_proposal_with_settings_and_schedule(self, app, db, make_user):
        anna = make_user()
        before = datetime.utcnow()
        resp = _start(app, anna, 'Stelling publiek', live='1', **PUBLIC)
        assert resp.status_code == 302
        t = _thread('Stelling publiek')
        p = t.proposal
        assert (p.kind, p.phase, p.threshold_pct, p.show_live_tally, p.host_id) == ('publiek', 'feiten', 50, True, anna.id)
        assert (p.feiten_minutes, p.bijstellen_minutes, p.stemmen_minutes) == (7 * 1440, 48 * 60, 5 * 1440)
        assert before <= p.started_at <= datetime.utcnow()
        # Vaste datums: elke fase begint waar de vorige eindigt.
        assert p.feiten_start == p.started_at
        assert p.feiten_end == p.started_at + timedelta(days=7)
        assert p.bijstellen_start == p.feiten_end and p.bijstellen_end == p.feiten_end + timedelta(hours=48)
        assert p.stemmen_start == p.bijstellen_end and p.stemmen_end == p.bijstellen_end + timedelta(days=5)
        assert p.window('vervolg') == (p.stemmen_end, None)
        assert t.eyebrow_nl == 'Forum · voorstel' and t.eyebrow_en == 'Forum · proposal'
        assert Post.query.filter_by(thread_id=t.id, is_op=True).one().body == 'Toelichting bij het voorstel.'

    def test_event_without_durations(self, app, db, make_user):
        anna = make_user()
        assert _start(app, anna, 'Stelling event', type='voorstel', kind='event').status_code == 302
        p = _thread('Stelling event').proposal
        assert (p.kind, p.phase, p.threshold_pct, p.show_live_tally, p.host_id) == ('event', 'feiten', 50, False, anna.id)
        assert all(getattr(p, f'{ph}_{part}') is None for ph in ('feiten', 'bijstellen', 'stemmen')
                   for part in ('minutes', 'start', 'end'))

    def test_schedule_from_fixed_start(self):
        start = datetime(2026, 11, 1, 9, 0)
        windows = proposals.schedule(start, {'feiten': 60, 'bijstellen': 1440, 'stemmen': 90})
        assert windows == {'feiten': (start, datetime(2026, 11, 1, 10, 0)),
                           'bijstellen': (datetime(2026, 11, 1, 10, 0), datetime(2026, 11, 2, 10, 0)),
                           'stemmen': (datetime(2026, 11, 2, 10, 0), datetime(2026, 11, 2, 11, 30))}


class TestMaximums:
    @pytest.mark.parametrize('field,value,unit,message', [
        ('feiten', '61', 'dagen', 'Voor deze fase maximaal 60 dagen.'),
        ('bijstellen', '241', 'uren', 'Voor deze fase maximaal 10 dagen.'),
        ('stemmen', '15', 'dagen', 'Voor deze fase maximaal 14 dagen.'),
        ('feiten', '0', 'dagen', 'Vul een geheel getal groter dan nul in.'),
        ('stemmen', 'twee', 'dagen', 'Vul een geheel getal groter dan nul in.'),
    ])
    def test_refused_and_form_keeps_values(self, app, db, make_user, field, value, unit, message):
        anna = make_user()
        data = dict(PUBLIC, **{f'duur_{field}': value, f'eenheid_{field}': unit})
        client = _client(app, anna)
        resp = _req(client, 'POST', '/forum/nieuw', data={'title': 'Te lang', 'body': 'Uitleg.', **data})
        assert resp.status_code == 303 and resp.headers['Location'].endswith('/forum/nieuw')
        assert Thread.query.filter_by(title_prefix_nl='Te lang').count() == 0
        html = _req(client, 'GET', '/forum/nieuw').get_data(as_text=True)
        assert message in html and 'value="Te lang"' in html and 'Uitleg.</textarea>' in html
        assert 'name="type" value="voorstel" checked' in html

    def test_exact_maximum_is_allowed(self, app, db, make_user):
        data = dict(PUBLIC, duur_feiten='60', duur_bijstellen='240', eenheid_bijstellen='uren', duur_stemmen='14')
        assert _start(app, make_user(), 'Precies maximaal', **data).status_code == 302
        assert _thread('Precies maximaal').proposal.feiten_minutes == 60 * 1440

    def test_admin_sets_maximums(self, app, db, make_user):
        admin, anna = make_user(admin=True), make_user()
        assert proposals.get_max_days() == {'feiten': 60, 'bijstellen': 10, 'stemmen': 14}
        assert _req(_client(app, anna), 'GET', '/admin/voorstellen').status_code == 403
        page = _req(_client(app, admin), 'GET', '/admin/voorstellen').get_data(as_text=True)
        assert 'Standaard: 60 dagen' in page and 'Standaard: 14 dagen' in page
        resp = _req(_client(app, admin), 'POST', '/admin/voorstellen',
                    data={'feiten': '5', 'bijstellen': '3', 'stemmen': '7'})
        assert resp.status_code == 302
        assert proposals.get_max_days() == {'feiten': 5, 'bijstellen': 3, 'stemmen': 7}
        assert _start(app, anna, 'Na nieuw maximum', **dict(PUBLIC, duur_feiten='6')).status_code == 303
        assert _start(app, anna, 'Binnen nieuw maximum', **dict(PUBLIC, duur_feiten='5')).status_code == 302
        bad = _req(_client(app, admin), 'POST', '/admin/voorstellen', data={'feiten': '0', 'bijstellen': '3', 'stemmen': '7'})
        assert 'Vul een geheel getal van 1 tot en met 365 in.' in bad.get_data(as_text=True)


class TestDisplay:
    STEPS_NL = ('Feiten en discussie', 'Voorstel bijstellen', 'Stemmen', 'Vervolg')
    STEPS_EN = ('Facts and discussion', 'Revise the proposal', 'Voting', 'Follow-up')

    @pytest.mark.parametrize('lang', ['nl', 'en'])
    def test_public_steps_with_dates_and_status(self, app, db, make_user, lang):
        anna = make_user()
        _start(app, anna, 'Zichtbaar voorstel', **PUBLIC)
        t = _thread('Zichtbaar voorstel')
        html = _req(_client(app, lang=lang), 'GET', f'/forum/{t.id}').get_data(as_text=True)
        steps = html[html.index('class="gu-proposal-steps"'):html.index('</ol>', html.index('class="gu-proposal-steps"'))]
        names = self.STEPS_NL if lang == 'nl' else self.STEPS_EN
        positions = [steps.index(n) for n in names]
        assert positions == sorted(positions)
        current = re.findall(r'<li class="gu-proposal-steps__step is-current" aria-current="step">', steps)
        assert len(current) == 1
        first = steps[:steps.index('</li>')]
        assert 'is-current' in first and names[0] in first
        explanation = ('Breng feiten en argumenten in en bespreek het voorstel.' if lang == 'nl'
                       else 'Bring in facts and arguments and discuss the proposal.')
        assert explanation in first
        # Datums bij een publiek voorstel: elke fase met begin en eind, vervolg vanaf.
        assert steps.count('class="gu-proposal-steps__when"') == 4
        assert (' tot ' if lang == 'nl' else ' to ') in steps and ('vanaf ' if lang == 'nl' else 'from ') in steps
        status = 'In discussie' if lang == 'nl' else 'In discussion'
        assert f'gu-proposal-status--in_discussie">{status}</span>' in html
        assert ('Publiek voorstel' if lang == 'nl' else 'Public proposal') in html
        assert ('Aangenomen bij meer dan 50% voor' if lang == 'nl' else 'Adopted with more than 50% in favour') in html

    def test_event_without_dates_and_status_in_overview(self, app, db, make_user):
        _start(app, make_user(), 'Event zonder datums', type='voorstel', kind='event')
        t = _thread('Event zonder datums')
        html = _req(_client(app), 'GET', f'/forum/{t.id}').get_data(as_text=True)
        assert 'class="gu-proposal-steps"' in html and 'gu-proposal-steps__when' not in html
        assert 'Tussenstand verborgen tot het stemmen sluit' in html
        index = _req(_client(app), 'GET', '/forum').get_data(as_text=True)
        row = index[index.index(f'/forum/{t.id}"'):]
        row = row[:row.index('</li>')]
        assert 'gu-proposal-status--in_discussie">In discussie</span>' in row

    def test_later_phase_labels(self, app, db, make_user):
        _start(app, make_user(), 'Fasen later', **PUBLIC)
        _start(app, make_user(), 'Event later', type='voorstel', kind='event')
        public, event = _thread('Fasen later').proposal, _thread('Event later').proposal
        # Vervolg zonder vastgelegde uitslag (met de hand gezet): de terugval "Gesloten".
        cases = [(public, 'stemmen', 'Stemmen open tot '), (public, 'bijstellen', 'Wordt bijgesteld'),
                 (public, 'vervolg', '>Gesloten<'), (event, 'stemmen', '>Stemmen open<')]
        for proposal, phase, text in cases:
            proposal.phase = phase
            db.session.commit()
            html = _req(_client(app), 'GET', f'/forum/{proposal.thread_id}').get_data(as_text=True)
            assert text in html, (phase, text)
            steps = html[html.index('class="gu-proposal-steps"'):]
            done = steps.count('gu-proposal-steps__step is-done')
            assert done == ('feiten', 'bijstellen', 'stemmen', 'vervolg').index(phase)


class TestRegularConversation:
    def test_unchanged_without_status_or_steps(self, app, db, make_user):
        anna = make_user()
        form = _req(_client(app, anna), 'GET', '/forum/nieuw').get_data(as_text=True)
        assert 'name="type" value="gesprek" checked' in form
        assert _start(app, anna, 'Gewoon gesprek').status_code == 302
        t = _thread('Gewoon gesprek')
        assert t.proposal is None and t.eyebrow_nl == 'Forum · gesprek'
        assert Proposal.query.filter_by(thread_id=t.id).count() == 0
        html = _req(_client(app), 'GET', f'/forum/{t.id}').get_data(as_text=True)
        assert 'gu-proposal' not in html and 'In discussie' not in html
        index = _req(_client(app), 'GET', '/forum').get_data(as_text=True)
        row = index[index.index(f'/forum/{t.id}"'):]
        assert 'gu-proposal-status' not in row[:row.index('</li>')]

    def test_conversation_ignores_proposal_fields(self, app, db, make_user):
        """Een gewoon gesprek met (bijvoorbeeld door de browser meegestuurde)
        voorstelvelden wordt geen voorstel en geeft geen fouten."""
        data = dict(PUBLIC, type='gesprek', duur_feiten='999')
        assert _start(app, make_user(), 'Gesprek met velden', **data).status_code == 302
        assert _thread('Gesprek met velden').proposal is None


class TestCleanup:
    def test_author_deleting_opening_post_removes_proposal(self, app, db, make_user):
        anna = make_user()
        _start(app, anna, 'Weg met voorstel', **PUBLIC)
        t = _thread('Weg met voorstel')
        tid, pid = t.id, t.proposal.id
        op = Post.query.filter_by(thread_id=tid, is_op=True).one()
        assert _req(_client(app, anna), 'POST', f'/forum/post/{op.id}/verwijderen').status_code == 302
        db.session.expire_all()
        assert Thread.query.filter_by(id=tid).count() == 0 and Proposal.query.filter_by(id=pid).count() == 0
