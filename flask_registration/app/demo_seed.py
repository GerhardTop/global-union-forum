"""Demo-inhoud voor het vraagstuk-model.

Een paar vraagstukken met voorstellen, feiten, discussie, steun, stemmen en
reacties, zodat de schermen (cockpit, voorstel-detail, feitenpagina,
voorstellenpagina en het overzicht) met echte inhoud te beoordelen zijn.
Sommige voorstellen zijn uitgebreid (meerdere versies, veel reacties, een
stemming), andere beperkt.

Hergebruikt de bestaande forum-demo-personages (DEMO_USER_EMAILS), zodat er
geen nieuwe accounts bijkomen en reset-content de content laat staan (is_demo
op de gesprekken en berichten). De steundrempel van de demo-vraagstukken staat
op 3, zodat een voorstel met deze vier personages op de stemlijst kan komen.

Idempotent: draait de seed opnieuw, dan gebeurt er niets als de content er al
staat.

    flask --app run seed-demo-issues            # maakt de demo aan
    flask --app run seed-demo-issues --force    # eerst de oude demo weg, dan opnieuw
"""
from datetime import datetime, timedelta


# (sleutel -> voornaam, achternaam, gebruikersnaam, rol, badge, leeftijd).
# De e-mails komen uit DEMO_USER_EMAILS (app/routes/forum.py); _get_or_create
# vindt de bestaande accounts terug op e-mail.
SPECS = {
    'jan':    ('Jan',    'Hofman (demo)',     'jan_h',    'member', None, 52),
    'fatima': ('Fatima', 'El Idrissi (demo)', 'fatima_e', 'member', None, 38),
    'sarah':  ('Sarah',  'Chen (demo)',       'sarah_c',  'expert', 'Internationale betrekkingen · LSE', 34),
    'marcus': ('Marcus', 'Osei (demo)',       'marcus_o', 'member', None, 31),
}

DEMO_SUPPORT_THRESHOLD = 3
_SENTINEL_TITLE = 'Hoe versterken we de Europese defensie zonder de NAVO te ondermijnen?'
_TITLE_B = 'Hoe maken we de lokale democratie toegankelijker voor jongeren?'
_TITLE_C = 'Welke rol moet de EU spelen bij de opvang van vluchtelingen?'


def _people():
    """De vier demo-personages als {sleutel: (user, rol, badge, leeftijd)}."""
    from app.routes.forum import DEMO_USER_EMAILS, _get_or_create_demo_user
    out = {}
    for key, (first, last, username, role, badge, age) in SPECS.items():
        user = _get_or_create_demo_user(first, last, DEMO_USER_EMAILS[key], username)
        user.digest_email = False  # geen e-mailsamenvattingen naar de demo-accounts
        out[key] = (user, role, badge, age)
    return out


def _post(db, thread, who, created_at, *, body=None, source_lang='nl', is_op=False,
          parent=None, kind=None, fact_direction=None, fact_source=None, fact_type=None,
          fact_title=None, issue_proposal_id=None, proposal_version=None):
    from app.models import Post
    user, role, badge, age = who
    post = Post(
        thread_id=thread.id, user_id=user.id,
        parent_id=parent.id if parent is not None else None,
        author_name=f'{user.first_name} {user.last_name}'.strip(),
        author_username=user.username, author_role=role,
        author_badge_nl=badge, author_badge_en=badge, author_age=age,
        body=body, source_lang=source_lang, is_op=is_op, is_demo=True, vote_count=0,
        kind=kind, fact_direction=fact_direction, fact_source=fact_source, fact_type=fact_type, fact_title=fact_title,
        issue_proposal_id=issue_proposal_id, proposal_version=proposal_version,
        created_at=created_at,
    )
    db.session.add(post)
    db.session.flush()
    return post


def _fact_mark(db, post):
    """Geef een feit het AI-kwaliteitsmerk (alle vijf criteria kloppen)."""
    from app.models import QualityAward
    db.session.add(QualityAward(
        post_id=post.id, source='demo', status='afgewezen', criterion=None,
        fact_mark=True, fact_has_source=True, fact_link_findable=True,
        fact_has_location=True, fact_on_topic=True, fact_is_new=True,
    ))


def _phase_pos(phase):
    return {'feiten': 0, 'bijstellen': 1, 'stemmen': 2, 'samenvatten': 3, 'vervolg': 3}.get(phase, 0)


def _issue(db, people, *, title, intro, author, created_at, phase, schedule,
           deadline=None, closed=False, followup=None):
    """Een vraagstuk met een eigen gesprek en openingsbericht."""
    from app.models import Issue, Thread, Post
    user = people[author][0]
    issue = Issue(
        title=title, intro=intro, kind='online', phase=phase,
        created_by_id=user.id, created_at=created_at, lang='nl',
        support_threshold=DEMO_SUPPORT_THRESHOLD, proposal_threshold_pct=50,
        show_interim=True, final_vote=True, is_closed=closed, followup_text=followup,
        followup_at=(created_at + timedelta(days=1)) if followup else None,
        starts_at=schedule[0], adjust_starts_at=schedule[1],
        vote_starts_at=schedule[2], vote_ends_at=schedule[3],
        phase_started_at=schedule[max(0, _phase_pos(phase))], phase_deadline=deadline,
    )
    db.session.add(issue)
    db.session.flush()
    thread = Thread(eyebrow_nl='Vraagstuk', eyebrow_en='Issue',
                    title_prefix_nl=title, title_prefix_en=title,
                    is_closed=closed, is_demo=True, created_at=created_at)
    db.session.add(thread)
    db.session.flush()
    issue.thread_id = thread.id
    op = Post(thread_id=thread.id, user_id=user.id,
              author_name=f'{user.first_name} {user.last_name}'.strip(),
              author_username=user.username, author_role=people[author][1],
              body=intro, source_lang='nl', is_op=True, is_demo=True, created_at=created_at)
    db.session.add(op)
    db.session.flush()
    return issue, thread


def _proposal(db, issue, people, author, versions, *, is_final=False):
    """Een voorstel met één of meer versies (stelling, toelichting, tijdstip);
    de laatste is de huidige."""
    from app.models import IssueProposal, IssueProposalVersion
    user = people[author][0]
    last = versions[-1]
    proposal = IssueProposal(
        issue_id=issue.id, statement=last[0], explanation=last[1],
        created_by_id=user.id, created_at=versions[0][2], lang='nl', is_final=is_final,
        original_statement=versions[0][0] if len(versions) > 1 else None,
        original_explanation=versions[0][1] if len(versions) > 1 else None,
        revised_at=last[2] if len(versions) > 1 else None,
    )
    db.session.add(proposal)
    db.session.flush()
    for i, (stmt, expl, when) in enumerate(versions, start=1):
        db.session.add(IssueProposalVersion(proposal_id=proposal.id, number=i,
                                            statement=stmt, explanation=expl, created_at=when))
    db.session.flush()
    return proposal


def _support(db, proposal, people, keys):
    from app.models import IssueProposalSupport
    for key in keys:
        db.session.add(IssueProposalSupport(proposal_id=proposal.id, user_id=people[key][0].id,
                                            created_at=proposal.created_at + timedelta(hours=6)))


def _link_fact(db, proposal, fact, by):
    from app.models import IssueProposalFact
    db.session.add(IssueProposalFact(proposal_id=proposal.id, fact_post_id=fact.id,
                                     linked_by_id=by[0].id, created_at=proposal.created_at))


def _votes(db, proposal, people, voor, tegen, onthouding, pad=(0, 0, 0)):
    """Stemmen van de personages, plus anonieme opvulling voor een vollere
    tussenstand (user_id NULL, zoals een stem van een verwijderd account)."""
    from app.models import IssueProposalVote
    now = datetime.utcnow()
    for choice, keys in (('voor', voor), ('tegen', tegen), ('onthouding', onthouding)):
        for key in keys:
            db.session.add(IssueProposalVote(proposal_id=proposal.id, user_id=people[key][0].id,
                                             choice=choice, created_at=now - timedelta(hours=2)))
    for choice, n in zip(('voor', 'tegen', 'onthouding'), pad):
        for _ in range(n):
            db.session.add(IssueProposalVote(proposal_id=proposal.id, user_id=None,
                                             choice=choice, created_at=now - timedelta(hours=3)))


def seed_demo_issues(force=False):
    """Maak de demo-vraagstukken. Geeft een korte samenvatting terug."""
    from app import db
    from app.models import Issue

    if Issue.query.filter_by(title=_SENTINEL_TITLE).first() is not None:
        if not force:
            return 'Demo stond er al; niets gedaan (gebruik --force om opnieuw te maken).'
        _remove_demo(db)

    people = _people()
    db.session.flush()
    now = datetime.utcnow()
    made = []

    # Vraagstuk 1: Europese defensie, in de stemfase (rijk)
    sch_a = (now - timedelta(days=20), now - timedelta(days=6),
             now - timedelta(days=3), now + timedelta(days=4))
    issue_a, thread_a = _issue(
        db, people, title=_SENTINEL_TITLE,
        intro=('Europa wil minder afhankelijk zijn van een enkele bondgenoot zonder de NAVO te '
               'verzwakken. Welke stap zetten we als eerste? Breng feiten in en denk mee.'),
        author='jan', created_at=now - timedelta(days=20), phase='stemmen',
        schedule=sch_a, deadline=sch_a[3])

    f1 = _post(db, thread_a, people['sarah'], now - timedelta(days=19), kind='feit', fact_direction='voor',
               fact_source='https://www.sipri.org/databases/milex',
               fact_title='EU-defensiebudget samen groter dan dat van Rusland', body='De Europese defensiebegrotingen samen zijn groter dan die van Rusland.')
    f2 = _post(db, thread_a, people['marcus'], now - timedelta(days=18), kind='feit', fact_direction='tegen',
               fact_source='https://www.rand.org/pubs/research_reports.html',
               fact_title='Dubbele commandostructuur kost tijd bij een crisis', body='Twee commandostructuren naast elkaar kosten tijd bij een crisis.')
    f3 = _post(db, thread_a, people['jan'], now - timedelta(days=18), kind='feit', fact_direction='neutraal',
               fact_source='NAVO-factsheet (geen directe link)',
               fact_title='NATO Response Force inzetbaar binnen vijf dagen', body='De NATO Response Force kan binnen vijf dagen worden ingezet.')
    f4 = _post(db, thread_a, people['fatima'], now - timedelta(days=17), kind='feit', fact_direction='voor',
               fact_type='media', fact_source='https://nos.nl/artikel/europese-defensie',
               fact_title='Reportage: gezamenlijke inkoop drukt de prijs per eenheid', body='Reportage over gezamenlijke inkoop en de prijsdaling per eenheid.')
    f5 = _post(db, thread_a, people['marcus'], now - timedelta(days=16), kind='feit', fact_direction='tegen',
               fact_source='https://www.politico.eu/',
               fact_title='Niet alle lidstaten willen bevoegdheden afstaan aan Brussel', body='Niet alle lidstaten willen bevoegdheden aan Brussel overdragen.')
    for fact in (f1, f2, f4, f5):
        _fact_mark(db, fact)

    d1 = _post(db, thread_a, people['sarah'], now - timedelta(days=15),
               body=('Een eigen capaciteit voor crises aan de rand van Europa is aanvullend, '
                     'geen vervanging van de NAVO. Door bestaande eenheden te bundelen blijven de kosten beperkt.'))
    _post(db, thread_a, people['marcus'], now - timedelta(days=14), parent=d1,
          body='Eens, mits de aansturing bij de NAVO-planning blijft. Anders krijg je dubbel werk.')
    _post(db, thread_a, people['jan'], now - timedelta(days=13),
          body='Wat mij betreft hoort hier ook de vraag bij hoe we dit betalen. Zonder dat wordt elk voorstel vrijblijvend.')

    p1 = _proposal(db, issue_a, people, 'jan', versions=[
        ('Richt een Europese snelle-reactiemacht op.',
         'Een gezamenlijke eenheid die snel inzetbaar is aan de rand van Europa.',
         now - timedelta(days=18)),
        ('Richt een Europese snelle-reactiemacht op van 20.000 militairen.',
         'Een staande eenheid van ongeveer 20.000 militairen, inzetbaar binnen vijf dagen.',
         now - timedelta(days=8)),
        ('Richt een Europese snelle-reactiemacht op onder NAVO-planning.',
         ('Een staande eenheid van ongeveer 20.000 militairen, inzetbaar binnen vijf dagen aan de '
          'rand van Europa, met de operationele aansturing binnen de bestaande NAVO-planning. '
          'Gefinancierd door bestaande nationale eenheden te bundelen.'),
         now - timedelta(days=5)),
    ])
    _support(db, p1, people, ['fatima', 'sarah', 'marcus'])
    _link_fact(db, p1, f1, people['jan'])
    _link_fact(db, p1, f2, people['marcus'])
    _votes(db, p1, people, voor=['jan', 'sarah'], tegen=['marcus'], onthouding=['fatima'], pad=(15, 9, 3))
    reacts_1 = [
        ('marcus', 2, 'De vijf-dagen-norm is ambitieus. Is daar een onderbouwing voor?'),
        ('jan', 3, 'Klopt, de aansturing ligt nu expliciet bij de NAVO-planning. Zie versie 3.'),
        ('sarah', 3, 'De bundeling van eenheden maakt dit betaalbaar. Goede stap.'),
        ('fatima', 2, 'Twintigduizend is veel. Begin kleiner en bouw op.'),
        ('marcus', 3, 'De koppeling met de NAVO-planning neemt mijn grootste bezwaar weg.'),
        ('jan', 1, 'De eerste versie was nog te vaag. De huidige is concreet genoeg.'),
        ('sarah', 3, 'Eens. Snelheid is juist het punt waar we nu tekortschieten.'),
    ]
    for i, (key, ver, text) in enumerate(reacts_1):
        _post(db, thread_a, people[key], now - timedelta(days=4) + timedelta(hours=i),
              body=text, issue_proposal_id=p1.id, proposal_version=ver)

    p2 = _proposal(db, issue_a, people, 'sarah', versions=[
        ('Bundel de inkoop van munitie op Europees niveau.',
         'Gezamenlijke inkoop drukt de prijs per eenheid en voorkomt tekorten.',
         now - timedelta(days=12)),
    ])
    _support(db, p2, people, ['marcus'])
    _link_fact(db, p2, f4, people['sarah'])
    _post(db, thread_a, people['jan'], now - timedelta(days=11),
          body='Dit kan los van de reactiemacht al winst opleveren.', issue_proposal_id=p2.id, proposal_version=1)
    _post(db, thread_a, people['fatima'], now - timedelta(days=10),
          body='Wie beheert de voorraad en wie beslist over de verdeling?', issue_proposal_id=p2.id, proposal_version=1)

    _proposal(db, issue_a, people, 'marcus', versions=[
        ('Houd defensie volledig bij de NAVO, investeer alleen nationaal bij.',
         'Geen nieuwe Europese structuur. Versterk de nationale bijdragen binnen de NAVO.',
         now - timedelta(days=9)),
    ])
    made.append(issue_a.title)

    # Vraagstuk 2: lokale democratie, in de feitenfase (bescheiden)
    sch_b = (now - timedelta(days=4), now + timedelta(days=17),
             now + timedelta(days=22), now + timedelta(days=29))
    issue_b, thread_b = _issue(
        db, people, title=_TITLE_B,
        intro=('Jongeren haken af bij de lokale politiek. Welke stappen helpen echt? '
               'Deel feiten en voorbeelden en denk mee.'),
        author='fatima', created_at=now - timedelta(days=4), phase='feiten',
        schedule=sch_b, deadline=sch_b[1])
    _post(db, thread_b, people['sarah'], now - timedelta(days=3), kind='feit', fact_direction='voor',
          fact_source='https://www.scp.nl/',
          fact_title='Opkomst jongeren bij gemeenteraad ver onder gemiddelde', body='De opkomst onder jongeren bij gemeenteraadsverkiezingen ligt ver onder het gemiddelde.')
    fb2 = _post(db, thread_b, people['marcus'], now - timedelta(days=3), kind='feit', fact_direction='neutraal',
                fact_source='https://www.prodemos.nl/',
                fact_title='Gemeenten experimenteren met jongerenraden met eigen budget', body='Enkele gemeenten experimenteren met jongerenraden met een eigen budget.')
    _fact_mark(db, fb2)
    _post(db, thread_b, people['jan'], now - timedelta(days=2),
          body='Begin bij de scholen. Zonder uitleg over wat de raad doet, blijft het abstract.')
    pb1 = _proposal(db, issue_b, people, 'sarah', versions=[
        ('Geef elke gemeente een jongerenraad met een eigen budget.',
         'Een vast budget maakt de raad echt in plaats van symbolisch.',
         now - timedelta(days=2)),
    ])
    _support(db, pb1, people, ['marcus'])
    _post(db, thread_b, people['jan'], now - timedelta(days=1),
          body='Een budget helpt, maar borg ook dat de raad echt gehoord wordt.',
          issue_proposal_id=pb1.id, proposal_version=1)
    pb2 = _proposal(db, issue_b, people, 'marcus', versions=[
        ('Laat jongeren vanaf 16 meestemmen bij lokale verkiezingen.',
         'Eerder meedoen vergroot de betrokkenheid op langere termijn.',
         now - timedelta(days=1)),
    ])
    _support(db, pb2, people, ['sarah'])
    made.append(issue_b.title)

    # Vraagstuk 3: EU en opvang, afgerond met uitslag
    sch_c = (now - timedelta(days=40), now - timedelta(days=22),
             now - timedelta(days=17), now - timedelta(days=5))
    issue_c, thread_c = _issue(
        db, people, title=_TITLE_C,
        intro=('Een eerlijke verdeling en humane opvang vragen om Europese afspraken. '
               'Welke aanpak kiezen we?'),
        author='sarah', created_at=now - timedelta(days=40), phase='vervolg', schedule=sch_c,
        followup=('Het voorstel voor een gezamenlijk opvangfonds met een eerlijke verdeling is '
                  'aangenomen. De initiatiefnemers werken het samen met experts verder uit.'))
    fc1 = _post(db, thread_c, people['fatima'], now - timedelta(days=39), kind='feit', fact_direction='voor',
                fact_source='https://www.unhcr.org/',
                fact_title='Enkele lidstaten vangen grootste deel van de aanvragen op', body='Een klein aantal lidstaten vangt nu het grootste deel van de aanvragen op.')
    _fact_mark(db, fc1)
    _post(db, thread_c, people['marcus'], now - timedelta(days=38),
          body='Een eerlijke verdeling is alleen houdbaar met duidelijke afspraken en controle.')
    pc1 = _proposal(db, issue_c, people, 'sarah', versions=[
        ('Richt een gezamenlijk opvangfonds op met een eerlijke verdeelsleutel.',
         'Lidstaten dragen naar draagkracht bij. Opvang en integratie worden samen bekostigd.',
         now - timedelta(days=37)),
    ])
    _support(db, pc1, people, ['jan', 'fatima', 'marcus'])
    _link_fact(db, pc1, fc1, people['sarah'])
    _votes(db, pc1, people, voor=['jan', 'fatima', 'sarah'], tegen=['marcus'], onthouding=[], pad=(21, 7, 4))
    _post(db, thread_c, people['jan'], now - timedelta(days=10),
          body='Goede sleutel. De bekostiging naar draagkracht maakt het eerlijk.',
          issue_proposal_id=pc1.id, proposal_version=1)
    made.append(issue_c.title)

    db.session.commit()
    return f'Demo aangemaakt: {len(made)} vraagstukken.\n  - ' + '\n  - '.join(made)


def _remove_demo(db):
    """Verwijder de demo-vraagstukken en hun content (voor --force). Laat de
    demo-accounts staan."""
    from app.models import (Issue, IssueProposal, IssueProposalVersion, IssueProposalSupport,
                            IssueProposalVote, IssueProposalFact, Post, Thread, QualityAward)
    titles = [_SENTINEL_TITLE, _TITLE_B, _TITLE_C]
    issues = Issue.query.filter(Issue.title.in_(titles)).all()
    if not issues:
        return
    issue_ids = [i.id for i in issues]
    thread_ids = [i.thread_id for i in issues if i.thread_id]
    prop_ids = [p.id for p in IssueProposal.query.filter(IssueProposal.issue_id.in_(issue_ids))]
    post_ids = [p.id for p in Post.query.filter(Post.thread_id.in_(thread_ids))] if thread_ids else []
    # Volgorde volgens de foreign keys, net als reset-content: eerst alles wat
    # naar de berichten verwijst (merk, moderatie, feitstemmen en de koppeling
    # van feiten aan voorstellen) via de bestaande helper, dan de likes, dan de
    # self-referentie los en de berichten weg, en pas daarna de voorstellen.
    if post_ids:
        from app import quality
        from app.models import PostLike
        quality.delete_for_posts(post_ids)
        PostLike.query.filter(PostLike.post_id.in_(post_ids)).delete(synchronize_session=False)
        Post.query.filter(Post.parent_id.in_(post_ids)).update({Post.parent_id: None}, synchronize_session=False)
        Post.query.filter(Post.id.in_(post_ids)).delete(synchronize_session=False)
    if prop_ids:
        for model in (IssueProposalVote, IssueProposalSupport, IssueProposalFact, IssueProposalVersion):
            model.query.filter(model.proposal_id.in_(prop_ids)).delete(synchronize_session=False)
        IssueProposal.query.filter(IssueProposal.id.in_(prop_ids)).delete(synchronize_session=False)
    for issue in issues:
        issue.thread_id = None
    db.session.flush()
    Issue.query.filter(Issue.id.in_([i.id for i in issues])).delete(synchronize_session=False)
    if thread_ids:
        Thread.query.filter(Thread.id.in_(thread_ids)).delete(synchronize_session=False)
    db.session.commit()
