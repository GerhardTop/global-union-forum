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

    def test_israel_and_us_show_no(self, client):
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
        assert 'gu-invite-badge--no">No<' in row_israel
        assert 'gu-invite-badge--no">No<' in row_us

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
