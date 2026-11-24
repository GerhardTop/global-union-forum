"""Safety net against fixed interface text outside gettext: a fixed set of
pages, opened in English, German, Spanish and French as a user with the right role, may
not show a typical Dutch word.

What counts is the visible text: text nodes, the attributes a reader sees or
hears (title, aria-label, placeholder, alt, the value of a button) and the
string literals of inline scripts (dialog texts). Content of users and the
demo content is left out: every text that is literally a value in the
database (post bodies, titles, names, translations) is skipped, so what is
left is the interface.

DUTCH_WORDS is checked against every English, German, Spanish and French translation in the
catalogs (no false hits there); deliberate exceptions are on ALLOWED, with
the reason.
"""
import html
import os
import re
import uuid
from html.parser import HTMLParser

import pytest
from babel.messages.pofile import read_po

from app.models import IssueProposal, PostReport, ProposalReport
from tests.test_delete_issue import _clean_session, _cleanup  # noqa: F401  (autouse fixture)
from tests.test_issue_polish import _client, _fact, _issue, _req, make_user  # noqa: F401  (fixture)

# Typical Dutch words that are no word in English, German, Spanish or French. Not on it, on
# purpose: alle, je, worden, bericht(en), week, juni, juli, oktober, Name
# (German or English), dit (French).
DUTCH_WORDS = {
    'het', 'een', 'geen', 'niet', 'ook', 'nog', 'maar', 'naar', 'deze', 'jouw', 'wilt', 'zijn', 'wordt',
    'kunnen', 'moet', 'alleen', 'terug', 'nieuw', 'nieuwe', 'totaal', 'naam', 'acties', 'aangemeld',
    'gesprek', 'gesprekken', 'gebruiker', 'gebruikers', 'verwijderen', 'verwijder', 'bekijken', 'bekijk',
    'opslaan', 'annuleren', 'wijzigen', 'bewerken', 'toevoegen', 'zoeken', 'sluiten', 'gesloten', 'beheer',
    'instellingen', 'melding', 'meldingen', 'vraagstuk', 'vraagstukken', 'voorstel', 'voorstellen',
    'bijdrage', 'bijdragen', 'feiten', 'stemmen', 'gedragsregels', 'spelregels', 'voorbeeld',
    'januari', 'februari', 'maart', 'augustus',
    'maandag', 'dinsdag', 'woensdag', 'donderdag', 'vrijdag', 'zaterdag', 'zondag',
}
WORD = re.compile(r'\b(' + '|'.join(sorted(DUTCH_WORDS)) + r')\b', re.I)

# Text that may be Dutch in every language, with the reason. Removed from the
# visible text before the check.
ALLOWED = {
    'Nederlands': 'the name of the language in its own language, in the language switcher',
    'Global Union Forum': 'brand name',
}

TRANSLATIONS = os.path.join(os.path.dirname(__file__), '..', 'app', 'translations')

PAGES = [
    # public
    '/', '/about', '/faq', '/manifest', '/manifest/landen', '/spelregels', '/privacy', '/initiatiefnemer',
    '/achtergrond',
    # forum and issue
    '/forum', '/forum/gesloten', '{thread}', '/gesprekken', '{issue}', '{issue}?vergroot=feiten', '{issue}?vergroot=voorstellen',
    '{issue}/voorstellen/nieuw', '{proposal}',
    # own pages
    '/profile', '/meldingen', '/profiel/meldingen',
    # moderation
    '/moderator', '/moderator?stroom=alle', '/moderator/melding/feit/{fact_id}', '/moderator/melding/voorstel/{proposal_id}',
    '/moderator/gebruiker/{author_id}', '/moderator/gebruiker/{author_id}/verwijderen', '{issue_manage}',
    '/moderator/rechten',
    # admin
    '/admin', '/admin/drempels', '/admin/voorstellen', '/admin/taalmonitor',
]
ANONYMOUS_PAGES = ['/login', '/aanmelden']

_ATTRS = ('title', 'aria-label', 'placeholder', 'alt')
# A script string that is code, not text: a path, URL, selector or lowercase
# identifier ('/admin/gebruiker/', '.gu-pv a[href="#voorstel"]', 'voorstel').
_CODE_STRING = re.compile(r'^(?:[/.#\[].*|[a-z0-9_-]+|\S*[\[\]=#]\S*(?: \S*[\[\]=#.]\S*)*)$')
_JS_STRING = re.compile(r"'((?:[^'\\\n]|\\.)*)'|\"((?:[^\"\\\n]|\\.)*)\"")


class _Visible(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.texts, self._skip, self._script = [], 0, False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for name in _ATTRS:
            if a.get(name):
                self.texts.append(a[name])
        if tag == 'input' and a.get('type') in ('submit', 'button') and a.get('value'):
            self.texts.append(a['value'])
        if tag == 'script':
            self._script = True
        elif tag in ('style', 'template'):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag == 'script':
            self._script = False
        elif tag in ('style', 'template') and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if self._script:
            literals = [m.group(1) or m.group(2) or '' for m in _JS_STRING.finditer(data)]
            self.texts += [t for t in literals if not _CODE_STRING.match(t)]
        elif not self._skip:
            self.texts.append(data)


QUOTED = re.compile(r'["\u201e\u201c]([^"\u201c\u201d]+)["\u201c\u201d]')


def _norm(text):
    return ' '.join(html.unescape(text).split())


def _content(db):
    """Every text value in the database: the content of users and the demo."""
    values = set()
    for table in db.metadata.sorted_tables:
        for col in [c for c in table.columns if _python_type(c) is str]:
            for (value,) in db.session.execute(db.select(col).distinct()):
                if value:
                    values.add(_norm(value))
    return values, '\n'.join(values)


def _python_type(col):
    try:
        return col.type.python_type
    except NotImplementedError:
        return None


def _interface_texts(page, content):
    values, blob = content
    parser = _Visible()
    parser.feed(page)
    out = []
    for raw in parser.texts:
        text = _norm(raw)
        # Content glued to punctuation of the interface (": title", "„title“", "title…").
        core = text.strip(' .…:;,·›»«()"\'„“”')
        if not core or text in values or core in values or (len(core) >= 12 and core in blob):
            continue
        # Content quoted inside an interface sentence (a report's reason:
        # 'Contribution · reason: "Niet respectvol."'): the quote goes, the
        # sentence around it is still checked. Other tests in the session leave
        # such reports open, in Dutch.
        text = QUOTED.sub(lambda m: ' ' if _norm(m.group(1)) in values else m.group(0), text)
        for allowed in ALLOWED:
            text = text.replace(allowed, ' ')
        out.append(text)
    return out


@pytest.fixture
def world(db, make_user):
    admin = make_user(admin=True)
    author, reporter = make_user(), make_user()
    issue = _issue(db, author, f'Q{uuid.uuid4().hex[:6]}')
    fact = _fact(db, issue, author, body=f'F{uuid.uuid4().hex[:6]}')
    proposal = IssueProposal(issue_id=issue.id, statement=f'S{uuid.uuid4().hex[:6]}', created_by_id=author.id)
    db.session.add(proposal)
    db.session.commit()
    # A reason in Dutch, as other tests leave them: user content inside an
    # interface sentence on /moderator, which the check must skip.
    db.session.add(PostReport(post_id=fact.id, reporter_id=reporter.id, reason='Niet respectvol, een test.'))
    db.session.add(ProposalReport(proposal_id=proposal.id, reporter_id=reporter.id, reason='y'))
    db.session.commit()
    paths = {'issue': f'/vraagstuk/{issue.id}', 'proposal': f'/vraagstuk/{issue.id}/voorstel/{proposal.id}',
             'thread': f'/forum/{issue.thread_id}', 'issue_manage': f'/moderator/vraagstuk/{issue.id}/beheer',
             'fact_id': fact.id, 'proposal_id': proposal.id, 'author_id': author.id}
    yield admin, [p.format(**paths) for p in PAGES]
    _cleanup(issue.id)


@pytest.mark.parametrize('lang', ['en', 'de', 'es', 'fr'])
def test_no_dutch_interface_text(app, db, world, lang):
    admin, pages = world
    content = _content(db)
    found = {}
    for user, paths in ((admin, pages), (None, ANONYMOUS_PAGES)):
        client = _client(app, user, lang)
        for path in paths:
            resp = _req(client, 'GET', path)
            assert resp.status_code == 200, (lang, path, resp.status_code)
            for text in _interface_texts(resp.get_data(as_text=True), content):
                if WORD.search(text):
                    found.setdefault(path, []).append(text)
    assert found == {}, found


@pytest.mark.parametrize('lang', ['en', 'de', 'es', 'fr'])
def test_dutch_words_hit_no_english_or_german_translation(lang):
    """The word list gives no false hits: no translation in the language contains one."""
    with open(os.path.join(TRANSLATIONS, lang, 'LC_MESSAGES', 'messages.po'), 'rb') as f:
        catalog = read_po(f)
    hits = []
    for message in catalog:
        if not message.id:
            continue
        strings = [message.string] if isinstance(message.string, str) else list(message.string)
        hits += [(s, WORD.findall(s)) for s in strings if WORD.search(re.sub(r'<[^>]+>', ' ', s))]
    assert hits == []


@pytest.mark.parametrize('lang', ['en', 'de', 'es', 'fr'])
def test_no_dutch_on_the_label_and_the_vote_block(app, db, make_user, lang):
    """Multilingual phase 2: an issue in the voting phase whose title, intro and
    proposal are machine-translated (the label) and a proposal page with the
    vote block ("you vote on the original text")."""
    from app import content_i18n, issue_proposals as ip, issues
    author, voter = make_user(), make_user('voter')
    issue = issues.create({'title': f'Q{uuid.uuid4().hex[:6]}', 'intro': f'I{uuid.uuid4().hex[:6]}',
                           'kind': 'online', 'support_threshold': 1}, author, lang='nl')
    db.session.flush()
    proposal = ip.create(issue, author, f'S{uuid.uuid4().hex[:6]}', f'E{uuid.uuid4().hex[:6]}', lang='nl')
    db.session.flush()
    ip.support(proposal, voter)
    issue.phase = 'stemmen'
    for obj, field in ((issue, 'title'), (issue, 'intro'), (proposal, 'statement'), (proposal, 'explanation')):
        content_i18n.set_translation(obj, field, lang, f'T{uuid.uuid4().hex[:6]}', is_machine=True)
    db.session.commit()
    try:
        content = _content(db)
        found = {}
        client = _client(app, voter, lang)
        for path in (f'/vraagstuk/{issue.id}', f'/vraagstuk/{issue.id}/voorstel/{proposal.id}'):
            resp = _req(client, 'GET', path)
            assert resp.status_code == 200, (lang, path)
            page = resp.get_data(as_text=True)
            assert 'gu-mt__note' in page
            if '/voorstel/' in path:
                assert 'gu-vote__official' in page
            for text in _interface_texts(page, content):
                if WORD.search(text):
                    found.setdefault(path, []).append(text)
        assert found == {}, found
    finally:
        _cleanup(issue.id)
