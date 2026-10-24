"""
Minder database-rondes per pagina (elke query kost in productie een
netwerkronde naar Neon) en indexen voor groei.

Dekt: het forumoverzicht doet een vast aantal query's, ongeacht het aantal
gesprekken (geen N+1); de gesprekspagina haalt berichten en
handhavingsgegevens één keer op; geen seed-query per verzoek; de indexen
via de idempotente migratie; de gunicorn-instellingen.

Gedeelde conftest-app (CSRF uit).
"""
import os
import re
import uuid
from contextlib import contextmanager

import pytest
from flask import g
from flask_babel import refresh
from sqlalchemy import event, inspect as sa_inspect, text

from app import db as _db
from app.models import Post, Thread


@contextmanager
def _count_queries():
    statements = []

    def _listener(conn, cursor, statement, params, context, executemany):
        statements.append(re.sub(r'\s+', ' ', statement))

    engine = _db.engine
    event.listen(engine, 'before_cursor_execute', _listener)
    try:
        yield statements
    finally:
        event.remove(engine, 'before_cursor_execute', _listener)


def _get(app, path):
    client = app.test_client()
    with client.session_transaction() as s:
        s['lang'] = 'nl'
    g.pop('_login_user', None)
    refresh()
    return client.get(path)


@pytest.fixture
def threads(db):
    made = []

    def _make(n_replies=2):
        t = Thread(eyebrow_nl='Q', eyebrow_en='Q', title_prefix_nl=f'Query {uuid.uuid4().hex[:6]}',
                   title_prefix_en='Query')
        db.session.add(t)
        db.session.flush()
        op = Post(thread_id=t.id, author_name='', author_username='op', body='Opening', is_op=True,
                  source_lang='nl')
        db.session.add(op)
        db.session.flush()
        for i in range(n_replies):
            db.session.add(Post(thread_id=t.id, parent_id=op.id, author_name='', author_username='r',
                                body=f'Reactie {i}', source_lang='nl'))
        db.session.commit()
        made.append(t.id)
        return t

    yield _make
    for tid in made:
        for p in Post.query.filter_by(thread_id=tid).order_by(Post.id.desc()):
            db.session.delete(p)
        db.session.flush()
        db.session.delete(db.session.get(Thread, tid))
    db.session.commit()


class TestForumOverview:
    def test_fixed_number_of_queries_regardless_of_thread_count(self, app, threads):
        threads()
        with _count_queries() as few:
            html_few = _get(app, '/forum').get_data(as_text=True)
        for _ in range(5):
            threads()
        with _count_queries() as many:
            html_many = _get(app, '/forum').get_data(as_text=True)
        assert len(many) == len(few), (len(few), len(many))
        assert len(many) <= 3
        # De berichten van alle gesprekken in één query.
        post_queries = [q for q in many if 'FROM posts' in q and 'GROUP BY' not in q]
        assert len(post_queries) == 1
        assert html_many.count('gu-threadlist__row') > html_few.count('gu-threadlist__row')

    def test_counts_and_last_activity_still_shown(self, app, threads):
        t = threads(n_replies=3)
        html = _get(app, '/forum').get_data(as_text=True)
        row = html[html.index(f'/forum/{t.id}"'):]
        row = row[:row.index('</li>')]
        assert re.search(r'4\s*reacties', row) and 'laatst' in row and 'zojuist' in row


class TestThreadPage:
    def test_posts_and_conduct_fetched_once(self, app, threads):
        t = threads(n_replies=4)
        with _count_queries() as queries:
            resp = _get(app, f'/forum/{t.id}')
        assert resp.status_code == 200 and 'Reactie 3' in resp.get_data(as_text=True)
        assert sum(1 for q in queries if 'FROM posts' in q) == 1, queries
        assert sum(1 for q in queries if 'FROM conduct_cases' in q) == 1, queries
        assert len(queries) <= 5, queries

    def test_branch_view_uses_the_same_posts(self, app, threads):
        t = threads(n_replies=1)
        reply = Post.query.filter_by(thread_id=t.id, is_op=False).one()
        with _count_queries() as queries:
            resp = _get(app, f'/forum/{t.id}/tak/{reply.id}')
        assert resp.status_code == 200
        assert sum(1 for q in queries if 'FROM posts' in q) == 1
        assert _get(app, f'/forum/{t.id}/tak/999999').status_code == 404

    def test_no_seed_query_per_request(self, app, threads):
        t = threads()
        for path in ('/forum', f'/forum/{t.id}'):
            with _count_queries() as queries:
                _get(app, path)
            assert not any('WHERE threads.is_demo' in q for q in queries), path


class TestIndexes:
    INDEXES = (('posts', 'thread_id'), ('posts', 'parent_id'), ('posts', 'user_id'),
               ('quality_reports', 'award_id'), ('conduct_objections', 'case_id'),
               ('quality_promotions', 'user_id'))

    def _indexed(self):
        insp = sa_inspect(_db.engine)
        return {(t, c) for t, c in self.INDEXES
                if (c,) in {tuple(ix['column_names']) for ix in insp.get_indexes(t)}}

    def test_migration_adds_missing_indexes_idempotently(self, app, db):
        from app import _migrate_columns
        assert self._indexed() == set(self.INDEXES)  # verse database: via index=True
        db.session.close()
        with db.engine.connect() as conn:
            for table, column in self.INDEXES:
                conn.execute(text(f'DROP INDEX ix_{table}_{column}'))
            conn.commit()
        db.engine.dispose()
        assert self._indexed() == set()
        for _ in range(2):  # twee keer opstarten
            _migrate_columns()
            assert self._indexed() == set(self.INDEXES)


class TestGunicornConfig:
    def test_gthread_with_workers_threads_and_preload(self, monkeypatch):
        import importlib.util
        for var in ('WEB_CONCURRENCY', 'GUNICORN_THREADS'):
            monkeypatch.delenv(var, raising=False)
        path = os.path.join(os.path.dirname(__file__), '..', 'gunicorn.conf.py')
        spec = importlib.util.spec_from_file_location('gunicorn_conf', path)
        conf = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(conf)
        assert (conf.worker_class, conf.workers, conf.threads, conf.preload_app) == ('gthread', 2, 4, True)
        assert callable(conf.post_fork)
        procfile = open(os.path.join(os.path.dirname(__file__), '..', 'Procfile')).read()
        assert 'gunicorn -c gunicorn.conf.py run:app' in procfile
