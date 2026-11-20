"""Multilingual phase 0 step 6: copy the old per-language columns into the
originals and content_translations. The only code that still reads the old
columns (body_nl/_en, author_badge_nl/_en, fact_title_en, explanation_nl/_en,
ai_response_nl/_en, eyebrow_*/title_*_nl/_en); the models no longer have them.

Runs at every start (app/__init__.py:_migrate_columns) and is idempotent:
an original is only filled when it is still empty, and a translation row is
only added when there is none yet for that object, field and language. A
second start therefore creates and overwrites nothing. Every start prints one
line with what it added, so the deploy log shows the counts.
"""
import hashlib
from datetime import datetime

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text

_OLD_LANGS = ('nl', 'en')


def _hash(value):
    return hashlib.sha256((value or '').encode('utf-8')).hexdigest()


def _columns(conn, table):
    insp = sa_inspect(conn)
    if table not in insp.get_table_names():
        return set()
    return {c['name'] for c in insp.get_columns(table)}


class _Writer:
    """Collects new translation rows, skipping keys that already exist."""

    def __init__(self, conn, object_type):
        self.conn, self.object_type = conn, object_type
        self.existing = {tuple(r) for r in conn.execute(text(
            'SELECT object_id, field, lang FROM content_translations WHERE object_type = :t'),
            {'t': object_type})}
        self.counts = {}

    def add(self, object_id, field, lang, value, original, is_machine):
        if not value or not original or (object_id, field, lang) in self.existing:
            return
        self.conn.execute(text(
            'INSERT INTO content_translations (object_type, object_id, field, lang, text, is_machine, '
            'source_hash, created_at) VALUES (:t, :i, :f, :l, :x, :m, :h, :c)'),
            {'t': self.object_type, 'i': object_id, 'f': field, 'l': lang, 'x': value,
             'm': bool(is_machine), 'h': _hash(original), 'c': datetime.utcnow()})
        self.existing.add((object_id, field, lang))
        key = f'{self.object_type}.{field}'
        self.counts[key] = self.counts.get(key, 0) + 1


def _fill(conn, table, column, object_id, value):
    """Fill an empty original (never overwrite one)."""
    conn.execute(text(f"UPDATE {table} SET {column} = :v WHERE id = :i AND ({column} IS NULL OR {column} = '')"),
                 {'v': value, 'i': object_id})


def migrate_posts(conn, counts):
    """6b: body, author_badge and fact_title of posts."""
    cols = _columns(conn, 'posts')
    old = [c for c in ('body_nl', 'body_en', 'author_badge_nl', 'author_badge_en', 'fact_title_en') if c in cols]
    if not old or 'author_badge' not in cols:
        return
    writer = _Writer(conn, 'posts')
    rows = conn.execute(text(
        f'SELECT id, body, source_lang, is_demo, author_badge, fact_title, {", ".join(old)} FROM posts '
        f'WHERE {" OR ".join(f"{c} IS NOT NULL" for c in old)}')).mappings().all()
    filled = 0
    for row in rows:
        src = row['source_lang'] or 'nl'
        machine = not row['is_demo']   # demo and seed posts are written by hand in both languages
        # The body: the original is `body`; seed posts kept it in body_<source_lang>.
        body = row['body']
        if not body and row.get(f'body_{src}'):
            body = row[f'body_{src}']
            _fill(conn, 'posts', 'body', row['id'], body)
            filled += 1
        for lang in _OLD_LANGS:
            if lang != src:
                writer.add(row['id'], 'body', lang, row.get(f'body_{lang}'), body, machine)
        # The author's badge: one original in the post's language, the other
        # language as a handwritten translation when it differs.
        badge = row['author_badge']
        if not badge:
            badge = row.get(f'author_badge_{src}') or next(
                (row.get(f'author_badge_{lang}') for lang in _OLD_LANGS if row.get(f'author_badge_{lang}')), None)
            if badge:
                _fill(conn, 'posts', 'author_badge', row['id'], badge)
        for lang in _OLD_LANGS:
            value = row.get(f'author_badge_{lang}')
            if lang != src and value != badge:
                writer.add(row['id'], 'author_badge', lang, value, badge, False)
        # The fact title: fact_title is the original, fact_title_en the English cache.
        title = row['fact_title']
        if not title and row.get('fact_title_en'):
            title = row['fact_title_en']
            _fill(conn, 'posts', 'fact_title', row['id'], title)
        if src != 'en':
            writer.add(row['id'], 'fact_title', 'en', row.get('fact_title_en'), title, machine)
    counts['posts.body filled'] = filled
    counts.update(writer.counts)


def _pick(row, prefix, src):
    """(original, language) from the old columns: the source language first."""
    if row.get(f'{prefix}_{src}'):
        return row[f'{prefix}_{src}'], src
    for lang in _OLD_LANGS:
        if row.get(f'{prefix}_{lang}'):
            return row[f'{prefix}_{lang}'], lang
    return None, src


def migrate_awards(conn, counts):
    """6c: QualityAward.explanation, in post_lang (the AI writes in the
    language of the post). The other language was a machine cache for AI
    awards and handwritten for demo and moderator awards."""
    cols = _columns(conn, 'quality_awards')
    old = [c for c in ('explanation_nl', 'explanation_en') if c in cols]
    if not old or 'explanation' not in cols:
        return
    writer = _Writer(conn, 'quality_awards')
    rows = conn.execute(text(
        f'SELECT id, explanation, post_lang, source, {", ".join(old)} FROM quality_awards '
        f'WHERE {" OR ".join(f"{c} IS NOT NULL" for c in old)}')).mappings().all()
    filled = 0
    for row in rows:
        src = row['post_lang'] or 'nl'
        original = row['explanation']
        if not original:
            original, _lang = _pick(row, 'explanation', src)
            if original:
                _fill(conn, 'quality_awards', 'explanation', row['id'], original)
                filled += 1
        for lang in _OLD_LANGS:
            value = row.get(f'explanation_{lang}')
            if lang != src and value != original:
                writer.add(row['id'], 'explanation', lang, value, original, row['source'] == 'ai')
    counts['quality_awards.explanation filled'] = filled
    counts.update(writer.counts)


def migrate_objections(conn, counts):
    """6c: QualityObjection.ai_response with its language (new column): the
    language of the post, like the explanation; the other one is a cache."""
    cols = _columns(conn, 'quality_objections')
    old = [c for c in ('ai_response_nl', 'ai_response_en') if c in cols]
    if not old or 'ai_response' not in cols:
        return
    writer = _Writer(conn, 'quality_objections')
    rows = conn.execute(text(
        f'SELECT o.id, o.ai_response, o.ai_response_lang, a.post_lang, {", ".join("o." + c for c in old)} '
        f'FROM quality_objections o LEFT JOIN quality_awards a ON a.id = o.award_id '
        f'WHERE {" OR ".join(f"o.{c} IS NOT NULL" for c in old)}')).mappings().all()
    filled = 0
    for row in rows:
        original, src = row['ai_response'], row['ai_response_lang']
        if not original:
            original, src = _pick(row, 'ai_response', row['post_lang'] or 'nl')
            if original:
                conn.execute(text("UPDATE quality_objections SET ai_response = :v, ai_response_lang = :l "
                                  "WHERE id = :i AND (ai_response IS NULL OR ai_response = '')"),
                             {'v': original, 'l': src, 'i': row['id']})
                filled += 1
        for lang in _OLD_LANGS:
            value = row.get(f'ai_response_{lang}')
            if lang != src and value != original:
                writer.add(row['id'], 'ai_response', lang, value, original, True)
    counts['quality_objections.ai_response filled'] = filled
    counts.update(writer.counts)


def migrate(engine):
    """Run every content migration in one transaction; print what it added."""
    counts = {}
    with engine.connect() as conn:
        if 'content_translations' not in sa_inspect(conn).get_table_names():
            return counts
        migrate_posts(conn, counts)
        migrate_awards(conn, counts)
        migrate_objections(conn, counts)
        conn.commit()
    summary = ', '.join(f'{k} {v}' for k, v in sorted(counts.items())) or 'nothing to migrate'
    print(f'Content migration (multilingual step 6): {summary}', flush=True)
    return counts
