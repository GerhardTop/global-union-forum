"""Multilingual phase 2a: issues and proposals are translated right away.

Issues (title, intro), proposals (statement, explanation), every version of a
proposal and the follow-up updates are machine-translated into every enabled
language (except their own) as soon as they are saved, without making the
save wait:

    schedule(obj)       after creating or editing: translate obj once the
                        current transaction commits, in a background thread
    translate_missing(obj, client=None)
                        the work itself: every missing or outdated
                        translation of obj (synchronous; the thread calls it)
    backfill(limit, client=None)
                        the safety net of the hourly tick (/intern/tik): what
                        is still missing or outdated, newest first, at most
                        `limit` AI calls per tick

The thread has its own app context and database session (we run on one
gunicorn worker). Without an AI key nothing is started; the tick fills the
gaps later. Reading never calls the AI: a page shows the original with the
label "Translation pending" until the translation is there
(content_i18n.view). Posts and facts are not here: they are translated on
request (the translate button, "always translate").
"""
import logging
import threading

from flask import current_app
from sqlalchemy import event

from app import content_i18n, db, i18n

log = logging.getLogger(__name__)

# The safety net translates at most this many fields per tick.
MAX_PER_TICK = 40


def _models():
    from app.models import Issue, IssueFollowup, IssueProposal, IssueProposalVersion
    return {
        'issues': (Issue, ('title', 'intro')),
        'issue_proposals': (IssueProposal, ('statement', 'explanation')),
        'issue_proposal_versions': (IssueProposalVersion, ('statement', 'explanation')),
        'issue_followups': (IssueFollowup, ('body',)),
    }


def fields_of(obj):
    return _models()[content_i18n.object_type(obj)][1]


def targets(obj):
    """The languages obj is translated into: every enabled one but its own."""
    src = content_i18n.source_lang(obj)
    return [lang for lang in i18n.enabled_codes() if lang != src]


def missing(obj):
    """(field, lang) pairs without a current translation (a field without text
    needs none)."""
    out = []
    for field in fields_of(obj):
        if not (content_i18n.original(obj, field) or '').strip():
            continue
        out.extend((field, lang) for lang in targets(obj) if content_i18n.text_for(obj, field, lang) is None)
    return out


def translate_missing(obj, client=None, limit=None):
    """Translate what obj is missing; returns the number of fields translated.
    A failure is logged and skipped (the tick tries again)."""
    done = 0
    for field, lang in missing(obj):
        if limit is not None and done >= limit:
            break
        try:
            content_i18n.translate(obj, field, lang, client=client)
            done += 1
        except Exception as exc:  # the AI is down or refuses: the tick retries
            db.session.rollback()
            log.warning('translation of %s %s.%s to %s failed: %s',
                        content_i18n.object_type(obj), obj.id, field, lang, exc)
    return done


# ── After the commit, in the background ───────────────────────────────────

_PENDING = 'content_jobs_pending'


def schedule(obj):
    """Translate obj after the current transaction commits. Call it where obj is
    created or edited (after invalidate for an edit). Flushes to get an id."""
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
        try:
            models = _models()
            for kind, oid in keys:
                obj = db.session.get(models[kind][0], oid)
                if obj is not None:
                    translate_missing(obj, client=client)
        finally:
            db.session.remove()


# ── The safety net of the hourly tick ─────────────────────────────────────

def backfill(limit=MAX_PER_TICK, client=None):
    """Translate what is still missing or outdated, newest content first, at
    most `limit` fields. Without an AI key (and no client) it does nothing."""
    if client is None and not ai_available():
        return 0
    done = 0
    for _kind, (model, fields) in _models().items():
        objs = model.query.order_by(model.id.desc()).all()
        content_i18n.preload(objs, *fields)
        for obj in objs:
            if done >= limit:
                return done
            done += translate_missing(obj, client=client, limit=limit - done)
    return done


def _register():
    event.listen(db.session, 'after_commit', _after_commit)
    event.listen(db.session, 'after_rollback', _after_rollback)


_register()
