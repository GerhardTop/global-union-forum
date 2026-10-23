"""
Forum fase C2: verdiende status (app/status.py), automatische promotie,
admin-instelbare drempels en de taal-monitor.

Dekt: promotie naar contributor, voter, moderator (met de onderwerpen-eis) en
senior; niet omlaag na een ingetrokken oordeel of hogere drempels; geen
invloed op handmatige rollen (admin, moderator, founder/expert, role); een
verdiende moderator krijgt geen moderatiemacht; stemrecht vanaf voter;
gewijzigde drempels; de herberekening in de bestaande oordeelflow; de
eenmalige promotiemelding en de badge; de taal-monitor (per source_lang,
percentage, verschil, periode, steekproef); de migratie.

Gedeelde conftest-app (CSRF uit); _req() wist de in `g` gecachte current_user
en locale. Elke test maakt eigen gebruikers, dus de tellingen staan los van
andere tests; de taal-monitor wordt vergeleken met een nulmeting.
"""
import uuid
from datetime import datetime, timedelta

import pytest
from flask import g
from flask_babel import refresh
from sqlalchemy import text

from app import bcrypt, status
from app.models import (AppSetting, Post, QualityAward, QualityObjection, QualityPromotion,
                        QualityReport, Thread, User)


@pytest.fixture(autouse=True)
def _fresh_limits_and_thresholds(app, db):
    from app import limiter
    limiter.reset()
    yield
    for row in AppSetting.query.all():  # drempels terug naar de defaults
        db.session.delete(row)
    db.session.commit()


@pytest.fixture
def world(db):
    """Maakt gebruikers, gesprekken en bijdragen met oordelen; ruimt alles op."""
    made = {'users': [], 'threads': []}

    def user(role='contributor', **kw):
        name = f's_{uuid.uuid4().hex[:8]}'
        u = User(username=name, email=f'{name}@example.com', verified=True,
                 password_hash=bcrypt.generate_password_hash('Sterk1!ww').decode('utf-8'), **kw)
        if role:
            u.set_role(role)
        u.accept_rules()
        db.session.add(u)
        db.session.commit()
        made['users'].append(u.id)
        return u

    def thread():
        t = Thread(eyebrow_nl='Forum · gesprek', eyebrow_en='Forum · conversation',
                   title_prefix_nl='Status', title_prefix_en='Status')
        db.session.add(t)
        db.session.commit()
        made['threads'].append(t.id)
        return t

    def post(u, t, awarded=True, lang='nl', source='ai', ai_awarded=None, created_at=None, author_role='member'):
        p = Post(thread_id=t.id, user_id=u.id if u else None, author_name='', author_role=author_role,
                 author_username=u.username if u else 'x', body=f'bijdrage {uuid.uuid4().hex[:6]}',
                 source_lang=lang)
        db.session.add(p)
        db.session.flush()
        ai = awarded if ai_awarded is None else ai_awarded
        db.session.add(QualityAward(post_id=p.id, source=source, status='toegekend' if awarded else 'afgewezen',
                                    criterion='K1' if awarded else None, ai_criterion='K1' if ai else None,
                                    post_lang=lang, created_at=created_at or datetime.utcnow()))
        db.session.commit()
        return p

    def awards(u, n, topics=1):
        threads = [thread() for _ in range(topics)]
        for i in range(n):
            post(u, threads[i % topics])
        return status.recalc_user(u.id)

    yield {'user': user, 'thread': thread, 'post': post, 'awards': awards}

    for tid in made['threads']:
        for p in Post.query.filter_by(thread_id=tid):
            if p.quality_award:
                for r in list(p.quality_award.reports) + list(p.quality_award.objections):
                    db.session.delete(r)
                db.session.delete(p.quality_award)
        db.session.flush()
        for p in Post.query.filter_by(thread_id=tid):
            db.session.delete(p)
        db.session.delete(db.session.get(Thread, tid))
    db.session.commit()
    for uid in made['users']:
        for r in QualityReport.query.filter((QualityReport.reporter_id == uid) | (QualityReport.decided_by_id == uid)):
            db.session.delete(r)
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


def _status(uid):
    from app import db
    db.session.expire_all()
    return db.session.get(User, uid).quality_status


# ── Promotie langs de ladder ─────────────────────────────────────────────

class TestPromotion:
    def test_contributor_after_first_award(self, world):
        u = world['user']()
        assert world['awards'](u, 1) == 'contributor'

    def test_voter_at_voter_min(self, world):
        u = world['user']()
        assert world['awards'](u, 2) == 'contributor'
        assert world['awards'](u, 1) == 'voter'  # 3 in totaal
        assert status.quality_counts(u.id) == (3, 2)

    def test_moderator_needs_count_and_topics(self, world):
        u = world['user']()
        assert world['awards'](u, 11, topics=2) == 'voter'  # 11 > 10, maar 2 < 3 onderwerpen
        v = world['user']()
        assert world['awards'](v, 10, topics=3) == 'voter'  # 10 is niet meer dan 10
        assert world['awards'](v, 1, topics=1) == 'moderator'  # 11 in 4 onderwerpen

    def test_senior_above_senior_min(self, world):
        u = world['user']()
        assert world['awards'](u, 100, topics=4) == 'moderator'
        assert world['awards'](u, 1) == 'senior'

    def test_promotion_log_records_each_level(self, world):
        u = world['user']()
        world['awards'](u, 3)
        levels = [p.level for p in QualityPromotion.query.filter_by(user_id=u.id).order_by(QualityPromotion.id)]
        assert levels == ['contributor', 'voter']

    def test_declined_and_deleted_posts_do_not_count(self, world):
        u = world['user']()
        t = world['thread']()
        world['post'](u, t, awarded=False)
        world['post'](None, t)  # geen auteur (verwijderd account)
        assert status.recalc_user(u.id) == 'reader' and _status(u.id) is None


# ── Alleen omhoog ────────────────────────────────────────────────────────

class TestNeverDown:
    def test_withdrawn_award_does_not_demote(self, app, db, world):
        u, mod = world['user'](), world['user']('moderator')
        t = world['thread']()
        posts = [world['post'](u, t) for _ in range(3)]
        assert status.recalc_user(u.id) == 'voter'
        objection = QualityObjection(award_id=posts[0].quality_award.id, author_id=u.id, text='x')
        db.session.add(objection)
        db.session.commit()
        resp = _req(_client(app, mod), 'POST', f'/forum/kwaliteit/bezwaar/{objection.id}/besluit',
                    data={'decision': 'intrekken'})
        assert resp.status_code == 302
        assert db.session.get(QualityAward, posts[0].quality_award.id).status == 'afgewezen'
        assert status.quality_counts(u.id)[0] == 2 and _status(u.id) == 'voter'

    def test_higher_thresholds_do_not_demote(self, world):
        u = world['user']()
        world['awards'](u, 3)
        status.set_thresholds({'voter_min': 50})
        assert status.recalc_user(u.id) == 'voter'


# ── Handmatige rollen en rechten ─────────────────────────────────────────

class TestManualRolesAndRights:
    def test_earned_status_never_changes_manual_roles(self, db, world):
        admin = world['user']('moderator', is_admin=True)
        mod = world['user']('moderator')
        senior = world['user']('senior_moderator')
        for u in (admin, mod, senior):
            world['awards'](u, 3)
        db.session.expire_all()
        admin, mod, senior = (db.session.get(User, x.id) for x in (admin, mod, senior))
        assert (admin.is_admin, admin.role) == (True, 'moderator')
        assert (mod.is_moderator, mod.role) == (True, 'moderator')
        assert (senior.role, senior.is_moderator) == ('senior_moderator', True)
        assert (admin.effective_status, mod.effective_status, senior.effective_status) == \
            ('senior', 'moderator', 'senior')
        assert all(u.earned_status == 'voter' for u in (admin, mod, senior))

    def test_founder_and_expert_badges_unchanged(self, db, world):
        u = world['user']()
        t = world['thread']()
        p = world['post'](u, t, author_role='founder')
        q = world['post'](u, t, author_role='expert')
        status.recalc_user(u.id)
        db.session.expire_all()
        assert (db.session.get(Post, p.id).author_role, db.session.get(Post, q.id).author_role) == ('founder', 'expert')

    def test_default_contributor_role_is_not_a_status(self, world):
        u = world['user']('contributor')
        assert u.manual_status == 'reader' and u.effective_status == 'reader'

    def test_effective_status_is_highest(self, world):
        voter_role = world['user']('voter')
        world['awards'](voter_role, 1)  # verdiend contributor, handmatig voter
        assert voter_role.effective_status == 'voter'
        plain = world['user']()
        world['awards'](plain, 3)
        assert plain.effective_status == 'voter'

    def test_earned_moderator_gets_no_moderation_power(self, app, world):
        u = world['user']()
        assert world['awards'](u, 11, topics=3) == 'moderator'
        assert u.can_moderate() is False and u.is_moderator is False and u.role == 'contributor'
        client = _client(app, u)
        assert _req(client, 'GET', '/moderator').status_code == 403
        t = world['thread']()
        assert _req(client, 'POST', f'/forum/{t.id}/close').status_code == 403
        assert _req(client, 'GET', '/admin/taalmonitor').status_code == 403

    def test_can_vote_from_voter(self, world):
        u = world['user']()
        world['awards'](u, 2)
        assert u.earned_status == 'contributor' and u.can_vote() is False
        world['awards'](u, 1)
        assert u.earned_status == 'voter' and u.can_vote() is True
        senior = world['user']()
        world['awards'](senior, 101, topics=3)
        assert senior.can_vote() is True


# ── Drempels ─────────────────────────────────────────────────────────────

class TestThresholds:
    def test_defaults_and_changed_thresholds(self, world):
        assert status.get_thresholds() == status.DEFAULT_THRESHOLDS
        u = world['user']()
        assert world['awards'](u, 2) == 'contributor'
        status.set_thresholds({'voter_min': 2})
        assert status.recalc_user(u.id) == 'voter'
        v = world['user']()
        status.set_thresholds({'moderator_min': 3, 'moderator_min_topics': 2})
        assert world['awards'](v, 4, topics=2) == 'moderator'

    def test_admin_page_saves_and_validates(self, app, world):
        admin = world['user']('moderator', is_admin=True)
        client = _client(app, admin)
        assert _req(client, 'GET', '/admin/drempels').status_code == 200
        data = {'voter_min': '5', 'moderator_min': '20', 'moderator_min_topics': '4', 'senior_min': '200'}
        assert _req(client, 'POST', '/admin/drempels', data=data).status_code == 302
        assert status.get_thresholds() == {k: int(v) for k, v in data.items()}
        bad = dict(data, voter_min='0')
        page = _req(client, 'POST', '/admin/drempels', data=bad).get_data(as_text=True)
        assert 'Vul een geheel getal van minimaal 1 in.' in page and status.get_thresholds()['voter_min'] == 5

    def test_only_admin_can_change_thresholds(self, app, world):
        assert _req(_client(app, world['user']('moderator')), 'GET', '/admin/drempels').status_code == 403


# ── Herberekening in de bestaande oordeelflow ────────────────────────────

class TestRecalcHooks:
    def test_moderator_decision_promotes_author(self, app, world):
        u, mod = world['user'](), world['user']('moderator')
        t = world['thread']()
        p = Post(thread_id=t.id, user_id=u.id, author_name='', author_username=u.username, body='zonder oordeel')
        from app import db
        db.session.add(p)
        db.session.commit()
        _req(_client(app, mod), 'POST', f'/forum/kwaliteit/{p.id}/besluit',
             data={'action': 'toekennen', 'criterion': 'K3', 'explanation': 'Eerlijk.'})
        assert _status(u.id) == 'contributor'

    def test_automatic_ai_award_promotes_author(self, app, db, world, monkeypatch):
        import json
        from types import SimpleNamespace
        import anthropic

        class Msgs:
            def create(self, **kw):
                if 'output_config' not in kw:
                    return SimpleNamespace(content=[SimpleNamespace(type='text', text='EN')])
                verdict = {'has_source': False, 'source_is_new': False, 'argument_is_new': True,
                           'suggested_criterion': 'K1', 'k3_possible': False, 'explanation': 'Nieuw.'}
                return SimpleNamespace(stop_reason='end_turn',
                                       content=[SimpleNamespace(type='text', text=json.dumps(verdict))])

        monkeypatch.setattr(anthropic, 'Anthropic', lambda **kw: SimpleNamespace(messages=Msgs()))
        monkeypatch.setitem(app.config, 'QUALITY_AI_INLINE', True)
        u = world['user']()
        t = world['thread']()
        _req(_client(app, u), 'POST', f'/forum/{t.id}', data={'body': 'Een nieuw argument.'})
        assert _status(u.id) == 'contributor'


# ── Weergave: badge en eenmalige melding ─────────────────────────────────

class TestDisplay:
    def test_no_status_badge_in_thread_but_on_profile(self, app, world):
        # Gelaagde weergave (punt 4): geen rol- of statusbadge meer bij de
        # auteur in het gesprek; de verdiende status blijft op het profiel.
        voter = world['user']()
        t = world['thread']()
        for _ in range(3):
            world['post'](voter, t)
        status.recalc_user(voter.id)
        html = _req(_client(app), 'GET', f'/forum/{t.id}').get_data(as_text=True)
        assert 'gu-status-badge' not in html.split('<main', 1)[1].split('</main>')[0]
        profile = _req(_client(app, voter), 'GET', '/profile').get_data(as_text=True)
        assert 'gu-status-badge--voter">Stemmer' in profile

    def test_one_time_banner_for_voter(self, app, world):
        u = world['user']()
        world['awards'](u, 1)
        client = _client(app, u)
        assert 'gu-promotion-banner' not in _req(client, 'GET', '/forum').get_data(as_text=True)  # contributor
        world['awards'](u, 2)
        page = _req(client, 'GET', '/forum').get_data(as_text=True)
        assert 'gu-promotion-banner' in page and 'met stemrecht' in page
        assert _req(client, 'POST', '/profiel/promotie-gezien', data={'next': '/forum'}).status_code == 302
        assert 'gu-promotion-banner' not in _req(client, 'GET', '/forum').get_data(as_text=True)

    def test_profile_shows_status_and_progress(self, app, world):
        u = world['user']()
        world['awards'](u, 2, topics=2)
        page = _req(_client(app, u), 'GET', '/profile').get_data(as_text=True)
        assert 'id="guStatus"' in page and 'Bijdrager' in page
        assert '2 kwaliteitsbijdragen in 2 onderwerpen' in page and 'Stemmer (met stemrecht) vanaf 3' in page


# ── Taal-monitor ─────────────────────────────────────────────────────────

class TestLanguageMonitor:
    def test_counts_per_source_lang_and_percentage(self, world):
        before = status.language_monitor('alles')
        u = world['user']()
        t = world['thread']()
        for awarded in (True, True, True, False):
            world['post'](u, t, awarded=awarded, lang='nl')
        for awarded in (True, False):
            world['post'](u, t, awarded=awarded, lang='en')
        # Taal volgt source_lang, niet post_lang; en het oordeel van de AI telt.
        p = world['post'](u, t, awarded=True, lang='nl')
        p.quality_award.post_lang = 'en'
        moved = world['post'](u, t, awarded=False, lang='en', ai_awarded=True)  # moderator trok in
        # Handgemaakte (demo/moderator) oordelen tellen niet mee.
        world['post'](u, t, awarded=True, lang='en', source='moderator')
        from app import db
        db.session.commit()
        after = status.language_monitor('alles')
        assert after['nl']['judged'] - before['nl']['judged'] == 5
        assert after['nl']['awarded'] - before['nl']['awarded'] == 4
        assert after['en']['judged'] - before['en']['judged'] == 3
        assert after['en']['awarded'] - before['en']['awarded'] == 2
        for code in ('nl', 'en'):
            row = after[code]
            assert row['pct'] == round(100.0 * row['awarded'] / row['judged'], 1)
        assert after['difference'] == round(after['nl']['pct'] - after['en']['pct'], 1)
        assert moved.quality_award.status == 'afgewezen'

    def test_percentage_math_and_no_data(self, monkeypatch):
        class Q:
            def __init__(self, rows): self.rows = rows
            def join(self, *a, **k): return self
            def filter(self, *a, **k): return self
            def group_by(self, *a, **k): return self
            def all(self): return self.rows
        from app import db
        monkeypatch.setattr(db.session, 'query', lambda *a, **k: Q([('nl', 4, 3), ('en', 8, 2)]))
        r = status.language_monitor('alles')
        assert (r['nl']['pct'], r['en']['pct'], r['difference']) == (75.0, 25.0, 50.0)
        monkeypatch.setattr(db.session, 'query', lambda *a, **k: Q([('nl', 3, 1)]))
        r = status.language_monitor('alles')
        assert r['nl']['pct'] == 33.3 and r['en'] == {'judged': 0, 'awarded': 0, 'pct': None}
        assert r['difference'] is None

    def test_period_filter(self, world):
        u = world['user']()
        t = world['thread']()
        b7, b30, ball = (status.language_monitor(p)['en']['judged'] for p in ('7', '30', 'alles'))
        world['post'](u, t, lang='en', created_at=datetime.utcnow() - timedelta(days=40))
        world['post'](u, t, lang='en', created_at=datetime.utcnow() - timedelta(days=10))
        world['post'](u, t, lang='en')
        assert status.language_monitor('7')['en']['judged'] - b7 == 1
        assert status.language_monitor('30')['en']['judged'] - b30 == 2
        assert status.language_monitor('alles')['en']['judged'] - ball == 3

    def test_page_access_and_sample_filter(self, app, world):
        u = world['user']()
        t = world['thread']()
        nl_post, en_post = world['post'](u, t, lang='nl'), world['post'](u, t, lang='en')
        mod = world['user']('moderator')
        client = _client(app, mod)
        page = _req(client, 'GET', '/admin/taalmonitor?periode=7').get_data(as_text=True)
        assert 'Taal-monitor' in page and 'data-monitor="nl-pct"' in page
        assert f'/forum/kwaliteit/{nl_post.id}' in page and f'/forum/kwaliteit/{en_post.id}' in page
        only_nl = _req(client, 'GET', '/admin/taalmonitor?taal=nl').get_data(as_text=True)
        assert 'data-sample-lang="en"' not in only_nl and f'/forum/kwaliteit/{nl_post.id}' in only_nl
        assert _req(_client(app, world['user']()), 'GET', '/admin/taalmonitor').status_code == 403
        assert _req(_client(app, mod, lang='en'), 'GET', '/admin/taalmonitor').status_code == 200


# ── Migratie ─────────────────────────────────────────────────────────────

class TestMigration:
    def test_column_added_and_one_time_backfill(self, app, db, world):
        import app as app_module
        u = world['user']()
        t = world['thread']()
        for _ in range(3):
            world['post'](u, t)
        db.session.commit()
        with db.engine.connect() as conn:  # oud schema: zonder quality_status
            conn.execute(text('ALTER TABLE users DROP COLUMN quality_status'))
            conn.commit()
        db.engine.dispose()  # SQLite: verse verbindingen na DROP COLUMN
        try:
            app_module._migrate_columns()
            assert app_module._QUALITY_STATUS_COLUMN_ADDED is True
            app_module._initial_status_backfill()
            assert _status(u.id) == 'voter'
            # Een volgende start voegt niets toe en zet de vlag niet opnieuw.
            app_module._QUALITY_STATUS_COLUMN_ADDED = False
            app_module._migrate_columns()
            assert app_module._QUALITY_STATUS_COLUMN_ADDED is False
        finally:
            app_module._QUALITY_STATUS_COLUMN_ADDED = False
