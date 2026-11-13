# Design-referentie

Als er een afgerond design-artboard is, is dat leidend voor de vormgeving van dat scherm. Bouw het scherm naar het artboard; hergebruik niet standaard de bestaande markup. De forum-design-tokens staan hieronder (ze komen uit de .gu-stijl van de artboards), niet uit een generieke design-systeem-referentie.

## Moderatorschermen en hun artboard
De artboards staan als statische referentie-HTML in deze map (`docs/design/`). Bouw elk scherm visueel conform het betreffende bestand.

| Scherm | Route | Artboardbestand |
|--------|-------|----------|
| Moderator overzicht | /moderator | docs/design/main.html |
| Melding beoordelen | /moderator/melding/... | docs/design/beoordelen.html |
| Beheer-menu op het vraagstuk (ingang van verwijderen en archiveren) | /vraagstuk/<id> (cockpit, menu "Beheer") | docs/design/toegang.html |
| Verwijderen | /vraagstuk/<id>/verwijderen (verwijderbevestiging) | docs/design/verwijderen.html |
| Gebruiker (gepland) | (gebruikersmoderatie) | docs/design/gebruiker.html |
| Meldingen instellen | /profiel/meldingen | docs/design/notificaties.html |
| Voorstel bekijken en aanpassen | /vraagstuk/<id>/voorstel/<pid> | docs/design/voorstel.html |
| Voorstellen vergroot (in-pagina) | /vraagstuk/<id>/voorstel/<pid> (vergroot) en /vraagstuk/<id> (vergroot) | docs/design/voorstel-vergroot.html |
| Vraagstuk beheren (moderatie) | /moderator/vraagstuk/<id>/beheer | docs/design/beheer.html |

Main.html toont nu ook de ingang naar het vraagstukbeheer: in de rechterkolom "Vraagstukken beheren" staat per vraagstuk een Beheer-link naar docs/design/beheer.html. Toegang.html is niet het rollenbeheer, maar de ingang van de verwijder-flow: het Beheer-menu op het vraagstuk (fasen beheren, archiveren, verwijderen). Gebruiker.html (gebruikersmoderatie: waarschuwingen, schorsingen, account verwijderen) is een geplande functie en nog niet gebouwd.

De bestanden zijn statisch, alleen om naar te kijken en naartoe te bouwen; ze zijn geen onderdeel van de app. De duur-knoppen in gebruiker.html staan op 7 en 30 dagen (de gekozen schorsingsduur).

## Design-tokens (uit de artboards)
globe #3ab0f0 · globe-deep #1b74b8 · europe #0077cc · ink #141b22 · paper #ffffff · night #0f1923
americas #d8232a · africa #e09400 · oceania/groen #108a4e
grijs: n50 #f5f7f9 · n100 #eef1f5 · n200/border #e4e9ef · n300 #d3dae2 · n400 #a7b1bd · n500 #6c7683 · n600 #525c69 · n700 #3a434e
font: Inter. schaduw: 0 1px 2px rgba(15,25,35,.04), 0 6px 20px rgba(15,25,35,.05)
In de repo heten deze tokens --gu-* (colors_and_type.css); ze komen grotendeels overeen. Gebruik de --gu-* tokens, en val terug op de exacte waarde hierboven waar een accent moet kloppen (globe-deep, de groene "behandeld"-tegels, de leeftijd-pills).

## Componenten (uit de artboards)
- card: #fff, 1px var(--border), radius 14px, schaduw.
- pill: inline-flex, 12px/500, padding 2px 9px, radius 999px, bg n100, kleur n600, border. count-pill: bg #eaf2fb, border #c5dcf4, kleur #1b5e9a, bold.
- leeftijd-pill: hot = bg #fcecec/border #f0c9cb/kleur #a3282c; warm = bg #fff3e0/border #f0d9b0/kleur #8a5a12; neutraal = n100.
- btn: 13px/500, padding 8px 14px, radius 8px. btn--primary = bg globe-deep, wit. btn--sm = kleiner.
- kpi: padding 14px 16px; num 25px/600; label 12px n600. "Behandeld"-tegels: bg #f3f8f4, border #d6e8dc, num in oceania-groen.
- seg (tabbalk): inline-flex, bg n100, border, radius 9px, padding 3px; knop 13px/500 radius 7px, kleur n600; actief = bg #fff, kleur ink, schaduw; telling .c 11px n500, actief in globe-deep.
- mrow (lijstregel): flex, align-start, gap 12px, padding 14px 16px; scheidingslijn n100 tussen regels.

## Overzicht (Main.dc.html), lay-out
- Kop: eyebrow "Beheer" + H1 "Moderator".
- Eén rij van 6 kengetallen (grid 6x1fr): Meldingen, Bezwaren, Door de AI, Zonder AI-oordeel, Behandeld totaal (groene tegel), Behandeld door jou (groene tegel).
- Twee kolommen (1fr 400px). Links: één card-bak met in de kop de seg-tabbalk (Alle, Meldingen, Bezwaren, Door de AI, Zonder AI-oordeel) + een regel "N meldingen, door deelnemers" + zoekveld + sorteren; daaronder compacte mrow-regels (citaat, vraagstuk-pill met gekleurde pijl, auteur, reden met "toon"-link, rechts een count-pill, een leeftijd-pill en een knop "Beoordelen"); footer "Toon alle N meldingen". Rechts (aside): twee kaartjes (Oudste wachttijd in rood, Lopende vraagstukken), "Recente vraagstukken" (+pill "waar jij aan werkte"), en "Fasebeheer" (+pill "N klaar") met per vraagstuk "fase X naar Y" en knop "Volgende fase".

## Beoordelen (Beoordelen.dc.html), lay-out
- Kruimelpad (Moderator / vraagstuk / Gemelde bijdrage), eyebrow "Melding beoordelen", H1 "Bijdrage in <vraagstuk>", context-pills.
- Twee kolommen (1fr 420px). Links: card "De bijdrage in het gesprek" met vraagstuk-contextbalk, het bovenliggende bericht gedimd, de gemelde bijdrage uitgelicht (linkerrand in africa-kleur, lichte achtergrond), de reactie eronder gedimd; daaronder card "Meldingen bij deze bijdrage" met per melding de melder, diens cijfers en de reden. Rechts (aside): card "Beslissing" met drie knoppen (Niets doen / Verplaatsen naar achtergrond / Verbergen), een toelichtingsveld voor het logboek en uitleg; daaronder een tip-card.
