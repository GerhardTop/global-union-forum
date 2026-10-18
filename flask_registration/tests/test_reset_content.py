"""
Tests voor het beheer-commando `flask reset-content` (app/cli.py): dry-run
verandert niets, --execute laat alleen admin + demo-accounts en demo-content
staan, en de vangnetten (geen admin in de keep-set, verkeerde bevestiging)
voorkomen dat er iets verdwijnt.

Eigen app met een lege in-memory SQLite-database (create_app() seedt daarin
zelf de demo-accounts en -draden), met foreign keys AAN zodat een verkeerde
verwijdervolgorde de test laat falen.
"""
import os
import sys

import pytest
from sqlalchemy import text

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import config as config_module
from app import bcrypt, create_app, db
from app.models import Post, PostLike, Thread, User
from app.routes.forum import DEMO_USER_EMAILS


@pytest.fixture
def app():
    missing = object()
    orig = {k: config_module.Config.__dict__.get(k, missing)
            for k in ('SQLALCHEMY_DATABASE_URI', 'TESTING')}
    config_module.Config.SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    config_module.Config.TESTING = True
    try:
        flask_app = create_app()
        with flask_app.app_context():
            db.session.execute(text('PRAGMA foreign_keys=ON'))
            db.session.commit()
            assert db.session.execute(text('PRAGMA foreign_keys')).scalar() == 1
            yield flask_app
            db.session.remove()
            db.drop_all()
    finally:
        for k, v in orig.items():
            if v is missing:
                if k in config_module.Config.__dict__:
                    delattr(config_module.Config, k)
            else:
                setattr(config_module.Config, k, v)


def _user(username, **kw):
    u = User(username=username, first_name=username.capitalize(), last_name='Test',
             email=kw.pop('email', f'{username}@example.com'),
             password_hash=bcrypt.generate_password_hash('Sterk1!ww').decode('utf-8'),
             verified=True, **kw)
    db.session.add(u)
    db.session.flush()
    return u


def _post(thread, user, body, parent=None, **kw):
    p = Post(thread_id=thread.id, user_id=user.id if user else None,
             parent_id=parent.id if parent else None,
             author_name=f'{user.first_name} Test' if user else '', author_username=user.username if user else 'x',
             body=body, **kw)
    db.session.add(p)
    db.session.flush()
    return p


@pytest.fixture
def world(app):
    """Admin, moderator, twee gewone leden, een echte draad met geneste
    reacties, een echte reactie in een demo-draad, en likes overal."""
    admin = _user('admin', is_admin=True, is_moderator=True)
    mod = _user('moddy', is_moderator=True)
    anna = _user('anna')
    bram = _user('bram')

    real = Thread(eyebrow_nl='x', eyebrow_en='x', title_prefix_nl='Echt', title_prefix_en='Real')
    db.session.add(real)
    db.session.flush()
    op = _post(real, anna, 'echte opening', is_op=True)
    r1 = _post(real, bram, 'reactie', parent=op)
    r2 = _post(real, anna, 'reactie op reactie', parent=r1)
    _post(real, admin, 'admin in echte draad', parent=r2)

    demo_thread = Thread.query.filter_by(is_demo=True).first()
    demo_post = Post.query.filter_by(thread_id=demo_thread.id, is_demo=True).first()
    in_demo = _post(demo_thread, bram, 'echte reactie in demo-draad', parent=demo_post)
    _post(demo_thread, anna, 'reactie daarop', parent=in_demo)

    db.session.add_all([
        PostLike(post_id=demo_post.id, user_id=admin.id),   # blijft
        PostLike(post_id=demo_post.id, user_id=anna.id),    # weg: gebruiker weg
        PostLike(post_id=r1.id, user_id=admin.id),          # weg: bericht weg
        PostLike(post_id=in_demo.id, user_id=mod.id),       # weg: beide weg
    ])
    db.session.commit()
    return {'admin': admin, 'demo_post': demo_post}


def _counts():
    return (User.query.count(), Thread.query.count(), Post.query.count(), PostLike.query.count())


def _run(app, *args, input=None):
    return app.test_cli_runner().invoke(args=['reset-content', *args], input=input)


class TestDryRun:
    def test_dry_run_changes_nothing_and_reports(self, app, world):
        before = _counts()
        demo_threads = Thread.query.filter_by(is_demo=True).count()
        result = _run(app)
        assert result.exit_code == 0, result.output
        assert _counts() == before
        out = result.output
        assert 'DRY-RUN' in out
        assert 'admin@example.com' in out
        for email in DEMO_USER_EMAILS.values():
            assert email in out
        assert 'gebruikers:              3  (waarvan moderator: moddy@example.com)' in out
        assert 'draden (niet-demo):      1' in out
        assert 'berichten totaal:        6' in out
        assert 'in demo-draden:        2' in out
        assert 'van de admin:          1' in out
        assert 'likes:                   3' in out
        assert Thread.query.filter_by(is_demo=True).count() == demo_threads

    def test_list_users_shows_accounts_to_delete(self, app, world):
        out = _run(app, '--list-users').output
        for email in ('moddy@example.com', 'anna@example.com', 'bram@example.com'):
            assert email in out.split('Te verwijderen accounts:')[1]


class TestExecute:
    def test_only_admin_and_demo_remain(self, app, world):
        demo_posts = Post.query.filter_by(is_demo=True).count()
        demo_threads = Thread.query.filter_by(is_demo=True).count()
        result = _run(app, '--execute', '--yes')
        assert result.exit_code == 0, result.output
        db.session.expire_all()

        emails = {u.email for u in User.query}
        assert emails == {'admin@example.com', *DEMO_USER_EMAILS.values()}
        assert Thread.query.count() == demo_threads == Thread.query.filter_by(is_demo=True).count()
        assert Post.query.count() == demo_posts == Post.query.filter_by(is_demo=True).count()
        likes = PostLike.query.all()
        assert [(l.post_id, l.user_id) for l in likes] == [(world['demo_post'].id, world['admin'].id)]

    def test_confirmation_prompt_must_match(self, app, world):
        before = _counts()
        result = _run(app, '--execute', input='verkeerd\n')
        assert result.exit_code != 0
        assert 'niets verwijderd' in result.output
        assert _counts() == before

    def test_confirmation_prompt_accepts_database_name(self, app, world):
        result = _run(app, '--execute', input=':memory:\n')
        assert result.exit_code == 0, result.output
        assert User.query.count() == 1 + len(DEMO_USER_EMAILS)

    def test_refuses_without_admin_in_keep_set(self, app, world):
        world['admin'].is_admin = False
        db.session.commit()
        before = _counts()
        result = _run(app, '--execute', '--yes')
        assert result.exit_code != 0
        assert 'Geen adminaccount' in result.output
        assert _counts() == before

    def test_admin_email_env_keeps_that_account(self, app, world, monkeypatch):
        import app as app_module
        keeper = _user('gerhard', email='Keeper@Example.com')
        db.session.commit()
        monkeypatch.setattr(app_module, 'ADMIN_EMAIL', 'keeper@example.com')
        result = _run(app, '--execute', '--yes')
        assert result.exit_code == 0, result.output
        assert db.session.get(User, keeper.id) is not None

    def test_rerun_after_execute_is_a_no_op(self, app, world):
        _run(app, '--execute', '--yes')
        result = _run(app)
        assert 'gebruikers:              0' in result.output
        assert 'berichten totaal:        0' in result.output


def test_foreign_keys_are_really_enforced(app, world):
    """Zonder handhaving bewijst de volgorde-test niets: een gebruiker met
    berichten rechtstreeks verwijderen moet hier falen."""
    from sqlalchemy.exc import IntegrityError
    anna = User.query.filter_by(username='anna').one()
    with pytest.raises(IntegrityError):
        db.session.execute(text('DELETE FROM users WHERE id = :id'), {'id': anna.id})
        db.session.flush()
    db.session.rollback()
