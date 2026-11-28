"""Multilingual phase 2: issues, proposals and facts are translated right away.

Issues (title, intro, and the title of their conversation), proposals
(statement, explanation), every version of a proposal, the follow-up
updates and facts (Post with kind 'feit': fact_title and body, not the
source) are machine-translated into every enabled language (except their
own) as soon as they are saved, without making the save wait:

    schedule(obj)       after creating or editing: translate obj once the
                        current transaction commits, in a background thread
    translate_missing(obj, client=None)
                        the work itself: every missing or outdated
                        translation of obj (synchronous; the thread calls it)
    backfill(limit, client=None)
                        the safety net of the hourly tick (/intern/tik): what
                        is still missing or outdated, newest first, at most
                        `limit` AI calls per tick
    backlog()           per type of content and language, the number of
                        fields still missing or outdated (the admin page
                        /admin/vertalingen)
    start_backlog_run(app)
                        the button on that page: the backlog in one go, in a
                        background thread, in small batches with a pause; one
                        run at a time

Every run (the background job, a tick, a backlog run) logs one INFO line with
the number done and failed, with the first error in full (a wrong key or
model shows up in the Render log at once). The failures since the process
started are counted in memory (stats()).

The thread has its own app context and database session (we run on one
gunicorn worker). Without an AI key nothing is started; the tick fills the
gaps later. Reading never calls the AI: a page shows the original with the
label "Translation pending" until the translation is there
(content_i18n.view). Ordinary posts, reactions to a proposal and the note
of a fact dispute are not here: they are translated on request (the
translate button, "always translate"). The design: docs/i18n/machinevertaling.md.
"""
import logging
import threading
import time
from datetime import datetime

from flask import current_app
from sqlalchemy import event

from app import content_i18n, db, i18n

log = logging.getLogger(__name__)
# Production logs at WARNING (app/__init__.py); the run lines must reach the log.
log.setLevel(logging.INFO)

# The safety net translates at most this many fields per tick (once an hour).
MAX_PER_TICK = 200
# The backlog button: at most this many fields per run, in batches with a pause
# in between so the site stays responsive (one gunicorn worker).
BACKLOG_LIMIT = 2000
BACKLOG_BATCH = 20
BACKLOG_PAUSE = 1.0  # seconds
# A run stops after this many failures (a wrong key fails every call).
MAX_FAILED = 20


def _models():
    from app.models import Event, Issue, IssueFollowup, IssueProposal, IssueProposalVersion, Post
    return {
        # Only facts among the posts (see _only): ordinary posts are translated on request.
        'posts': (Post, ('fact_title', 'body')),
        'issues': (Issue, ('title', 'intro')),
        'issue_proposals': (IssueProposal, ('statement', 'explanation')),
        'issue_proposal_versions': (IssueProposalVersion, ('statement', 'explanation')),
        'issue_followups': (IssueFollowup, ('body',)),
        'events': (Event, ('name', 'note')),   # events step 1b
    }


def _only(model, query):
    """Narrow the safety net's query to what is translated right away."""
    from app.models import FACT_KIND, Post
    return query.filter(Post.kind == FACT_KIND) if model is Post else query


def _wanted(obj):
    return content_i18n.object_type(obj) != 'posts' or obj.is_fact


def fields_of(obj):
    return _models()[content_i18n.object_type(obj)][1]


def targets(obj):
    """The languages obj is translated into: every enabled one but its own."""
    src = content_i18n.source_lang(obj)
    return [lang for lang in i18n.enabled_codes() if lang != src]


def _parts(obj):
    """(object, field) pairs to translate for obj: its own fields and, for an
    issue, the title of its conversation (the same text, so the translation
    is reused: the conversations list shows it without an extra AI call)."""
    if not _wanted(obj):
        return []
    parts = [(obj, field) for field in fields_of(obj)]
    thread = getattr(obj, 'thread', None) if content_i18n.object_type(obj) == 'issues' else None
    if thread is not None and thread.title_prefix == obj.title and not thread.title_accent:
        parts.append((thread, 'title_prefix'))
    # Background figures: the name of a chart's average goes with its fact
    # (counted with the facts on /admin/vertalingen).
    if content_i18n.object_type(obj) == 'posts' and obj.fact_type == 'cijfer':
        chart = obj.chart
        if chart is not None and chart.average_name:
            parts.append((chart, 'average_name'))
        snapshot = chart.current_snapshot if chart is not None else None
        if snapshot is not None:   # the definition and unit as the source gives them (English)
            parts += [(snapshot, 'definition'), (snapshot, 'unit')]
            # Step 3c (Eurostat): the selection and the labels of the flags, as short fields.
            parts += [(snapshot, field) for field in content_i18n.label_fields(snapshot)]
    return parts


def missing(obj):
    """(object, field, lang) without a current translation (a field without
    text needs none)."""
    out = []
    for target, field in _parts(obj):
        if not (content_i18n.original(target, field) or '').strip():
            continue
        out.extend((target, field, lang) for lang in targets(target)
                   if content_i18n.text_for(target, field, lang) is None)
    return out


# ── Counting a run ────────────────────────────────────────────────────────

class Tally:
    """What one run did: fields done, fields failed, the first error."""

    def __init__(self):
        self.done = 0
        self.failed = 0
        self.first_error = None   # (what, exception)

    def fail(self, what, exc):
        self.failed += 1
        if self.first_error is None:
            self.first_error = (what, exc)
        with _LOCK:
            _STATS['failed'] += 1
            _STATS['last_error'] = f'{what}: {type(exc).__name__}: {exc}'

    def log(self, name):
        """One INFO line for the run; on a failure the first error in full."""
        if self.first_error is None:
            log.info('content translations (%s): %d done, %d failed', name, self.done, self.failed)
            return
        what, exc = self.first_error
        log.info('content translations (%s): %d done, %d failed; first error, %s: %s: %s',
                 name, self.done, self.failed, what, type(exc).__name__, exc,
                 exc_info=(type(exc), exc, exc.__traceback__))


_LOCK = threading.Lock()
# Since the process started: failed attempts and the last error.
_STATS = {'failed': 0, 'last_error': None}
# The backlog run of the admin page (one at a time).
_RUN = {'running': False, 'done': 0, 'failed': 0, 'limit': 0, 'started': None, 'finished': None}


def stats():
    """The failures since the process started and the state of the backlog run."""
    with _LOCK:
        return {**_STATS, 'run': dict(_RUN)}


def translate_missing(obj, client=None, limit=None, tally=None, fail_limit=None):
    """Translate what obj is missing; returns the number of fields translated.
    A failure is counted in `tally` (and in stats()) and skipped (the tick
    tries again); it stops once tally.failed reaches `fail_limit`."""
    tally = tally if tally is not None else Tally()
    done = 0
    for target, field, lang in missing(obj):
        if limit is not None and done >= limit:
            break
        if fail_limit is not None and tally.failed >= fail_limit:
            break
        what = f'{content_i18n.object_type(target)} {target.id}.{field} to {lang}'
        try:
            content_i18n.translate(target, field, lang, client=client)
            done += 1
            tally.done += 1
        except Exception as exc:  # the AI is down or refuses: the tick retries
            db.session.rollback()
            log.debug('translation of %s failed: %s', what, exc)
            tally.fail(what, exc)
    return done


# ── After the commit, in the background ───────────────────────────────────

_PENDING = 'content_jobs_pending'


def schedule(obj):
    """Translate obj after the current transaction commits. Call it where obj is
    created or edited (after invalidate for an edit). Flushes to get an id.
    An ordinary post is skipped (translated on request)."""
    if not _wanted(obj):
        return
    if obj.id is None:
        db.session.flush()
    db.session.info.setdefault(_PENDING, set()).add((content_i18n.object_type(obj), obj.id))


def _after_commit(session):
    keys = session.info.pop(_PENDING, None)
    if keys:
        _start(current_app._get_current_object(), sorted(keys))


def _after_rollback(session):
    session.info.pop(_PENDING, None)


def ai_available(app=None):
    """True when there is an AI key. The tests switch this off (conftest)."""
    return bool((app or current_app).config.get('ANTHROPIC_API_KEY'))


def _start(app, keys):
    """Run the translations in a thread. Tests replace this to run them in line."""
    if not ai_available(app):
        return  # no AI here: the tick fills the gaps once there is
    threading.Thread(target=run, args=(app, keys), daemon=True, name='content-translations').start()


def run(app, keys, client=None):
    """The background work: its own app context and database session."""
    with app.app_context():
        tally = Tally()
        try:
            models = _models()
            for kind, oid in keys:
                obj = db.session.get(models[kind][0], oid)
                if obj is not None:
                    translate_missing(obj, client=client, tally=tally)
        finally:
            tally.log('after saving')
            db.session.remove()


# ── The safety net of the hourly tick ─────────────────────────────────────

def _scan():
    """Every object translated right away, newest first per type, with its
    translations preloaded (and those of the conversation titles of issues)."""
    for kind, (model, fields) in _models().items():
        objs = _only(model, model.query).order_by(model.id.desc()).all()
        content_i18n.preload(objs, *fields)
        if kind == 'issues':
            threads = [o.thread for o in objs if getattr(o, 'thread', None) is not None]
            if threads:
                content_i18n.preload(threads, 'title_prefix')
        yield kind, objs


def backfill(limit=MAX_PER_TICK, client=None, tally=None, name='tick', max_failed=None):
    """Translate what is still missing or outdated, newest content first, at
    most `limit` fields; stop after `max_failed` failures. Without an AI key
    (and no client) it does nothing. Logs one line, unless the caller passes
    its own tally (and logs it). max_failed defaults to MAX_FAILED, read at
    the call."""
    if client is None and not ai_available():
        return 0
    if max_failed is None:
        max_failed = MAX_FAILED
    own = tally is None
    tally = Tally() if own else tally
    fail_limit = tally.failed + max_failed
    done = 0
    try:
        for _kind, objs in _scan():
            for obj in objs:
                if done >= limit or tally.failed >= fail_limit:
                    return done
                done += translate_missing(obj, client=client, limit=limit - done, tally=tally,
                                          fail_limit=fail_limit)
        return done
    finally:
        if own:
            tally.log(name)


# ── The backlog: what is missing, and the button to translate it now ──────

# The types of content on the admin page, in this order (the conversation
# title of an issue counts with the issue).
BACKLOG_KINDS = ('issues', 'issue_proposals', 'issue_proposal_versions', 'issue_followups', 'posts', 'events')


def backlog():
    """{kind: {lang: n}}: per type of content and target language, the number
    of fields with a text but no current translation. Reads only."""
    langs = i18n.enabled_codes()
    out = {kind: dict.fromkeys(langs, 0) for kind in BACKLOG_KINDS}
    for kind, objs in _scan():
        for obj in objs:
            for _target, _field, code in missing(obj):
                out[kind][code] += 1
    return out


def backlog_running():
    with _LOCK:
        return _RUN['running']


def _spawn(target, args):
    """Start a daemon thread. Tests replace this to run it in line."""
    threading.Thread(target=target, args=args, daemon=True, name='content-translations-backlog').start()


def start_backlog_run(app, limit=BACKLOG_LIMIT):
    """Start translating the backlog in the background. Returns False (and
    starts nothing) when a run is already busy or there is no AI key."""
    if not ai_available(app):
        return False
    with _LOCK:
        if _RUN['running']:
            return False
        _RUN.update(running=True, done=0, failed=0, limit=limit,
                    started=datetime.utcnow(), finished=None)
    try:
        _spawn(backlog_run, (app, limit))
    except Exception:
        with _LOCK:
            _RUN.update(running=False, finished=datetime.utcnow())
        raise
    return True


def backlog_run(app, limit=BACKLOG_LIMIT, client=None):
    """The backlog run itself: batches of BACKLOG_BATCH fields with a short
    pause, until `limit` is reached, a batch translates nothing (nothing left)
    or a batch reaches MAX_FAILED failures (a wrong key: the reuse of an
    existing translation still succeeds, so done alone does not show it). The
    next tick tries again."""
    with app.app_context():
        tally = Tally()
        try:
            while tally.done < limit:
                before, failed_before = tally.done, tally.failed
                backfill(limit=min(BACKLOG_BATCH, limit - tally.done), client=client, tally=tally)
                with _LOCK:
                    _RUN.update(done=tally.done, failed=tally.failed)
                if tally.done == before or tally.failed - failed_before >= MAX_FAILED:
                    break
                db.session.remove()   # a fresh session per batch: nothing held in between
                time.sleep(BACKLOG_PAUSE)
        except Exception as exc:
            tally.fail('backlog run', exc)
        finally:
            tally.log('backlog run')
            with _LOCK:
                _RUN.update(running=False, done=tally.done, failed=tally.failed,
                            finished=datetime.utcnow())
            db.session.remove()


def _register():
    event.listen(db.session, 'after_commit', _after_commit)
    event.listen(db.session, 'after_rollback', _after_rollback)


_register()
