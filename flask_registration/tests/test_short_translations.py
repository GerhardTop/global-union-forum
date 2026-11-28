"""
The translation route with a stronger model for short fields (titles,
labels, names, units: quality.SHORT_TRANSLATION_MODEL) and the command
`flask vertalingen-kort-opnieuw` that makes the existing short machine
translations again. A fake client; nothing goes to the network.

Shared conftest app; every test makes its own post with unique texts and
removes it (with its translations) afterwards.
"""
import uuid
from types import SimpleNamespace

import pytest

from app import content_i18n, db as _db, quality
from app.models import ContentTranslation, Post, Thread
from tests.test_issue_polish import make_user  # noqa: F401  (fixture)


class Recorder:
    """A fake Anthropic client: records the model per call and answers with
    `answers[source]`, else 'NEW <source>'."""

    def __init__(self, answers=None):
        self.models, self.answers, self.messages = [], answers or {}, self

    def create(self, **kw):
        self.models.append(kw['model'])
        source = kw['messages'][0]['content'].split('<source>\n', 1)[1].rsplit('\n</source>', 1)[0]
        return SimpleNamespace(content=[SimpleNamespace(text=self.answers.get(source, f'NEW {source}'))])


def test_short_uses_the_stronger_model_and_text_the_usual_one():
    client = Recorder()
    quality.translate_text('Tariefvoet voor alle producten', 'en', client=client, kind='short')
    quality.translate_text('Een langere bijdrage over tarieven.', 'en', client=client, kind='text')
    quality.translate_text('Onbekende soort', 'en', client=client, kind='raar')
    assert client.models == [quality.SHORT_TRANSLATION_MODEL, quality.TRANSLATION_MODEL, quality.TRANSLATION_MODEL]
    assert quality.SHORT_TRANSLATION_MODEL == 'claude-sonnet-5-5'
    assert quality.SHORT_TRANSLATION_MODEL != quality.TRANSLATION_MODEL


@pytest.fixture
def post(app, db, make_user):
    """A fact with a short title and a long body, and four stored translations:
    the title machine-translated into de and fr, handwritten into es, and
    the body (a long field) machine-translated into de."""
    author = make_user()
    thread = Thread.query.filter_by(is_demo=True).order_by(Thread.id).first()
    tag = uuid.uuid4().hex[:6]
    p = Post(thread_id=thread.id, user_id=author.id, author_name='', author_username=author.username,
             body=f'Een lange tekst {tag}.', fact_title=f'Tarief {tag}', kind='feit', source_lang='nl')
    db.session.add(p)
    db.session.commit()
    content_i18n.set_translation(p, 'fact_title', 'de', 'Tarieefvoet alt', is_machine=True)
    content_i18n.set_translation(p, 'fact_title', 'fr', 'Taux ancien', is_machine=True)
    content_i18n.set_translation(p, 'fact_title', 'es', 'Hecho a mano', is_machine=False)
    content_i18n.set_translation(p, 'body', 'de', 'Langer Text alt.', is_machine=True)
    db.session.commit()
    yield p
    content_i18n.invalidate(p)
    db.session.delete(p)
    db.session.commit()


def _mine(post):
    _db.session.expire_all()
    return {(r.field, r.lang): (r.text, r.is_machine)
            for r in ContentTranslation.query.filter_by(object_type='posts', object_id=post.id)}


def test_without_uitvoeren_nothing_changes(app, post, monkeypatch):
    client = Recorder()
    monkeypatch.setattr(quality, '_client', lambda: client)
    before = _mine(post)
    result = app.test_cli_runner().invoke(args=['vertalingen-kort-opnieuw'])
    assert result.exit_code == 0, result.output
    assert 'Machine translations of short fields:' in result.output and 'Nothing changed.' in result.output
    assert client.models == [] and _mine(post) == before
    counts = content_i18n.redo_short()['counts']
    assert counts.get('de', 0) >= 1 and counts.get('fr', 0) >= 1


def test_uitvoeren_replaces_only_short_machine_translations(app, post, monkeypatch):
    # fr gets an answer the checks refuse (a heading): the old text stays.
    client = Recorder({post.fact_title: 'Tariefvoet'})

    def create(**kw):
        client.models.append(kw['model'])
        source = kw['messages'][0]['content'].split('<source>\n', 1)[1].rsplit('\n</source>', 1)[0]
        refused = ' into French.' in kw['system'] and source == post.fact_title
        text = '# Taux' if refused else client.answers.get(source, f'NEW {source}')
        return SimpleNamespace(content=[SimpleNamespace(text=text)])
    client.create = create
    monkeypatch.setattr(quality, '_client', lambda: client)
    result = app.test_cli_runner().invoke(args=['vertalingen-kort-opnieuw', '--uitvoeren'])
    assert result.exit_code == 0, result.output
    mine = _mine(post)
    assert mine[('fact_title', 'de')] == ('Tariefvoet', True)                # replaced
    assert mine[('fact_title', 'fr')] == ('Taux ancien', True)               # refused: old text kept
    assert mine[('fact_title', 'es')] == ('Hecho a mano', False)             # handwritten: never touched
    assert mine[('body', 'de')] == ('Langer Text alt.', True)                # a long field: not touched
    assert set(client.models) == {quality.SHORT_TRANSLATION_MODEL}
    assert "kept posts" in result.output and 'Replaced:' in result.output and 'failed (old text kept):' in result.output
    assert "'Tarieefvoet alt' -> 'Tariefvoet'" in result.output
    # The replaced translation counts as current for the original.
    assert content_i18n.text_for(post, 'fact_title', 'de') == 'Tariefvoet'


def test_the_same_answer_counts_as_the_same(app, post, monkeypatch):
    client = Recorder({post.fact_title: 'Tarieefvoet alt'})
    monkeypatch.setattr(quality, '_client', lambda: client)
    result = content_i18n.redo_short(execute=True, client=client, echo=lambda line: None)
    assert result['same'] >= 1
    assert _mine(post)[('fact_title', 'de')] == ('Tarieefvoet alt', True)


def test_commits_per_fifty(app, post, monkeypatch):
    commits = []
    real = _db.session.commit
    monkeypatch.setattr(content_i18n, 'REDO_BATCH', 1)
    monkeypatch.setattr(_db.session, 'commit', lambda: (commits.append(1), real())[1])
    content_i18n.redo_short(execute=True, client=Recorder(), echo=lambda line: None)
    rows = len(content_i18n.short_machine_rows())
    assert len(commits) >= rows   # one per row with a batch of one, and one at the end
