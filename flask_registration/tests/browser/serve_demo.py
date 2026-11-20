"""Serve the app on its own SQLite database with the demo content and one
logged-in admin/moderator, for the browser checks in tests/test_mobile_overflow.py.

Usage: python tests/browser/serve_demo.py DB_FILE PORT (from flask_registration/).
GET /_as_admin logs the browser in. Only for tests: never imported by the app.
"""
import os
import sys

sys.path.insert(0, os.getcwd())
db_file, port = sys.argv[1], int(sys.argv[2])
os.environ['DATABASE_URL'] = f'sqlite:///{db_file}'
for key, value in (('SECRET_KEY', 'test-only'), ('RESEND_API_KEY', 'fake'), ('ANTHROPIC_API_KEY', ''),
                   ('GOOGLE_CLIENT_ID', 'fake'), ('GOOGLE_CLIENT_SECRET', 'fake')):
    os.environ.setdefault(key, value)

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


@app.route('/_as_admin')
def _as_admin():
    from flask import redirect, request, session
    from flask_login import login_user
    login_user(User.query.filter_by(username='browser_admin').first())
    session['lang'] = request.args.get('lang', 'nl')
    return redirect('/')


app.run(port=port, use_reloader=False, threaded=True)
