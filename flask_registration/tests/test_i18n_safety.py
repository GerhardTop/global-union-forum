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
    r"""|['"]nl['"]\s*:"""                                 # {'nl': ..., 'en': ...}
    r"""|\[\s*lang\s*\]""")                              # labels[lang]: a per-language lookup

# path -> (count, reason). Lower the count as branches are converted.
EXCEPTIONS = {
    'app/__init__.py': (3, 'data: de taalkeuze (sessie, user.lang bij inloggen), get_locale en LANGUAGES; komt bij het taalregister'),
    'app/digest.py': (1, 'data: lang_for(user), de opgeslagen taal van de ontvanger met terugval op nl'),
    'app/issues.py': (2, 'contentmodel, fase 0 stap 6: titel en openingsbericht van een gesprek per taal (title_*_nl/_en, body_nl/_en)'),
    'app/models.py': (4, 'contentmodel, fase 0 stap 6: Thread.title(lang), de AI-uitleg (explanation_nl/_en) en post_lang'),
    'app/notifications.py': (1, 'contentmodel, fase 0 stap 6: de titel van een gesprek per taal in een melding'),
    'app/quality.py': (9, 'contentmodel, fase 0 stap 6: AI-vertaal- en kwaliteitscode (taalregels voor de prompt, explanation_nl/_en, body_nl)'),
    'app/routes/admin.py': (2, 'data: zoeken in de titels in beide talen; de taalmonitor filtert op taal'),
    'app/routes/auth.py': (2, 'data: de registratietaal wordt opgeslagen (user.lang, auto_translate)'),
    'app/routes/forum.py': (4, 'contentmodel, fase 0 stap 6: automatisch vertalen (auto_translate, translate_text, body_nl/_en)'),
    'app/routes/main.py': (2, 'data: set_lang (de taalkiezer) en auto_translate'),
    'app/routes/social.py': (2, 'data: de registratietaal wordt opgeslagen (user.lang, auto_translate)'),
    'app/status.py': (4, 'data: de taalmonitor telt per taal'),
    'app/templates/_footer.html': (2, 'taalkiezer: markeert de actieve taal (is-active); de taalkiezer komt bij het taalregister'),
    'app/templates/_header.html': (4, 'taalkiezer: markeert de actieve taal (is-active); de taalkiezer komt bij het taalregister'),
    'app/templates/forum/_post.html': (1, 'contentmodel, fase 0 stap 6: body_nl/body_en van een bijdrage'),
    'app/templates/forum/closed.html': (2, 'contentmodel, fase 0 stap 6: titel en eyebrow van een gesprek (title_*_nl/_en, eyebrow_nl/_en)'),
    'app/templates/forum/edit_thread.html': (1, 'contentmodel, fase 0 stap 6: titel en eyebrow van een gesprek (title_*_nl/_en, eyebrow_nl/_en)'),
    'app/templates/forum/index.html': (2, 'contentmodel, fase 0 stap 6: titel en eyebrow van een gesprek (title_*_nl/_en, eyebrow_nl/_en)'),
    'app/templates/forum/thread.html': (5, 'contentmodel, fase 0 stap 6: titel en eyebrow van een gesprek (title_*_nl/_en, eyebrow_nl/_en)'),
    'app/templates/issues/proposal.html': (2, 'contentmodel, fase 0 stap 6: body_nl/body_en van een reactie op een voorstel'),
    'app/templates/issues/show.html': (1, 'contentmodel, fase 0 stap 6: titel en eyebrow van een gesprek (title_*_nl/_en, eyebrow_nl/_en)'),
    'app/templates/landenlijst.html': (7, 'contentmodel, fase 0 stap 6: landgegevens per taal (land_nl/_en, niveau_nl/_en, remarks_nl/_en) en de downloads (Excel, PDF) per taal'),
    'app/templates/profile.html': (3, 'taalkiezer (2x is-active); data: de standaard voor automatisch vertalen volgt de taal (auto_translate)'),
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


# ── After-care: catch untranslated text and template syntax in msgids ──────

def _extracted():
    """The msgids extract finds in app/ (same keywords as the project's extract)."""
    from babel.messages.extract import extract_from_dir
    keywords = {'_': None, 'gettext': None, 'ngettext': (1, 2), 'pgettext': ((1, 'c'), 2),
                'npgettext': ((1, 'c'), 2, 3), 'lazy_gettext': None, 'lazy_pgettext': ((1, 'c'), 2)}
    method_map = [('**.py', 'python'), ('**/templates/**.html', 'jinja2')]
    out = set()
    for _f, _l, message, _c, context in extract_from_dir(APP, method_map=method_map, keywords=keywords):
        out.add((context, message if isinstance(message, str) else tuple(message)))
    return out


def _all_texts(message):
    ids = [message.id] if isinstance(message.id, str) else list(message.id)
    strs = [message.string] if isinstance(message.string, str) else list(message.string)
    return ids, strs


def test_no_template_syntax_in_msgids_or_translations():
    """A msgid with {{ }} or {% %} is never filled in; it shows literally."""
    bad = []
    for lang in ('nl', 'en'):
        for message in _po(lang):
            if not message.id:
                continue
            ids, strs = _all_texts(message)
            if any(tag in text for text in ids + strs for tag in ('{{', '}}', '{%', '%}')):
                bad.append((lang, message.id))
    for _context, message in _extracted():
        texts = [message] if isinstance(message, str) else list(message)
        if any(tag in text for text in texts for tag in ('{{', '}}', '{%', '%}')):
            bad.append(('source', message))
    assert not bad, bad


def test_every_extracted_msgid_is_in_the_nl_catalog():
    """A msgid without an NL entry shows in English to Dutch readers."""
    catalog = {(m.context, m.id if isinstance(m.id, str) else tuple(m.id)) for m in _po('nl') if m.id}
    missing = sorted(_extracted() - catalog, key=str)
    assert not missing, f'Not in the NL catalog: {missing[:10]}'


# NL translations identical to their msgid, on purpose. Anything else that
# stays identical is a Dutch msgid or an untranslated English text.
SAME_IN_DUTCH = {
    'Freedom for all': 'slogan, niet vertalen',
    'Dashboard': 'leenwoord', 'Privacy': 'leenwoord', 'Cookies': 'leenwoord', 'Spam': 'leenwoord',
    'Respect': 'leenwoord', 'Media': 'leenwoord', 'Podcast': 'leenwoord', 'Video': 'leenwoord',
    'Online': 'leenwoord', 'Experts': 'leenwoord', 'Event': 'leenwoord', 'event': 'leenwoord',
    'Contact': 'leenwoord',
    'Admin': 'rolnaam', 'Moderator': 'rolnaam', 'Moderators': 'rolnaam', 'Senior': 'rolnaam',
    'Senior moderator': 'rolnaam', 'Senior moderators': 'rolnaam',
    'AI:': 'afkorting',
    'WTO': 'naam van een organisatie', 'WCO': 'naam van een organisatie', 'FATF': 'naam van een organisatie',
    'IASB': 'naam van een organisatie', 'FAO/WHO': 'naam van een organisatie', 'ICAO, IMO': 'naam van een organisatie',
    'ISO, IEC, ITU': 'naam van een organisatie', 'Codex Alimentarius': 'naam van een standaard',
    'Basel III': 'naam van een standaard', 'IFRS': 'naam van een standaard', 'UNCAC': 'naam van een verdrag',
    'UNFCCC': 'naam van een verdrag',
}


def test_nl_translation_differs_from_the_msgid_unless_allowed():
    same = []
    for message in _po('nl'):
        if not message.id:
            continue
        ids, strs = _all_texts(message)
        if any(i == s for i, s in zip(ids, strs)) and ids[0] not in SAME_IN_DUTCH:
            same.append(message.id)
    assert not same, f'NL equals the msgid (translate it, or add it to SAME_IN_DUTCH with a reason): {same}'
    assert all(SAME_IN_DUTCH.values())


def test_no_long_or_short_dashes_in_msgids_or_dutch():
    """App text has no en dash (U+2013) or em dash (U+2014)."""
    bad = []
    for message in _po('nl'):
        if not message.id:
            continue
        ids, strs = _all_texts(message)
        if any(dash in text for text in ids + strs for dash in ('–', '—')):
            bad.append(message.id)
    assert not bad, bad
