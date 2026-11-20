"""Multilingual phase 0 step 6: the data migration from the old per-language
columns (app/content_migration.py), on a separate database that still has
those columns. Idempotent: a second run adds and overwrites nothing.
"""
import hashlib

import pytest
from sqlalchemy import create_engine, text

from app import content_migration
from app.models import ContentTranslation
from tests.test_issue_polish import make_user  # noqa: F401  (fixture)


def _h(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


@pytest.fixture
def old_db(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "old.sqlite3"}')
    ContentTranslation.__table__.create(engine)
    with engine.begin() as conn:
        conn.execute(text(
            'CREATE TABLE posts (id INTEGER PRIMARY KEY, body TEXT, source_lang VARCHAR(5), is_demo BOOLEAN, '
            'author_badge VARCHAR(200), fact_title VARCHAR(100), body_nl TEXT, body_en TEXT, '
            'author_badge_nl VARCHAR(200), author_badge_en VARCHAR(200), fact_title_en VARCHAR(100))'))
        conn.execute(text("INSERT INTO posts VALUES "
                          # 1: a seed post, the text only in the old columns
                          "(1, NULL, 'nl', 1, NULL, NULL, 'Hallo.', 'Hello.', 'Handelsrecht', 'Trade law', NULL),"
                          # 2: a member's post with a cached machine translation
                          "(2, 'An English post.', 'en', 0, NULL, NULL, '[nl] Een Engels bericht.', NULL, NULL, NULL, NULL),"
                          # 3: a fact with an English title
                          "(3, 'Een feit.', 'nl', 0, NULL, 'Kern', NULL, NULL, NULL, NULL, 'Core'),"
                          # 4: a demo badge that is the same in both languages
                          "(4, 'Tekst.', 'nl', 1, NULL, NULL, NULL, NULL, 'LSE', 'LSE', NULL),"
                          # 5: nothing to migrate
                          "(5, 'Alleen origineel.', 'nl', 0, NULL, NULL, NULL, NULL, NULL, NULL, NULL)"))
    return engine


def _translations(engine):
    with engine.connect() as conn:
        return sorted(tuple(r) for r in conn.execute(text(
            'SELECT object_type, object_id, field, lang, text, is_machine, source_hash FROM content_translations')))


def test_posts_are_migrated(old_db):
    counts = content_migration.migrate(old_db)
    with old_db.connect() as conn:
        posts = {r[0]: tuple(r[1:]) for r in conn.execute(text('SELECT id, body, author_badge, fact_title FROM posts'))}
    assert posts[1] == ('Hallo.', 'Handelsrecht', None)       # filled from body_nl / author_badge_nl
    assert posts[2] == ('An English post.', None, None)
    assert posts[3] == ('Een feit.', None, 'Kern')
    assert posts[4] == ('Tekst.', 'LSE', None)
    assert _translations(old_db) == [
        ('posts', 1, 'author_badge', 'en', 'Trade law', False, _h('Handelsrecht')),
        ('posts', 1, 'body', 'en', 'Hello.', False, _h('Hallo.')),                 # demo: handwritten
        ('posts', 2, 'body', 'nl', '[nl] Een Engels bericht.', True, _h('An English post.')),  # machine
        ('posts', 3, 'fact_title', 'en', 'Core', True, _h('Kern')),
    ]
    assert counts == {'posts.body filled': 1, 'posts.body': 2, 'posts.author_badge': 1, 'posts.fact_title': 1}


def test_a_second_run_changes_nothing(old_db):
    content_migration.migrate(old_db)
    before = _translations(old_db)
    with old_db.begin() as conn:
        # An edit after the first run: the original changed, the old columns did not.
        conn.execute(text("UPDATE posts SET body = 'Hallo, bewerkt.' WHERE id = 1"))
    counts = content_migration.migrate(old_db)
    assert _translations(old_db) == before
    with old_db.connect() as conn:
        assert conn.execute(text('SELECT body FROM posts WHERE id = 1')).scalar() == 'Hallo, bewerkt.'
    assert counts == {'posts.body filled': 0}


@pytest.fixture
def old_awards(old_db):
    with old_db.begin() as conn:
        conn.execute(text(
            'CREATE TABLE quality_awards (id INTEGER PRIMARY KEY, explanation VARCHAR(400), post_lang VARCHAR(5), '
            'source VARCHAR(10), explanation_nl VARCHAR(400), explanation_en VARCHAR(400))'))
        conn.execute(text("INSERT INTO quality_awards VALUES "
                          # 1: AI, English post, with a machine translation into Dutch
                          "(1, NULL, 'en', 'ai', '[nl] Een nieuw argument.', 'A new argument.'),"
                          # 2: AI, Dutch post, not translated
                          "(2, NULL, 'nl', 'ai', 'Nieuw argument.', NULL),"
                          # 3: demo, handwritten in both languages
                          "(3, NULL, 'nl', 'demo', 'Neemt het serieus.', 'Takes it seriously.'),"
                          # 4: moderator note, the same text in both
                          "(4, NULL, 'nl', 'moderator', 'Notitie.', 'Notitie.')"))
        conn.execute(text(
            'CREATE TABLE quality_objections (id INTEGER PRIMARY KEY, award_id INTEGER, ai_response TEXT, '
            'ai_response_lang VARCHAR(5), ai_response_nl TEXT, ai_response_en TEXT)'))
        conn.execute(text("INSERT INTO quality_objections VALUES "
                          "(1, 1, NULL, NULL, '[nl] Het oordeel blijft.', 'The judgement stands.')"))
    return old_db


def test_awards_and_objections_are_migrated(old_awards):
    counts = content_migration.migrate(old_awards)
    with old_awards.connect() as conn:
        awards = dict(tuple(r) for r in conn.execute(text('SELECT id, explanation FROM quality_awards')))
        objection = tuple(conn.execute(text('SELECT ai_response, ai_response_lang FROM quality_objections')).one())
    assert awards == {1: 'A new argument.', 2: 'Nieuw argument.', 3: 'Neemt het serieus.', 4: 'Notitie.'}
    assert objection == ('The judgement stands.', 'en')
    rows = [r for r in _translations(old_awards) if r[0] != 'posts']
    assert rows == [
        ('quality_awards', 1, 'explanation', 'nl', '[nl] Een nieuw argument.', True, _h('A new argument.')),
        ('quality_awards', 3, 'explanation', 'en', 'Takes it seriously.', False, _h('Neemt het serieus.')),
        ('quality_objections', 1, 'ai_response', 'nl', '[nl] Het oordeel blijft.', True, _h('The judgement stands.')),
    ]
    assert counts['quality_awards.explanation filled'] == 4
    assert counts['quality_objections.ai_response filled'] == 1
    again = content_migration.migrate(old_awards)
    assert [r for r in _translations(old_awards) if r[0] != 'posts'] == rows
    assert again['quality_awards.explanation filled'] == again['quality_objections.ai_response filled'] == 0


def test_threads_are_migrated(old_db):
    with old_db.begin() as conn:
        conn.execute(text(
            'CREATE TABLE threads (id INTEGER PRIMARY KEY, source_lang VARCHAR(5), eyebrow VARCHAR(200), '
            'title_prefix VARCHAR(300), title_accent VARCHAR(200), title_suffix VARCHAR(50), '
            'eyebrow_nl VARCHAR(200), eyebrow_en VARCHAR(200), title_prefix_nl VARCHAR(300), '
            'title_accent_nl VARCHAR(200), title_suffix_nl VARCHAR(50), title_prefix_en VARCHAR(300), '
            'title_accent_en VARCHAR(200), title_suffix_en VARCHAR(50))'))
        conn.execute(text("INSERT INTO threads VALUES "
                          # 1: a demo thread, written in both languages
                          "(1, NULL, NULL, NULL, NULL, NULL, 'Het idee', 'The idea', 'Wat als', 'manipuleren', '?', "
                          "'What if', 'gaming it', '?'),"
                          # 2: a member's thread, the same title in both columns
                          "(2, NULL, NULL, NULL, NULL, NULL, 'Forum · gesprek', 'Forum · conversation', 'Mijn vraag', "
                          "NULL, '', 'Mijn vraag', NULL, ''),"
                          # 3: created after the migration (old columns empty)
                          "(3, 'en', 'Issue', 'A new one', NULL, '', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)"))
    counts = content_migration.migrate(old_db)
    with old_db.connect() as conn:
        threads = {r[0]: tuple(r[1:]) for r in conn.execute(text(
            'SELECT id, source_lang, eyebrow, title_prefix, title_accent, title_suffix FROM threads'))}
    assert threads == {1: ('nl', 'Het idee', 'Wat als', 'manipuleren', '?'),
                       2: ('nl', 'Forum · gesprek', 'Mijn vraag', None, ''),
                       3: ('en', 'Issue', 'A new one', None, '')}
    rows = [r[1:5] for r in _translations(old_db) if r[0] == 'threads']
    assert rows == [(1, 'eyebrow', 'en', 'The idea'), (1, 'title_accent', 'en', 'gaming it'),
                    (1, 'title_prefix', 'en', 'What if'), (2, 'eyebrow', 'en', 'Forum · conversation')]
    assert all(not r[5] for r in _translations(old_db) if r[0] == 'threads')  # handwritten
    assert counts['threads filled'] == 2
    again = content_migration.migrate(old_db)
    assert [r[1:5] for r in _translations(old_db) if r[0] == 'threads'] == rows and again['threads filled'] == 0


def test_issue_conversations_take_the_issue_language(old_db):
    """After-care: a conversation of an issue is in the issue's language; the
    handwritten translation into that language becomes the original."""
    with old_db.begin() as conn:
        conn.execute(text(
            'CREATE TABLE threads (id INTEGER PRIMARY KEY, source_lang VARCHAR(5), eyebrow VARCHAR(200), '
            'title_prefix VARCHAR(300), title_accent VARCHAR(200), title_suffix VARCHAR(50))'))
        conn.execute(text("INSERT INTO threads VALUES "
                          "(1, 'nl', 'Vraagstuk', 'Fair trade rules', NULL, ''),"     # English issue
                          "(2, 'nl', 'Vraagstuk', 'Eerlijke regels', NULL, ''),"      # Dutch issue
                          "(3, 'nl', 'Forum · gesprek', 'Editorial', NULL, ''),"      # no issue
                          "(4, 'en', 'Issue', 'Made after step 6', NULL, '')"))       # already right
        conn.execute(text('CREATE TABLE issues (id INTEGER PRIMARY KEY, lang VARCHAR(5), thread_id INTEGER)'))
        conn.execute(text("INSERT INTO issues VALUES (1, 'en', 1), (2, 'nl', 2), (3, 'en', 4)"))
        for tid, label in ((1, 'Issue'), (2, 'Issue'), (3, 'Forum · conversation')):
            conn.execute(text(
                "INSERT INTO content_translations (object_type, object_id, field, lang, text, is_machine, "
                "source_hash) VALUES ('threads', :i, 'eyebrow', 'en', :x, 0, :h)"),
                {'i': tid, 'x': label, 'h': _h({1: 'Vraagstuk', 2: 'Vraagstuk', 3: 'Forum · gesprek'}[tid])})
        conn.execute(text(
            "INSERT INTO content_translations (object_type, object_id, field, lang, text, is_machine, "
            "source_hash) VALUES ('threads', 4, 'eyebrow', 'nl', 'Vraagstuk', 0, :h)"), {'h': _h('Issue')})
    before = {r for r in _translations(old_db) if r[0] == 'threads' and r[1] != 1}

    counts = content_migration.migrate(old_db)
    with old_db.connect() as conn:
        threads = {r[0]: tuple(r[1:]) for r in conn.execute(text(
            'SELECT id, source_lang, eyebrow, title_prefix FROM threads'))}
    assert threads == {1: ('en', 'Issue', 'Fair trade rules'), 2: ('nl', 'Vraagstuk', 'Eerlijke regels'),
                       3: ('nl', 'Forum · gesprek', 'Editorial'), 4: ('en', 'Issue', 'Made after step 6')}
    assert [r for r in _translations(old_db) if r[0] == 'threads' and r[1] == 1] == [
        ('threads', 1, 'eyebrow', 'nl', 'Vraagstuk', False, _h('Issue'))]
    assert {r for r in _translations(old_db) if r[0] == 'threads' and r[1] != 1} == before
    assert counts['threads.source_lang from issue'] == 1

    after = _translations(old_db)
    again = content_migration.migrate(old_db)
    assert _translations(old_db) == after and again['threads.source_lang from issue'] == 0


def test_an_issue_conversation_reads_right_after_the_migration(app, db, make_user):
    """On the app's own database: an English issue whose conversation still
    says nl shows the right eyebrow and title to both readers afterwards, and
    its opening post keeps its own language (the 'Original in' banner and
    the translate button follow the post)."""
    from app import content_i18n
    from app.models import Issue, Post, Thread
    user = make_user()
    thread = Thread(source_lang='nl', eyebrow='Vraagstuk', title_prefix='Fair trade rules', title_suffix='')
    db.session.add(thread)
    db.session.flush()
    content_i18n.set_translation(thread, 'eyebrow', 'en', 'Issue')
    issue = Issue(title='Fair trade rules', lang='en', thread_id=thread.id)
    op = Post(thread_id=thread.id, user_id=user.id, author_name='', author_username=user.username,
              body='Why fair trade?', source_lang='en', is_op=True)
    db.session.add_all([issue, op])
    db.session.commit()
    try:
        counts = content_migration.migrate(db.engine)
        assert counts['threads.source_lang from issue'] == 1
        db.session.expire_all()
        content_i18n.clear_cache()
        assert thread.source_lang == 'en'
        assert (thread.eyebrow_in('en'), thread.eyebrow_in('nl')) == ('Issue', 'Vraagstuk')
        assert thread.title_text('en') == thread.title_text('nl') == 'Fair trade rules'
        assert content_i18n.reader_view(op, 'body', 'en') == ('Why fair trade?', False)
        assert content_i18n.source_lang(op) == 'en'
    finally:
        content_i18n.invalidate(thread)
        db.session.delete(issue)
        db.session.delete(op)
        db.session.delete(thread)
        db.session.commit()
