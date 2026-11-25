"""Orphan translations (content_i18n.orphans / remove_orphans): rows in
content_translations whose object no longer exists. Removed at every start
(_migrate_columns), logged per object type, counted on /admin/vertalingen.
"""
import logging

from sqlalchemy import func

from app import _migrate_columns, content_i18n
from app.models import ContentTranslation, Post, Thread
from tests.test_issue_polish import _client, _req, make_user  # noqa: F401  (fixture)
from tests.test_translation_backlog import _cell


def _row(kind, oid, text):
    return ContentTranslation(object_type=kind, object_id=oid, field='body', lang='en',
                              text=text, source_hash='x', is_machine=True)


def _world(db, make_user):
    """A living post with a translation, and orphan rows for a post and a
    thread that do not exist."""
    author = make_user()
    thread = Thread.query.filter_by(is_demo=True).order_by(Thread.id).first()
    post = Post(thread_id=thread.id, user_id=author.id, author_name='', author_username=author.username,
                body='Blijft staan.', source_lang='nl')
    db.session.add(post)
    db.session.commit()
    gone_post = (db.session.query(func.max(Post.id)).scalar() or 0) + 1000
    gone_thread = (db.session.query(func.max(Thread.id)).scalar() or 0) + 1000
    db.session.add_all([_row('posts', post.id, 'Stays.'), _row('posts', gone_post, 'Orphan post.'),
                        _row('threads', gone_thread, 'Orphan thread.')])
    db.session.commit()
    return post, gone_post, gone_thread


def _count(kind, oid):
    return ContentTranslation.query.filter_by(object_type=kind, object_id=oid).count()


def _cleanup(db, post):
    content_i18n.invalidate(post)
    db.session.delete(post)
    db.session.commit()


def test_orphans_go_translations_of_existing_objects_stay(app, db, make_user, caplog):
    post, gone_post, gone_thread = _world(db, make_user)
    try:
        assert content_i18n.orphans()['posts'] >= 1 and content_i18n.orphans()['threads'] >= 1
        with caplog.at_level(logging.INFO, logger='app.content_i18n'):
            removed = content_i18n.remove_orphans()
        assert removed['posts'] >= 1 and removed['threads'] >= 1
        assert _count('posts', gone_post) == 0 and _count('threads', gone_thread) == 0
        assert _count('posts', post.id) == 1
        lines = [r.getMessage() for r in caplog.records if r.name == 'app.content_i18n']
        assert f"Orphan translations removed: {removed['posts']} (posts)" in lines
        assert f"Orphan translations removed: {removed['threads']} (threads)" in lines
        # A second run removes nothing and logs nothing.
        caplog.clear()
        with caplog.at_level(logging.INFO, logger='app.content_i18n'):
            assert content_i18n.remove_orphans() == {}
        assert not [r for r in caplog.records if r.name == 'app.content_i18n']
        assert content_i18n.orphans() == {} and _count('posts', post.id) == 1
    finally:
        _cleanup(db, post)


def test_the_start_removes_them_and_a_second_start_nothing(app, db, make_user, caplog):
    post, gone_post, gone_thread = _world(db, make_user)
    try:
        with caplog.at_level(logging.INFO, logger='app.content_i18n'):
            _migrate_columns()
        assert any(r.getMessage().startswith('Orphan translations removed: ') for r in caplog.records)
        assert _count('posts', gone_post) == 0 and _count('threads', gone_thread) == 0
        assert _count('posts', post.id) == 1
        caplog.clear()
        with caplog.at_level(logging.INFO, logger='app.content_i18n'):
            _migrate_columns()
        assert not [r for r in caplog.records if r.getMessage().startswith('Orphan translations')]
        assert _count('posts', post.id) == 1
    finally:
        _cleanup(db, post)


def test_the_admin_page_counts_them(app, db, make_user):
    admin = make_user(admin=True)
    post, _gone_post, _gone_thread = _world(db, make_user)
    try:
        page = _req(_client(app, admin), 'GET', '/admin/vertalingen').get_data(as_text=True)
        assert _cell(page, 'orphans') == sum(content_i18n.orphans().values()) >= 2
        content_i18n.remove_orphans()
        page = _req(_client(app, admin), 'GET', '/admin/vertalingen').get_data(as_text=True)
        assert _cell(page, 'orphans') == 0
    finally:
        _cleanup(db, post)
