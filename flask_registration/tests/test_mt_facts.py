"""Multilingual phase 2: facts are translated right away, like issues and
proposals (app/content_jobs.py), and carry the label in every view.

- Adding or editing a fact schedules its title and text (not the source)
  for every other enabled language; the background job runs after the
  commit (here: by the test, with a fake AI client).
- The facts list (enlarged facts), the facts panel (the cockpit column) and
  the link list (choosing facts for a proposal) show the translation with the
  label; compact lists the original with its code until it is there.
- A disputed fact shows to the moderator in their own language with the
  label; the note of the dispute is translated on request.
- Ordinary posts stay on request; a fixed number of translation queries per
  facts list; never an AI call on a page load.
"""
import html
import re

import pytest

import config
from app import content_i18n, content_jobs, db, facts, quality
from app.models import FactDispute, Post
from tests.test_content_i18n import FakeClient
from tests.test_content_jobs import _run, _translations, jobs  # noqa: F401  (fixture)
from tests.test_delete_issue import _clean_session, _cleanup  # noqa: F401  (autouse fixture)
from tests.test_issue_polish import _client, _issue, _req, make_user  # noqa: F401  (fixture)
from tests.test_mt_lists import _queries

LABEL = {'de': 'Maschinell übersetzt, Originalsprache: Niederländisch',
         'es': 'Traducido automáticamente del neerlandés',
         'fr': "Traduction automatique, langue d'origine : néerlandais"}
LANGUAGE = {'de': 'German', 'es': 'Spanish', 'fr': 'French'}


def _page(app, user, lang, path):
    return html.unescape(_req(_client(app, user, lang), 'GET', path).get_data(as_text=True))


def _add_fact(app, user, issue, title, text, source='https://cbs.example/rapport'):
    return _req(_client(app, user, 'nl'), 'POST', f'/forum/{issue.thread_id}/feiten',
                data={'title': title, 'text': text, 'direction': 'voor', 'source': source, 'type': 'feit',
                      'return_to': str(issue.id)})


@pytest.fixture
def no_ai(monkeypatch):
    """Fails the test if a page load calls the AI (the background job runs
    with its own fake client, see _run)."""
    real = quality.translate_text

    def guard(*args, **kw):
        if kw.get('client') is None:
            pytest.fail('the AI was called on a page load')
        return real(*args, **kw)
    monkeypatch.setattr(quality, 'translate_text', guard)


@pytest.fixture
def world(app, db, make_user, jobs):
    from app import limiter
    limiter.reset()  # adding facts is rate limited per user id (ids repeat across tests)
    author = make_user()
    issue = _issue(db, author, 'Hoe houden we het water schoon?')
    jobs.clear()  # the issue's own job
    yield author, issue
    _cleanup(issue.id)


def _panel(page):
    """The compact facts column of the cockpit (no ids on its items)."""
    panel = page[page.index('gu-cockpit__compact'):]
    return panel[:panel.index('</ol>')]


def _fact_box(page, fact_id):
    """The component of one fact (title, text, label) in a list item."""
    item = page[page.index(f'id="post-{fact_id}"'):]
    return item[:item.index('</li>')]


def test_a_new_fact_is_scheduled_and_translated_after_the_commit(app, world, jobs):
    author, issue = world
    _add_fact(app, author, issue, 'Het water is schoner', 'Het water is schoner dan in 2010.')
    fact = Post.query.filter_by(body='Het water is schoner dan in 2010.').one()
    assert jobs == [[('posts', fact.id)]]          # scheduled, not run during the save
    assert _translations('posts', fact.id) == {}
    _run(app, jobs, FakeClient())
    rows = _translations('posts', fact.id)
    others = [lang for lang in config.ENABLED_LANGUAGES if lang != 'nl']
    assert set(rows) == {(field, lang) for field in ('fact_title', 'body') for lang in others}
    assert all(row.is_machine for row in rows.values())
    assert 'fact_source' not in {field for field, _lang in rows}   # the source is never translated


@pytest.mark.parametrize('lang', ['de', 'es', 'fr'])
def test_a_translated_fact_shows_with_the_label_in_every_view(app, world, make_user, jobs, no_ai, lang):
    author, issue = world
    _add_fact(app, author, issue, 'Het water is schoner', 'Het water is schoner dan in 2010.')
    fact = Post.query.filter_by(body='Het water is schoner dan in 2010.').one()
    _run(app, jobs, FakeClient())
    reader = make_user()
    title, text = f'[{LANGUAGE[lang]}] Het water is schoner', f'[{LANGUAGE[lang]}] Het water is schoner dan in 2010.'

    # The facts list (enlarged) and the facts panel (the cockpit column): one component, one label.
    page = _page(app, reader, lang, f'/vraagstuk/{issue.id}?vergroot=feiten')
    big = _fact_box(page[page.index('gu-bigf__list'):], fact.id)
    panel = _panel(page)
    for box in (big, panel):
        assert f'<p class="gu-fact__title" lang="{lang}" data-mt-translation>{title}</p>' in box
        assert f'<p class="gu-fact__text" lang="{lang}" data-mt-translation>{text}</p>' in box
        # The original, hidden, in Dutch, swapped by the one button; the source as is.
        assert '<p class="gu-fact__title" lang="nl" data-mt-original hidden>Het water is schoner</p>' in box
        assert '<p class="gu-fact__text" lang="nl" data-mt-original hidden>Het water is schoner dan in 2010.</p>' in box
        assert box.count(f'data-mt-label>{LABEL[lang]}</span>') == 1 and box.count('class="gu-mt__toggle"') == 1
        assert 'https://cbs.example/rapport' in box and '/forum/translate/' not in box

    # The link list (choosing facts for a new proposal): the translation, no code.
    form = _page(app, reader, lang, f'/vraagstuk/{issue.id}/voorstellen/nieuw')
    choices = form[form.index('gu-factchoices'):]
    choices = choices[:choices.index('</fieldset>')]
    assert f'<span lang="{lang}">{text}</span>' in choices and 'gu-mt__code' not in choices


def test_before_the_job_the_original_with_translation_pending_or_the_code(app, world, make_user, jobs, no_ai):
    author, issue = world
    _add_fact(app, author, issue, 'Het water is schoner', 'Het water is schoner dan in 2010.')
    fact = Post.query.filter_by(body='Het water is schoner dan in 2010.').one()
    reader = make_user()
    page = _page(app, reader, 'de', f'/vraagstuk/{issue.id}?vergroot=feiten')
    box = _panel(page)
    assert 'Übersetzung folgt' in box and 'lang="nl" data-mt-original>Het water is schoner dan in 2010.</p>' in box
    form = _page(app, reader, 'de', f'/vraagstuk/{issue.id}/voorstellen/nieuw')
    assert ('<span lang="nl">Het water is schoner dan in 2010.</span> <abbr class="gu-mt__code" '
            'title="Original auf Niederländisch">NL</abbr>') in form


def test_editing_a_fact_makes_new_translations(app, world, jobs):
    author, issue = world
    _add_fact(app, author, issue, 'Het water is schoner', 'Het water is schoner dan in 2010.')
    fact = Post.query.filter_by(body='Het water is schoner dan in 2010.').one()
    _run(app, jobs, FakeClient())
    r = _req(_client(app, author, 'nl'), 'POST', f'/forum/feit/{fact.id}/bewerken',
             data={'title': 'Het water is veel schoner', 'text': 'Het water is veel schoner dan in 2010.',
                   'direction': 'voor', 'source': 'https://cbs.example/rapport', 'type': 'feit',
                   'return_to': str(issue.id)})
    assert r.status_code in (302, 303)
    assert jobs == [[('posts', fact.id)]]
    assert _translations('posts', fact.id) == {}      # the old ones are gone at once
    _run(app, jobs, FakeClient())
    rows = _translations('posts', fact.id)
    assert rows[('body', 'de')].text == '[German] Het water is veel schoner dan in 2010.'
    assert rows[('fact_title', 'fr')].text == '[French] Het water is veel schoner'


def test_ordinary_posts_stay_on_request_and_the_tick_takes_only_facts(app, world, jobs):
    author, issue = world
    post = Post(thread_id=issue.thread_id, user_id=author.id, author_name='', author_username=author.username,
                body='Een gewone bijdrage.', source_lang='nl')
    db.session.add(post)
    content_jobs.schedule(post)
    db.session.commit()
    assert jobs == []                                   # not scheduled
    fact = Post(thread_id=issue.thread_id, user_id=author.id, author_name='', author_username=author.username,
                body='Een feit zonder vertaling.', source_lang='nl', kind='feit', fact_direction='voor',
                fact_source='https://example.org')
    db.session.add(fact)
    db.session.commit()
    content_jobs.backfill(limit=200, client=FakeClient())
    db.session.expire_all()
    assert _translations('posts', post.id) == {}
    assert ('body', 'de') in _translations('posts', fact.id)


def test_a_fixed_number_of_translation_queries_per_facts_list(app, db, make_user, no_ai):
    user = make_user()
    counts, made = [], []
    try:
        for n in (1, 5):
            issue = _issue(db, user, 'Een vraagstuk voor de telling')
            made.append(issue.id)
            for i in range(n):
                fact = Post(thread_id=issue.thread_id, user_id=user.id, author_name='', author_username=user.username,
                            body=f'Feit {i}.', source_lang='nl', kind='feit', fact_direction='voor',
                            fact_title=f'Kern {i}', fact_source='https://example.org')
                db.session.add(fact)
                db.session.flush()
                content_i18n.set_translation(fact, 'body', 'en', f'Fact {i}.', is_machine=True)
                content_i18n.set_translation(fact, 'fact_title', 'en', f'Core {i}', is_machine=True)
            db.session.commit()
            for path in (f'/vraagstuk/{issue.id}?vergroot=feiten', f'/vraagstuk/{issue.id}/voorstellen/nieuw'):
                with _queries() as seen:
                    page = _page(app, user, 'en', path)
                assert 'Fact 0.' in page, path
                counts.append((path.split('?')[0].rsplit('/', 1)[-1],
                               sum(1 for q in seen if 'content_translations' in q)))
        few, many = counts[:2], counts[2:]
        assert [c for _p, c in few] == [c for _p, c in many], counts
        assert max(c for _p, c in counts) <= 6, counts
    finally:
        for issue_id in made:
            _cleanup(issue_id)


def test_the_moderator_sees_a_disputed_fact_in_their_language_and_the_note_on_request(app, world, make_user, jobs,
                                                                                         no_ai):
    author, issue = world
    _add_fact(app, author, issue, 'Het water is schoner', 'Het water is schoner dan in 2010.')
    fact = Post.query.filter_by(body='Het water is schoner dan in 2010.').one()
    _run(app, jobs, FakeClient())
    disputer = make_user()
    facts.dispute(fact, disputer, 'klopt_niet', 'De cijfers zijn van een ander meetpunt.', lang='nl')
    db.session.commit()
    row = FactDispute.query.filter_by(fact_post_id=fact.id).one()
    moderator = make_user('moderator')
    page = _page(app, moderator, 'fr', '/moderator')
    section = page[page.index('id="betwist"'):]
    section = section[:section.index('</section>')]
    assert '<p class="gu-fact__text" lang="fr" data-mt-translation>[French] Het water is schoner dan in 2010.</p>' in section
    assert LABEL['fr'] in section and 'data-mt-original hidden>Het water is schoner dan in 2010.</p>' in section
    # The note: the original, "Original in Dutch" and a translate button to its own route.
    note = section[section.index('gu-mrow__notetext') - 200:]
    assert 'De cijfers zijn van een ander meetpunt.' in note
    assert f'data-mt-url="/forum/feit/betwisting/{row.id}/vertalen"' in note
    assert "Original en néerlandais" in note

    # The route: a moderator only; from the cache without an AI call.
    content_i18n.set_translation(row, 'note', 'fr', 'Les chiffres viennent d’un autre point de mesure.', is_machine=True)
    db.session.commit()
    client = _client(app, moderator, 'fr')
    resp = _req(client, 'POST', f'/forum/feit/betwisting/{row.id}/vertalen')
    assert resp.get_json() == {'translation': 'Les chiffres viennent d’un autre point de mesure.'}
    assert _req(_client(app, disputer, 'fr'), 'POST', f'/forum/feit/betwisting/{row.id}/vertalen').status_code == 403
    # Translated before: the label and the swap, no request.
    page = _page(app, moderator, 'fr', '/moderator')
    assert re.search(r'<p class="gu-mrow__notetext" lang="fr" data-mt-translation( hidden)?>Les chiffres', page)


def test_deleting_a_fact_takes_its_translations_and_the_dispute_notes_along(app, world, jobs):
    author, issue = world
    _add_fact(app, author, issue, 'Het water is schoner', 'Het water is schoner dan in 2010.')
    fact = Post.query.filter_by(body='Het water is schoner dan in 2010.').one()
    _run(app, jobs, FakeClient())
    facts.dispute(fact, author, 'klopt_niet', 'Een toelichting.', lang='nl')
    db.session.commit()
    row = FactDispute.query.filter_by(fact_post_id=fact.id).one()
    content_i18n.set_translation(row, 'note', 'en', 'A note.', is_machine=True)
    db.session.commit()
    fact_id, row_id = fact.id, row.id
    quality.delete_for_posts([fact_id])
    Post.query.filter(Post.id == fact_id).delete(synchronize_session=False)
    db.session.commit()
    assert _translations('posts', fact_id) == {} and _translations('fact_disputes', row_id) == {}
