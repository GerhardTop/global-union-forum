# Woordenlijst Spaans (es)

De vaste Spaanse vertaling van de kernbegrippen van het platform. Elke Spaanse tekst (catalogus, mails, AI-prompt, landnamen) volgt deze lijst, zodat een begrip overal hetzelfde heet. Wie een begrip wil wijzigen, wijzigt het hier en daarna overal in `app/translations/es/LC_MESSAGES/messages.po`.

De Spaanse vertaling is een machinevertaling (fase 1). `docs/i18n/review_es.csv` is het naleesbestand voor een Spaanstalige (lees eerst "Eerst voor de nalezer" hieronder); `python i18n_review.py apply es` zet correcties daaruit terug in de catalogus.

## Eerst voor de nalezer

1. **Rolnamen.** Waar het kon, is de rolnaam een vorm die voor iedereen gelijk is: Participante (Bijdrager) en Votante (Stemmer). Lector, Moderador, Moderador sénior en Promotor zijn generiek mannelijk. Graag een neutrale en korte vorm voorstellen die op de statusladder in het profiel past, zonder @, x of e.
2. **"Cuestión" voor vraagstuk.** Gekozen omdat het een open vraag is waarover men samen nadenkt; "tema" is te vaag, "problema" te negatief. Is "cuestión" (mv. "cuestiones") natuurlijk als naam van een onderdeel van het platform ("Iniciar una cuestión")?
3. **"Promotor" voor initiatiefnemer.** De gangbare term voor wie een burgerinitiatief start. Alternatief: "iniciador".
4. **"Contribución" voor bijdrage.** Gekozen omdat het in Spanje en Latijns-Amerika even gewoon is ("aportación" is Spaans, "aporte" Latijns-Amerikaans).
5. **Afkortingen van organisaties** staan in de Spaanse vorm (OMC, OMA, GAFI, OIT, OMS, UIT, OACI, OMI, NIIF, CNUCC, CMNUCC; ONU, OCDE), behalve IASB en Codex Alimentarius. Klopt dat overal in het manifest?
6. **Losse woordkeuzes om na te lopen:** "Juego limpio" (fairness, een kwaliteitscriterium), "cabildeo" (lobby), "Recuento provisional" (tussenstand van een stemming), "Prompt utilizado", "Afiliación" (lidmaatschap), "Quiénes somos" (menu Over ons), maandafkortingen zonder punt ("ene", "sept").
7. **Generiek mannelijk waar het lastig was:** onder meer "reportado", "movido", "oculto" in statuslabels, "el autor" in moderatieteksten. Waar mogelijk is de zin om het account of de persoon heen gebouwd ("Tu cuenta está suspendida").

## Stijlregels

- **Aanspreekvorm: tú.** Overal en consequent, ook in mails en in de AI-prompt (past bij je/jij in het Nederlands). Geen usted, geen vosotros: meervoud is "ustedes" waar het echt nodig is, maar meestal is een zin in het enkelvoud of onpersoonlijk beter.
- **Neutraal internationaal Spaans.** Geen streekwoorden; woorden die in Spanje en Latijns-Amerika even gewoon zijn (contraseña, correo electrónico, iniciar sesión, en línea).
- **Geen inclusieve schrijfwijzen** (geen @, x of e). Waar het kan een neutrale formulering ("quienes votan", "las personas que participan"). Rolnamen zijn vaste labels (zie hieronder).
- **Eenvoudige taal.** Korte zinnen, op het niveau van de Nederlandse tekst. Geen ambtelijke stijl.
- **Geen lange of korte streepjes** (geen raya, – of —). Gebruik een komma, dubbele punt of haakjes. Een koppelteken in een samenstelling is gewoon toegestaan. `tests/test_i18n_safety.py` bewaakt dit.
- **Leestekens:** ¿ en ¡ aan het begin van een vraag of uitroep. Aanhalingstekens zoals in de msgid ("...").
- **Niet vertalen:** "Global Union", "Global Union Forum", "Freedom for all", namen van organisaties, verdragen en standaarden (WTO, IFRS, UNCAC enzovoort).
- **Placeholders en markup ongewijzigd:** `%(name)s`, `%(num)d`, `<strong>`, `<a href="...">` blijven precies zoals in de msgid.
- **Meervoud:** Spaans heeft twee vormen (`nplurals=2; plural=(n != 1)`), beide invullen.

## Kernbegrippen

| EN | NL | ES | Waarom |
|---|---|---|---|
| issue | vraagstuk | cuestión (mv. cuestiones) | Een open vraag waarover deelnemers samen nadenken; "tema" is te vaag, "problema" te negatief. |
| proposal | voorstel | propuesta | Het gewone woord in burgerparticipatie ("presentar propuestas"). |
| summary proposal | samenvattend voorstel | propuesta de síntesis | Het voorstel dat de uitkomst samenvat; "síntesis" is daarvoor het gangbare woord. |
| statement | stelling | afirmación (v.) | De korte stelling van een voorstel waarover gestemd wordt; neutraal en duidelijk. Vrouwelijk: "la afirmación", "revisada", "fija". |
| explanation | toelichting | explicación | Standaardwoord voor een toelichting bij een voorstel. |
| conversation | gesprek | conversación | Volgt het Nederlands; "debate" is gereserveerd voor discussion (de discussiekolom). |
| discussion | discussie | debate | "Discusión" kan als ruzie klinken; "debate" is de gewone term voor een inhoudelijke discussie. |
| contribution | bijdrage | contribución | Even gewoon in Spanje en Latijns-Amerika. |
| fact | feit | hecho | Gangbaar ("basado en hechos"). |
| research | onderzoek | estudio | Als soort bron (naast pódcast, noticia) is "estudio" de natuurlijke term. |
| news article | nieuwsartikel | noticia | Gewone term voor een krantenbericht. |
| support (steunbetuiging) | steunbetuiging, steunen | apoyo, apoyar | Gangbaar bij petities en voorstellen ("reunir apoyos"). |
| quality mark | keurmerk | sello de calidad | Het gangbare Spaanse woord voor een keurmerk. |
| quality contribution | kwaliteitsbijdrage | contribución de calidad | Consequent met sello de calidad en contribución. |
| ground rules (de pagina "Spelregels") | spelregels | reglas del juego | Zelfde beeld als in het Nederlands, in het Spaans even gewoon. |
| conduct rules | gedragsregels | normas de conducta | De regels over gedrag in het forum (moderatie); onderscheiden van reglas del juego. |
| background page | achtergrondpagina | página de contexto | "Contexto" is het gewone woord voor achtergrond bij een onderwerp. |
| objection | bezwaar | objeción | Bezwaar tegen een beslissing van de moderatie; "recurso" is juridisch. |
| report (melding) | melding | reporte (m.), reportar | Een melding van een bijdrage aan de moderatie. Mannelijk: "el reporte", "reportes abiertos"; status "reportado". Niet "denuncia" (te juridisch). |
| notification | melding (notificatie) | notificación | In het Nederlands heet dit ook "melding"; het Spaans houdt de twee uit elkaar. |
| summary (dagelijks/wekelijks) | samenvatting | resumen | Gangbaar voor een digest-mail. |
| decision (moderatie) | beslissing, besluit | decisión | Gangbaar en neutraal. |
| phase: facts and discussion | feiten en discussie | Hechos y debate | Volgt de begrippen fact en discussion. |
| phase: adjusting | bijstellen | Revisión | Het gewone woord voor het bijwerken van een voorstel. |
| phase: voting | stemmen | Votación | Het zelfstandig naamwoord voor de fase; werkwoord "votar". |
| phase: summarising | samenvatten | Síntesis | Volgt summary proposal. |
| phase: follow-up | vervolg | Seguimiento | Wat er na de stemming met de uitkomst gebeurt. |
| role: Reader | Lezer | Lector | Vaste label. |
| role: Contributor | Bijdrager | Participante | Vaste label; voor iedereen dezelfde vorm, en kort. |
| role: Voter | Stemmer | Votante | Vaste label; voor iedereen dezelfde vorm. |
| role: Moderator | Moderator | Moderador | Vaste label. |
| role: Senior moderator | Senior moderator | Moderador sénior | Vaste label. |
| right to vote | stemrecht | derecho a voto | Het gewone woord. |
| follow / unfollow | volgen / ontvolgen | seguir / dejar de seguir | Gangbaar op platforms. |
| online / event | online / event | en línea / evento | "En línea" is neutraal internationaal; "evento" voor een bijeenkomst op een plek. |
| username | gebruikersnaam | nombre de usuario | Het ene woord voor de naam waaronder je meedoet (@naam); nergens "seudónimo". Met het voorzetsel "con": "con tu nombre de usuario". |
| initiator | initiatiefnemer | Promotor | Vaste label voor wie een cuestión start; de gangbare term bij burgerinitiatieven. |
| think tank | denktank | laboratorio de ideas | De door de RAE aanbevolen term. |
| moderation | moderatie | moderación | Zelfde woord. |
| Global Union, Global Union Forum, Freedom for all | (niet vertaald) | (niet vertaald) | Merknamen en slogan. |
| AI selection | AI-selectie | Selección por IA | Het label op een achtergrondcijfer dat de AI uit een open bron koos; IA is de gewone Spaanse afkorting. |
| About me (profile section) | Over mij | Sobre mí | De sectie in het profiel met de vrijwillige vragen (representativiteit). |
| Who votes? | Wie stemt er mee? | ¿Quién vota? | De kop boven de vrijwillige vragen; kort en neutraal. |
| Prefer not to say | Zeg ik liever niet | Prefiero no decirlo | Het gangbare antwoord in Spaanstalige formulieren. |
| education: Basic / Secondary / Higher | Basis / Middelbaar / Hoger | Básica / Media / Superior | De drie ISCED-niveaus (0 tot en met 2, 3 en 4, 5 tot en met 8), zoals "educación básica, media, superior"; de uitleg noemt "secundaria completa" en formación profesional (niet "bachillerato": dat betekent per land iets anders), nooit mbo of hbo. |
| Who voted (block) | Wie stemde mee | Quién votó | Het blok bij de uitslag met de samenstelling van de stemmers. |
| Not filled in | Niet ingevuld | Sin respuesta | Niets ingevuld, "zeg ik liever niet" of samengevoegd. |
| Smaller groups combined / Other countries combined | Kleinere groepen samen / Andere landen samen | Grupos pequeños agrupados / Otros países juntos | Groepen onder de 10 mensen, samen getoond. |
| Frozen (the totals at the end of voting) | Bevroren | Fijado | De totalen worden één keer vastgelegd aan het eind van de stemfase en daarna niet meer gewijzigd. |
