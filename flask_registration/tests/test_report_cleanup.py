"""Opruimen van meldingen (PostReport en ProposalReport) bij het verwijderen
van een bijdrage of een account. Op Postgres zou een achtergebleven melding
een foreign-key-fout geven; SQLite dwingt dat niet af, dus deze tests
controleren rechtstreeks dat de meldingen verdwijnen."""
import uuid

from app import bcrypt, db
from app.models import (IssueProposal, PostReport, ProposalReport, Post, Thread,
                        User)


def _user():
    name = f'u_{uuid.uuid4().hex[:8]}'
    u = User(username=name, email=f'{name}@example.com', verified=True,
             password_hash=bcrypt.generate_password_hash('Sterk1!ww').decode('utf-8'))
    u.set_role('contributor')
    u.accept_rules()
    db.session.add(u)
    db.session.commit()
    return u


def _post(author):
    t = Thread(eyebrow_nl='T', eyebrow_en='T', title_prefix_nl='Vraag', title_prefix_en='Question')
    db.session.add(t)
    db.session.flush()
    op = Post(thread_id=t.id, user_id=author.id, author_name='', author_username=author.username,
              body='Een bijdrage.', is_op=True, source_lang='nl')
    db.session.add(op)
    db.session.commit()
    return op


def _proposal(author):
    from app import issues
    issue = issues.create({'title': 'Een vraagstuk', 'intro': 'Toelichting.', 'kind': 'online'}, author)
    db.session.commit()
    prop = IssueProposal(issue_id=issue.id, statement='Een voorstel.', created_by_id=author.id)
    db.session.add(prop)
    db.session.commit()
    return prop


def test_delete_for_posts_removes_post_report(app, db):
    from app import quality
    author, reporter = _user(), _user()
    post = _post(author)
    db.session.add(PostReport(post_id=post.id, reporter_id=reporter.id, reason='ongepast'))
    db.session.commit()
    quality.delete_for_posts([post.id])
    db.session.commit()
    assert PostReport.query.filter_by(post_id=post.id).count() == 0


def test_detach_user_removes_own_post_report(app, db):
    from app import quality
    author, reporter = _user(), _user()
    post = _post(author)
    db.session.add(PostReport(post_id=post.id, reporter_id=reporter.id, reason='ongepast'))
    db.session.commit()
    quality.detach_user(reporter.id)
    db.session.commit()
    assert PostReport.query.filter_by(reporter_id=reporter.id).count() == 0


def test_detach_user_removes_own_proposal_report(app, db):
    from app import issue_proposals
    author, reporter = _user(), _user()
    prop = _proposal(author)
    db.session.add(ProposalReport(proposal_id=prop.id, reporter_id=reporter.id, reason='ongepast'))
    db.session.commit()
    issue_proposals.detach_user(reporter.id)
    db.session.commit()
    assert ProposalReport.query.filter_by(reporter_id=reporter.id).count() == 0


def test_detach_decider_keeps_report_without_name(app, db):
    from app import quality
    author, reporter, mod = _user(), _user(), _user()
    post = _post(author)
    db.session.add(PostReport(post_id=post.id, reporter_id=reporter.id, reason='ongepast',
                              decision='ok', decided_by_id=mod.id))
    db.session.commit()
    quality.detach_user(mod.id)
    db.session.commit()
    row = PostReport.query.filter_by(post_id=post.id).first()
    assert row is not None and row.decided_by_id is None
