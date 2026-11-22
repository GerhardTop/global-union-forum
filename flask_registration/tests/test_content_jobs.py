"""Multilingual phase 2a: issues, proposals (with their versions) and the
follow-up are translated into every enabled language right after saving, in
the background; the hourly tick is the safety net (app/content_jobs.py).

The AI client is a fake (tests/test_content_i18n.FakeClient); the background
thread is replaced by a list of jobs that the test runs itself, so "after the
commit, not during the save" is visible.
"""
import pytest

import config
from app import content_i18n, content_jobs, db, issue_lifecycle, issue_proposals as ip, quality
from app.models import ContentTranslation
from tests.test_content_i18n import FakeClient
from tests.test_delete_issue import _clean_session, _cleanup  # noqa: F401  (autouse fixture)
from tests.test_issue_polish import _client, _issue, _req, make_user  # noqa: F401  (fixture)

OTHER = [lang for lang in config.ENABLED_LANGUAGES if lang != 'nl']
ENGLISH_NAME = {'en': 'English', 'de': 'German', 'es': 'Spanish', 'fr': 'French'}


@pytest.fixture
def jobs(monkeypatch):
    """The background start, captured: a list of key lists, one per commit."""
    started = []
    monkeypatch.setattr(content_jobs, '_start', lambda app, keys: started.append(keys))
    return started


@pytest.fixture
def no_sync_ai(monkeypatch):
    """Fails the test if anything calls the AI during the request."""
    calls = []

    def refuse(*args, **kw):
        calls.append(args)
        raise AssertionError('the AI was called while saving')
    monkeypatch.setattr(quality, 'translate_text', refuse)
    return calls


def _translations(kind, oid):
    return {(row.field, row.lang): row for row in
            ContentTranslation.query.filter_by(object_type=kind, object_id=oid)}


def _run(app, jobs, client):
    for keys in jobs:
        content_jobs.run(app, keys, client=client)
    jobs.clear()
    db.session.expire_all()
    content_i18n.clear_cache()  # the test shares one app context (and its request cache) with the pages


def test_a_new_issue_is_translated_into_every_other_language(app, db, make_user, jobs):
    user = make_user()
    issue = _issue(db, user, 'Een vraagstuk over water')  # nl, with an intro
    try:
        assert jobs == [[('issues', issue.id)]]   # scheduled at the commit, not run yet
        assert _translations('issues', issue.id) == {}
        client = FakeClient()
        _run(app, jobs, client)
        rows = _translations('issues', issue.id)
        assert set(rows) == {(field, lang) for field in ('title', 'intro') for lang in OTHER}
        assert rows[('title', 'de')].text == '[German] Een vraagstuk over water'
        assert all(row.is_machine for row in rows.values())
        assert len(client.calls) == 2 * len(OTHER)
    finally:
        _cleanup(issue.id)


def test_saving_does_not_call_the_ai(app, db, make_user, jobs, no_sync_ai):
    user = make_user()
    issue = _issue(db, user)
    try:
        jobs.clear()
        resp = _req(_client(app, user, 'en'), 'POST', f'/vraagstuk/{issue.id}/voorstellen/nieuw',
                    data={'statement': 'A proposal in English.', 'explanation': 'Why.'})
        assert resp.status_code in (302, 303)
        assert no_sync_ai == []
        (keys,) = jobs
        assert {kind for kind, _ in keys} == {'issue_proposals', 'issue_proposal_versions'}
    finally:
        _cleanup(issue.id)


def test_a_new_version_gets_new_translations_and_the_old_one_keeps_its_own(app, db, make_user, jobs):
    user = make_user()
    issue = _issue(db, user)
    jobs.clear()  # only the proposal's jobs below
    try:
        proposal = ip.create(issue, user, 'Eerste tekst.', 'Uitleg een.', lang='nl')
        db.session.commit()
        client = FakeClient()
        _run(app, jobs, client)
        first = proposal.version_rows[0]
        assert _translations('issue_proposals', proposal.id)[('statement', 'en')].text == '[English] Eerste tekst.'
        # A proposal and its version 1 have the same text: one AI call per language and field.
        assert len(client.calls) == 2 * len(OTHER)
        assert _translations('issue_proposal_versions', first.id)[('statement', 'en')].text == '[English] Eerste tekst.'

        ip.revise(proposal, 'Tweede tekst.', 'Uitleg twee.')
        db.session.commit()
        assert _translations('issue_proposals', proposal.id) == {}   # invalidated with the edit
        _run(app, jobs, client)
        second = proposal.version_rows[1]
        assert _translations('issue_proposals', proposal.id)[('statement', 'fr')].text == '[French] Tweede tekst.'
        assert _translations('issue_proposal_versions', second.id)[('explanation', 'es')].text == '[Spanish] Uitleg twee.'
        old = _translations('issue_proposal_versions', first.id)
        assert old[('statement', 'de')].text == '[German] Eerste tekst.'   # the old version keeps its own
        assert len(old) == 2 * len(OTHER)
    finally:
        _cleanup(issue.id)


def test_follow_up_updates_are_translated_from_the_writers_language(app, db, make_user, jobs):
    user = make_user()
    issue = _issue(db, user)
    try:
        update = issue_lifecycle.add_followup(issue, 'We sent the proposal to parliament.', user, lang='en')
        db.session.commit()
        assert content_i18n.source_lang(update) == 'en'
        _run(app, jobs, FakeClient())
        rows = _translations('issue_followups', update.id)
        assert rows[('body', 'nl')].text == '[Dutch] We sent the proposal to parliament.'
        assert ('body', 'en') not in rows

        issue_lifecycle.edit_followup(update, 'We sent it to the government.')
        db.session.commit()
        _run(app, jobs, FakeClient())
        assert _translations('issue_followups', update.id)[('body', 'nl')].text == '[Dutch] We sent it to the government.'
    finally:
        _cleanup(issue.id)


def test_the_tick_fills_in_what_is_missing_and_repeats_nothing(app, db, make_user, jobs):
    user = make_user()
    issue = _issue(db, user, 'Een vraagstuk zonder vertaling')
    try:
        jobs.clear()  # the background job never ran (no AI at the time, a restart)
        content_i18n.set_translation(issue, 'title', 'en', 'An issue without a translation', is_machine=True)
        db.session.commit()
        client = FakeClient()
        done = content_jobs.backfill(limit=1000, client=client)
        rows = _translations('issues', issue.id)
        assert set(rows) >= {(field, lang) for field in ('title', 'intro') for lang in OTHER}
        assert rows[('title', 'en')].text == 'An issue without a translation'   # present: not redone
        assert done >= 2 * len(OTHER) - 1
        again = FakeClient()
        assert content_jobs.backfill(limit=1000, client=again) == 0
        assert again.calls == []
    finally:
        _cleanup(issue.id)


def test_the_tick_respects_its_limit_and_skips_without_ai(app, db, make_user, jobs):
    user = make_user()
    issue = _issue(db, user, 'Nog een vraagstuk')
    try:
        assert content_jobs.backfill() == 0          # no AI key in the tests: nothing happens
        client = FakeClient()
        assert content_jobs.backfill(limit=3, client=client) == 3
        assert len(client.calls) == 3
    finally:
        _cleanup(issue.id)


def test_an_outdated_translation_is_redone_by_the_tick(app, db, make_user, jobs):
    user = make_user()
    issue = _issue(db, user, 'Oude titel')
    try:
        _run(app, jobs, FakeClient())
        issue.title = 'Nieuwe titel'          # edited without invalidate: the hash no longer matches
        db.session.commit()
        assert content_i18n.text_for(issue, 'title', 'en') is None
        content_jobs.backfill(limit=1000, client=FakeClient())
        assert content_i18n.text_for(issue, 'title', 'en') == '[English] Nieuwe titel'
    finally:
        _cleanup(issue.id)


def test_a_missing_translation_shows_the_original_as_pending(app, db, make_user, jobs, monkeypatch):
    user = make_user()
    issue = _issue(db, user, 'Een vraagstuk dat nog vertaald wordt')
    try:
        assert content_i18n.view(issue, 'title', 'en').kind == 'pending'
        with monkeypatch.context() as m:   # opening the page never calls the AI
            m.setattr(quality, 'translate_text', lambda *a, **kw: pytest.fail('the AI was called on a page load'))
            page = _req(_client(app, user, 'en'), 'GET', f'/vraagstuk/{issue.id}').get_data(as_text=True)
        assert 'Een vraagstuk dat nog vertaald wordt' in page and 'Translation pending' in page
        _run(app, jobs, FakeClient())
        page = _req(_client(app, user, 'en'), 'GET', f'/vraagstuk/{issue.id}').get_data(as_text=True)
        assert '[English] Een vraagstuk dat nog vertaald wordt' in page
        nl_page = _req(_client(app, user, 'nl'), 'GET', f'/vraagstuk/{issue.id}').get_data(as_text=True)
        assert 'Vertaling volgt' not in nl_page   # the reader's own language: no label
    finally:
        _cleanup(issue.id)


def test_deleting_an_issue_takes_the_translations_along(app, db, make_user, jobs):
    user = make_user()
    issue = _issue(db, user)
    proposal = ip.create(issue, user, 'Een voorstel.', None, lang='nl')
    db.session.commit()
    _run(app, jobs, FakeClient())
    ids = {'issues': [issue.id], 'issue_proposals': [proposal.id],
           'issue_proposal_versions': [v.id for v in proposal.version_rows]}
    _cleanup(issue.id)
    for kind, oids in ids.items():
        assert ContentTranslation.query.filter(ContentTranslation.object_type == kind,
                                               ContentTranslation.object_id.in_(oids)).count() == 0
