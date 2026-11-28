# Woordenlijst Duits (de)

De vaste Duitse vertaling van de kernbegrippen van het platform. Elke Duitse tekst (catalogus, mails, AI-prompt, landnamen) volgt deze lijst, zodat een begrip overal hetzelfde heet. Wie een begrip wil wijzigen, wijzigt het hier en daarna overal in `app/translations/de/LC_MESSAGES/messages.po`.

De Duitse vertaling is een machinevertaling (fase 1). `docs/i18n/review_de.csv` is het naleesbestand voor een Duitstalige (lees eerst "Eerst voor de nalezer" hieronder); `python i18n_review.py apply de` zet correcties daaruit terug in de catalogus.

## Eerst voor de nalezer

1. **Rolnamen.** De rolnamen zijn nu generiek mannelijk: Leser, Mitwirkender, Stimmberechtigter (ook Moderator, Senior-Moderator, Initiator). Graag per rol een neutrale en korte vorm voorstellen die op de statusladder in het profiel past (daar staan ze naast elkaar, op 320px breed nu al afgebroken als "Mitwir-kender" en "Stimm-berech-tigter"). Zonder genderster of Binnen-I; een korte zelfstandige vorm als "Mitglied" of een deelwoordvorm als "Mitwirkende" mag. Pas daarna deze tabel en de catalogus aan (zie de rijen "role:" hieronder).

## Stijlregels

- **Aanspreekvorm: du.** Overal en consequent, ook in mails en in de AI-prompt (past bij je/jij in het Nederlands). Kleine letter (du, dein, dich), zoals gebruikelijk op websites.
- **Geen genderster, geen Binnen-I, geen dubbele vormen.** Waar het kan een neutrale formulering ("alle, die abstimmen dürfen", "wer einen Vorschlag macht", "Teilnehmende"). Rolnamen zijn vaste labels (zie hieronder) en worden niet verbogen naar gender.
- **Eenvoudige taal.** Korte zinnen, op het niveau van de Nederlandse tekst. Geen ambtelijke stijl.
- **Geen lange of korte streepjes** (geen Gedankenstrich, – of —). Gebruik een komma, dubbele punt of haakjes. Een koppelteken in een samenstelling (E-Mail, Online-Fragestellung) is gewoon toegestaan. `tests/test_i18n_safety.py` bewaakt dit.
- **Niet vertalen:** "Global Union", "Global Union Forum", "Freedom for all", namen van organisaties, verdragen en standaarden (WTO, IFRS, UNCAC enzovoort).
- **Placeholders en markup ongewijzigd:** `%(name)s`, `%(num)d`, `<strong>`, `<a href="...">` blijven precies zoals in de msgid.
- **Meervoud:** Duits heeft twee vormen (`nplurals=2; plural=(n != 1)`), beide invullen.

## Kernbegrippen

| EN | NL | DE | Waarom |
|---|---|---|---|
| issue | vraagstuk | Fragestellung (Mz. Fragestellungen) | Een vraagstuk is een open vraag waarover deelnemers samen nadenken; "Fragestellung" is in Bürgerbeteiligung gangbaar en raakt die bedoeling beter dan "Thema" (te vaag) of "Problem" (te negatief). |
| proposal | voorstel | Vorschlag | Het gewone woord in Bürgerbeteiligung en Bürgerhaushalt ("Vorschläge einreichen"). |
| summary proposal | samenvattend voorstel | zusammenfassender Vorschlag | Letterlijk en direct begrijpelijk. |
| statement | stelling | These | De korte stelling van een voorstel waarover gestemd wordt; "These" is de gangbare term in debat en discussie. |
| explanation | toelichting | Erläuterung | Standaardwoord voor een toelichting bij een voorstel. |
| conversation | gesprek | Gespräch | Volgt het Nederlands; "Diskussion" is gereserveerd voor discussion (de discussiekolom). |
| discussion | discussie | Diskussion | Gangbaar, onderscheidt zich van Gespräch. |
| contribution | bijdrage | Beitrag | Gangbaar voor forumberichten. |
| fact | feit | Fakt (Mz. Fakten) | "Fakten" is gangbaar in faktenbasierte Diskussion; korter dan "Tatsache". |
| research | onderzoek | Studie | Als soort bron (naast Podcast, Nachrichtenartikel) is "Studie" in het Duits de natuurlijke term. |
| news article | nieuwsartikel | Nachrichtenartikel | Gewone term voor een krantenbericht. |
| support (steunbetuiging) | steunbetuiging, steunen | Unterstützung, unterstützen | Gangbaar bij petities en voorstellen ("Unterstützungen sammeln"). |
| quality mark | keurmerk | Gütesiegel | Het gangbare Duitse woord voor een keurmerk. |
| quality contribution | kwaliteitsbijdrage | Qualitätsbeitrag | Samenstelling, consequent met Gütesiegel. |
| ground rules (de pagina "Spelregels") | spelregels | Spielregeln | Zelfde beeld als in het Nederlands, in het Duits even gewoon voor de regels van een platform. |
| conduct rules | gedragsregels | Verhaltensregeln | De regels over gedrag in het forum (moderatie); onderscheiden van Spielregeln. |
| background page | achtergrondpagina | Hintergrundseite | Letterlijk en duidelijk. |
| objection | bezwaar | Einspruch | Gangbaar voor bezwaar tegen een beslissing; "Widerspruch" is een juridische term uit het bestuursrecht. |
| report (melding) | melding | Meldung | Een melding van een bijdrage aan de moderatie; "Meldung" is de gangbare term op platforms. |
| notification | melding (notificatie) | Benachrichtigung | In het Nederlands heet dit ook "melding"; het Duits houdt de twee uit elkaar. |
| summary (dagelijks/wekelijks) | samenvatting | Zusammenfassung | Gangbaar voor een digest-mail. |
| decision (moderatie) | beslissing, besluit | Entscheidung | Gangbaar en neutraal. |
| phase: facts and discussion | feiten en discussie | Fakten und Diskussion | Volgt de begrippen fact en discussion. |
| phase: adjusting | bijstellen | Überarbeiten | "Überarbeiten" is het gewone woord voor het bijwerken van een tekst of voorstel. |
| phase: voting | stemmen | Abstimmung | Het zelfstandig naamwoord voor de fase; werkwoord "abstimmen". |
| phase: summarising | samenvatten | Zusammenfassen | Volgt summary proposal. |
| phase: follow-up | vervolg | Weiteres Vorgehen | Wat er na de stemming met de uitkomst gebeurt; "Weiteres Vorgehen" is daarvoor de gangbare uitdrukking. |
| role: Reader | Lezer | Leser | Vaste label. |
| role: Contributor | Bijdrager | Mitwirkender | Vaste label; "Mitwirkende" is in Bürgerbeteiligung gangbaar voor wie actief bijdraagt. |
| role: Voter | Stemmer | Stimmberechtigter | Vaste label; de rol die het stemrecht heeft. "Wähler" hoort bij verkiezingen. |
| role: Moderator | Moderator | Moderator | Vaste label, ook in het Duits gangbaar. |
| role: Senior moderator | Senior moderator | Senior-Moderator | Vaste label; Duitse samenstelling met koppelteken. |
| right to vote | stemrecht | Stimmrecht | Het gewone woord. |
| follow / unfollow | volgen / ontvolgen | folgen / nicht mehr folgen | Gangbaar op platforms; "entfolgen" is spreektaal. |
| online / event | online / event | Online / Veranstaltung | "Online" is ook in het Duits gewoon; voor een event is "Veranstaltung" het neutrale woord. |
| username | gebruikersnaam | Benutzername | Het ene woord voor de naam waaronder je meedoet (@naam); nergens "Pseudonym". "Unter meinem Benutzernamen", "unter deinem Benutzernamen" (n-declinatie). |
| Former account | Voormalig account | Ehemaliges Konto | Het label dat bij een account dat er niet meer is in plaats van de naam staat; zonder @, het is geen gebruikersnaam. Zegt niet wie het account verwijderde, en is genderneutraal. |
| organiser | organisator | Veranstaltende (Person, die die Veranstaltung organisiert) | Wie een event maakt en beheert. Neutrale deelwoordvorm, zoals "Teilnehmende"; geen "Veranstalter/in" of genderster. Als los label (de pil bij je eigen events, stap 1d) "Veranstaltende". |
| New event | Nieuw event | Neue Veranstaltung | De knop in Denk mee en de titel van het formulier. |
| Everyone may vote | Iedereen mag stemmen | Alle dürfen abstimmen | De instelling op een event; neutraal, zonder "jeder". |
| Participants may add issues | Deelnemers mogen vraagstukken toevoegen | Teilnehmende dürfen Fragestellungen hinzufügen | De instelling op een event. |
| Where does the event take place? | Waar vindt het event plaats? | Wo findet die Veranstaltung statt? | De vraag boven de vorm van een event. |
| At a location / Online / At a location and online | Op een locatie / Online / Op een locatie en online | An einem Ort / Online / An einem Ort und online | De drie vormen van een event (codes location, online, hybrid). |
| Link to the video call or the livestream | Link naar het videogesprek of de livestream | Link zum Videoanruf oder zum Livestream | Niet de link naar het event zelf; vandaar de volledige naam. |
| Fixed since voting began. | Vastgelegd sinds het stemmen begon. | Festgelegt, seit die Abstimmung begonnen hat. | Onder het uitgeschakelde vinkje Iedereen mag stemmen. |
| For everyone | Voor iedereen | Für alle | De soort vraagstuk waar iedereen aan meedoet (niet van een event); vervangt het label "Online". |
| From events | Van events | Aus Veranstaltungen | Het filter in Denk mee. |
| Planned | Gepland | Geplant | Toestand van een event: de eerste dag is nog niet begonnen. |
| Your events | Jouw events | Deine Veranstaltungen | Het blok bovenaan Denk mee. |
| Back to the event / Go to the event | Terug naar het event / Naar het event | Zurück zur Veranstaltung / Zur Veranstaltung | De knoppen in Jouw events. |
| event code | eventcode | Veranstaltungscode | De code van 8 tekens in de link. |
| Do you have an event code? | Heb je een eventcode? | Hast du einen Veranstaltungscode? | Het veld in Denk mee. |
| Link to this event | Link naar dit event | Link zu dieser Veranstaltung | In de strook voor de organisator. |
| Copy link | Link kopiëren | Link kopieren | Icoonknop. |
| Copy QR code / Download QR code | QR-code kopiëren / QR-code downloaden | QR-Code kopieren / QR-Code herunterladen | Icoonknoppen. |
| Scan to take part | Scan om mee te doen | Scannen zum Mitmachen | Onder de QR-code. |
| Make a new link | Nieuwe link maken | Neuen Link erstellen | De oude link en QR-code werken daarna niet meer. |
| Share with all participants | Delen met alle deelnemers | Mit allen Teilnehmenden teilen | Na afloop, alleen de organisator. |
| Join via the video call | Meedoen via het videogesprek | Über den Videoanruf mitmachen | De link naar het videogesprek op de eventpagina. |
| You are at: … | Je bent bij: … | Du bist bei: … | De balk bovenaan de pagina's van een event; daarna de naam van het event. |
| initiator | initiatiefnemer | Initiator | Vaste label voor de rol van wie een vraagstuk start. |
| think tank | denktank | Denkfabrik | Gangbaar Duits woord. |
| moderation | moderatie | Moderation | Zelfde woord. |
| Global Union, Global Union Forum, Freedom for all | (niet vertaald) | (niet vertaald) | Merknamen en slogan. |
| AI selection | AI-selectie | KI-Auswahl | Het label op een achtergrondcijfer dat de AI uit een open bron koos. Duits gebruikt KI, niet AI. |
| About me (profile section) | Over mij | Über mich | De sectie in het profiel met de vrijwillige vragen (representativiteit). |
| Who votes? | Wie stemt er mee? | Wer stimmt mit ab? | De kop boven de vrijwillige vragen; "mit abstimmen" volgt het Nederlandse "meestemmen". |
| Prefer not to say | Zeg ik liever niet | Möchte ich nicht sagen | Het gangbare antwoord in Duitse formulieren; ich-vorm zoals het Nederlands. |
| education: Basic / Secondary / Higher | Basis / Middelbaar / Hoger | Grundbildung / Mittlere Bildung / Höhere Bildung | De drie ISCED-niveaus (0 tot en met 2, 3 en 4, 5 tot en met 8); de uitleg noemt Duitse voorbeelden (Hauptschul- of Realschulabschluss bij Grundbildung, Abitur of Lehre bij Mittlere Bildung, Fachhochschule), nooit mbo of hbo. De vraag: "Was ist die höchste Ausbildung, die du abgeschlossen hast oder gerade machst?" |
| Who voted (block) | Wie stemde mee | Wer abgestimmt hat | Het blok bij de uitslag met de samenstelling van de stemmers. |
| Not filled in | Niet ingevuld | Nicht angegeben | Niets ingevuld, "zeg ik liever niet" of samengevoegd. |
| Smaller groups combined / Other countries combined | Kleinere groepen samen / Andere landen samen | Kleinere Gruppen zusammengefasst / Andere Länder zusammen | Groepen onder de 10 mensen, samen getoond. |
| Frozen (the totals at the end of voting) | Bevroren | Festgehalten | De totalen worden één keer vastgelegd aan het eind van de stemfase en daarna niet meer gewijzigd. |
