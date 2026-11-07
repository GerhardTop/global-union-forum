"""Bij --force ruimt de voorbeeld-seed ook een vraagstuk met de oude titel
("VOORBEELD: ...") op, zodat een hernoeming geen tweede vraagstuk achterlaat."""


def test_force_cleans_up_old_voorbeeld_title(app, db):
    from app.models import Issue
    from app import demo_voorbeeld as dv
    dv.seed_demo_voorbeeld(force=True)
    issue = Issue.query.filter_by(title=dv._TITLE).one()
    issue.title = 'VOORBEELD: ' + dv._TITLE  # simuleer de oude productiestand
    # Een echte gebruiker volgt het voorbeeld; dat mag het opruimen niet blokkeren.
    from app.models import IssueFollow, User
    reader = User.query.first()
    db.session.add(IssueFollow(issue_id=issue.id, user_id=reader.id))
    db.session.commit()
    dv.seed_demo_voorbeeld(force=True)
    assert Issue.query.filter_by(title='VOORBEELD: ' + dv._TITLE).count() == 0
    assert Issue.query.filter_by(title=dv._TITLE).count() == 1
