# Toegankelijkheid

Status: besloten, 28 november 2026. Norm: WCAG 2.2 niveau AA. Dit bestand is leidend voor kleuren, focus en toegankelijkheid op de hele site, en voor elk nieuw artboard en elke bouwstap met schermen.

## Kleuren (voorstel A)
- Hoofdknop: achtergrond #0077cc met witte tekst (4,7 op 1), hover #005ea3. Token --gu-action en --gu-action-hover.
- Links: #005ea3 (6,0 tot 6,7 op 1 op wit en op de grijze achtergrond), hover #004a80. Token --gu-link en --gu-link-hover.
- Hulpzinnen, de kleine regel boven titels en andere secundaire tekst: #5e6877 (5,1 tot 5,6 op 1). Token --gu-fg-3. Het grijs #aaaaaa (--gu-payoff) alleen voor de payoff bij het logo, nooit voor tekst in de interface.
- Randen van invoervelden, keuzelijsten, tekstvakken en witte knoppen: #8a95a3 (3,0 op 1). Token --gu-border-field. Randen van kaarten en scheidingslijnen blijven --gu-border.
- Het lichte globeblauw #3ab0f0 (--gu-globe) alleen als accent: logo, illustraties, decoratie. Nooit als achtergrond van tekst en nooit als tekstkleur.

## Focus
- Alleen bij toetsenbordgebruik (:focus-visible).
- Velden, witte knoppen en links: één doorlopende lijn van 3 pixels in #0077cc direct om het element (rand plus box-shadow 0 0 0 2px), zonder ruimte ertussen.
- Blauwe knoppen: dezelfde lijn met 2 pixels ruimte (outline 3px, outline-offset 2px).
- Altijd ook een doorzichtige outline, zodat de lijn zichtbaar blijft in de modus voor hoog contrast (forced-colors).

## Links
- Links in lopende tekst altijd onderstreept: dunne lijn (1 pixel), 3 pixels onder de letters, bij hover 2 pixels en #004a80.
- Knoppen, menu-items, filters en titels in lijsten niet onderstreept.

## Overig
- Betekenis nooit alleen met kleur: altijd ook een woord of icoon.
- Tekst minimaal 12 pixels.
- Geen animaties of overgangen bij prefers-reduced-motion: reduce.
- Elke pagina een eigen titel in het tabblad (nooit de standaardtitel), één h1, koppen in volgorde.
- Een link "Naar de inhoud" als eerste element, zichtbaar bij focus, naar de main.
- Velden met een label; een fout gekoppeld aan het veld (aria-invalid, aria-describedby); bij een fout de focus op het eerste veld met een fout.
- Knoppen met alleen een icoon hebben een naam (aria-label).
- Meldingen: een schermlezer leest ze voor; ze blijven minstens 6 seconden staan, pauzeren bij hover en focus, en hebben een sluitknop. Foutmeldingen sluiten niet vanzelf.

## Controle
- tests/test_contrast.py rekent de kleurparen uit dit bestand na.
- tests/test_accessibility.py controleert de gerenderde pagina's op de regels hierboven die automatisch te controleren zijn.
- Niet automatisch te controleren: het hele pad één keer met alleen het toetsenbord en VoiceOver (Cmd+F5) bij een nieuw scherm.
- Nog niet in deze stap: de kleuren in de grafieken (charts.css); eigen controle later.
