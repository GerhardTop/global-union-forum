"""
Test-suite voor app/rate_limit_storage.py (SQLStorage) — de persistente,
SQL-backed vervanger van Flask-Limiter's in-memory teller (zie de module-
docstring daar voor de volledige aanleiding: in-memory tellers overleven
geen container-herstart op Render).

Dekt:
  - basiscontract (incr/get/get_expiry/clear/reset), fixed-window-gedrag
    (venster reset pas ná expiry, niet bij elke hit);
  - PERSISTENTIE: een teller die "halverwege" zit, overleeft het volledig
    weggooien en opnieuw opbouwen van de storage/engine (= een container-
    herstart) — inclusief de oorspronkelijke expiry, die niet opnieuw mag
    beginnen te lopen;
  - FAIL-OPEN: als de database niet bereikbaar is, laat incr()/get() het
    verzoek door (i.p.v. de fout op te gooien) én logt dat duidelijk;
  - hetzelfde, maar dan end-to-end via twee losse create_app()-aanroepen
    tegen hetzelfde sqlite-bestand — de letterlijke 'app-context opnieuw
    opbouwen'-proef.

Sqlite-BESTAND (niet ':memory:') is hier bewust: een ':memory:'-database
bestaat alleen binnen de connectie die hem aanmaakte en simuleert dus geen
herstart — een bestand op schijf overleeft een nieuwe engine/connectie
precies zoals Neon een nieuwe gunicorn-container overleeft.
"""
import importlib
import logging
import os
import sys
import time

import pytest
import sqlalchemy as sa

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import config as config_module
from app.rate_limit_storage import SQLStorage

social_module = importlib.import_module("app.routes.social")


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "ratelimit_test.sqlite3")


@pytest.fixture
def db_uri(db_path):
    return f"sqlite:///{db_path}"


class TestSQLStorageBasics:

    def test_incr_telt_op_binnen_hetzelfde_venster(self, db_uri):
        storage = SQLStorage(db_uri)
        assert storage.incr("k1", 3600, 1) == 1
        assert storage.incr("k1", 3600, 1) == 2
        assert storage.incr("k1", 3600, 3) == 5
        assert storage.get("k1") == 5

    def test_venster_reset_pas_na_expiry(self, db_uri, monkeypatch):
        storage = SQLStorage(db_uri)
        now = [1_000_000.0]
        monkeypatch.setattr(time, "time", lambda: now[0])

        assert storage.incr("k2", 10, 1) == 1
        now[0] += 5  # nog binnen het venster van 10s
        assert storage.incr("k2", 10, 1) == 2
        now[0] += 11  # venster verlopen
        assert storage.incr("k2", 10, 1) == 1  # begint opnieuw, niet 3

    def test_get_en_get_expiry_op_onbekende_sleutel(self, db_uri):
        storage = SQLStorage(db_uri)
        assert storage.get("nooit-geraakt") == 0
        # get_expiry() op een onbekende sleutel: 'nu' (net als MemoryStorage),
        # niet een fout of een ver-in-de-toekomst-waarde.
        assert abs(storage.get_expiry("nooit-geraakt") - time.time()) < 2

    def test_clear_verwijdert_alleen_die_ene_sleutel(self, db_uri):
        storage = SQLStorage(db_uri)
        storage.incr("a", 3600, 1)
        storage.incr("b", 3600, 1)
        storage.clear("a")
        assert storage.get("a") == 0
        assert storage.get("b") == 1

    def test_reset_verwijdert_alles(self, db_uri):
        storage = SQLStorage(db_uri)
        storage.incr("a", 3600, 1)
        storage.incr("b", 3600, 1)
        storage.reset()
        assert storage.get("a") == 0
        assert storage.get("b") == 0

    def test_check_gezond(self, db_uri):
        assert SQLStorage(db_uri).check() is True


class TestPersistentieOverHerstart:
    """De kern van deze feature: overleeft de teller een 'container-herstart'?"""

    def test_teller_blijft_staan_na_volledig_opnieuw_opbouwen(self, db_uri):
        storage1 = SQLStorage(db_uri)
        assert storage1.incr("bot-ip", 3600, 1) == 1
        assert storage1.incr("bot-ip", 3600, 1) == 2
        storage1._engine.dispose()  # simuleert het proces dat stopt

        # Nieuwe storage, nieuwe engine, geen enkele Python-state gedeeld met
        # storage1 — alleen het sqlite-bestand op schijf is gemeenschappelijk.
        # Dit IS een container-herstart: als dit slaagt, telt de vervolgtree
        # door i.p.v. terug naar 0 (wat het hele lek uit de vorige aanpak was).
        storage2 = SQLStorage(db_uri)
        assert storage2.get("bot-ip") == 2
        assert storage2.incr("bot-ip", 3600, 1) == 3

    def test_expiry_blijft_op_het_oorspronkelijke_moment_na_herstart(self, db_uri):
        """
        Eis B: niet alleen de tellerstand, ook het verval-moment moet
        overleven — een teller die halverwege zijn venster zit, mag na een
        herstart niet een vers venster beginnen (dat zou een bot een gratis
        nieuw budget geven bij elke herstart).
        """
        storage1 = SQLStorage(db_uri)
        t0 = time.time()
        storage1.incr("half-venster", 3600, 1)  # venster van 1 uur
        expiry_voor_herstart = storage1.get_expiry("half-venster")
        assert abs(expiry_voor_herstart - (t0 + 3600)) < 2
        storage1._engine.dispose()

        # 'Herstart': nieuwe storage/engine, zelfde bestand.
        storage2 = SQLStorage(db_uri)
        expiry_na_herstart = storage2.get_expiry("half-venster")
        # Exact dezelfde waarde als vóór herstart (opgeslagen in de rij, niet
        # herberekend) — geen nieuw venster.
        assert expiry_na_herstart == expiry_voor_herstart

        # Nog een hit ná herstart mag het venster nog steeds niet verlengen
        # (fixed-window: alleen de counter gaat omhoog zolang het venster
        # loopt, expires_at blijft ongewijzigd).
        storage2.incr("half-venster", 3600, 1)
        assert storage2.get_expiry("half-venster") == expiry_voor_herstart


class TestFailOpen:
    """Eis A: DB onbereikbaar tijdens een check -> verzoek door, niet dicht."""

    def test_incr_faalt_open_en_logt(self, db_uri, caplog):
        storage = SQLStorage(db_uri)

        def _boom(*a, **kw):
            raise sa.exc.OperationalError("insert ...", {}, Exception("connectie geweigerd"))

        storage._engine.begin = _boom  # simuleert een onbereikbare DB

        with caplog.at_level(logging.ERROR, logger="app.rate_limit_storage"):
            result = storage.incr("ip-tijdens-storing", 3600, 1)

        # Fail-open: 0 betekent voor FixedWindowRateLimiter.hit() altijd
        # 'binnen de limiet' (0 <= elke limiet), dus het verzoek gaat door.
        assert result == 0
        assert any("RATE-LIMIT" in r.message for r in caplog.records)
        assert any("onbereikbaar" in r.message for r in caplog.records)

    def test_get_faalt_open_en_logt(self, db_uri, caplog):
        storage = SQLStorage(db_uri)

        def _boom(*a, **kw):
            raise sa.exc.OperationalError("select ...", {}, Exception("timeout"))

        storage._engine.connect = _boom

        with caplog.at_level(logging.ERROR, logger="app.rate_limit_storage"):
            result = storage.get("ip-tijdens-storing")

        assert result == 0
        assert any("RATE-LIMIT" in r.message for r in caplog.records)

    def test_hit_via_fixedwindowratelimiter_gaat_door_bij_db_storing(self, db_uri):
        """Niet alleen de storage-methode zelf, ook de echte flask-limiter-
        strategie die 'm aanroept moet het verzoek toelaten."""
        from limits import RateLimitItemPerHour
        from limits.strategies import FixedWindowRateLimiter

        storage = SQLStorage(db_uri)
        storage._engine.begin = lambda *a, **kw: (_ for _ in ()).throw(
            sa.exc.OperationalError("insert ...", {}, Exception("weg"))
        )
        limiter = FixedWindowRateLimiter(storage)
        item = RateLimitItemPerHour(5)  # 5/uur
        # Zou zonder fail-open een StorageError laten opborrelen; met
        # fail-open geeft hit() gewoon True terug (verzoek toegestaan).
        assert limiter.hit(item, "ip-tijdens-storing") is True


class TestAppRebuildEndToEnd:
    """
    De letterlijke proef: twee aparte create_app()-aanroepen (twee losse
    Flask-apps/engines, zoals twee opeenvolgende containers) tegen dezelfde
    RATELIMIT_STORAGE_URI, en de teller/expiry lopen gewoon door.
    """

    def _build_app(self, sqlite_uri):
        orig_db_uri = config_module.Config.SQLALCHEMY_DATABASE_URI
        orig_ratelimit_enabled = getattr(config_module.Config, "RATELIMIT_ENABLED", True)
        config_module.Config.SQLALCHEMY_DATABASE_URI = sqlite_uri
        config_module.Config.TESTING = True
        config_module.Config.WTF_CSRF_ENABLED = False
        config_module.Config.RATELIMIT_ENABLED = True
        try:
            from app import create_app
            return create_app()
        finally:
            config_module.Config.SQLALCHEMY_DATABASE_URI = orig_db_uri
            config_module.Config.RATELIMIT_ENABLED = orig_ratelimit_enabled

    def test_teller_en_expiry_overleven_create_app_herstart(self, tmp_path, monkeypatch):
        # Eigen databasebestand voor de APPDATA (users etc.), los van het
        # sqlite-bestand voor de rate-limit-teller: create_app() draait
        # db.create_all()/_seed_forum() etc. — dat willen we niet laten
        # botsen met de rate_limit_counters-tabel qua metadata/bindings.
        app_db_uri = f"sqlite:///{tmp_path / 'app.sqlite3'}"
        ratelimit_uri = f"sqlite:///{tmp_path / 'ratelimit.sqlite3'}"

        calls = []
        monkeypatch.setattr(social_module, "send_email", lambda to, s, h: (calls.append(to), True)[1])

        # "Container 1"
        flask_app1 = self._build_app(app_db_uri)
        # RATELIMIT_STORAGE_URI wordt in create_app() afgeleid van
        # SQLALCHEMY_DATABASE_URI (de appdata-DB) — voor deze end-to-end-
        # proef willen we 'm los daarvan naar ons eigen ratelimit-bestand
        # laten wijzen, dus overschrijven we 'm hier na het bouwen en
        # initialiseren we de limiter-storage opnieuw met die URI.
        flask_app1.config["RATELIMIT_STORAGE_URI"] = ratelimit_uri
        from app import limiter as _limiter
        _limiter._storage = SQLStorage(ratelimit_uri)
        _limiter._limiter = type(_limiter._limiter)(_limiter._storage)

        with flask_app1.app_context():
            client1 = flask_app1.test_client()
            ip = "192.0.2.50"
            for i in range(3):
                resp = client1.post(
                    "/uitnodiging",
                    data={"lang": "nl", "invite_email": f"a{i}@example.com",
                          "invite_message": "", "website": ""},
                    environ_overrides={"REMOTE_ADDR": ip},
                )
                assert resp.status_code == 302
        assert len(calls) == 3

        # Bewaar het verval-moment van de per-IP-sleutel vóór 'herstart'.
        # (Opgezocht via een directe query op de tabel i.p.v. geraden — zie
        # de opmerking in het testbestand hierboven: flask-limiter bouwt de
        # sleutel zelf op uit endpoint + scope + limietdefinitie.)
        key = f"LIMITER/{ip}/social.uitnodiging/5/1/hour"
        expiry_voor = _limiter._storage.get_expiry(key)
        assert expiry_voor > time.time()  # venster loopt nog

        # "Container 2": volledig nieuwe create_app(), zelfde ratelimit-URI.
        flask_app2 = self._build_app(app_db_uri)
        flask_app2.config["RATELIMIT_STORAGE_URI"] = ratelimit_uri
        _limiter._storage = SQLStorage(ratelimit_uri)
        _limiter._limiter = type(_limiter._limiter)(_limiter._storage)

        with flask_app2.app_context():
            client2 = flask_app2.test_client()
            # Nog 2 pogingen vanaf hetzelfde IP: 3 (van 'container 1') + 2 = 5
            # -> precies op de limiet, allebei nog toegestaan.
            for i in range(2):
                resp = client2.post(
                    "/uitnodiging",
                    data={"lang": "nl", "invite_email": f"b{i}@example.com",
                          "invite_message": "", "website": ""},
                    environ_overrides={"REMOTE_ADDR": ip},
                )
                assert resp.status_code == 302
            assert len(calls) == 5

            # De 6e (eerste ná de limiet) moet nu geblokkeerd worden — zou
            # bij een gereset teller (het oude in-memory-lek) gewoon
            # doorgegaan zijn.
            resp = client2.post(
                "/uitnodiging",
                data={"lang": "nl", "invite_email": "b-zesde@example.com",
                      "invite_message": "", "website": ""},
                environ_overrides={"REMOTE_ADDR": ip},
                follow_redirects=True,
            )
            assert len(calls) == 5  # niet verstuurd
            assert "Te veel pogingen" in resp.get_data(as_text=True)

        # En het verval-moment zelf is ongewijzigd overgekomen naar
        # 'container 2' — geen nieuw venster begonnen bij het herstart.
        expiry_na = _limiter._storage.get_expiry(key)
        assert expiry_na == expiry_voor
