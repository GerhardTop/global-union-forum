"""Multilingual phase 0: the safety net for moving the NL/EN language
branches to gettext.

a) Every binary language branch in app/ (templates, Python, inline JS) is on
   the exception list below with its exact count. A new one fails; converting
   one means lowering the count (and removing the file at zero). Branches that
   are about DATA (source_lang, post_lang, an issue's language, the language
   monitor, set_lang, get_locale) stay, with their reason.
b) Every msgid in the NL catalog has a non-empty msgstr and no fuzzy flag.
c) The compiled .mo files in git match their .po files.
d) A smoke test: core pages render in NL and EN with fixed core sentences,
   recorded before the conversion, so the output stays the same.
"""
import gettext
import html
import io
import os
import re

import pytest
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po

from tests.test_delete_issue import _clean_session, _cleanup  # noqa: F401  (autouse fixture)
from tests.test_issue_polish import _client, _issue, _req, make_user  # noqa: F401  (fixture)

ROOT = os.path.join(os.path.dirname(__file__), '..')
APP = os.path.join(ROOT, 'app')
TRANSLATIONS = os.path.join(APP, 'translations')

# ── a) The language branches ───────────────────────────────────────────────

PATTERN = re.compile(
    r"""[!=]==?\s*['"](?:nl|en)['"]"""                     # lang == 'nl', lang === 'en', != 'nl'
    r"""|\bin\s*\(\s*['"]nl['"]\s*,\s*['"]en['"]\s*\)"""   # in ('nl', 'en')
    r"""|\bif\s+(?:not\s+)?nl\b"""                         # {% if nl %}, 'x' if nl else 'y'
    r"""|['"]nl['"]\s*:""")                                 # {'nl': ..., 'en': ...}

# path -> (count, reason). Lower the count as branches are converted.
EXCEPTIONS = {
    'app/__init__.py': (5, 'stap 4 (Python-teksten)'),
    'app/digest.py': (1, 'stap 4 (Python-teksten)'),
    'app/faq.py': (2, 'stap 4 (Python-teksten)'),
    'app/issues.py': (2, 'stap 4 (Python-teksten)'),
    'app/models.py': (11, 'stap 4 (Python-teksten)'),
    'app/notifications.py': (25, 'stap 4 (Python-teksten)'),
    'app/quality.py': (8, 'stap 4 (Python-teksten)'),
    'app/reports.py': (1, 'stap 4 (Python-teksten)'),
    'app/routes/admin.py': (15, 'stap 4 (Python-teksten)'),
    'app/routes/auth.py': (17, 'stap 4 (Python-teksten)'),
    'app/routes/forum.py': (7, 'stap 4 (Python-teksten)'),
    'app/routes/issues.py': (6, 'stap 4 (Python-teksten)'),
    'app/routes/main.py': (3, 'stap 4 (Python-teksten)'),
    'app/routes/profile.py': (3, 'stap 4 (Python-teksten)'),
    'app/routes/social.py': (11, 'stap 4 (Python-teksten)'),
    'app/status.py': (12, 'stap 4 (Python-teksten)'),
    'app/templates/404.html': (3, "ronde 4 (overige pagina's)"),
    'app/templates/500.html': (3, "ronde 4 (overige pagina's)"),
    'app/templates/_footer.html': (2, 'taalkiezer: markeert de actieve taal (is-active); de taalkiezer komt bij het taalregister'),
    'app/templates/_header.html': (4, 'taalkiezer: markeert de actieve taal (is-active); de taalkiezer komt bij het taalregister'),
    'app/templates/aanmelden/aanmelden.html': (3, "ronde 4 (overige pagina's)"),
    'app/templates/about.html': (19, 'stap 3 (redactietekst)'),
    'app/templates/base.html': (6, "ronde 4 (overige pagina's)"),
    'app/templates/email/notification.html': (4, "ronde 4 (overige pagina's)"),
    'app/templates/faq.html': (2, "ronde 4 (overige pagina's)"),
    'app/templates/forum/_post.html': (1, 'contentmodel, fase 0 stap 6: body_nl/body_en van een bijdrage'),
    'app/templates/forum/closed.html': (2, 'contentmodel, fase 0 stap 6: titel en eyebrow van een gesprek (title_*_nl/_en, eyebrow_nl/_en)'),
    'app/templates/forum/edit_thread.html': (1, 'contentmodel, fase 0 stap 6: titel en eyebrow van een gesprek (title_*_nl/_en, eyebrow_nl/_en)'),
    'app/templates/forum/index.html': (2, 'contentmodel, fase 0 stap 6: titel en eyebrow van een gesprek (title_*_nl/_en, eyebrow_nl/_en)'),
    'app/templates/forum/thread.html': (5, 'contentmodel, fase 0 stap 6: titel en eyebrow van een gesprek (title_*_nl/_en, eyebrow_nl/_en)'),
    'app/templates/index.html': (10, "ronde 4 (overige pagina's)"),
    'app/templates/initiatiefnemer.html': (5, "ronde 4 (overige pagina's)"),
    'app/templates/issues/proposal.html': (2, 'contentmodel, fase 0 stap 6: body_nl/body_en van een reactie op een voorstel'),
    'app/templates/issues/show.html': (1, 'contentmodel, fase 0 stap 6: titel en eyebrow van een gesprek (title_*_nl/_en, eyebrow_nl/_en)'),
    'app/templates/landenlijst.html': (19, 'stap 3 (redactietekst)'),
    'app/templates/login.html': (5, "ronde 4 (overige pagina's)"),
    'app/templates/manifest.html': (53, 'stap 3 (redactietekst)'),
    'app/templates/meldingen.html': (9, "ronde 4 (overige pagina's)"),
    'app/templates/motie_d66.html': (21, 'stap 3 (redactietekst)'),
    'app/templates/notificaties.html': (22, "ronde 4 (overige pagina's)"),
    'app/templates/privacy.html': (5, "ronde 4 (overige pagina's)"),
    'app/templates/profile.html': (56, "ronde 4 (overige pagina's)"),
    'app/templates/spelregels.html': (4, "ronde 4 (overige pagina's)"),
    'app/templates/wachtwoord_reset.html': (2, "ronde 4 (overige pagina's)"),
    'app/utils.py': (1, 'stap 4 (Python-teksten)'),
}


def _counts():
    out = {}
    for folder, _dirs, files in os.walk(APP):
        if 'translations' in folder:
            continue
        for name in files:
            if name.endswith(('.py', '.html', '.js')):
                path = os.path.join(folder, name)
                with open(path, encoding='utf-8') as f:
                    n = len(PATTERN.findall(f.read()))
                if n:
                    out[os.path.relpath(path, ROOT)] = n
    return out


def test_language_branches_are_on_the_exception_list():
    found = _counts()
    new = {path: n for path, n in found.items() if path not in EXCEPTIONS}
    assert not new, f'New language branches (use gettext instead): {new}'
    changed = {path: (EXCEPTIONS[path][0], found.get(path, 0)) for path in EXCEPTIONS
               if found.get(path, 0) != EXCEPTIONS[path][0]}
    assert not changed, f'Update EXCEPTIONS (expected, found): {changed}'
    assert all(reason for _n, reason in EXCEPTIONS.values())


# ── b) and c) The catalogs ─────────────────────────────────────────────────

def _po(lang):
    with open(os.path.join(TRANSLATIONS, lang, 'LC_MESSAGES', 'messages.po'), 'rb') as f:
        return read_po(f)


def test_nl_catalog_is_complete_and_not_fuzzy():
    catalog = _po('nl')
    empty, fuzzy = [], []
    for message in catalog:
        if not message.id:
            continue
        strings = message.string if isinstance(message.string, (list, tuple)) else [message.string]
        if not all(strings):
            empty.append(message.id)
        if message.fuzzy:
            fuzzy.append(message.id)
    assert not empty, f'NL msgids without a translation: {empty[:10]}'
    assert not fuzzy, f'NL msgids marked fuzzy: {fuzzy[:10]}'


@pytest.mark.parametrize('lang', ['nl', 'en'])
def test_compiled_mo_matches_the_po(lang):
    buffer = io.BytesIO()
    write_mo(buffer, _po(lang))
    fresh = gettext.GNUTranslations(io.BytesIO(buffer.getvalue()))._catalog
    with open(os.path.join(TRANSLATIONS, lang, 'LC_MESSAGES', 'messages.mo'), 'rb') as f:
        committed = gettext.GNUTranslations(f)._catalog
    fresh.pop('', None)       # the header (creation date) may differ
    committed.pop('', None)
    assert fresh == committed, 'Recompile the .mo (pybabel compile) and commit it'


# ── d) Smoke test with core sentences per language ─────────────────────────

CORE = {
    '/': {'nl': ('Een unie van functionerende democratieën', 'Global Union Forum is een open denktank.'),
          'en': ('A union of functioning democracies', 'Global Union Forum is an open think tank.')},
    '/forum': {'nl': ('Het gesprek', 'reacties · laatst'), 'en': ('The conversation', 'replies · last')},
    'thread': {'nl': ('Het gesprek ›', 'reacties · laatst'), 'en': ('The conversation ›', 'replies · last')},
    'issue': {'nl': ('Feiten en achtergrond', 'Discussie', 'Voorstellen'),
              'en': ('Facts and background', 'Discussion', 'Proposals')},
    '/profile': {'nl': ('Mijn profiel', 'Hoe verschijn je in het forum?'),
                 'en': ('My profile', 'How do you appear in the forum?')},
    '/moderator': {'nl': ('Betwiste feiten', 'Geen open meldingen.'), 'en': ('Disputed facts', 'No open reports.')},
    '/manifest': {'nl': ('Gelijkgezinde landen als positieve kracht', 'In dit manifest'),
                  'en': ('Like-minded countries as a positive force', 'In this manifesto')},
    '/about': {'nl': ('Een denktank met sterke intentie', 'Geen lidmaatschap. Geen contributie.'),
               'en': ('A think tank with strong intent', 'No membership. No fees.')},
    '/faq': {'nl': ('Veelgestelde vragen', 'Wat is Global Union?'),
             'en': ('What is Global Union?', 'What is the difference between Global Union and the Global Union Forum?')},
}


def _text(page):
    page = re.sub(r'<script\b.*?</script>|<style\b.*?</style>', ' ', page, flags=re.S | re.I)
    return ' '.join(html.unescape(re.sub(r'<[^>]+>', ' ', page)).split())


@pytest.fixture
def smoke(db, make_user):
    from app.models import Thread
    admin = make_user(admin=True)
    issue = _issue(db, admin, 'Een rooktest-vraagstuk')
    thread = Thread.query.filter_by(is_demo=True).order_by(Thread.id).first()
    yield {'admin': admin, 'issue': f'/vraagstuk/{issue.id}', 'thread': f'/forum/{thread.id}'}
    _cleanup(issue.id)


@pytest.mark.parametrize('lang', ['nl', 'en'])
def test_core_pages_render_with_their_core_sentences(app, db, smoke, lang):
    for key, sentences in CORE.items():
        path = smoke.get(key, key)
        resp = _req(_client(app, smoke['admin'], lang), 'GET', path)
        assert resp.status_code == 200, (lang, path)
        text = _text(resp.get_data(as_text=True))
        for sentence in sentences[lang]:
            assert sentence in text, (lang, path, sentence)
