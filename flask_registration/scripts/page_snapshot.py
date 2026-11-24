"""Page snapshot: render every demo page and compare two versions of the code.

For every change that should make no visible difference (a refactor, a query
fix, a migration): record the pages with the code before the change, then
compare with the code after it. Run from flask_registration/:

    python scripts/page_snapshot.py opnemen      # before the change
    python scripts/page_snapshot.py vergelijken  # after the change

The pages: home, the forum, the conversations list, every conversation, every
issue (also with the proposals and the facts enlarged) and every proposal of
the demo content, as a visitor and as a member, in every enabled language
(200 pages with the current demo and five languages).

opnemen builds a fresh SQLite database with the demo content (demo_seed) and
fake machine translations ("[German] ...", never the AI), keeps a copy of it
and writes the pages to the snapshot folder (outside git; default
$TMPDIR/guf-page-snapshot, or --map). vergelijken renders the pages again
with the current code, on a fresh copy of exactly that database, and prints
per page "gelijk" or the difference (the first lines of a diff); exit code 1
when a page differs or is missing. Nonces and asset versions are left out of
the comparison.

Record and compare shortly after each other: the pages show times relative
to now ("2 dagen geleden", the deadline of a vote); after a few hours such a
line can differ. Record again then.
"""
import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # flask_registration/
REPO = os.path.dirname(ROOT)                                          # the git repository
DEFAULT_DIR = os.path.join(tempfile.gettempdir(), 'guf-page-snapshot')
MEMBER = 'snapshot_member'


def _app(db_file):
    """The app on db_file, with test settings and without any AI or mail.
    Once per process: config reads DATABASE_URL when it is first imported."""
    os.environ['DATABASE_URL'] = f'sqlite:///{db_file}'
    for key, value in (('SECRET_KEY', 'page-snapshot'), ('RESEND_API_KEY', 'fake'), ('ANTHROPIC_API_KEY', ''),
                       ('GOOGLE_CLIENT_ID', 'fake'), ('GOOGLE_CLIENT_SECRET', 'fake')):
        os.environ[key] = value
    sys.path.insert(0, ROOT)
    os.chdir(ROOT)
    import app.quality as quality
    quality.schedule_judgement = lambda *a, **k: None   # no AI judgement of new posts
    from app import create_app
    app = create_app()
    app.config.update(WTF_CSRF_ENABLED=False, RATELIMIT_ENABLED=False)
    return app


def _build(db_file):
    """A fresh database: the demo content, one member, fake translations."""
    from types import SimpleNamespace
    app = _app(db_file)
    from app import bcrypt, content_jobs, db, demo_seed
    from app.models import User

    class FakeTranslator:
        def __init__(self):
            self.messages = self

        def create(self, **kw):
            prompt = kw['messages'][0]['content']
            language = prompt.split(' to ', 1)[1].split('.', 1)[0]
            return SimpleNamespace(content=[SimpleNamespace(text=f'[{language}] ' + prompt.split('\n\n', 1)[1])])

    with app.app_context():
        demo_seed.seed_demo_issues()
        member = User(username=MEMBER, email=f'{MEMBER}@example.com', verified=True, first_name='Snap',
                      last_name='Shot', password_hash=bcrypt.generate_password_hash('Sterk1!ww').decode())
        member.set_role('voter')
        member.accept_rules()
        db.session.add(member)
        db.session.commit()
        content_jobs.backfill(limit=100000, client=FakeTranslator())


def _paths(app):
    from app.models import Issue, IssueProposal, Thread
    with app.app_context():
        paths = ['/', '/forum', '/gesprekken']
        paths += [f'/forum/{t.id}' for t in Thread.query.order_by(Thread.id)]
        for issue in Issue.query.order_by(Issue.id):
            paths += [f'/vraagstuk/{issue.id}', f'/vraagstuk/{issue.id}?vergroot=voorstellen',
                      f'/vraagstuk/{issue.id}?vergroot=feiten']
        paths += [f'/vraagstuk/{p.issue_id}/voorstel/{p.id}' for p in IssueProposal.query.order_by(IssueProposal.id)]
    return paths


def _normalize(html):
    html = re.sub(r'nonce="[^"]+"|nonce-[A-Za-z0-9+/=_-]+', 'NONCE', html)
    return re.sub(r'\?v=[0-9a-f]+', '?v=X', html)


def _render(db_file):
    """{name: (status, html)} for every page, as a visitor and as the member,
    in every enabled language."""
    app = _app(db_file)
    from app import i18n
    from app.models import User
    with app.app_context():
        member_id = User.query.filter_by(username=MEMBER).one().id
    paths = _paths(app)
    pages = {}
    for who in ('bezoeker', 'lid'):
        for lang in i18n.enabled_codes():
            client = app.test_client()
            with client.session_transaction() as session:
                session['lang'] = lang
                if who == 'lid':
                    session['_user_id'] = str(member_id)
                    session['_fresh'] = True
            for path in paths:
                resp = client.get(path)
                name = f'{who}_{lang}{re.sub(r"[/?=]", "_", path)}.html'
                pages[name] = (resp.status_code, _normalize(resp.get_data(as_text=True)))
    return pages


def _working_copy(folder):
    work = os.path.join(tempfile.mkdtemp(prefix='guf-snapshot-'), 'db.sqlite3')
    shutil.copy(os.path.join(folder, 'demo.sqlite3'), work)
    return work


def record(folder):
    if os.path.exists(folder):
        shutil.rmtree(folder)
    os.makedirs(os.path.join(folder, 'pages'))
    # The database in its own process (one app per process, see _app); the
    # pages on a copy of it, since rendering as the member writes read marks.
    subprocess.run([sys.executable, os.path.abspath(__file__), '_bouw', os.path.join(folder, 'demo.sqlite3')],
                   check=True)
    pages = _render(_working_copy(folder))
    for name, (_status, html) in pages.items():
        with open(os.path.join(folder, 'pages', name), 'w', encoding='utf-8') as f:
            f.write(html)
    with open(os.path.join(folder, 'status.json'), 'w') as f:
        json.dump({name: status for name, (status, _html) in pages.items()}, f, indent=1, sort_keys=True)
    not_ok = sorted(name for name, (status, _html) in pages.items() if status != 200)
    print(f'Opgenomen: {len(pages)} pagina\'s in {folder}' + (f'; niet 200: {not_ok}' if not_ok else ''))
    return 0


def compare(folder, diff_lines):
    if not os.path.exists(os.path.join(folder, 'demo.sqlite3')):
        print(f'Geen opname in {folder}: eerst "opnemen".', file=sys.stderr)
        return 2
    with open(os.path.join(folder, 'status.json')) as f:
        recorded_status = json.load(f)
    pages = _render(_working_copy(folder))
    differing = 0
    for name in sorted(set(recorded_status) | set(pages)):
        if name not in pages:
            print(f'ONTBREEKT  {name} (alleen in de opname)')
            differing += 1
            continue
        if name not in recorded_status:
            print(f'NIEUW      {name} (niet in de opname)')
            differing += 1
            continue
        status, html = pages[name]
        with open(os.path.join(folder, 'pages', name), encoding='utf-8') as f:
            before = f.read()
        if status == recorded_status[name] and html == before:
            print(f'gelijk     {name}')
            continue
        differing += 1
        print(f'VERSCHIL   {name}' + (f' (status {recorded_status[name]} -> {status})'
                                       if status != recorded_status[name] else ''))
        diff = difflib.unified_diff(before.splitlines(), html.splitlines(), 'opname', 'nu', lineterm='', n=1)
        for line in list(diff)[:diff_lines]:
            print(f'    {line[:200]}')
    print(f'\n{len(pages) - differing} van {len(set(recorded_status) | set(pages))} pagina\'s gelijk'
          + (f', {differing} verschillend' if differing else ''))
    return 1 if differing else 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ['_bouw']:   # internal: build the database (record runs it in its own process)
        _build(argv[1])
        return 0
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('stand', choices=('opnemen', 'vergelijken'))
    parser.add_argument('--map', default=DEFAULT_DIR, help=f'snapshot folder, outside git (default {DEFAULT_DIR})')
    parser.add_argument('--regels', type=int, default=20, help='diff lines per differing page (default 20)')
    args = parser.parse_args(argv)
    folder = os.path.abspath(args.map)
    if os.path.commonpath([folder, REPO]) == REPO:
        parser.error('the snapshot folder must be outside the git repository')
    return record(folder) if args.stand == 'opnemen' else compare(folder, args.regels)


if __name__ == '__main__':
    sys.exit(main())
