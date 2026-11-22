"""The translation backlog (app/content_jobs.py, /admin/vertalingen): per type
of content and language the fields still missing or outdated, the button that
translates the backlog now in a background run (one at a time), and the
counting and logging of failures.

The AI is a fake (tests/test_content_i18n.FakeClient); the background thread
is captured so the test runs it itself.
"""
import logging
import re

import pytest

import config
from app import content_i18n, content_jobs, db as _db, issue_lifecycle, issue_proposals as ip
from app.models import ContentTranslation
from tests.test_content_i18n import FakeClient
from tests.test_delete_issue import _clean_session, _cleanup  # noqa: F401  (autouse fixture)
from tests.test_issue_polish import _client, _fact, _issue, _req, make_user  # noqa: F401  (fixture)

OTHER = [lang for lang in config.ENABLED_LANGUAGES if lang != 'nl']


class FailingClient(FakeClient):
    """Fails every call into one language (a refusal, a wrong model)."""

    def __init__(self, failing_lang='German'):
        super().__init__()
        self.failing_lang = failing_lang

    def create(self, **kw):
        if f' to {self.failing_lang}.' in kw['messages'][0]['content']:
            raise RuntimeError('model not found: claude-nonexistent')
        return super().create(**kw)


class BrokenClient(FakeClient):
    """Fails every call (a wrong key)."""

    def create(self, **kw):
        self.calls.append(kw)
        raise RuntimeError('invalid x-api-key')


@pytest.fixture
def jobs(monkeypatch):
    """The background start after a save, captured (not run)."""
    started = []
    monkeypatch.setattr(content_jobs, '_start', lambda app, keys: started.append(keys))
    return started


@pytest.fixture
def backlog_env(app, monkeypatch):
    """An AI key, no pause between batches, a fresh run state and failure
    counter, the backlog thread captured; the translations made here are
    removed afterwards (the test database is shared)."""
    monkeypatch.setattr(content_jobs, 'ai_available', lambda app=None: True)
    monkeypatch.setattr(content_jobs, 'BACKLOG_PAUSE', 0)
    monkeypatch.setattr(content_jobs, 'BACKLOG_BATCH', 5)
    monkeypatch.setitem(content_jobs._STATS, 'failed', 0)
    monkeypatch.setitem(content_jobs._STATS, 'last_error', None)
    for key, value in {'running': False, 'done': 0, 'failed': 0, 'limit': 0,
                       'started': None, 'finished': None}.items():
        monkeypatch.setitem(content_jobs._RUN, key, value)
    spawned = []
    monkeypatch.setattr(content_jobs, '_spawn', lambda target, args: spawned.append((target, args)))
    last = _db.session.query(_db.func.max(ContentTranslation.id)).scalar() or 0
    yield spawned
    _db.session.rollback()
    ContentTranslation.query.filter(ContentTranslation.id > last).delete(synchronize_session=False)
    _db.session.commit()
    content_i18n.clear_cache()


def _fresh():
    _db.session.expire_all()
    content_i18n.clear_cache()


def _delta(after, before):
    return {kind: {lang: after[kind][lang] - before[kind][lang] for lang in after[kind]} for kind in after}


def _cell(page, key):
    m = re.search(rf'data-backlog="{re.escape(key)}">(?:<strong>)?(\d+)', page)
    assert m, key
    return int(m.group(1))


def _content(db, user):
    """One of each: an issue, a proposal (with its version 1), a follow-up
    update and a fact, all in Dutch, none translated yet."""
    issue = _issue(db, user, 'Een vraagstuk over de achterstand')
    proposal = ip.create(issue, user, 'Een stelling.', None, lang='nl')
    issue_lifecycle.add_followup(issue, 'Een update.', user, lang='nl')
    db.session.commit()
    _fact(db, issue, user, body='Een feit zonder vertaling.')
    return issue, proposal


def test_the_counts_per_type_and_language(app, db, make_user, jobs, backlog_env):
    before = content_jobs.backlog()
    user = make_user()
    issue, _proposal = _content(db, user)
    try:
        _fresh()
        delta = _delta(content_jobs.backlog(), before)
        thread = issue.thread
        issue_fields = 2 + int(thread.title_prefix == issue.title and not thread.title_accent)
        for lang in OTHER:
            assert delta['issues'][lang] == issue_fields     # title, intro (and the conversation title)
            assert delta['issue_proposals'][lang] == 1       # the statement (no explanation)
            assert delta['issue_proposal_versions'][lang] == 1
            assert delta['issue_followups'][lang] == 1
            assert delta['posts'][lang] == 1                  # the fact's text (no title)
        assert all(delta[kind]['nl'] == 0 for kind in delta)  # the source language needs nothing

        # Translated: nothing missing; the original edited: outdated again.
        content_jobs.translate_missing(issue, client=FakeClient())
        _fresh()
        assert all(_delta(content_jobs.backlog(), before)['issues'][lang] == 0 for lang in OTHER)
        issue = db.session.get(type(issue), issue.id)
        issue.title = 'Een nieuwe titel'                      # no invalidate: the hash no longer matches
        db.session.commit()
        _fresh()
        assert all(_delta(content_jobs.backlog(), before)['issues'][lang] == 1 for lang in OTHER)
    finally:
        _cleanup(issue.id)


def test_the_page_is_for_the_admin_and_shows_the_counts(app, db, make_user, jobs, backlog_env):
    admin, moderator = make_user(admin=True), make_user(role='moderator')
    assert _req(_client(app, moderator), 'GET', '/admin/vertalingen').status_code == 403
    assert _req(_client(app, moderator), 'POST', '/admin/vertalingen/achterstand').status_code == 403
    issue, _proposal = _content(db, admin)
    try:
        _fresh()
        counts = content_jobs.backlog()
        page = _req(_client(app, admin), 'GET', '/admin/vertalingen').get_data(as_text=True)
        assert 'Vertalingen' in page and 'Achterstand nu vertalen' in page
        for kind in content_jobs.BACKLOG_KINDS:
            for lang in config.ENABLED_LANGUAGES:
                assert _cell(page, f'{kind}-{lang}') == counts[kind][lang]
        assert _cell(page, 'total') == sum(sum(c.values()) for c in counts.values()) > 0
        assert 'Mislukte pogingen sinds de laatste start: 0' in page
        assert '/admin/vertalingen' in _req(_client(app, admin), 'GET', '/admin').get_data(as_text=True)
    finally:
        _cleanup(issue.id)


def test_the_button_translates_the_whole_backlog(app, db, make_user, jobs, backlog_env):
    admin = make_user(admin=True)
    issue, _proposal = _content(db, admin)
    try:
        resp = _req(_client(app, admin), 'POST', '/admin/vertalingen/achterstand')
        assert resp.status_code in (302, 303)
        (target, args), = backlog_env              # started in the background, not in the request
        assert content_jobs.backlog_running()
        page = _req(_client(app, admin), 'GET', '/admin/vertalingen').get_data(as_text=True)
        assert 'Bezig: 0 van maximaal 2000 velden gedaan' in page
        assert 'Achterstand nu vertalen <span' not in page   # no button during a run

        client = FakeClient()
        target(*args, client=client)
        assert not content_jobs.backlog_running()
        _fresh()
        assert all(n == 0 for counts in content_jobs.backlog().values() for n in counts.values())
        run = content_jobs.stats()['run']
        assert len(client.calls) <= run['done']             # reused texts cost no call
        assert run['done'] > content_jobs.BACKLOG_BATCH      # more than one batch
        assert run['failed'] == 0
        assert content_i18n.text_for(issue, 'title', 'de') == '[German] Een vraagstuk over de achterstand'
        page = _req(_client(app, admin), 'GET', '/admin/vertalingen').get_data(as_text=True)
        assert f'Laatste run: {run["done"]} velden gedaan, 0 mislukt.' in page
    finally:
        _cleanup(issue.id)


def test_a_second_click_during_a_run_starts_nothing(app, db, make_user, jobs, backlog_env):
    admin = make_user(admin=True)
    c = _client(app, admin)
    _req(c, 'POST', '/admin/vertalingen/achterstand')
    resp = _req(c, 'POST', '/admin/vertalingen/achterstand', follow_redirects=True)
    assert len(backlog_env) == 1
    assert 'Er loopt al een run.' in resp.get_data(as_text=True)
    assert content_jobs.start_backlog_run(app) is False
    assert len(backlog_env) == 1


def test_without_an_ai_key_nothing_starts(app, db, make_user, jobs, backlog_env, monkeypatch):
    monkeypatch.setattr(content_jobs, 'ai_available', lambda app=None: False)
    admin = make_user(admin=True)
    resp = _req(_client(app, admin), 'POST', '/admin/vertalingen/achterstand', follow_redirects=True)
    assert backlog_env == []
    assert 'Er is hier geen AI-sleutel' in resp.get_data(as_text=True)


def test_a_failure_is_counted_and_logged_in_full(app, db, make_user, jobs, backlog_env, caplog):
    admin = make_user(admin=True)
    issue, _proposal = _content(db, admin)
    try:
        caplog.set_level(logging.INFO, logger='app.content_jobs')
        content_jobs.start_backlog_run(app)
        (target, args), = backlog_env
        target(*args, client=FailingClient('German'))
        run = content_jobs.stats()['run']
        assert run['failed'] > 0 and run['done'] > 0         # German fails, the rest goes on
        assert content_jobs.stats()['failed'] >= run['failed']
        lines = [r for r in caplog.records if 'content translations (backlog run)' in r.getMessage()]
        assert len(lines) == 1                              # one line per run
        line = lines[0]
        assert line.levelno == logging.INFO
        assert f'{run["done"]} done, {run["failed"]} failed' in line.getMessage()
        assert 'RuntimeError: model not found: claude-nonexistent' in line.getMessage()
        assert line.exc_info is not None                    # the first error in full, with its traceback
        page = _req(_client(app, admin), 'GET', '/admin/vertalingen').get_data(as_text=True)
        assert f'Mislukte pogingen sinds de laatste start: {content_jobs.stats()["failed"]}' in page
        assert 'model not found: claude-nonexistent' in page
        _fresh()
        assert content_i18n.text_for(issue, 'title', 'de') is None
        assert content_i18n.text_for(issue, 'title', 'fr') is not None
    finally:
        _cleanup(issue.id)


def test_a_wrong_key_stops_the_run_early(app, db, make_user, jobs, backlog_env, caplog, monkeypatch):
    monkeypatch.setattr(content_jobs, 'BACKLOG_BATCH', 1000)   # one batch, whatever the shared database holds
    admin = make_user(admin=True)
    issue, _proposal = _content(db, admin)
    try:
        caplog.set_level(logging.INFO, logger='app.content_jobs')
        client = BrokenClient()
        content_jobs.backlog_run(app, client=client)
        run = content_jobs.stats()['run']
        # The run stops at MAX_FAILED: exactly that many calls, all failed
        # (done only counts reused translations of the same text, no AI call).
        assert run['failed'] == content_jobs.MAX_FAILED
        assert len(client.calls) == content_jobs.MAX_FAILED
        assert any('invalid x-api-key' in r.getMessage() for r in caplog.records)
    finally:
        _cleanup(issue.id)


def test_the_background_job_and_the_tick_log_one_line(app, db, make_user, jobs, backlog_env, caplog, monkeypatch):
    user = make_user()
    issue = _issue(db, user, 'Een vraagstuk voor de log')
    try:
        caplog.set_level(logging.INFO, logger='app.content_jobs')
        for keys in jobs:
            content_jobs.run(app, keys, client=FailingClient('French'))
        lines = [r.getMessage() for r in caplog.records if 'content translations (after saving)' in r.getMessage()]
        assert len(lines) == 1 and 'failed; first error' in lines[0] and 'model not found' in lines[0]

        # The tick: the counts in its output and in the log; skipped during a backlog run.
        from app import digest
        monkeypatch.setenv('TICK_SECRET', 'geheim')
        monkeypatch.setattr(digest, 'tick', lambda: {})
        monkeypatch.setattr(content_jobs, 'backfill',
                            lambda tally=None, **kw: (tally.fail('x', RuntimeError('down')), 0)[1])
        headers = {'Authorization': 'Bearer geheim'}
        out = app.test_client().post('/intern/tik', headers=headers).get_json()
        assert out['translations'] == 0 and out['translations_failed'] == 1
        assert any('content translations (tick): 0 done, 1 failed' in r.getMessage() for r in caplog.records)
        monkeypatch.setitem(content_jobs._RUN, 'running', True)
        out = app.test_client().post('/intern/tik', headers=headers).get_json()
        assert out['translations_skipped'] == 'backlog run busy'
    finally:
        _cleanup(issue.id)


def test_the_tick_translates_more_per_hour():
    assert content_jobs.MAX_PER_TICK == 200
