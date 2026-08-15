"""
Test-suite voor main.py: /manifest/landen (landenlijst-pagina).
Dekt: pagina laadt, toont 183 landen, i18n-tekst per taal, en de drie
uitzonderingsgevallen (Taiwan/Israel/VS) tonen de juiste 'Uitnodigen'-waarde.
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

    def test_taiwan_shows_separate_status_not_yes(self, client):
        client.get("/lang/en")
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        # Zoek de tabelrij zelf (niet de uitzonderingsalinea, die 'separate
        # status' ook al noemt) en beperk tot díe rij (tot de sluitende
        # </tr>), zodat lange remark-tooltips of een vaste tekenlimiet niet
        # per ongeluk de volgende rij meepakken.
        table_start = html.find('gu-landen-table__country')
        idx_taiwan = html.find('Taiwan', table_start)
        assert idx_taiwan != -1
        row_end = html.find('</tr>', idx_taiwan)
        row_taiwan = html[idx_taiwan:row_end]
        assert 'gu-invite-badge--separate">separate status<' in row_taiwan

    def test_israel_and_us_show_not_yet(self, client):
        client.get("/lang/en")
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        # Zoek vanaf de tabel (na de introtekst, die 'Israel'/'United States'
        # ook al noemt in de uitzonderingsalinea) zodat we echt de tabelrij
        # pakken, niet de eerste vermelding in de lopende tekst. Beperk tot
        # de sluitende </tr> i.p.v. een vaste tekenlimiet.
        table_start = html.find('gu-landen-table__country')
        idx_israel = html.find('Israel', table_start)
        idx_us = html.find('United States', table_start)
        assert idx_israel != -1 and idx_us != -1
        row_israel = html[idx_israel:html.find('</tr>', idx_israel)]
        row_us = html[idx_us:html.find('</tr>', idx_us)]
        assert 'gu-invite-badge--no">Not yet<' in row_israel
        assert 'gu-invite-badge--no">Not yet<' in row_us

    def test_below_minimum_country_also_shows_not_yet(self, client):
        # Bevestigt dat 'Nog niet'/'Not yet' niet alleen voor Israel/VS geldt:
        # elk land met invite_class 'no' (109 van de 111) is een gewoon
        # 'Below minimum'-land zonder remark, bijv. Panama.
        client.get("/lang/en")
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        table_start = html.find('gu-landen-table__country')
        idx = html.find('Panama', table_start)
        assert idx != -1
        row = html[idx:html.find('</tr>', idx)]
        assert 'gu-invite-badge--no">Not yet<' in row

    def test_yes_badge_shows_invite_label(self, client):
        client.get("/lang/en")
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        table_start = html.find('gu-landen-table__country')
        idx = html.find('Norway', table_start)
        assert idx != -1
        row = html[idx:html.find('</tr>', idx)]
        assert 'gu-invite-badge--yes">Invite<' in row
        assert '>Yes<' not in row

    def test_yes_badge_shows_uitnodigen_label_dutch(self, client):
        client.get("/lang/nl")
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        table_start = html.find('gu-landen-table__country')
        idx = html.find('Norway', table_start)
        assert idx != -1
        row = html[idx:html.find('</tr>', idx)]
        assert 'gu-invite-badge--yes">Uitnodigen<' in row

    def test_invite_badge_classes(self, client):
        client.get("/lang/en")
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        # Alle vier de badge-varianten moeten voorkomen (tenminste 1x elk).
        assert 'gu-invite-badge--yes' in html
        assert 'gu-invite-badge--no' in html
        assert 'gu-invite-badge--separate' in html
        assert 'gu-invite-badge--na' in html

    def test_mobile_cards_present_for_all_countries(self, client):
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        assert html.count('class="gu-landen-card"') == 183
        assert html.count('gu-landen-card__summary') == 183

    def test_mobile_card_details_show_scores_not_level_or_invite_again(self, client):
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
        # Niveau/Uitnodigen-badges horen alleen in de summary, niet herhaald
        # in de uitgeklapte details.
        assert 'gu-level-badge' in summary_part
        assert 'gu-invite-badge' in summary_part
        assert 'gu-level-badge' not in details_part
        assert 'gu-invite-badge' not in details_part
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

    def test_desktop_detail_row_hidden_by_default(self, client):
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        assert html.count('gu-landen-table__detailRow') > 0
        # Elke detailrij heeft het hidden-attribuut in de server-gerenderde HTML
        # (JS is wat het later verwijdert bij een klik, niet de server).
        idx = html.find('gu-landen-table__detailRow')
        row_tag_end = html.find('>', idx)
        assert 'hidden' in html[idx:row_tag_end]

    def test_desktop_detail_row_has_index_dots(self, client):
        client.get("/lang/en")
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        table_start = html.find('gu-landen-table__country')
        idx = html.find('Norway', table_start)
        detail_start = html.find('gu-landen-table__detailRow', idx)
        detail_end = html.find('</tr>', detail_start)
        detail = html[detail_start:detail_end]
        # Norway: DI 9.81, CPI 81, HRI 0.947 — alle drie ruim boven de
        # 'Boven minimum'-drempel, dus alle drie 'pass' (groen).
        assert 'gu-index-dot--pass' in detail
        assert 'gu-index-dot--fail' not in detail

    def test_remark_visible_in_desktop_detail_not_only_tooltip(self, client):
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

    def test_index_dot_fail_for_below_minimum_country(self, client):
        client.get("/lang/en")
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        table_start = html.find('gu-landen-table__country')
        idx = html.find('Afghanistan', table_start)  # DI 0.25, CPI 16, HRI 0.041 — alles onder de drempel
        detail_start = html.find('gu-landen-table__detailRow', idx)
        detail_end = html.find('</tr>', detail_start)
        detail = html[detail_start:detail_end]
        assert 'gu-index-dot--pass' not in detail
        assert detail.count('gu-index-dot--fail') == 3


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
