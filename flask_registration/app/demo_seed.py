"""Demo-inhoud voor het vraagstuk-model.

Twee vraagstukken met voorstellen, feiten, discussie, steun, stemmen, reacties
en (in de vervolgfase) een tijdlijn van updates, zodat de schermen met echte,
gevarieerde inhoud te beoordelen zijn. Net als een echt forum: feiten die
uitgebreid zijn en feiten van een regel, soms met een link naar een dataset of
tabel; reacties die lang zijn en reacties van een paar woorden; en voorstellen
van verschillende kwaliteit.

Zes demo-personages met een eigen stem (DEMO_USER_EMAILS in app/routes/forum.py),
zodat de teksten vanzelf verschillen in lengte en toon. Er komen geen echte
accounts bij; de content staat op is_demo en blijft staan bij reset-content. De
steundrempel staat op 3, zodat een voorstel met deze personages op de stemlijst
kan komen.

Idempotent: draait de seed opnieuw, dan gebeurt er niets als de content er al
staat.

    flask --app run seed-demo-issues            # maakt de demo aan
    flask --app run seed-demo-issues --force    # eerst de oude demo weg, dan opnieuw
"""
from datetime import datetime, timedelta


# (sleutel -> voornaam, achternaam, gebruikersnaam, rol, leeftijd).
# De e-mails komen uit DEMO_USER_EMAILS (app/routes/forum.py); _get_or_create
# vindt de bestaande accounts terug op e-mail.
SPECS = {
    'jan':    ('Jan',    'Hofman (demo)',     'jan_h',    'member', 52),
    'fatima': ('Fatima', 'El Idrissi (demo)', 'fatima_e', 'member', 38),
    'sarah':  ('Sarah',  'Chen (demo)',       'sarah_c',  'expert', 34),
    'marcus': ('Marcus', 'Osei (demo)',       'marcus_o', 'member', 31),
    'henk':   ('Henk',   'Veenstra (demo)',   'henk_v',   'member', 67),
    'noa':    ('Noa',    'de Wit (demo)',     'noa_w',    'member', 23),
}

DEMO_SUPPORT_THRESHOLD = 3
_SENTINEL_TITLE = 'Hoe versterken we de Europese defensie zonder de NAVO te ondermijnen?'
_TITLE_C = 'Welke rol moet de EU spelen bij de opvang van vluchtelingen?'
# Oude demo-titel, alleen nog om op te ruimen bij --force (wordt niet meer gemaakt).
_TITLE_B_OLD = 'Hoe maken we de lokale democratie toegankelijker voor jongeren?'


def _people():
    """De demo-personages als {sleutel: (user, rol, leeftijd)}."""
    from app.routes.forum import DEMO_USER_EMAILS, _get_or_create_demo_user
    out = {}
    for key, (first, last, username, role, age) in SPECS.items():
        user = _get_or_create_demo_user(first, last, DEMO_USER_EMAILS[key], username)
        user.digest_email = False  # geen e-mailsamenvattingen naar de demo-accounts
        out[key] = (user, role, age)
    return out


def _post(db, thread, who, created_at, *, body=None, source_lang='nl', is_op=False,
          parent=None, kind=None, fact_direction=None, fact_source=None, fact_type=None,
          fact_title=None, issue_proposal_id=None, proposal_version=None):
    from app.models import Post
    user, role, age = who
    post = Post(
        thread_id=thread.id, user_id=user.id,
        parent_id=parent.id if parent is not None else None,
        author_name=f'{user.first_name} {user.last_name}'.strip(),
        author_username=user.username, author_role=role,
        author_age=age,
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
           deadline=None, closed=False):
    """Een vraagstuk met een eigen gesprek en openingsbericht."""
    from app.models import Issue, Thread, Post
    user = people[author][0]
    issue = Issue(
        title=title, intro=intro, kind='online', phase=phase,
        created_by_id=user.id, created_at=created_at, lang='nl',
        support_threshold=DEMO_SUPPORT_THRESHOLD, proposal_threshold_pct=50,
        show_interim=True, final_vote=True, is_closed=closed,
        starts_at=schedule[0], adjust_starts_at=schedule[1],
        vote_starts_at=schedule[2], vote_ends_at=schedule[3],
        phase_started_at=schedule[max(0, _phase_pos(phase))], phase_deadline=deadline,
    )
    db.session.add(issue)
    db.session.flush()
    thread = Thread(title_prefix=title, source_lang=issue.lang,
                    is_closed=closed, is_demo=True, created_at=created_at)
    db.session.add(thread)
    db.session.flush()
    from app import content_i18n
    from app.models import THREAD_EYEBROWS
    content_i18n.set_label(thread, 'eyebrow', THREAD_EYEBROWS['issue'])
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


def _followup(db, issue, people, author, text, when):
    """Een update in de vervolgtijdlijn (nieuwste wordt in beeld bovenaan)."""
    from app.models import IssueFollowup
    db.session.add(IssueFollowup(issue_id=issue.id, author_id=people[author][0].id,
                                 body=text, created_at=when))


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

    # ════════════════════════════════════════════════════════════════════
    # Vraagstuk A: Europese defensie, in de stemfase (rijk, veel variatie)
    # ════════════════════════════════════════════════════════════════════
    sch_a = (now - timedelta(days=22), now - timedelta(days=7),
             now - timedelta(days=3), now + timedelta(days=4))
    issue_a, thread_a = _issue(
        db, people, title=_SENTINEL_TITLE,
        intro=('Europa wil minder afhankelijk zijn van een enkele bondgenoot zonder de NAVO te '
               'verzwakken. Welke stap zetten we als eerste? Breng feiten in en denk mee.'),
        author='jan', created_at=now - timedelta(days=22), phase='stemmen',
        schedule=sch_a, deadline=sch_a[3])

    # ── Feiten: uitgebreid, kort, met tabel-link, zonder directe link, media ──
    fa1 = _post(db, thread_a, people['sarah'], now - timedelta(days=21), kind='feit', fact_direction='voor',
                fact_type='onderzoek', fact_source='https://milex.sipri.org/sipri',
                fact_title='EU-defensiebudgetten samen ruim groter dan dat van Rusland',
                body=('De gezamenlijke defensie-uitgaven van de EU-lidstaten lagen in 2024 boven de 300 '
                      'miljard euro, ruim drie keer zo hoog als die van Rusland. Het geld is alleen over '
                      '27 begrotingen verdeeld, waardoor veel capaciteit dubbel wordt aangeschaft. De '
                      'cijfers per land en per jaar staan in de SIPRI Military Expenditure Database.'))
    fa2 = _post(db, thread_a, people['marcus'], now - timedelta(days=21), kind='feit', fact_direction='tegen',
                fact_type='feit', fact_source='Eigen analyse (geen directe link)',
                fact_title='Twee commandostructuren kosten tijd bij een crisis',
                body='Twee commandostructuren naast elkaar kosten bij een crisis kostbare tijd.')
    fa3 = _post(db, thread_a, people['jan'], now - timedelta(days=20), kind='feit', fact_direction='neutraal',
                fact_type='feit', fact_source='NAVO-factsheet (geen directe link)',
                fact_title='NATO Response Force inzetbaar binnen vijf dagen',
                body='De snelle reactiemacht van de NAVO kan volgens de NAVO binnen vijf dagen worden ingezet.')
    fa4 = _post(db, thread_a, people['fatima'], now - timedelta(days=20), kind='feit', fact_direction='voor',
                fact_type='nieuwsartikel', fact_source='https://nos.nl/artikel/europese-defensie-inkoop',
                fact_title='Reportage: gezamenlijke inkoop drukt de prijs per eenheid',
                body=('Een reportage over een gezamenlijke munitie-aanbesteding laat zien dat de prijs per '
                      'eenheid daalt naarmate meer landen samen inkopen.'))
    fa5 = _post(db, thread_a, people['henk'], now - timedelta(days=19), kind='feit', fact_direction='neutraal',
                fact_type='onderzoek', fact_source='https://www.iiss.org/publications/the-military-balance',
                fact_title='Versnippering van wapensystemen maakt Europese defensie duur',
                body=('Sinds de jaren negentig is de defensie-uitgave in veel Europese landen als aandeel '
                      'van het bbp gedaald, terwijl het aantal verschillende wapensystemen juist hoog bleef. '
                      'De EU telt tientallen types gevechtsvoertuigen, fregatten en gevechtsvliegtuigen naast '
                      'elkaar, waar de Verenigde Staten met een handvol toe kunnen. Die versnippering maakt '
                      'onderhoud, opleiding en gezamenlijk optreden duur en traag, los van de vraag of er een '
                      'aparte Europese structuur komt. Het IISS brengt deze aantallen elk jaar in kaart in '
                      'The Military Balance.'))
    fa6 = _post(db, thread_a, people['noa'], now - timedelta(days=18), kind='feit', fact_direction='voor',
                fact_type='feit', fact_source='https://europa.eu/eurobarometer/',
                fact_title='Jongeren steunen meer Europese defensiesamenwerking',
                body='Uit de Eurobarometer: een ruime meerderheid van de 15 tot 24-jarigen steunt meer Europese samenwerking op defensie.')
    fa7 = _post(db, thread_a, people['marcus'], now - timedelta(days=18), kind='feit', fact_direction='tegen',
                fact_type='podcast', fact_source='https://www.politico.eu/podcast/',
                fact_title='Podcast: lidstaten aarzelen om bevoegdheden af te staan',
                body=('In een aflevering over Europese defensie leggen twee diplomaten uit waarom lidstaten '
                      'terughoudend zijn om zeggenschap over inzet aan Brussel over te dragen.'))
    for fact in (fa1, fa4, fa5, fa7):  # niet elk feit krijgt het merk: variatie in kwaliteit
        _fact_mark(db, fact)

    # ── Discussie: lang (Henk) en heel kort (Noa), met reacties erop ──
    da1 = _post(db, thread_a, people['henk'], now - timedelta(days=17),
                body=('Het is goed om te bedenken dat dit geen nieuwe discussie is. Al in de jaren vijftig lag '
                      'er een plan voor een Europese Defensiegemeenschap, dat in 1954 sneuvelde in het Franse '
                      'parlement. Daarna is defensie steeds nationaal gebleven, met de NAVO als gezamenlijk dak. '
                      'Wie nu pleit voor meer Europese samenwerking, loopt tegen dezelfde vragen aan als toen: '
                      'wie beslist over inzet, wie draagt de kosten, en wat gebeurt er met de nationale '
                      'zeggenschap? Ik ben niet tegen, maar we moeten die vragen niet overslaan omdat de urgentie '
                      'nu hoger is. Een structuur die je in haast optuigt, blijft decennia hangen.'))
    _post(db, thread_a, people['noa'], now - timedelta(days=17), parent=da1,
          body='Mooi verhaal, maar jongeren willen daden, geen geschiedenisles.')
    da2 = _post(db, thread_a, people['sarah'], now - timedelta(days=16),
                body=('Een eigen capaciteit voor crises aan de rand van Europa is aanvullend, geen vervanging '
                      'van de NAVO. Door bestaande eenheden te bundelen blijven de kosten beperkt en houd je de '
                      'aansturing bij de bestaande planning.'))
    _post(db, thread_a, people['marcus'], now - timedelta(days=16), parent=da2,
          body='Eens, mits de aansturing bij de NAVO blijft. Anders krijg je dubbel werk.')
    _post(db, thread_a, people['fatima'], now - timedelta(days=15),
          body=('Belangrijk is wie straks beslist over inzet en wie de rekening betaalt. Zonder heldere '
                'afspraken daarover blijft elk voorstel vrijblijvend.'))
    _post(db, thread_a, people['jan'], now - timedelta(days=14),
          body='Eens. De financiering hoort er vanaf het begin bij.')

    # ── Voorstellen van verschillende kwaliteit ──
    pa1 = _proposal(db, issue_a, people, 'jan', versions=[
        ('Richt een Europese snelle-reactiemacht op.',
         'Een gezamenlijke eenheid die snel inzetbaar is aan de rand van Europa.',
         now - timedelta(days=19)),
        ('Richt een Europese snelle-reactiemacht op van 20.000 militairen.',
         'Een staande eenheid van ongeveer 20.000 militairen, inzetbaar binnen vijf dagen.',
         now - timedelta(days=9)),
        ('Richt een Europese snelle-reactiemacht op onder NAVO-planning.',
         ('Een staande eenheid van ongeveer 20.000 militairen, inzetbaar binnen vijf dagen aan de rand van '
          'Europa, met de operationele aansturing binnen de bestaande NAVO-planning. Gefinancierd door '
          'bestaande nationale eenheden te bundelen.'),
         now - timedelta(days=5)),
    ])
    _support(db, pa1, people, ['fatima', 'sarah', 'henk'])
    _link_fact(db, pa1, fa1, people['jan'])
    _link_fact(db, pa1, fa2, people['marcus'])
    _votes(db, pa1, people, voor=['jan', 'sarah', 'henk'], tegen=['marcus'], onthouding=['fatima'], pad=(18, 9, 4))
    reacts_a1 = [
        ('marcus', 2, 'De vijf-dagen-norm is ambitieus. Is daar een onderbouwing voor?'),
        ('jan', 3, 'Klopt, de aansturing ligt nu expliciet bij de NAVO-planning. Zie versie 3.'),
        ('henk', 3, ('Dit is de juiste volgorde. Door de eenheid onder de bestaande planning te hangen voorkom '
                     'je precies de dubbele structuur waar ik bang voor was, en houd je de nationale '
                     'parlementen aan boord. Mijn eerdere bezwaar is hiermee grotendeels weg.')),
        ('noa', 3, 'Eindelijk concreet. Doen.'),
        ('fatima', 2, 'Twintigduizend is veel. Begin kleiner en bouw op.'),
        ('marcus', 3, 'De koppeling met de NAVO-planning neemt mijn grootste bezwaar weg.'),
        ('sarah', 3, 'Eens. Snelheid is juist het punt waar we nu tekortschieten.'),
    ]
    for i, (key, ver, text) in enumerate(reacts_a1):
        _post(db, thread_a, people[key], now - timedelta(days=4) + timedelta(hours=i),
              body=text, issue_proposal_id=pa1.id, proposal_version=ver)

    pa2 = _proposal(db, issue_a, people, 'sarah', versions=[
        ('Bundel de inkoop van munitie op Europees niveau.',
         'Gezamenlijke inkoop drukt de prijs per eenheid en voorkomt tekorten.',
         now - timedelta(days=13)),
    ])
    _support(db, pa2, people, ['marcus', 'henk', 'noa'])
    _link_fact(db, pa2, fa4, people['sarah'])
    _votes(db, pa2, people, voor=['sarah', 'henk'], tegen=['marcus'], onthouding=['noa'], pad=(11, 5, 2))
    _post(db, thread_a, people['jan'], now - timedelta(days=12),
          body='Dit kan los van de reactiemacht al winst opleveren.', issue_proposal_id=pa2.id, proposal_version=1)
    _post(db, thread_a, people['fatima'], now - timedelta(days=11),
          body='Wie beheert de voorraad en wie beslist over de verdeling?', issue_proposal_id=pa2.id, proposal_version=1)

    pa3 = _proposal(db, issue_a, people, 'noa', versions=[
        ('Europa moet gewoon één leger krijgen.',
         'Samen staan we sterker. Als we alles samenvoegen is het probleem opgelost.',
         now - timedelta(days=10)),
    ])
    _support(db, pa3, people, ['noa'])
    _post(db, thread_a, people['sarah'], now - timedelta(days=9),
          body=('Dit klinkt sterk, maar "alles samenvoegen" springt over de moeilijke vragen heen: wie beslist '
                'over inzet, en wat betekent dit voor de nationale grondwetten? Zonder antwoord daarop is het '
                'niet uitvoerbaar.'),
          issue_proposal_id=pa3.id, proposal_version=1)
    _post(db, thread_a, people['marcus'], now - timedelta(days=9),
          body='Een EU-leger haalt het nooit langs 27 parlementen.', issue_proposal_id=pa3.id, proposal_version=1)

    _proposal(db, issue_a, people, 'marcus', versions=[
        ('Houd defensie volledig bij de NAVO, investeer alleen nationaal bij.',
         'Geen nieuwe Europese structuur. Versterk de nationale bijdragen binnen de NAVO.',
         now - timedelta(days=9)),
    ])
    made.append(issue_a.title)

    # ════════════════════════════════════════════════════════════════════
    # Vraagstuk C: EU en opvang, in de vervolgfase (toont de updatetijdlijn)
    # ════════════════════════════════════════════════════════════════════
    sch_c = (now - timedelta(days=46), now - timedelta(days=28),
             now - timedelta(days=23), now - timedelta(days=11))
    issue_c, thread_c = _issue(
        db, people, title=_TITLE_C,
        intro=('Een eerlijke verdeling en humane opvang vragen om Europese afspraken. Welke aanpak kiezen we? '
               'Breng feiten in en denk mee.'),
        author='sarah', created_at=now - timedelta(days=46), phase='vervolg', schedule=sch_c)

    fc1 = _post(db, thread_c, people['fatima'], now - timedelta(days=45), kind='feit', fact_direction='voor',
                fact_type='onderzoek', fact_source='https://www.unhcr.org/refugee-statistics/',
                fact_title='Een klein deel van de lidstaten vangt het grootste deel op',
                body=('De aanvragen zijn ongelijk verdeeld: een handvol lidstaten neemt het grootste deel voor '
                      'zijn rekening, terwijl andere er nauwelijks aan meedoen. De cijfers per land en per jaar '
                      'staan in de statistiekendatabank van de UNHCR.'))
    fc2 = _post(db, thread_c, people['marcus'], now - timedelta(days=44), kind='feit', fact_direction='neutraal',
                fact_type='feit', fact_source='Eigen observatie (geen directe link)',
                fact_title='Eerdere verdeelafspraken werden slecht nageleefd',
                body='Eerdere afspraken over verdeling werden door meerdere landen niet nageleefd.')
    fc3 = _post(db, thread_c, people['henk'], now - timedelta(days=43), kind='feit', fact_direction='tegen',
                fact_type='onderzoek', fact_source='https://www.cbs.nl/',
                fact_title='Opvang en integratie kosten jaren, niet maanden',
                body=('Uit onderzoek naar eerdere opvanggolven blijkt dat integratie, taal en werk pas na jaren '
                      'resultaat geven, niet na maanden. Een fonds dat alleen de eerste opvang dekt, laat de '
                      'duurdere, langere fase bij de gemeenten liggen. Dat is een reden om de bekostiging breed '
                      'en meerjarig op te zetten, niet om het idee af te wijzen.'))
    fc4 = _post(db, thread_c, people['noa'], now - timedelta(days=42), kind='feit', fact_direction='voor',
                fact_type='feit', fact_source='https://ec.europa.eu/eurostat',
                fact_title='Meeste aanvragers zijn jong',
                body='Het grootste deel van de aanvragers is jonger dan 35.')
    for fact in (fc1, fc3):
        _fact_mark(db, fact)

    _post(db, thread_c, people['marcus'], now - timedelta(days=42),
          body='Een eerlijke verdeling is alleen houdbaar met duidelijke afspraken en controle op naleving.')
    _post(db, thread_c, people['henk'], now - timedelta(days=41),
          body=('Let op dat we niet alleen de eerste opvang regelen. De echte kosten zitten in de jaren erna, '
                'bij taal, onderwijs en werk. Als het fonds daar niet in voorziet, schuiven we het probleem '
                'door naar de gemeenten.'))

    pc1 = _proposal(db, issue_c, people, 'sarah', versions=[
        ('Richt een gezamenlijk opvangfonds op met een eerlijke verdeelsleutel.',
         'Lidstaten dragen naar draagkracht bij. Opvang en integratie worden samen bekostigd.',
         now - timedelta(days=40)),
        ('Richt een gezamenlijk, meerjarig opvangfonds op met een eerlijke verdeelsleutel.',
         ('Lidstaten dragen naar draagkracht bij. Het fonds bekostigt niet alleen de eerste opvang, maar ook '
          'de meerjarige integratie (taal, onderwijs, werk), met controle op naleving.'),
         now - timedelta(days=30)),
    ])
    _support(db, pc1, people, ['jan', 'fatima', 'marcus', 'henk'])
    _link_fact(db, pc1, fc1, people['sarah'])
    _link_fact(db, pc1, fc3, people['henk'])
    _votes(db, pc1, people, voor=['jan', 'fatima', 'sarah', 'henk'], tegen=['marcus'], onthouding=[], pad=(26, 6, 3))
    _post(db, thread_c, people['jan'], now - timedelta(days=24),
          body='Goede sleutel. De bekostiging naar draagkracht maakt het eerlijk.',
          issue_proposal_id=pc1.id, proposal_version=2)
    _post(db, thread_c, people['henk'], now - timedelta(days=24),
          body='Fijn dat de meerjarige integratie er in versie 2 bij is gekomen. Daarmee kan ik voor.',
          issue_proposal_id=pc1.id, proposal_version=2)

    # ── De vervolgtijdlijn: meerdere gedateerde updates (nieuwste in beeld bovenaan) ──
    _followup(db, issue_c, people, 'sarah',
              ('De stemming is afgerond en het voorstel voor een gezamenlijk, meerjarig opvangfonds is '
               'aangenomen. We bundelen de uitkomst en bereiden een korte notitie voor de betrokken '
               'Europarlementariërs voor.'),
              now - timedelta(days=11))
    _followup(db, issue_c, people, 'fatima',
              ('Eerste gesprek met twee Europarlementariërs gehad. Zij willen het idee van een meerjarig fonds '
               'agenderen bij de commissie en vroegen om een beknopte onderbouwing.'),
              now - timedelta(days=6))
    _followup(db, issue_c, people, 'sarah',
              ('De position paper is af en gedeeld met de commissie. Wie wil meelezen voor de volgende versie, '
               'kan zich melden. Zodra er een reactie is, zetten we die hier.'),
              now - timedelta(days=1))
    made.append(issue_c.title)

    db.session.commit()
    return f'Demo aangemaakt: {len(made)} vraagstukken.\n  - ' + '\n  - '.join(made)


def _remove_demo(db):
    """Verwijder de demo-vraagstukken en hun content (voor --force). Laat de
    demo-accounts staan. Ruimt ook de oude demo (vraagstuk B) op."""
    from app.models import (Issue, IssueProposal, IssueProposalVersion, IssueProposalSupport,
                            IssueProposalVote, IssueProposalFact, IssueFollowup, Post, Thread, ProposalReport)
    titles = [_SENTINEL_TITLE, _TITLE_C, _TITLE_B_OLD]
    issues = Issue.query.filter(Issue.title.in_(titles)).all()
    if not issues:
        return
    issue_ids = [i.id for i in issues]
    thread_ids = [i.thread_id for i in issues if i.thread_id]
    prop_ids = [p.id for p in IssueProposal.query.filter(IssueProposal.issue_id.in_(issue_ids))]
    post_ids = [p.id for p in Post.query.filter(Post.thread_id.in_(thread_ids))] if thread_ids else []
    if post_ids:
        from app import quality
        from app.models import PostLike
        quality.delete_for_posts(post_ids)
        PostLike.query.filter(PostLike.post_id.in_(post_ids)).delete(synchronize_session=False)
        Post.query.filter(Post.parent_id.in_(post_ids)).update({Post.parent_id: None}, synchronize_session=False)
        Post.query.filter(Post.id.in_(post_ids)).delete(synchronize_session=False)
    if prop_ids:
        for model in (IssueProposalVote, IssueProposalSupport, IssueProposalFact, IssueProposalVersion, ProposalReport):
            model.query.filter(model.proposal_id.in_(prop_ids)).delete(synchronize_session=False)
        from app import issue_proposals
        issue_proposals.delete_reads(prop_ids)
        IssueProposal.query.filter(IssueProposal.id.in_(prop_ids)).delete(synchronize_session=False)
    IssueFollowup.query.filter(IssueFollowup.issue_id.in_(issue_ids)).delete(synchronize_session=False)
    from app import issue_lifecycle
    from app.models import IssueEventDay
    issue_lifecycle.delete_follows(issue_ids=issue_ids)
    from app import notifications
    notifications.delete_for_issues(issue_ids)
    IssueEventDay.query.filter(IssueEventDay.issue_id.in_(issue_ids)).delete(synchronize_session=False)
    for issue in issues:
        issue.thread_id = None
    db.session.flush()
    from app import participants
    for issue in issues:
        participants.delete_for_issue(issue.id)
    event_ids = [i.event_id for i in issues]
    Issue.query.filter(Issue.id.in_([i.id for i in issues])).delete(synchronize_session=False)
    from app import events
    events.delete_empty(event_ids)  # events step 1b: an event without issues goes along
    if thread_ids:
        from app import content_i18n
        content_i18n.delete_for('threads', thread_ids)   # a bulk delete passes the ORM cleanup
        Thread.query.filter(Thread.id.in_(thread_ids)).delete(synchronize_session=False)
    db.session.commit()
