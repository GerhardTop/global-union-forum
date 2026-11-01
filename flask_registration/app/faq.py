"""
De inhoud van de FAQ-pagina (/faq). Redactionele tekst, net als de andere
inhoudspagina's tweetalig en niet via de vertaalcatalogus: per taal een
lijst thema's, elk met (vraag, antwoord). nl en en houden dezelfde
volgorde en hetzelfde aantal vragen (getest in tests/test_faq.py).

"Global Union", "Global Union Forum" en "Freedom for all" blijven
onvertaald. Een antwoord met een link is Markup; de rest is platte tekst en
wordt in de template ge-escaped.
"""
from markupsafe import Markup

_PRIVACY_NL = Markup('Zo min mogelijk. Je pseudoniem, je e-mailadres voor herstel en wat je zelf plaatst. '
                     'Extra profielgegevens alleen als je ze zelf invult. De details staan op de '
                     '<a href="/privacy">privacy-pagina</a>.')
_PRIVACY_EN = Markup('As little as possible. Your pseudonym, your email address for recovery and what you post '
                     'yourself. Extra profile details only if you fill them in yourself. The details are on the '
                     '<a href="/privacy">privacy page</a>.')

FAQ = {
    'nl': [
        ('Global Union Forum', [
            ('Wat is Global Union?',
             'Global Union is het idee van een samenwerkingsverband van functionerende democratieën wereldwijd. '
             'Het is gebouwd op aantrekkingskracht in plaats van dwang, naar het voorbeeld van de Europese Unie. '
             'Het staat open voor landen die de rechtsstaat, mensenrechten, de aanpak van corruptie, '
             'duurzaamheid en de internationale rechtsorde respecteren.'),
            ('Wat is het verschil tussen Global Union en het Global Union Forum?',
             'Global Union is het idee en de beweging. Het Global Union Forum is de plek waar dat idee wordt '
             'ontwikkeld en besproken. Hier denk je mee, breng je feiten in en stem je mee. Experts en beginners '
             'doen dat naast elkaar.'),
            ('Wie zit er achter het platform?',
             'Het is een onafhankelijk initiatief, opgezet door oprichter Gerhard Top. Bij voldoende momentum '
             'wordt dit initiatief ondergebracht in een stichting. Het idee is om eerst mede-initiatiefnemers te '
             'betrekken en daarna een groeiende groep deelnemers en experts, om het samen verder uit te werken.'),
            ('Is dit verbonden aan een politieke partij of een overheid?',
             'Nee. Het forum is onafhankelijk en niet verbonden aan een partij, een regering of een bedrijf. '
             'Het is reformistisch en het is niet gebonden aan een politieke kleur.'),
            ('Hoe kan ik meedoen of bijdragen?',
             'Maak een account aan en lees de spelregels. Daarna kun je beginnen: voeg feiten toe, geef je mening '
             'of start zelf een vraagstuk. Wat dit wordt, bepalen de mensen die meedoen.'),
        ]),
        ('Manifest', [
            ('Wat staat er in het manifest?',
             'Het manifest uit maart 2026 is een persoonlijk essay van de oprichter. Daarin zet hij het idee van '
             'een globale unie van functionerende democratieën uiteen: waarom het nodig is, hoe het zou kunnen '
             'werken en waarom het de moeite waard is om te proberen. Zie het als een begin van de discussie.'),
            ('Wat betekent "een unie van functionerende democratieën"?',
             'Een samenwerking van landen die de democratie en de rechtsstaat echt naleven. Landen uit de hele '
             'wereld kunnen meedoen. Wat telt is of een land aan die waarden voldoet.'),
            ('Waarom het voorbeeld van de Europese Unie?',
             'De EU laat zien dat landen vrijwillig kunnen samenwerken op basis van gedeelde regels. Ze trekken '
             'elkaar aan in plaats van elkaar te dwingen. Dat principe willen we toepassen op wereldschaal.'),
            ('Welke landen zouden kunnen meedoen en op basis waarvan?',
             'Landen die voldoen aan maatstaven als de rechtsstaat, mensenrechten, de aanpak van corruptie en de '
             'internationale rechtsorde. Toetreding gaat op democratische prestatie. Niet alleen welvarende '
             'westerse landen, maar juist ook voorbeeldlanden uit Afrika en Zuid-Amerika.'),
            ('Is dit niet te groots of te idealistisch?',
             'Het is een groots idee. Misschien moeilijk haalbaar, maar wel de moeite waard. Je begint klein en '
             'bouwt het samen op, stap voor stap.'),
        ]),
        ('Hoe het werkt', [
            ('Hoe verloopt een vraagstuk, van discussie tot stemming?',
             'Iemand start een vraagstuk met een vraag of probleem. In de eerste fase breng je feiten in en '
             'bespreek je het samen. Daar komen voorstellen uit voort. Daarna kan de inbrenger een voorstel '
             'bijstellen. Vervolgens wordt er gestemd. Tot slot volgt de vervolgfase: wat gebeurt ermee.'),
            ('Wat zijn feiten en onderzoek en hoe tellen ze mee?',
             'Feiten en onderzoek zijn onderbouwde bijdragen met een bron. Elk feit heeft een richting: pleit '
             'voor, pleit tegen of neutraal. Ze onderbouwen de discussie en de voorstellen.'),
            ('Hoe ontstaan voorstellen en hoe wordt erover gestemd?',
             'Voorstellen komen voort uit de discussie. Een voorstel komt op de stemlijst zodra genoeg mensen het '
             'steunen. Er wordt per voorstel gestemd met voor, tegen of onthouding. Op basis van alle voorstellen '
             'en de discussie kan de initiatiefnemer het vraagstuk aanpassen en een samenvattend voorstel doen. '
             'Daarover kunnen de deelnemers dan opnieuw stemmen.'),
            ('Wat is het kwaliteitskeurmerk?',
             'Een medaille die automatisch wordt toegekend aan een bijdrage die de discussie verder brengt. Dat '
             'kan met een nieuw argument of met een onderbouwing met een bron. Geen keurmerk is geen straf. Stijl '
             'en taalvaardigheid tellen niet mee.'),
            ('Hoe verdien ik stemrecht?',
             'Door kwaliteit bij te dragen. Na drie bijdragen met een keurmerk mag je meestemmen. Zo telt wat je '
             'inbrengt en niet alleen dat je een account hebt.'),
            ('Welke spelregels gelden er en wat gebeurt er bij een overtreding?',
             'Er zijn gedragsregels: respectvol blijven, bij het onderwerp blijven en één account per persoon. '
             'Die worden gehandhaafd. Daarnaast zijn er kwaliteitsrichtlijnen zoals onderbouwd en concreet. Die '
             'worden beloond en niet afgedwongen. Bij een duidelijke overtreding gaat een bijdrage naar een '
             'openbare achtergrondpagina met uitleg. Je kunt daar bezwaar tegen maken. Niets wordt definitief '
             'verwijderd. Alleen pure spam en grof misbruik worden volledig verborgen.'),
            ('Hoe voorkomen jullie dubbel stemmen of nepaccounts?',
             'Helemaal te voorkomen is het niet. Een nieuw account begint op nul en moet stemrecht eerst '
             'verdienen met kwaliteitsbijdragen. Eén account per persoon is een regel en moderatie grijpt in bij '
             'misbruik. Daardoor is massaal nepstemmen veel werk en levert het weinig op.'),
        ]),
        ('Account en privacy', [
            ('Hoe maak ik een account aan?',
             'Kies een naam en vul een e-mailadres in. Dat e-mailadres is alleen voor herstel en wordt nooit '
             'getoond. Accepteer de spelregels en je kunt beginnen. Je hoeft niet te betalen en geen '
             'identiteitsbewijs te geven.'),
            ('Waarom is een pseudoniem standaard?',
             'We willen dat je vrijuit mee kunt doen aan de discussie, zonder dat je hier aan wie dan ook '
             'verantwoording moet afleggen. Een pseudoniem maakt dat mogelijk. Politieke deelname kan in veel '
             'landen risico\'s meebrengen. Een identiteitsdrempel sluit juist mensen uit die we willen bereiken.'),
            ('Welke gegevens bewaren jullie en waarvoor?', _PRIVACY_NL),
            ('Wie kan zien wat ik schrijf?',
             'Je bijdragen zijn openbaar en zichtbaar onder je pseudoniem. Iedereen kan ze lezen. Je e-mailadres '
             'en eventuele privégegevens blijven privé.'),
            ('Wat is de minimumleeftijd?', 'Zestien jaar.'),
            ('Hoe verwijder ik mijn account of mijn gegevens?',
             'Dat kan via je accountinstellingen of op verzoek. Je persoonlijke gegevens zoals je e-mailadres en '
             'profiel worden dan gewist.'),
            ('Wat gebeurt er met mijn inbreng nadat ik mijn account heb verwijderd?',
             'Je bijdragen blijven staan, maar losgekoppeld van je naam. Zo houden de discussies en stemmingen '
             'waaraan anderen hebben meegedaan hun samenhang. Alleen de koppeling met jou verdwijnt.'),
        ]),
    ],
    'en': [
        ('Global Union Forum', [
            ('What is Global Union?',
             'Global Union is the idea of a partnership of functioning democracies worldwide. It is built on '
             'attraction instead of force, following the example of the European Union. It is open to countries '
             'that respect the rule of law, human rights, the fight against corruption, sustainability and the '
             'international legal order.'),
            ('What is the difference between Global Union and the Global Union Forum?',
             'Global Union is the idea and the movement. The Global Union Forum is the place where that idea is '
             'developed and discussed. Here you think along, bring in facts and vote. Experts and beginners do '
             'this side by side.'),
            ('Who is behind the platform?',
             'It is an independent initiative, set up by founder Gerhard Top. With enough momentum, this '
             'initiative will be placed in a foundation. The idea is to involve co-initiators first, and then a '
             'growing group of participants and experts, to develop it further together.'),
            ('Is this linked to a political party or a government?',
             'No. The forum is independent and not linked to a party, a government or a company. It is '
             'reformist and it is not tied to a political colour.'),
            ('How can I take part or contribute?',
             'Create an account and read the ground rules. Then you can start: add facts, give your opinion or '
             'start an issue yourself. What this becomes is decided by the people who take part.'),
        ]),
        ('Manifesto', [
            ('What is in the manifesto?',
             'The manifesto from March 2026 is a personal essay by the founder. In it he sets out the idea of a '
             'global union of functioning democracies: why it is needed, how it could work and why it is worth '
             'trying. See it as the start of the discussion.'),
            ('What does "a union of functioning democracies" mean?',
             'A partnership of countries that truly uphold democracy and the rule of law. Countries from all '
             'over the world can join. What counts is whether a country meets those values.'),
            ('Why the example of the European Union?',
             'The EU shows that countries can work together voluntarily, based on shared rules. They attract '
             'each other instead of forcing each other. We want to apply that principle on a world scale.'),
            ('Which countries could join, and on what basis?',
             'Countries that meet standards such as the rule of law, human rights, the fight against corruption '
             'and the international legal order. Membership is based on democratic performance. Not only '
             'wealthy Western countries, but also, and especially, model countries from Africa and South '
             'America.'),
            ('Is this not too grand or too idealistic?',
             'It is a grand idea. Perhaps hard to achieve, but worth it. You start small and build it up '
             'together, step by step.'),
        ]),
        ('How it works', [
            ('How does an issue go, from discussion to vote?',
             'Someone starts an issue with a question or problem. In the first phase you bring in facts and '
             'discuss it together. Proposals come out of that. After that, the proposer can revise a proposal. '
             'Then there is a vote. Finally there is the follow-up phase: what happens with it.'),
            ('What are facts and research, and how do they count?',
             'Facts and research are well-founded contributions with a source. Each fact has a direction: argues '
             'for, argues against or neutral. They support the discussion and the proposals.'),
            ('How do proposals come about, and how is the vote held?',
             'Proposals come out of the discussion. A proposal goes on the voting list as soon as enough people '
             'support it. Each proposal is voted on with for, against or abstain. Based on all proposals and the '
             'discussion, the initiator can adjust the issue and make a summary proposal. The participants can '
             'then vote on that again.'),
            ('What is the quality mark?',
             'A medal that is given automatically to a contribution that moves the discussion forward. That can '
             'be a new argument or support with a source. No quality mark is not a punishment. Style and '
             'language skills do not count.'),
            ('How do I earn the right to vote?',
             'By contributing quality. After three contributions with a quality mark, you may vote. That way, '
             'what you bring in counts, not just having an account.'),
            ('Which ground rules apply, and what happens when they are broken?',
             'There are conduct rules: stay respectful, stay on topic and one account per person. These are '
             'enforced. There are also quality guidelines, such as well-founded and concrete. These are rewarded '
             'and not enforced. When a rule is clearly broken, a contribution moves to a public background page '
             'with an explanation. You can object to that there. Nothing is removed for good. Only pure spam and '
             'gross abuse are hidden completely.'),
            ('How do you prevent double voting or fake accounts?',
             'It cannot be prevented completely. A new account starts at zero and has to earn the right to vote '
             'first, with quality contributions. One account per person is a rule, and moderation steps in when '
             'there is abuse. This makes mass fake voting a lot of work for little gain.'),
        ]),
        ('Account and privacy', [
            ('How do I create an account?',
             'Choose a name and enter an email address. That email address is only for recovery and is never '
             'shown. Accept the ground rules and you can start. You do not have to pay and you do not need to '
             'show any ID.'),
            ('Why is a pseudonym the default?',
             'We want you to be able to join the discussion freely, without having to answer to anyone here. A '
             'pseudonym makes that possible. In many countries, political participation can bring risks. An '
             'identity barrier would shut out exactly the people we want to reach.'),
            ('Which data do you keep, and what for?', _PRIVACY_EN),
            ('Who can see what I write?',
             'Your contributions are public and visible under your pseudonym. Anyone can read them. Your email '
             'address and any private details stay private.'),
            ('What is the minimum age?', 'Sixteen.'),
            ('How do I delete my account or my data?',
             'You can do that in your account settings or on request. Your personal data, such as your email '
             'address and profile, is then erased.'),
            ('What happens to my contributions after I delete my account?',
             'Your contributions stay, but are no longer linked to your name. That way, the discussions and '
             'votes others took part in keep their coherence. Only the link to you disappears.'),
        ]),
    ],
}


def for_lang(lang):
    return FAQ['en' if lang == 'en' else 'nl']
