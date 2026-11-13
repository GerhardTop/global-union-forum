"""Delete rights step 3: the public pages cleaned up.

The issue page's Beheer menu links to the manage screen instead of deleting;
the public proposal page has no admin delete block, only the author's quiet
"Voorstel intrekken" while nobody has reacted; hidden proposals are left out
of every public list and count; and a hidden proposal's page is a 404 for
everyone who may not hide it, with a note for whoever may.
"""
import re

import pytest

from app import issue_proposals as ip
from app import issues
from app.models import AppSetting, ModerationAction
from tests.test_delete_issue import _clean_session, _cleanup, _client, _req, _user  # noqa: F401  (autouse fixture)
from tests.test_delete_rights import MOD_KEY, SENIOR_KEY, _issue, _proposal, _react


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
    w['issue'] = _issue(db, w['mod'], 'Vraagstuk van de moderator')
    w['shown'] = _proposal(db, w['issue'], w['author'], 'Zichtbaar voorstel.')
    w['hidden'] = _proposal(db, w['issue'], w['author'], 'Verborgen voorstel.')
    issues.hide(w['hidden'], w['mod'])
    db.session.commit()
    issue_id = w['issue'].id
    yield w
    db.session.rollback()
    _cleanup(issue_id)


def _get(app, user, path, lang='nl'):
    c = _client(app, user, lang) if user is not None else app.test_client()
    return _req(c, 'GET', path)


def _html(app, user, path, lang='nl'):
    return re.sub(r'\s+', ' ', _get(app, user, path, lang).get_data(as_text=True))


def _menu(page):
    if '<details class="gu-beheer" id="beheer">' not in page:
        return None
    menu = page[page.index('<details class="gu-beheer" id="beheer">'):]
    return menu[:menu.index('</details>')]


# ── 1. The Beheer menu on the issue page ───────────────────────────────────

def test_beheer_menu_links_to_the_manage_screen(app, db, world):
    w = world
    path, manage = f'/vraagstuk/{w["issue"].id}', f'href="/moderator/vraagstuk/{w["issue"].id}/beheer"'
    for who in ('mod', 'senior', 'admin'):
        menu = _menu(_html(app, w[who], path))
        assert menu is not None and manage in menu and 'Vraagstukbeheer' in menu, who
        assert 'Vraagstuk verwijderen' not in menu and '/verwijderen' not in menu, who
    assert 'Archiveren' in _menu(_html(app, w['admin'], path))       # archiving stays, admin only
    assert 'Archiveren' not in _menu(_html(app, w['mod'], path))
    assert 'Manage issue' in _menu(_html(app, w['mod'], path, 'en'))
    # No right on this issue: no link (and no menu at all for these viewers).
    for who in ('other_mod', 'author', 'contributor'):
        page = _html(app, w[who], path)
        assert manage not in page and _menu(page) is None, who
    assert manage not in _html(app, None, path)


# ── 2. The public proposal page ────────────────────────────────────────────

def test_withdraw_only_for_the_author_without_reactions(app, db, world):
    w = world
    proposal = w['shown']
    path = f'/vraagstuk/{w["issue"].id}/voorstel/{proposal.id}'
    delete = f'href="/vraagstuk/{w["issue"].id}/voorstel/{proposal.id}/verwijderen"'
    page = _html(app, w['author'], path)
    assert 'Voorstel intrekken' in page and delete in page
    assert 'Je kunt je eigen voorstel intrekken zolang er nog geen reacties zijn.' in page
    assert 'Withdraw proposal' in _html(app, w['author'], path, 'en')
    # Moderators and admins delete from the manage screen: nothing here, and the old block is gone.
    for who in ('mod', 'senior', 'admin', 'contributor', None):
        page = _html(app, w[who] if who else None, path)
        assert 'Voorstel intrekken' not in page and delete not in page, who
        assert 'beheer-admin' not in page and 'Voorstel verwijderen' not in page, who
    # Once anyone reacted, the author can no longer withdraw.
    _react(db, w['issue'], proposal, w['contributor'])
    page = _html(app, w['author'], path)
    assert 'Voorstel intrekken' not in page and delete not in page
    assert _get(app, w['author'], path + '/verwijderen').status_code == 403


# ── 3. Hidden proposals out of the public lists and counts ─────────────────

def test_hidden_proposal_left_out_of_public_lists(app, db, world):
    w = world
    issue = w['issue']
    assert [p.id for p, _c in ip.listing(issue)] == [w['shown'].id]
    assert ip.regular_query(issue).count() == 1 and ip.regular_query(issue, include_hidden=True).count() == 2
    assert {p.id for p, _c in ip.listing(issue, include_hidden=True)} == {w['shown'].id, w['hidden'].id}
    for who in ('contributor', 'admin'):  # also an admin: the public pages are the same for everyone
        cockpit = _html(app, w[who], f'/vraagstuk/{issue.id}')
        column = cockpit.split('id="panel-proposals"')[1].split('</section>')[0]
        assert 'Zichtbaar voorstel.' in column and 'Verborgen voorstel.' not in column, who
        assert 'Voorstellen <span class="gu-facts__count">(1)</span>' in column, who
        listing = _html(app, w[who], f'/vraagstuk/{issue.id}/voorstellen')
        assert 'Zichtbaar voorstel.' in listing and 'Verborgen voorstel.' not in listing, who
        rail = _html(app, w[who], f'/vraagstuk/{issue.id}/voorstel/{w["shown"].id}')
        rail = rail.split('id="panel-proposals"')[1].split('</section>')[0]
        assert 'Verborgen voorstel.' not in rail and 'Voorstellen <span class="gu-facts__count">1</span>' in rail, who
    # The overview counts only the visible one; so does the phase bar ("Voorstel aanpassen", singular).
    overview = _html(app, w['contributor'], '/gesprekken')
    card = overview.split('Vraagstuk van de moderator')[1][:1500]
    assert '1 voorstel<' in card
    issue.phase = 'bijstellen'
    db.session.commit()
    phase = _html(app, w['contributor'], f'/vraagstuk/{issue.id}').split('<ol class="gu-proposal-steps')[1].split('</ol>')[0]
    assert 'Voorstel aanpassen' in phase and 'Voorstellen aanpassen' not in phase


def test_hidden_summary_proposal_is_not_public(app, db, world):
    w = world
    issue = w['issue']
    final = ip.create_final(issue, w['mod'], 'Samenvatting.')
    db.session.commit()
    assert ip.final_proposal(issue).id == final.id
    issues.hide(final, w['senior'])
    db.session.commit()
    assert ip.final_proposal(issue) is None and ip.final_proposal(issue, include_hidden=True).id == final.id
    assert 'Samenvatting.' not in _html(app, w['contributor'], f'/vraagstuk/{issue.id}')
    # The initiator cannot make a second summary proposal next to the hidden one.
    issue.final_vote, issue.phase = True, 'samenvatten'
    db.session.commit()
    assert not ip.can_make_final(issue, w['mod'])


# ── 4. The page of a hidden proposal ───────────────────────────────────────

def test_hidden_proposal_page_only_for_who_may_hide_it(app, db, world):
    w = world
    hidden = w['hidden']
    path = f'/vraagstuk/{w["issue"].id}/voorstel/{hidden.id}'
    for who in ('author', 'contributor', 'other_mod'):
        assert _get(app, w[who], path).status_code == 404, who
    assert _get(app, None, path).status_code == 404
    # Also no actions on it for them, such as supporting it.
    assert _req(_client(app, w['contributor']), 'POST', path + '/steunen').status_code == 404
    for who in ('mod', 'senior', 'admin'):
        page = _html(app, w[who], path)
        assert 'Verborgen voorstel.' in page, who
        assert 'Dit voorstel is verborgen.' in page, who
        assert f'href="/moderator/vraagstuk/{w["issue"].id}/beheer#voorstel-{hidden.id}"' in page, who
    assert 'This proposal is hidden.' in _html(app, w['mod'], path, 'en')
    # A visible proposal has no such note; made visible again, the page is public.
    assert 'Dit voorstel is verborgen.' not in _html(app, w['mod'], f'/vraagstuk/{w["issue"].id}/voorstel/{w["shown"].id}')
    issues.unhide(hidden, w['mod'])
    db.session.commit()
    assert _get(app, w['contributor'], path).status_code == 200
    assert _get(app, None, path).status_code == 200
