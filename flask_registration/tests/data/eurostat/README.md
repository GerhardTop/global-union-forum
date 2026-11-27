# Eurostat test data (background figures, step 3a)

Fetched once, live, on 26 November 2026, for tests/test_eurostat_source.py.
The suite never goes on the network.

- `toc.txt`: a piece of the table of contents
  (https://ec.europa.eu/eurostat/api/dissemination/catalogue/toc/txt?lang=en):
  the header and 35 lines with folders, a dataset and main tables (type
  "table"), among them a table in two folders (tqoe3b4), tables with old data
  (tai10, tqoe3b4) and one without a data start and end (ten00001). The full
  table of contents had 12401 lines: 1939 folders, 8918 datasets and 1543
  table lines for 947 main tables.
- `<code>.json`: JSON-stat 2.0 from the Statistics API
  (https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/<code>?format=JSON&lang=EN),
  from ten years back (sinceTimePeriod 2016, 2016-Q1 for the quarterly table),
  only NL, DE, FR, PL, ES, EL and EU27_2020; of extension.annotation only the
  types CREATED and DISSEMINATION_OBJECT_TYPE are kept (the rest is long and
  unused).
  - `tespm140.json`: Housing cost overburden rate (one value per dimension;
    flags b and e).
  - `tps00203.json`: Unemployment rate, with three units (PC_ACT, PC_POP,
    THS_PER): needs a choice.
  - `tps00203_pc_act.json`: the same with unit=PC_ACT (flags b and d).
  - `ei_hppi_q.json`: House price index, quarterly only (freq Q: unsupported
    in this step). Eurostat has no annual main table for house prices.
  - `tessi162.json` (fetched 27 November 2026, step 3c): Housing cost
    overburden rate by income quintile, QU1 to QU5 and no total (no_total).

Step 3c added five lines of the table of contents of 27 November 2026 at the
end of `toc.txt`: tessi162, tipsna50 and three waste tables "per capita"
(cei_pc031, cei_pc040, cei_pc050), for the search per term.

Licence: Eurostat data, CC BY 4.0, source: Eurostat
(https://ec.europa.eu/eurostat/about-us/policies/copyright). Eurostat is not
responsible for any processing of the data.
