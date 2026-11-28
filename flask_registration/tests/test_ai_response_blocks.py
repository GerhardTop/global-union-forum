"""
AI answers may start with a thinking block (claude-sonnet-5-5 does so at
times): the app reads the text of every block of type 'text'
(quality.response_text), never content[0]. For translating (short and
long), the quality judgement and the choice of background figures; an
answer with only a thinking block is refused and nothing is saved.

Fake clients; nothing goes to the network. Shared conftest app.
"""
import json
import uuid
from types import SimpleNamespace

import pytest

from app import background_figures as bf, content_i18n, db as _db, issues, quality
from app.models import ContentTranslation, Post, QualityAward
from tests.test_background_figures import (FakeClient as FiguresClient, FakeHttp, _answer, _chart, _charts,  # noqa: F401
                                           _issue, _run_for, made, started, watched)
from tests.test_delete_issue import _cleanup
from tests.test_forum_quality import _verdict
from tests.test_issue_polish import make_user  # noqa: F401  (fixture)

THINKING = SimpleNamespace(type='thinking', thinking='Let me think about this first.', signature='sig')
REDACTED = SimpleNamespace(type='redacted_thinking', data='xyz')


def _answer_with(text, thinking=True):
    blocks = [THINKING, REDACTED] if thinking else []
    if text is not None:
        blocks.append(SimpleNamespace(type='text', text=text))
    return SimpleNamespace(stop_reason='end_turn', content=blocks)


class Translator:
    """Thinks first, then translates by prefixing 'EN: '; only_thinking: no text block at all."""

    def __init__(self, only_thinking=False):
        self.only_thinking, self.messages = only_thinking, self

    def create(self, **kw):
        source = kw['messages'][0]['content'].split('<source>\n', 1)[1].rsplit('\n</source>', 1)[0]
        return _answer_with(None if self.only_thinking else f'EN: {source}')


def test_response_text_joins_the_text_blocks_only():
    response = SimpleNamespace(content=[THINKING, SimpleNamespace(type='text', text='Een '), REDACTED,
                                        SimpleNamespace(type='text', text='tekst.')])
    assert quality.response_text(response) == 'Een tekst.'
    assert quality.response_text(_answer_with(None)) == ''
    assert quality.response_text(SimpleNamespace(content=[])) == ''


@pytest.mark.parametrize('kind', ['short', 'text'])
def test_translating_after_a_thinking_block(kind):
    assert quality.translate_text('Tariefvoet voor alle producten', 'en', client=Translator(), kind=kind) == \
        'EN: Tariefvoet voor alle producten'


@pytest.mark.parametrize('kind', ['short', 'text'])
def test_only_a_thinking_block_is_refused(kind):
    with pytest.raises(quality.TranslationRejected):
        quality.translate_text('Tariefvoet', 'en', client=Translator(only_thinking=True), kind=kind)


def test_only_a_thinking_block_saves_nothing(app, db, make_user):
    issue = issues.create({'title': f'Vraag {uuid.uuid4().hex[:6]}', 'intro': 'Uitleg.', 'kind': 'online'},
                          make_user(), lang='nl')
    db.session.commit()
    try:
        with pytest.raises(quality.TranslationRejected):
            content_i18n.translate(issue, 'title', 'en', client=Translator(only_thinking=True))
        db.session.rollback()
        assert ContentTranslation.query.filter_by(object_type='issues', object_id=issue.id, lang='en').count() == 0
        assert content_i18n.translate(issue, 'title', 'en', client=Translator()) == f'EN: {issue.title}'
    finally:
        _cleanup(issue.id)


# ── The quality judgement ────────────────────────────────────────────────

class Judge:
    """Translations and the verdict, each after a thinking block."""

    def __init__(self, only_thinking=False):
        self.only_thinking, self.messages, self.judged = only_thinking, self, 0

    def create(self, **kw):
        if 'output_config' not in kw:
            source = kw['messages'][0]['content'].split('<source>\n', 1)[1].rsplit('\n</source>', 1)[0]
            return _answer_with(f'EN: {source}')
        self.judged += 1
        return _answer_with(None if self.only_thinking else json.dumps(_verdict()))


@pytest.mark.parametrize('only_thinking', [False, True])
def test_the_judgement_after_a_thinking_block(app, db, make_user, monkeypatch, only_thinking):
    author = make_user()
    issue = issues.create({'title': f'Vraag {uuid.uuid4().hex[:6]}', 'intro': 'Uitleg.', 'kind': 'online'},
                          author, lang='nl')
    db.session.commit()
    post = Post(thread_id=issue.thread_id, user_id=author.id, author_name='', author_username=author.username,
                body=f'Een nieuw argument {uuid.uuid4().hex[:6]}.', source_lang='nl')
    db.session.add(post)
    db.session.commit()
    judge = Judge(only_thinking)
    monkeypatch.setattr(quality, '_client', lambda: judge)
    try:
        award = quality.judge_post(post.id)
        assert judge.judged == 1
        if only_thinking:
            assert award is None and QualityAward.query.filter_by(post_id=post.id).count() == 0
        else:
            assert award is not None and award.status == 'toegekend' and award.criterion == 'K1'
    finally:
        _db.session.rollback()
        _cleanup(issue.id)


# ── The choice of background figures ─────────────────────────────────────

class ThinkingFiguresClient(FiguresClient):
    """The fake of the background figure tests, with a thinking block first."""

    def create(self, **kw):
        response = super().create(**kw)
        return SimpleNamespace(content=[THINKING] + [SimpleNamespace(type='text', text=b.text)
                                                     for b in response.content])


def test_the_choice_of_background_figures_after_a_thinking_block(app, db, make_user, made, started, watched):
    user = make_user()
    issue = _issue(db, user, made)
    client = ThinkingFiguresClient(_answer(_chart()))
    assert bf.run(_run_for(issue).id, client=client, http=FakeHttp()) == 'done'
    assert len(_charts(issue)) == 1


def test_the_figures_ask_reads_text_blocks_only():
    class Only:
        messages = None

        def create(self, **kw):
            return _answer_with('{"terms": []}')
    client = Only()
    client.messages = client
    assert bf._ask(client, 'model', 'system', 'user', 10) == '{"terms": []}'
