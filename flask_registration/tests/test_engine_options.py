"""The Neon pool options (pool_pre_ping, pool_recycle) apply to Postgres only,
the database of production on Render; SQLite (the tests) gets none. Before,
they applied to every database: on an in-memory SQLite database a replaced
connection is an empty one ("no such table" halfway through create_app)."""
import pytest

import config


@pytest.mark.parametrize('uri', [
    'postgresql://user:secret@ep-example.eu-central-1.aws.neon.tech/guf?sslmode=require',
    'postgresql+psycopg2://user:secret@localhost:5432/guf',
    'postgres://user:secret@localhost/guf',
])
def test_postgres_keeps_the_neon_options(uri):
    assert config.engine_options(uri) == {'pool_pre_ping': True, 'pool_recycle': 300}


@pytest.mark.parametrize('uri', ['sqlite:///:memory:', 'sqlite:////tmp/app.sqlite3', 'mysql+pymysql://root@localhost/x',
                                 '', None])
def test_anything_else_gets_none(uri):
    assert config.engine_options(uri) == {}


def test_a_render_url_becomes_postgresql_and_keeps_the_options(monkeypatch):
    # Render and Neon may hand out postgres://; _build_db_url makes it postgresql://.
    monkeypatch.setenv('DATABASE_URL', 'postgres://user:secret@ep-example.neon.tech/guf')
    uri = config._build_db_url()
    assert uri.startswith('postgresql://') and config.engine_options(uri) == config.NEON_ENGINE_OPTIONS


def test_the_test_app_has_no_pool_options(app):
    from app import db
    assert app.config['SQLALCHEMY_ENGINE_OPTIONS'] == {}
    assert db.engine.pool._pre_ping is False
