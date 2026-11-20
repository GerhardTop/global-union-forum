"""Multilingual phase 0 step 6: the data migration from the old per-language
columns (app/content_migration.py), on a separate database that still has
those columns. Idempotent: a second run adds and overwrites nothing.
"""
import hashlib

import pytest
from sqlalchemy import create_engine, text

from app import content_migration
from app.models import ContentTranslation


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
