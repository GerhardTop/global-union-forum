# Ontwerp moderatie (moderator-dashboard)

Dit is het ontwerp van de moderatie-schermen voor Global Union, als
zelfstandige HTML-snapshots. Open een `.html` in elke browser; er is geen
bouwstap of Design-omgeving voor nodig. De bewerkbare, levende versie staat op
de Claude Design-canvas:

- Live canvas: https://claude.ai/artifact/MjFtAPXQAndEBM6rYLsrcE

De snapshots zijn een momentopname (8 nov 2026). Leidend is de code zodra een
fase gebouwd is; dit ontwerp beschrijft de richting.

## De schermen

| Bestand | Scherm |
|---|---|
| `overzicht.html` | Het moderator-overzicht: kengetallen, één bak met de stromen als filter, Recente vraagstukken, Fasebeheer |
| `melding-beoordelen.html` | Eén melding beoordelen: de bijdrage in context, de meldingen met redenen, en de beslissing (niets doen, verplaatsen, verbergen) |
| `waar-verwijderen-begint.html` | Waar verwijderen begint: het Beheer-menu op de vraagstuk-pagina |
| `vraagstuk-verwijderen.html` | Bevestiging voor het definitief verwijderen van een heel vraagstuk of voorstel (typ de titel) |
| `gebruiker-bekijken.html` | Een gebruiker inzien: dossier, handhaving-historie, bijdragen met filter, en de maatregelen (waarschuwen, schorsen, verwijderen) |

## Uitgangspunten

- **Twee sporen.** Alles wat aandacht nodig heeft (de gedeelde wachtrij) staat
  los van de vraagstukken waar de moderator zelf aan werkte ("Recente
  vraagstukken"). Een item is één beslissing: oppakken is meteen afhandelen.
- **Eén meldknop.** Bij elke bijdrage en elk feit zit dezelfde "Melden"-knop
  met een verplicht redenveld. Eén melding per persoon, niet op de eigen
  bijdrage. De meldingen zijn alleen voor moderators; per bijdrage toont het
  dashboard hoeveel meldingen er zijn.
- **Drie stromen, één bak.** Meldingen (door mensen), Door de AI gemarkeerd
  (de twijfelgevallen, met "zonder AI-oordeel"), en Bezwaren (beroepen van
  auteurs). Je filtert bovenin de bak, zodat niets uit beeld schuift.
- **Verbergen en verplaatsen** voor losse bijdragen en feiten (omkeerbaar);
  **hard verwijderen** alleen voor een heel vraagstuk of voorstel, achter een
  bevestiging.
- **Opvoedende maatregelen.** Waarschuwen (geel), tijdelijk schorsen (oranje),
  account verwijderen (rood). Begin bij bijsturen; schorsen en verwijderen zijn
  zware stappen. Bij een naam staan "meldingen gedaan" en "ontvangen (waarvan
  gegrond)" als context, geen oordeel.

## Bouwfases

1. Meldknop en meldmodel (fundament).
2. Het moderator-overzicht.
3. Verwijderen (Beheer-menu plus bevestiging).
4. Gebruikersscherm en maatregelen (waarschuwen en schorsen zijn nieuw).
5. Later: notificatiesysteem (in-app inbox, voortbouwend op het Digest-model).
