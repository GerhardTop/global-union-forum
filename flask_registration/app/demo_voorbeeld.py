"""Een uitgebreid voorbeeld-vraagstuk voor een test met echte gebruikers.

Eén vraagstuk met een titel die met VOORBEELD begint, een ruime toelichting,
ongeveer tien feiten met een bron en een korte duiding, één rijk uitgewerkt
voorstel met meerdere versies en reacties, en een lange discussie tot vier
lagen diep met per deelnemer een eigen schrijfstijl. De inhoud staat in het
Nederlands; de site vertaalt de Engelse weergave automatisch.

Hergebruikt de helpers en de vier demo-personages uit demo_seed.py, dus er
komen geen accounts bij en reset-content laat de inhoud staan (is_demo). Apart
commando, zodat je dit los van de gewone demo op productie kunt zetten:

    flask --app run seed-demo-voorbeeld
    flask --app run seed-demo-voorbeeld --force
"""
from datetime import datetime, timedelta

from app.demo_seed import (_people, _post, _fact_mark, _issue, _proposal,
                           _support, _link_fact)

_TITLE = ('VOORBEELD: Hoe maken we wereldwijde belastingafspraken voor '
          'multinationals eerlijk en afdwingbaar?')

_INTRO = (
    'Grote bedrijven verdienen in veel landen tegelijk, maar betalen hun '
    'belasting vaak op één plek met een laag tarief. Daardoor lopen landen geld '
    'mis dat nergens wordt belast. Er bestaan sinds kort wereldwijde afspraken, '
    'zoals een minimumtarief, maar de uitvoering en de handhaving blijven lastig. '
    'De vraag voor dit vraagstuk is hoe we die afspraken eerlijk maken voor arme '
    'en rijke landen, en tegelijk afdwingbaar. Breng feiten in, deel voorbeelden '
    'en denk mee. Dit is een voorbeeld-vraagstuk om de site mee te testen.')

# (auteur, richting, bron, type, tekst met duiding)
_FACTS = [
    ('sarah', 'voor', 'https://www.oecd.org/tax/beps/', None,
     'Volgens de OESO lopen overheden wereldwijd jaarlijks tussen de 100 en 240 miljard '
     'dollar aan belasting mis doordat bedrijven winst verschuiven naar laagbelaste landen. '
     'Dat is de kern van het probleem.'),
    ('jan', 'voor', 'https://www.oecd.org/tax/beps/', None,
     'Meer dan 140 landen hebben een wereldwijd minimumtarief van 15 procent voor grote '
     'bedrijven onderschreven. De afspraak bestaat dus al. Het gaat nu om de uitvoering.'),
    ('marcus', 'neutraal', 'https://taxfoundation.org/', None,
     'Dat minimumtarief van 15 procent ligt onder het gemiddelde vennootschapstarief in de EU. '
     'Critici noemen het daarom een bodem, geen eindpunt.'),
    ('fatima', 'tegen', 'https://www.imf.org/en/Publications', None,
     'Kleinere landen waarschuwen dat ze met lage tarieven investeringen aantrekken. Een '
     'minimumtarief raakt dat instrument, blijkt uit een working paper van het IMF.'),
    ('sarah', 'voor', 'https://www.ftm.nl/', 'media',
     'In deze reportage volgt een journalist hoe een groot techbedrijf zijn winst via drie '
     'landen naar een belastingparadijs leidt.'),
    ('jan', 'voor', 'https://taxjustice.net/', None,
     'Het Tax Justice Network berekent dat een handvol doorsluislanden samen het grootste deel '
     'van de verschoven bedrijfswinst opvangt. De pijn zit dus bij weinig landen.'),
    ('marcus', 'tegen', 'https://www.businesseurope.eu/', None,
     'Brancheorganisaties stellen dat extra rapportageplichten vooral kleinere bedrijven raken, '
     'die de administratie niet makkelijk aankunnen.'),
    ('sarah', 'voor', 'https://finance.ec.europa.eu/', None,
     'Sinds 2024 moeten grote bedrijven in de EU per land openbaar maken wat ze verdienen en '
     'betalen, de zogeheten public country-by-country reporting.'),
    ('fatima', 'neutraal', 'https://www.oecd.org/tax/', None,
     'Of de afspraken werken, hangt af van de nationale belastingdiensten. Hun capaciteit '
     'verschilt sterk per land, aldus de OESO.'),
    ('jan', 'voor', 'https://www.taxobservatory.eu/', None,
     'De EU Tax Observatory schat dat het minimumtarief in de eerste jaren tientallen miljarden '
     'extra kan opleveren, mits landen het volledig toepassen.'),
]
_FACTS_MARKED = {0, 1, 2, 3, 4, 5, 7, 9}   # feit op index 6 en 8 krijgen geen merk


def seed_demo_voorbeeld(force=False):
    from app import db
    from app.models import Issue

    if Issue.query.filter_by(title=_TITLE).first() is not None:
        if not force:
            return 'Voorbeeld stond er al; niets gedaan (gebruik --force om opnieuw te maken).'
        _remove(db)

    people = _people()
    db.session.flush()
    now = datetime.utcnow()

    # Feiten-en-discussiefase, ruim open, zodat echte gebruikers nog kunnen
    # bijdragen (feit toevoegen, meedoen in de discussie, steunen).
    sch = (now - timedelta(days=10), now + timedelta(days=20),
           now + timedelta(days=25), now + timedelta(days=32))
    issue, thread = _issue(
        db, people, title=_TITLE, intro=_INTRO, author='sarah',
        created_at=now - timedelta(days=10), phase='feiten', schedule=sch, deadline=sch[1])

    facts = []
    for i, (who, direction, source, ftype, body) in enumerate(_FACTS):
        f = _post(db, thread, people[who], now - timedelta(days=9, hours=i),
                  kind='feit', fact_direction=direction, fact_source=source, fact_type=ftype, body=body)
        if i in _FACTS_MARKED:
            _fact_mark(db, f)
        facts.append(f)

    # Eén rijk uitgewerkt voorstel, drie versies, met reacties per versie.
    proposal = _proposal(db, issue, people, 'sarah', versions=[
        ('Maak per land openbaar wat grote bedrijven verdienen en aan belasting betalen.',
         'Openbaarheid per land maakt zichtbaar waar winst wel en geen belasting raakt.',
         now - timedelta(days=9)),
        ('Maak de winst en belasting van grote bedrijven per land openbaar, en koppel daar een '
         'minimumtarief van 15 procent aan.',
         'Naast openbaarheid komt er een bodem onder het tarief, zodat verschuiven minder loont.',
         now - timedelta(days=6)),
        ('Richt een openbaar register op dat per land laat zien wat grote bedrijven verdienen en '
         'betalen, met een wereldwijd minimumtarief van 15 procent en handhaving door de nationale '
         'belastingdiensten onder coördinatie van de OESO.',
         'Het voorstel heeft drie delen die samenhangen. Een openbaar register per land maakt de '
         'cijfers zichtbaar en controleerbaar. Een minimumtarief van 15 procent zorgt voor een '
         'bodem, zodat het verschuiven van winst minder oplevert. En de handhaving ligt bij de '
         'nationale belastingdiensten, met coördinatie en gedeelde gegevens via de OESO, zodat ook '
         'landen met een kleinere dienst kunnen meedoen. Zo blijft de afspraak niet op papier '
         'staan, maar wordt ze zichtbaar, eerlijk en uitvoerbaar.',
         now - timedelta(days=3)),
    ])
    _support(db, proposal, people, ['jan', 'marcus', 'fatima'])
    _link_fact(db, proposal, facts[0], people['sarah'])
    _link_fact(db, proposal, facts[7], people['sarah'])
    _link_fact(db, proposal, facts[9], people['sarah'])

    reacts = [
        ('marcus', 2, 'Een register is mooi, maar wie dwingt het af? Zonder sancties blijft het een lijstje.'),
        ('sarah', 3, 'Daarom staat de handhaving nu expliciet in versie 3, bij de nationale diensten met coördinatie van de OESO.'),
        ('jan', 1, 'Begin bij transparantie. Als het zichtbaar is, volgt de druk vanzelf.'),
        ('fatima', 3, 'Ik vind de combinatie sterk. Openbaarheid en een bodem onder het tarief horen bij elkaar.'),
        ('marcus', 3, 'Vijftien procent blijft laag. Zet er een afspraak bij om het stap voor stap te verhogen.'),
        ('jan', 3, 'Houd het uitvoerbaar. Liever een eenvoudige regel die werkt dan een perfecte die niemand toepast.'),
    ]
    for i, (who, ver, text) in enumerate(reacts):
        _post(db, thread, people[who], now - timedelta(days=2, hours=-i),
              body=text, issue_proposal_id=proposal.id, proposal_version=ver)

    # Lange discussie, tot vier lagen diep, met per deelnemer een eigen stijl.
    d1 = _post(db, thread, people['sarah'], now - timedelta(days=8),
               body=('Laten we scherp houden waar het misgaat. De OESO schat het verlies op 100 tot '
                     '240 miljard dollar per jaar. Dat is geld voor scholen en zorg dat nu nergens '
                     'wordt belast.'))
    d1r1 = _post(db, thread, people['marcus'], now - timedelta(days=8, hours=-3), parent=d1,
                 body='Mooie cijfers, maar een minimumtarief is pas iets als grote landen zelf meedoen. Doen ze dat?')
    d1r1r1 = _post(db, thread, people['sarah'], now - timedelta(days=7), parent=d1r1,
                   body=('Een groeiend aantal wel. De EU heeft het al ingevoerd en meer dan 140 landen '
                         'hebben de afspraak onderschreven. De vraag is niet of, maar hoe streng.'))
    _post(db, thread, people['jan'], now - timedelta(days=7, hours=-2), parent=d1r1r1,
          body='Dan is de volgende vraag simpel: wie controleert het, en met welke mensen?')
    _post(db, thread, people['fatima'], now - timedelta(days=7, hours=-4), parent=d1,
          body=('Precies dat laatste. Ik werk met mensen bij een lokale dienst, en de handen ontbreken '
                'gewoon. Een afspraak op papier is niet genoeg.'))

    _post(db, thread, people['jan'], now - timedelta(days=6),
          body='Begin klein. Eerst openbaar maken wat bedrijven per land betalen. De rest volgt als het zichtbaar is.')

    d3 = _post(db, thread, people['fatima'], now - timedelta(days=5),
               body=('Ik merk in gesprekken dat mensen dit abstract vinden, tot je het concreet maakt. '
                     'Een bedrijf dat hier verkoopt, hier mensen in dienst heeft en toch bijna niets '
                     'bijdraagt, dat begrijpt iedereen meteen. Zo moeten we het ook uitleggen.'))
    _post(db, thread, people['marcus'], now - timedelta(days=5, hours=-5), parent=d3,
          body=('Eens met de uitleg, maar pas op voor te mooie voorbeelden. Niet elk laagbelast bedrijf '
                'ontwijkt. Soms is het gewoon beleid van een land.'))

    db.session.commit()
    return ('Voorbeeld aangemaakt: 1 vraagstuk met %d feiten, 1 voorstel (3 versies) en een lange '
            'discussie tot vier lagen diep.' % len(facts))


def _remove(db):
    from app.models import (Issue, IssueProposal, IssueProposalVersion, IssueProposalSupport,
                            IssueProposalVote, IssueProposalFact, Post, Thread, QualityAward)
    issue = Issue.query.filter_by(title=_TITLE).first()
    if issue is None:
        return
    thread_id = issue.thread_id
    prop_ids = [p.id for p in IssueProposal.query.filter_by(issue_id=issue.id)]
    if prop_ids:
        for model in (IssueProposalVote, IssueProposalSupport, IssueProposalFact, IssueProposalVersion):
            model.query.filter(model.proposal_id.in_(prop_ids)).delete(synchronize_session=False)
        IssueProposal.query.filter(IssueProposal.id.in_(prop_ids)).delete(synchronize_session=False)
    if thread_id:
        post_ids = [p.id for p in Post.query.filter_by(thread_id=thread_id)]
        if post_ids:
            QualityAward.query.filter(QualityAward.post_id.in_(post_ids)).delete(synchronize_session=False)
            Post.query.filter(Post.id.in_(post_ids)).delete(synchronize_session=False)
    issue.thread_id = None
    db.session.flush()
    Issue.query.filter_by(id=issue.id).delete(synchronize_session=False)
    if thread_id:
        Thread.query.filter_by(id=thread_id).delete(synchronize_session=False)
    db.session.commit()
