# Achtergrondcijfers: grafieken uit open bronnen (AI kiest, bron levert, moderator kan ingrijpen)

Status: besloten ontwerp, 24 november 2026, bijgewerkt 25 november. Dit bestand is de bouw-specificatie van dit spoor en is bij elke bouwstap ervan leidend. Ontwerp: `docs/design/achtergrondcijfers-kolom.html` (de kaart in de feitenkolom) en `docs/design/achtergrondcijfers-uitgeklapt.html` (de grafiekpagina), met voorbeelddata. Het profielveld "Mijn land" (stap 2) krijgt later een eigen artboard. Geen proef vooraf: meteen bouwen.

## Doel
Vraagstukken en voorstellen minder statisch maken met een paar grafieken die nodig zijn om goed na te denken over het vraagstuk, met cijfers uit gezaghebbende open bronnen, in de huisstijl, met een legenda waarin je landen of reeksen aan en uit zet.

Bewust NIET: afbeeldingen laten genereren door een beeldmodel (verzint getallen, tekst in pixels past niet bij vijf talen, framing, extra leverancier) en geen uploads door gebruikers (auteursrecht, AVG, illegale inhoud, moderatielast).

## Taakverdeling
- De AI kiest alleen: welke indicator, welke landen, welk gemiddelde, welke periode, en waarom. Uit de echte catalogus van een toegestane bron. De AI zet nooit zelf een getal neer.
- De app haalt de cijfers op via de API van de bron, bewaart een momentopname (alle landen van de indicator over de gekozen periode, definitie, eenheid, bronvermelding, licentie, datum van ophalen) en tekent de grafiek zelf volgens de vaste grafiekstandaard (hieronder).
- Geen handmatige goedkeuring vooraf. De app controleert het antwoord van de AI (zie Standaardprompt) en publiceert dan zelf. Toezicht achteraf: deelnemers kunnen melden met de meldvlag (zoals bij andere feiten) en betwisten; een moderator kan de grafiek verwijderen.
- Een verwijderde grafiek en een losgemaakte koppeling komen bij een volgende selectie niet terug (de app onthoudt ze per vraagstuk).
- Vindt de AI geen geschikte cijfers (vaak bij institutionele onderwerpen zoals VN-hervorming of NAVO-besluitvorming), dan zegt hij dat. Niets forceren.
- Een achtergrondcijfer heeft geen zichtbare auteur: de cijfers komen van de bron, de AI maakt alleen een selectie. (Technisch krijgt de Post een vaste systeemwaarde in author_name en author_username en geen user_id.)

## Wanneer de AI selecteert
- Automatisch bij het aanmaken van een vraagstuk, op de achtergrond (zoals de vertalingen in app/content_jobs.py); de uurtik is het vangnet.
- Bij een nieuw voorstel, alleen in de feiten- en bijstelfase: de AI bekijkt of bestaande grafieken aan dat voorstel gekoppeld moeten worden en of een nieuwe grafiek echt nuttige informatie toevoegt.
- Na het begin van de stemfase niets nieuws. Een nieuwere versie naast de bevroren versie komt later.
- Model: Opus 5.5 (één aanroep per vraagstuk en per nieuw voorstel; zonder menselijke goedkeuring telt de kwaliteit van de keuze zwaar). Het model staat als vaste instelling in de code.
- Een pagina roept de AI nooit aan en haalt nooit iets bij de bron op; alles gebeurt op de achtergrond.

## Grafiekstandaard (vast, in code, niet in de prompt)
Opmaak is geen keuze van de AI maar een vaste regel in de app, zodat elke grafiek er hetzelfde uitziet en elk voorstel gelijk behandeld wordt.
- Y-as links met labels in de eenheid (bijvoorbeeld 0% tot 4%). Mooie, ronde stappen; uitgeklapt 4 tot 6 rasterlijnen, in de kolom 3. De nullijn iets donkerder. De as begint bij 0 voor percentages en bedragen.
- X-as: begin- en eindjaar, uitgeklapt ook enkele tussenjaren.
- Lijnen 2,5 px (kolom 2 px), punt, naam en waarde aan het eind van elke lijn (uitgeklapt), labels die elkaar niet overlappen.
- Kleuren in vaste volgorde, verschillend in lichtheid: blauw #1b74b8, oranje #e09400, groen #108a4e, paars #7048c8 (duidelijk anders dan het grijs van het gemiddelde); nooit alleen kleur als onderscheid. Door de lezer toegevoegde landen (stap 2) krijgen eigen kleuren (#0f8b8d, #b4541a, #c2378a) en het label "door jou toegevoegd".
- Gemiddelde of totaal als gestippelde grijze lijn (#6c7683), met eigen legenda-item, aan en uit te zetten.
- Kolom: hoogstens vier landen plus het gemiddelde; uitgeklapt hoogstens acht (inclusief toegevoegde).
- Getallen in de notatie van de taal van de lezer (NL: komma), één decimaal waar zinvol, via Babel.
- Altijd een tabelweergave als toegankelijk alternatief.
- Geen referentielijnen (zoals een NAVO-norm) tenzij de AI die voorstelt, als apart onderdeel met eigen bron.

## Standaardprompt (keuzes, niet opmaak)
Eén vaste prompt in de code, met het vraagstuk (titel, inleiding, de voorstellen), de al gekozen grafieken, en een lijst kandidaat-indicatoren uit de zoekfunctie van de bron. De AI geeft gestructureerde uitvoer (JSON), die de app controleert voordat iets getoond wordt. Prompt en model worden per aanroep bewaard (zoals QualityAward.prompt_text).
Regels in de prompt:
1. Liever één grafiek per vraagstuk; hoogstens vier in totaal, alleen als dat nuttig is. Geen nieuwe als die weinig toevoegt; geen geschikte, zeg dat.
2. Alleen indicatoren uit de meegegeven lijst (de app controleert dat de slug bestaat).
3. Evenwicht: samen relevant voor meerdere voorstellen of kanten; per indicator noemen voor welke voorstellen. Voorbeeld (Europese defensie, voorstellen over een EU-leger, de NAVO-norm, en diplomatie en ontwikkelingshulp): niet alleen defensie-uitgaven (het kader van de NAVO-norm), maar ook steun voor gezamenlijk defensiebeleid en ontwikkelingshulp.
4. Landen:
   a. eerst de landen die letterlijk in het vraagstuk of de voorstellen genoemd worden;
   b. anders een vaste set per regio (voorlopig in code: Europa: Duitsland, Frankrijk, Nederland, Polen; wereld: VS, China, India, EU; later instelbaar in het beheer, met eerst een ontwerp);
   c. landen nooit kiezen om een standpunt te ondersteunen, geen uitschieters zoeken.
5. Gemiddelde: alleen een gemiddelde of totaal dat in de data van de bron staat (bijvoorbeeld EU-27, wereld). Een gemiddelde dat OWID zelf berekende telt als van de bron (de bron is daarvoor verantwoordelijk); de herkomst staat bij "Over deze cijfers". Is er geen, dan geen gemiddelde; de app rekent nooit zelf uit een selectie landen.
6. Periode: de laatste tien jaar, tenzij een langere periode nodig is om het punt te zien; een afwijking uitleggen in de reden.
7. Reden: één of twee zinnen in gewone taal, zonder oordeel over welk voorstel beter is.
Uitvoer per indicator: bron, slug, landen, gemiddelde (of geen), periode, reden, relevant voor (voorstellen). Bij een nieuw voorstel ook: welke bestaande grafieken aan dat voorstel gekoppeld worden.
De app controleert: slug bestaat, landen en gemiddelde staan in de data, periode heeft data, niet eerder verwijderd, niet meer dan vier in totaal. Faalt iets, dan valt de indicator af; niets repareren of raden.

## Koppelen aan voorstellen
- De AI koppelt automatisch ("relevant voor"), als IssueProposalFact zonder linked_by. De voorsteller (en een moderator) kan ontkoppelen met de bestaande knop op de voorstelpagina; daar staat bij zo'n koppeling "Gekoppeld door AI" (nu staat bij een koppeling zonder persoon "@deleted_user"). issue_proposals.can_unlink moet daarvoor ook de voorsteller toelaten bij een koppeling zonder persoon.
- Een losgemaakte koppeling zet de AI niet opnieuw.
- Op de grafiekpagina staat onder "Waarom deze grafiek" het kopje "Gekoppeld aan" met de stellingen van alle voorstellen die nu gekoppeld zijn, door de AI of door een deelnemer (voorstellen hebben geen letter of korte titel). Elke stelling linkt naar het voorstel, in de taal van de lezer, na twee regels afgekapt. Wie koppelde staat op de voorstelpagina, niet hier.

## Land toevoegen door de lezer (stap 2)
- De standaardselectie is voor iedereen gelijk (gedeelde basis, ook bij het stemmen). Geen personalisatie van de standaardgrafiek op taal of locatie: taal is een slechte maat voor land, en verschillende grafieken per lezer ondermijnen de discussie.
- Uitgeklapt: knop "Land toevoegen" (zoeken in alle landen van de momentopname) en één voorstel met één klik ("Spanje toevoegen"). Het voorstel komt uit: 1) het profielveld "Mijn land", als de lezer dat invulde; 2) anders de regio-instelling van de browser (bijvoorbeeld es-ES), alleen op het eigen apparaat gelezen, niets naar de server; 3) geen IP-locatie.
- Toegevoegde landen ziet alleen de lezer zelf; eigen kleur en label "door jou toegevoegd".

## Mijn land en andere persoonsgegevens (stap 2)
- "Mijn land": optioneel, alleen in het profiel onder "Taal en land", niet zichtbaar voor anderen. Niet vragen bij de registratie (houdt de drempel laag). Wel één keer op het moment dat het nut zichtbaar is: als een lezer voor het eerst zelf een land toevoegt, de vraag "Wil je <land> onthouden als jouw land?". Enige gebruik: het voorstel bij "Land toevoegen". Privacyverklaring bijwerken (en PRIVACY_UPDATED).
- Geslacht en leeftijd: nu NIET vragen. Redenen: belofte van minimale gegevens (pseudoniem, e-mail, "zo min mogelijk" in de FAQ); het platform verwerkt al politieke meningen (bijzondere categorie in de AVG), en extra kenmerken maken mensen herkenbaarder en een datalek ernstiger; er is nu geen concreet doel.
- Later mogelijk, als er genoeg deelnemers zijn, met één doel: laten zien wie er meedoet (representativiteit bij de uitslag). Dan: vrijwillig met "zeg ik liever niet", gevraagd bij de eerste stem, leeftijdsgroepen in plaats van geboortedatum, geslacht als vrouw / man / anders / zeg ik liever niet, alleen totalen met een minimale groepsgrootte (bijvoorbeeld 10), geen uitsplitsing van stemmen zonder die grens, extra zorg omdat de doelgroep bij 17 jaar begint, en vooraf een DPIA en een aangepaste privacyverklaring.

## Bronnen (toegestane lijst, uitbreidbaar)
Criteria voor een bron: officieel of wetenschappelijk, open licentie met bronvermelding, een API of stabiele download, en metadata met definitie en eenheid. Elke bron is een eigen adapter.
1. Our World in Data (eerste bron). Grapher-API: https://ourworldindata.org/grapher/<slug>.csv en <slug>.metadata.json. Ruim 5.000 grafieken; bundelt al veel andere bronnen (onder meer World Bank, V-Dem, SIPRI), met een kant-en-klare bronvermelding en eigen regio-gemiddelden. Landcodes zijn ISO3; regio's hebben eigen codes (zoals OWID_WRL). Zoeken: OWID heeft geen stabiele zoek-API, dus een eigen catalogustabel (slug, titel, ondertitel, onderwerp), gevuld uit de openbare chartlijst van OWID met een admin-knop "Catalogus verversen"; lukt verversen niet, dan blijft de oude lijst staan. De AI haalt zoekwoorden uit het vraagstuk, de app zoekt lokaal.
2. Eurostat (tweede bron, voor EU-onderwerpen). Statistics API in JSON-stat 2.0, antwoorden in EN, FR, DE.
3. Later, per behoefte: World Bank API, OECD (SDMX), IMF, UN Data, CBS Statline (OData) voor Nederlandse onderwerpen, Eurobarometer voor publieke opinie.
Bronnen zonder API (bijvoorbeeld een losse Excel) alleen via OWID, als die ze al opneemt.

## Weergave
- Een achtergrondcijfer is een soort feit (Post met kind 'feit', fact_type 'cijfer', richting 'neutraal'; niet kiesbaar in het feitformulier) in de feitenlijst van het vraagstuk, en te koppelen aan een voorstel, net als andere feiten. Titel = de titel van de grafiek, tekst = de reden, bron = de grafiek bij de bron.
- Bovenregel van de kaart: de richting-pil "Neutraal", het label "AI-selectie" in dezelfde pilvorm, en de meldvlag. Geen apart label "Achtergrondcijfer" (de grafiek spreekt voor zich) en geen icoon bij AI-selectie (een icoon maakt het knop-achtig, alsof je er iets met AI kunt doen).
- AI-selectie toont een uitleg bij aanwijzen, focus of aantikken (aantikken is nodig voor mobiel): "Door AI geselecteerd uit <bron> op <datum>. De cijfers komen rechtstreeks uit de bron." Op de grafiekpagina onder "Waarom deze grafiek": "Gekozen door AI op <datum>". Vertalingen van het label: DE "KI-Auswahl", ES "Selección por IA", FR "Sélection par IA" (Duits gebruikt KI, niet AI).
- Geen kwaliteitsbeoordeling en geen handhaving door de AI (fact_add start quality.schedule_judgement; voor een achtergrondcijfer niet). Niemand kan de kaart bewerken.
- Kolom (cockpit, circa 280 px): titel, periode en aantal landen, kleine lijngrafiek, legenda in twee kolommen met de laatste waarde, bron, link "Grafiek uitvergroten" (opent de grafiekpagina; het vergroot-icoon in de kop van de kolom blijft de feitenlijst vergroten).
- Grafiekpagina (eigen pagina met kruimelpad): grote grafiek die meeschaalt, landen en gemiddelde aan en uit in de legenda, land toevoegen (stap 2), periode (5 jaar, 10 jaar, en "Alles" als de AI een langere periode koos), grafiek of tabel, kaart "Over deze cijfers" (definitie, eenheid, bron, herkomst van het gemiddelde, licentie, opgehaald, momentopname), kaart "Waarom deze grafiek" met "Gekoppeld aan", acties koppelen, betwisten en (moderator) verwijderen. Werkt zonder JavaScript (dan de volledige grafiek en de tabel). Op mobiel staan de zijkaarten onder de grafiek.
- Titels, redenen en namen van gemiddelden via de bestaande vertaalroute (content_i18n, content_jobs). Landnamen uit de eigen landenlijst (app/data/landenlijst_data.json, ISO2; ISO3 van OWID omgezet naar ISO2). Een land dat niet in de landenlijst staat valt af.
- Deelnemers kunnen een achtergrondcijfer betwisten en melden zoals elk feit; een melding komt in de bestaande sectie Meldingen.

## Momentopname en stemmen
De momentopname (alle landen, de gekozen periode, standaard tien jaar) wordt bevroren bij het begin van de stemfase (issue_lifecycle.set_phase, het enige punt waar de stemfase begint), zodat iedereen over dezelfde cijfers stemt. Een nieuwere versie komt er hooguit naast, nooit in de plaats.

## Kosten
Laag: één AI-aanroep per vraagstuk en per nieuw voorstel, plus vertaling van titels en labels. De data is gratis.

## Bouwvolgorde
1. OWID, in deelstappen (elk een eigen opdracht, eigen tests, push via scripts/check_and_push.sh):
   1a. deze opzet en de artboards in de repo;
   1b. datamodel, OWID-adapter en catalogus met de knop "Catalogus verversen" (nog niets zichtbaar voor deelnemers);
   1c. grafiekstandaard in code (SVG uit Python, tabel, getalnotatie);
   1d. automatische selectie, controle, publiceren en koppelen, met het geheugen voor verwijderd en ontkoppeld;
   1e. kaart in de kolom, meldvlag, verwijderen door een moderator;
   1f. grafiekpagina;
   1g. bevriezen bij de stemfase, ontkoppelen door de voorsteller ("Gekoppeld door AI");
   1h. teksten in vijf talen (naleesbestanden opnieuw exporteren).
2. Land toevoegen en het profielveld "Mijn land" (met de eenmalige vraag).
3. Eurostat erbij.
4. Feitkaart (een groot getal uit een gewoon feit, alleen als het getal letterlijk in de tekst staat) als aanvulling.
Na de eerste echte vraagstukken met grafieken nagaan of deelnemers ernaar verwijzen in de discussie (in plaats van een proef vooraf).

## Randvoorwaarden voor de bouw (stand 25 november 2026)
- Werkregels uit CLAUDE.md gelden onverkort: pushen alleen via scripts/check_and_push.sh (weigert ook nieuwe niet-gecommitte bestanden in app/ en tests/); tests in willekeurige volgorde (pytest-randomly), dus elke nieuwe test maakt eigen data met unieke teksten en ruimt op wat hij commit; app-tekst via gettext in vijf talen, geen lange of korte streepjes; Engelse commit-teksten.
- Geen netwerk in tests: vaste OWID-voorbeeldbestanden (CSV en metadata) in tests/ en een nep-AI-client, zoals in tests/conftest.py en tests/test_content_jobs.py. De achtergrondstart staat in de tests uit, zoals bij content_jobs.
- Vast aantal queries: de vraagstuk- en voorstelpagina laden met een vast aantal queries, hoeveel voorstellen er ook zijn (tests/test_proposal_queries.py). Grafieken en momentopnamen worden voor alle feiten van een pagina in één keer geladen, nooit per feit; breid die test uit met een vraagstuk met grafieken.
- Verwijderen: bij het verwijderen van een feit (facts.delete_for_posts, ook via het opruimen van een heel vraagstuk) gaan de grafiekgegevens en momentopnamen mee. Bij verwijderen in bulk ook de vertalingen met content_i18n.delete_for(<tabel>, ids). Krijgt een nieuwe tabel eigen vertaalde velden (zoals de naam van een gemiddelde), registreer die dan zo dat content_i18n.remove_orphans en /admin/vertalingen hem kennen.
- Migraties: additief en idempotent bij het opstarten (nieuwe tabellen via create_all, kolommen via _migrate_columns), getest over meerdere starts; Neon-snapshot vooraf.
- Een stap die niets zichtbaars hoort te veranderen (zoals 1b): vergelijk de pagina's met scripts/page_snapshot.py (200 pagina's). Zodra de demo grafieken krijgt, verandert de vergelijking bewust; zeg dan welke pagina's en waarom.
- Productie: Render met één gunicorn-worker (achtergrondthreads zoals in content_jobs werken daar), AI-sleutel aanwezig. De server haalt bij ourworldindata.org op; de browser praat nooit met de bron (de grafiek is SVG in de pagina, CSP hoeft niet te veranderen).
- Huisstijl: de tokens in docs/design/README.md (--gu-* in colors_and_type.css). Een afgerond artboard is leidend voor de vormgeving.

## Open
- Vaste sets per regio (regel 4b): nu in code; later instelbaar in het beheer, met eerst een ontwerp.

## Stemmen op feiten (geparkeerd 24 november)
Omhoog en omlaag stemmen op feiten zoals bij Reddit: voorlopig niet. Opnieuw bekijken als het aantal feiten zo groot wordt dat zoeken naar kwaliteit een probleem is. Eerder besloten (vraagstuk-model, 28 oktober): één positieve knop "nuttig", geen omlaag; kwaliteit via bron en keurmerk. Opties voor dan: sorteren op "meest nuttig" per richting, "overbruggend nuttig" (het idee achter Community Notes), alleen "nuttig" van leden met stemrecht laten tellen.
