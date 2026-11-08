"""Meldknop (fundament): een deelnemer meldt een bijdrage of feit met een
verplichte reden. Eén open melding per persoon, niet op een eigen bijdrage,
en de meldknop verschijnt bij andermans bijdrage maar niet bij de eigen."""
import uuid

import pytest
from flask import g
from flask_babel import refresh

from app import bcrypt
from app.models import Post, PostReport, Thread, User


@pytest.fixture(autouse=True)
def _reset_limiter():
    from sqlalchemy.exc import OperationalError
    from app import limiter
    try:
        limiter.reset()
    except (AssertionError, OperationalError):
        pass
    yield


def _user(db):
    name = f'u_{uuid.uuid4().hex[:8]}'
    u = User(username=name, email=f'{name}@example.com', verified=True,
             password_hash=bcrypt.generate_password_hash('Sterk1!ww').decode('utf-8'))
    u.set_role('contributor')
    u.accept_rules()
    db.session.add(u)
    db.session.commit()
    return u


def _thread_with_post(db, author):
    t = Thread(eyebrow_nl='T', eyebrow_en='T', title_prefix_nl='Vraag', title_prefix_en='Question')
    db.session.add(t)
    db.session.flush()
    op = Post(thread_id=t.id, user_id=author.id, author_name='', author_username=author.username,
              body='Een bijdrage.', is_op=True, source_lang='nl')
    db.session.add(op)
    db.session.commit()
    return t, op


def _client(app, user=None, lang='nl'):
    c = app.test_client()
    with c.session_transaction() as s:
        s['lang'] = lang
        if user is not None:
            s['_user_id'] = str(user.id)
            s['_fresh'] = True
    return c


def _req(client, method, path, **kw):
    g.pop('_login_user', None)
    refresh()
    return client.open(path, method=method, **kw)


def test_report_creates_one(app, db):
    author, reporter = _user(db), _user(db)
    t, op = _thread_with_post(db, author)
    r = _req(_client(app, reporter), 'POST', f'/forum/bijdrage/{op.id}/melden',
             data={'reason': 'Niet respectvol.'})
    assert r.status_code == 302
    rows = PostReport.query.filter_by(post_id=op.id).all()
    assert len(rows) == 1
    assert rows[0].reporter_id == reporter.id and rows[0].reason == 'Niet respectvol.'
    assert rows[0].decision is None


def test_reason_is_required(app, db):
    author, reporter = _user(db), _user(db)
    t, op = _thread_with_post(db, author)
    r = _req(_client(app, reporter), 'POST', f'/forum/bijdrage/{op.id}/melden', data={'reason': '   '})
    assert r.status_code == 302
    assert PostReport.query.filter_by(post_id=op.id).count() == 0


def test_cannot_report_own_post(app, db):
    author = _user(db)
    t, op = _thread_with_post(db, author)
    r = _req(_client(app, author), 'POST', f'/forum/bijdrage/{op.id}/melden', data={'reason': 'x'})
    assert r.status_code == 403
    assert PostReport.query.filter_by(post_id=op.id).count() == 0


def test_one_open_report_per_person(app, db):
    author, reporter = _user(db), _user(db)
    t, op = _thread_with_post(db, author)
    for _ in range(2):
        _req(_client(app, reporter), 'POST', f'/forum/bijdrage/{op.id}/melden', data={'reason': 'reden'})
    assert PostReport.query.filter_by(post_id=op.id, reporter_id=reporter.id).count() == 1


def test_report_button_shows_for_others_not_self(app, db):
    author, reporter = _user(db), _user(db)
    t, op = _thread_with_post(db, author)
    seen_by_other = _req(_client(app, reporter), 'GET', f'/forum/{t.id}').get_data(as_text=True)
    assert 'Melden' in seen_by_other
    seen_by_author = _req(_client(app, author), 'GET', f'/forum/{t.id}').get_data(as_text=True)
    assert 'Melden' not in seen_by_author


def test_report_a_fact(app, db):
    from app.models import FACT_KIND
    author, reporter = _user(db), _user(db)
    t, op = _thread_with_post(db, author)
    fact = Post(thread_id=t.id, user_id=author.id, author_name='', author_username=author.username,
                body='Een feit.', source_lang='nl', kind=FACT_KIND, fact_direction='voor',
                fact_source='https://example.org/bron')
    db.session.add(fact)
    db.session.commit()
    r = _req(_client(app, reporter), 'POST', f'/forum/bijdrage/{fact.id}/melden',
             data={'reason': 'de bron klopt niet'})
    assert r.status_code == 302
    assert PostReport.query.filter_by(post_id=fact.id).count() == 1
