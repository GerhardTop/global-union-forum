# CLAUDE.md

## Vraagstuk-model

`docs/vraagstuk-ontwerp.md` is de canonieke bouw-specificatie voor het vraagstuk-model (Issue) en is bij elke bouwstap leidend.

## Communicatie

Wanneer een taak vereist dat de gebruiker zelf iets buiten de chat doet (een wachtwoord of credential aanleveren, een commando met de hand draaien, of iets uitvoeren in een externe omgeving zoals de Neon- of Render-console), zet dat als allereerste zin van je antwoord, duidelijk zichtbaar. De gebruiker leest niet altijd het volledige antwoord.

## Ontwerp

Afgeronde design-artboards zijn leidend voor de vormgeving. De artboards staan als statische referentie-HTML in `docs/design/` (bijvoorbeeld `docs/design/gebruiker.html`), met `docs/design/README.md` als overzicht (artboard per scherm, tokens, componenten). Bouw elk UI-scherm visueel conform het betreffende artboardbestand in `docs/design/`; hergebruik niet standaard de bestaande code-markup. De schermopdracht noemt het artboardbestand waar naartoe gebouwd wordt.
