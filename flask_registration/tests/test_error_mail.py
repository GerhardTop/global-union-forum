"""
Test-suite voor de dedup van 500-error-mails (_should_send_error_mail in
app/__init__.py) en de errorhandler(500) die 'm gebruikt.

Context: send_error_email() had géén rate limiting — een herhaaldelijk
crashend endpoint kon zo het Resend-quotum opsouperen. _should_send_error_mail
staat hooguit 1 mail per exception-type per _ERROR_MAIL_COOLDOWN_SECONDS toe.
"""
import pytest

import app as app_module
import app.mail as mail_module


@pytest.fixture(autouse=True)
def _clear_error_mail_cache():
    """Voorkom lekken van cooldown-state tussen tests (module-level dict)."""
    app_module._error_mail_last_sent.clear()
    yield
    app_module._error_mail_last_sent.clear()


class TestShouldSendErrorMailUnit:
    """Pure-functie tests, met gecontroleerde tijdstippen — geen Flask nodig."""

    def test_eerste_keer_voor_een_exception_type_mag_altijd(self):
        assert app_module._should_send_error_mail("ValueError", now=1000.0) is True

    def test_binnen_cooldown_wordt_onderdrukt(self):
        assert app_module._should_send_error_mail("ValueError", now=1000.0) is True
        # 5 minuten later, binnen het venster van 15 minuten
        assert app_module._should_send_error_mail("ValueError", now=1000.0 + 300) is False

    def test_na_verstrijken_cooldown_mag_weer(self):
        assert app_module._should_send_error_mail("ValueError", now=1000.0) is True
        cooldown = app_module._ERROR_MAIL_COOLDOWN_SECONDS
        assert app_module._should_send_error_mail("ValueError", now=1000.0 + cooldown + 1) is True

    def test_verschillend_exception_type_heeft_eigen_cooldown(self):
        assert app_module._should_send_error_mail("ValueError", now=1000.0) is True
        # Ander type, zelfde tijdstip: mag, want eigen teller.
        assert app_module._should_send_error_mail("TypeError", now=1000.0) is True
        # Het eerste type zit nog wel binnen zijn eigen venster.
        assert app_module._should_send_error_mail("ValueError", now=1000.1) is False


class TestServerErrorHandlerIntegration:
    """
    Simuleert een herhaaldelijk crashend endpoint via de echte 500-handler:
    server_error() moet send_error_email precies 1x aanroepen binnen het
    cooldown-venster, en die aanroep onderdrukken zolang het venster loopt.
    """

    @pytest.fixture(autouse=True)
    def _boom_route(self, app):
        """Registreert eenmalig een route die op verzoek een exception gooit."""
        from flask import request

        def _boom():
            exc_name = request.args.get("exc", "ValueError")
            exc_cls = {"ValueError": ValueError, "TypeError": TypeError}[exc_name]
            raise exc_cls("kaboom — gesimuleerde herhaaldelijke crash")

        if "test_boom" not in app.view_functions:
            app.add_url_rule("/__test_boom__", "test_boom", _boom)

    @pytest.fixture()
    def propagate_off(self, app):
        """
        TESTING=True laat exceptions normaal doorstromen naar de test i.p.v.
        naar de geregistreerde errorhandler (Flask-gedrag). PROPAGATE_EXCEPTIONS
        expliciet op False forceert dat de echte errorhandler(500) — met de
        nieuwe dedup-logica — wordt aangeroepen, zoals in productie.
        """
        original = app.config.get("PROPAGATE_EXCEPTIONS")
        app.config["PROPAGATE_EXCEPTIONS"] = False
        yield
        app.config["PROPAGATE_EXCEPTIONS"] = original

    def test_herhaalde_crash_stuurt_maar_1x_mail_binnen_venster(
        self, client, propagate_off, monkeypatch
    ):
        calls = []
        monkeypatch.setattr(
            mail_module, "send_error_email",
            lambda error, traceback_str: calls.append(error),
        )

        r1 = client.get("/__test_boom__")
        assert r1.status_code == 500
        assert len(calls) == 1

        # Zelfde exception-type, meteen erna: onderdrukt door de cooldown.
        r2 = client.get("/__test_boom__")
        assert r2.status_code == 500
        assert len(calls) == 1

        r3 = client.get("/__test_boom__")
        assert r3.status_code == 500
        assert len(calls) == 1

    def test_na_verlopen_venster_mag_er_weer_1_mail(
        self, client, propagate_off, monkeypatch
    ):
        calls = []
        monkeypatch.setattr(
            mail_module, "send_error_email",
            lambda error, traceback_str: calls.append(error),
        )

        assert client.get("/__test_boom__").status_code == 500
        assert len(calls) == 1

        # Cooldown handmatig laten verlopen door de opgeslagen timestamp
        # terug te zetten (geen echte 15 minuten wachten in de testsuite).
        app_module._error_mail_last_sent["ValueError"] -= (
            app_module._ERROR_MAIL_COOLDOWN_SECONDS + 1
        )

        assert client.get("/__test_boom__").status_code == 500
        assert len(calls) == 2

    def test_ander_exception_type_krijgt_eigen_mail(
        self, client, propagate_off, monkeypatch
    ):
        calls = []
        monkeypatch.setattr(
            mail_module, "send_error_email",
            lambda error, traceback_str: calls.append(error),
        )

        assert client.get("/__test_boom__", query_string={"exc": "ValueError"}).status_code == 500
        assert client.get("/__test_boom__", query_string={"exc": "TypeError"}).status_code == 500
        assert len(calls) == 2
