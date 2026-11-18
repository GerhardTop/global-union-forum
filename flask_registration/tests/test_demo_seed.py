"""The demo content (app/demo_seed.py): two issues (A in the voting phase,
C in the follow-up phase with a timeline of updates), six demo characters,
idempotent, and --force also clears the old demo issue B."""
import pytest

from app import demo_seed
from app.models import Issue, IssueFollowup, IssueProposal, Post, User
from app.routes.forum import DEMO_USER_EMAILS
from tests.test_delete_issue import _clean_session, _forget_deleted  # noqa: F401  (autouse fixture)
from tests.test_issue_polish import _get, _issue, make_user  # noqa: F401  (fixture)


@pytest.fixture
def clean_demo(db):
    before = {u.email for u in User.query.filter(User.email.in_(DEMO_USER_EMAILS.values()))}
    yield
    db.session.rollback()
    demo_seed._remove_demo(db)
    _forget_deleted()
    from app import quality
    for user in User.query.filter(User.email.in_(DEMO_USER_EMAILS.values())).all():
        if user.email not in before:  # only the accounts this test created
            quality.detach_user(user.id)
            db.session.delete(user)
    db.session.commit()
    _forget_deleted()


def test_seed_makes_two_issues_with_six_characters_and_a_timeline(app, db, clean_demo, make_user):
    assert {'henk', 'noa'} <= set(DEMO_USER_EMAILS)
    # An old demo issue B is cleared by --force.
    _issue(db, make_user(), demo_seed._TITLE_B_OLD)
    # (A is not there yet, so a plain run creates A and C; B stays until --force.)
    out = demo_seed.seed_demo_issues()
    assert 'Demo aangemaakt: 2 vraagstukken.' in out
    a = Issue.query.filter_by(title=demo_seed._SENTINEL_TITLE).one()
    c = Issue.query.filter_by(title=demo_seed._TITLE_C).one()
    assert (a.phase, c.phase) == ('stemmen', 'vervolg')
    assert IssueProposal.query.filter_by(issue_id=a.id).count() == 4
    kinds = {p.fact_type for p in Post.query.filter_by(thread_id=a.thread_id, kind='feit')}
    assert kinds == {'onderzoek', 'feit', 'nieuwsartikel', 'podcast'}
    authors = {p.author_username for p in Post.query.filter(Post.thread_id.in_([a.thread_id, c.thread_id]))}
    assert {'henk_v', 'noa_w'} <= authors
    # C's follow-up timeline: three dated updates, newest first.
    bodies = [u.body for u in c.followups]
    assert len(bodies) == 3 and bodies[0].startswith('De position paper is af')
    page = _get(app, f'/vraagstuk/{c.id}')
    section = page.split('id="vervolg"')[1].split('</section>')[0]
    assert section.count('<li class="gu-followup__item') == 3 and 'Nieuwste' in section
    # Idempotent without --force.
    assert 'niets gedaan' in demo_seed.seed_demo_issues()
    assert Issue.query.filter_by(title=demo_seed._TITLE_C).count() == 1
    # --force: the old content (and B) goes, a fresh A and C come back.
    old_ids = {a.id, c.id}
    assert 'Demo aangemaakt: 2 vraagstukken.' in demo_seed.seed_demo_issues(force=True)
    _forget_deleted()
    assert Issue.query.filter_by(title=demo_seed._TITLE_B_OLD).count() == 0
    fresh = {i.id for i in Issue.query.filter(Issue.title.in_([demo_seed._SENTINEL_TITLE, demo_seed._TITLE_C]))}
    assert len(fresh) == 2
    # (SQLite reuses ids, so count the timeline rows: only the fresh C's three.)
    assert IssueFollowup.query.filter(IssueFollowup.issue_id.in_(old_ids | fresh)).count() == 3
