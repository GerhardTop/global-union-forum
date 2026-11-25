"""A temporary SQLite database file for tests that build an app of their own.

Not 'sqlite:///:memory:': an in-memory database lives on one connection only
(StaticPool), so a new connection (a reconnect, a pool that replaces its
connection) sees an empty database. That made a test fail now and then with
"no such table: posts" halfway through create_app (seed 1947323433). A file
works like production: every connection sees the same data.
"""
import shutil
import tempfile


def temp_sqlite_uri(request):
    """The URI of a new database file in its own temporary folder; the folder
    is removed when the test that asked for it is done (after the fixture's
    own teardown, which drops the tables)."""
    folder = tempfile.mkdtemp(prefix='guf-test-app-')
    request.addfinalizer(lambda: shutil.rmtree(folder, ignore_errors=True))
    return f'sqlite:///{folder}/app.sqlite3'
