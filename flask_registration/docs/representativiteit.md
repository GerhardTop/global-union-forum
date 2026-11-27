# Representativiteit: wie stemt er mee (meten, later wegen en loten)

Status: besloten ontwerp niveau 1, 27 november 2026. Dit bestand is de bouw-specificatie van dit spoor en is bij elke bouwstap ervan leidend. Ontwerp: docs/design/representativiteit-vragen.html (de vragen bij de eerste stem) en docs/design/representativiteit-uitslag.html (het blok "Wie stemde mee" en de variant met te weinig stemmers), met voorbeelddata. Gerhard volgt het advies bij alle keuzes, met twee wijzigingen: geboortejaar in plaats van leeftijdsgroep (K4), en maar één keer vragen (K2). Voorgeschiedenis: de sectie "Mijn land en andere persoonsgegevens" in docs/achtergrondcijfers.md.

## Doel
Bij de uitslag van een vraagstuk laten zien wie er meestemde (leeftijd, geslacht, opleiding, land), zodat iedereen kan zien hoe divers de groep was en of een groep ontbreekt. Eén doel, en niet meer: de antwoorden dienen alleen om totalen te tonen.

Bewust NIET in deze stap: stemmen uitsplitsen per groep, een gewogen uitslag, loting, etnische afkomst, geboortedatum, woonplaats.

## Drie niveaus
1. Meten (deze opzet): vrijwillige vragen, alleen totalen bij de uitslag. Verandert niets aan de stemmen.
2. Wegen (later, schets onderaan): een gewogen uitslag naast de gewone.
3. Loten (later en apart, schets onderaan): een geloot panel bij een specifiek vraagstuk.

## Wat al vastlag (achtergrondcijfers-opzet, 24 november)
Vrijwillig met "zeg ik liever niet", gevraagd bij de eerste stem, leeftijdsgroepen in plaats van geboortedatum (op 27 november aangepast: geboortejaar vragen, alleen groepen tonen, zie K4), geslacht als vrouw / man / anders / zeg ik liever niet, alleen totalen met een minimale groepsgrootte (10), geen uitsplitsing van stemmen, extra zorg omdat de doelgroep bij 16 jaar begint, vooraf een DPIA en een aangepaste privacyverklaring. Geen etniciteit (startnotitie, 27 november).

## Hoe het nu werkt (uit de code)
- Stemmen kan alleen wie stemrecht heeft (na een aantal kwaliteitsbijdragen, vote.voter_min). Een stem is één rij in issue_proposal_votes per voorstel, met user_id; een klik op Voor, Tegen of Onthouding verstuurt meteen (issues.proposal_vote).
- De stemfase eindigt met de overgang naar de volgende fase (issue_lifecycle.set_phase); daar worden ook de achtergrondcijfers bevroren.
- Er is nog geen veld "Mijn land" op de gebruiker (stap 2 van de achtergrondcijfers is nog niet gebouwd).

## Besluiten (27 november 2026)
Per keuze het besluit, dan het alternatief dat niet gekozen is.

K1. Wanneer vragen we het?
Besluit: direct NA de eerste stem op een voorstel van een vraagstuk, als kaart in het stemblok ("Je stem is opgeslagen. Mogen we nog iets vragen?"). De stem is dan al binnen, dus niemand denkt dat de vragen een voorwaarde zijn. Alternatief (niet gekozen): vóór de eerste stem, als tussenstap. Nadeel: voelt als een poort en vertraagt het stemmen.

K2. Hoe vaak?
Besluit: maar één keer, bij de eerste stem. De kaart heeft twee knoppen: "Opslaan" en "Overslaan". Na allebei komt de kaart niet terug; invullen, wijzigen en wissen kan daarna alleen in het profiel (Over mij). Alternatief (niet gekozen): met "Niet nu" de kaart nog één keer laten terugkomen.
Eén keer betekent: de kaart verschijnt op de voorstelpagina direct na de eerste stem en asked_at wordt gezet zodra hij getoond is; wie hem negeert, krijgt hem niet opnieuw.

K3. Welke vragen?
Besluit: vier vragen, alle vier met "Zeg ik liever niet": geboortejaar, geslacht, opleiding, land. Geen etniciteit, geen woonplaats, geen inkomen. Alternatief (niet gekozen): alleen leeftijd en geslacht (minder vragen, maar opleiding is volgens onderzoek vaak het scheefste kenmerk bij deelname).

K4. Leeftijd: geboortejaar vragen, groepen tonen
Besluit (voorstel van Gerhard): we vragen het geboortejaar en tonen alleen groepen. Redenen: de leeftijd veroudert vanzelf (een vaste groep klopt na een paar jaar niet meer), de groepen kunnen meegroeien met het aantal stemmers, en voor niveau 2 sluit het precies aan op de leeftijdsgroepen van Eurostat (blokken van vijf jaar). Het e-mailadres en vaak de naam zijn al bekend, dus een jaar maakt iemand nauwelijks herkenbaarder; de DPIA legt uit waarom het jaar nodig is (minimale gegevens).
- Vraag: "In welk jaar ben je geboren?", een keuzelijst van "1920 of eerder" tot het jaar waarin je 16 wordt, plus "Zeg ik liever niet". Hint: "We tonen nooit je leeftijd, alleen groepen zoals "25 tot en met 44"."
- Jongste leeftijd 16, gelijk aan de minimumleeftijd van de aanmelding (27 november 2026).
- Leeftijd bij de uitslag = het jaar van bevriezen min het geboortejaar (klopt op een jaar na; genoeg voor groepen).
- Groepen als vaste trap, waarin elk niveau precies uit het fijnere bestaat:
  Fijn: 16 tot en met 24, 25 tot en met 34, 35 tot en met 44, 45 tot en met 54, 55 tot en met 64, 65 tot en met 74, 75 of ouder;
  Middel: 16 tot en met 24, 25 tot en met 44, 45 tot en met 64, 65 of ouder;
  Grof: 16 tot en met 44, 45 of ouder.
  De app kiest het fijnste niveau waarop elke groep minstens 10 mensen heeft. Lukt het zelfs bij Grof niet, dan bij leeftijd alleen "Niet genoeg ingevuld om te tonen". Geen vrij gekozen grenzen per vraagstuk: die zouden door vergelijken terugrekenen mogelijk maken en vraagstukken onvergelijkbaar maken.
- Het geboortejaar en de leeftijd verschijnen nergens, behalve voor de gebruiker zelf in zijn eigen profiel.
Alternatief (niet gekozen): vaste leeftijdsgroepen vragen.

K5. Opleiding
Besluit: drie niveaus, internationaal vergelijkbaar (de indeling ISCED die Eurostat gebruikt), met een korte uitleg per niveau in elke taal: "Basis" (lagere school of het eerste deel van de middelbare school), "Middelbaar" (middelbare school afgerond, of een beroepsopleiding zoals het mbo), "Hoger" (hbo, universiteit). Vraag: "Wat is de hoogste opleiding die je hebt afgerond of nu volgt?" Met "of nu volgt" komt een student van 19 bij het niveau van zijn opleiding terecht en niet bij "Basis". Alternatief (niet gekozen): de Nederlandse indeling (vmbo, havo, mbo...): herkenbaar voor Nederlanders, maar niet bruikbaar in de andere talen.

K6. Land
Besluit: één veld, "Mijn land" (het land waar je woont), dat we nu bouwen en dat ook het voorstel bij "Land toevoegen" van de achtergrondcijfers voedt. Eén plek in het profiel, twee doelen, beide in de privacyverklaring. Staat "Mijn land" al ingevuld, dan is de vraag in de kaart vooraf ingevuld. Alternatief (niet gekozen): een apart land voor de uitslag (zuiverder doelbinding, maar twee bijna gelijke vragen).

K7. Waar tonen we "Wie stemde mee", en wanneer?
Besluit: één blok per vraagstuk, bij de uitslag in de cockpit, voor iedereen die op minstens één voorstel van de stemlijst stemde. De cijfers worden één keer berekend en bevroren als de stemfase eindigt (set_phase, naast het bevriezen van de achtergrondcijfers). Tijdens het stemmen niets: een telling die na elke stem verandert, laat zien in welke groep de laatste stemmer zat. Bij de uitslag van elk voorstel een regel "312 deelnemers · Wie stemde mee" die naar het blok linkt. Het samenvattende voorstel (de slotstemming) krijgt later een eigen blok met dezelfde regels. Alternatief (niet gekozen): per voorstel een eigen blok (meer detail, maar meer kleine groepen en meer kans om verschillen tussen voorstellen te gebruiken om iets over één persoon af te leiden).
Plek: op de vraagstukpagina over de volle breedte, onder de fase en vóór de drie kolommen.

K8. Drempels
Besluit:
- Het blok verschijnt alleen als minstens 30 mensen stemden; anders de zin "Te weinig deelnemers om te laten zien wie er meestemde (minder dan 30)."
- Leeftijd: de vaste trap uit K4. Bij de andere kenmerken tonen we alleen groepen van minstens 10 mensen. Kleinere groepen gaan samen in "Kleinere groepen samen"; is die samen nog onder de 10, dan gaat hij op in "Niet ingevuld". Zo kun je een kleine groep niet terugrekenen door af te trekken.
- Percentages van alle stemmers, afgerond op hele procenten, met het aantal mensen erbij. "Niet ingevuld" (niets ingevuld of "zeg ik liever niet") staat altijd als laatste regel, zodat duidelijk is hoeveel mensen meededen aan de vragen.
- Land: de landen met minstens 10 stemmers, dan "Andere landen samen", dan "Niet ingevuld".
- Een groep met 0 mensen is niet te klein en staat er met 0%; te klein is 1 tot en met 9. Past bij leeftijd geen enkel niveau, dan staan er bij leeftijd geen groepen en ook geen regel Niet ingevuld, maar alleen de zin Niet genoeg ingevuld om te tonen.
Alternatief (niet gekozen): drempel 10 voor het hele blok (eerder zichtbaar, maar met 10 tot 29 stemmers zit bijna alles in "Kleinere groepen").

K9. Vergelijking met de bevolking
Besluit: in niveau 1 niet. Alleen het eigen publiek, zonder referentie. De keuze welke bevolking de maat is (EU-27, de wereld, het land van het vraagstuk) hoort bij niveau 2 en verdient daar een eigen besluit. Alternatief (niet gekozen): nu al een streepje "EU-bevolking" per groep, uit Eurostat (zichtbaarder wie ontbreekt, maar dan moet die keuze nu al gemaakt worden).

K10. Wie ziet de losse antwoorden?
Besluit: niemand in de app, ook geen moderator of beheerder: geen scherm, geen export, niet op de gebruikerspagina in het beheer, niet in logs. Alleen de gebruiker zelf ziet en wijzigt zijn antwoorden in het profiel. De app gebruikt de antwoorden op één plek: het bevriezen van de totalen.

## Weergave (zie de artboards)
Vragen bij de eerste stem (artboard 1):
- In het stemblok, onder de bevestiging van de stem. Kop "Wie stemt er mee?" met de pil "Vrijwillig", dan de uitleg: "Je stem is opgeslagen. Mogen we nog iets vragen? Bij de uitslag laten we zien hoe de groep stemmers is samengesteld, alleen in groepen van minstens 10 mensen. Nooit wie wat stemde." Daaronder "Je stem telt hoe dan ook." en een link "Waarom we dit vragen" naar de privacyverklaring.
- Vier vragen: geboortejaar (keuzelijst), geslacht en opleiding (keuzerondjes in knopvorm) en land (keuzelijst). Elke vraag heeft "Zeg ik liever niet". Een vraag leeg laten mag.
- Knoppen "Opslaan" (primair) en "Overslaan". Onderaan: "We vragen dit maar één keer. Je kunt het altijd invullen, wijzigen of wissen in je profiel, onder Over mij."
- Werkt zonder JavaScript (gewoon formulier, terug naar het stemblok). Op mobiel staan de keuzes onder elkaar.
- Na opslaan: één regel "Dank je. Je antwoorden staan in je profiel onder Over mij."

Profiel (onder "Taal en land", nieuwe sectie "Over mij"): dezelfde vier vragen, de datum waarop je ze invulde, een knop "Opslaan" en een knop "Antwoorden wissen". Geen eigen artboard; volgt de bestaande profielvorm.

Wie stemde mee (artboard 2):
- Kaart in de cockpit bij de uitslag. Kop "Wie stemde mee", regel "312 deelnemers stemden op minstens één voorstel. 241 van hen (77%) vulden minstens één vraag in."
- Per kenmerk een kopje en per groep een regel met naam, een liggende balk, percentage en aantal. "Niet ingevuld" in grijs, als laatste. Geen taartdiagram, geen kleur als enig onderscheid (de balken hebben één kleur; de tekst draagt de betekenis).
- Een tabelweergave voor schermlezers (zoals bij de achtergrondcijfers).
- Onderaan: "Zelf opgegeven en vrijwillig. Percentages van alle stemmers, afgerond. Leeftijdsgroepen zo fijn als het aantal stemmers toelaat. Andere groepen kleiner dan 10 zijn samengevoegd. Dit zegt niets over wie wat stemde." en "Bevroren op <datum>, aan het eind van de stemfase."
- Te weinig stemmers: de kaart met alleen de zin uit K8.

## Gegevens en techniek (voor de bouw)
- Nieuwe tabel participant_profiles: user_id (uniek), birth_year (geheel getal of leeg; 0 betekent "zeg ik liever niet"), gender, education, answered_at, asked_at (de kaart is getoond en komt niet terug). Apart van users, zodat de antwoorden niet meekomen met elke query op de gebruiker. Waarden als vaste codes in code (gender f, m, x, na; education basic, middle, higher, na; de leeftijdstrap als vaste lijst in code); de labels via gettext.
- "Mijn land": kolom users.country (ISO2 uit app/data/landenlijst_data.json), optioneel.
- users.country slaat alleen een land op; "zeg ik liever niet" bij land wordt leeg opgeslagen.
- Nieuwe tabel issue_participation_stats: issue_id, frozen_at, voters, answered, age_level (fine, middle, coarse of none), counts (JSON met per kenmerk de groepen na het toepassen van de drempels). Alleen totalen; geen user_id. De drempels gelden bij het bevriezen, zodat de opgeslagen cijfers zelf al veilig zijn.
- Bevriezen in set_phase bij het einde van de stemfase, één keer per vraagstuk; bij heropenen (issue_lifecycle.reopen) niet opnieuw. Een vraagstuk dat eindigde vóór deze functie krijgt geen blok.
- Account verwijderen: de rij in participant_profiles en users.country gaan weg. Bevroren totalen blijven (ze bevatten niemand).
- Schakelaar: PARTICIPANT_PROFILE (omgevingsvariabele, "1" is aan, standaard uit). Uit: geen kaart, geen sectie in het profiel, niets bevriezen. Aan pas na de DPIA en de nieuwe privacyverklaring.
- Privacyverklaring en FAQ bijwerken (en PRIVACY_UPDATED): welke gegevens, het ene doel, vrijwillig op basis van toestemming, intrekken door wissen in het profiel, wie ze ziet (niemand), hoe lang (zolang het account bestaat).
- Randvoorwaarden zoals bij de achtergrondcijfers: werkregels uit CLAUDE.md, tests in willekeurige volgorde, vijf talen via gettext (zie Meertaligheid), geen lange of korte streepjes, vast aantal queries op de vraagstuk- en voorstelpagina (het blok leest één rij issue_participation_stats), migraties additief en idempotent, page_snapshot.py voor stappen die niets zichtbaars horen te veranderen.
- Tests die bewaken dat de antwoorden nergens anders verschijnen: niet op /admin en de moderatorschermen, niet in de profielpagina van een ander, niet in de JSON-antwoorden.

## Meertaligheid (volgens CLAUDE.md, sectie "Vertalingen (i18n)", en docs/i18n/machinevertaling.md)
- Alles in dit spoor is vaste interfacetekst. Er is geen inhoud van gebruikers of de AI, dus niets via content_i18n of content_jobs en geen label "Machinaal vertaald".
- Alle tekst via gettext; de msgid is Engels, de Nederlandse tekst staat in de catalogus. De teksten in deze opzet en in de artboards zijn de Nederlandse vertalingen; de bouwstap kiest de Engelse msgids en geeft elke tekst een translator-comment (TRANSLATORS:).
- Elke bouwstap (1b, 1c, 1d) levert zijn eigen teksten meteen in alle vijf ingeschakelde talen (nl, en, de, es, fr): geen lege of fuzzy items, .mo meecommitten, naleesbestanden opnieuw exporteren (python i18n_review.py export de, es en fr). Niet uitstellen naar een latere stap.
- Aanspreekvorm en woordkeuze volgens de woordenlijsten (docs/i18n/glossary_<taal>.md): je, du, tú, vous; geen genderster of inclusieve schrijfwijze, neutraal waar het kan; geen lange of korte streepjes. De nieuwe kernbegrippen (Wie stemde mee, Wie stemt er mee, Zeg ik liever niet, Niet ingevuld, Kleinere groepen samen, de drie opleidingsniveaus, Over mij) komen in de drie woordenlijsten.
- Opleiding: de namen Basis, Middelbaar en Hoger worden gewoon vertaald; de uitleg eronder noemt per taal voorbeelden uit dat taalgebied, nooit de Nederlandse namen (mbo, hbo). De Engelse msgid is neutraal (bijvoorbeeld "College or university"); de translator-comment noemt de ISCED-niveaus (0 tot en met 2, 3 en 4, 5 tot en met 8) en vraagt om een voorbeeld dat lezers in die taal herkennen.
- Leeftijdsgroepen als msgids met plaatshouders ("%(low)s to %(high)s", "%(age)s or older"); de code vult de getallen in.
- Getallen, percentages en datums via Babel in de taal van de lezer (de bestaande filters zoals decimalformat en datetimeformat; een percentage in de notatie van de taal, in het Frans met een spatie voor het teken). Uitzondering: geboortejaren in de keuzelijst zijn kale jaartallen, zonder scheidingsteken voor duizendtallen (1987, niet 1.987).
- Landen: opslaan als ISO2-code; namen alleen via app/sources/countries.display_name (de eigen landenlijst, met een eigen naam per taal; PS, HK, TW en XK nooit via Babel), in de keuzelijst gesorteerd op de naam in de taal van de lezer.
- De bevroren totalen (issue_participation_stats.counts) bevatten alleen codes, nooit labels: leeftijdsgroep, f/m/x, basic/middle/higher, ISO2, plus vaste codes voor "Kleinere groepen samen", "Andere landen samen" en "Niet ingevuld". De weergave vertaalt bij het tonen, dus het blok klopt in elke taal, ook in een taal die later aangaat.
- Opmaak met logische CSS (inline-start en inline-end, text-align start en end), zodat kaart en blok later ook in een taal van rechts naar links kloppen (tests/test_logical_css.py).
- tests/test_no_dutch_interface.py dekt de nieuwe schermen per taal: de kaart na de eerste stem, het blok "Wie stemde mee" en de sectie "Over mij" in het profiel.

## Privacy (aandachtspunten voor de DPIA)
Ik ben geen jurist; laat de DPIA en de tekst van de privacyverklaring nalezen.
- Leeftijdsgroep, geslacht, opleiding en land zijn geen bijzondere persoonsgegevens, maar het platform verwerkt al politieke meningen (de stemmen). Samen maken ze iemand herkenbaarder. Daarom: apart bewaren, alleen totalen, drempels, niemand ziet losse antwoorden, bevriezen pas na het stemmen.
- Grondslag: toestemming (vrijwillig, apart te geven, even makkelijk in te trekken). Het stemrecht hangt er niet van af.
- Doelgroep vanaf 16 jaar. De AVG laat lidstaten de leeftijd voor eigen toestemming bij online diensten op 13 tot 16 jaar zetten, dus een 16-jarige kan zelf toestemming geven. Toch extra zorg: alleen groepen tonen, en de jongste groep loopt in elk niveau tot en met 24 of verder, zodat 16-jarigen er niet apart uit springen.
- Geboortejaar in plaats van leeftijdsgroep: nodig voor het verouderen en de meegroeiende groepen (K4); het jaar wordt nergens getoond of geëxporteerd.
- Zelf opgegeven gegevens zijn niet te controleren. Bij niveau 1 is er geen reden om te liegen (er hangt niets van af); bij niveau 2 en 3 wel, zie daar.

## Bouwvolgorde niveau 1 (elk een eigen opdracht, push via scripts/check_and_push.sh)
1a. Deze opzet en de artboards in de repo (docs/representativiteit.md, docs/design/representativiteit-vragen.html, docs/design/representativiteit-uitslag.html, rijen in docs/design/README.md, verwijzing in CLAUDE.md) (klaar, 27 november 2026).
1b. Datamodel (participant_profiles, users.country, issue_participation_stats), de sectie "Over mij" en "Mijn land" in het profiel, wissen, opruimen bij account verwijderen; achter PARTICIPANT_PROFILE. Niets zichtbaars met de schakelaar uit (page_snapshot.py) (klaar, 27 november 2026).
1c. De kaart na de eerste stem, één keer (K1, K2), met en zonder JavaScript (klaar, 27 november 2026).
1d. Bevriezen bij het einde van de stemfase en het blok "Wie stemde mee" met de leeftijdstrap en de drempels (K4, K7, K8), plus de regel bij de uitslag van elk voorstel; uitbreiding van tests/test_proposal_queries.py (klaar, 27 november 2026).
1e. Privacyverklaring en FAQ (per alinea een msgid, in vijf talen), PRIVACY_UPDATED, naleesbestanden opnieuw exporteren. De teksten van 1b tot en met 1d gaan al in die stappen in alle vijf talen (zie Meertaligheid) (klaar, 27 november 2026).
Daarna: DPIA afronden, dan PARTICIPANT_PROFILE=1 op Render.

## Niveau 2: wegen (schets, later)
- Wanneer: pas als er per vraagstuk genoeg stemmers zijn die de vragen invulden (orde van grootte 200 of meer) en de groepen niet te scheef zijn.
- Wat: naast de gewone uitslag een tweede regel: "Als de stemmers qua leeftijd, geslacht en opleiding waren samengesteld zoals <bevolking>, was de uitslag ongeveer 58% voor." Nooit in plaats van de gewone uitslag; nooit bepalend voor aangenomen of verworpen.
- Hoe: elke stemmer krijgt een gewicht (aandeel van zijn groep in de bevolking gedeeld door aandeel in de stemmers), met een grens (bijvoorbeeld tussen 0,3 en 3) zodat één persoon niet zwaar gaat tellen. Wie niets invulde telt mee met gewicht 1, of valt erbuiten (keuze voor dan).
- Referentie: bevolkingscijfers van Eurostat (die bron is er al), met een besluit welke bevolking de maat is.
- Privacy: de berekening heeft intern stemmen per groep nodig. Alleen de gewogen percentages worden bewaard en getoond, berekend bij het bevriezen. Nieuwe DPIA-paragraaf.
- Risico: wie weet dat zijn groep zwaarder telt, kan iets anders invullen. Daarom een gewogen uitslag alleen ter informatie.

## Niveau 3: loten (schets, later en apart)
- Niet als poort voor het gewone stemmen (sluit mensen uit en verlaagt de deelname).
- Wel: een apart panel bij één specifiek vraagstuk, gestratificeerd geloot uit wie zich aanmeldt, zoals bij burgerberaden (Utrecht gebruikte leeftijd, geslacht, wijk en opleiding).
- Probleem: zelf opgegeven kenmerken zijn online niet te controleren, en bij loting is er wel een reden om iets anders in te vullen. Burgerberaden nodigen daarom uit via de basisadministratie. Hier mogelijk met een partner die deelnemers kan verifiëren (een gemeente, een onderzoeksbureau met een panel).
- Eigen opzet, eigen DPIA.

## Besloten bij de open punten (27 november)
- Slotstemming: later een eigen blok met dezelfde regels, apart van het blok van de stemfase.
- Alleen bij vraagstukken, niet bij de forumvoorstellen (proposal_votes).
- Verouderen: opgelost door het geboortejaar (K4).
