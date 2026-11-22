"""Multilingual phase 0: the safety net for moving the NL/EN language
branches to gettext.

a) Every binary language branch in app/ (templates, Python, inline JS) is on
   the exception list below with its exact count. A new one fails; converting
   one means lowering the count (and removing the file at zero). What is left
   is the content model (fase 0 stap 6) or real data (the language monitor,
   source_lang); the language choice itself goes through app/i18n.py.
b) Every enabled language except the source language (en) has a complete
   catalog: every msgid translated, nothing fuzzy, no template syntax, no
   long or short dashes, and no translation equal to its msgid unless that
   language's allow list (SAME_AS_MSGID) says so.
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
import config
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po

from tests.test_delete_issue import _clean_session, _cleanup  # noqa: F401  (autouse fixture)
from tests.test_issue_polish import _client, _issue, _req, make_user  # noqa: F401  (fixture)

ROOT = os.path.join(os.path.dirname(__file__), '..')
APP = os.path.join(ROOT, 'app')
TRANSLATIONS = os.path.join(APP, 'translations')
# The languages with a catalog to check: every enabled language but the
# source language of the msgids.
TRANSLATED = [code for code in config.ENABLED_LANGUAGES if code != config.SOURCE_LANGUAGE]

# ── a) The language branches ───────────────────────────────────────────────

PATTERN = re.compile(
    r"""[!=]==?\s*['"](?:nl|en)['"]"""                     # lang == 'nl', lang === 'en', != 'nl'
    r"""|\bin\s*\(\s*['"]nl['"]\s*,\s*['"]en['"]\s*\)"""   # in ('nl', 'en')
    r"""|\bif\s+(?:not\s+)?nl\b"""                         # {% if nl %}, 'x' if nl else 'y'
    r"""|['"]nl['"]\s*:"""                                 # {'nl': ..., 'en': ...}
    r"""|\[\s*lang\s*\]""")                              # labels[lang]: a per-language lookup

# path -> (count, reason). Lower the count as branches are converted.
EXCEPTIONS = {
    'app/content_migration.py': (1, 'data: de datamigratie van fase 0 stap 6 leest de oude kolommen per taal (nl, en)'),
    'app/routes/admin.py': (1, 'data: de taalmonitor filtert op taal (source_lang)'),
    'app/status.py': (4, 'data: de taalmonitor telt per taal (source_lang)'),
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


@pytest.mark.parametrize('lang', TRANSLATED)
def test_catalog_is_complete_and_not_fuzzy(lang):
    catalog = _po(lang)
    empty, fuzzy = [], []
    for message in catalog:
        if not message.id:
            continue
        strings = message.string if isinstance(message.string, (list, tuple)) else [message.string]
        if not all(strings):
            empty.append(message.id)
        if message.fuzzy:
            fuzzy.append(message.id)
    assert not empty, f'{lang} msgids without a translation: {empty[:10]}'
    assert not fuzzy, f'{lang} msgids marked fuzzy: {fuzzy[:10]}'


@pytest.mark.parametrize('lang', config.ENABLED_LANGUAGES)
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
          'en': ('A union of functioning democracies', 'Global Union Forum is an open think tank.'),
          'de': ('Eine Union funktionierender Demokratien', 'Global Union Forum ist eine offene Denkfabrik.'),
          'es': ('Una unión de democracias que funcionan', 'Global Union Forum es un laboratorio de ideas abierto.'),
          'fr': ('Une union de démocraties qui fonctionnent', "Global Union Forum est un laboratoire d'idées ouvert.")},
    '/forum': {'nl': ('Het gesprek', 'reacties · laatst'), 'en': ('The conversation', 'replies · last'),
               'de': ('Das Gespräch', 'Antworten · zuletzt'), 'es': ('La conversación', 'respuestas · última'),
               'fr': ('La conversation', 'réponses · dernier message')},
    'thread': {'nl': ('Het gesprek ›', 'reacties · laatst'), 'en': ('The conversation ›', 'replies · last'),
               'de': ('Das Gespräch ›', 'Antworten · zuletzt'), 'es': ('La conversación ›', 'respuestas · última'),
               'fr': ('La conversation ›', 'réponses · dernier message')},
    'issue': {'nl': ('Feiten en achtergrond', 'Discussie', 'Voorstellen'),
              'en': ('Facts and background', 'Discussion', 'Proposals'),
              'de': ('Fakten und Hintergrund', 'Diskussion', 'Vorschläge'),
              'es': ('Hechos y contexto', 'Debate', 'Propuestas'),
              'fr': ('Faits et contexte', 'Débat', 'Propositions')},
    '/profile': {'nl': ('Mijn profiel', 'Hoe verschijn je in het forum?'),
                 'en': ('My profile', 'How do you appear in the forum?'),
                 'de': ('Mein Profil', 'Wie erscheinst du im Forum?'),
                 'es': ('Mi perfil', '¿Cómo apareces en el foro?'),
                 'fr': ('Mon profil', 'Comment apparaissez-vous dans le forum ?')},
    '/moderator': {'nl': ('Betwiste feiten', 'Geen open meldingen.'), 'en': ('Disputed facts', 'No open reports.'),
                   'de': ('Angezweifelte Fakten', 'Keine offenen Meldungen.'),
                   'es': ('Hechos cuestionados', 'No hay reportes abiertos.'),
                   'fr': ('Faits contestés', 'Aucun signalement ouvert.')},
    '/manifest': {'nl': ('Gelijkgezinde landen als positieve kracht', 'In dit manifest'),
                  'en': ('Like-minded countries as a positive force', 'In this manifesto'),
                  'de': ('Gleichgesinnte Länder als positive Kraft', 'In diesem Manifest'),
                  'es': ('Países afines como fuerza positiva', 'En este manifiesto'),
                  'fr': ('Des pays partageant les mêmes valeurs, une force positive', 'Dans ce manifeste')},
    '/about': {'nl': ('Een denktank met sterke intentie', 'Geen lidmaatschap. Geen contributie.'),
               'en': ('A think tank with strong intent', 'No membership. No fees.'),
               'de': ('Eine Denkfabrik mit starker Absicht', 'Keine Mitgliedschaft. Keine Beiträge.'),
               'es': ('Un laboratorio de ideas con un propósito firme', 'Sin afiliación. Sin cuotas.'),
               'fr': ("Un laboratoire d'idées animé d'une ferme intention", "Pas d'adhésion. Pas de cotisation.")},
    '/faq': {'nl': ('Veelgestelde vragen', 'Wat is Global Union?'),
             'en': ('What is Global Union?', 'What is the difference between Global Union and the Global Union Forum?'),
             'de': ('Häufige Fragen', 'Was ist der Unterschied zwischen Global Union und dem Global Union Forum?'),
             'es': ('Preguntas frecuentes', '¿Qué diferencia hay entre Global Union y el Global Union Forum?'),
             'fr': ('Questions fréquentes', 'Quelle est la différence entre Global Union et le Global Union Forum ?')},
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


@pytest.mark.parametrize('lang', ['nl', 'en', 'de', 'es', 'fr'])
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
    for lang in config.ENABLED_LANGUAGES:
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


# A loose % in a msgid ("0% import duties") is read by Babel as a placeholder
# ("% i"), so every translation is forced to carry the same fake placeholder.
# Percentages go in through a named placeholder: _('%(rate)s import duties', rate='0%').
LOOSE_PERCENT_ALLOWED = {
    '%d %B %Y': 'strftime format, not a gettext placeholder',
    'Global%20Union%20motion': 'URL-encoded mail subject',
}


def test_no_loose_percent_sign_in_msgids():
    placeholder = re.compile(r'%\([a-z_]+\)[sd]|%%')
    bad = sorted({message for _context, message in _extracted()
                  for text in ([message] if isinstance(message, str) else list(message))
                  if '%' in placeholder.sub('', text) and text not in LOOSE_PERCENT_ALLOWED}, key=str)
    assert not bad, bad


@pytest.mark.parametrize('lang', TRANSLATED)
def test_every_extracted_msgid_is_in_the_catalog(lang):
    """A msgid without an entry shows in English to readers of that language."""
    catalog = {(m.context, m.id if isinstance(m.id, str) else tuple(m.id)) for m in _po(lang) if m.id}
    missing = sorted(_extracted() - catalog, key=str)
    assert not missing, f'Not in the {lang} catalog: {missing[:10]}'


# NL translations identical to their msgid, on purpose. Anything else that
# stays identical is a Dutch msgid or an untranslated English text.
SAME_IN_DUTCH = {
    'Freedom for all': 'slogan, niet vertalen',
    'Dashboard': 'leenwoord', 'Privacy': 'leenwoord', 'Cookies': 'leenwoord', 'Spam': 'leenwoord',
    'Respect': 'leenwoord', 'Media': 'leenwoord', 'Podcast': 'leenwoord', 'Video': 'leenwoord',
    'Online': 'leenwoord', 'Experts': 'leenwoord', 'Event': 'leenwoord', 'event': 'leenwoord',
    'Contact': 'leenwoord', 'Forum': 'leenwoord', 'Status': 'leenwoord', 'Menu': 'leenwoord',
    'Download Excel': 'leenwoord en merknaam', 'Download PDF': 'leenwoord en merknaam',
    'Admin': 'rolnaam', 'Moderator': 'rolnaam', 'Moderators': 'rolnaam', 'Senior': 'rolnaam',
    'Senior moderator': 'rolnaam', 'Senior moderators': 'rolnaam',
    'AI:': 'afkorting',
    'WTO': 'naam van een organisatie', 'WCO': 'naam van een organisatie', 'FATF': 'naam van een organisatie',
    'IASB': 'naam van een organisatie', 'FAO/WHO': 'naam van een organisatie', 'ICAO, IMO': 'naam van een organisatie',
    'ISO, IEC, ITU': 'naam van een organisatie', 'Codex Alimentarius': 'naam van een standaard',
    'Basel III': 'naam van een standaard', 'IFRS': 'naam van een standaard', 'UNCAC': 'naam van een verdrag',
    'UNFCCC': 'naam van een verdrag',
}


# German translations identical to their msgid, on purpose (phase 1, see
# docs/i18n/glossary_de.md).
SAME_IN_GERMAN = {
    'Freedom for all': 'Slogan, nicht übersetzen',
    'Initiator · Global Union Forum': 'Rollenname und Markenname',
    'Moderator': 'Rollenname', 'Senior': 'Rollenname', 'Admin': 'Rollenname',
    'Spam': 'Lehnwort', 'Fairness': 'Lehnwort', 'Dashboard': 'Lehnwort', 'Cookies': 'Lehnwort',
    'Podcast': 'Lehnwort', 'Video': 'Lehnwort', 'Online': 'Lehnwort',
    'Name': 'im Deutschen gleich', 'Organisation': 'im Deutschen gleich', 'Phase': 'im Deutschen gleich',
    'Moderation': 'im Deutschen gleich', 'Pseudonym': 'im Deutschen gleich', 'optional': 'im Deutschen gleich',
    'Optional': 'im Deutschen gleich', 'Neutral': 'im Deutschen gleich', 'Version %(n)s': 'im Deutschen gleich',
    'Forum': 'im Deutschen gleich', 'Status': 'im Deutschen gleich', 'Navigation': 'im Deutschen gleich',
    'WTO': 'Name einer Organisation', 'WCO': 'Name einer Organisation', 'FATF': 'Name einer Organisation',
    'IASB': 'Name einer Organisation', 'FAO/WHO': 'Name einer Organisation', 'ICAO, IMO': 'Name einer Organisation',
    'ISO, IEC, ITU': 'Name einer Organisation', 'UN': 'Name einer Organisation', 'UN, ILO': 'Name einer Organisation',
    'Codex Alimentarius': 'Name eines Standards', 'Basel III': 'Name eines Standards', 'IFRS': 'Name eines Standards',
    'UNCAC': 'Name eines Abkommens', 'UNFCCC': 'Name eines Abkommens',
}


# Spanish translations identical to their msgid, on purpose (phase 1, see
# docs/i18n/glossary_es.md).
SAME_IN_SPANISH = {
    'Freedom for all': 'eslogan, no se traduce',
    'Spam': 'préstamo', 'Cookies': 'préstamo',
    'No': 'igual en español', 'no': 'igual en español', 'Neutral': 'igual en español',
    'Visible': 'igual en español',
    'Codex Alimentarius': 'nombre de una norma', 'IASB': 'nombre de una organización',
    'Total': 'igual en español',
}


# French translations identical to their msgid, on purpose (phase 1, see
# docs/i18n/glossary_fr.md).
SAME_IN_FRENCH = {
    'Freedom for all': 'slogan, non traduit',
    'Spam': 'emprunt', 'Cookies': 'emprunt', 'Podcast': 'emprunt',
    'Respect': 'identique en français', 'Forum · conversation': 'identique en français',
    'Senior': 'identique en français', 'Contact': 'identique en français', 'Navigation': 'identique en français',
    'Menu': 'identique en français', 'Experts': 'identique en français', 'Message': 'identique en français',
    'Organisation': 'identique en français', 'Phase': 'identique en français', 'phase': 'identique en français',
    'Phases': 'identique en français', 'Notifications': 'identique en français', 'Forum': 'identique en français',
    'Actions': 'identique en français', 'Contribution': 'identique en français',
    'Contributions': 'identique en français', 'Date': 'identique en français', 'Source': 'identique en français',
    'source': 'identique en français', 'public': 'identique en français', 'Version %(n)s': 'identique en français',
    'version': 'identique en français', 'vote': 'identique en français', 'Introduction': 'identique en français',
    'Visible': 'identique en français', '%(num)d participant': 'identique en français',
    'Total': 'identique en français',
    '%(num)d suspension': 'identique en français',
    'Motion · Global Union': 'identique en français, nom propre',
    '%d %B %Y': 'format de date identique en français',
    'Codex Alimentarius': "nom d'une norme", 'IFRS': "sigle d'une norme", 'IASB': "sigle d'une organisation",
    'Convention 108+': "nom d'une convention",
}


# Per language: the msgids whose translation may equal the msgid, with the
# reason. A newly enabled language gets its own entry here.
SAME_AS_MSGID = {
    'nl': SAME_IN_DUTCH,
    'de': SAME_IN_GERMAN,
    'es': SAME_IN_SPANISH,
    'fr': SAME_IN_FRENCH,
}


@pytest.mark.parametrize('lang', TRANSLATED)
def test_translation_differs_from_the_msgid_unless_allowed(lang):
    allowed = SAME_AS_MSGID.get(lang, {})
    same = []
    for message in _po(lang):
        if not message.id:
            continue
        ids, strs = _all_texts(message)
        if any(i == s for i, s in zip(ids, strs)) and ids[0] not in allowed:
            same.append(message.id)
    assert not same, (f'{lang} equals the msgid (translate it, or add it to SAME_AS_MSGID[{lang!r}] '
                      f'with a reason): {same}')
    assert all(allowed.values())


@pytest.mark.parametrize('lang', TRANSLATED)
def test_no_long_or_short_dashes_in_msgids_or_translations(lang):
    """App text has no en dash (U+2013) or em dash (U+2014)."""
    bad = []
    for message in _po(lang):
        if not message.id:
            continue
        ids, strs = _all_texts(message)
        if any(dash in text for text in ids + strs for dash in ('–', '—')):
            bad.append(message.id)
    assert not bad, bad


# ── Phase 0 step 6: the old per-language content columns are unused ─────────

OLD_COLUMNS = re.compile(
    r"\b(?:body|author_badge|explanation|ai_response|eyebrow|title_prefix|title_accent|title_suffix"
    r"|land|niveau|uitnodigen|remarks)_(?:nl|en)\b"
    r"|\bfact_title_en\b"
    r"|\b(?:body|explanation|ai_response|eyebrow|title_prefix|title_accent|title_suffix)_\{")  # f'{field}_{lang}'
# Only the data migration reads them (and names them to relax NOT NULL).
OLD_COLUMNS_ALLOWED = {'app/content_migration.py'}


def _without_comments(path, source):
    if path.endswith('.py'):
        return '\n'.join(line.split('#', 1)[0] for line in source.splitlines())
    return re.sub(r'\{#.*?#\}', '', source, flags=re.S)


def test_no_code_uses_the_old_content_columns():
    found = []
    for folder, _dirs, files in os.walk(APP):
        if 'translations' in folder:
            continue
        for name in files:
            if not name.endswith(('.py', '.html', '.js', '.json')):
                continue
            path = os.path.relpath(os.path.join(folder, name), ROOT)
            if path in OLD_COLUMNS_ALLOWED:
                continue
            with open(os.path.join(folder, name), encoding='utf-8') as f:
                source = _without_comments(path, f.read())
            found += [(path, m.group(0)) for m in OLD_COLUMNS.finditer(source)]
    assert not found, f'Old per-language columns in use (go through app/content_i18n.py): {found}'
