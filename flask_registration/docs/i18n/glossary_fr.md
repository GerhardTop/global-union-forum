# Woordenlijst Frans (fr)

De vaste Franse vertaling van de kernbegrippen van het platform. Elke Franse tekst (catalogus, mails, AI-prompt, landnamen) volgt deze lijst, zodat een begrip overal hetzelfde heet. Wie een begrip wil wijzigen, wijzigt het hier en daarna overal in `app/translations/fr/LC_MESSAGES/messages.po`.

De Franse vertaling is een machinevertaling (fase 1). `docs/i18n/review_fr.csv` is het naleesbestand voor een Franstalige (lees eerst "Eerst voor de nalezer" hieronder); `python i18n_review.py apply fr` zet correcties daaruit terug in de catalogus.

## Eerst voor de nalezer

1. **"Dossier" voor vraagstuk.** Een vraagstuk is een open vraag waarover deelnemers samen nadenken. "Question" botst met de gewone vraag (een vraagstuk begint "met een vraag of probleem") en met de FAQ, "sujet" is te vaag, "problématique" te zwaar. "Dossier" is in de politiek gangbaar voor een onderwerp waaraan gewerkt wordt ("le dossier des retraites"). Is "Lancer un dossier" natuurlijk?
2. **Rolnamen.** Lecteur, Contributeur, Votant, Modérateur, Modérateur senior en Initiateur zijn generiek mannelijk. Graag een neutrale en korte vorm voorstellen die op de statusladder in het profiel past, zonder punt médian.
3. **"Contestation" / "contester" voor bezwaar** tegen een beslissing van de moderatie ("objection" klinkt juridisch). Past dat?
4. **"Énoncé" voor stelling** (de korte stelling van een voorstel waarover gestemd wordt). Alternatieven: "affirmation", "thèse".
5. **Typografie.** Franse aanhalingstekens « » (met vaste spatie binnen) en een vaste spatie voor ; : ! ?, overal consequent. Graag melden als het ergens niet klopt.

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
| statement | stelling | énoncé | De korte stelling van een voorstel waarover gestemd wordt; neutraal en duidelijk. |
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
| objection | bezwaar | contestation, contester | Bezwaar tegen een beslissing van de moderatie; "recours" is juridisch. |
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
| pseudonym | pseudoniem | pseudonyme | Zelfde woord. |
| initiator | initiatiefnemer | Initiateur | Vaste label voor wie een dossier start. |
| think tank | denktank | laboratoire d'idées | De aanbevolen Franse term (FranceTerme). |
| moderation | moderatie | modération | Zelfde woord. |
| Global Union, Global Union Forum, Freedom for all | (niet vertaald) | (niet vertaald) | Merknamen en slogan. |
