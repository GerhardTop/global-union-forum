# Woordenlijst Frans (fr)

De vaste Franse vertaling van de kernbegrippen van het platform. Elke Franse tekst (catalogus, mails, AI-prompt, landnamen) volgt deze lijst, zodat een begrip overal hetzelfde heet. Wie een begrip wil wijzigen, wijzigt het hier en daarna overal in `app/translations/fr/LC_MESSAGES/messages.po`.

De Franse vertaling is een machinevertaling (fase 1). `docs/i18n/review_fr.csv` is het naleesbestand voor een Franstalige (lees eerst "Eerst voor de nalezer" hieronder); `python i18n_review.py apply fr` zet correcties daaruit terug in de catalogus.

## Eerst voor de nalezer

1. **"Dossier" voor vraagstuk.** Een vraagstuk is een open vraag waarover deelnemers samen nadenken. "Question" botst met de gewone vraag (een vraagstuk begint "met een vraag of probleem") en met de FAQ, "sujet" is te vaag, "problématique" te zwaar. "Dossier" is in de politiek gangbaar voor een onderwerp waaraan gewerkt wordt ("le dossier des retraites"). Is "Lancer un dossier" natuurlijk?
2. **Rolnamen.** Lecteur, Contributeur, Votant, Modérateur, Modérateur senior en Initiateur zijn generiek mannelijk. Graag een neutrale en korte vorm voorstellen die op de statusladder in het profiel past, zonder punt médian.
3. **Typografie.** Franse aanhalingstekens « » (met vaste spatie binnen) en een vaste spatie voor ; : ! ? en %, overal consequent. Graag melden als het ergens niet klopt.
4. **Neutraal geformuleerd waar een vorm anders mannelijk of vrouwelijk zou zijn:** "Connexion réussie" (in plaats van "Vous êtes connecté"), "Votre compte est suspendu", "Vous confirmez ?". Klinkt dat natuurlijk?
5. **Losse woordkeuzes om na te lopen:** "Moniteur des langues" (taalmonitor), "grille d'évaluation" (scorecard), "Socle" en "Approfondissement" (fasen in de wetgevingstabel), "droits humains" (niet "droits de l'homme"), "Participants" voor co-thinkers, "abonné retiré" (een volger die bij het verwijderen loskomt), "Au niveau" (voldoet aan de norm), "s.o." voor n/a, "cockpit".
6. **"Signaler" in twee betekenissen:** een melding door een gebruiker, en een markering door het systeem ("Signalé à un modérateur"). Is een ander woord voor de tweede beter?

## Stijlregels

- **Aanspreekvorm: vous.** Overal en consequent, ook in mails en in de AI-prompt. Geen tu.
- **Standaard Frans.** Geen Belgische, Zwitserse of Canadese varianten; woorden die overal gewoon zijn (mot de passe, adresse e-mail, se connecter, en ligne).
- **Geen inclusieve schrijfwijzen** (geen punt médian, geen ·e·s, geen haakjes-e). Waar het kan een neutrale formulering ("les personnes qui votent", "quiconque"). Rolnamen zijn vaste labels (zie hieronder).
- **Eenvoudige taal.** Korte zinnen, op het niveau van de Nederlandse tekst. Geen ambtelijke stijl.
- **Geen lange of korte streepjes** (ook niet als incise): gebruik een komma, dubbele punt of haakjes. Een koppelteken in een samenstelling (e-mail, peut-être) is gewoon toegestaan. `tests/test_i18n_safety.py` bewaakt dit.
- **Typografie:** aanhalingstekens in lopende tekst als « … » met een vaste spatie (U+00A0) binnen; een vaste spatie voor ; : ! ?. Aanhalingstekens in HTML-attributen (`href="..."`) blijven gewone rechte aanhalingstekens.
- **Niet vertalen:** "Global Union", "Global Union Forum", "Freedom for all", namen van organisaties, verdragen en standaarden in hun gangbare Franse vorm (OMC, OIT, OMS, ONU, OCDE, IFRS).
- **Placeholders en markup ongewijzigd:** `%(name)s`, `%(num)d`, `<strong>`, `<a href="...">` blijven precies zoals in de msgid.
- **Meervoud:** Frans heeft twee vormen met `nplurals=2; plural=(n > 1)`: 0 en 1 gebruiken het enkelvoud. Het enkelvoud bevat daarom altijd het getal (`%(num)d`) als het meervoud dat heeft, ook als de Engelse enkelvoudsvorm het weglaat.

## Kernbegrippen

| EN | NL | FR | Waarom |
|---|---|---|---|
| issue | vraagstuk | dossier (mv. dossiers) | Een onderwerp waaraan samen gewerkt wordt; "question" botst met de gewone vraag en de FAQ. |
| proposal | voorstel | proposition | Het gewone woord in burgerparticipatie ("déposer une proposition"). |
| summary proposal | samenvattend voorstel | proposition de synthèse | Het voorstel dat de uitkomst samenvat. |
| statement | stelling | affirmation (v.) | De korte stelling van een voorstel waarover gestemd wordt; neutraal en duidelijk. Vrouwelijk: "l'affirmation initiale", "révisée". |
| explanation | toelichting | explication | Standaardwoord voor een toelichting bij een voorstel. |
| conversation | gesprek | conversation | Volgt het Nederlands; "débat" is gereserveerd voor discussion (de discussiekolom). |
| discussion | discussie | débat | De gewone term voor een inhoudelijke discussie. |
| contribution | bijdrage | contribution | Gangbaar voor forumberichten. |
| fact | feit | fait | Gangbaar ("fondé sur les faits"). |
| research | onderzoek | étude | Als soort bron (naast podcast, article de presse) de natuurlijke term. |
| news article | nieuwsartikel | article de presse | Gewone term voor een krantenbericht. |
| support (steunbetuiging) | steunbetuiging, steunen | soutien, soutenir | Gangbaar bij petities en voorstellen ("recueillir des soutiens"). |
| quality mark | keurmerk | label de qualité | Het gangbare Franse woord voor een keurmerk. |
| quality contribution | kwaliteitsbijdrage | contribution de qualité | Consequent met label de qualité en contribution. |
| ground rules (de pagina "Spelregels") | spelregels | règles du jeu | Zelfde beeld als in het Nederlands, in het Frans even gewoon. |
| conduct rules | gedragsregels | règles de conduite | De regels over gedrag in het forum (moderatie); onderscheiden van règles du jeu. |
| background page | achtergrondpagina | page de contexte | "Contexte" is het gewone woord voor achtergrond bij een onderwerp. |
| objection | bezwaar | recours (m.), faire un recours / déposer un recours | Bezwaar tegen een beslissing van de moderatie. Mannelijk, onveranderlijk in het meervoud: "un recours", "recours ouverts", "Recours rejeté". Werkwoord naar wat in de zin past: "faire un recours" (kunnen), "déposer un recours" (indienen). Betwisten van een feit blijft "contester" ("Faits contestés"). |
| report (melding) | melding | signalement, signaler | Zo heet het bij de grote platforms. |
| notification | melding (notificatie) | notification | In het Nederlands heet dit ook "melding"; het Frans houdt de twee uit elkaar. |
| summary (dagelijks/wekelijks) | samenvatting | résumé | Gangbaar voor een digest-mail. |
| decision (moderatie) | beslissing, besluit | décision | Gangbaar en neutraal. |
| phase: facts and discussion | feiten en discussie | Faits et débat | Volgt de begrippen fact en discussion. |
| phase: adjusting | bijstellen | Révision | Het gewone woord voor het bijwerken van een voorstel. |
| phase: voting | stemmen | Vote | Het zelfstandig naamwoord voor de fase; werkwoord "voter". |
| phase: summarising | samenvatten | Synthèse | Volgt summary proposal. |
| phase: follow-up | vervolg | Suivi | Wat er na de stemming met de uitkomst gebeurt. |
| role: Reader | Lezer | Lecteur | Vaste label. |
| role: Contributor | Bijdrager | Contributeur | Vaste label. |
| role: Voter | Stemmer | Votant | Vaste label; de rol die het stemrecht heeft. "Électeur" hoort bij verkiezingen. |
| role: Moderator | Moderator | Modérateur | Vaste label. |
| role: Senior moderator | Senior moderator | Modérateur senior | Vaste label. |
| right to vote | stemrecht | droit de vote | Het gewone woord. |
| follow / unfollow | volgen / ontvolgen | suivre / ne plus suivre | Gangbaar op platforms. |
| online / event | online / event | en ligne / événement | "En ligne" is het gewone woord; "événement" voor een bijeenkomst op een plek. |
| username | gebruikersnaam | nom d'utilisateur | Het ene woord voor de naam waaronder je meedoet (@naam); nergens "pseudonyme". Met het voorzetsel "sous": "sous votre nom d'utilisateur". |
| Former account | Voormalig account | Ancien compte | Het label dat bij een account dat er niet meer is in plaats van de naam staat; zonder @, het is geen gebruikersnaam. Zegt niet wie het account verwijderde, en is genderneutraal. |
| organiser | organisator | personne qui organise (l'organisation de l'événement) | Wie een event maakt en beheert. Neutraal, zonder "organisateur/trice"; als los label "Organisation". |
| New event | Nieuw event | Nouvel événement | De knop in Denk mee en de titel van het formulier. |
| Everyone may vote | Iedereen mag stemmen | Tout le monde peut voter | De instelling op een event. |
| Participants may add issues | Deelnemers mogen vraagstukken toevoegen | Les participants peuvent ajouter des dossiers | De instelling op een event. |
| Where does the event take place? | Waar vindt het event plaats? | Où a lieu l'événement ? | De vraag boven de vorm van een event (vaste spatie voor het vraagteken). |
| At a location / Online / At a location and online | Op een locatie / Online / Op een locatie en online | Dans un lieu / En ligne / Dans un lieu et en ligne | De drie vormen van een event (codes location, online, hybrid). |
| Link to the video call or the livestream | Link naar het videogesprek of de livestream | Lien vers l'appel vidéo ou la diffusion en direct | Niet de link naar het event zelf; vandaar de volledige naam. |
| Fixed since voting began. | Vastgelegd sinds het stemmen begon. | Fixé depuis le début du vote. | Onder het uitgeschakelde vinkje Iedereen mag stemmen. |
| initiator | initiatiefnemer | Initiateur | Vaste label voor wie een dossier start. |
| think tank | denktank | laboratoire d'idées | De aanbevolen Franse term (FranceTerme). |
| moderation | moderatie | modération | Zelfde woord. |
| Global Union, Global Union Forum, Freedom for all | (niet vertaald) | (niet vertaald) | Merknamen en slogan. |
| AI selection | AI-selectie | Sélection par IA | Het label op een achtergrondcijfer dat de AI uit een open bron koos; IA is de gewone Franse afkorting. |
| About me (profile section) | Over mij | À propos de moi | De sectie in het profiel met de vrijwillige vragen (representativiteit). |
| Who votes? | Wie stemt er mee? | Qui vote ? | De kop boven de vrijwillige vragen; kort en neutraal. |
| Prefer not to say | Zeg ik liever niet | Je préfère ne pas répondre | Het gangbare antwoord in Franse formulieren. |
| education: Basic / Secondary / Higher | Basis / Middelbaar / Hoger | De base / Secondaire / Supérieur | De drie ISCED-niveaus (0 tot en met 2, 3 en 4, 5 tot en met 8); "De base" en niet "Primaire", want het collège hoort in Frankrijk bij het secundair. De uitleg noemt collège, baccalauréat, CAP, BTS en grande école, nooit mbo of hbo. |
| Who voted (block) | Wie stemde mee | Qui a voté | Het blok bij de uitslag met de samenstelling van de stemmers. |
| Not filled in | Niet ingevuld | Non renseigné | Niets ingevuld, "zeg ik liever niet" of samengevoegd. |
| Smaller groups combined / Other countries combined | Kleinere groepen samen / Andere landen samen | Petits groupes regroupés / Autres pays regroupés | Groepen onder de 10 mensen, samen getoond. |
| Frozen (the totals at the end of voting) | Bevroren | Arrêté | De totalen worden één keer vastgelegd aan het eind van de stemfase en daarna niet meer gewijzigd. |
