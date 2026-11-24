"""Multilingual phase 0 step 6: copy the old per-language columns into the
originals and content_translations, then drop those columns. The only code
that still names the old columns (body_nl/_en, author_badge_nl/_en,
fact_title_en, explanation_nl/_en, ai_response_nl/_en, eyebrow_*/title_*_nl/_en);
the models no longer have them.

Runs at every start (app/__init__.py:_migrate_columns) and is idempotent:
    migrate(engine)       an original is only filled when it is still empty,
                          and a translation row is only added when there is
                          none yet for that object, field and language. Every
                          part skips a table whose old columns are gone.
    drop_old_columns(engine)
                          after migrate, in the same start: drops the old
                          columns (and author_badge, which no page shows) that
                          still exist. A database where migrate never ran
                          (an old local copy) is migrated first, so nothing is
                          lost; on every later start both do nothing.
Every start prints one line per step with what it did (the deploy log).
"""
import hashlib
from datetime import datetime

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text

_OLD_LANGS = ('nl', 'en')

# Dropped by drop_old_columns: the old per-language columns, and
# posts.author_badge (the original of step 6b; no page shows it).
DROPPED_COLUMNS = {
    'posts': ('body_nl', 'body_en', 'author_badge_nl', 'author_badge_en', 'fact_title_en', 'author_badge'),
    'threads': ('eyebrow_nl', 'eyebrow_en', 'title_prefix_nl', 'title_prefix_en',
                'title_accent_nl', 'title_accent_en', 'title_suffix_nl', 'title_suffix_en'),
    'quality_awards': ('explanation_nl', 'explanation_en'),
    'quality_objections': ('ai_response_nl', 'ai_response_en'),
}


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
    """6b: body and fact_title of posts (the author's badge is not migrated:
    no page shows it, drop_old_columns removes it)."""
    cols = _columns(conn, 'posts')
    old = [c for c in ('body_nl', 'body_en', 'fact_title_en') if c in cols]
    if not old:
        return
    writer = _Writer(conn, 'posts')
    rows = conn.execute(text(
        f'SELECT id, body, source_lang, is_demo, fact_title, {", ".join(old)} FROM posts '
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


_THREAD_FIELDS = ('eyebrow', 'title_prefix', 'title_accent', 'title_suffix')


def migrate_threads(conn, counts):
    """6d: the eyebrow and title of a conversation. The Dutch columns become
    the original (source_lang nl), the English ones handwritten translations
    where they differ."""
    cols = _columns(conn, 'threads')
    old = [f'{f}_{lang}' for f in _THREAD_FIELDS for lang in _OLD_LANGS if f'{f}_{lang}' in cols]
    if not old or not all(f in cols for f in _THREAD_FIELDS + ('source_lang',)):
        return
    writer = _Writer(conn, 'threads')
    rows = conn.execute(text(
        f'SELECT id, source_lang, {", ".join(_THREAD_FIELDS)}, {", ".join(old)} FROM threads '
        f'WHERE {" OR ".join(f"{c} IS NOT NULL" for c in old)}')).mappings().all()
    filled = 0
    for row in rows:
        if row['source_lang'] is None:
            # Not migrated yet: the Dutch columns are the original.
            conn.execute(text(
                'UPDATE threads SET source_lang = :l, eyebrow = :e, title_prefix = :p, title_accent = :a, '
                'title_suffix = :s WHERE id = :i AND source_lang IS NULL'),
                {'l': 'nl', 'e': row.get('eyebrow_nl'), 'p': row.get('title_prefix_nl') or '',
                 'a': row.get('title_accent_nl'), 's': row.get('title_suffix_nl') or '', 'i': row['id']})
            filled += 1
            originals = {f: row.get(f'{f}_nl') for f in _THREAD_FIELDS}
        else:
            originals = {f: row[f] for f in _THREAD_FIELDS}
        for field in _THREAD_FIELDS:
            value = row.get(f'{field}_en')
            if value != originals[field]:
                writer.add(row['id'], field, 'en', value, originals[field], False)
    counts['threads filled'] = filled
    counts.update(writer.counts)


def migrate_issue_threads(conn, counts):
    """Step 6 after-care: a conversation that belongs to an issue is in the
    issue's language. migrate_threads gave every existing conversation
    source_lang nl; here the conversations of an issue (issues.thread_id) get
    the issue's language where it differs. Editorial conversations stay nl.

    Per field, a handwritten translation into the new source language
    becomes the original and the old original becomes the translation into
    the old language (the eyebrow 'Vraagstuk' / 'Issue'). A field without
    such a translation keeps its original (an issue title was written once,
    the same in both old columns). Idempotent: a second run finds no
    conversation whose language differs from its issue."""
    from app import i18n
    if not {'lang', 'thread_id'} <= _columns(conn, 'issues') or 'source_lang' not in _columns(conn, 'threads'):
        return
    rows = conn.execute(text(
        'SELECT t.id, t.source_lang, i.lang, t.eyebrow, t.title_prefix, t.title_accent, t.title_suffix '
        'FROM threads t JOIN issues i ON i.thread_id = t.id '
        'WHERE i.id = (SELECT MIN(i2.id) FROM issues i2 WHERE i2.thread_id = t.id) '
        'ORDER BY t.id')).mappings().all()
    changed = 0
    for row in rows:
        old, new = i18n.normalize(row['source_lang']), i18n.normalize(row['lang'])
        if old == new:
            continue
        values = {}
        for field in _THREAD_FIELDS:
            swap = conn.execute(text(
                "SELECT text, is_machine FROM content_translations WHERE object_type = 'threads' "
                'AND object_id = :i AND field = :f AND lang = :l'),
                {'i': row['id'], 'f': field, 'l': new}).first()
            if swap is None or swap.is_machine or not swap.text:
                values[field] = row[field]
                continue
            values[field] = swap.text
            conn.execute(text(
                "DELETE FROM content_translations WHERE object_type = 'threads' AND object_id = :i "
                'AND field = :f AND lang IN (:new, :old)'),
                {'i': row['id'], 'f': field, 'new': new, 'old': old})
            if row[field] and row[field] != swap.text:
                conn.execute(text(
                    'INSERT INTO content_translations (object_type, object_id, field, lang, text, is_machine, '
                    "source_hash, created_at) VALUES ('threads', :i, :f, :l, :x, :m, :h, :c)"),
                    {'i': row['id'], 'f': field, 'l': old, 'x': row[field], 'm': False,
                     'h': _hash(swap.text), 'c': datetime.utcnow()})
        conn.execute(text(
            'UPDATE threads SET source_lang = :l, eyebrow = :e, title_prefix = :p, title_accent = :a, '
            'title_suffix = :s WHERE id = :i'),
            {'l': new, 'e': values['eyebrow'], 'p': values['title_prefix'], 'a': values['title_accent'],
             's': values['title_suffix'], 'i': row['id']})
        changed += 1
    counts['threads.source_lang from issue'] = changed


def migrate(engine):
    """Run every content migration in one transaction; print what it added."""
    counts = {}
    with engine.connect() as conn:
        if 'content_translations' not in sa_inspect(conn).get_table_names():
            return counts
        migrate_posts(conn, counts)
        migrate_awards(conn, counts)
        migrate_objections(conn, counts)
        migrate_threads(conn, counts)
        migrate_issue_threads(conn, counts)
        conn.commit()
    summary = ', '.join(f'{k} {v}' for k, v in sorted(counts.items())) or 'nothing to migrate'
    print(f'Content migration (multilingual step 6): {summary}', flush=True)
    return counts


def drop_old_columns(engine):
    """Drop the columns of DROPPED_COLUMNS that still exist, and the
    translations of posts.author_badge. Call it after migrate. Returns the
    dropped columns as ['table.column']; a second start drops nothing."""
    dropped = []
    with engine.connect() as conn:
        dialect = conn.dialect.name
        for table, names in DROPPED_COLUMNS.items():
            present = _columns(conn, table)
            for name in names:
                if name not in present:
                    continue
                if dialect == 'postgresql':
                    conn.execute(text(f'ALTER TABLE {table} DROP COLUMN IF EXISTS {name}'))
                else:  # SQLite (3.35 and later) and MySQL have no IF EXISTS here: checked above
                    conn.execute(text(f'ALTER TABLE {table} DROP COLUMN {name}'))
                dropped.append(f'{table}.{name}')
        if 'content_translations' in sa_inspect(conn).get_table_names():
            gone = conn.execute(text("DELETE FROM content_translations "
                                     "WHERE object_type = 'posts' AND field = 'author_badge'")).rowcount
            if gone:
                dropped.append(f'content_translations author_badge {gone}')
        conn.commit()
    print(f'Old language columns: {"dropped " + ", ".join(dropped) if dropped else "none left"}', flush=True)
    return dropped
