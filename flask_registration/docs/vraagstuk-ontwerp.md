# Vraagstuk-model (Issue), bouw-specificatie

Canoniek voor de bouw van het vraagstuk-model in Global Union Forum. Claude Code werkt hieruit. De strategie en de bredere beslissingen staan in het claude.ai-Project (forum-fasering.md). Bij verschil over bouwdetails geldt dit document.

Naamgeving: nl "Vraagstuk", en "Issue". Het menu heet "Denk mee" (en "Have your say"); het oude losse forum staat niet meer in het hoofdmenu (volledig uitfaseren is stap 8). Het afsluitende voorstel van de initiatiefnemer heet "samenvattend voorstel" (en "summary proposal"). De feitenverzameling heet op de volledige pagina "Feiten, onderzoek en media" en in de krappe cockpit-kop "Feiten en achtergrond". App-tekst tweetalig nl/en, geen lange of korte streepjes. Ontwikkelaarstekst en commit-teksten in het Engels.

## Het model in het kort
Een vraagstuk is een bak met één gedeelde discussie en één tijdlijn. Uit de discussie ontstaan voorstellen; die worden per stuk verfijnd en gestemd. Aan het einde kan de initiatiefnemer op basis van alle voorstellen en de discussie het vraagstuk aanpassen en een samenvattend voorstel doen, waarover de deelnemers opnieuw stemmen. Dat is optioneel per vraagstuk (de aan/uit-keuze bij het starten). Feiten en onderzoek staan gedeeld op vraagstuk-niveau en kunnen aan voorstellen gekoppeld worden.

Een voorstel doen en steunen kan alleen in de feitenfase. Daarna ligt de stemlijst vast. In de bijstelfase stelt alleen de inbrenger bestaande voorstellen bij.

## Bestaand schema waarop we bouwen (aangetroffen in stap 1)
- Een voorstel (proposals) hangt aan precies één gesprek via thread_id. De stelling is de titel van dat gesprek.
- Proposal-velden: kind (online/publiek of event), phase, threshold_pct, show_live_tally, host_id, started_at; per fase (feiten, bijstellen, stemmen) een *_minutes, *_start, *_end; phase_changed_at, revised_at, original_statement, uitslagvelden, created_at. Daaromheen proposal_votes, proposal_rounds met round_votes, proposal_follows.
- Fase-waarden zijn strings: 'feiten', 'bijstellen', 'stemmen', 'vervolg' (PROPOSAL_PHASES). Het vraagstuk gebruikt dezelfde waarden.
- Geen aparte feiten-tabel. Een feit is een Post met kind='feit', met fact_direction en fact_source. Feiten vind je via voorstel, gesprek, vraagstuk.
- Issue (issues): title, intro, kind (online/event), event_location, event_date, voting_deadline, days_facts, days_adjust, days_vote, support_threshold, proposal_threshold_pct, show_interim, final_vote, phase, phase_started_at, phase_deadline, created_by_id (nullable, relatie created_by), created_at, lang, is_closed, thread_id (nullable FK naar het gesprek). proposals.issue_id is een nullable FK met index.

## Schermen

### Overzicht "Denk mee": /gesprekken
Alle vraagstukken, nieuwste eerst. Per vraagstuk de titel, het online- of event-label met datum, de huidige fase (of "gesloten") en de aantallen voorstellen en deelnemers (de initiatiefnemer en wie bijdroeg, zoals bij de meldingen). Filters: Alle, Online, Event, Lopend, Afgerond (afgerond is gesloten of in de vervolgfase). Een knop "Nieuw vraagstuk". Klikken opent de cockpit.

### Kruimelpad
Bovenaan de cockpit en alle onderliggende pagina's: "Denk mee" (naar het overzicht), dan de naam van het vraagstuk (naar de cockpit), dan eventueel de pagina zelf.

### Cockpit: /vraagstuk/<id>
Boven de kolommen: het online/event-label met datum (online: "stemmen sluit <datum>"; event: "<datum>, <locatie>"), de titel (de vraag), en een fase-strip (feiten, bijstellen, stemmen, vervolg) met de huidige fase gemarkeerd en de resterende tijd.
Desktop, drie kolommen:
- Links (context en feiten): "Waar gaat dit over" (de intro), en "Feiten en achtergrond" met de top-feiten (posts kind='feit') met richting en bron, plus een link naar de volledige feiten-pagina.
- Midden (gedeelde discussie): de thread van het gesprek met de bestaande forumweergave (geneste reacties tot 4 diep, keurmerk, eens, handhaving). Feiten-posts staan hier niet, die staan links. Onderaan de reactie-invoer.
- Rechts (voorstellen en stemrecht): de voorstellen onder het vraagstuk, compact met status (op de stemlijst / verzamelt steun x van n), link naar het detail, en een knop "nieuw voorstel doen". Daaronder "jouw stemrecht" (voortgang, aantal keurmerken).
Mobiel: tabs met de context en fase-strip erboven. Tabs: Discussie (standaard), Voorstellen, Feiten en achtergrond.

### Feiten, onderzoek en media
Gedeelde lijst op vraagstuk-niveau. Per feit: richting (pleit voor / pleit tegen / neutraal), tekst, bron, het soort (feit of onderzoek, of media: nieuws, podcast, video, getoond als label "media"), het AI-kwaliteitsmerk (groen vinkje met de criteria bij mouse-over), en "betwisten" met reden (geen bron, klopt niet, niet relevant, misleidend) dat naar een moderator gaat. Filter op richting, sorteren (kwaliteit eerst, nieuwste), een "toevoegen"-knop, en "toon meer" bij lange lijsten.

Het kwaliteitsmerk (polijstronde P2) komt uit dezelfde AI-aanroep als de handhaving. De AI beoordeelt alleen controleerbare punten: is er een bron, is de bron een vindbare link, staat erbij waar in de bron het te vinden is, is het op het onderwerp, en is het een nieuw feit. Niet de betrouwbaarheid van de bron. Het merk staat als alle vijf kloppen (een vindbare link moet ook echt met http of https beginnen). Het merk is geen keurmerk en telt niet voor stemrecht of de stilte-regel. Kwaliteit eerst betekent: merk, dan een link als bron, dan de nieuwste. Het oude "nuttig"-signaal is weg; de tabel fact_useful_votes blijft staan maar wordt niet meer gebruikt. Kolommen: posts.fact_type, quality_awards.fact_mark en de vijf punten (fact_has_source, fact_link_findable, fact_has_location, fact_on_topic, fact_is_new).

### Voorstellen (lijst)
Alle voorstellen bij het vraagstuk. Per voorstel: status (op de stemlijst / verzamelt steun x van n), inbrenger, de stelling. Filter (alle / op de stemlijst / verzamelt steun) en sorteren (meeste steun / nieuwste / stemlijst eerst). Een "steunen"-knop bij voorstellen die nog steun verzamelen, en "nieuw voorstel doen".

### Voorstel-detail
De stelling, de status, de toelichting, de bijstel-historie (versies), en de sectie "waarop dit voorstel zich baseert" met de gekoppelde feiten en hun richting-labels plus een "feit koppelen"-knop (iedereen mag koppelen, ook tegenfeiten). De stemming (voor, tegen, onthouding) met de drempel per voorstel en de tussenstand als die aanstaat, en, als dat bij het vraagstuk aanstaat, de melding dat de initiatiefnemer aan het einde op basis van alle voorstellen en de discussie een samenvattend voorstel kan doen, waarover opnieuw wordt gestemd. De discussie gefilterd op dit voorstel. Mobiel: de stemming prominent bovenaan.

### Vraagstuk starten: /gesprekken/nieuw
De vraag, het eerste bericht, de keuze online of event (met datum, en locatie bij event), de planning per fase (feiten 21, bijstellen 5, stemmen 7 dagen), en de stemregels (drempel per voorstel 50 procent, steundrempel 5, tussenstand aan/uit, samenvattend voorstel met nieuwe stemming aan/uit).

### Event of online
Elke pagina toont bovenaan duidelijk of het online is (met datum) of een event (met datum en locatie), met een eigen label en kleur.

## Fasen en levenscyclus
- Volgorde: feiten, bijstellen, stemmen, vervolg. Bij elke overgang krijgen phase, phase_started_at en phase_deadline een nieuwe waarde uit days_facts, days_adjust en days_vote. Uit de vervolgfase gaat niets automatisch verder.
- Online: de uurtaak (/intern/tik) zet een vraagstuk door zodra de deadline van de huidige fase verstreken is. Een overgang op de deadline begint op die deadline, zodat de planning blijft kloppen; een gemiste tik wordt ingehaald, zo nodig over meerdere fasen.
- De feitenfase sluit na days_facts dagen, of eerder na 5 dagen zonder nieuw keurmerk in het vraagstuk (de stilte telt vanaf het begin van de feitenfase of het laatste keurmerk). De stilte-drempel is instelbaar in beheer.
- Events worden met de hand verzet: de initiatiefnemer of een moderator zet het vraagstuk naar de volgende fase, zonder datums. Bij online kan dezelfde actie een fase vroegtijdig afsluiten.
- In de vervolgfase beschrijft de initiatiefnemer of een moderator wat er met het vraagstuk gebeurt (de vervolgtekst). Een afgerond of gesloten vraagstuk kan na een bevestiging opnieuw worden opengesteld: terug naar de feitenfase met een nieuwe planning. Voorstellen, steun en stemmen blijven bewaard.
- Het samenvattende voorstel ontstaat in de vervolgfase (alleen de initiatiefnemer, als dat bij het vraagstuk aanstaat) en heeft een eigen stemperiode van days_vote dagen vanaf het aanmaken, met dezelfde drempel.
- De uitslag wordt live uit de stemmen berekend, niet vastgelegd. Bij het verwijderen van een account blijft een stem meetellen zonder naam, zodat een uitslag niet verandert.
- Een vraagstuk dat een oud voorstel inpakt (uit de backfill van stap 1) volgt de fase van dat oude voorstel en heeft geen eigen automatiek, tot het oude model is uitgefaseerd.
- Meldingen lopen via de bestaande dagelijkse samenvatting: de initiatiefnemer en wie heeft bijgedragen (berichten, feiten, voorstellen, steun, stemmen) krijgen een melding bij een faseovergang, het openen van de stemming, de uitslag, het samenvattende voorstel, de vervolgtekst en het opnieuw openstellen.

## Beslissingen (en waarom)
- Eén cockpit per vraagstuk met één gedeelde discussie, niet een discussie per voorstel. Dat houdt de deliberatie bij elkaar (één burgerberaad, één tijdlijn).
- Op feiten geen stemknoppen: een omlaag-knop zou een waar maar onwelgevallig feit wegdrukken. Kwaliteit loopt via het AI-kwaliteitsmerk op controleerbare punten (polijstronde P2; vervangt de eerdere "nuttig"-knop).
- Iedereen mag feiten aan een voorstel koppelen, ook tegenfeiten, met labels zichtbaar, zodat het beeld eerlijk blijft. Koppelen, niet kopiëren.
- Stemmen per voorstel (voor, tegen, onthouding). Optioneel per vraagstuk: aan het einde past de initiatiefnemer op basis van alle voorstellen en de discussie het vraagstuk aan en doet een samenvattend voorstel, waarover de deelnemers opnieuw stemmen.

## Bouwstappen
1. Datamodel en het vraagstuk als bak, plus navigatie. Af, lokaal gecommit (01fd408), getest op Postgres, nog niet gepusht.
2. De cockpit, responsive. Af, lokaal gecommit (0ad3f37), getest, nog niet gepusht.
3. Feiten en onderzoek (model, nuttig, sorteren, betwisten, pagina en panel).
4. Voorstellen: ontstaan, steun, bijstellen, formulier, pagina, detail.
5. Feiten koppelen aan voorstellen.
6. Stemmen, het samenvattende voorstel met de stemming daarover, uitslag.
7. Vervolg, fase-overgangen via de uurtaak, notificaties, opnieuw openstellen.
8. Opruimen: het oude losse-voorstel-model uitfaseren.

## Regels bij elke stap
- Additieve, nullable migratie via de bestaande idempotente aanpak (_migrate_* en _migrate_indexes), getest op een Postgres-kopie over meerdere starts. Vooraf een Neon-snapshot (doet Gerhard).
- App-tekst tweetalig nl/en, geen lange of korte streepjes. Ontwikkelaarstekst en commit-teksten Engels.
- Responsive volgens de mobiele ontwerpen.
- De fase-engine en de tik blijven ongewijzigd tot stap 6/7.

## Standaardwaarden (instelbaar in beheer)
Feiten en discussie 21 dagen of 5 dagen stilte; bijstellen 5 dagen, maximaal één keer; stemmen 7 dagen; vervolg open. Steundrempel 5; drempel per voorstel 50 procent voor. Stemrecht na 3 keurmerken; moderator meer dan 10 over minstens 3 onderwerpen; senior meer dan 100 handelingen.
