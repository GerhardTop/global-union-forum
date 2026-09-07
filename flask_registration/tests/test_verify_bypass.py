"""
Test-suite voor de TIJDELIJKE e-mailverificatie-bypass bij registratie
(zie de uitleg bovenaan app/routes/social.py) — ingevoerd voor de
gastgebruikerstest van 2026-09-08, terwijl het Resend-maandquotum op is.

Dekt: binnen het bypass-venster wordt een nieuw account direct geverifieerd
aangemaakt zonder verificatiemail-poging en kan meteen worden ingelogd; na
de deadline (gesimuleerd via het systeemklok-argument, niet de echte datum)
keert het normale gedrag automatisch terug.

Eigen app-fixture i.p.v. de gedeelde sessie-fixture uit conftest.py — zelfde
patroon als test_auth.py/test_username.py: /aanmelden is rate-limited
(3/uur/IP) en zonder RATELIMIT_ENABLED=False vóór create_app() lopen
herhaalde POSTs binnen dezelfde pytest-sessie daar binnen één testfile al
tegenaan.
"""
import importlib
import logging
import os
import sys
from datetime import timedelta

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import config as config_module
from app import create_app, db
from app.models import User

# app/routes/__init__.py doet `from app.routes.social import social`, wat het
# attribuut app.routes.social overschrijft met de Blueprint i.p.v. de module
# — een gewone `import app.routes.social as ...` zou dus de Blueprint pakken.
# importlib.import_module() haalt de echte module op via sys.modules.
social_module = importlib.import_module("app.routes.social")

VALID_PASSWORD = "ValidPassword123!"


@pytest.fixture
def app():
    orig_db_uri = config_module.Config.SQLALCHEMY_DATABASE_URI
    config_module.Config.SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    config_module.Config.TESTING = True
    config_module.Config.WTF_CSRF_ENABLED = False
    config_module.Config.RATELIMIT_ENABLED = False

    try:
        flask_app = create_app()
        with flask_app.app_context():
            yield flask_app
            db.session.remove()
            db.drop_all()
    finally:
        config_module.Config.SQLALCHEMY_DATABASE_URI = orig_db_uri


@pytest.fixture
def client(app):
    return app.test_client()


def _register(client, *, username, email):
    return client.post(
        "/aanmelden",
        data={
            "username": username,
            "email": email,
            "password": VALID_PASSWORD,
            "password_confirm": VALID_PASSWORD,
            "linkedin_url": f"https://www.linkedin.com/in/{username}",
        },
        follow_redirects=True,
    )


def _login(client, *, email):
    return client.post(
        "/login",
        data={"email": email, "password": VALID_PASSWORD},
        follow_redirects=False,
    )


class TestVerifyBypassBinnenVenster:
    def test_registratie_slaat_mail_over_en_verifieert_direct(
        self, app, client, monkeypatch, caplog
    ):
        monkeypatch.setattr(social_module, "_today_utc",
                            lambda: social_module._VERIFY_BYPASS_DEADLINE)
        sent = []
        monkeypatch.setattr(social_module, "_send_verify_email",
                            lambda *a, **kw: sent.append(a))

        with caplog.at_level(logging.WARNING):
            response = _register(client, username="gast_bypass1", email="gast1@example.com")

        assert response.status_code == 200
        assert sent == []  # geen verificatiemail-poging

        with app.app_context():
            user = User.query.filter_by(email="gast1@example.com").first()
            assert user is not None
            assert user.verified is True

        assert any(
            "[TIJDELIJK]" in r.message and "gast1@example.com" in r.message
            for r in caplog.records
        )

    def test_gebruiker_kan_direct_inloggen_zonder_verificatie(
        self, app, client, monkeypatch
    ):
        monkeypatch.setattr(social_module, "_today_utc",
                            lambda: social_module._VERIFY_BYPASS_DEADLINE)
        monkeypatch.setattr(social_module, "_send_verify_email", lambda *a, **kw: None)

        _register(client, username="gast_bypass2", email="gast2@example.com")
        login_response = _login(client, email="gast2@example.com")

        assert login_response.status_code == 302
        assert login_response.headers["Location"] == "/"
        with client.session_transaction() as sess:
            assert sess.get("_user_id") is not None


class TestVerifyBypassNaDeadline:
    def test_na_deadline_werkt_verificatie_weer_normaal(
        self, app, client, monkeypatch
    ):
        na_deadline = social_module._VERIFY_BYPASS_DEADLINE + timedelta(days=1)
        monkeypatch.setattr(social_module, "_today_utc", lambda: na_deadline)
        sent = []
        monkeypatch.setattr(social_module, "_send_verify_email",
                            lambda *a, **kw: sent.append(a))

        response = _register(client, username="normaal_gebruiker", email="normaal@example.com")

        assert response.status_code == 200
        with app.app_context():
            user = User.query.filter_by(email="normaal@example.com").first()
            assert user is not None
            assert user.verified is False
        assert len(sent) == 1  # verificatiemail wordt weer geprobeerd

    def test_na_deadline_kan_niet_ongeverifieerd_inloggen(self, app, client, monkeypatch):
        na_deadline = social_module._VERIFY_BYPASS_DEADLINE + timedelta(days=1)
        monkeypatch.setattr(social_module, "_today_utc", lambda: na_deadline)
        monkeypatch.setattr(social_module, "_send_verify_email", lambda *a, **kw: None)

        _register(client, username="normaal_gebruiker2", email="normaal2@example.com")
        login_response = _login(client, email="normaal2@example.com")

        # Credentials kloppen maar account is niet geverifieerd: geen login,
        # login.html toont de resend-modal (Blokker 1) i.p.v. een redirect.
        assert login_response.status_code == 200
        with client.session_transaction() as sess:
            assert sess.get("_user_id") is None


class TestVerifyBypassActiveUnit:
    """Pure-functie grenstest — geen Flask/db nodig, alleen _today_utc gemockt."""

    def test_op_de_deadline_zelf_is_bypass_nog_actief(self, monkeypatch):
        monkeypatch.setattr(social_module, "_today_utc",
                            lambda: social_module._VERIFY_BYPASS_DEADLINE)
        assert social_module._verify_bypass_active() is True

    def test_een_dag_na_de_deadline_is_bypass_niet_meer_actief(self, monkeypatch):
        monkeypatch.setattr(
            social_module, "_today_utc",
            lambda: social_module._VERIFY_BYPASS_DEADLINE + timedelta(days=1),
        )
        assert social_module._verify_bypass_active() is False

    def test_een_dag_voor_de_deadline_is_bypass_actief(self, monkeypatch):
        monkeypatch.setattr(
            social_module, "_today_utc",
            lambda: social_module._VERIFY_BYPASS_DEADLINE - timedelta(days=1),
        )
        assert social_module._verify_bypass_active() is True
