"""Delete rights step 2: the manage screen per issue (docs/design/beheer.html)
and its entry on the moderator overview (docs/design/main.html).

The guard (who may open /moderator/vraagstuk/<id>/beheer), the per-proposal
actions matching the step-1 rights, this viewer's rights and the log on the
screen, and the "Vraagstukken beheren" section on the overview.
"""
import re

import pytest

from app import issues
from app.models import AppSetting, ModerationAction, Post, PostReport
from tests.test_delete_issue import _clean_session, _cleanup, _client, _req, _user  # noqa: F401  (autouse fixture)
from tests.test_delete_rights import MOD_KEY, SENIOR_KEY, _issue, _proposal, _react, _set


@pytest.fixture(autouse=True)
def _defaults(db):
    def reset():
        AppSetting.query.filter(AppSetting.key.in_([SENIOR_KEY, MOD_KEY])).delete(synchronize_session=False)
        ModerationAction.query.delete(synchronize_session=False)
        db.session.commit()
    reset()
    yield
    reset()


@pytest.fixture
def world(db):
    w = {'admin': _user(db, admin=True), 'senior': _user(db, 'senior_moderator'),
         'mod': _user(db, 'moderator'), 'other_mod': _user(db, 'moderator'),
         'author': _user(db), 'contributor': _user(db)}
    w['mod_issue'] = _issue(db, w['mod'], 'Vraagstuk van de moderator')
    w['plain_issue'] = _issue(db, w['contributor'], 'Vraagstuk van een deelnemer')
    w['p1'] = _proposal(db, w['mod_issue'], w['author'], 'Eerste voorstel bij de moderator.')
    w['p2'] = _proposal(db, w['mod_issue'], w['contributor'], 'Tweede voorstel bij de moderator.')
    w['p_plain'] = _proposal(db, w['plain_issue'], w['author'], 'Voorstel bij de deelnemer.')
    issue_ids = [w['mod_issue'].id, w['plain_issue'].id]
    yield w
    db.session.rollback()
    for issue_id in issue_ids:
        _cleanup(issue_id)


def _path(issue):
    return f'/moderator/vraagstuk/{issue.id}/beheer'


def _page(app, user, path, lang='nl'):
    return re.sub(r'\s+', ' ', _req(_client(app, user, lang), 'GET', path).get_data(as_text=True))


def _row(page, proposal):
    return page.split(f'id="voorstel-{proposal.id}"')[1].split('id="voorstel-')[0].split('</section>')[0]


# ── The guard ─────────────────────────────────────────────────────────────

def test_who_may_open_the_manage_screen(app, db, world):
    w = world
    own, other = w['mod_issue'], w['plain_issue']
    for who, issue, status in (('admin', own, 200), ('admin', other, 200),
                               ('senior', own, 200), ('senior', other, 200),  # may hide all proposals
                               ('mod', own, 200), ('mod', other, 403),
                               ('other_mod', own, 403),
                               ('author', own, 403),        # a plain author never reaches it
                               ('contributor', other, 403)):  # nor a plain issue creator
        assert _req(_client(app, w[who]), 'GET', _path(issue)).status_code == status, (who, issue.title)
    r = _req(app.test_client(), 'GET', _path(own))
    assert r.status_code == 302 and '/login' in r.headers['Location']
    assert _req(_client(app, w['admin']), 'GET', '/moderator/vraagstuk/999999/beheer').status_code == 404


def test_senior_on_an_issue_without_proposals_needs_the_setting(app, db, world):
    w = world
    empty = _issue(db, w['contributor'], 'Leeg vraagstuk')
    try:
        assert _req(_client(app, w['senior']), 'GET', _path(empty)).status_code == 403
        _set(db, SENIOR_KEY, True)
        assert _req(_client(app, w['senior']), 'GET', _path(empty)).status_code == 200
    finally:
        _cleanup(empty.id)


# ── The actions match the rights ───────────────────────────────────────────

def test_actions_per_proposal_follow_the_rights(app, db, world):
    w = world
    issue, p1, p2 = w['mod_issue'], w['p1'], w['p2']
    _react(db, issue, p2, w['author'])
    hide_url = f'/vraagstuk/{issue.id}/voorstel/{p1.id}/verbergen'
    del_url = f'/vraagstuk/{issue.id}/voorstel/{p1.id}/verwijderen'

    # The moderator of the issue: hide and delete on every proposal, and delete the issue; no archive.
    page = _page(app, w['mod'], _path(issue))
    for p in (p1, p2):
        row = _row(page, p)
        assert 'Verbergen' in row and 'Verwijderen' in row and 'Zichtbaar maken' not in row
    assert f'action="{hide_url}"' in page and f'href="{del_url}?next=' in page
    assert 'Vraagstuk verwijderen' in page and 'Archiveren' not in page
    # With deleting turned off for moderators: hide only, and the locked line says so.
    _set(db, MOD_KEY, False)
    page = _page(app, w['mod'], _path(issue))
    assert 'Verbergen' in _row(page, p1) and 'Verwijderen' not in _row(page, p1)
    assert 'Voorstellen verwijderen staat voor moderators uit' in page
    _set(db, MOD_KEY, True)

    # A senior moderator on someone else's issue: hide and delete, not the issue (setting off).
    page = _page(app, w['senior'], _path(issue))
    assert 'Verbergen' in _row(page, p1) and 'Verwijderen' in _row(page, p1)
    assert 'Vraagstuk verwijderen' not in page and 'Archiveren' not in page
    assert 'Andermans vraagstuk verwijderen kan alleen een admin' in page
    _set(db, SENIOR_KEY, True)
    assert 'Vraagstuk verwijderen' in _page(app, w['senior'], _path(issue))

    # The admin: everything, also archiving; no locked line.
    page = _page(app, w['admin'], _path(issue))
    assert 'Archiveren' in page and 'Vraagstuk verwijderen' in page and 'is-locked' not in page

    # Each shown button matches the step-1 rights for that viewer and proposal.
    for who in ('mod', 'senior', 'admin'):
        page = _page(app, w[who], _path(issue))
        for p in (p1, p2):
            row = _row(page, p)
            assert ('Verbergen' in row) == issues.can_hide_proposal(w[who], p)
            assert ('Verwijderen' in row) == issues.can_delete_proposal(w[who], p)


def test_hidden_proposal_shows_make_visible_and_the_filter(app, db, world):
    w = world
    issue, p1 = w['mod_issue'], w['p1']
    issues.hide(p1, w['mod'])
    db.session.commit()
    page = _page(app, w['mod'], _path(issue))
    row = _row(page, p1)
    assert 'Verborgen</span>' in row and 'Zichtbaar maken' in row and 'Verborgen door jou' in row
    assert f'action="/vraagstuk/{issue.id}/voorstel/{p1.id}/zichtbaar"' in row
    assert 'Alle <span class="gu-mgmt__c">2</span>' in page and 'Zichtbaar <span class="gu-mgmt__c">1</span>' in page
    assert 'Verborgen <span class="gu-mgmt__c">1</span>' in page
    props = lambda q: _page(app, w['mod'], _path(issue) + q).split('id="voorstellen"')[1].split('</section>')[0]  # noqa: E731
    assert 'Eerste voorstel' in props('?toon=verborgen') and 'Tweede voorstel' not in props('?toon=verborgen')
    assert 'Eerste voorstel' not in props('?toon=zichtbaar') and 'Tweede voorstel' in props('?toon=zichtbaar')
    # Hiding from the screen returns to it.
    r = _req(_client(app, w['mod']), 'POST', f'/vraagstuk/{issue.id}/voorstel/{w["p2"].id}/verbergen',
             data={'next': _path(issue)})
    assert r.status_code == 302 and r.headers['Location'].endswith(_path(issue))


def test_header_pills_rights_and_log(app, db, world):
    w = world
    issue, p1 = w['mod_issue'], w['p1']
    reporter = _user(db)
    db.session.add(PostReport(post_id=Post.query.filter_by(thread_id=issue.thread_id).first().id,
                              reporter_id=reporter.id, reason='x'))
    db.session.commit()
    issues.hide(p1, w['mod'])
    db.session.commit()
    issues.unhide(p1, w['senior'])
    db.session.commit()
    page = _page(app, w['mod'], _path(issue))
    assert 'Moderator</a>' in page and '<span aria-current="page">Beheer</span>' in page
    assert 'Jouw vraagstuk' in page and '1 open melding' in page and 'Aangemaakt door jou' in page
    rights = page.split('id="mgmtRights"')[1].split('</section>')[0]
    assert 'dit is jouw vraagstuk' in rights and '>Moderator</span>' in rights
    for line in ('Voorstellen hier verbergen en weer zichtbaar maken', 'Voorstellen onder dit vraagstuk verwijderen',
                 'Dit vraagstuk zelf verwijderen'):
        assert line in rights
    assert 'Een admin stelt deze rechten in bij Rechten.' in rights
    log = page.split('id="mgmtLog"')[1].split('</section>')[0]
    assert log.index('weer zichtbaar') < log.index('verborg')  # newest first
    assert f'<b>{w["senior"].public_name}</b>' in log and '<b>Jij</b> verborg "Eerste voorstel bij de moderator."' in log
    # A deleted proposal stays in the log of its issue.
    _req(_client(app, w['mod']), 'POST', f'/vraagstuk/{issue.id}/voorstel/{w["p2"].id}/verwijderen',
         data={'confirm_title': 'Tweede voorstel bij de moderator.', 'next': _path(issue)})
    log = _page(app, w['mod'], _path(issue)).split('id="mgmtLog"')[1].split('</section>')[0]
    assert 'verwijderde "Tweede voorstel bij de' in log
    en = _page(app, w['senior'], _path(issue), 'en')
    assert 'Issue management' in en and 'Who may do what' in en and 'Senior moderator' in en and 'made "' in en


def test_delete_from_the_screen_returns_to_it(app, db, world):
    w = world
    issue, p1 = w['mod_issue'], w['p1']
    confirm = _page(app, w['mod'], f'/vraagstuk/{issue.id}/voorstel/{p1.id}/verwijderen?next={_path(issue)}')
    assert f'name="next" value="{_path(issue)}"' in confirm
    r = _req(_client(app, w['mod']), 'POST', f'/vraagstuk/{issue.id}/voorstel/{p1.id}/verwijderen',
             data={'confirm_title': p1.statement, 'next': _path(issue)})
    assert r.status_code == 302 and r.headers['Location'].endswith(_path(issue))
    # An outside next is ignored.
    r = _req(_client(app, w['mod']), 'POST', f'/vraagstuk/{issue.id}/voorstel/{w["p2"].id}/verwijderen',
             data={'confirm_title': w['p2'].statement, 'next': 'https://evil.example'})
    assert r.headers['Location'].endswith(f'/vraagstuk/{issue.id}')


# ── The entry on the moderator overview ─────────────────────────────────────

def _entry(app, user):
    page = _page(app, user, '/moderator')
    aside = page[page.index('<aside'):page.index('</aside>')]
    return aside.split('id="beheren"')[1].split('</section>')[0] if 'id="beheren"' in aside else None


def test_entry_hidden_for_a_moderator_without_own_issues(app, db, world):
    assert _entry(app, world['other_mod']) is None


def test_entry_shows_a_moderators_own_issues(app, db, world):
    w = world
    entry = _entry(app, w['mod'])
    assert entry is not None and 'Vraagstukken beheren' in entry
    assert 'Vraagstuk van de moderator' in entry and 'Vraagstuk van een deelnemer' not in entry
    assert f'href="/moderator/vraagstuk/{w["mod_issue"].id}/beheer"' in entry and 'jouw vraagstuk' in entry
    assert 'Klik bij een vraagstuk op <strong>Beheer</strong>' in entry


def test_entry_for_senior_and_admin(app, db, world):
    w = world
    for who in ('senior', 'admin'):
        entry = _entry(app, w[who])
        assert entry is not None and 'Vraagstukken beheren' in entry, who
        for issue in (w['mod_issue'], w['plain_issue']):
            assert f'href="/moderator/vraagstuk/{issue.id}/beheer"' in entry, who
    # Capped, with a link to all issues when there are more.
    extra = [_issue(db, w['contributor'], f'Extra vraagstuk {i}') for i in range(issues.MANAGE_LIST_MAX)]
    try:
        entry = _entry(app, w['admin'])
        assert entry.count('class="gu-mgmt-entry__link"') == issues.MANAGE_LIST_MAX
        assert 'Alle vraagstukken' in entry
    finally:
        for issue in extra:
            _cleanup(issue.id)


def test_melding_label_leads_to_the_manage_screen_when_allowed(app, db, world):
    w = world
    fact = Post.query.filter_by(thread_id=w['mod_issue'].thread_id).first()
    db.session.add(PostReport(post_id=fact.id, reporter_id=w['author'].id, reason='x'))
    db.session.commit()
    beheer = f'href="/moderator/vraagstuk/{w["mod_issue"].id}/beheer"'

    def pill(page):
        """The label of this issue's melding (other tests may leave other meldingen)."""
        pills = [p.split('</a>')[0] for p in page.split('class="gu-pill gu-pill--ctx"')[1:]]
        return next(p for p in pills if 'Vraagstuk van de moderator' in p)
    assert beheer in pill(_page(app, w['mod'], '/moderator?stroom=meldingen'))
    other = pill(_page(app, w['other_mod'], '/moderator?stroom=meldingen'))
    assert 'beheer' not in other and f'href="/vraagstuk/{w["mod_issue"].id}"' in other
