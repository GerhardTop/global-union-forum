"""
Accessibility step 1 (docs/toegankelijkheid.md, WCAG 2.2 AA): the colour
pairs of the decision, computed from the tokens in colors_and_type.css. Fails
when someone later sets a token back to a colour without enough contrast.
"""
import os
import re

import pytest

CSS = os.path.join(os.path.dirname(__file__), '..', 'app', 'static', 'css', 'colors_and_type.css')
WHITE, GREY = '#ffffff', '#f1f4f7'


def _tokens():
    with open(CSS, encoding='utf-8') as f:
        root = f.read().split(':root', 1)[1].split('\n}', 1)[0]
    return dict(re.findall(r'(--gu-[\w-]+):\s*([^;]+);', root))


def _resolve(name, tokens):
    value = tokens[name].strip()
    for _ in range(10):
        m = re.fullmatch(r'var\((--gu-[\w-]+)\)', value)
        if not m:
            break
        value = tokens[m.group(1)].strip()
    assert re.fullmatch(r'#[0-9a-fA-F]{6}', value), (name, value)
    return value


def _luminance(hex_colour):
    channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a, b):
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_the_formula():
    assert contrast('#000000', '#ffffff') == pytest.approx(21)
    assert contrast('#ffffff', '#ffffff') == pytest.approx(1)


@pytest.mark.parametrize('foreground, background, minimum', [
    (WHITE, '--gu-action', 4.5),          # white text on the main button
    (WHITE, '--gu-action-hover', 4.5),
    ('--gu-link', WHITE, 4.5),
    ('--gu-link', GREY, 4.5),
    ('--gu-link-hover', WHITE, 4.5),
    ('--gu-link-hover', GREY, 4.5),
    ('--gu-fg-3', WHITE, 4.5),            # hints, eyebrows, secondary text
    ('--gu-fg-3', GREY, 4.5),
    ('--gu-border-field', WHITE, 3),      # borders of fields and white buttons
    ('--gu-action', WHITE, 3),            # the focus line
])
def test_the_pairs_of_the_decision(foreground, background, minimum):
    tokens = _tokens()
    fg = foreground if foreground.startswith('#') else _resolve(foreground, tokens)
    bg = background if background.startswith('#') else _resolve(background, tokens)
    assert contrast(fg, bg) >= minimum, (foreground, background, round(contrast(fg, bg), 2))


def test_the_values_of_the_decision():
    tokens = _tokens()
    assert {name: _resolve(name, tokens).lower() for name in
            ('--gu-action', '--gu-action-hover', '--gu-link', '--gu-link-hover', '--gu-fg-3', '--gu-border-field')} == {
        '--gu-action': '#0077cc', '--gu-action-hover': '#005ea3', '--gu-link': '#005ea3',
        '--gu-link-hover': '#004a80', '--gu-fg-3': '#5e6877', '--gu-border-field': '#8a95a3'}
    # The payoff grey and the globe blue stay, as accents only.
    assert _resolve('--gu-payoff', tokens).lower() == '#aaaaaa'
    assert _resolve('--gu-globe', tokens).lower() == '#3ab0f0'
    assert contrast(_resolve('--gu-globe', tokens), WHITE) < 4.5   # why it is never text
