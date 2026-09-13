"""
Test-suite voor de anti-bot-bescherming op /uitnodiging: @login_required +
honeypot + globale (niet per-IP) rate limit bovenop de bestaande per-IP
limiet van 5/uur.

Bewijst dat:
  0. een anonieme aanvraag /uitnodiging niet eens in komt — @login_required
     stuurt 'm naar de loginpagina, vóór honeypot/rate-limit-logica ooit
     aangeraakt wordt (dus het oorspronkelijke IP-rotatie-misbruik, dat
     volledig anoniem liep, is hiermee al gestopt);
  1. de honeypot een gevulde poging (van een ingelogde afzender) stil
     blokkeert (geen mail, wél "succes"-ogende redirect, zodat een geautoma-
     tiseerd/gecompromitteerd account niet leert dat het gefilterd wordt);
  2. de globale 20/uur-teller toeslaat zodra het totaal over ALLE IP's heen
     de grens bereikt — ook voor een IP dat zelf nog nooit eerder langskwam
     en dus ver onder zijn eigen per-IP-limiet zit (het vangnet ná login,
     voor het geval een account zelf misbruikt wordt).

Eigen app-fixture i.p.v. de gedeelde sessie-fixture uit conftest.py — en
in tegenstelling tot test_auth.py/test_verify_bypass.py/test_username.py
zetten we RATELIMIT_ENABLED hier expliciet op True (i.p.v. False): dat is
precies het gedrag dat deze test moet verifiëren.

/uitnodiging vereist sinds @login_required alleen nog ingelogde leden — de
tests loggen daarom eerst een geverifieerde gebruiker in via de echte
/login-route (één sessie-cookie voor de hele test), en simuleren IP-rotatie
puur via REMOTE_ADDR-overrides per request; de sessie-cookie blijft
ondertussen gewoon hetzelfde (een bot zou dus niet zomaar 'toevallig'
ingelogd zijn — dat is precies waarom dit scenario nu een geldig account
vereist en geen anonieme aanvraag meer is).
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

# app/routes/__init__.py doet `from app.routes.social import social`, wat het
# attribuut app.routes.social overschrijft met de Blueprint i.p.v. de module
# — importlib.import_module() haalt de echte module op via sys.modules, zodat
# we send_email hieronder kunnen monkeypatchen.
social_module = importlib.import_module("app.routes.social")


@pytest.fixture
def app():
    orig_db_uri = config_module.Config.SQLALCHEMY_DATABASE_URI
    # config_module.Config is één gedeelde klasse over de hele pytest-sessie:
    # test_auth.py/test_verify_bypass.py/test_username.py zetten
    # RATELIMIT_ENABLED=False en zetten dat nooit terug — dus als deze
    # testfile ná zo'n testfile draait, staat het attribuut hier nog steeds
    # op False tenzij we het expliciet terugzetten. Expliciet op True (i.p.v.
    # "gewoon niet aanraken") maakt deze test onafhankelijk van de volgorde
    # waarin pytest de testfiles oppakt.
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
def client(app):
    return app.test_client()


@pytest.fixture
def logged_in_client(app, client):
    """Een testclient met een ingelogde, geverifieerde gebruiker — nodig
    sinds /uitnodiging @login_required vereist. Eén sessie-cookie voor de
    hele test; de REMOTE_ADDR-simulatie per request in _invite() staat hier
    los van."""
    with app.app_context():
        user = User(
            username="uitnodiger",
            first_name="Uit",
            last_name="Nodiger",
            email="uitnodiger@example.com",
            password_hash=bcrypt.generate_password_hash("Sterk1!ww").decode("utf-8"),
            verified=True,
        )
        db.session.add(user)
        db.session.commit()
    client.post(
        "/login",
        data={"email": "uitnodiger@example.com", "password": "Sterk1!ww"},
        follow_redirects=True,
    )
    with client.session_transaction() as sess:
        assert "_user_id" in sess  # sanity check: login moet echt gelukt zijn
    return client


@pytest.fixture
def sent_emails(monkeypatch):
    """Vervangt send_email door een stub die niets verstuurt maar wel telt —
    zodat we kunnen bewijzen dat een geblokkeerde poging de mail-verzending
    nooit bereikt, zonder echt tegen Resend aan te lopen."""
    calls = []

    def _fake_send_email(to, subject, html):
        calls.append(to)
        return True

    monkeypatch.setattr(social_module, "send_email", _fake_send_email)
    return calls


def _invite(client, *, ip, email="vriend@example.com", website=""):
    """POST naar /uitnodiging vanaf een gesimuleerd IP-adres. environ_overrides
    zet REMOTE_ADDR — dezelfde bron die flask-limiter's get_remote_address()
    en de honeypot/rate-limit-logica gebruiken."""
    return client.post(
        "/uitnodiging",
        data={
            "lang": "nl",
            "invite_email": email,
            "invite_message": "",
            "website": website,  # honeypot-veld
        },
        environ_overrides={"REMOTE_ADDR": ip},
        follow_redirects=True,
    )


class TestUitnodigingAntiBot:

    def test_honeypot_blokkeert_stil_zonder_mail(self, logged_in_client, sent_emails):
        """Een gevulde honeypot moet ogen als succes maar geen mail versturen."""
        resp = _invite(logged_in_client, ip="10.0.0.1", website="http://spam.example")

        assert resp.status_code == 200
        assert sent_emails == []  # geen enkele mail verstuurd
        assert "Uitnodiging verstuurd" in resp.get_data(as_text=True)

    def test_globale_limiet_stopt_ip_rotatie(self, logged_in_client, sent_emails):
        """
        Simuleert IP-rotatie: 20 legitiem-ogende aanvragen, elk vanaf een
        ANDER IP (dus nooit méér dan 1 per IP — ver onder de per-IP-limiet
        van 5/uur), allemaal binnen dezelfde ingelogde sessie (@login_required
        blokkeert een écht anonieme bot allang, vóórdat de limiter ook maar
        geraakt wordt — dit test dus het vangnet ná die eerste horde, bv. een
        gescript/gecompromitteerd account). De 21e aanvraag komt van weer een
        nieuw, nog nooit gebruikt IP — dat IP heeft zelf 0/5 verbruikt, maar
        moet toch geblokkeerd worden omdat de GLOBALE teller (20/uur, over
        alle IP's heen) vol is.
        """
        for i in range(20):
            resp = _invite(logged_in_client, ip=f"203.0.113.{i}", email=f"doelwit{i}@example.com")
            assert resp.status_code == 200
            assert "Uitnodiging verstuurd" in resp.get_data(as_text=True)
        assert len(sent_emails) == 20

        # 21e aanvraag: gloednieuw IP, nog nooit eerder gezien.
        resp = _invite(logged_in_client, ip="203.0.113.250", email="doelwit-21@example.com")

        # De globale limiet moet toeslaan: geen mail verstuurd (teller bleef
        # op 20 staan) en de "te veel pogingen"-melding verschijnt, ondanks
        # dat dit IP zijn eigen per-IP-limiet nooit heeft aangeraakt.
        assert len(sent_emails) == 20
        assert "Te veel pogingen" in resp.get_data(as_text=True)

    def test_per_ip_limiet_blijft_ook_gewoon_werken(self, logged_in_client, sent_emails):
        """Bestaand gedrag mag niet stilzwijgend verdwenen zijn: 1 IP dat
        6x achter elkaar post, loopt nog steeds tegen de 5/uur per-IP-limiet
        aan (los van de globale teller)."""
        ip = "198.51.100.7"
        for i in range(5):
            resp = _invite(logged_in_client, ip=ip, email=f"buur{i}@example.com")
            assert "Uitnodiging verstuurd" in resp.get_data(as_text=True)
        assert len(sent_emails) == 5

        resp = _invite(logged_in_client, ip=ip, email="buur-zesde@example.com")
        assert len(sent_emails) == 5  # 6e vanaf hetzelfde IP niet verstuurd
        assert "Te veel pogingen" in resp.get_data(as_text=True)

    def test_login_required_stuurt_anonieme_aanvraag_naar_login(self, client):
        """De kern van deze wijziging: zonder ingelogde sessie kom je /uitnodiging
        niet eens in — @login_required redirect naar de loginpagina, nog vóór
        de honeypot- of rate-limit-logica ooit aangeraakt wordt."""
        resp = client.post(
            "/uitnodiging",
            data={"lang": "nl", "invite_email": "iemand@example.com",
                  "invite_message": "", "website": ""},
            environ_overrides={"REMOTE_ADDR": "203.0.113.99"},
        )
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]
