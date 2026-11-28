"""
Translations without thinking (quality.TRANSLATION_THINKING): the short-field
model (claude-sonnet-5-5) thinks by default and the thinking counted within
max_tokens, so a hard title could end with stop_reason max_tokens and only a
thinking block, or a cut-off text. The call now turns thinking off for that
model; an answer cut off by max_tokens is always refused, with the stop
reason and the kinds of blocks in the log. A fake client; no network.
"""
import logging
from types import SimpleNamespace

import pytest

from app import quality

THINKING = SimpleNamespace(type='thinking', thinking='', signature='sig')


class Fake:
    def __init__(self, response):
        self.response, self.calls, self.messages = response, [], self

    def create(self, **kw):
        self.calls.append(kw)
        return self.response


def _ok(text='Zollsatz'):
    return SimpleNamespace(stop_reason='end_turn', content=[SimpleNamespace(type='text', text=text)])


def test_the_short_call_turns_thinking_off():
    client = Fake(_ok())
    quality.translate_text('Tariefvoet', 'de', client=client, kind='short')
    (call,) = client.calls
    assert call['model'] == quality.SHORT_TRANSLATION_MODEL == 'claude-sonnet-5-5'
    assert call['thinking'] == {'type': 'between_tools'} and call['max_tokens'] == 200


def test_the_long_call_sends_no_thinking_setting():
    client = Fake(_ok('Ein langer Text.'))
    quality.translate_text('Een lange tekst.', 'de', client=client, kind='text')
    (call,) = client.calls
    assert call['model'] == quality.TRANSLATION_MODEL and 'thinking' not in call


def test_only_thinking_with_max_tokens_is_refused_and_logged(caplog):
    client = Fake(SimpleNamespace(stop_reason='max_tokens', content=[THINKING]))
    with caplog.at_level(logging.WARNING, logger='app.quality'):
        with pytest.raises(quality.TranslationRejected, match='max_tokens'):
            quality.translate_text('Aandeel HNW in % van BNI', 'fr', client=client, kind='short')
    line = '\n'.join(r.getMessage() for r in caplog.records)
    assert "stop_reason max_tokens" in line and "blocks ['thinking']" in line
    assert 'Aandeel' not in line          # no content in the log


def test_a_cut_off_text_is_refused_too(caplog):
    client = Fake(SimpleNamespace(stop_reason='max_tokens',
                                  content=[THINKING, SimpleNamespace(type='text', text='Taux tarifaire pour t')]))
    with caplog.at_level(logging.WARNING, logger='app.quality'):
        with pytest.raises(quality.TranslationRejected, match='cut off'):
            quality.translate_text('Tariefvoet voor alle producten', 'fr', client=client, kind='short')
    assert "blocks ['thinking', 'text']" in '\n'.join(r.getMessage() for r in caplog.records)
