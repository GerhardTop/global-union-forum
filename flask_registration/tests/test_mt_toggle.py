"""Multilingual phase 2b: the machine-translation label swaps between the
translation and the original in a real browser (Playwright for Node, the demo
server of tests/browser/, with fake machine translations). Skipped without
node and the playwright package, like tests/test_mobile_overflow.py.
"""
import json
import os
import subprocess

from tests.test_mobile_overflow import HERE, demo_server  # noqa: F401  (fixture)


def test_the_label_swaps_between_translation_and_original(demo_server):  # noqa: F811
    base, node_path = demo_server
    run = subprocess.run(['node', os.path.join(HERE, 'browser', 'mt_toggle.js'), base],
                         env={**os.environ, 'NODE_PATH': node_path}, capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)

    shown, original, back = out['title']
    assert shown['visibleLang'] == 'en' and shown['visibleText'].startswith('[EN] ')
    assert shown['label'] == 'Machine-translated from Dutch'
    assert (shown['button'], shown['pressed']) == ('Show original', 'false')
    assert original['visibleLang'] == 'nl' and not original['visibleText'].startswith('[EN]')
    assert original['label'] == 'Original in Dutch'
    assert (original['button'], original['pressed']) == ('Show translation', 'true')
    assert back == shown

    # A fact: title and text swap together; the hidden parts stay hidden (the clamp).
    fact_shown, fact_original = out['fact']
    assert fact_shown['translation'][0] == 'en:gu-fact__title' and fact_shown['original'] == []
    assert fact_shown['label'] == 'Machine-translated from Dutch'
    assert fact_original['translation'] == []
    assert fact_original['original'] == [part.replace('en:', 'nl:') for part in fact_shown['translation']]
    assert out['factClamped']['original'] == [] and 'en:gu-fact__text' in out['factClamped']['translation']
    assert fact_original['label'] == 'Original in Dutch'

    fetched, post_original = out['post']
    assert fetched['hasTranslation'] and fetched['visibleLang'] == 'de'
    assert fetched['visibleText'].startswith('Erster Absatz.') and out['postParagraphs'] == 2
    assert fetched['label'] == 'Maschinell übersetzt, Originalsprache: Niederländisch'
    assert post_original['visibleLang'] == 'nl' and post_original['button'] == 'Übersetzung zeigen'
