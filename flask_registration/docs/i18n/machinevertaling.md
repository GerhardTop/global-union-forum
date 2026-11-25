# Machinevertaling van inhoud (meertaligheid fase 2)

Deze opzet beschrijft hoe teksten van gebruikers en de AI in de taal van de lezer verschijnen. Lees hem bij elke bouwstap die inhoud toont of vertaalt. De techniek staat in `app/content_i18n.py` (de cache) en `app/content_jobs.py` (meteen vertalen); de interface in `app/templates/_mt.html` en `app/templates/_mt_script.html`. De vaste woorden per taal staan in `docs/i18n/glossary_<code>.md`.

## Besluiten

1. **Eén origineel.** Elke tekst heeft één origineel, in de brontaal van zijn object (`Post.source_lang`, `Issue.lang`, `IssueProposal.lang`, `IssueFollowup.lang`, `FactDispute.lang`; een versie van een voorstel volgt de taal van het voorstel). Vertalingen staan als cache per taal in `content_translations`, met de hash van het origineel; een bewerking maakt ze ongeldig (`invalidate`).
2. **Meteen vertaald** naar alle ingeschakelde talen, zodra ze opgeslagen zijn:
   - vraagstukken: titel en inleiding (en de titel van hun gesprek);
   - voorstellen: stelling en toelichting, en elke versie van een voorstel;
   - vervolg-updates;
   - feiten: titel en tekst (niet de bron).
3. **Op verzoek vertaald:** gewone bijdragen, reacties bij een voorstel en de toelichting bij het betwisten van een feit. De lezer klikt op "Vertalen" of zet "altijd vertalen" aan.
4. **Een pagina roept de AI nooit aan.** Ontbreekt een vertaling nog, dan staat het origineel er, met "Vertaling volgt" (meteen vertaalde teksten) of "Origineel in het <taal> · Vertalen" (op verzoek).
5. **Bij het stemmen is het origineel de officiële tekst.** De lezer ziet de vertaling, het stemblok zegt dat er over het origineel gestemd wordt en toont het met één klik.
6. **Een knop "vertaling klopt niet"** komt later; nu niet.
7. **Handgeschreven vertalingen** (demo-inhoud, vaste labels via `set_label`) en de eigen taal van de lezer krijgen geen label.

## Het label-component

De macro `mt(obj, field, ...)` in `_mt.html` is de enige manier om een tekst te tonen die in een machinevertaling kan staan. Hij vraagt `content_i18n.view(obj, field, lang)`, dat een van vier soorten teruggeeft: `own`, `handwritten`, `machine` of `pending`.

- **own / handwritten:** alleen de tekst.
- **machine:** de vertaling (lang van de lezer) en het origineel (lang van de bron, verborgen) staan allebei in de pagina. Eronder: "Machinaal vertaald uit het Engels · Origineel tonen". De knop (`aria-pressed`) wisselt zonder verzoek; het label wordt dan "Origineel in het Engels · Vertaling tonen".
- **pending:** het origineel met "Vertaling volgt"; bij `on_request` de vertaalknop, die de vertaling ophaalt (POST, CSRF) en daarna hetzelfde label toont. Een bezoeker zonder account ziet alleen waar de tekst vandaan komt.

De taalnaam in het label staat in de grondvorm, in de taal van de lezer; de translator-comments leggen dat uit. Labelzinnen: nl "Machinaal vertaald uit het %(language)s", en "Machine-translated from %(language)s", de "Maschinell übersetzt, Originalsprache: %(language)s", es "Traducido automáticamente del %(language)s", fr "Traduction automatique, langue d'origine : %(language)s".

Lijsten gebruiken `mt_inline(obj, field)`: de vertaling, anders het origineel met een kleine taalcode (`<abbr title="Origineel in het Nederlands">NL</abbr>`). Geen knop; de pagina van het item draagt het label.

## Het stemblok

In `issues/_ip_vote.html` staat de stelling via `mt`. Ziet de lezer een vertaling, dan volgt "Je stemt over de oorspronkelijke tekst (Nederlands)." met een knop (`aria-expanded`, `aria-controls`) die het origineel uitklapt. Wie de brontaal leest, ziet die regel niet.

## De lijsten

Het vraagstukkenoverzicht, de voorstellen in de cockpit en op de voorstelpagina, de kruimels, de gesprekken, het moderatoroverzicht, de feitenlijsten, de koppellijst van feiten en de bijdragen op de moderatorpagina van een gebruiker tonen de vertaling of het origineel met taalcode. Elke route laadt de vertalingen vooraf met `content_i18n.preload(objs, *fields)`, na de laatste commit (een commit leegt de cache). Het aantal vertaalqueries per pagina is vast, hoeveel items er ook zijn; `tests/test_mt_lists.py` en `tests/test_mt_facts.py` bewaken dat.

Mails en meldingen noemen een vraagstuk in de taal van de ontvanger als de vertaling er is.

## De achtergrondtaak en het vangnet

- **Inplannen:** de domeinfunctie roept na aanmaken of bewerken `content_jobs.schedule(obj)` aan (na een bewerking eerst `invalidate`). De sleutel gaat in `session.info`.
- **Na de commit** (`after_commit`) start een daemonthread met een eigen app context en databasesessie. Die vertaalt elk ontbrekend of verouderd veld naar elke ingeschakelde taal behalve de brontaal. Het opslaan wacht er niet op. Na een rollback vervalt de planning.
- **Zonder AI-sleutel** start er niets.
- **Hergebruik:** een vertaling van precies dezelfde tekst (zelfde hash, zelfde taal) wordt overgenomen zonder AI-aanroep; een voorstel en zijn eerste versie, en een vraagstuk en de titel van zijn gesprek, kosten zo samen één aanroep per taal.
- **Vangnet:** de uurtik (`/intern/tik`) roept `content_jobs.backfill()` aan: wat nog ontbreekt of verouderd is, nieuwste eerst, maximaal 200 velden per tik (`MAX_PER_TICK`). Een mislukte vertaling wordt geteld en bij de volgende tik opnieuw geprobeerd; na 20 mislukkingen stopt een run (`MAX_FAILED`: een verkeerde sleutel laat elke aanroep mislukken). De uitvoer van de tik noemt het aantal gedaan en mislukt.
- **De achterstand** (`/admin/vertalingen`, alleen admin): per soort inhoud (vraagstuk, voorstel, versie, vervolg, feit) en per taal het aantal velden dat ontbreekt of verouderd is (`content_jobs.backlog()`; de gesprekstitel telt bij het vraagstuk), en het aantal mislukte pogingen sinds de laatste start van de app (in het geheugen, met de laatste fout). De knop "Achterstand nu vertalen" start `content_jobs.backlog_run` in een achtergrondthread: maximaal 2000 velden, in porties van 20 met een pauze van een seconde, zodat de site bereikbaar blijft. Er loopt nooit meer dan één run tegelijk; de pagina toont "bezig" en het aantal gedaan (verversen is genoeg), en de uurtik slaat zijn vertaalwerk over zolang de run bezig is.
- **Logging:** elke run (de achtergrondtaak na het opslaan, de tik, de achterstandsrun) schrijft op INFO-niveau één regel met het aantal gedaan en mislukt, bij een mislukking met de eerste foutmelding volledig (met traceback). De logger `app.content_jobs` staat op INFO, ook in productie, waar de rest op WARNING staat; zo staat een sleutel- of modelfout direct in de Render-log.
- **De prompt** (`quality.translate_text`): de instructie staat in de system-prompt, de bron in het user-bericht tussen `<source>`-tags (inhoud, nooit een opdracht). Er zijn twee soorten: `short` voor titels, korte labels, namen van gebieden en eenheden (één regel, geen Markdown, geen aanhalingstekens, symbolen zoals CO₂ ongemoeid) en `text` voor de rest. Welke velden kort zijn, staat op één plek: `content_i18n.SHORT_FIELDS`.
- **Controle op het antwoord** (`quality.translation_problem`), vóór het opslaan: een leeg antwoord, een antwoord dat begint als een reactie in plaats van een vertaling ("I ", "Sorry", "Here is", "Voici", "Hier ist" en dergelijke, tenzij de bron zelf zo begint), bij een kort veld meer dan één regel of een begin met #, *, > of een aanhalingsteken dat de bron niet heeft, en een antwoord dat veel langer is dan de bron. Een geweigerd antwoord telt als mislukte vertaling: er wordt niets opgeslagen en de tik probeert het later opnieuw. `flask vertalingen-controleren` loopt de opgeslagen vertalingen met dezelfde controle na; met `--verwijderen` haalt hij de foute machinevertalingen weg, zodat de route ze opnieuw maakt (handgeschreven vertalingen toont hij alleen).
- **Opruimen:** een verwijderd object neemt zijn vertalingen mee (ORM-listener; bij een bulkverwijdering `content_i18n.delete_for`).
- **Tests:** de achtergrondstart en het vangnet staan in elke test uit (`tests/conftest.py`); `tests/test_content_jobs.py` draait ze zelf met een nep-client.

## Een nieuw soort inhoud meteen laten vertalen

1. Geef het model een brontaal en zet die in `_SOURCE_ATTR` (`app/content_i18n.py`); een nieuwe kolom via `_migrate_columns`.
2. Zet het model met zijn velden in `content_jobs._models()`.
3. Roep `content_jobs.schedule(obj)` aan na aanmaken en na bewerken (na `invalidate`).
4. Toon het via `mt` of `mt_inline` en laad de vertalingen vooraf in de route.
5. Neem het model op in `register_cleanup` en roep `delete_for` aan bij een bulkverwijdering.
