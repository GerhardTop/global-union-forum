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
        # status' ook al noemt) en check daar de Invite-celwaarde.
        table_start = html.find('gu-landen-table__country')
        idx_taiwan = html.find('Taiwan', table_start)
        assert idx_taiwan != -1
        row_taiwan = html[idx_taiwan:idx_taiwan + 700]
        assert 'data-label="Invite">separate status<' in row_taiwan

    def test_israel_and_us_show_no(self, client):
        client.get("/lang/en")
        response = client.get("/manifest/landen")
        html = response.get_data(as_text=True)
        # Zoek vanaf de tabel (na de introtekst, die 'Israel'/'United States'
        # ook al noemt in de uitzonderingsalinea) zodat we echt de tabelrij
        # pakken, niet de eerste vermelding in de lopende tekst.
        table_start = html.find('gu-landen-table__country')
        idx_israel = html.find('Israel', table_start)
        idx_us = html.find('United States', table_start)
        assert idx_israel != -1 and idx_us != -1
        row_israel = html[idx_israel:idx_israel + 700]
        row_us = html[idx_us:idx_us + 700]
        assert 'data-label="Invite">No<' in row_israel
        assert 'data-label="Invite">No<' in row_us


class TestManifestLandenSection:
    def test_manifest_toc_has_four_items(self, client):
        response = client.get("/manifest")
        html = response.get_data(as_text=True)
        assert html.count('gu-manifest__tocN') == 4

    def test_manifest_landen_section_present(self, client):
        response = client.get("/manifest")
        html = response.get_data(as_text=True)
        assert 'id="landen"' in html
        assert 'id="lange-termijn"' in html

    def test_manifest_links_to_landenlijst(self, client):
        response = client.get("/manifest")
        html = response.get_data(as_text=True)
        assert '/manifest/landen' in html
