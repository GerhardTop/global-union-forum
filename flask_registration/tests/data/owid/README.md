# Testdata Our World in Data

Vaste voorbeeldbestanden voor tests/test_owid_source.py; de tests gaan nooit het netwerk op.

## military-spending-as-a-share-of-gdp-sipri

- Herkomst: Our World in Data, grafiek "Military spending as a share of GDP" (https://ourworldindata.org/grapher/military-spending-as-a-share-of-gdp-sipri), licentie CC BY 4.0.
- Citation (chart.citation in de metadata): Stockholm International Peace Research Institute (2026). De kolom noemt als bron de SIPRI Military Expenditure Database, with minor processing by Our World in Data.
- Opgehaald op 25 november 2026 met `?csvType=full&useColumnShortNames=true` en `.metadata.json`. De slug `military-expenditure-share-gdp` verwijst (HTTP 301) door naar deze.
- `military-spending-as-a-share-of-gdp-sipri.metadata.json`: precies zoals ontvangen.
- `military-spending-as-a-share-of-gdp-sipri.csv`: ingekort, met dezelfde kolommen (`entity,code,year,share_gdp,owid_region`): Nederland, Duitsland, Frankrijk, Polen, Spanje en de Verenigde Staten, plus alle entiteiten met een `OWID_`-code (in deze grafiek alleen Kosovo, `OWID_KOS`), jaren 2010 tot en met 2025. Deze grafiek bevat geen regio's of gemiddelden (geen World, geen EU-27) en geen entiteit met een lege code.

## Handgemaakt

- `two-columns.csv`: twee datakolommen (moet 'unsupported' geven).
- `daily.csv`: `day` in plaats van `year` (moet 'unsupported' geven).
- `regions.csv`: gemiddelden zoals andere OWID-grafieken ze leveren (`World` met `OWID_WRL`, `European Union (27)` met `OWID_EU27`), een regio met een lege code, en lege en niet-numerieke cellen. Verzonnen getallen.
