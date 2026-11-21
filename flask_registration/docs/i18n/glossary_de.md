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
| pseudonym | pseudoniem | Pseudonym | Zelfde woord. |
| initiator | initiatiefnemer | Initiator | Vaste label voor de rol van wie een vraagstuk start. |
| think tank | denktank | Denkfabrik | Gangbaar Duits woord. |
| moderation | moderatie | Moderation | Zelfde woord. |
| Global Union, Global Union Forum, Freedom for all | (niet vertaald) | (niet vertaald) | Merknamen en slogan. |
