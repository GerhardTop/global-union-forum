"""No horizontal overflow at 320px on the nine pages of the step 7 set (home,
forum, a conversation, an issue with its cockpit, a proposal, profile,
moderator overview, manifest, country list) and the chart page of a background figure, in nl, en, de, es and fr (long German compounds, longer Spanish and French phrases).

A real browser lays the pages out: Playwright for Node, against the app on
its own SQLite database with the demo content (tests/browser/). The check:
documentElement.scrollWidth <= 320 with the overflow-x: clip on html and body
lifted (with the clip the overflow is cut off, not gone; it was how home and
profile were 346 and 386 px wide unnoticed).

Skipped when node or the playwright package is not available; set
PLAYWRIGHT_NODE_PATH to the node_modules folder that holds playwright.
"""
import glob
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
# '/_chart' redirects to the chart page of the demo chart (tests/browser/serve_demo.py).
PAGES = ['/', '/forum', '/forum/1', '/vraagstuk/1', '/vraagstuk/1/voorstel/1', '/profile', '/moderator',
         '/manifest', '/manifest/landen', '/_chart', '/_chart?weergave=tabel']


def _playwright_node_path():
    candidates = [os.environ.get('PLAYWRIGHT_NODE_PATH', '')]
    candidates += sorted(glob.glob(os.path.expanduser('~/.npm/_npx/*/node_modules')))
    return next((c for c in candidates if c and os.path.isdir(os.path.join(c, 'playwright'))), None)


def _free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def _wait_for(base, token, proc):
    """Whether our own server (it answers /_ready with its database file)
    answers within a minute; False when it stopped (its port was taken)."""
    for _ in range(120):
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(base + '/_ready', timeout=2) as resp:
                if resp.read().decode() == token:
                    return True
        except OSError:
            pass
        time.sleep(0.5)
    return False


@pytest.fixture(scope='module')
def demo_server(tmp_path_factory):
    node_path = _playwright_node_path()
    if shutil.which('node') is None or node_path is None:
        pytest.skip('node with the playwright package is not available')
    # Every pytest-xdist worker starts its own server on its own free port. The
    # port is free when chosen but taken only after the demo seed, so another
    # worker could take it first: then this server stops and a new port is tried.
    proc, base = None, None
    for _attempt in range(3):
        port = _free_port()
        db_file = tmp_path_factory.mktemp('overflow') / 'demo.sqlite3'
        proc = subprocess.Popen([sys.executable, os.path.join(HERE, 'browser', 'serve_demo.py'), str(db_file),
                                 str(port)], cwd=PROJECT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = f'http://127.0.0.1:{port}'
        if _wait_for(base, str(db_file), proc):
            break
        proc.terminate()
        proc.wait(timeout=10)
    else:
        pytest.fail('the demo server did not start')
    try:
        yield base, node_path
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.mark.parametrize('lang', ['nl', 'en', 'de', 'es', 'fr'])
def test_no_horizontal_overflow_at_320px(demo_server, lang):
    base, node_path = demo_server
    run = subprocess.run(['node', os.path.join(HERE, 'browser', 'overflow.js'), base, lang, *PAGES],
                         env={**os.environ, 'NODE_PATH': node_path}, capture_output=True, text=True, timeout=180)
    assert run.returncode == 0, run.stderr
    result = json.loads(run.stdout)
    assert {path: r['status'] for path, r in result.items()} == {path: 200 for path in PAGES}
    too_wide = {path: r for path, r in result.items() if r['html'] > 320 or r['body'] > 320}
    assert too_wide == {}
