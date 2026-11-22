# CLAUDE.md

## Vraagstuk-model

`docs/vraagstuk-ontwerp.md` is de canonieke bouw-specificatie voor het vraagstuk-model (Issue) en is bij elke bouwstap leidend.

## Communicatie

Wanneer een taak vereist dat de gebruiker zelf iets buiten de chat doet (een wachtwoord of credential aanleveren, een commando met de hand draaien, of iets uitvoeren in een externe omgeving zoals de Neon- of Render-console), zet dat als allereerste zin van je antwoord, duidelijk zichtbaar. De gebruiker leest niet altijd het volledige antwoord.

## Werkregels

Wijzig geen code terwijl de volledige suite draait. Commit alleen als de laatste volledige run op precies de te committen code groen is; bij een rode run eerst de oorzaak vinden, ook als die buiten de wijziging lijkt te liggen.

## Ontwerp

Afgeronde design-artboards zijn leidend voor de vormgeving. De artboards staan als statische referentie-HTML in `docs/design/` (bijvoorbeeld `docs/design/gebruiker.html`), met `docs/design/README.md` als overzicht (artboard per scherm, tokens, componenten). Bouw elk UI-scherm visueel conform het betreffende artboardbestand in `docs/design/`; hergebruik niet standaard de bestaande code-markup. De schermopdracht noemt het artboardbestand waar naartoe gebouwd wordt.

## Vertalingen (i18n)

Alle tekst voor de gebruiker gaat via gettext; geen `{% if lang == 'nl' %}`-knips meer (tests/test_i18n_safety.py bewaakt dat). De msgid is Engels, de Nederlandse tekst staat in de catalogus.

Werkwijze na een tekstwijziging, vanuit `flask_registration/`:

1. `pybabel extract -F babel.cfg -k lazy_gettext -k lazy_pgettext:1c,2 -c TRANSLATORS: -o messages.pot app` (zonder `-k lazy_gettext` en `-c TRANSLATORS:` mist extract de labels in Python en de vertalerscommentaren).
2. `pybabel update -i messages.pot -d app/translations -N --no-wrap` is weer veilig: de catalogi bevatten geen ongebruikte msgids meer, dus er ontstaat niets obsolete. Altijd met `-N` (geen fuzzy matching).
3. Vul de nieuwe NL-teksten in (en de EN-msgstr gelijk aan de msgid); geen lege of fuzzy NL-items.
4. `pybabel compile -d app/translations` en commit de .mo mee (Render compileert niet bij de build).

Een vertaling die gelijk is aan de msgid mag alleen als hij op de toegestane lijst van die taal staat (`SAME_AS_MSGID` in tests/test_i18n_safety.py; voor nl is dat `SAME_IN_DUTCH`), met reden. Geen lange of korte streepjes in msgids of vertalingen.

### Het taalregister

`config.py` is de enige plek met talen: `LANGUAGES` (alle talen die het platform kent: code, naam in de eigen taal, Engelse naam, schrijfrichting, en `reviewed`: of een moedertaalspreker de vertaling heeft nagelezen), `ENABLED_LANGUAGES` (de talen die aanstaan), `DEFAULT_LANGUAGE` (nl) en `SOURCE_LANGUAGE` (en, de taal van de msgids). Code en templates vragen het register via `app/i18n.py` (`enabled_languages()`, `is_enabled()`, `normalize()`, `language_name()`, `text_direction()`, `is_reviewed()`, `auto_translate_default()`); geen eigen lijsten als `('nl', 'en')`. De taalkiezer in header, footer en profiel is een lus over de ingeschakelde talen. Automatisch vertalen staat standaard aan voor iedereen wiens taal niet de standaardtaal is.

Een taal aanzetten (voorbeeld de), vanuit `flask_registration/`:

1. Staat de taal niet in `LANGUAGES`, voeg hem toe (code, naam in de eigen taal, Engelse naam voor de AI-prompts, `ltr` of `rtl`, `reviewed`). Maak eerst een woordenlijst `docs/i18n/glossary_<code>.md` met de kernbegrippen en stijlregels (voorbeeld: `glossary_de.md`).
2. Extract zoals hierboven, dan `pybabel init -i messages.pot -d app/translations -l de --no-wrap`.
3. Vertaal alles in `app/translations/de/LC_MESSAGES/messages.po`: geen lege of fuzzy items. Teksten die gelijk mogen blijven aan de msgid krijgen een eigen lijst in `SAME_AS_MSGID['de']`, met reden.
   Landnamen in `app/data/landenlijst_data.json`: verplicht een eigen naam onder `names` in de nieuwe taal voor de vier politiek gevoelige gebieden (`"sensitive": true`: PS, HK, TW, XK); zij vallen nooit terug op Babel (tests/test_landenlijst_i18n.py faalt anders). Loop daarna eenmalig de Babel-namen van de overige landen in de nieuwe taal na; waar die informeel of fout zijn, neem een eigen naam op.
4. `pybabel compile -d app/translations` en commit de .po en .mo.
5. Zet `"de"` in `ENABLED_LANGUAGES`. De catalogustests gelden dan vanzelf ook voor de; draai de volledige suite.

Een machinevertaling staat in het register met `"reviewed": False`; de footer zegt dan in die taal dat de pagina automatisch vertaald is. Geef de AI-taalregel voor de kwaliteitsprompt zo nodig een eigen regel in `_OWN_LANGUAGE_RULES` (app/quality.py), zoals voor nl, de, es en fr (je/jij, du, tú, vous). Naleesbestand: `python i18n_review.py export de` schrijft `docs/i18n/review_de.csv` (bron, translator-comment, EN, NL, vertaling; manifest en spelregels bovenaan); na correcties door een moedertaalspreker zet `python i18n_review.py apply de` ze terug in de .po en .mo (met controle op placeholders en streepjes). Exporteer opnieuw na elke wijziging in de catalogus (tests/test_i18n_review.py bewaakt dat het bestand actueel is); zet `reviewed` op `True` als de taal helemaal is nagelezen.

### Inhoud in meerdere talen

Van elke tekst van gebruikers en de AI (bijdrage, feitkern, gesprekstitel en eyebrow, AI-uitleg) is er één origineel, in de brontaal van dat object (`Post.source_lang`, `Thread.source_lang`, `QualityAward.post_lang`). Vertalingen staan als cache per taal in de tabel `content_translations` en lopen alleen via `app/content_i18n.py` (`text_for`, `display`, `reader_view`, `translate`, `set_translation`, `invalidate`, `set_label`). Na een bewerking van het origineel: `invalidate`. Een vaste label (zoals de eyebrow "Issue") gaat met `set_label` in elke ingeschakelde taal mee. De oude kolommen per taal (`body_nl`, `title_prefix_en` enzovoort) staan nog in de database maar de code gebruikt ze niet; alleen `app/content_migration.py` leest ze (tests/test_i18n_safety.py bewaakt dat). De landenlijst gebruikt ISO-codes. Elk land heeft in `names` een eigen naam per taal (in nl en en elk land, zodat een Babel-update niets stil verandert); Babel is alleen de terugval voor een taal zonder eigen naam. De politiek gevoelige gebieden (PS, HK, TW, XK, met `"sensitive": true`) hebben in elke ingeschakelde taal een eigen naam en vallen nooit terug op Babel.
