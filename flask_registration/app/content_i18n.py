"""Multilingual phase 0 step 6: translations of content, the one entry point.

Every piece of content has one original, in the language it was written in
(the object's source language). Translations into other languages live in
the content_translations table (app.models.ContentTranslation), one row per
object, field and language. They are a cache: a translation belongs to the
version of the original it was made from (source_hash), so after an edit the
old translation is no longer used, and invalidate() removes it.

The rest of the code goes through these functions only:

    original(obj, field)                 the original text
    source_lang(obj)                     the language of the original
    text_for(obj, field, lang)           original or a current translation, else None
    display(obj, field, lang)            text_for, falling back to the original
    handwritten_for(obj, field, lang)    a current handwritten translation, else None
    translate(obj, field, lang)          AI translation, cached (is_machine=True)
    set_translation(obj, field, lang, text, is_machine=False)
    invalidate(obj, field=None)          drop the translations (after an edit)
    reader_view(obj, field, lang)        (text, written for the reader) for the post views
    view(obj, field, lang)               View(text, original, source, kind) for the label
    preload(objs, *fields)               one query for a page full of objects
    set_label(obj, field, label)         a gettext label in every enabled language
    all_texts(obj, fields)               originals and translations (search)
"""
import hashlib
import logging
from collections import namedtuple
from datetime import datetime

from flask import g, has_app_context
from sqlalchemy import event, exists, func, select

import config
from app import db
from app.models import ContentTranslation

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)

# The attribute that holds the source language, per table. The object's
# own language field where it has one; None means the default language.
_SOURCE_ATTR = {
    'posts': 'source_lang',
    'threads': 'source_lang',
    'quality_awards': 'post_lang',          # the AI writes in the post's language
    'quality_objections': 'ai_response_lang',
    # Multilingual phase 2a: issues, proposals and the follow-up have their
    # own lang field; a version is in the language of its proposal.
    'issues': 'lang',
    'issue_proposals': 'lang',
    'issue_proposal_versions': lambda version: version.proposal.lang if version.proposal else None,
    'issue_followups': 'lang',
    'fact_disputes': 'lang',                # the note, translated on request
    # Background figures: the name of an average comes from the source in English.
    'fact_charts': lambda chart: 'en',
}


def object_type(obj):
    return obj.__tablename__


def source_lang(obj):
    attr = _SOURCE_ATTR.get(object_type(obj))
    if callable(attr):
        lang = attr(obj)
    else:
        lang = getattr(obj, attr, None) if attr else None
    return lang or config.DEFAULT_LANGUAGE


def original(obj, field):
    return getattr(obj, field)


def source_hash(text):
    return hashlib.sha256((text or '').encode('utf-8')).hexdigest()


# ── Reading ────────────────────────────────────────────────────────────────

# A cached translation: plain values, not ORM rows (those expire on commit).
_Cached = namedtuple('_Cached', 'text is_machine source_hash')


def _cached(row):
    return _Cached(row.text, bool(row.is_machine), row.source_hash)


def _cache():
    """(object_type, object_id, field) -> {lang: _Cached}, complete for that
    object and field once loaded. Lives in g and only within one
    transaction: every commit or rollback clears it (see the listeners at
    the bottom), so it never outlives the data it was read from."""
    if not has_app_context():
        return {}
    if not hasattr(g, '_content_translations'):
        g._content_translations = {}
    return g._content_translations


def clear_cache(*_args):
    if has_app_context():
        g.pop('_content_translations', None)


def _key(obj, field):
    return (object_type(obj), obj.id, field)


def _rows(obj, field):
    if obj.id is None:
        return {}
    cache = _cache()
    key = _key(obj, field)
    if key not in cache:
        rows = ContentTranslation.query.filter_by(object_type=key[0], object_id=key[1], field=field).all()
        cache[key] = {row.lang: _cached(row) for row in rows}
    return cache[key]


def preload(objs, *fields):
    """Fetch the translations of some fields for many objects in one query (a
    thread page, an overview), so the per-object lookups below hit a cache."""
    objs = [o for o in objs if o is not None and o.id is not None]
    if not objs or not fields:
        return
    cache = _cache()
    by_type = {}
    for obj in objs:
        by_type.setdefault(object_type(obj), set()).add(obj.id)
    for kind, ids in by_type.items():
        for oid in ids:
            for field in fields:
                cache[(kind, oid, field)] = {}
        for chunk in _chunks(sorted(ids), 500):
            rows = ContentTranslation.query.filter(ContentTranslation.object_type == kind,
                                                   ContentTranslation.object_id.in_(chunk),
                                                   ContentTranslation.field.in_(fields)).all()
            for row in rows:
                cache[(kind, row.object_id, row.field)].update({row.lang: _cached(row)})


def _chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _current(obj, field, lang):
    """The translation row when it belongs to the current original, else None."""
    row = _rows(obj, field).get(lang)
    if row is None or row.source_hash != source_hash(original(obj, field)):
        return None
    return row


def text_for(obj, field, lang):
    """The text in `lang`: the original when `lang` is the source language,
    else a translation made from the current original, else None."""
    if lang == source_lang(obj):
        return original(obj, field)
    row = _current(obj, field, lang)
    return row.text if row is not None else None


def display(obj, field, lang):
    """What to show a reader of `lang`: text_for, else the original."""
    text = text_for(obj, field, lang)
    return text if text is not None else original(obj, field)


def handwritten_for(obj, field, lang):
    """A current handwritten translation into `lang` (editors, demo content),
    else None."""
    if lang == source_lang(obj):
        return None
    row = _current(obj, field, lang)
    return row.text if row is not None and not row.is_machine else None


def has_handwritten(obj, field):
    """True when the field has a current handwritten translation: the content
    was written for more than one language (demo content, editors)."""
    digest = source_hash(original(obj, field))
    return any(not row.is_machine and row.source_hash == digest for row in _rows(obj, field).values())


def all_texts(obj, fields):
    """The originals and every current translation of some fields (for
    searching in all languages)."""
    out = []
    for field in fields:
        value = original(obj, field)
        if value:
            out.append(value)
        digest = source_hash(value)
        out.extend(row.text for row in _rows(obj, field).values() if row.source_hash == digest)
    return out


# What a reader sees of a field: the text, the original and its language, and
# what kind of text it is: 'own' (the reader's language is the original's),
# 'handwritten' or 'machine' (a current translation), or 'pending' (no
# translation yet: the original is shown, with "Translation pending").
View = namedtuple('View', 'text original source kind')


def view(obj, field, lang):
    """The View of a field for a reader of `lang`. Never calls the AI. An
    object outside the database (a derived old version) shows as is."""
    if not hasattr(obj, '__tablename__'):
        text = getattr(obj, field)
        return View(text, text, None, 'own')
    orig = original(obj, field)
    src = source_lang(obj)
    if lang == src:
        return View(orig, orig, src, 'own')
    row = _current(obj, field, lang)
    if row is None:
        return View(orig, orig, src, 'pending')
    return View(row.text, orig, src, 'machine' if row.is_machine else 'handwritten')


def reader_view(obj, field, lang):
    """(text, written_for_reader) as a reader of `lang` sees it. Content
    written for several languages (handwritten translations) shows in the
    reader's language as is, without the 'original in' banner or a translate
    button; anything else shows the original (a translation comes on request)."""
    text = handwritten_for(obj, field, lang)
    if text is not None:
        return text, True
    if lang == source_lang(obj) and has_handwritten(obj, field):
        return original(obj, field), True
    return original(obj, field), False


# ── Writing ────────────────────────────────────────────────────────────────

def set_translation(obj, field, lang, text, is_machine=False):
    """Store a translation of the current original (insert or replace). The
    caller commits. The object must have an id (flush first)."""
    if obj.id is None:
        db.session.flush()
    digest = source_hash(original(obj, field))
    row = ContentTranslation.query.filter_by(object_type=object_type(obj), object_id=obj.id,
                                             field=field, lang=lang).first()
    if row is None:
        row = ContentTranslation(object_type=object_type(obj), object_id=obj.id, field=field, lang=lang)
        db.session.add(row)
    row.text, row.is_machine, row.source_hash = text, bool(is_machine), digest
    row.created_at = datetime.utcnow()
    _rows(obj, field).update({lang: _cached(row)})  # keep the request cache current
    return row


def set_label(obj, field, label):
    """Fill a field with a fixed label from the gettext catalog (e.g. the
    eyebrow 'Issue' of a conversation): the original in the object's source
    language, a handwritten translation for every other enabled language.
    `label` is a lazy_gettext string. The caller commits."""
    from flask_babel import force_locale
    from app import i18n
    src = source_lang(obj)
    with force_locale(src):
        setattr(obj, field, str(label))
    if obj.id is None:
        db.session.flush()
    for lang in i18n.enabled_codes():
        if lang != src:
            with force_locale(lang):
                set_translation(obj, field, lang, str(label))


def translate(obj, field, lang, client=None):
    """The text in `lang`, translated by the AI when there is no current
    translation yet, then cached (is_machine=True) and committed. Raises when
    the AI is unavailable or fails."""
    existing = text_for(obj, field, lang)
    if existing is not None:
        return existing
    source = original(obj, field) or ''
    reused = _same_text_translated(source, lang)
    if reused is not None:  # the same text was translated before (a proposal and its version)
        set_translation(obj, field, lang, reused.text, is_machine=reused.is_machine)
        db.session.commit()
        return reused.text
    from app import quality
    translated = quality.translate_text(source, lang, client=client)
    set_translation(obj, field, lang, translated, is_machine=True)
    db.session.commit()
    return translated


def _same_text_translated(source, lang):
    """A current translation into `lang` of exactly this text, of any object."""
    return (ContentTranslation.query.filter_by(source_hash=source_hash(source), lang=lang)
            .order_by(ContentTranslation.is_machine).first())


def invalidate(obj, field=None):
    """Drop the translations of one field (or of all fields) of an object, for
    instance after its original was edited. The caller commits."""
    if obj.id is None:
        return
    q = ContentTranslation.query.filter_by(object_type=object_type(obj), object_id=obj.id)
    if field is not None:
        q = q.filter_by(field=field)
    q.delete(synchronize_session=False)
    cache = _cache()
    for key in [k for k in cache if k[0] == object_type(obj) and k[1] == obj.id
                and (field is None or k[2] == field)]:
        cache[key] = {}


def delete_for(kind, ids):
    """Drop the translations of objects of one table (`kind`, e.g. 'issues')
    that are deleted in bulk (a query delete passes the ORM listener below)."""
    ids = [i for i in ids if i is not None]
    if ids:
        ContentTranslation.query.filter(ContentTranslation.object_type == kind,
                                        ContentTranslation.object_id.in_(ids)).delete(synchronize_session=False)


def _orphan_conditions():
    """(kind, where clause) per object type in content_translations whose
    table is known: the rows whose object no longer exists in that table. An
    object type without a known table is left alone."""
    table = ContentTranslation.__table__
    kinds = db.session.execute(select(table.c.object_type).distinct()).scalars().all()
    for kind in sorted(kinds):
        target = db.metadata.tables.get(kind)
        if target is None or 'id' not in target.c:
            continue
        yield kind, (table.c.object_type == kind) & ~exists().where(target.c.id == table.c.object_id)


def orphans():
    """{kind: n}: translations whose object no longer exists, per object type
    (only types that have any). Reads only."""
    table = ContentTranslation.__table__
    out = {}
    for kind, where in _orphan_conditions():
        n = db.session.execute(select(func.count()).select_from(table).where(where)).scalar()
        if n:
            out[kind] = n
    return out


def remove_orphans():
    """Delete the translations whose object no longer exists (left behind by
    bulk deletes from before delete_for), log one INFO line per object type and
    commit. Idempotent: a second call removes nothing. Returns {kind: n}."""
    table = ContentTranslation.__table__
    removed = {}
    for kind, where in list(_orphan_conditions()):
        n = db.session.execute(table.delete().where(where)).rowcount
        if n:
            removed[kind] = n
            log.info('Orphan translations removed: %d (%s)', n, kind)
    db.session.commit()
    return removed


# A deleted object takes its translations along (ORM deletes; bulk query
# deletes clean up themselves where they matter).
def _delete_translations(mapper, connection, target):
    table = ContentTranslation.__table__
    connection.execute(table.delete().where(table.c.object_type == target.__tablename__)
                       .where(table.c.object_id == target.id))


def register_cleanup(*models):
    for model in models:
        event.listen(model, 'after_delete', _delete_translations)


def _register_models():
    from app.models import (ChartSnapshot, FactChart, FactDispute, Issue, IssueFollowup, IssueProposal,
                            IssueProposalVersion, Post, QualityAward, QualityObjection, Thread)
    register_cleanup(Post, Thread, QualityAward, QualityObjection,
                     Issue, IssueProposal, IssueProposalVersion, IssueFollowup, FactDispute,
                     FactChart, ChartSnapshot)
    event.listen(db.session, 'after_commit', clear_cache)
    event.listen(db.session, 'after_rollback', clear_cache)


_register_models()
