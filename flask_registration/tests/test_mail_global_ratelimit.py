"""
Test-suite voor de gedeelde globale mail-rate-limit (app/utils.py:
mail_global_key / MAIL_GLOBAL_RATE_LIMIT = "30 per hour") — het structurele
vangnet BOVENOP de bestaande per-route- en per-IP-limieten: welk mail-
formulier een misbruiker ook vindt (/feedback, /uitnodiging, /aanmelden,
/wachtwoord-vergeten, /verify/resend, /verify/resend-onbevestigd), ze delen
allemaal precies dezelfde teller.

Dit testbestand bewijst specifiek het GEDEELDE, cross-route-karakter: het
per-route-gedrag van elke afzonderlijke route (per-IP-limiet, honeypot,
@login_required) is al gedekt door test_uitnodiging_antibot.py — dit
bestand test dat /feedback en /uitnodiging (en dus impliciet elke andere
route die dezelfde decorator gebruikt) in werkelijkheid één en dezelfde
sleutel delen, ongeacht welke route of welk IP de aanvraag doet.

Verspreid over veel verschillende gesimuleerde IP's (zoals in
test_uitnodiging_antibot.py) zodat geen enkele PER-IP-limiet (5/uur op
beide routes) of de eigen 20/uur-per-route-globale-limiet van /uitnodiging
ooit toeslaat vóórdat de gedeelde 30/uur-limiet dat doet — anders zou de
test iets anders meten dan bedoeld.
"""
import importlib
import os
import sys

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import config as config_module
from app import bcrypt, create_app, db
from app.models import User

social_module = importlib.import_module("app.routes.social")


@pytest.fixture
def app():
    orig_db_uri = config_module.Config.SQLALCHEMY_DATABASE_URI
    orig_ratelimit_enabled = getattr(config_module.Config, "RATELIMIT_ENABLED", True)
    config_module.Config.SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    config_module.Config.TESTING = True
    config_module.Config.WTF_CSRF_ENABLED = False
    config_module.Config.RATELIMIT_ENABLED = True

    try:
        flask_app = create_app()
        with flask_app.app_context():
            yield flask_app
            db.session.remove()
            db.drop_all()
    finally:
        config_module.Config.SQLALCHEMY_DATABASE_URI = orig_db_uri
        config_module.Config.RATELIMIT_ENABLED = orig_ratelimit_enabled


@pytest.fixture
def logged_in_client(app):
    """Zowel /feedback als /uitnodiging vereisen sinds @login_required een
    ingelogde gebruiker — één sessie-cookie voor de hele test."""
    with app.app_context():
        db.session.add(User(
            username="mailtester", first_name="Mail", last_name="Tester",
            email="mailtester@example.com",
            password_hash=bcrypt.generate_password_hash("Sterk1!ww").decode("utf-8"),
            verified=True,
        ))
        db.session.commit()
    client = app.test_client()
    client.post("/login", data={"email": "mailtester@example.com",
                                 "password": "Sterk1!ww"})
    with client.session_transaction() as sess:
        assert "_user_id" in sess
    return client


@pytest.fixture
def sent_emails(monkeypatch):
    calls = []

    def _fake_send_email(to, subject, html):
        calls.append(to)
        return True

    monkeypatch.setattr(social_module, "send_email", _fake_send_email)
    return calls


def _feedback(client, *, ip, i):
    return client.post(
        "/feedback",
        data={"lang": "nl", "feedback_message": f"bericht {i}", "website": ""},
        environ_overrides={"REMOTE_ADDR": ip},
        follow_redirects=True,
    )


def _invite(client, *, ip, i):
    return client.post(
        "/uitnodiging",
        data={"lang": "nl", "invite_email": f"doelwit{i}@example.com",
              "invite_message": "", "website": ""},
        environ_overrides={"REMOTE_ADDR": ip},
        follow_redirects=True,
    )


class TestGedeeldeMailGlobalLimiet:

    def test_teller_wordt_gedeeld_tussen_feedback_en_uitnodiging(
        self, logged_in_client, sent_emails
    ):
        """
        20 verschillende IP's posten naar /feedback (ver onder de 5/uur-
        per-IP-limiet daar, en /feedback heeft geen eigen globale limiet) +
        10 verschillende IP's posten naar /uitnodiging (ver onder zowel
        zijn eigen 5/uur-per-IP als zijn eigen 20/uur-per-route-globale
        limiet) = 30 geslaagde mails, exact op de gedeelde grens.

        De 31e poging — weer een nieuw IP, en op WEIDE VAN DE TWEE ROUTES
        gestuurd — moet alsnog geblokkeerd worden, want de gedeelde teller
        (niet de per-route-tellers) is nu vol.
        """
        for i in range(20):
            resp = _feedback(logged_in_client, ip=f"198.51.100.{i}", i=i)
            assert "Bedankt voor je feedback" in resp.get_data(as_text=True)
        for i in range(10):
            resp = _invite(logged_in_client, ip=f"203.0.113.{i}", i=i)
            assert "Uitnodiging verstuurd" in resp.get_data(as_text=True)
        assert len(sent_emails) == 30

        # 31e: nog een gloednieuw IP, op /feedback (had op zichzelf nog
        # ruimschoots budget: 0/5 per-IP, geen eigen globale limiet).
        resp = _feedback(logged_in_client, ip="198.51.100.250", i=999)
        assert len(sent_emails) == 30  # niet verstuurd
        assert "Te veel pogingen" in resp.get_data(as_text=True)

        # En ook via de ANDERE route blijft de gedeelde teller vol — dit is
        # precies het punt: het is niet toevallig "feedback's eigen teller"
        # die vol zit, maar de gedeelde.
        resp = _invite(logged_in_client, ip="203.0.113.250", i=998)
        assert len(sent_emails) == 30
        assert "Te veel pogingen" in resp.get_data(as_text=True)


class TestFeedbackLoginRequired:

    def test_login_required_stuurt_anonieme_aanvraag_naar_login(self, app):
        client = app.test_client()
        resp = client.post(
            "/feedback",
            data={"lang": "nl", "feedback_message": "hoi", "website": ""},
            environ_overrides={"REMOTE_ADDR": "203.0.113.77"},
        )
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]
