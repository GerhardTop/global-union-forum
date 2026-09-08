"""
Test-suite voor de twee losstaande WikiPolitics-preview-pagina's.

Deze pagina's staan bewust volledig los van de site: bereikbaar via hun
directe URL, maar niet gelinkt vanuit _header.html of enige bestaande
route/template.  De tests borgen precies dat: beide laden, de onderlinge
link klopt, de ruimere CSP geldt alleen hier, en niets in de rest van de
site verwijst ernaar.
"""
import re
from pathlib import Path

import pytest

_APP_DIR = Path(__file__).resolve().parent.parent / "app"


class TestWikipoliticsPages:
    def test_design_loads(self, client):
        resp = client.get("/wikipolitics/design")
        assert resp.status_code == 200
        assert resp.mimetype == "text/html"

    def test_overwegingen_loads(self, client):
        resp = client.get("/wikipolitics/overwegingen")
        assert resp.status_code == 200
        assert resp.mimetype == "text/html"

    def test_design_is_served_verbatim(self, client):
        resp = client.get("/wikipolitics/design")
        disk = (_APP_DIR / "wikipolitics" / "wikipolitics-wireframes.html").read_bytes()
        assert resp.data == disk

    def test_overwegingen_is_served_verbatim(self, client):
        resp = client.get("/wikipolitics/overwegingen")
        disk = (_APP_DIR / "wikipolitics" / "wikipolitics-rationale.html").read_bytes()
        assert resp.data == disk

    def test_cross_link_design_to_overwegingen(self, client):
        html = client.get("/wikipolitics/design").get_data(as_text=True)
        assert 'href="/wikipolitics/overwegingen"' in html

    def test_cross_link_overwegingen_to_design(self, client):
        html = client.get("/wikipolitics/overwegingen").get_data(as_text=True)
        assert 'href="/wikipolitics/design"' in html

    @pytest.mark.parametrize("path,target,label", [
        ("/wikipolitics/design", "/wikipolitics/overwegingen", "Design rationale"),
        ("/wikipolitics/overwegingen", "/wikipolitics/design", "Back to wireframes"),
    ])
    def test_crosslink_is_reinjected_after_root_swap(self, client, path, target, label):
        """
        De bundle-loader vervangt <documentElement>, waardoor de statische
        cross-link uit de <body> verdwijnt.  Er moet daarom ook een runtime-
        her-injectie zijn, direct na de root-swap: een #wp-nav-pill met het
        Engelse label en de juiste href.
        """
        html = client.get(path).get_data(as_text=True)
        # statische fallback in de <body>
        assert f'<a id="wp-nav" href="{target}"' in html
        assert label in html
        # runtime her-injectie, na replaceWith
        swap = "document.documentElement.replaceWith(doc.documentElement);"
        assert swap in html
        after = html.split(swap, 1)[1]
        assert "a.id = 'wp-nav';" in after
        assert f"a.href = '{target}';" in after
        assert label in after
        # Engelstalig — geen resten van de oude NL-balk
        for nl in ("Bekijk de overwegingen", "Terug naar het design"):
            assert nl not in html


class TestWikipoliticsCsp:
    def test_relaxed_csp_only_on_these_routes(self, client):
        for path in ("/wikipolitics/design", "/wikipolitics/overwegingen"):
            csp = client.get(path).headers.get("Content-Security-Policy", "")
            assert "blob:" in csp, f"{path} mist blob: in CSP"
            assert "script-src" in csp and "'unsafe-eval'" in csp

    def test_site_csp_stays_strict(self, client):
        # Een gewone pagina houdt het strikte, blob-loze beleid.
        csp = client.get("/").headers.get("Content-Security-Policy", "")
        assert "blob:" not in csp
        assert "'unsafe-eval'" not in csp


class TestWikipoliticsIsolation:
    def test_not_referenced_anywhere_in_site(self):
        """
        Geen enkel template of route-bestand (buiten de WikiPolitics-blueprint
        en deze test) mag naar /wikipolitics/ of de HTML-bestandsnamen
        verwijzen.
        """
        offenders = []
        needles = ("wikipolitics/design", "wikipolitics/overwegingen",
                   "wikipolitics-wireframes", "wikipolitics-rationale")
        allowed = {
            _APP_DIR / "routes" / "wikipolitics.py",
        }
        for path in list(_APP_DIR.rglob("*.html")) + list(_APP_DIR.rglob("*.py")):
            if path in allowed or "wikipolitics" in path.parts[-2:]:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if any(n in text for n in needles):
                offenders.append(str(path.relative_to(_APP_DIR)))
        assert not offenders, f"onverwachte verwijzing naar WikiPolitics in: {offenders}"

    def test_header_has_no_wikipolitics_link(self):
        header = (_APP_DIR / "templates" / "_header.html").read_text(encoding="utf-8")
        assert "wikipolitics" not in header.lower()
