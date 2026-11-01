"""
Herontwerp, bouwstap 3: feiten en onderzoek (app/facts.py, de route voor
betwisten in app/routes/forum.py en de feitenpagina in app/routes/issues.py).

Dekt: geen nuttig meer (polijstronde P2, vervangen door het kwaliteitsmerk);
geen eens/oneens op feiten; betwisten
met een reden (het feit blijft staan), één openstaand bezwaar per persoon,
het label alleen voor moderators, de moderatorweergave en afhandelen; de
feitenpagina met filter, sorteren, "toon meer" en toevoegen (ook bij een
vraagstuk zonder voorstel); de sortering zelf; opruimen bij verwijderen.

Gedeelde conftest-app (CSRF uit).
"""
import uuid
from datetime import datetime, timedelta

import pytest
from flask import g
from flask_babel import refresh

from app import bcrypt, facts, issues
from app.models import FACT_KIND, FactDispute, FactUsefulVote, Issue, Post, Thread, User
# Autouse: de limiter staat in de testapp aan en telt per gebruiker-id; SQLite
# hergebruikt id's, dus zonder reset erft een test de teller van een vorige.
from tests.test_forum_conduct import _fresh_rate_limits  # noqa: F401


@pytest.fixture
def make_user(db):
    made = []

    def _make(role='contributor', rules=True):
        name = f'f_{uuid.uuid4().hex[:8]}'
        u = User(username=name, email=f'{name}@example.com', verified=True,
                 password_hash=bcrypt.generate_password_hash('Sterk1!ww').decode('utf-8'))
        if rules:
            u.set_role(role)
            u.accept_rules()
        db.session.add(u)
        db.session.commit()
        made.append(u.id)
        return u

    yield _make
    from app import quality
    from app.routes.forum import _delete_thread
    for tid in [t.id for t in Thread.query.join(Post).filter(Post.user_id.in_(made)).distinct()]:
        _delete_thread(db.session.get(Thread, tid))
    Issue.query.filter(Issue.created_by_id.in_(made)).delete(synchronize_session=False)
    db.session.commit()
    for uid in made:
        quality.detach_user(uid)
        db.session.delete(db.session.get(User, uid))
    db.session.commit()
    # De bulk-deletes laten objecten in de identity map achter; SQLite
    # hergebruikt id's, dus een volgende test zou ermee botsen.
    db.session.expunge_all()


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


def _issue(db, user, title='Wat doen we met de haven?'):
    issue = issues.create({'title': title, 'intro': 'Toelichting.', 'kind': 'online'}, user)
    db.session.commit()
    return issue


def _fact(db, issue, user, body, direction='voor', source='https://example.org', when=None):
    f = Post(thread_id=issue.thread_id, user_id=user.id, author_name='', author_username=user.username,
             body=body, source_lang='nl', kind=FACT_KIND, fact_direction=direction, fact_source=source)
    if when is not None:
        f.created_at = when
    db.session.add(f)
    db.session.commit()
    return f


# ── Nuttig is weg (polijstronde P2) ──────────────────────────────────────

def test_useful_route_is_gone(app, db, make_user):
    author, reader = make_user(), make_user()
    issue = _issue(db, author)
    fact = _fact(db, issue, author, 'Een feit')
    assert _req(_client(app, reader), 'POST', f'/forum/feit/{fact.id}/nuttig').status_code in (404, 405)
    page = _req(_client(app, reader), 'GET', f'/vraagstuk/{issue.id}/feiten').get_data(as_text=True)
    assert 'Nuttig' not in page and 'nuttig' not in page.split('<main')[1]


def test_no_agree_disagree_on_facts(app, db, make_user):
    author, reader = make_user(), make_user()
    issue = _issue(db, author)
    fact = _fact(db, issue, author, 'Geen eens hier')
    page = _req(_client(app, reader), 'GET', f'/vraagstuk/{issue.id}/feiten').get_data(as_text=True)
    item = page.split(f'id="post-{fact.id}"')[1].split('</li>')[0]
    assert 'Betwisten' in item and 'gu-stance' not in item
    assert _req(_client(app, reader), 'POST', f'/forum/stance/{fact.id}', data={'stance': 'eens'}).status_code == 404


# ── Betwisten ────────────────────────────────────────────────────────────

def test_dispute_goes_to_moderators_and_fact_stays(app, db, make_user):
    author, reader, mod = make_user(), make_user(), make_user('moderator')
    issue = _issue(db, author)
    fact = _fact(db, issue, author, 'Een twijfelachtig feit')
    r = _req(_client(app, reader), 'POST', f'/forum/feit/{fact.id}/betwisten',
             data={'reason': 'klopt_niet', 'note': 'De cijfers zijn van 2010.'})
    assert r.status_code == 302
    row = FactDispute.query.filter_by(fact_post_id=fact.id).one()
    assert (row.reason, row.note, row.status, row.user_id) == ('klopt_niet', 'De cijfers zijn van 2010.', 'open', reader.id)
    # Een tweede keer: geen tweede bezwaar.
    _req(_client(app, reader), 'POST', f'/forum/feit/{fact.id}/betwisten', data={'reason': 'misleidend'})
    assert FactDispute.query.filter_by(fact_post_id=fact.id).count() == 1

    # Het feit blijft voor iedereen staan; het label ziet alleen een moderator.
    anon = _req(_client(app), 'GET', f'/vraagstuk/{issue.id}/feiten').get_data(as_text=True)
    assert 'Een twijfelachtig feit' in anon and '>Betwist<' not in anon
    mine = _req(_client(app, reader), 'GET', f'/vraagstuk/{issue.id}/feiten').get_data(as_text=True)
    assert 'Je hebt dit feit betwist' in mine and '>Betwist<' not in mine
    as_mod = _req(_client(app, mod), 'GET', f'/vraagstuk/{issue.id}/feiten').get_data(as_text=True)
    assert '>Betwist<' in as_mod

    dash = _req(_client(app, mod), 'GET', '/moderator').get_data(as_text=True)
    assert 'Betwiste feiten (' in dash and 'Een twijfelachtig feit' in dash and 'De cijfers zijn van 2010.' in dash
    assert f'/forum/feit/betwisting/{row.id}/afhandelen' in dash

    r = _req(_client(app, mod), 'POST', f'/forum/feit/betwisting/{row.id}/afhandelen')
    assert r.status_code == 302
    db.session.refresh(row)
    assert row.status == 'resolved' and row.resolved_by_id == mod.id and row.resolved_at is not None
    as_mod = _req(_client(app, mod), 'GET', f'/vraagstuk/{issue.id}/feiten').get_data(as_text=True)
    assert '>Betwist<' not in as_mod


def test_dispute_rules(app, db, make_user):
    author, reader, mod = make_user(), make_user(), make_user('moderator')
    issue = _issue(db, author)
    fact = _fact(db, issue, author, 'Feit')
    assert _req(_client(app, author), 'POST', f'/forum/feit/{fact.id}/betwisten',
                data={'reason': 'geen_bron'}).status_code == 403
    _req(_client(app, reader), 'POST', f'/forum/feit/{fact.id}/betwisten', data={'reason': 'onzin'})
    _req(_client(app, reader), 'POST', f'/forum/feit/{fact.id}/betwisten', data={'reason': 'geen_bron', 'note': 'x' * 501})
    assert FactDispute.query.filter_by(fact_post_id=fact.id).count() == 0
    # Een gewone gebruiker kan niet afhandelen; een moderator niet bij zijn eigen feit.
    own = _fact(db, issue, mod, 'Feit van de moderator')
    _req(_client(app, reader), 'POST', f'/forum/feit/{own.id}/betwisten', data={'reason': 'geen_bron'})
    row = FactDispute.query.filter_by(fact_post_id=own.id).one()
    assert _req(_client(app, reader), 'POST', f'/forum/feit/betwisting/{row.id}/afhandelen').status_code == 403
    assert _req(_client(app, mod), 'POST', f'/forum/feit/betwisting/{row.id}/afhandelen').status_code == 403


# ── De feitenpagina ──────────────────────────────────────────────────────

def test_facts_page_filter_sort_and_more(app, db, make_user):
    author, reader, other = make_user(), make_user(), make_user()
    issue = _issue(db, author)
    base = datetime.utcnow() - timedelta(days=1)
    plain = _fact(db, issue, author, 'Zonder link', 'tegen', 'Krant van gisteren', when=base + timedelta(hours=3))
    old_link = _fact(db, issue, author, 'Oud met link', 'voor', 'https://a.example', when=base)
    new_link = _fact(db, issue, author, 'Nieuw met link', 'neutraal', 'https://b.example', when=base + timedelta(hours=2))
    # Polijstronde P2: het kwaliteitsmerk gaat voor alles; dan een link als bron.
    from app.models import QualityAward
    db.session.add(QualityAward(post_id=plain.id, status='afgewezen', source='ai', fact_mark=True))
    db.session.commit()

    def order(**args):
        q = '&'.join(f'{k}={v}' for k, v in args.items())
        page = _req(_client(app, reader), 'GET', f'/vraagstuk/{issue.id}/feiten?{q}').get_data(as_text=True)
        names = ['Zonder link', 'Oud met link', 'Nieuw met link']
        return sorted((n for n in names if n in page), key=page.index)

    # Kwaliteit eerst: het merk boven, dan een link als bron, daarbinnen de nieuwste.
    assert order() == ['Zonder link', 'Nieuw met link', 'Oud met link']
    assert order(sorteer='nieuwste') == ['Zonder link', 'Nieuw met link', 'Oud met link']
    assert order(sorteer='nuttig') == order()  # bestaat niet meer: valt terug op kwaliteit eerst
    assert order(richting='tegen') == ['Zonder link']

    for i in range(10):
        _fact(db, issue, author, f'Extra feit {i:02d}', when=base - timedelta(hours=i + 1))
    page = _req(_client(app, reader), 'GET', f'/vraagstuk/{issue.id}/feiten').get_data(as_text=True)
    assert page.count('class="gu-fact gu-fact--') == 10 and 'Toon meer (3)' in page
    page = _req(_client(app, reader), 'GET', f'/vraagstuk/{issue.id}/feiten?n=20').get_data(as_text=True)
    assert page.count('class="gu-fact gu-fact--') == 13 and 'Toon meer' not in page


def test_add_fact_from_facts_page_on_issue_without_proposal(app, db, make_user):
    author, reader = make_user(), make_user()
    issue = _issue(db, author)
    page = _req(_client(app, reader), 'GET', f'/vraagstuk/{issue.id}/feiten').get_data(as_text=True)
    assert 'Feit, onderzoek of media toevoegen' in page and f'name="return_to" value="{issue.id}"' in page
    r = _req(_client(app, reader), 'POST', f'/forum/{issue.thread_id}/feiten',
             data={'text': 'Nieuw onderzoek', 'direction': 'voor', 'source': 'https://onderzoek.example',
                   'return_to': str(issue.id)})
    fact = Post.query.filter_by(body='Nieuw onderzoek').one()
    assert fact.kind == FACT_KIND and fact.thread_id == issue.thread_id
    assert r.headers['Location'].endswith(f'/vraagstuk/{issue.id}/feiten#post-{fact.id}')
    # Fouten: terug naar de feitenpagina met de melding.
    r = _req(_client(app, reader), 'POST', f'/forum/{issue.thread_id}/feiten',
             data={'text': '', 'direction': 'voor', 'source': 'x', 'return_to': str(issue.id)})
    assert r.status_code == 303 and f'/vraagstuk/{issue.id}/feiten' in r.headers['Location']


def test_no_new_facts_after_the_facts_phase(app, db, make_user):
    author, reader = make_user(), make_user()
    issue = _issue(db, author)
    issue.phase = 'bijstellen'
    db.session.commit()
    r = _req(_client(app, reader), 'POST', f'/forum/{issue.thread_id}/feiten',
             data={'text': 'Te laat', 'direction': 'voor', 'source': 'https://x.example'})
    assert r.status_code == 403
    page = _req(_client(app, reader), 'GET', f'/vraagstuk/{issue.id}/feiten').get_data(as_text=True)
    assert 'Feit, onderzoek of media toevoegen' not in page and 'de feiten staan vast' in page


def test_facts_page_in_english(app, db, make_user):
    author = make_user()
    issue = _issue(db, author)
    _fact(db, issue, author, 'A fact', 'tegen')
    page = _req(_client(app, lang='en'), 'GET', f'/vraagstuk/{issue.id}/feiten').get_data(as_text=True)
    for text in ('Facts, research and media', 'Argues against', 'Quality first', 'Newest'):
        assert text in page
    assert 'Most useful' not in page


def test_facts_page_404(app):
    assert _req(_client(app), 'GET', '/vraagstuk/999999/feiten').status_code == 404


def test_cockpit_links_to_facts_page_with_counts(app, db, make_user):
    author, reader = make_user(), make_user()
    issue = _issue(db, author)
    _fact(db, issue, author, 'Feit in de cockpit')
    body = _req(_client(app, reader), 'GET', f'/vraagstuk/{issue.id}').get_data(as_text=True)
    panel = body.split('id="panel-facts"')[1].split('id="panel-discussion"')[0]
    assert f'/vraagstuk/{issue.id}/feiten' in panel and 'Feit in de cockpit' in panel
    assert 'Nuttig' not in panel


# ── Sorteren en opruimen ─────────────────────────────────────────────────

def test_sort_quality_first_prefers_mark_then_link():
    now = datetime(2026, 10, 1)
    a = Post(id=1, body='a', kind=FACT_KIND, fact_source='tekst', created_at=now)
    b = Post(id=2, body='b', kind=FACT_KIND, fact_source='https://b', created_at=now - timedelta(days=2))
    c = Post(id=3, body='c', kind=FACT_KIND, fact_source='https://c', created_at=now - timedelta(days=1))
    d = Post(id=4, body='d', kind=FACT_KIND, fact_source='tekst', created_at=now - timedelta(days=3))
    got = facts.sort_facts([a, b, c, d], 'kwaliteit', marked={d.id})
    assert [p.id for p in got] == [4, 3, 2, 1]


def test_deleting_a_thread_removes_signals(db, make_user):
    from app.routes.forum import _delete_thread
    author, reader = make_user(), make_user()
    issue = _issue(db, author)
    fact = _fact(db, issue, author, 'Weg')
    db.session.add(FactUsefulVote(fact_post_id=fact.id, user_id=reader.id))
    db.session.add(FactDispute(fact_post_id=fact.id, user_id=reader.id, reason='geen_bron'))
    db.session.commit()
    fid = fact.id
    _delete_thread(issue.thread)
    assert FactUsefulVote.query.filter_by(fact_post_id=fid).count() == 0
    assert FactDispute.query.filter_by(fact_post_id=fid).count() == 0


def test_deleting_a_user_detaches_signals(db, make_user):
    from app import quality
    author, reader = make_user(), make_user()
    issue = _issue(db, author)
    fact = _fact(db, issue, author, 'Blijft')
    db.session.add(FactUsefulVote(fact_post_id=fact.id, user_id=reader.id))
    db.session.add(FactDispute(fact_post_id=fact.id, user_id=reader.id, reason='geen_bron'))
    db.session.commit()
    quality.detach_user(reader.id)
    db.session.commit()
    assert FactUsefulVote.query.filter_by(fact_post_id=fact.id).count() == 0
    assert FactDispute.query.filter_by(fact_post_id=fact.id).one().user_id is None
