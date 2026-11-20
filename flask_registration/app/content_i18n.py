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
    preload(objs, field, lang)           one query for a page full of objects
"""
import hashlib
from datetime import datetime

from flask import g, has_app_context
from sqlalchemy import event

import config
from app import db
from app.models import ContentTranslation

# The attribute that holds the source language, per table. The object's
# own language field where it has one; None means the default language.
_SOURCE_ATTR = {
    'posts': 'source_lang',
    'threads': 'source_lang',
    'quality_awards': 'post_lang',          # the AI writes in the post's language
    'quality_objections': 'ai_response_lang',
}


def object_type(obj):
    return obj.__tablename__


def source_lang(obj):
    attr = _SOURCE_ATTR.get(object_type(obj))
    return (getattr(obj, attr, None) if attr else None) or config.DEFAULT_LANGUAGE


def original(obj, field):
    return getattr(obj, field)


def source_hash(text):
    return hashlib.sha256((text or '').encode('utf-8')).hexdigest()


# ── Reading ────────────────────────────────────────────────────────────────

_MISSING = object()


def _cache():
    if not has_app_context():
        return {}
    if not hasattr(g, '_content_translations'):
        g._content_translations = {}
    return g._content_translations


def _key(obj, field, lang):
    return (object_type(obj), obj.id, field, lang)


def _row(obj, field, lang):
    if obj.id is None:
        return None
    cache = _cache()
    key = _key(obj, field, lang)
    row = cache.get(key, _MISSING)
    if row is _MISSING:
        row = ContentTranslation.query.filter_by(object_type=key[0], object_id=key[1],
                                                 field=field, lang=lang).first()
        cache[key] = row
    return row


def preload(objs, field, lang):
    """Fetch the translations of one field for many objects in one query (a
    thread page, an overview), so the per-object lookups below hit a cache."""
    objs = [o for o in objs if o is not None and o.id is not None]
    if not objs:
        return
    cache = _cache()
    by_type = {}
    for obj in objs:
        by_type.setdefault(object_type(obj), set()).add(obj.id)
    for kind, ids in by_type.items():
        for oid in ids:
            cache.setdefault((kind, oid, field, lang), None)
        rows = ContentTranslation.query.filter(ContentTranslation.object_type == kind,
                                               ContentTranslation.object_id.in_(ids),
                                               ContentTranslation.field == field,
                                               ContentTranslation.lang == lang).all()
        for row in rows:
            cache[(kind, row.object_id, field, lang)] = row


def _current(obj, field, lang):
    """The translation row when it belongs to the current original, else None."""
    row = _row(obj, field, lang)
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
    else None. A handwritten translation is shown as is, without the
    'original in' banner or a translate button."""
    if lang == source_lang(obj):
        return None
    row = _current(obj, field, lang)
    return row.text if row is not None and not row.is_machine else None


def has_handwritten(obj, field):
    """True when the field has at least one current handwritten translation:
    the content was written for every language (demo, editors)."""
    if obj.id is None:
        return False
    digest = source_hash(original(obj, field))
    return db.session.query(ContentTranslation.id).filter_by(
        object_type=object_type(obj), object_id=obj.id, field=field,
        is_machine=False, source_hash=digest).first() is not None


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
    _cache()[_key(obj, field, lang)] = row
    return row


def translate(obj, field, lang, client=None):
    """The text in `lang`, translated by the AI when there is no current
    translation yet, then cached (is_machine=True) and committed. Raises when
    the AI is unavailable or fails."""
    existing = text_for(obj, field, lang)
    if existing is not None:
        return existing
    from app import quality
    source = original(obj, field) or ''
    translated = quality.translate_text(source, lang, client=client)
    set_translation(obj, field, lang, translated, is_machine=True)
    db.session.commit()
    return translated


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
        del cache[key]


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
    from app.models import Post, QualityAward, QualityObjection, Thread
    register_cleanup(Post, Thread, QualityAward, QualityObjection)


_register_models()
