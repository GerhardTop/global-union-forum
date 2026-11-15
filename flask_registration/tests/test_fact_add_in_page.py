"""Page consolidation step 2 (docs/design/feit-toevoegen.html): adding a fact
in the page, five kinds of fact, the renamed text label, and the "+" on both
columns.

- The "+" in the facts column head opens the add form at the top of the
  enlarged facts (without JavaScript: ?vergroot=feiten&nieuw-feit=1). Only in
  the facts phase and for whoever may contribute.
- The form posts to forum.fact_add and comes back enlarged with the new fact;
  an input error reopens the form with the values and the messages.
- The "+" in the proposals column head (compact too) opens proposal_new, only
  when a new proposal is allowed.
- Kinds: feit, onderzoek, nieuwsartikel, podcast, video; an old 'media' fact
  still shows.
"""
import re
from urllib.parse import parse_qs, urlsplit

import pytest

from app import issue_proposals as ip
from app.models import FACT_KIND, FACT_TYPES, Post
from tests.test_delete_issue import _clean_session, _cleanup  # noqa: F401  (autouse fixture)
from tests.test_issue_polish import _client, _get, _issue, _req, make_user  # noqa: F401  (fixture)


@pytest.fixture
def world(db, make_user):
    host = make_user()
    issue = _issue(db, host, 'Feit toevoegen in de pagina')
    proposal = ip.create(issue, host, 'Een voorstel.')
    db.session.commit()
    yield issue, proposal, host
    _cleanup(issue.id)


def _pages(issue, proposal):
    return {'issue': f'/vraagstuk/{issue.id}', 'proposal': f'/vraagstuk/{issue.id}/voorstel/{proposal.id}'}


def _head(page, panel_id):
    return page.split(f'id="{panel_id}"')[1].split('</div>')[0]


def _box(page):
    """The add box (with its open/hidden attribute) in the enlarged facts."""
    m = re.search(r'<div class="gu-addfact" id="nieuw-feit" data-fact-add-box( hidden)?>(.*?)</form>', page, re.S)
    return m


def _fact_data(**over):
    data = {'title': 'Nieuwe kern', 'text': 'Een nieuw feit uit het invoervak.', 'direction': 'tegen',
            'source': 'https://example.org/rapport', 'type': 'onderzoek'}
    data.update(over)
    return data


# ── The "+" on the facts column ────────────────────────────────────────────

def test_fact_plus_opens_the_add_form_in_the_page(app, db, world):
    issue, proposal, host = world
    for key, path in _pages(issue, proposal).items():
        page = _get(app, path, host)
        head = _head(page, 'panel-facts')
        plus = re.search(r'<a href="([^"]+)" class="gu-iconbtn gu-addfact__plus" data-fact-add aria-controls="nieuw-feit"', head)
        assert plus, key
        parts = urlsplit(plus.group(1).replace('&amp;', '&'))
        assert parts.path == path and parts.fragment == 'panel-facts', key
        assert {k: v[0] for k, v in parse_qs(parts.query).items()} == {'vergroot': 'feiten', 'nieuw-feit': '1'}, key
        # Compact: the form is in the enlarged view, closed.
        box = _box(page)
        assert box and box.group(1) == ' hidden', key
        # The no-script link: enlarged, form open, at the top above the filter bar.
        opened = _get(app, path + '?vergroot=feiten&nieuw-feit=1', host)
        assert re.search(r'<main class="[^"]*gu-cockpit--expanded', opened) and 'data-expanded="facts"' in opened, key
        box = _box(opened)
        assert box and box.group(1) is None, key
        assert f'action="/forum/{issue.thread_id}/feiten"' in box.group(2), key
        assert f'name="return_to" value="{issue.id}"' in box.group(2) and 'name="in_page" value="1"' in box.group(2), key
        assert ('name="return_proposal" value="%d"' % proposal.id in box.group(2)) == (key == 'proposal'), key
        big = opened.split('id="panel-facts"')[1].split('</section>')[0].split('gu-cockpit__bigbox')[1]
        assert big.index('gu-addfact') < big.index('gu-cockpit__factlist') if 'gu-cockpit__factlist' in big else True
        for lang, texts in (('nl', ('Toevoegen aan feiten en achtergrond', '>Toevoegen <', '>Annuleren</a>', 'aria-label="Sluiten"')),
                            ('en', ('Add to facts and background', '>Add <', '>Cancel</a>', 'aria-label="Close"'))):
            box = _box(_get(app, path + '?vergroot=feiten&nieuw-feit=1', host, lang)).group(0)
            for text in texts:
                assert text in box, (key, lang, text)
        # The close links drop nieuw-feit and keep the facts enlarged.
        assert f'href="{path}?vergroot=feiten#panel-facts" class="gu-btn gu-btn--ghost" data-fact-add-close' in opened, key


def test_fact_plus_only_in_the_facts_phase_and_for_contributors(app, db, world, make_user):
    issue, proposal, host = world
    for key, path in _pages(issue, proposal).items():
        anonymous = _get(app, path + '?vergroot=feiten&nieuw-feit=1')
        assert 'data-fact-add' not in anonymous and 'gu-addfact' not in anonymous, key
        # Signed in but has not accepted the conduct rules: may not contribute.
        newcomer = make_user()
        newcomer.rules_accepted_at = None
        db.session.commit()
        assert not newcomer.can_contribute()
        page = _get(app, path + '?vergroot=feiten&nieuw-feit=1', newcomer)
        assert 'data-fact-add' not in page and 'gu-addfact' not in page, key
    issue.phase = 'bijstellen'
    db.session.commit()
    for key, path in _pages(issue, proposal).items():
        page = _get(app, path + '?vergroot=feiten&nieuw-feit=1', host)
        assert 'data-fact-add' not in page and 'gu-addfact' not in page, key


# ── Adding through the form ────────────────────────────────────────────────

def test_adding_a_fact_in_the_page_comes_back_enlarged_with_the_fact(app, db, world):
    issue, proposal, host = world
    for key, extra in (('issue', {}), ('proposal', {'return_proposal': str(proposal.id)})):
        text = f'Een nieuw feit uit het invoervak ({key}).'
        resp = _req(_client(app, host), 'POST', f'/forum/{issue.thread_id}/feiten',
                    data=dict(_fact_data(text=text), return_to=str(issue.id), in_page='1', **extra))
        assert resp.status_code == 302, key
        fact = Post.query.filter_by(body=text).one()
        # Step 3: back with the new fact in view.
        target = _pages(issue, proposal)[key] + f'?vergroot=feiten#post-{fact.id}'
        assert resp.headers['Location'].endswith(target), (key, resp.headers['Location'])
        assert fact.kind == FACT_KIND and fact.fact_type == 'onderzoek' and fact.fact_direction == 'tegen'
        page = _get(app, resp.headers['Location'], host)
        big = page.split('id="panel-facts"')[1].split('</section>')[0].split('gu-cockpit__bigbox')[1]
        assert text in big and 'data-expanded="facts"' in page, key
        assert _box(page).group(1) == ' hidden', key  # the form is closed again
    # Step 4: without in_page (the facts list is gone) it also lands in the cockpit.
    resp = _req(_client(app, host), 'POST', f'/forum/{issue.thread_id}/feiten',
                data=dict(_fact_data(text='Een feit zonder in_page.'), return_to=str(issue.id)))
    parts = urlsplit(resp.headers['Location'])
    assert parts.path == f'/vraagstuk/{issue.id}' and parts.query == 'vergroot=feiten'


def test_an_input_error_reopens_the_form_with_the_values(app, db, world):
    issue, proposal, host = world
    for key, extra in (('issue', {}), ('proposal', {'return_proposal': str(proposal.id)})):
        c = _client(app, host)
        resp = _req(c, 'POST', f'/forum/{issue.thread_id}/feiten',
                    data=dict(_fact_data(title='Mijn kern', source='', type='podcast'), return_to=str(issue.id),
                              in_page='1', **extra))
        assert resp.status_code == 303, key
        parts = urlsplit(resp.headers['Location'])
        assert parts.path == _pages(issue, proposal)[key] and parts.fragment == 'panel-facts', key
        assert {k: v[0] for k, v in parse_qs(parts.query).items()} == {'vergroot': 'feiten', 'nieuw-feit': '1'}, key
        page = _req(c, 'GET', resp.headers['Location']).get_data(as_text=True)
        box = _box(page)
        assert box and box.group(1) is None, key
        form = box.group(2)
        assert 'value="Mijn kern"' in form and 'Een nieuw feit uit het invoervak.</textarea>' in form, key
        assert 'Geef een bron.' in form and 'gu-input--error' in form, key
        assert re.search(r'value="tegen" checked', form) and re.search(r'value="podcast" checked', form), key
        # The message is used once: a reload shows the form closed and empty.
        again = _req(c, 'GET', _pages(issue, proposal)[key] + '?vergroot=feiten').get_data(as_text=True)
        assert _box(again).group(1) == ' hidden' and 'value="Mijn kern"' not in again, key
    assert not Post.query.filter_by(body='Een nieuw feit uit het invoervak.').first()


# ── Five kinds, the old 'media', the label ────────────────────────────────

def test_the_five_kinds_are_accepted_and_shown(app, db, world, make_user):
    issue, proposal, host = world
    assert FACT_TYPES == ('feit', 'onderzoek', 'nieuwsartikel', 'podcast', 'video')
    labels = {'feit': None, 'onderzoek': ('Onderzoek', 'Research'), 'nieuwsartikel': ('Nieuwsartikel', 'News article'),
              'podcast': ('Podcast', 'Podcast'), 'video': ('Video', 'Video')}
    made = {}
    for kind in FACT_TYPES:
        text = f'Een feit van soort {kind}.'
        _req(_client(app, make_user()), 'POST', f'/forum/{issue.thread_id}/feiten',
             data=dict(_fact_data(text=text, type=kind), return_to=str(issue.id), in_page='1'))
        made[kind] = Post.query.filter_by(body=text).one()
        assert made[kind].fact_type == kind
    # An unknown kind falls back to a fact.
    _req(_client(app, make_user()), 'POST', f'/forum/{issue.thread_id}/feiten',
         data=dict(_fact_data(text='Onbekende soort.', type='roman'), return_to=str(issue.id), in_page='1'))
    assert Post.query.filter_by(body='Onbekende soort.').one().fact_type == 'feit'
    for lang, i in (('nl', 0), ('en', 1)):
        page = _get(app, f'/vraagstuk/{issue.id}?vergroot=feiten', host, lang)
        for kind, fact in made.items():
            card = page.split(f'Een feit van soort {kind}.')[0].rsplit('<li class="gu-fact ', 1)[1]
            if labels[kind] is None:
                assert 'gu-fact__media' not in card, (lang, kind)
            else:
                assert f'gu-fact__kind--{kind}">{labels[kind][i]}</span>' in card, (lang, kind)
    # The form: five chips, Feit chosen by default.
    form = _box(_get(app, f'/vraagstuk/{issue.id}?vergroot=feiten&nieuw-feit=1', host)).group(2)
    kinds = form.split('gu-fact-form__kinds')[1].split('</fieldset>')[0]
    assert '<legend>Soort</legend>' in kinds
    assert re.findall(r'<span>([^<]+)</span>', kinds) == ['Feit', 'Onderzoek', 'Nieuwsartikel', 'Podcast', 'Video']
    assert re.findall(r'value="(\w+)" checked', kinds) == ['feit']
    en = _box(_get(app, f'/vraagstuk/{issue.id}?vergroot=feiten&nieuw-feit=1', host, 'en')).group(2)
    assert re.findall(r'<span>([^<]+)</span>', en.split('gu-fact-form__kinds')[1].split('</fieldset>')[0]) == \
        ['Fact', 'Research', 'News article', 'Podcast', 'Video']


def test_an_old_media_fact_still_shows(app, db, world):
    issue, proposal, host = world
    old = Post(thread_id=issue.thread_id, user_id=host.id, author_name='', author_username=host.username,
               body='Een oud mediafeit.', source_lang='nl', kind=FACT_KIND, fact_direction='voor',
               fact_source='https://nos.example', fact_type='media', fact_title='Oud')
    db.session.add(old)
    db.session.commit()
    assert old.is_media and old.fact_kind == 'media'
    for path in (f'/vraagstuk/{issue.id}', f'/vraagstuk/{issue.id}?vergroot=feiten',
                 f'/vraagstuk/{issue.id}/voorstel/{proposal.id}?vergroot=feiten'):
        page = _get(app, path, host)
        card = page.split('Een oud mediafeit.')[0].rsplit('<li', 1)[1]
        assert 'gu-fact__kind--media">Media</span>' in card, path
    # Editing it on the enlarged card: the form opens with Nieuwsartikel chosen, and an old form that sends 'media' still works.
    page = _get(app, f'/vraagstuk/{issue.id}?vergroot=feiten', host)
    edit = page.split(f'id="big-edit-{old.id}-title"')[1].split(f'id="big-edit-{old.id}-source"')[0]
    assert re.findall(r'value="(\w+)" checked', edit.split('gu-fact-form__kinds')[1]) == ['nieuwsartikel']
    _req(_client(app, host), 'POST', f'/forum/feit/{old.id}/bewerken',
         data={'title': 'Oud', 'text': 'Een oud mediafeit.', 'direction': 'voor', 'source': 'https://nos.example',
               'type': 'media', 'return_to': str(issue.id)})
    db.session.refresh(old)
    assert old.fact_type == 'media'


def test_the_text_field_label_is_renamed(app, db, world):
    issue, proposal, host = world
    for lang, label, gone in (('nl', 'Toelichting of conclusies', 'Het feit</label>'),
                              ('en', 'Explanation or conclusions', 'The fact</label>')):
        for path in (f'/vraagstuk/{issue.id}?vergroot=feiten&nieuw-feit=1', f'/vraagstuk/{issue.id}/voorstel/{proposal.id}?vergroot=feiten&nieuw-feit=1',
                     f'/vraagstuk/{issue.id}/voorstellen/nieuw', f'/gesprekken/nieuw'):
            page = _get(app, path, host, lang)
            assert f'>{label}</label>' in page and gone not in page, (lang, path)


# ── The "+" on the proposals column ────────────────────────────────────────

def test_proposal_plus_on_both_pages_opens_the_make_page(app, db, world, make_user):
    issue, proposal, host = world
    plus = f'<a href="/vraagstuk/{issue.id}/voorstellen/nieuw" class="gu-iconbtn" title="Nieuw voorstel"'
    for key, path in _pages(issue, proposal).items():
        page = _get(app, path, host)
        head = _head(page, 'panel-proposals')
        assert plus in head, key  # in the shared head, so also in the compact column
        compact = page.split('id="panel-proposals"')[1].split('gu-cockpit__box gu-cockpit__compact')[0]
        assert plus in compact, key
        assert plus not in _get(app, path), key  # not signed in
    assert _req(_client(app, host), 'GET', f'/vraagstuk/{issue.id}/voorstellen/nieuw').status_code == 200
    # Not when a new proposal is not allowed (voting phase).
    issue.phase = 'stemmen'
    db.session.commit()
    for key, path in _pages(issue, proposal).items():
        assert plus not in _get(app, path, host), key


def test_direction_options_are_chips_with_a_dot(app, db, world):
    """The direction in the fact form: chips like the kinds, a coloured dot
    before each label, real radio inputs underneath; everywhere the form is."""
    issue, proposal, host = world
    for lang, labels in (('nl', ['Voor', 'Tegen', 'Neutraal']), ('en', ['For', 'Against', 'Neutral'])):
        for path in (f'/vraagstuk/{issue.id}?vergroot=feiten&nieuw-feit=1', f'/vraagstuk/{issue.id}/voorstel/{proposal.id}?vergroot=feiten&nieuw-feit=1',
                     f'/vraagstuk/{issue.id}/voorstellen/nieuw', '/gesprekken/nieuw'):
            page = _get(app, path, host, lang)
            dirs = page.split('<fieldset class="gu-fact-form__field gu-fact-form__dirs gu-fact-form__chips">')[1].split('</fieldset>')[0]
            chips = re.findall(r'<label class="gu-fact-form__dir gu-fact-form__chip">\s*<input type="radio" name="(?:\w*)direction" value="(\w+)"[^>]*>\s*'
                               r'<span><span class="gu-fact-form__dot gu-fact-form__dot--(\w+)" aria-hidden="true"></span>([^<]+)</span>', dirs)
            assert [(v, d) for v, d, _l in chips] == [('voor', 'voor'), ('tegen', 'tegen'), ('neutraal', 'neutraal')], (lang, path)
            assert [l for _v, _d, l in chips] == labels, (lang, path)
