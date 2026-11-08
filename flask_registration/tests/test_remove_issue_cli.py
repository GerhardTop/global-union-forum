"""flask remove-issue PREFIX: standaard een dry-run, pas met --execute echt weg."""


def _make_test_issue(db):
    from app import demo_voorbeeld as dv
    from app.models import Issue
    dv.seed_demo_voorbeeld(force=True)
    issue = Issue.query.filter_by(title=dv._TITLE).one()
    issue.title = 'TEST oud vraagstuk'
    db.session.commit()
    return issue.id


def test_dry_run_removes_nothing(app, db):
    from app.models import Issue
    issue_id = _make_test_issue(db)
    result = app.test_cli_runner().invoke(args=['remove-issue', 'TEST'])
    assert result.exit_code == 0, result.output
    assert 'TEST oud vraagstuk' in result.output and 'Dry-run' in result.output
    assert db.session.get(Issue, issue_id) is not None


def test_execute_removes_only_matching_issue(app, db):
    from app.models import Issue
    issue_id = _make_test_issue(db)
    result = app.test_cli_runner().invoke(args=['remove-issue', 'TEST', '--execute', '--yes'])
    assert result.exit_code == 0, result.output
    assert 'verwijderd' in result.output
    db.session.expire_all()
    assert db.session.get(Issue, issue_id) is None


def test_short_prefix_is_refused(app, db):
    result = app.test_cli_runner().invoke(args=['remove-issue', 'TE'])
    assert result.exit_code != 0


def test_execute_removes_backfilled_issue_with_old_proposal(app, db, verified_user):
    """Een vraagstuk uit de backfill deelt zijn gesprek met een oud voorstel."""
    from app import issues, proposals
    from app.models import Issue, Proposal, Thread
    user = verified_user
    thread = Thread(eyebrow_nl='Forum', eyebrow_en='Forum', title_prefix_nl='TEST oud', title_prefix_en='TEST oud')
    db.session.add(thread)
    db.session.flush()
    old = proposals.create(thread, 'publiek', user, minutes={'feiten': 1440, 'bijstellen': 1440, 'stemmen': 1440},
                           threshold=60, show_live_tally=True)
    db.session.commit()
    issues.backfill()
    db.session.refresh(old)
    issue = db.session.get(Issue, old.issue_id)
    issue.title = 'TEST oud voorstel'
    db.session.commit()
    issue_id, thread_id, old_id = issue.id, thread.id, old.id
    result = app.test_cli_runner().invoke(args=['remove-issue', 'TEST', '--execute', '--yes'])
    assert result.exit_code == 0, result.output
    db.session.expire_all()
    assert db.session.get(Issue, issue_id) is None
    assert db.session.get(Thread, thread_id) is None
    assert db.session.get(Proposal, old_id) is None
