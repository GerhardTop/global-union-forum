"""Multilingual phase 0 step 6a: translations of content (app/content_i18n.py).

One original per text, in its source language; translations are a cache per
language in content_translations, tied to the original by source_hash. The
AI client is a fake: no real API calls.
"""
from types import SimpleNamespace

import pytest
import sqlalchemy

import config
from app import content_i18n, db, i18n
from app.models import ContentTranslation, Post, Thread
from tests.test_issue_polish import make_user  # noqa: F401  (fixture)


class FakeClient:
    """Stands in for anthropic.Anthropic: records the prompts, 'translates' by
    prefixing the language named in the prompt."""

    def __init__(self):
        self.calls = []
        self.messages = self

    def create(self, **kw):
        prompt = kw['messages'][0]['content']
        self.calls.append(prompt)
        language = prompt.split(' to ', 1)[1].split('.', 1)[0]
        source = prompt.split('\n\n', 1)[1]
        return SimpleNamespace(content=[SimpleNamespace(text=f'[{language}] {source}')])


@pytest.fixture
def german(monkeypatch):
    monkeypatch.setattr(config, 'ENABLED_LANGUAGES', ['nl', 'en', 'de'])


@pytest.fixture
def post(app, db, make_user):
    author = make_user()
    thread = Thread.query.filter_by(is_demo=True).order_by(Thread.id).first()
    p = Post(thread_id=thread.id, user_id=author.id, author_name='', author_username=author.username,
             body='Een korte bijdrage.', source_lang='nl')
    db.session.add(p)
    db.session.commit()
    yield p
    content_i18n.invalidate(p)
    db.session.delete(p)
    db.session.commit()


def test_the_original_in_its_own_language(post):
    assert content_i18n.source_lang(post) == 'nl'
    assert content_i18n.text_for(post, 'body', 'nl') == 'Een korte bijdrage.'
    assert content_i18n.text_for(post, 'body', 'en') is None          # not translated yet
    assert content_i18n.display(post, 'body', 'en') == 'Een korte bijdrage.'  # falls back to the original


def test_translating_to_german(app, post, german):
    assert i18n.is_enabled('de')
    client = FakeClient()
    text = content_i18n.translate(post, 'body', 'de', client=client)
    assert text == '[German] Een korte bijdrage.'
    assert len(client.calls) == 1 and 'to German.' in client.calls[0]
    row = ContentTranslation.query.filter_by(object_type='posts', object_id=post.id, field='body', lang='de').one()
    assert row.is_machine is True and row.text == text
    assert row.source_hash == content_i18n.source_hash('Een korte bijdrage.')


def test_a_cached_translation_needs_no_second_ai_call(app, post):
    client = FakeClient()
    first = content_i18n.translate(post, 'body', 'en', client=client)
    db.session.expire_all()
    from flask import g
    g.pop('_content_translations', None)  # a new request
    again = content_i18n.translate(db.session.get(Post, post.id), 'body', 'en', client=client)
    assert first == again == '[English] Een korte bijdrage.'
    assert len(client.calls) == 1


def test_no_ai_call_for_the_source_language(post):
    client = FakeClient()
    assert content_i18n.translate(post, 'body', 'nl', client=client) == 'Een korte bijdrage.'
    assert client.calls == []


def test_an_edit_invalidates_the_translations(app, post):
    client = FakeClient()
    content_i18n.translate(post, 'body', 'en', client=client)
    post.body = 'Een bewerkte bijdrage.'
    # Even before invalidate(), a translation of the old text is not shown.
    assert content_i18n.text_for(post, 'body', 'en') is None
    content_i18n.invalidate(post, 'body')
    db.session.commit()
    assert ContentTranslation.query.filter_by(object_type='posts', object_id=post.id).count() == 0
    assert content_i18n.translate(post, 'body', 'en', client=client) == '[English] Een bewerkte bijdrage.'
    assert len(client.calls) == 2


def test_one_translation_per_object_field_and_language(app, post):
    content_i18n.set_translation(post, 'body', 'en', 'A short contribution.')
    db.session.commit()
    content_i18n.set_translation(post, 'body', 'en', 'A brief contribution.', is_machine=True)
    db.session.commit()
    rows = ContentTranslation.query.filter_by(object_type='posts', object_id=post.id, field='body').all()
    assert [(r.lang, r.text, r.is_machine) for r in rows] == [('en', 'A brief contribution.', True)]
    # The database itself refuses a second row for the same language.
    db.session.add(ContentTranslation(object_type='posts', object_id=post.id, field='body', lang='en',
                                      text='x', source_hash='x'))
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_handwritten_translations(app, post):
    content_i18n.set_translation(post, 'body', 'en', 'A short contribution.')
    db.session.commit()
    assert content_i18n.handwritten_for(post, 'body', 'en') == 'A short contribution.'
    assert content_i18n.has_handwritten(post, 'body')
    content_i18n.set_translation(post, 'body', 'en', 'A short contribution.', is_machine=True)
    assert content_i18n.handwritten_for(post, 'body', 'en') is None
    assert content_i18n.text_for(post, 'body', 'en') == 'A short contribution.'


def test_preload_fetches_a_page_in_one_query(app, post):
    content_i18n.set_translation(post, 'body', 'en', 'A short contribution.')
    db.session.commit()
    from flask import g
    g.pop('_content_translations', None)
    content_i18n.preload([post], 'body')
    queries = []

    def record(conn, cursor, statement, *args):
        queries.append(statement)
    sqlalchemy.event.listen(db.engine, 'before_cursor_execute', record)
    try:
        assert content_i18n.text_for(post, 'body', 'en') == 'A short contribution.'
    finally:
        sqlalchemy.event.remove(db.engine, 'before_cursor_execute', record)
    assert not [q for q in queries if 'content_translations' in q]


def test_deleting_an_object_deletes_its_translations(app, db, make_user):
    author = make_user()
    thread = Thread.query.filter_by(is_demo=True).order_by(Thread.id).first()
    p = Post(thread_id=thread.id, user_id=author.id, author_name='', author_username=author.username,
             body='Weg ermee.', source_lang='nl')
    db.session.add(p)
    db.session.commit()
    content_i18n.set_translation(p, 'body', 'en', 'Away with it.')
    db.session.commit()
    pid = p.id
    db.session.delete(p)
    db.session.commit()
    assert ContentTranslation.query.filter_by(object_type='posts', object_id=pid).count() == 0


def test_translate_text_takes_a_language_code():
    from app import quality
    client = FakeClient()
    assert quality.translate_text('Hallo.', 'fr', client=client) == '[French] Hallo.'
    with pytest.raises(ValueError):
        quality.translate_text('Hallo.', 'xx', client=client)
