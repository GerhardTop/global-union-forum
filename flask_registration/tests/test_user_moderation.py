"""User moderation step 1 (Gebruiker.dc.html): the Warning and Suspension
models, the User fields, user_stats() and moderation_history(). No UI yet."""
from datetime import datetime, timedelta

import pytest

from app.models import (ConductCase, Post, PostReport, QualityAward, Suspension,
                        User, Warning)
from app.users import moderation_history, user_stats
from tests.test_delete_issue import _cleanup
from tests.test_moderator import _client, _issue_with_fact, _req, _user


@pytest.fixture(autouse=True)
def _clean_session(db):
    db.session.expunge_all()
    yield


def _post(db, issue, user, body):
    p = Post(thread_id=issue.thread_id, user_id=user.id, author_name=user.username,
             author_username=user.username, body=body)
    db.session.add(p)
    db.session.commit()
    return p


def _drop_moderation_rows(*users):
    from app import db
    ids = [u.id for u in users]
    Warning.query.filter(Warning.user_id.in_(ids)).delete(synchronize_session=False)
    Suspension.query.filter(Suspension.user_id.in_(ids)).delete(synchronize_session=False)
    db.session.commit()


def test_warning_and_suspension_link_to_user(app, db):
    user, mod = _user(db), _user(db, 'senior_moderator')
    try:
        until = datetime.utcnow() + timedelta(days=7)
        db.session.add(Warning(user_id=user.id, by_id=mod.id, message='Let op je toon.'))
        db.session.add(Suspension(user_id=user.id, by_id=mod.id, reason='Herhaald.', until=until))
        db.session.commit()
        db.session.expire_all()
        user = db.session.get(User, user.id)
        assert [w.message for w in user.warnings] == ['Let op je toon.']
        assert user.warnings[0].by.id == mod.id
        assert user.suspensions[0].until == until and user.suspensions[0].by.id == mod.id
    finally:
        _drop_moderation_rows(user)


def test_is_suspended_follows_suspended_until(app, db):
    user = _user(db)
    assert user.suspended_until is None and not user.is_suspended
    user.suspended_until = datetime.utcnow() + timedelta(hours=1)
    assert user.is_suspended
    user.suspended_until = datetime.utcnow() - timedelta(minutes=1)
    assert not user.is_suspended
    assert not user.is_deleted and not user.is_anonymised
    user.deleted_at = datetime.utcnow()
    assert user.is_deleted and user.is_anonymised
    db.session.rollback()


def test_senior_moderator_check(app, db):
    assert _user(db, 'senior_moderator').is_senior_moderator
    assert not _user(db, 'moderator').is_senior_moderator
    assert not _user(db).is_senior_moderator
    admin = _user(db)
    admin.is_admin = True
    assert admin.is_senior_moderator


def test_user_stats_and_history(app, db):
    author, other, mod = _user(db), _user(db), _user(db, 'senior_moderator')
    issue, fact = _issue_with_fact(db, author)  # one fact by the author
    issue_id = issue.id
    try:
        base = user_stats(author)  # the opening post of the discussion is the author's too
        assert base['feiten'] == 1 and base['waarschuwingen'] == 0
        good = _post(db, issue, author, 'Een goede bijdrage.')
        moved = _post(db, issue, author, 'Een bijdrage die te ver gaat.')
        theirs = _post(db, issue, other, 'Een bijdrage van een ander.')
        t0 = datetime.utcnow() - timedelta(days=3)
        db.session.add(QualityAward(post_id=good.id, criterion='K1', status='toegekend'))
        db.session.add(ConductCase(post_id=moved.id, action='verplaatst', rule='G2', source='moderator',
                                   explanation='Persoonlijke aanval.', on_background=True,
                                   acted_at=t0, acted_by_id=mod.id))
        # Two reports on the author's posts (one upheld), one made by the author.
        db.session.add(PostReport(post_id=moved.id, reporter_id=other.id, reason='aanval',
                                  decision='verplaatst'))
        db.session.add(PostReport(post_id=good.id, reporter_id=other.id, reason='onzin'))
        db.session.add(PostReport(post_id=theirs.id, reporter_id=author.id, reason='spam'))
        db.session.add(Warning(user_id=author.id, by_id=mod.id, message='Eerste waarschuwing.',
                               created_at=t0 + timedelta(days=1)))
        db.session.add(Suspension(user_id=author.id, by_id=mod.id, reason='Opnieuw.',
                                  until=datetime.utcnow() + timedelta(days=7),
                                  created_at=t0 + timedelta(days=2)))
        db.session.add(Warning(user_id=other.id, by_id=mod.id, message='Niet van de auteur.'))
        db.session.commit()

        stats = user_stats(author)
        assert stats == {'bijdragen': base['bijdragen'] + 2, 'feiten': 1, 'keurmerken': 1,
                         'meldingen_ontvangen': 2, 'gegrond': 1, 'meldingen_gedaan': 1,
                         'waarschuwingen': 1, 'schorsingen': 1}

        history = moderation_history(author)
        assert [h.kind for h in history] == ['schorsing', 'waarschuwing', 'verplaatst']
        susp, warn, case = history
        assert susp.description == 'Opnieuw.' and susp.until is not None and susp.actor.id == mod.id
        assert warn.description == 'Eerste waarschuwing.' and warn.issue is None
        assert case.description == 'Persoonlijke aanval.' and case.actor.id == mod.id
        assert case.issue is not None and case.issue.id == issue_id and case.at == t0
        assert not case.by_ai

        # A restored post drops out of the history.
        case.source.action = 'teruggeplaatst'
        db.session.commit()
        assert [h.kind for h in moderation_history(author)] == ['schorsing', 'waarschuwing']
    finally:
        _drop_moderation_rows(author, other)
        _cleanup(issue_id)


def test_deleting_a_user_removes_their_moderation_rows(app, db):
    from app import quality
    user, mod = _user(db), _user(db, 'senior_moderator')
    db.session.add(Warning(user_id=user.id, by_id=mod.id, message='x'))
    db.session.add(Warning(user_id=mod.id, by_id=user.id, message='y'))
    db.session.commit()
    try:
        quality.detach_user(user.id)
        db.session.commit()
        assert Warning.query.filter_by(user_id=user.id).count() == 0
        assert Warning.query.filter_by(user_id=mod.id).one().by_id is None
    finally:
        _drop_moderation_rows(user, mod)


# ── Step 2: the Gebruiker page and "Waarschuwing sturen" ────────────────

def _kpi(page, key):
    import re
    m = re.search(r'data-kpi="%s"><span class="gu-kpi__num[^"]*">(\d+)<' % key, page)
    return int(m.group(1))


def test_gebruiker_page_is_moderator_only(app, db):
    target, mod, plain = _user(db), _user(db, 'moderator'), _user(db)
    r = _req(_client(app, mod), 'GET', f'/moderator/gebruiker/{target.id}')
    assert r.status_code == 200
    page = r.get_data(as_text=True)
    assert f'@{target.username}' in page and 'Handhaving-historie' in page
    assert 'Waarschuwing sturen' in page and 'Bericht aan de gebruiker' in page
    assert 'Tijdelijk schorsen' not in page and 'Account verwijderen' not in page
    assert _req(_client(app, plain), 'GET', f'/moderator/gebruiker/{target.id}').status_code == 403
    assert _req(_client(app, plain), 'POST', f'/moderator/gebruiker/{target.id}/waarschuwing',
                data={'message': 'x'}).status_code == 403
    assert _req(_client(app, mod), 'GET', '/moderator/gebruiker/999999').status_code == 404


def test_gebruiker_page_shows_figures_history_and_contributions(app, db):
    author, other, mod = _user(db), _user(db), _user(db, 'moderator')
    issue, fact = _issue_with_fact(db, author)
    issue_id = issue.id
    try:
        good = _post(db, issue, author, 'Een goede bijdrage over het plein.')
        moved = _post(db, issue, author, 'Een bijdrage die te ver gaat.')
        theirs = _post(db, issue, other, 'Een bijdrage van een ander.')
        db.session.add(QualityAward(post_id=good.id, criterion='K1', status='toegekend'))
        db.session.add(ConductCase(post_id=moved.id, action='verplaatst', rule='respect', source='moderator',
                                   explanation='Persoonlijke aanval.', body_snapshot=moved.body,
                                   on_background=True, acted_at=datetime.utcnow(), acted_by_id=mod.id))
        db.session.add(PostReport(post_id=moved.id, reporter_id=other.id, reason='aanval', decision='verplaatst'))
        db.session.add(PostReport(post_id=theirs.id, reporter_id=author.id, reason='spam'))
        db.session.add(Warning(user_id=author.id, by_id=other.id, message='Blijf op de inhoud.'))
        db.session.commit()
        stats = user_stats(author)

        page = _req(_client(app, mod), 'GET', f'/moderator/gebruiker/{author.id}').get_data(as_text=True)
        assert _kpi(page, 'bijdragen') == stats['bijdragen']
        assert _kpi(page, 'feiten') == 1 and _kpi(page, 'keurmerken') == 1
        assert _kpi(page, 'ontvangen') == 1 and '(1 gegrond)' in page
        assert _kpi(page, 'gedaan') == 1
        assert '1 waarschuwing<' in page and '0 schorsingen<' in page

        hist = page[page.index('id="historie"'):page.index('id="bijdragen"')]
        assert 'Bijdrage verplaatst naar achtergrond' in hist and 'door jou' in hist
        assert 'Persoonlijke aanval.' in hist and issue.title in hist
        assert 'Waarschuwing gestuurd' in hist and 'Blijf op de inhoud.' in hist and f'@{other.username}' in hist

        contrib = page[page.index('id="bijdragen"'):page.index('id="waarschuwing"')]
        assert 'data-status="verplaatst"' in contrib and '#zaak-' in contrib
        assert 'data-status="keurmerk"' in contrib and 'data-status="feit"' in contrib
        assert 'Feit: ' in contrib and 'gu-pill--ctx' in contrib
    finally:
        _drop_moderation_rows(author, other)
        _cleanup(issue_id)


def test_sending_a_warning_adds_it_to_the_history(app, db):
    target, mod = _user(db), _user(db, 'moderator')
    try:
        c = _client(app, mod)
        url = f'/moderator/gebruiker/{target.id}/waarschuwing'
        r = _req(c, 'POST', url, data={'message': '   '})
        assert r.status_code == 302 and Warning.query.filter_by(user_id=target.id).count() == 0

        r = _req(c, 'POST', url, data={'message': 'Blijf op de inhoud, niet op de persoon.'})
        assert r.status_code == 302 and r.headers['Location'].endswith(f'/moderator/gebruiker/{target.id}')
        w = Warning.query.filter_by(user_id=target.id).one()
        assert w.by_id == mod.id and w.message == 'Blijf op de inhoud, niet op de persoon.'

        page = _req(c, 'GET', f'/moderator/gebruiker/{target.id}').get_data(as_text=True)
        hist = page[page.index('id="historie"'):page.index('id="bijdragen"')]
        assert 'Waarschuwing gestuurd' in hist and 'Blijf op de inhoud, niet op de persoon.' in hist
        assert 'door jou' in hist and '1 waarschuwing<' in page

        # A moderator cannot warn themselves.
        assert _req(c, 'POST', f'/moderator/gebruiker/{mod.id}/waarschuwing',
                    data={'message': 'x'}).status_code == 403
    finally:
        _drop_moderation_rows(target, mod)


# ── Step 3: Tijdelijk schorsen ──────────────────────────────────────────

def test_suspending_is_senior_only_and_not_yourself(app, db):
    target, mod, senior = _user(db), _user(db, 'moderator'), _user(db, 'senior_moderator')
    try:
        url = f'/moderator/gebruiker/{target.id}/schorsen'
        page = _req(_client(app, mod), 'GET', f'/moderator/gebruiker/{target.id}').get_data(as_text=True)
        assert 'Waarschuwing sturen' in page and 'Tijdelijk schorsen' not in page
        assert _req(_client(app, mod), 'POST', url, data={'duur': '7', 'reason': 'x'}).status_code == 403

        c = _client(app, senior)
        page = _req(c, 'GET', f'/moderator/gebruiker/{target.id}').get_data(as_text=True)
        assert 'Tijdelijk schorsen' in page and 'Reden van de schorsing' in page
        assert 'Schorsen voor 7 dagen' in page and 'value="30"' in page
        assert 'Komt in de historie.' in page and 'krijgt je bericht' not in page
        own = _req(c, 'GET', f'/moderator/gebruiker/{senior.id}').get_data(as_text=True)
        assert 'id="schorsen"' not in own
        assert _req(c, 'POST', f'/moderator/gebruiker/{senior.id}/schorsen',
                    data={'duur': '7', 'reason': 'x'}).status_code == 403
        # Only 7 or 30 days, and a reason is required.
        _req(c, 'POST', url, data={'duur': '14', 'reason': 'x'})
        _req(c, 'POST', url, data={'duur': '7', 'reason': '  '})
        assert Suspension.query.filter_by(user_id=target.id).count() == 0
        assert db.session.get(User, target.id).suspended_until is None
    finally:
        _drop_moderation_rows(target, senior)


def test_suspension_blocks_contributing_and_shows_in_dossier(app, db):
    target, senior = _user(db), _user(db, 'senior_moderator')
    issue, _fact = _issue_with_fact(db, target)
    issue_id, thread_id = issue.id, issue.thread_id
    try:
        c = _client(app, senior)
        r = _req(c, 'POST', f'/moderator/gebruiker/{target.id}/schorsen',
                 data={'duur': '30', 'reason': 'Herhaalde beledigingen.'})
        assert r.status_code == 302
        susp = Suspension.query.filter_by(user_id=target.id).one()
        assert susp.by_id == senior.id and susp.reason == 'Herhaalde beledigingen.'
        db.session.expire_all()
        user = db.session.get(User, target.id)
        assert user.suspended_until == susp.until and user.is_suspended
        assert timedelta(days=29, hours=23) < susp.until - datetime.utcnow() <= timedelta(days=30)
        assert not user.can_contribute() and not user.can_vote()

        page = _req(c, 'GET', f'/moderator/gebruiker/{target.id}').get_data(as_text=True)
        hist = page[page.index('id="historie"'):page.index('id="bijdragen"')]
        assert 'Geschorst' in hist and 'Herhaalde beledigingen.' in hist and 'door jou' in hist
        assert '1 schorsing<' in page and 'Geschorst tot' in page

        # The suspended user cannot reply; the notice explains why.
        tc = _client(app, target)
        before = Post.query.filter_by(thread_id=thread_id).count()
        r = _req(tc, 'POST', f'/forum/{thread_id}', data={'body': 'Toch nog even reageren.'})
        assert r.status_code == 302
        assert Post.query.filter_by(thread_id=thread_id).count() == before
        landing = _req(tc, 'GET', r.headers['Location']).get_data(as_text=True)
        assert 'Je bent geschorst tot' in landing
        # The forms are gone; the page says why instead of pointing to the rules.
        show = _req(tc, 'GET', f'/vraagstuk/{issue_id}').get_data(as_text=True)
        assert 'Je bent geschorst en kunt nu niet bijdragen' in show
        assert 'Accepteer de gedragsregels' not in show
        # Other contribution routes refuse too.
        r = _req(tc, 'POST', '/gesprekken/nieuw', data={'title': 'Nieuw'})
        assert r.status_code == 302 and '/spelregels' not in r.headers['Location']

        # Lifting is automatic once suspended_until has passed.
        user.suspended_until = datetime.utcnow() - timedelta(minutes=1)
        db.session.commit()
        assert not user.is_suspended and user.can_contribute()
    finally:
        _drop_moderation_rows(target, senior)
        _cleanup(issue_id)


# ── Step 4: Account verwijderen (anonymise) ─────────────────────────────

def test_account_delete_is_senior_or_admin_only(app, db):
    target, mod, senior = _user(db), _user(db, 'moderator'), _user(db, 'senior_moderator')
    admin = _user(db)
    admin.is_admin = True
    db.session.commit()
    url = f'/moderator/gebruiker/{target.id}/verwijderen'
    page = _req(_client(app, mod), 'GET', f'/moderator/gebruiker/{target.id}').get_data(as_text=True)
    assert 'id="verwijderen"' not in page
    assert _req(_client(app, mod), 'GET', url).status_code == 403
    assert _req(_client(app, mod), 'POST', url, data={'confirm_username': target.username}).status_code == 403

    c = _client(app, senior)
    page = _req(c, 'GET', f'/moderator/gebruiker/{target.id}').get_data(as_text=True)
    card = page[page.index('id="verwijderen"'):]
    assert 'Account verwijderen' in card and 'Reden (komt in de historie)' in card
    assert 'Opent een bevestigingsscherm' in card
    own = _req(c, 'GET', f'/moderator/gebruiker/{senior.id}').get_data(as_text=True)
    assert 'id="verwijderen"' not in own
    # Not yourself, not an admin.
    assert _req(c, 'GET', f'/moderator/gebruiker/{senior.id}/verwijderen').status_code == 403
    assert _req(c, 'POST', f'/moderator/gebruiker/{admin.id}/verwijderen',
                data={'confirm_username': admin.username}).status_code == 403
    # The admin may open the screen too.
    r = _req(_client(app, admin), 'GET', url + '?reden=Structureel+onveilig')
    assert r.status_code == 200
    screen = r.get_data(as_text=True)
    assert 'Account definitief verwijderen' in screen and '>Admin<' in screen
    assert f'@{target.username}' in screen and 'Wat er gebeurt' in screen
    assert 'Structureel onveilig</textarea>' in screen and 'data-confirm-button' in screen
    assert db.session.get(User, target.id).deleted_at is None


def test_account_delete_anonymises_and_keeps_contributions(app, db):
    target, other, senior = _user(db), _user(db), _user(db, 'senior_moderator')
    target.first_name, target.last_name, target.show_real_name = 'Jan', 'Hofman', True
    db.session.commit()
    issue, fact = _issue_with_fact(db, target)
    issue_id, thread_id, user_id, old_name = issue.id, issue.thread_id, target.id, target.username
    try:
        post = _post(db, issue, target, 'Een bijdrage die moet blijven staan.')
        post_id = post.id
        db.session.add(Warning(user_id=target.id, by_id=senior.id, message='Eerder gewaarschuwd.'))
        db.session.add(Warning(user_id=other.id, by_id=target.id, message='Door het account gegeven.'))
        db.session.commit()
        c = _client(app, senior)
        url = f'/moderator/gebruiker/{user_id}/verwijderen'

        screen = _req(c, 'GET', url).get_data(as_text=True)
        assert 'data-effect="posts"' in screen and 'data-effect="facts"' in screen
        assert 'data-effect="warnings"' in screen and 'data-effect="given"' in screen
        assert 'data-effect="suspensions"' not in screen

        # A wrong name deletes nothing.
        r = _req(c, 'POST', url, data={'confirm_username': old_name + 'x', 'reden': 'Onveilig.'})
        assert r.status_code == 302
        assert db.session.get(User, user_id).deleted_at is None

        r = _req(c, 'POST', url, data={'confirm_username': '@' + old_name, 'reden': 'Onveilig.'})
        assert r.status_code == 302 and r.headers['Location'].endswith('/moderator')
        db.session.expire_all()
        user = db.session.get(User, user_id)
        assert user.deleted_at is not None and user.deleted_by_id == senior.id
        assert user.deleted_reason == 'Onveilig.' and user.is_deleted
        assert old_name not in (user.username, user.email) and user.username.startswith('verwijderd_')
        assert user.first_name is None and user.last_name is None and not user.verified
        assert user.public_name == '@verwijderd' and user.handle == '@verwijderd'
        # Own warnings went, the one given as a moderator stays without a name.
        assert Warning.query.filter_by(user_id=user_id).count() == 0
        assert Warning.query.filter_by(user_id=other.id).one().by_id is None

        # Contributions stay and show as @verwijderd.
        assert db.session.get(Post, post_id) is not None and db.session.get(Post, fact.id) is not None
        page = _req(_client(app, other), 'GET', f'/forum/{thread_id}').get_data(as_text=True)
        assert 'Een bijdrage die moet blijven staan.' in page and '@verwijderd' in page
        assert old_name not in page and 'Hofman' not in page

        # Login is impossible and an old session is gone.
        from app.models import load_user
        assert load_user(str(user_id)) is None
        lc = app.test_client()
        _req(lc, 'POST', '/login', data={'email': f'{old_name}@example.com', 'password': 'Sterk1!ww'})
        with lc.session_transaction() as s:
            assert '_user_id' not in s

        dossier = _req(c, 'GET', f'/moderator/gebruiker/{user_id}').get_data(as_text=True)
        assert '<h1 class="gu-user__name">@verwijderd</h1>' in dossier and 'Verwijderd op' in dossier
        assert 'id="waarschuwing"' not in dossier
        assert _req(c, 'GET', url).status_code == 404
    finally:
        _drop_moderation_rows(target, other)
        _cleanup(issue_id)
