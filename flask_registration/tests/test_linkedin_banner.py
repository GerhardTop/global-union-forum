"""
Test-suite voor de verwijdering van de oranje LinkedIn-banner (voorheen in
base.html, met "Vul je LinkedIn profiel in..."/"Aanvullen →") die op elke
pagina verscheen voor ingelogde gebruikers zonder linkedin_url — een
verwarrend overblijfsel nu LinkedIn optioneel is.

Dekt: de banner-CSS-klasse verschijnt nergens meer, in geen van beide talen,
voor een ingelogde gebruiker zonder LinkedIn-URL. De onderliggende
/profiel/linkedin-aanvullen-route blijft intact en los bereikbaar.
"""
from app import bcrypt
from app.models import User


def _maak_ingelogde_client_zonder_linkedin(client, db, email):
    username = "geen_lnkdn_" + email.split("@")[0]
    user = User(
        username=username,
        email=email,
        password_hash=bcrypt.generate_password_hash("Sterk1!ww").decode("utf-8"),
        verified=True,
        linkedin_url=None,
    )
    db.session.add(user)
    db.session.commit()

    client.post("/login", data={"email": email, "password": "Sterk1!ww"})
    return user


class TestLinkedinBannerVerwijderd:
    def test_geen_banner_op_homepage_nederlands(self, client, db):
        _maak_ingelogde_client_zonder_linkedin(client, db, "geenlinkedin_nl@example.com")
        client.get("/lang/nl")

        html = client.get("/").get_data(as_text=True)

        assert "gu-linkedin-banner" not in html
        assert "Vul je LinkedIn profiel in" not in html
        assert "Aanvullen →" not in html

    def test_geen_banner_op_homepage_engels(self, client, db):
        _maak_ingelogde_client_zonder_linkedin(client, db, "geenlinkedin_en@example.com")
        client.get("/lang/en")

        html = client.get("/").get_data(as_text=True)

        assert "gu-linkedin-banner" not in html
        assert "Add your LinkedIn profile to post under your real name." not in html
        assert "Add it →" not in html

    def test_geen_banner_op_andere_pagina(self, client, db):
        """De banner stond in base.html — dus op elke pagina, niet alleen /."""
        _maak_ingelogde_client_zonder_linkedin(client, db, "geenlinkedin_manifest@example.com")

        html = client.get("/manifest").get_data(as_text=True)

        assert "gu-linkedin-banner" not in html

    def test_linkedin_aanvullen_route_blijft_los_bereikbaar(self, client, db):
        """De banner is weg, maar de onderliggende pagina/route zelf niet."""
        _maak_ingelogde_client_zonder_linkedin(client, db, "geenlinkedin_route@example.com")

        response = client.get("/profiel/linkedin-aanvullen")

        assert response.status_code == 200
