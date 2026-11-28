"""
Accessibility step 1 (docs/toegankelijkheid.md, WCAG 2.2 AA): the rendered
pages checked on the rules that a machine can check. The same pages as
tests/test_no_dutch_interface.py (as a visitor and as an admin with every
role, in nl and en), each on:

- an own title in the tab (not the old default "MijnApp", not empty);
- exactly one h1 and no jump in heading levels (h2 to h4);
- the link "Skip to the content" as the first element in body, to the main;
- one main;
- a label for every visible input, select and textarea (label for, a label
  around it, aria-label or aria-labelledby);
- alt on every img (alt="" for decoration);
- an accessible name for every button and every link;
- lang on html;
- no text colour from the forbidden list (#aaa, #aaaaaa, the globe blue, the
  payoff grey) in a style attribute.

Not checked here (by hand, docs/toegankelijkheid.md): the whole path with only
the keyboard and VoiceOver.
"""
import re
from html.parser import HTMLParser

import pytest

from tests.test_delete_issue import _clean_session  # noqa: F401  (autouse fixture)
from tests.test_issue_polish import _client, _req, make_user  # noqa: F401  (fixture)
from tests.test_no_dutch_interface import ANONYMOUS_PAGES, PAGES, world  # noqa: F401  (fixture)

VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'track', 'wbr'}
# Public pages a visitor opens (the pages of PAGES that need no login).
PUBLIC = ['/', '/about', '/faq', '/manifest', '/manifest/landen', '/spelregels', '/privacy', '/initiatiefnemer',
          '/achtergrond', '/forum', '{thread}', '/gesprekken', '{issue}', '{issue}?vergroot=feiten',
          '{issue}?vergroot=voorstellen', '{proposal}']
FORBIDDEN_TEXT_COLOUR = re.compile(
    r'(?<![\w-])color\s*:\s*(#aaa\b|#aaaaaa\b|var\(\s*--gu-globe\s*[,)]|var\(\s*--gu-payoff\s*[,)])', re.I)
NO_LABEL_TYPES = {'hidden', 'submit', 'button', 'reset', 'image'}


class Node:
    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent, self.children = tag, dict(attrs), parent, []

    def text(self):
        out = []
        for child in self.children:
            if isinstance(child, str):
                out.append(child)
            elif not child.hidden_self():
                out.append(child.name_from_content())
        return ' '.join(' '.join(out).split())

    def name_from_content(self):
        if self.tag == 'img':
            return self.attrs.get('alt') or ''
        if self.tag == 'svg':
            titles = [c for c in self.children if isinstance(c, Node) and c.tag == 'title']
            return titles[0].text() if titles else ''
        if self.attrs.get('aria-label'):
            return self.attrs['aria-label']
        return self.text()

    def hidden_self(self):
        return 'hidden' in self.attrs or self.attrs.get('aria-hidden') == 'true' or self.tag in ('template', 'script', 'style')

    def hidden(self):
        node = self
        while node is not None:
            if 'hidden' in node.attrs or node.tag == 'template' \
                    or re.search(r'display\s*:\s*none', node.attrs.get('style') or ''):
                return True
            node = node.parent
        return False

    def ancestor(self, tag):
        node = self.parent
        while node is not None:
            if node.tag == tag:
                return node
            node = node.parent
        return None

    def walk(self):
        for child in self.children:
            if isinstance(child, Node):
                yield child
                yield from child.walk()


class Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node('#root', [], None)
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.cur)
        self.cur.children.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, attrs, self.cur))

    def handle_endtag(self, tag):
        node = self.cur
        while node is not None and node.tag != tag:
            node = node.parent
        if node is not None and node.parent is not None:
            self.cur = node.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def parse(page):
    tree = Tree()
    tree.feed(page)
    return tree.root


def problems(page):
    root = parse(page)
    nodes = list(root.walk())
    found = []
    by_id = {n.attrs['id'] for n in nodes if n.attrs.get('id')}

    html = next((n for n in nodes if n.tag == 'html'), None)
    if html is None or not (html.attrs.get('lang') or '').strip():
        found.append('html without lang')

    title = next((n for n in nodes if n.tag == 'title'), None)
    title_text = title.text() if title else ''
    if not title_text or 'MijnApp' in title_text:
        found.append(f'title: {title_text!r}')

    live = [n for n in nodes if not n.hidden()]
    h1 = [n for n in live if n.tag == 'h1']
    if len(h1) != 1:
        found.append(f'{len(h1)} h1')
    previous = 0
    for n in live:
        if re.fullmatch(r'h[1-6]', n.tag):
            level = int(n.tag[1])
            if previous and level > previous + 1:
                found.append(f'heading jump h{previous} to h{level}: {n.text()[:40]!r}')
            previous = level

    body = next((n for n in nodes if n.tag == 'body'), None)
    first = next((c for c in body.children if isinstance(c, Node)), None) if body else None
    target = (first.attrs.get('href') or '')[1:] if first is not None and first.tag == 'a' else ''
    mains = [n for n in nodes if n.tag == 'main']
    if not target or target not in by_id or 'gu-skiplink' not in (first.attrs.get('class') or ''):
        found.append('no skip link as the first element')
    elif not mains or mains[0].attrs.get('id') != target:
        found.append('the skip link does not lead to the main')
    if len(mains) != 1:
        found.append(f'{len(mains)} main')

    labelled = {n.attrs.get('for') for n in nodes if n.tag == 'label'}
    for n in live:
        if n.tag in ('input', 'select', 'textarea'):
            if n.tag == 'input' and (n.attrs.get('type') or 'text').lower() in NO_LABEL_TYPES:
                continue
            ok = (n.attrs.get('id') and n.attrs['id'] in labelled) or n.ancestor('label') is not None \
                or (n.attrs.get('aria-label') or '').strip() or n.attrs.get('aria-labelledby')
            if not ok:
                found.append(f'{n.tag} without label: name={n.attrs.get("name")!r} id={n.attrs.get("id")!r}')
        elif n.tag == 'img' and 'alt' not in n.attrs:
            found.append(f'img without alt: {n.attrs.get("src")!r}')
        elif n.tag == 'button' or (n.tag == 'a' and 'href' in n.attrs):
            name = (n.attrs.get('aria-label') or '').strip() or n.attrs.get('aria-labelledby') \
                or (n.attrs.get('title') or '').strip() or n.text()
            if not name:
                found.append(f'{n.tag} without a name: class={n.attrs.get("class")!r} href={n.attrs.get("href")!r}')
        style = n.attrs.get('style') or ''
        if FORBIDDEN_TEXT_COLOUR.search(style):
            found.append(f'forbidden text colour: {style!r}')
    return found


def _report(found):
    return '\n' + '\n'.join(f'{path}: {problem}' for path, items in found.items() for problem in items)


def _check(client, paths, lang, found):
    for path in paths:
        resp = _req(client, 'GET', path)
        assert resp.status_code == 200, (lang, path, resp.status_code)
        for problem in problems(resp.get_data(as_text=True)):
            found.setdefault(path, []).append(problem)


@pytest.mark.parametrize('lang', ['nl', 'en'])
def test_the_pages_as_an_admin(app, db, world, lang):
    admin, pages = world
    found = {}
    _check(_client(app, admin, lang), pages, lang, found)
    assert found == {}, _report(found)


@pytest.mark.parametrize('lang', ['nl', 'en'])
def test_the_pages_as_a_visitor(app, db, world, lang):
    _admin, pages = world
    paths = {p: pages[PAGES.index(p)] for p in PUBLIC}
    found = {}
    _check(_client(app, None, lang), list(paths.values()) + ANONYMOUS_PAGES, lang, found)
    assert found == {}, _report(found)


# ── The checker itself ───────────────────────────────────────────────────

GOOD = ('<!DOCTYPE html><html lang="nl"><head><title>X · Global Union</title></head><body>'
        '<a href="#inhoud" class="gu-skiplink">Naar de inhoud</a><main id="inhoud"><h1>T</h1><h2>S</h2>'
        '<label for="a">A</label><input id="a"><img src="x" alt=""><button aria-label="Sluiten"><svg></svg></button>'
        '<a href="/x"><svg><title>Profiel</title></svg></a></main></body></html>')


def test_a_good_page_has_no_problems():
    assert problems(GOOD) == []


@pytest.mark.parametrize('change, problem', [
    (('<title>X · Global Union</title>', '<title>MijnApp</title>'), 'title'),
    (('<h1>T</h1>', ''), '0 h1'),
    (('<h2>S</h2>', '<h3>S</h3>'), 'heading jump'),
    (('<a href="#inhoud" class="gu-skiplink">Naar de inhoud</a>', ''), 'no skip link'),
    (('<label for="a">A</label>', ''), 'input without label'),
    (('alt=""', ''), 'img without alt'),
    (('aria-label="Sluiten"', ''), 'button without a name'),
    (('<title>Profiel</title>', ''), 'a without a name'),
    (('lang="nl"', ''), 'html without lang'),
    (('<h2>S</h2>', '<h2 style="color: #aaa">S</h2>'), 'forbidden text colour'),
    (('<h2>S</h2>', '<h2 style="color:var(--gu-globe)">S</h2>'), 'forbidden text colour'),
])
def test_the_checker_finds(change, problem):
    page = GOOD.replace(*change)
    assert any(p.startswith(problem) for p in problems(page)), problems(page)


def test_decoration_in_the_globe_blue_is_allowed():
    assert problems(GOOD.replace('<h2>S</h2>', '<h2>S<span style="background:var(--gu-globe)"></span></h2>')) == []


def test_every_page_has_its_own_title_block():
    """Every template that extends base.html sets its own title, so the
    default of base.html never shows (not every page is rendered above)."""
    import glob
    import os
    root = os.path.join(os.path.dirname(__file__), '..', 'app', 'templates')
    missing = []
    for path in glob.glob(os.path.join(root, '**', '*.html'), recursive=True):
        with open(path, encoding='utf-8') as f:
            source = f.read()
        if re.search(r'{%\s*extends\s+["\']base\.html["\']', source) and not re.search(r'{%\s*block title\s*%}', source):
            missing.append(os.path.relpath(path, root))
    assert missing == []
    with open(os.path.join(root, 'base.html'), encoding='utf-8') as f:
        assert 'MijnApp' not in f.read()
