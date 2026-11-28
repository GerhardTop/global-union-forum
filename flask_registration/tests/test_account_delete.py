"""
Deleting your own account (POST /account/verwijderen) anonymises it instead of
wiping it, as the privacy statement says: the row stays without anything
personal, so posts, likes and votes keep working under "@verwijderd"; name,
email, LinkedIn, password, the answers of "Who votes?" and users.country go.
Login is impossible and the username and email address are free again. The
text in the danger zone says so, in all five languages.

Shared conftest app (CSRF off).
"""
import importlib
import uuid

import pytest
from flask import g
from flask_babel import refresh

from app import bcrypt
from app.models import (DELETED_HANDLE, IssueProposalVote, ParticipantProfile, Post, PostLike,
                        Thread, User)
from tests.test_delete_issue import _clean_session, _cleanup  # noqa: F401  (autouse fixture)
from tests.test_issue_polish import _client, _req, make_user  # noqa: F401  (fixture)

social_module = importlib.import_module('app.routes.social')
PASSWORD = 'Sterk1!ww'
NEW_TEXT = {
    'nl': 'Dit verwijdert je account en je persoonsgegevens. Je bijdragen blijven staan, zonder je naam, '
          'zodat de gesprekken leesbaar blijven. Dit kan niet ongedaan worden gemaakt.',
    'en': 'This deletes your account and your personal data. Your contributions stay, without your name, '
          'so the conversations remain readable. This cannot be undone.',
    'de': 'Damit löschst du dein Konto und deine persönlichen Daten.',
    'es': 'Esto elimina tu cuenta y tus datos personales.',
    'fr': 'Cela supprime votre compte et vos données personnelles.',
}
OLD_TEXT = ('This permanently deletes', 'all your forum posts', 'all associated data',
            'al je forumberichten', 'alle bijbehorende gegevens')


def _fresh(client, method, path, **kw):
    g.pop('_login_user', None)
    refresh()
    return client.open(path, method=method, **kw)


@pytest.fixture
def world(db, make_user):
    """A member with a name, LinkedIn, answers, a post, a like on someone
    else's post, a like from someone else on theirs and a vote."""
    from app import issue_proposals as ip, issues
    name = f'del_{uuid.uuid4().hex[:8]}'
    user = User(username=name, email=f'{name}@example.com', verified=True, first_name='Dora', last_name='Delete',
                linkedin_url='https://www.linkedin.com/in/dora', show_real_name=True, country='NL',
                password_hash=bcrypt.generate_password_hash(PASSWORD).decode('utf-8'))
    user.set_role('voter')
    user.accept_rules()
    db.session.add(user)
    db.session.commit()
    db.session.add(ParticipantProfile(user_id=user.id, birth_year=1970, gender='f', education='higher'))
    other = make_user()
    thread = Thread(eyebrow='T', title_prefix=f'Del {name}')
    db.session.add(thread)
    db.session.commit()
    own = Post(thread_id=thread.id, user_id=user.id, author_name='Dora Delete', author_username=name,
               body=f'Bericht van {name}', source_lang='nl')
    theirs = Post(thread_id=thread.id, user_id=other.id, author_name='', author_username=other.username,
                  body=f'Ander bericht {name}', source_lang='nl')
    db.session.add_all([own, theirs])
    db.session.commit()
    db.session.add_all([PostLike(post_id=theirs.id, user_id=user.id), PostLike(post_id=own.id, user_id=other.id)])
    author = make_user()
    issue = issues.create({'title': f'Q{name}', 'intro': 'I', 'kind': 'online', 'support_threshold': 1}, author,
                          lang='nl')
    db.session.flush()
    proposal = ip.create(issue, author, f'S{name}', 'E', lang='nl')
    db.session.flush()
    ip.support(proposal, make_user('voter'))
    issue.phase = 'stemmen'
    db.session.commit()
    yield {'user': user, 'name': name, 'own': own.id, 'theirs': theirs.id, 'thread': thread.id,
           'issue': issue, 'proposal': proposal}
    db.session.rollback()
    _cleanup(issue.id)
    PostLike.query.filter(PostLike.post_id.in_([own.id, theirs.id])).delete(synchronize_session=False)
    Post.query.filter_by(thread_id=thread.id).delete(synchronize_session=False)
    Thread.query.filter_by(id=thread.id).delete(synchronize_session=False)
    ParticipantProfile.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    User.query.filter(User.id == user.id).delete(synchronize_session=False)
    User.query.filter(User.email == f'{name}@example.com').delete(synchronize_session=False)
    db.session.commit()


def test_self_delete_anonymises(app, db, world):
    user, name = world['user'], world['name']
    uid = user.id
    client = _client(app, user)
    path = f'/vraagstuk/{world["issue"].id}/voorstel/{world["proposal"].id}'
    _req(client, 'POST', path + '/stemmen', data={'choice': 'voor'})
    assert IssueProposalVote.query.filter_by(proposal_id=world['proposal'].id, user_id=uid).count() == 1

    r = _req(client, 'POST', '/account/verwijderen')
    assert r.status_code == 302
    db.session.expire_all()
    u = db.session.get(User, uid)
    # The row stays, anonymised.
    assert u is not None and u.deleted_at is not None and u.is_deleted
    assert u.username.startswith(DELETED_HANDLE) and u.username != name
    assert u.email != f'{name}@example.com' and name not in u.email
    assert (u.first_name, u.last_name, u.linkedin_url, u.country) == (None, None, None, None)
    assert u.show_real_name is False
    assert ParticipantProfile.query.filter_by(user_id=uid).first() is None
    # Posts, likes and the vote stay, under the anonymised author.
    own = db.session.get(Post, world['own'])
    assert own is not None and own.user_id == uid and own.author_username == DELETED_HANDLE
    assert own.public_author_name() == '@' + DELETED_HANDLE and 'Dora' not in own.public_author_name()
    assert PostLike.query.filter_by(post_id=world['theirs'], user_id=uid).count() == 1
    assert PostLike.query.filter_by(post_id=world['own']).count() == 1
    assert IssueProposalVote.query.filter_by(proposal_id=world['proposal'].id).count() == 1
    # Logged out, with the existing message.
    page = _req(client, 'GET', '/').get_data(as_text=True)
    assert 'Je account is verwijderd.' in page or 'Your account has been deleted.' in page


def test_deleted_account_cannot_log_in_and_name_and_email_are_free(app, db, world, monkeypatch):
    user, name = world['user'], world['name']
    email = f'{name}@example.com'
    _req(_client(app, user), 'POST', '/account/verwijderen')
    db.session.expire_all()

    client = app.test_client()
    _fresh(client, 'POST', '/login', data={'email': email, 'password': PASSWORD})
    with client.session_transaction() as sess:
        assert '_user_id' not in sess

    monkeypatch.setattr(social_module, '_send_verify_email', lambda *a, **k: None)
    _fresh(client, 'POST', '/aanmelden', data={
        'accept_rules': '1', 'username': name, 'email': email,
        'password': PASSWORD, 'password_confirm': PASSWORD, 'age_ok': '1', 'age_confirmed': '1'})
    db.session.expire_all()
    fresh = User.query.filter_by(email=email).one()
    assert fresh.id != user.id and fresh.username == name and fresh.deleted_at is None


def test_admin_cannot_delete_itself(app, db, make_user):
    admin = make_user(admin=True)
    r = _req(_client(app, admin), 'POST', '/account/verwijderen')
    assert r.status_code == 302 and r.headers['Location'].endswith('/profile')
    db.session.expire_all()
    assert db.session.get(User, admin.id).deleted_at is None


@pytest.mark.parametrize('lang', ['nl', 'en', 'de', 'es', 'fr'])
def test_danger_zone_text(app, make_user, lang):
    page = _req(_client(app, make_user(), lang), 'GET', '/profile').get_data(as_text=True)
    assert page.count(NEW_TEXT[lang]) == 2  # the danger zone and the confirmation window
    for old in OLD_TEXT:
        assert old not in page
