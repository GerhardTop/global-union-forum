# CLAUDE.md

## Vraagstuk-model

`docs/vraagstuk-ontwerp.md` is de canonieke bouw-specificatie voor het vraagstuk-model (Issue) en is bij elke bouwstap leidend.

## Communicatie

Wanneer een taak vereist dat de gebruiker zelf iets buiten de chat doet (een wachtwoord of credential aanleveren, een commando met de hand draaien, of iets uitvoeren in een externe omgeving zoals de Neon- of Render-console), zet dat als allereerste zin van je antwoord, duidelijk zichtbaar. De gebruiker leest niet altijd het volledige antwoord.

## Ontwerp

Afgeronde design-artboards zijn leidend voor de vormgeving. De artboards staan als statische referentie-HTML in `docs/design/` (bijvoorbeeld `docs/design/gebruiker.html`), met `docs/design/README.md` als overzicht (artboard per scherm, tokens, componenten). Bouw elk UI-scherm visueel conform het betreffende artboardbestand in `docs/design/`; hergebruik niet standaard de bestaande code-markup. De schermopdracht noemt het artboardbestand waar naartoe gebouwd wordt.

## Vertalingen (i18n)

Alle tekst voor de gebruiker gaat via gettext; geen `{% if lang == 'nl' %}`-knips meer (tests/test_i18n_safety.py bewaakt dat). De msgid is Engels, de Nederlandse tekst staat in de catalogus.

Werkwijze na een tekstwijziging, vanuit `flask_registration/`:

1. `pybabel extract -F babel.cfg -k lazy_gettext -k lazy_pgettext:1c,2 -c TRANSLATORS: -o messages.pot app` (zonder `-k lazy_gettext` en `-c TRANSLATORS:` mist extract de labels in Python en de vertalerscommentaren).
2. `pybabel update -i messages.pot -d app/translations -N --no-wrap` is weer veilig: de catalogi bevatten geen ongebruikte msgids meer, dus er ontstaat niets obsolete. Altijd met `-N` (geen fuzzy matching).
3. Vul de nieuwe NL-teksten in (en de EN-msgstr gelijk aan de msgid); geen lege of fuzzy NL-items.
4. `pybabel compile -d app/translations` en commit de .mo mee (Render compileert niet bij de build).

Een NL-vertaling die gelijk is aan de msgid mag alleen als hij op `SAME_IN_DUTCH` in tests/test_i18n_safety.py staat, met reden. Geen lange of korte streepjes in msgids of vertalingen.
