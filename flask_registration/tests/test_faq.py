"""
De FAQ-pagina (/faq): tweetalig, vier thema's met uitklapbare vragen
(<details>, standaard dicht), de link naar /privacy en het menu-item.

Gedeelde conftest-app.
"""
import re

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


def test_nl_and_en_have_the_same_shape():
    nl, en = faq.FAQ['nl'], faq.FAQ['en']
    assert len(nl) == len(en) == 4
    assert [len(q) for _t, q in nl] == [len(q) for _t, q in en] == [5, 5, 7, 7]


def test_no_long_or_short_dashes():
    for lang in ('nl', 'en'):
        for theme, questions in faq.FAQ[lang]:
            for text in [theme] + [part for pair in questions for part in pair]:
                assert '–' not in text and '—' not in text


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
