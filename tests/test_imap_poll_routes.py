from unittest.mock import patch

import pytest

from web.app import app


@pytest.fixture
def client(tmp_path):
    app.config["TESTING"] = True
    app.config["DATA_DIR"] = tmp_path
    app.config["SECRET_KEY"] = "test-secret"
    with app.test_client() as c:
        yield c


# --- POST /imap-poll (Dashboard-Button) ---

def test_imap_poll_ohne_konfiguration_redirected_auf_dashboard(client):
    response = client.post("/imap-poll")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")


def test_imap_poll_ohne_konfiguration_zeigt_flash(client):
    response = client.post("/imap-poll", follow_redirects=True)

    assert "Bitte zuerst IMAP-Host in den Einstellungen eintragen." in response.data.decode()


def test_imap_poll_erfolg_redirected_auf_dashboard(client):
    with patch("web.app.get_settings", return_value={"imap_host": "imap.example.com"}), \
         patch("web.imap_poller.poll_inbox", return_value=3):
        response = client.post("/imap-poll")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")


def test_imap_poll_erfolg_zeigt_anzahl_neuer_nachrichten(client):
    with patch("web.app.get_settings", return_value={"imap_host": "imap.example.com"}), \
         patch("web.imap_poller.poll_inbox", return_value=3):
        response = client.post("/imap-poll", follow_redirects=True)

    assert "3 neue Nachricht(en) abgerufen." in response.data.decode()


def test_imap_poll_keine_neuen_nachrichten_zeigt_flash(client):
    with patch("web.app.get_settings", return_value={"imap_host": "imap.example.com"}), \
         patch("web.imap_poller.poll_inbox", return_value=0):
        response = client.post("/imap-poll", follow_redirects=True)

    assert "Keine neuen Nachrichten im Posteingang." in response.data.decode()


def test_imap_poll_fehler_redirected_auf_dashboard(client):
    with patch("web.app.get_settings", return_value={"imap_host": "imap.example.com"}), \
         patch("web.imap_poller.poll_inbox", side_effect=TimeoutError("timed out")):
        response = client.post("/imap-poll")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")


def test_imap_poll_fehler_zeigt_fehlermeldung(client):
    with patch("web.app.get_settings", return_value={"imap_host": "imap.example.com"}), \
         patch("web.imap_poller.poll_inbox", side_effect=TimeoutError("timed out")):
        response = client.post("/imap-poll", follow_redirects=True)

    assert "IMAP-Fehler – TimeoutError: timed out" in response.data.decode()


# --- POST /settings/imap-poll bleibt unverändert (Regression) ---

def test_settings_imap_poll_redirected_weiterhin_auf_settings(client):
    response = client.post("/settings/imap-poll")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/settings")


def test_settings_imap_poll_erfolg_redirected_auf_settings(client):
    with patch("web.app.get_settings", return_value={"imap_host": "imap.example.com"}), \
         patch("web.imap_poller.poll_inbox", return_value=2):
        response = client.post("/settings/imap-poll")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/settings")


def test_settings_imap_poll_fehler_zeigt_fehlermeldung(client):
    with patch("web.app.get_settings", return_value={"imap_host": "imap.example.com"}), \
         patch("web.imap_poller.poll_inbox", side_effect=TimeoutError("timed out")):
        response = client.post("/settings/imap-poll", follow_redirects=True)

    assert "IMAP-Fehler – TimeoutError: timed out" in response.data.decode()
