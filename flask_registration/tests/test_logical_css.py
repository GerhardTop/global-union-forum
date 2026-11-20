"""Multilingual phase 0 step 7: logical CSS, so a right-to-left language is a
setting (dir="rtl" on <html>) and not a rebuild.

Fails when the stylesheets or a template (style attribute or <style> block)
use a physical left/right property: margin-left, padding-right, border-left,
left:/right: for positioning, text-align left/right, float, a corner radius
such as border-top-left-radius, or a four-value margin/padding/border-radius
that differs between left and right. Use the logical form (margin-inline-start,
inset-inline-end, text-align: start, border-start-end-radius, ...). Something
that must stay physical carries the comment "physical on purpose: <reason>"
on the same line.
"""
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), '..', 'app')
CSS_DIR = os.path.join(ROOT, 'static', 'css')
TEMPLATES = os.path.join(ROOT, 'templates')
MARKER = 'physical on purpose'

PHYSICAL = re.compile(
    r"""(?<![-\w])(?:margin|padding|border)-(?:left|right)(?:-[a-z]+)*\s*:"""   # margin-left, border-right-color
    r"""|(?<![-\w])border-(?:top|bottom)-(?:left|right)-radius\s*:"""
    r"""|(?<![-\w])(?:left|right)\s*:"""                                        # left: 0 (position)
    r"""|(?<![-\w])(?:text-align|float|clear)\s*:\s*(?:left|right)\b"""
    r"""|(?<![-\w])transition(?:-property)?\s*:[^;]*(?<![-\w])(?:left|right)\b""")
SHORTHAND = re.compile(r'(?<![-\w])(margin|padding|border-radius)\s*:\s*([^;}"!]+)')


def _asymmetric(prop, value):
    parts = value.split()
    if len(parts) != 4 or '/' in parts:
        return False
    if prop == 'border-radius':                   # top-left top-right bottom-right bottom-left
        return parts[0] != parts[1] or parts[2] != parts[3]
    return parts[1] != parts[3]                   # top right bottom left


def physical_on(text):
    """The physical declarations in a piece of CSS (comments removed)."""
    text = re.sub(r'/\*.*?\*/', ' ', text, flags=re.S)
    found = [m.group(0) for m in PHYSICAL.finditer(text)]
    found += [m.group(0).strip() for m in SHORTHAND.finditer(text) if _asymmetric(m.group(1), m.group(2).strip())]
    return found


def _css_offenders():
    out = []
    for name in sorted(os.listdir(CSS_DIR)):
        if not name.endswith('.css'):
            continue
        with open(os.path.join(CSS_DIR, name), encoding='utf-8') as f:
            text = f.read()
        # Blank comments out but keep the line structure, then check per line.
        bare = re.sub(r'/\*.*?\*/', lambda m: re.sub(r'[^\n]', ' ', m.group(0)), text, flags=re.S)
        for n, (line, raw) in enumerate(zip(bare.split('\n'), text.split('\n')), 1):
            if MARKER not in raw and physical_on(line):
                out.append(f'{name}:{n}: {raw.strip()[:120]}')
    return out


def _template_offenders():
    out = []
    for dirpath, _dirs, files in os.walk(TEMPLATES):
        for name in files:
            if not name.endswith('.html'):
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding='utf-8') as f:
                text = f.read()
            lines = text.split('\n')
            pieces = [(m.start(1), m.end(1), m.group(1)) for m in re.finditer(r'\bstyle="([^"]*)"', text)]
            pieces += [(m.start(1), m.end(1), m.group(1))
                       for m in re.finditer(r'<style\b[^>]*>(.*?)</style>', text, flags=re.S | re.I)]
            for start, end, css in pieces:
                if not physical_on(css):
                    continue
                first, last = text.count('\n', 0, start), text.count('\n', 0, end)
                if not any(MARKER in lines[i] for i in range(first, last + 1)):
                    rel = os.path.relpath(path, TEMPLATES)
                    out.append(f'{rel}:{first + 1}: {", ".join(physical_on(css))}')
    return out


def test_no_physical_left_right_in_the_stylesheets():
    assert _css_offenders() == []


def test_no_physical_left_right_in_templates():
    assert _template_offenders() == []


def test_the_guard_catches_physical_properties():
    for css in ('margin-left: 4px', 'padding-right:0', 'border-left-color: red', 'left: 0',
                'text-align: right', 'float: left', 'border-top-left-radius: 4px',
                'padding: 14px 0 14px 20px', 'border-radius: 0 8px 8px 0', 'transition: left .15s'):
        assert physical_on(css), css
    for css in ('margin-inline-start: 4px', 'inset-inline-end: 0', 'text-align: start', 'padding: 4px 8px',
                'margin: 0 auto', 'border-radius: 8px', 'padding: 1px 2px 3px 2px', 'text-transform: uppercase',
                'border-start-end-radius: 4px', 'transition: inset-inline-start .15s'):
        assert not physical_on(css), css
