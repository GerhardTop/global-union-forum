"""The follow-up phase as a timeline (docs/design/vervolg.html): dated
updates, newest first; the old ones stay. The initiator and moderators add;
only the newest update can be edited or deleted."""
import re
from datetime import datetime, timedelta

import pytest

from app import issue_lifecycle as lifecycle
from app.models import Issue, IssueFollowup
from tests.test_delete_issue import _clean_session, _cleanup, _forget_deleted  # noqa: F401  (autouse fixture)
from tests.test_issue_polish import _client, _get, _issue, _req, make_user  # noqa: F401  (fixture)


@pytest.fixture
def world(db, make_user):
    host, mod, other = make_user(), make_user('moderator'), make_user()
    issue = _issue(db, host, 'Vervolg als tijdlijn')
    lifecycle.set_phase(issue, 'vervolg', datetime.utcnow() - timedelta(days=1))
    db.session.commit()
    yield {'issue': issue, 'host': host, 'mod': mod, 'other': other}
    _cleanup(issue.id)


def _url(issue, update=None, action=None):
    base = f'/vraagstuk/{issue.id}/vervolg'
    return base if update is None else f'{base}/{update.id}/{action}'


def _post(app, user, url, text=None):
    return _req(_client(app, user), 'POST', url, data={} if text is None else {'followup_text': text})


def _rows(issue):
    return IssueFollowup.query.filter_by(issue_id=issue.id).order_by(IssueFollowup.created_at.desc(),
                                                                      IssueFollowup.id.desc()).all()


def test_host_adds_updates_newest_first_and_old_ones_stay(app, db, world):
    w = world
    issue = w['issue']
    assert _post(app, w['host'], _url(issue), 'Eerste update.').status_code == 302
    first = _rows(issue)[0]
    first.created_at = datetime.utcnow() - timedelta(hours=2)  # clearly older
    db.session.commit()
    assert _post(app, w['mod'], _url(issue), 'Tweede update, van de moderator.').status_code == 302
    rows = _rows(issue)
    assert [r.body for r in rows] == ['Tweede update, van de moderator.', 'Eerste update.']
    assert rows[0].author_id == w['mod'].id and rows[1].author_id == w['host'].id
    db.session.expire_all()
    assert [u.body for u in db.session.get(Issue, issue.id).followups] == [r.body for r in rows]
    for lang, texts in (('nl', ('Wat er nu gebeurt', 'Nieuwste', 'Update toevoegen', 'Plaatsen',
                                'De vorige updates blijven staan, elk met de datum van toen.')),
                        ('en', ('What happens next', 'Latest', 'Add an update', '>Post<',
                                'The previous updates stay, each with the date from then.'))):
        page = _get(app, f'/vraagstuk/{issue.id}', w['host'], lang)
        section = page.split('id="vervolg"')[1].split('</section>')[0]
        for text in texts:
            assert text in section, (lang, text)
        items = section.split('<li class="gu-followup__item')[1:]
        assert len(items) == 2
        assert 'Tweede update' in items[0] and 'gu-followup__item--old' not in items[0][:30]
        assert items[1].startswith(' gu-followup__item--old') and 'Eerste update.' in items[1]
        # Only the newest has edit and delete.
        assert '/bewerken' in items[0] and '/verwijderen' in items[0]
        assert '/bewerken' not in items[1] and '/verwijderen' not in items[1]
    # The discussion's own "Post" button keeps its Dutch label.
    assert 'Posten' in _get(app, f'/vraagstuk/{issue.id}', w['host'])
    # Empty text adds nothing.
    assert _post(app, w['host'], _url(issue), '   ').status_code == 303 and len(_rows(issue)) == 2
    # A reader sees the timeline, without the add box or the actions.
    page = _get(app, f'/vraagstuk/{issue.id}', w['other'])
    section = page.split('id="vervolg"')[1].split('</section>')[0]
    assert 'Eerste update.' in section and 'gu-followup__add' not in section and '/bewerken' not in section


def test_non_host_gets_403(app, db, world):
    w = world
    issue = w['issue']
    _post(app, w['host'], _url(issue), 'Van de host.')
    update = _rows(issue)[0]
    assert _post(app, w['other'], _url(issue), 'Niet van mij.').status_code == 403
    assert _post(app, w['other'], _url(issue, update, 'bewerken'), 'Overschreven.').status_code == 403
    assert _post(app, w['other'], _url(issue, update, 'verwijderen')).status_code == 403
    db.session.refresh(update)
    assert update.body == 'Van de host.' and len(_rows(issue)) == 1
    # Not before the follow-up phase either.
    lifecycle.set_phase(issue, 'stemmen', datetime.utcnow())
    db.session.commit()
    assert _post(app, w['host'], _url(issue), 'Te vroeg.').status_code == 403
    assert _post(app, w['host'], _url(issue, update, 'bewerken'), 'Te vroeg.').status_code == 403


def test_only_the_newest_update_can_be_edited_or_deleted(app, db, world):
    w = world
    issue = w['issue']
    _post(app, w['host'], _url(issue), 'Oud.')
    old = _rows(issue)[0]
    old.created_at = datetime.utcnow() - timedelta(days=3)
    db.session.commit()
    _post(app, w['host'], _url(issue), 'Nieuw.')
    assert _post(app, w['host'], _url(issue, old, 'bewerken'), 'Anders.').status_code == 403
    assert _post(app, w['mod'], _url(issue, old, 'verwijderen')).status_code == 403
    db.session.refresh(old)
    assert old.body == 'Oud.' and len(_rows(issue)) == 2
    # An update of another issue cannot be reached through this one.
    other_issue = _issue(db, w['host'], 'Ander vraagstuk')
    try:
        lifecycle.set_phase(other_issue, 'vervolg', datetime.utcnow())
        db.session.commit()
        newest = _rows(issue)[0]
        assert _post(app, w['host'], f'/vraagstuk/{other_issue.id}/vervolg/{newest.id}/bewerken', 'X').status_code == 403
    finally:
        _cleanup(other_issue.id)


def test_edit_sets_edited_at_and_delete_removes_the_row(app, db, world):
    w = world
    issue = w['issue']
    _post(app, w['host'], _url(issue), 'Eerst zo.')
    update = _rows(issue)[0]
    assert update.edited_at is None
    assert _post(app, w['mod'], _url(issue, update, 'bewerken'), 'Nu zo.').status_code == 302
    db.session.refresh(update)
    assert update.body == 'Nu zo.' and update.edited_at is not None
    page = _get(app, f'/vraagstuk/{issue.id}', w['host'])
    assert re.search(r'gu-followup__edited">· bewerkt ', page)
    update_id = update.id
    assert _post(app, w['host'], _url(issue, update, 'verwijderen')).status_code == 302
    db.session.expire_all()
    assert db.session.get(IssueFollowup, update_id) is None


def test_changes_since_announces_a_new_update(app, db, world):
    w = world
    issue = w['issue']
    since = datetime.utcnow() - timedelta(minutes=5)
    assert {'type': 'issue_vervolgtekst'} not in lifecycle.changes(issue, since)
    lifecycle.add_followup(issue, 'Er is iets gebeurd.', w['host'])
    db.session.commit()
    assert {'type': 'issue_vervolgtekst'} in lifecycle.changes(issue, since)
    # Not when the newest update is from before "since".
    assert {'type': 'issue_vervolgtekst'} not in lifecycle.changes(issue, datetime.utcnow() + timedelta(minutes=1))


def test_migrate_followup_log_moves_the_old_text_once(app, db, world):
    w = world
    issue = w['issue']
    when = datetime.utcnow() - timedelta(days=4)
    issue.followup_text, issue.followup_at = 'De oude vervolgtekst.', when
    db.session.commit()
    assert lifecycle.migrate_followup_log() >= 1
    db.session.expire_all()
    rows = _rows(issue)
    assert [(r.body, r.author_id, r.created_at) for r in rows] == [('De oude vervolgtekst.', None, when)]
    fresh = db.session.get(Issue, issue.id)
    assert fresh.followup_text is None and fresh.followup_at is None
    # Idempotent: a second run adds nothing.
    assert lifecycle.migrate_followup_log() == 0
    assert len(_rows(issue)) == 1
    # An issue that already has a timeline keeps it; the old text is only cleared.
    fresh.followup_text = 'Nog een oude tekst.'
    db.session.commit()
    lifecycle.migrate_followup_log()
    db.session.expire_all()
    assert [r.body for r in _rows(issue)] == ['De oude vervolgtekst.']
    assert db.session.get(Issue, issue.id).followup_text is None


def test_purge_and_user_deletion_handle_the_timeline(app, db, make_user):
    from app import issues, quality
    host = make_user()
    issue = _issue(db, host, 'Weg ermee')
    lifecycle.set_phase(issue, 'vervolg', datetime.utcnow())
    lifecycle.add_followup(issue, 'Een update.', host)
    db.session.commit()
    issue_id = issue.id
    quality.detach_user(host.id)
    db.session.commit()
    assert IssueFollowup.query.filter_by(issue_id=issue_id).one().author_id is None
    issues.purge(issue)
    _forget_deleted()  # purge deletes in bulk: drop the stale objects from the session
    assert IssueFollowup.query.filter_by(issue_id=issue_id).count() == 0
