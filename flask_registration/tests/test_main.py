"""
Test-suite voor main.py: /manifest/landen (landenlijst-pagina).
Dekt: pagina laadt, toont 183 landen, i18n-tekst per taal (incl. NL/EN
landnamen), losse DI/CPI/HRI-kolommen met 3-standen-kleurpuntjes, en dat het
uitklap-mechanisme op desktop uitsluitend voor de drie uitzonderingsgevallen
(Taiwan/Israel/VS) bestaat.
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
        assert "Toetreding is gekoppeld aan drie bestaande" in html

    def test_landenlijst_english_text(self, client):
        client.get("/lang/en")
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        assert "Country overview" in html
        assert "Accession is tied to three existing" in html

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

    def test_threshold_text_dutch(self, client):
        client.get("/lang/nl")
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        assert "DI ≥ 6,0" in html
        assert "CPI ≥ 40" in html
        assert "HRI ≥ 0,70" in html
        assert "DI ≥ 8,0" in html
        assert "HRI ≥ 0,75" in html

    def test_threshold_text_english(self, client):
        client.get("/lang/en")
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        assert "DI ≥ 6.0" in html
        assert "HRI ≥ 0.70" in html
        assert "DI ≥ 8.0" in html
        assert "HRI ≥ 0.75" in html

    def test_three_separate_index_columns_desktop(self, client):
        # Wijziging A: geen gegroepeerde 'Index'-kolom meer, maar drie losse,
        # volledig uitgeschreven kolommen.
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        thead_end = html.find('</thead>')
        thead = html[:thead_end]
        assert '<th>Democracy Index</th>' in thead
        assert '<th>Corruption Perceptions Index</th>' in thead
        assert '<th>Human Rights Index</th>' in thead
        assert '<th>Index</th>' not in thead

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

    def test_desktop_no_toggle_for_country_without_remark(self, client):
        # Wijziging B: geen uitklap-pijltje/gedrag meer voor landen zonder
        # toelichting — Noorwegen heeft geen remark. Rij vanaf de openende
        # <tr> zelf pakken (niet vanaf de landnaam), anders wordt het
        # class-attribuut van de <tr>, dat vóór de naam staat, gemist.
        client.get("/lang/en")
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        table_start = html.find('gu-landen-table__country')
        idx = html.find('Norway', table_start)
        assert idx != -1
        tr_start = html.rfind('<tr', 0, idx)
        row = html[tr_start:html.find('</tr>', idx)]
        assert 'gu-landen-table__row' not in row  # dekt ook __rowToggle

    def test_desktop_toggle_only_for_three_exception_countries(self, client):
        # Alleen Taiwan/Israel/VS behouden het uitklap-mechanisme —
        # precies 3 uitklaprijen op de hele pagina. Alleen binnen <tbody>
        # tellen, niet in de hele pagina: de inline <script> onderaan bevat
        # de klasse-naam ook als string-literal (classList.contains-check).
        client.get("/lang/en")
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        tbody = html[html.find('<tbody>'):html.find('</tbody>')]
        assert tbody.count('gu-landen-table__detailRow') == 3
        assert tbody.count('gu-landen-table__rowToggle') == 3
        table_start = html.find('gu-landen-table__country')
        for name in ("Taiwan", "Israel", "United States"):
            idx = html.find(name, table_start)
            assert idx != -1, name
            tr_start = html.rfind('<tr', 0, idx)
            row = html[tr_start:html.find('</tr>', idx)]
            assert 'gu-landen-table__row"' in row
            assert 'gu-landen-table__rowToggle' in row

    def test_desktop_detail_row_hidden_by_default(self, client):
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        idx = html.find('gu-landen-table__detailRow')
        assert idx != -1
        # Elke detailrij heeft het hidden-attribuut in de server-gerenderde HTML
        # (JS is wat het later verwijdert bij een klik, niet de server).
        row_tag_end = html.find('>', idx)
        assert 'hidden' in html[idx:row_tag_end]

    def test_remark_visible_in_desktop_detail_not_only_tooltip(self, client):
        # De detailrij toont uitsluitend de remark (DI/CPI/HRI staan al in
        # de kolommen van de hoofdrij, dus niet meer herhaald hier).
        client.get("/lang/en")
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        table_start = html.find('gu-landen-table__country')
        idx = html.find('Taiwan', table_start)
        detail_start = html.find('gu-landen-table__detailRow', idx)
        detail_end = html.find('</tr>', detail_start)
        detail = html[detail_start:detail_end]
        assert 'gu-landen-remark' in detail
        assert 'No UN recognition' in detail
        assert 'gu-index-dot' not in detail

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

    def test_remark_visible_in_mobile_card_details(self, client):
        client.get("/lang/en")
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        # Ga naar de mobiele kaarten-sectie (na de tabel) zodat we Taiwans
        # <details>-kaart pakken, niet een eerdere vermelding elders op de
        # pagina. Zoek dan terug naar het begin van díe kaart en vooruit naar
        # het einde.
        cards_start = html.find('class="gu-landen-cards"')
        idx_taiwan = html.find('Taiwan', cards_start)
        assert idx_taiwan != -1
        card_start = html.rfind('<details class="gu-landen-card">', 0, idx_taiwan)
        card_end = html.find('</details>', idx_taiwan)
        assert card_start != -1 and card_end != -1
        card = html[card_start:card_end]
        assert 'gu-landen-remark' in card
        assert 'No UN recognition' in card


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


class TestManifestWetgevingSection:
    def test_wetgeving_section_present_between_landen_and_lange_termijn(self, client):
        response = client.get("/manifest")
        html = response.get_data(as_text=True)
        idx_landen = html.find('id="landen"')
        idx_wetgeving = html.find('id="wetgeving"')
        idx_lange_termijn = html.find('id="lange-termijn"')
        assert -1 not in (idx_landen, idx_wetgeving, idx_lange_termijn)
        assert idx_landen < idx_wetgeving < idx_lange_termijn

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

    def test_wetgeving_gatt_and_exclusions_paragraphs(self, client):
        client.get("/lang/nl")
        response = client.get("/manifest")
        html = response.get_data(as_text=True)
        assert "artikel XX GATT" in html
        assert "Landbouwbeleid, cohesiefondsen en de muntunie" in html
