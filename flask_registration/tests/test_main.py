"""
Test-suite voor main.py: /manifest/landen (landenlijst-pagina).
Dekt: pagina laadt, toont 183 landen, i18n-tekst per taal (incl. NL/EN
landnamen), losse DI/CPI/HRI-kolommen met 3-standen-kleurpuntjes, en dat er
geen intro-tekst en geen (i)-info-icoon/uitklap-mechanisme meer is voor de
drie uitzonderingsgevallen (Taiwan/Israel/VS) — die toelichting staat alleen
nog op /manifest.
"""


class TestLandenlijst:
    def test_landenlijst_loads(self, client):
        response = client.get("/manifest/landen")
        assert response.status_code == 200

    def test_landenlijst_shows_all_countries(self, client):
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        assert html.count('gu-landen-table__country') == 183

    def test_landenlijst_dutch_text(self, client):
        client.get("/lang/nl")
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        assert "Landenoverzicht" in html

    def test_landenlijst_english_text(self, client):
        client.get("/lang/en")
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        assert "Country overview" in html

    def test_intro_text_removed_from_landenlijst(self, client):
        # De intro-tekst (voorheen via _landen_intro.html) staat alleen nog
        # op /manifest, niet meer op de lijst-pagina zelf.
        client.get("/lang/nl")
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        assert "Toetreding is gekoppeld aan drie bestaande" not in html
        assert "gu-landen__intro" not in html

        client.get("/lang/en")
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        assert "Accession is tied to three existing" not in html

    def test_uitnodigen_badge_removed_entirely(self, client):
        # De Uitnodigen-badge is verwijderd uit zowel desktop als mobiel
        # (functioneel al af te leiden uit Niveau GU).
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        assert 'gu-invite-badge' not in html
        assert 'Uitnodigen<' not in html

    def test_mobile_cards_present_for_all_countries(self, client):
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        assert html.count('class="gu-landen-card"') == 183
        assert html.count('gu-landen-card__summary') == 183

    def test_mobile_card_details_show_scores_not_level_again(self, client):
        client.get("/lang/en")
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        idx = html.find('class="gu-landen-card"')
        assert idx != -1
        card_end = html.find('</details>', idx)
        assert card_end != -1
        card = html[idx:card_end]
        details_start = card.find('gu-landen-card__details')
        summary_part = card[:details_start]
        details_part = card[details_start:]
        # Niveau-badge hoort alleen in de summary, niet herhaald in de
        # uitgeklapte details.
        assert 'gu-level-badge' in summary_part
        assert 'gu-level-badge' not in details_part
        # DI/CPI/HRI horen alleen in de details, niet in de summary.
        assert '>DI<' in details_part and '>DI<' not in summary_part
        assert '>CPI<' in details_part and '>CPI<' not in summary_part
        assert '>HRI<' in details_part and '>HRI<' not in summary_part

    def test_old_threshold_text_removed_from_landenlijst(self, client):
        # Wijziging 1: de losse drempeltekst boven de tabel is weg van
        # landenlijst.html (verhuisd naar /manifest, zie TestManifestLandenSection).
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        assert 'gu-landen__thresholds' not in html
        assert 'DI ≥ 6,0' not in html
        assert 'DI ≥ 6.0' not in html

    def test_legend_table_present_dutch(self, client):
        # Wijziging 7: compacte legenda-tabel vervangt de weggehaalde tekst.
        client.get("/lang/nl")
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        idx = html.find('gu-landen-legend')
        assert idx != -1
        legend_end = html.find('gu-landen-table-wrap')
        legend = html[idx:legend_end]
        assert 'gu-level-badge--above">Boven minimum<' in legend
        assert 'gu-level-badge--meets">Op niveau<' in legend
        assert '≥ 6,0' in legend
        assert '≥ 40' in legend
        assert '≥ 0,70' in legend
        assert '≥ 8,0' in legend
        assert '≥ 70' in legend
        assert '≥ 0,75' in legend

    def test_legend_table_present_english(self, client):
        client.get("/lang/en")
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        idx = html.find('gu-landen-legend')
        assert idx != -1
        legend_end = html.find('gu-landen-table-wrap')
        legend = html[idx:legend_end]
        assert 'gu-level-badge--above">Above minimum<' in legend
        assert 'gu-level-badge--meets">Meets standard<' in legend
        assert '≥ 6.0' in legend
        assert '≥ 0.70' in legend
        assert '≥ 8.0' in legend
        assert '≥ 0.75' in legend

    def test_three_separate_index_columns_desktop(self, client):
        # Wijziging A (eerdere ronde): geen gegroepeerde 'Index'-kolom meer,
        # maar drie losse, volledig uitgeschreven kolommen. Vanaf
        # gu-landen-table-wrap zoeken (niet vanaf het begin van de pagina):
        # de legenda-tabel (wijziging 7) heeft óók een <thead>/</thead>, dus
        # een onbeperkte find() zou die eerst pakken i.p.v. de hoofdtabel.
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        wrap_start = html.find('class="gu-landen-table-wrap"')
        assert wrap_start != -1
        thead_end = html.find('</thead>', wrap_start)
        thead = html[wrap_start:thead_end]
        assert 'data-sort-key="di">Democracy Index' in thead
        assert 'data-sort-key="cpi">Corruption Perceptions Index' in thead
        assert 'data-sort-key="hri">Human Rights Index' in thead
        assert '>Index<' not in thead

    def test_niveau_column_header_renamed(self, client):
        # Wijziging 4: kolomkop wordt voluit "Global Union niveau"/"level";
        # de badge-tekst in de cellen zelf (bijv. "Op niveau") blijft gelijk.
        client.get("/lang/nl")
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        wrap_start = html.find('class="gu-landen-table-wrap"')
        thead = html[wrap_start:html.find('</thead>', wrap_start)]
        assert 'Global Union niveau' in thead
        assert 'Niveau GU' not in thead

        client.get("/lang/en")
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        wrap_start = html.find('class="gu-landen-table-wrap"')
        thead = html[wrap_start:html.find('</thead>', wrap_start)]
        assert 'Global Union level' in thead
        assert 'GU level' not in thead

    def test_index_columns_centered(self, client):
        # Wijziging 5: alleen DI/CPI/HRI-kolommen (kop én waarde) gecentreerd.
        client.get("/lang/en")
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        wrap_start = html.find('class="gu-landen-table-wrap"')
        thead = html[wrap_start:html.find('</thead>', wrap_start)]
        assert thead.count('gu-landen-table__th--center') == 3
        table_start = html.find('gu-landen-table__country')
        idx = html.find('Norway', table_start)
        row = html[idx:html.find('</tr>', idx)]
        assert row.count('gu-landen-table__td--center') == 3
        # De land-cel zelf krijgt geen center-klasse (blijft links uitgelijnd).
        country_cell_end = row.find('</td>')
        assert 'gu-landen-table__td--center' not in row[:country_cell_end]

    def test_table_has_colgroup_for_equal_column_widths(self, client):
        # Wijziging 6: kolombreedtes via <colgroup> i.p.v. content-afhankelijk
        # auto-sizen.
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        table_start = html.find('class="gu-landen-table"')
        colgroup_start = html.find('<colgroup>', table_start)
        colgroup_end = html.find('</colgroup>', table_start)
        assert colgroup_start != -1 and colgroup_end != -1
        colgroup = html[colgroup_start:colgroup_end]
        # '<col ' met spatie: '<colgroup>' zelf begint ook met de substring
        # '<col' en zou anders dubbel meetellen.
        assert colgroup.count('<col ') == 5

    def test_sort_attributes_present_for_norway(self, client):
        # Wijziging 3: elke cel draagt data-sort-value zodat de client-side
        # sort-JS kan sorteren zonder de server opnieuw te bevragen. Rij
        # vanaf de openende <tr> pakken: 'Norway' komt voor het eerst voor
        # binnen het data-sort-value-attribuut van de land-cel zelf, vóór
        # de zichtbare naam — zoeken vanaf de tekst 'Norway' zou dat
        # attribuut-prefix missen.
        client.get("/lang/en")
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        table_start = html.find('gu-landen-table__country')
        idx = html.find('Norway', table_start)
        assert idx != -1
        tr_start = html.rfind('<tr', 0, idx)
        row = html[tr_start:html.find('</tr>', idx)]
        assert 'data-sort-cell="land" data-sort-value="Norway"' in row
        assert 'data-sort-cell="di" data-sort-value="9.81"' in row
        assert 'data-sort-cell="cpi" data-sort-value="81"' in row
        assert 'data-sort-cell="hri" data-sort-value="0.947"' in row
        assert 'data-sort-cell="niveau" data-sort-value="3"' in row  # 'meets' = hoogste rang

    def test_sort_script_present(self, client):
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        assert 'data-sort-key' in html
        assert 'addEventListener(\'click\'' in html or "addEventListener('click'" in html

    def test_index_dots_in_own_columns_for_norway(self, client):
        # Norway: DI 9.81, CPI 81, HRI 0.947 — alle drie ruim boven de
        # 'Op niveau'-drempel, dus alle drie 'meets' (groen), elk los in zijn
        # eigen kolom (niet gegroepeerd).
        client.get("/lang/en")
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        table_start = html.find('gu-landen-table__country')
        idx = html.find('Norway', table_start)
        assert idx != -1
        row = html[idx:html.find('</tr>', idx)]
        assert 'gu-index-group' not in row
        assert row.count('class="gu-index-dot gu-index-dot--meets"') == 3

    def test_index_dots_mixed_states_for_taiwan_main_row(self, client):
        # Taiwan: DI 8.78 (Op niveau), CPI 68 (Boven minimum, niet Op
        # niveau: 40 <= 68 < 70), HRI 0.930 (Op niveau) -> 2x meets, 1x above,
        # zichtbaar in de hoofdrij zelf (niet pas na uitklappen).
        client.get("/lang/en")
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        table_start = html.find('gu-landen-table__country')
        idx = html.find('Taiwan', table_start)
        assert idx != -1
        row = html[idx:html.find('</tr>', idx)]
        assert row.count('class="gu-index-dot gu-index-dot--meets"') == 2
        assert row.count('class="gu-index-dot gu-index-dot--above"') == 1

    def test_index_dots_below_for_afghanistan_main_row(self, client):
        # Afghanistan (DI 0.25, CPI 16, HRI 0.041 — alles onder de drempel)
        # heeft geen remark en dus ook geen uitklaprij: de dots moeten in de
        # hoofdrij zelf zichtbaar zijn.
        client.get("/lang/en")
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        table_start = html.find('gu-landen-table__country')
        idx = html.find('Afghanistan', table_start)
        assert idx != -1
        row = html[idx:html.find('</tr>', idx)]
        assert row.count('class="gu-index-dot gu-index-dot--below"') == 3

    def test_mobile_card_summary_shows_index_group(self, client):
        # Mobiel blijft de gegroepeerde variant gebruiken (wijziging A is
        # desktop-only).
        client.get("/lang/en")
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        cards_start = html.find('class="gu-landen-cards"')
        idx = html.find('Norway', cards_start)
        assert idx != -1
        summary_end = html.find('</summary>', idx)
        summary = html[idx:summary_end]
        assert 'gu-index-group' in summary
        assert summary.count('class="gu-index-dot gu-index-dot--') == 3

    def test_desktop_no_toggle_or_info_icon_anywhere(self, client):
        # Het (i)-icoon en het bijbehorende uitklap-mechanisme (chevron,
        # klikbare rij, verborgen detailrij) zijn volledig verwijderd — ook
        # voor Taiwan/Israel/VS: de toelichting staat al op /manifest en zou
        # hier dubbelop zijn geweest. <tbody> vanaf gu-landen-table-wrap
        # zoeken: de legenda-tabel (wijziging 7) heeft óók een <tbody>.
        client.get("/lang/en")
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        wrap_start = html.find('class="gu-landen-table-wrap"')
        assert wrap_start != -1
        tbody = html[html.find('<tbody>', wrap_start):html.find('</tbody>', wrap_start)]
        assert 'gu-landen-table__info' not in tbody
        assert 'gu-landen-table__rowToggle' not in tbody
        assert 'gu-landen-table__row"' not in tbody
        assert 'gu-landen-table__detailRow' not in tbody
        assert 'gu-landen-remark' not in tbody
        # Ook expliciet voor de drie uitzonderingslanden zelf, niet alleen
        # 'ergens in de tbody'.
        table_start = html.find('gu-landen-table__country')
        for name in ("Taiwan", "Israel", "United States"):
            idx = html.find(name, table_start)
            assert idx != -1, name
            tr_start = html.rfind('<tr', 0, idx)
            row = html[tr_start:html.find('</tr>', idx)]
            assert 'gu-landen-table__info' not in row
            assert 'gu-landen-table__row"' not in row

    def test_mobile_no_info_icon_or_remark_for_exception_countries(self, client):
        # Zelfde verwijdering op mobiel: geen (i)-icoon en geen remark-tekst
        # meer in de kaart-details van Taiwan/Israel/VS.
        client.get("/lang/en")
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        cards_start = html.find('class="gu-landen-cards"')
        assert 'gu-landen-table__info' not in html[cards_start:]
        assert 'gu-landen-remark' not in html[cards_start:]
        idx_taiwan = html.find('Taiwan', cards_start)
        assert idx_taiwan != -1
        card_start = html.rfind('<details class="gu-landen-card">', 0, idx_taiwan)
        card_end = html.find('</details>', idx_taiwan)
        card = html[card_start:card_end]
        assert 'No UN recognition' not in card

    def test_exception_country_name_orange_desktop(self, client):
        client.get("/lang/en")
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        table_start = html.find('gu-landen-table__country')
        idx = html.find('Taiwan', table_start)
        assert idx != -1
        tr_start = html.rfind('<tr', 0, idx)
        row = html[tr_start:html.find('</tr>', idx)]
        assert 'gu-landen-exception-name">Taiwan</span>' in row

    def test_non_exception_country_name_not_orange_desktop(self, client):
        client.get("/lang/en")
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        table_start = html.find('gu-landen-table__country')
        idx = html.find('Norway', table_start)
        assert idx != -1
        tr_start = html.rfind('<tr', 0, idx)
        row = html[tr_start:html.find('</tr>', idx)]
        assert 'gu-landen-exception-name' not in row

    def test_exception_country_name_orange_mobile(self, client):
        client.get("/lang/en")
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        cards_start = html.find('class="gu-landen-cards"')
        idx = html.find('Taiwan', cards_start)
        assert idx != -1
        summary_start = html.rfind('<summary', 0, idx)
        summary_end = html.find('</summary>', idx)
        summary = html[summary_start:summary_end]
        assert 'gu-landen-exception-name">Taiwan</span>' in summary

    def test_dutch_country_names_on_dutch_page(self, client):
        client.get("/lang/nl")
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        table_start = html.find('gu-landen-table__country')
        table = html[table_start:]
        assert 'Duitsland' in table
        assert 'Nederland' in table
        assert 'Verenigde Staten' in table
        assert '>Germany<' not in table

    def test_english_country_names_on_english_page(self, client):
        client.get("/lang/en")
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        table_start = html.find('gu-landen-table__country')
        table = html[table_start:]
        assert 'Germany' in table
        assert 'Netherlands' in table
        assert 'United States' in table
        assert 'Duitsland' not in table

class TestManifestLandenSection:
    def test_manifest_toc_has_five_items(self, client):
        response = client.get("/manifest")
        html = response.get_data(as_text=True)
        assert html.count('gu-manifest__tocN') == 5

    def test_manifest_landen_section_present(self, client):
        response = client.get("/manifest")
        html = response.get_data(as_text=True)
        assert 'id="landen"' in html
        assert 'id="lange-termijn"' in html

    def test_manifest_links_to_landenlijst(self, client):
        response = client.get("/manifest")
        html = response.get_data(as_text=True)
        assert '/manifest/landen' in html

    def test_manifest_landen_new_explainer_text_dutch(self, client):
        # Wijziging 2: de (uitgebreide) uitlegtekst staat nu op /manifest,
        # in de #landen-sectie, i.p.v. als losse tekst op /manifest/landen.
        client.get("/lang/nl")
        response = client.get("/manifest")
        html = response.get_data(as_text=True)
        section_start = html.find('id="landen"')
        section_end = html.find('id="wetgeving"')
        assert section_start != -1 and section_end != -1
        section = html[section_start:section_end]
        assert "Toetreding is gekoppeld aan drie bestaande" in section
        assert "Landen boven het minimumniveau worden uitgenodigd mee te doen." in section
        assert "beperkte foutmarge" in section

    def test_manifest_landen_new_explainer_text_english(self, client):
        client.get("/lang/en")
        response = client.get("/manifest")
        html = response.get_data(as_text=True)
        section_start = html.find('id="landen"')
        section_end = html.find('id="wetgeving"')
        assert section_start != -1 and section_end != -1
        section = html[section_start:section_end]
        assert "Accession is tied to three existing" in section
        assert "Countries above the minimum level are invited to join." in section
        assert "limited margin of error" in section

    def test_manifest_landen_explainer_not_on_landenlijst_page(self, client):
        # De nieuwe tekst (met de unieke zin over uitnodigen) hoort alleen op
        # /manifest te staan, niet (ook) op /manifest/landen.
        client.get("/lang/nl")
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        assert "Landen boven het minimumniveau worden uitgenodigd mee te doen." not in html


class TestManifestWetgevingSection:
    def test_wetgeving_section_present_between_landen_and_lange_termijn(self, client):
        response = client.get("/manifest")
        html = response.get_data(as_text=True)
        idx_landen = html.find('id="landen"')
        idx_wetgeving = html.find('id="wetgeving"')
        idx_lange_termijn = html.find('id="lange-termijn"')
        assert -1 not in (idx_landen, idx_wetgeving, idx_lange_termijn)
        assert idx_landen < idx_wetgeving < idx_lange_termijn

    def test_wetgeving_title_renamed_dutch(self, client):
        # Sectiekop én TOC-label worden "Gefaseerde wetgeving" (i.p.v. enkel
        # "Wetgeving"), in beide gevallen exact dezelfde tekst.
        client.get("/lang/nl")
        response = client.get("/manifest")
        html = response.get_data(as_text=True)
        assert html.count("Gefaseerde wetgeving") == 2  # TOC-label + <h2>
        assert '<h2>Gefaseerde wetgeving</h2>' in html

    def test_wetgeving_title_renamed_english(self, client):
        client.get("/lang/en")
        response = client.get("/manifest")
        html = response.get_data(as_text=True)
        assert html.count("Phased legislation") == 2
        assert '<h2>Phased legislation</h2>' in html

    def test_wetgeving_section_is_one_table_with_three_body_rows(self, client):
        # Eén semantische <table> (niet drie losse tabellen/kaarten), met
        # precies 3 datarijen in de tbody.
        response = client.get("/manifest")
        html = response.get_data(as_text=True)
        section_start = html.find('id="wetgeving"')
        section_end = html.find('id="lange-termijn"')
        section = html[section_start:section_end]
        assert section.count('<table') == 1
        tbody_start = section.find('<tbody>')
        tbody_end = section.find('</tbody>')
        tbody = section[tbody_start:tbody_end]
        assert tbody.count('<tr>') == 3
        assert '<thead>' in section and '</thead>' in section

    def test_wetgeving_table_scope_attributes(self, client):
        # Behoud van semantische thead/tbody-structuur inclusief scope:
        # scope="col" op de kolomkoppen, scope="row" op de fase-cel per rij.
        response = client.get("/manifest")
        html = response.get_data(as_text=True)
        section_start = html.find('id="wetgeving"')
        section_end = html.find('id="lange-termijn"')
        section = html[section_start:section_end]
        assert section.count('scope="col"') == 4
        assert section.count('scope="row"') == 3

    def test_wetgeving_table_has_three_rows_dutch(self, client):
        client.get("/lang/nl")
        response = client.get("/manifest")
        html = response.get_data(as_text=True)
        assert html.count('gu-wet-table__phase') == 3
        assert "1. Fundament" in html
        assert "2. Verdieping" in html
        assert "3. Politiek gevoelig" in html
        assert "80%-drempel" in html

    def test_wetgeving_table_has_three_rows_english(self, client):
        client.get("/lang/en")
        response = client.get("/manifest")
        html = response.get_data(as_text=True)
        assert html.count('gu-wet-table__phase') == 3
        assert "1. Foundation" in html
        assert "2. Deepening" in html
        assert "3. Politically sensitive" in html
        assert "80% threshold" in html

    def test_wetgeving_new_intro_text_dutch(self, client):
        client.get("/lang/nl")
        response = client.get("/manifest")
        html = response.get_data(as_text=True)
        assert "Wetgeving nemen we gefaseerd over. Eenvoudige, technische regels komen eerst" in html
        assert "naar het voorbeeld van de EU: klein beginnen en uitbreiden waar het werkt" not in html
        assert "Simpele, technische regels komen eerst" not in html

    def test_wetgeving_new_intro_text_english(self, client):
        client.get("/lang/en")
        response = client.get("/manifest")
        html = response.get_data(as_text=True)
        assert "We adopt legislation in phases. Simple, technical rules come first" in html
        assert "following the EU's example: start small and expand where it works" not in html

    def test_wetgeving_gatt_and_exclusions_paragraphs_dutch(self, client):
        client.get("/lang/nl")
        response = client.get("/manifest")
        html = response.get_data(as_text=True)
        assert "artikel XX GATT" in html
        assert "Dit lijkt strijdig met het principe van de wereldhandelsorganisatie" in html
        assert "verwachten we hier een uitzondering op te kunnen maken" in html
        assert "De grondslag is de algemene uitzondering van artikel XX GATT" not in html
        # Uitsluitingsalinea ongewijzigd.
        assert "Landbouwbeleid, cohesiefondsen en de muntunie" in html

    def test_wetgeving_gatt_and_exclusions_paragraphs_english(self, client):
        client.get("/lang/en")
        response = client.get("/manifest")
        html = response.get_data(as_text=True)
        assert "Article XX GATT" in html
        assert "This appears to conflict with the World Trade Organization's principle" in html
        assert "we expect to be able to make an exception here" in html
        assert "The basis is the general exception in Article XX GATT" not in html
        assert "We do not adopt agricultural policy, cohesion funds, or the monetary union" in html
