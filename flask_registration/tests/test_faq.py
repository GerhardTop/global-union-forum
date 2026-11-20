"""
De FAQ-pagina (/faq): vier thema's met uitklapbare vragen (<details>,
standaard dicht), de link naar /privacy en het menu-item. Since
multilingual phase 0 step 3 the content is in the translation catalog
(app/faq.py, lazy_gettext); tests/data/faq_snapshot.json holds the NL and EN
text from before, and both renders must still show exactly that.

Gedeelde conftest-app.
"""
import html
import json
import os
import re

import pytest
from flask import g
from flask_babel import refresh

from app import faq


def _get(app, lang):
    c = app.test_client()
    with c.session_transaction() as s:
        s['lang'] = lang
    g.pop('_login_user', None)
    refresh()
    return c.get('/faq')


SNAPSHOT = json.load(open(os.path.join(os.path.dirname(__file__), 'data', 'faq_snapshot.json'), encoding='utf-8'))


def test_one_list_with_the_same_shape():
    assert len(faq.FAQ) == 4
    assert [len(q) for _t, q in faq.FAQ] == [5, 5, 7, 7]
    for lang in ('nl', 'en'):
        assert [len(q) for _t, q in SNAPSHOT[lang]] == [5, 5, 7, 7]


def test_no_long_or_short_dashes():
    for lang in ('nl', 'en'):
        for theme, questions in SNAPSHOT[lang]:
            for text in [theme] + [part for pair in questions for part in pair]:
                assert '–' not in text and '—' not in text


@pytest.mark.parametrize('lang', ['nl', 'en'])
def test_render_matches_the_text_from_before(app, lang):
    page = _get(app, lang).get_data(as_text=True)
    themes = re.findall(r'<h2 class="gu-faq__themetitle"[^>]*>(.*?)</h2>', page)
    questions = re.findall(r'<summary class="gu-faq__question">(.*?)</summary>', page)
    answers = re.findall(r'<div class="gu-faq__answer"><p>(.*?)</p></div>', page, re.S)
    snap = SNAPSHOT[lang]
    assert [html.unescape(t) for t in themes] == [t for t, _q in snap]
    assert [html.unescape(q) for q in questions] == [q for _t, qs in snap for q, _a in qs]
    # The answers render as before (the one with the privacy link as markup).
    assert [html.unescape(a) for a in answers] == [html.unescape(a) for _t, qs in snap for _q, a in qs]


def test_page_in_dutch(app):
    r = _get(app, 'nl')
    assert r.status_code == 200
    page = r.get_data(as_text=True)
    assert '<h1 class="gu-profile__title">Veelgestelde vragen</h1>' in page
    for theme in ('Global Union Forum', 'Manifest', 'Hoe het werkt', 'Account en privacy'):
        assert f'>{theme}</h2>' in page
    assert 'Wat is Global Union?' in page and 'Zestien jaar.' in page
    assert page.count('<details class="gu-faq__item"') == 24
    assert not re.search(r'<details[^>]*\bopen\b', page)  # standaard alles dicht
    assert '<a href="/privacy">privacy-pagina</a>' in page
    assert 'risico&#39;s' in page  # platte tekst wordt ge-escaped


def test_page_in_english(app):
    page = _get(app, 'en').get_data(as_text=True)
    assert '<h1 class="gu-profile__title">FAQ</h1>' in page
    for theme in ('Global Union Forum', 'Manifesto', 'How it works', 'Account and privacy'):
        assert f'>{theme}</h2>' in page
    assert 'What is Global Union?' in page and 'Sixteen.' in page
    assert '<a href="/privacy">privacy page</a>' in page
    assert page.count('<details class="gu-faq__item"') == 24


def test_nav_item(app):
    page = _get(app, 'nl').get_data(as_text=True)
    assert re.search(r'<a href="/faq"\s+class="is-active">FAQ</a>', page)
    other = app.test_client().get('/about').get_data(as_text=True)
    assert re.search(r'<a href="/faq"\s+>FAQ</a>', other)
