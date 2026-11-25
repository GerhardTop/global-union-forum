# CLAUDE.md

## Vraagstuk-model

`docs/vraagstuk-ontwerp.md` is de canonieke bouw-specificatie voor het vraagstuk-model (Issue) en is bij elke bouwstap leidend.

## Achtergrondcijfers

`docs/achtergrondcijfers.md` is de bouw-specificatie voor de achtergrondcijfers (grafieken met cijfers uit open bronnen, door de AI geselecteerd, als soort feit) en is bij elke bouwstap van dat spoor leidend. Het ontwerp staat in `docs/design/achtergrondcijfers-kolom.html` (de kaart in de feitenkolom) en `docs/design/achtergrondcijfers-uitgeklapt.html` (de grafiekpagina).

## Communicatie

Wanneer een taak vereist dat de gebruiker zelf iets buiten de chat doet (een wachtwoord of credential aanleveren, een commando met de hand draaien, of iets uitvoeren in een externe omgeving zoals de Neon- of Render-console), zet dat als allereerste zin van je antwoord, duidelijk zichtbaar. De gebruiker leest niet altijd het volledige antwoord.

## Werkregels

Wijzig geen code terwijl de volledige suite draait. Commit alleen als de laatste volledige run op precies de te committen code groen is; bij een rode run eerst de oorzaak vinden, ook als die buiten de wijziging lijkt te liggen.

De tests draaien in willekeurige volgorde (pytest-randomly; installeer de testtools met `pip install -r requirements-dev.txt`). Een test mag dus niet afhangen van wat een andere test achterlaat: maak eigen data met unieke teksten, ruim op wat je commit, en tel op gedeelde overzichten (zoals `/moderator`) alleen je eigen items (`?alle=1`, of de toename). tests/conftest.py begint elke test met een lege `flask.g`, een lege databasesessie en lege rate-limit-tellers. Faalt een run, dan staat de seed in de uitvoer ("Using --randomly-seed=..."; het script toont hem bij rood); herhaal precies die volgorde met `pytest --randomly-seed=<seed>`.

Bij elke wijziging die geen zichtbaar verschil hoort te geven (een refactor, een query- of N+1-fix, een migratie, opruimen): vergelijk de pagina's met `scripts/page_snapshot.py`. Neem op met de code van vóór de wijziging (`python scripts/page_snapshot.py opnemen`, bijvoorbeeld met de wijziging in een stash), breng de wijziging aan en vergelijk (`python scripts/page_snapshot.py vergelijken`). Het script rendert alle demo-pagina's, ook de vergrote weergaven, als bezoeker en als lid, in elke ingeschakelde taal (200 pagina's), op dezelfde demodatabase, en geeft per pagina "gelijk" of het verschil; de opname staat buiten git (standaard `$TMPDIR/guf-page-snapshot`). Vergelijk kort na het opnemen: de pagina's noemen tijden ten opzichte van nu. Een verschil dat je niet verwachtte: eerst de oorzaak vinden.

Pushen gaat altijd via `scripts/check_and_push.sh`, nooit met een losse `git push`. Het script draait de volledige suite op de gecommitte code (het stopt bij niet-gecommitte wijzigingen in `flask_registration/`), schrijft de uitvoer naar `.check_and_push.log`, toont de laatste regels en pusht alleen als pytest met 0 eindigt en de code tijdens de run niet veranderde. Draai pytest nooit via een pipe zonder `set -o pipefail` (zoals `pytest | tail`): dan is de afsluitcode die van het laatste commando en lijkt een rode run groen. Lees bij een losse run de afsluitcode van pytest zelf, of schrijf naar een bestand en lees dat.

## Ontwerp

Afgeronde design-artboards zijn leidend voor de vormgeving. De artboards staan als statische referentie-HTML in `docs/design/` (bijvoorbeeld `docs/design/gebruiker.html`), met `docs/design/README.md` als overzicht (artboard per scherm, tokens, componenten). Bouw elk UI-scherm visueel conform het betreffende artboardbestand in `docs/design/`; hergebruik niet standaard de bestaande code-markup. De schermopdracht noemt het artboardbestand waar naartoe gebouwd wordt.

## Vertalingen (i18n)

Alle tekst voor de gebruiker gaat via gettext; geen `{% if lang == 'nl' %}`-knips meer (tests/test_i18n_safety.py bewaakt dat). De msgid is Engels, de Nederlandse tekst staat in de catalogus.

Werkwijze na een tekstwijziging, vanuit `flask_registration/`:

1. `pybabel extract -F babel.cfg -k lazy_gettext -k lazy_pgettext:1c,2 -c TRANSLATORS: -o messages.pot app` (zonder `-k lazy_gettext` en `-c TRANSLATORS:` mist extract de labels in Python en de vertalerscommentaren).
2. `pybabel update -i messages.pot -d app/translations -N --no-wrap` is weer veilig: de catalogi bevatten geen ongebruikte msgids meer, dus er ontstaat niets obsolete. Altijd met `-N` (geen fuzzy matching).
3. Vul de nieuwe NL-teksten in (en de EN-msgstr gelijk aan de msgid); geen lege of fuzzy NL-items.
4. `pybabel compile -d app/translations` en commit de .mo mee (Render compileert niet bij de build).

Bij een inhoudelijke wijziging van de privacyverklaring (`app/templates/privacy.html`) altijd `PRIVACY_UPDATED` in `app/routes/main.py` bijwerken: de regel "Laatst bijgewerkt" toont die datum, met Babel opgemaakt in de taal van de lezer (één msgid `Last updated: %(date)s`, geen datum in de vertalingen).

Een vertaling die gelijk is aan de msgid mag alleen als hij op de toegestane lijst van die taal staat (`SAME_AS_MSGID` in tests/test_i18n_safety.py; voor nl is dat `SAME_IN_DUTCH`), met reden. Geen lange of korte streepjes in msgids of vertalingen.

### Het taalregister

`config.py` is de enige plek met talen: `LANGUAGES` (alle talen die het platform kent: code, naam in de eigen taal, Engelse naam, schrijfrichting, en `reviewed`: of een moedertaalspreker de vertaling heeft nagelezen), `ENABLED_LANGUAGES` (de talen die aanstaan), `DEFAULT_LANGUAGE` (nl: de terugval voor mails zonder opgeslagen taal, migraties en dergelijke), `VISITOR_FALLBACK_LANGUAGE` (en: voor een bezoeker zonder keuze wiens browser om geen ingeschakelde taal vraagt) en `SOURCE_LANGUAGE` (en, de taal van de msgids). Code en templates vragen het register via `app/i18n.py` (`enabled_languages()`, `is_enabled()`, `normalize()`, `language_name()`, `text_direction()`, `is_reviewed()`, `auto_translate_default()`); geen eigen lijsten als `('nl', 'en')`. De taalkiezer in header (voor bezoekers en leden, naast de avatar en in het mobiele menu), footer en profiel is een lus over de ingeschakelde talen. `get_locale` kiest in deze volgorde: sessie, het cookie `lang` (gezet door `/lang/<code>` en bij inloggen via `i18n.remember`, een jaar, functioneel en vermeld op de privacypagina), de browser (Accept-Language), `VISITOR_FALLBACK_LANGUAGE`. Bij inloggen wint de opgeslagen taal van het lid. Code die de taal van het verzoek nodig heeft (de brontaal van nieuwe inhoud, de taal die een `session.clear()` overleeft) gebruikt `i18n.current()`, nooit `session.get('lang', 'nl')`. Automatisch vertalen staat standaard aan voor iedereen wiens taal niet de standaardtaal is.

Een taal aanzetten (voorbeeld de), vanuit `flask_registration/`:

1. Staat de taal niet in `LANGUAGES`, voeg hem toe (code, naam in de eigen taal, Engelse naam voor de AI-prompts, `ltr` of `rtl`, `reviewed`). Maak eerst een woordenlijst `docs/i18n/glossary_<code>.md` met de kernbegrippen en stijlregels (voorbeeld: `glossary_de.md`).
2. Extract zoals hierboven, dan `pybabel init -i messages.pot -d app/translations -l de --no-wrap`.
3. Vertaal alles in `app/translations/de/LC_MESSAGES/messages.po`: geen lege of fuzzy items. Teksten die gelijk mogen blijven aan de msgid krijgen een eigen lijst in `SAME_AS_MSGID['de']`, met reden.
   Landnamen in `app/data/landenlijst_data.json`: verplicht een eigen naam onder `names` in de nieuwe taal voor de vier politiek gevoelige gebieden (`"sensitive": true`: PS, HK, TW, XK); zij vallen nooit terug op Babel (tests/test_landenlijst_i18n.py faalt anders). Loop daarna eenmalig de Babel-namen van de overige landen in de nieuwe taal na; waar die informeel of fout zijn, neem een eigen naam op.
4. `pybabel compile -d app/translations` en commit de .po en .mo.
5. Zet `"de"` in `ENABLED_LANGUAGES`. De catalogustests gelden dan vanzelf ook voor de; draai de volledige suite.

Een machinevertaling staat in het register met `"reviewed": False`; de footer zegt dan in die taal dat de pagina automatisch vertaald is. Geef de AI-taalregel voor de kwaliteitsprompt zo nodig een eigen regel in `_OWN_LANGUAGE_RULES` (app/quality.py), zoals voor nl, de, es en fr (je/jij, du, tú, vous). Naleesbestand: `python i18n_review.py export de` schrijft `docs/i18n/review_de.csv` (bron, translator-comment, EN, NL, vertaling; manifest en spelregels bovenaan); na correcties door een moedertaalspreker zet `python i18n_review.py apply de` ze terug in de .po en .mo (met controle op placeholders en streepjes). Exporteer opnieuw na elke wijziging in de catalogus (tests/test_i18n_review.py bewaakt dat het bestand actueel is); zet `reviewed` op `True` als de taal helemaal is nagelezen.

### Inhoud in meerdere talen

De opzet van de machinevertaling (besluiten, label, stemblok, lijsten, achtergrondtaak en vangnet) staat in `docs/i18n/machinevertaling.md`; lees die bij elke bouwstap die inhoud toont of vertaalt.

Van elke tekst van gebruikers en de AI (bijdrage, feitkern, gesprekstitel en eyebrow, AI-uitleg) is er één origineel, in de brontaal van dat object (`Post.source_lang`, `Thread.source_lang`, `QualityAward.post_lang`). Vertalingen staan als cache per taal in de tabel `content_translations` en lopen alleen via `app/content_i18n.py` (`text_for`, `display`, `reader_view`, `translate`, `set_translation`, `invalidate`, `set_label`). Na een bewerking van het origineel: `invalidate`. Verwijder je objecten in bulk (een query-delete, die de ORM-listener overslaat), ruim dan ook hun vertalingen op met `content_i18n.delete_for(<tabel>, ids)`. Vertalingen waarvan het object niet meer bestaat (weesvertalingen) haalt `content_i18n.remove_orphans` bij elke start weg (idempotent, een INFO-regel per objecttype); `/admin/vertalingen` toont hoeveel er nu zijn, en dat hoort 0 te zijn. Een vaste label (zoals de eyebrow "Issue") gaat met `set_label` in elke ingeschakelde taal mee. De oude kolommen per taal (`body_nl`, `title_prefix_en` enzovoort) en `posts.author_badge` bestaan niet meer: bij het opstarten zet `content_migration.migrate` wat er nog in staat over en haalt `content_migration.drop_old_columns` ze daarna weg (idempotent; tests/test_i18n_safety.py bewaakt dat ze niet in de modellen, de database of de code terugkomen). Wat meteen vertaald wordt en wat op verzoek (fase 2): vraagstukken (titel, inleiding, en de titel van hun gesprek), voorstellen (stelling, toelichting), elke versie van een voorstel, de vervolg-updates en feiten (titel en tekst, niet de bron) worden meteen naar alle ingeschakelde talen vertaald (`app/content_jobs.py`): de domeinfunctie roept na aanmaken of bewerken `content_jobs.schedule(obj)` aan (na een bewerking eerst `invalidate`); na de commit start een achtergrondthread met een eigen app context en sessie. De uurtik (`/intern/tik`) is het vangnet (`content_jobs.backfill`, maximaal 200 velden per tik); de achterstand per soort en taal staat op `/admin/vertalingen`, met een knop die hem in één run op de achtergrond wegwerkt. Een vertaling van precies dezelfde tekst wordt hergebruikt. Een pagina roept de AI nooit aan: ontbreekt de vertaling nog, dan het origineel met "Vertaling volgt". Gewone bijdragen, reacties en de toelichting bij het betwisten van een feit worden op verzoek vertaald (de vertaalknop, "altijd vertalen"). In de tests staan de achtergrondstart en het vangnet uit (tests/conftest.py); tests/test_content_jobs.py draait ze zelf met een nep-client.

Het label: elke tekst die in een machinevertaling getoond kan worden, gaat door de macro `mt` in `app/templates/_mt.html` ("Machinaal vertaald uit het <taal> · Origineel tonen", wisselt naar het origineel, beide teksten staan in de pagina; script `_mt_script.html` in base.html). Een feit gebruikt `mt_fact` (titel en tekst samen achter één label). Lijsten gebruiken `mt_inline` (de vertaling, anders het origineel met een taalcode) en laden de vertalingen vooraf met `content_i18n.preload`. Handgeschreven vertalingen en de eigen taal van de lezer krijgen geen label. Bij het stemmen is het origineel de officiële tekst (het stemblok zegt dat).

De landenlijst gebruikt ISO-codes. Elk land heeft in `names` een eigen naam per taal (in nl en en elk land, zodat een Babel-update niets stil verandert); Babel is alleen de terugval voor een taal zonder eigen naam. De politiek gevoelige gebieden (PS, HK, TW, XK, met `"sensitive": true`) hebben in elke ingeschakelde taal een eigen naam en vallen nooit terug op Babel.
