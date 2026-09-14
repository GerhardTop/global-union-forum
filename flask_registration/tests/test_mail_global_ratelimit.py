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

TestGeblokkeerdVerzoekVerbruiktGeenGedeeldBudget test het omgekeerde
scenario: een IP dat WEL over zijn eigen smallere limiet heen gaat, mag de
gedeelde teller niet verder ophogen zodra die smallere limiet al blokkeert
— zie de decorator-volgorde-toelichting bij /uitnodiging in
app/routes/social.py voor waarom dat niet vanzelfsprekend is.
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
from app.utils import MAIL_GLOBAL_SCOPE

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


class TestGeblokkeerdVerzoekVerbruiktGeenGedeeldBudget:
    """
    Het interactie-scenario dat aan het licht kwam: @limiter.limit(...)
    (en dus ook shared_limit) verhoogt zijn teller ALTIJD, ongeacht of het
    verzoek uiteindelijk wordt toegestaan — en flask-limiter evalueert
    gestapelde decorators van binnen naar buiten (dichtst-bij-def eerst),
    stoppend bij de eerste overschrijding (fail_on_first_breach, standaard).
    Stond de gedeelde limiet dichter bij def dan de per-IP-limiet (de
    volgorde vóór deze fix), dan werd de gedeelde teller altijd als EERSTE
    verhoogd — dus ook voor verzoeken die daarna alsnog door de per-IP-
    limiet werden geblokkeerd. Een bot die alleen tegen zijn eigen per-IP-
    limiet aanloopt, kon zo toch het budget van alle andere mail-routes
    opsouperen.

    Met de smalste limiet nu als binnenste decorator (dichtst bij def),
    stopt de evaluatie bij een per-IP-overschrijding VOORDAT de gedeelde
    limiet ooit geraakt wordt — industry best practice bij gelaagde rate
    limits (vgl. APISIX: "a request rejected by any plugin does not
    consume quota in subsequent plugins").
    """

    def test_over_de_per_ip_limiet_heen_geblokkeerde_pogingen_tellen_niet_mee(
        self, logged_in_client, sent_emails
    ):
        from app import limiter as _limiter

        # Eén IP knalt ver over zijn eigen per-IP-limiet (5/uur op /feedback)
        # heen: 8 pogingen, waarvan de laatste 3 al door DIE smalle limiet
        # geblokkeerd worden — nog lang niet in de buurt van de gedeelde
        # 30/uur-grens.
        bot_ip = "198.51.100.66"
        for i in range(8):
            resp = _feedback(logged_in_client, ip=bot_ip, i=i)
            if i < 5:
                assert "Bedankt voor je feedback" in resp.get_data(as_text=True)
            else:
                assert "Te veel pogingen" in resp.get_data(as_text=True)
        assert len(sent_emails) == 5  # alleen de eerste 5 zijn echt verstuurd

        # De kern van deze test: de GEDEELDE teller staat op 5, niet op 8.
        # De 3 door de per-IP-limiet geblokkeerde pogingen hebben 'm niet
        # aangeraakt.
        shared_count = _limiter._storage.get(
            f"LIMITER/{MAIL_GLOBAL_SCOPE}/{MAIL_GLOBAL_SCOPE}/30/1/hour"
        )
        assert shared_count == 5

        # En dus heeft een ander, legitiem IP nog steeds ruimschoots ruimte
        # in de gedeelde limiet — de bot heeft dat budget niet opgesoupeerd
        # ondanks zijn 8 pogingen.
        legit_ip = "203.0.113.200"
        resp = _feedback(logged_in_client, ip=legit_ip, i=100)
        assert "Bedankt voor je feedback" in resp.get_data(as_text=True)
        assert len(sent_emails) == 6


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
