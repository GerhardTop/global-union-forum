"""
Discussion step 2: the issue discussion and the proposal discussion are
separate. A reaction on a proposal (a post with issue_proposal_id), and any
reply under it, shows only on that proposal, grouped per version. The issue
page's shared discussion (and the thread and branch views of the same thread)
shows only the issue-level posts. No "about proposal / on version N" label, and
no note on the proposal page that the reaction also appears in the discussion.
"""
from datetime import datetime

from app import issue_proposals as ip
from app.models import Post
from app.routes.forum import post_url, without_proposal_branches
from tests.test_issue_polish import _client, _get, _issue, _req, make_user  # noqa: F401  (fixture)
from tests.test_forum_conduct import _fresh_rate_limits, fake_ai  # noqa: F401  (autouse)


def _post(db, issue, user, body, proposal=None, parent=None):
    post = Post(thread_id=issue.thread_id, user_id=user.id, author_name='', author_username=user.username,
                body=body, source_lang='nl', created_at=datetime.utcnow(),
                issue_proposal_id=proposal.id if proposal else None,
                proposal_version=ip.current_version(proposal) if proposal else None,
                parent_id=parent.id if parent else None)
    db.session.add(post)
    db.session.flush()
    return post


def _discussion(page):
    return page.split('id="panel-discussion"')[1].split('id="panel-proposals"')[0]


def test_issue_discussion_shows_only_issue_level_posts(app, db, make_user):
    host, anna, bob = make_user(), make_user(), make_user()
    issue = _issue(db, host)
    proposal = ip.create(issue, host, 'Een stelling.')
    db.session.commit()
    # Via the real forms: an issue-level reaction from the cockpit, a reaction from the proposal page.
    _req(_client(app, anna), 'POST', f'/forum/{issue.thread_id}', data={'body': 'Over het vraagstuk zelf.', 'return_to': issue.id})
    _req(_client(app, anna), 'POST', f'/forum/{issue.thread_id}',
         data={'body': 'Over het voorstel.', 'issue_proposal_id': proposal.id, 'return_proposal': '1'})
    on_proposal = Post.query.filter_by(body='Over het voorstel.').one()
    # Older data: a reply under a proposal reaction stays with that branch.
    _post(db, issue, bob, 'Antwoord in de voorsteltak.', parent=on_proposal)
    db.session.commit()

    for lang in ('nl', 'en'):
        disc = _discussion(_get(app, f'/vraagstuk/{issue.id}', bob, lang))
        assert 'Over het vraagstuk zelf.' in disc
        assert 'Over het voorstel.' not in disc and 'Antwoord in de voorsteltak.' not in disc
        assert 'gu-post__proposalref' not in disc and 'gu-versionchip' not in disc
        assert 'Bij voorstel' not in disc and 'About proposal' not in disc
    # The thread page of the same discussion agrees; the proposal branch is not reachable as a branch.
    thread = _get(app, f'/forum/{issue.thread_id}', bob)
    assert 'Over het vraagstuk zelf.' in thread and 'Over het voorstel.' not in thread
    assert _req(_client(app, bob), 'GET', f'/forum/{issue.thread_id}/tak/{on_proposal.id}').status_code == 404

    # The proposal page still has it, with the current version, and the counts are unchanged.
    page = _get(app, f'/vraagstuk/{issue.id}/voorstel/{proposal.id}', bob)
    assert 'Over het voorstel.' in page.split('id="reacties"')[1]
    assert 'Over het vraagstuk zelf.' not in page
    show = _get(app, f'/vraagstuk/{issue.id}', bob)
    assert '<span class="gu-rcount">1 reactie</span>' in show.split('id="panel-proposals"')[1]


def test_filter_drops_proposal_posts_and_everything_under_them(app, db, make_user):
    host = make_user()
    issue = _issue(db, host)
    proposal = ip.create(issue, host, 'Een stelling.')
    db.session.commit()
    top = _post(db, issue, host, 'Vraagstuk.')
    reply = _post(db, issue, host, 'Antwoord.', parent=top)
    tied = _post(db, issue, host, 'Voorstel.', proposal=proposal)
    under = _post(db, issue, host, 'Onder.', parent=tied)
    deeper = _post(db, issue, host, 'Dieper.', parent=under)
    kept = without_proposal_branches([deeper, under, tied, reply, top])
    assert {p.id for p in kept} == {top.id, reply.id}


def test_proposal_reaction_links_to_the_proposal_and_cannot_be_replied_to_in_the_discussion(app, db, make_user):
    host, anna, bob = make_user(), make_user(), make_user()
    issue = _issue(db, host)
    proposal = ip.create(issue, host, 'Een stelling.')
    db.session.commit()
    tied = _post(db, issue, anna, 'Bij het voorstel.', proposal=proposal)
    plain = _post(db, issue, anna, 'Bij het vraagstuk.')
    db.session.commit()
    with app.test_request_context():
        assert post_url(tied) == f'/vraagstuk/{issue.id}/voorstel/{proposal.id}#post-{tied.id}'
        assert post_url(plain).startswith(f'/forum/{issue.thread_id}')
    # A reply with a proposal reaction as parent is refused, like a missing parent.
    _req(_client(app, bob), 'POST', f'/forum/{issue.thread_id}', data={'body': 'Erop in.', 'parent_id': tied.id})
    assert Post.query.filter_by(body='Erop in.').count() == 0
    _req(_client(app, bob), 'POST', f'/forum/{issue.thread_id}', data={'body': 'Wel goed.', 'parent_id': plain.id})
    assert Post.query.filter_by(body='Wel goed.').one().parent_id == plain.id


def test_proposal_composer_has_no_shared_discussion_note(app, db, make_user):
    host = make_user()
    issue = _issue(db, host)
    proposal = ip.create(issue, host, 'Een stelling.')
    db.session.commit()
    for lang, text in (('nl', 'verschijnt ook in de gedeelde discussie'), ('en', 'also appears in the shared discussion')):
        page = _get(app, f'/vraagstuk/{issue.id}/voorstel/{proposal.id}', host, lang)
        assert 'class="gu-pv__composer"' in page and text not in page
        assert 'In de discussie' not in page and 'In the discussion' not in page
