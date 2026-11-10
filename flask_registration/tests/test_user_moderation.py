"""User moderation step 1 (Gebruiker.dc.html): the Warning and Suspension
models, the User fields, user_stats() and moderation_history(). No UI yet."""
from datetime import datetime, timedelta

import pytest

from app.models import (ConductCase, Post, PostReport, QualityAward, Suspension,
                        User, Warning)
from app.users import moderation_history, user_stats
from tests.test_delete_issue import _cleanup
from tests.test_moderator import _issue_with_fact, _user


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
