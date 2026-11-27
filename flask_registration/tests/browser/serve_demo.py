"""Serve the app on its own SQLite database with the demo content and one
logged-in admin/moderator, for the browser checks in tests/test_mobile_overflow.py
and tests/test_mt_toggle.py. The demo issues and proposals get fake machine
translations ("[EN] ...") in every language, so the pages carry the label.

Usage: python tests/browser/serve_demo.py DB_FILE PORT (from flask_registration/).
GET /_as_admin logs the browser in. Only for tests: never imported by the app.
"""
import os
import sys

sys.path.insert(0, os.getcwd())
db_file, port = sys.argv[1], int(sys.argv[2])
os.environ['DATABASE_URL'] = f'sqlite:///{db_file}'
for key, value in (('SECRET_KEY', 'test-only'), ('RESEND_API_KEY', 'fake'),
                   ('GOOGLE_CLIENT_ID', 'fake'), ('GOOGLE_CLIENT_SECRET', 'fake')):
    os.environ.setdefault(key, value)
# Never the AI, also not with the key the suite sets (conftest: "fake") or one
# from .env: without a key quality._client() makes no client, so a translation
# on request or after saving fails at once instead of waiting on the network
# (a slow answer kept the browser checks from reaching "networkidle").
os.environ['ANTHROPIC_API_KEY'] = ''

import app.quality as quality  # noqa: E402
quality.schedule_judgement = lambda *a, **k: None   # no AI calls

from app import bcrypt, create_app, db  # noqa: E402

app = create_app()
app.config.update(WTF_CSRF_ENABLED=False, RATELIMIT_ENABLED=False)
try:
    from app import limiter
    limiter.enabled = False
except ImportError:
    pass

with app.app_context():
    from app import demo_seed
    from app.models import User
    demo_seed.seed_demo_issues()
    admin = User(username='browser_admin', email='browser_admin@example.com', verified=True, is_admin=True,
                 first_name='Browser', last_name='Admin',
                 password_hash=bcrypt.generate_password_hash('Sterk1!ww').decode())
    admin.set_role('moderator')
    admin.accept_rules()
    db.session.add(admin)
    db.session.commit()

    # Background figures: one chart fact on the first issue, from the OWID
    # fixture (no AI, no network), so the chart page can be checked too.
    import json as _json
    from app import background_figures
    from app.models import Issue
    from app.sources import owid
    _folder = os.path.join(os.getcwd(), 'tests', 'data', 'owid')
    _slug = 'military-spending-as-a-share-of-gdp-sipri'
    with open(os.path.join(_folder, f'{_slug}.csv'), encoding='utf-8') as _f:
        _csv = _f.read()
    with open(os.path.join(_folder, f'{_slug}.metadata.json'), encoding='utf-8') as _f:
        _meta = _json.load(_f)
    _parsed = owid.parse(_csv, _meta, slug=_slug)
    _issue = Issue.query.order_by(Issue.id).first()
    background_figures.publish(background_figures.Context(issue=_issue), background_figures.Outcome(
        status='done', accepted=[background_figures.Accepted(
            slug=_slug, parsed=_parsed, metadata=_meta, keys=['POL', 'FRA', 'DEU', 'NLD', 'ESP', 'USA'],
            average_key=None, year_from=2016, year_to=2025, proposal_ids=[],
            reason='The chart shows how much each country spends on its armed forces compared with its economy.')]))
    db.session.commit()

    # Multilingual phase 2: machine translations of the demo issues and
    # proposals, so the pages carry the label. A fake translator ("[EN] ..."),
    # never the AI.
    from types import SimpleNamespace
    from app import content_jobs

    class _FakeTranslator:
        def __init__(self):
            self.messages = self

        def create(self, **kw):
            prompt = kw['messages'][0]['content']
            language = kw['system'].split(' into ', 1)[1].split('.', 1)[0]
            code = {'English': 'EN', 'Dutch': 'NL', 'German': 'DE', 'Spanish': 'ES', 'French': 'FR'}[language]
            return SimpleNamespace(content=[SimpleNamespace(text=f'[{code}] ' + prompt.split('<source>\n', 1)[1].rsplit('\n</source>', 1)[0])])

    content_jobs.backfill(limit=100000, client=_FakeTranslator())


@app.route('/_ready')
def _ready():
    """Which server this is (its database file): with pytest-xdist several
    demo servers run at once, each on its own port; the fixture waits for
    its own answer, not for any server on that port."""
    return db_file


@app.route('/_as_admin')
def _as_admin():
    from flask import redirect, request, session
    from flask_login import login_user
    login_user(User.query.filter_by(username='browser_admin').first())
    session['lang'] = request.args.get('lang', 'nl')
    return redirect('/')


@app.route('/_chart')
def _chart():
    """The chart page of the demo chart (its id depends on the seed)."""
    from flask import redirect, request, url_for
    from app.models import FactChart
    chart = FactChart.query.order_by(FactChart.id).first()
    return redirect(url_for('issues.chart_page', issue_id=chart.issue_id, post_id=chart.post_id, **request.args))


app.run(port=port, use_reloader=False, threaded=True)
