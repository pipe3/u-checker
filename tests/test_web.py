import io
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


# --- Startseite ---

def test_index_erreichbar(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Untersuchungs-Checker" in response.data.decode()


def test_index_zeigt_kein_xls_hinweis_wenn_keine_datei(client):
    response = client.get("/")
    assert "Noch keine Datei hochgeladen" in response.data.decode()


# --- Upload ---

def test_upload_speichert_datei(client, tmp_path):
    datei = (io.BytesIO(b"dummy xls content"), "export.xls")
    response = client.post("/upload", data={"xls_datei": datei}, content_type="multipart/form-data", follow_redirects=True)
    assert response.status_code == 200
    assert b"erfolgreich hochgeladen" in response.data
    assert (tmp_path / "latest.xls").exists()


def test_upload_ohne_datei_zeigt_fehler(client):
    response = client.post("/upload", data={}, follow_redirects=True)
    assert b"Keine Datei" in response.data


def test_upload_falscher_dateityp_zeigt_fehler(client):
    datei = (io.BytesIO(b"text"), "export.txt")
    response = client.post("/upload", data={"xls_datei": datei}, content_type="multipart/form-data", follow_redirects=True)
    assert b"XLS" in response.data


def test_upload_xlsx_wird_abgelehnt(client, tmp_path):
    datei = (io.BytesIO(b"xlsx content"), "export.xlsx")
    response = client.post("/upload", data={"xls_datei": datei}, content_type="multipart/form-data", follow_redirects=True)
    assert b"XLS" in response.data
    assert not (tmp_path / "latest.xls").exists()


def test_upload_speichert_originalen_dateinamen(client, tmp_path):
    datei = (io.BytesIO(b"dummy xls content"), "mp_feuer_export.xls")
    client.post("/upload", data={"xls_datei": datei}, content_type="multipart/form-data", follow_redirects=True)
    name_file = tmp_path / "latest_name.txt"
    assert name_file.exists()
    assert name_file.read_text(encoding="utf-8").strip() == "mp_feuer_export.xls"


# --- Task-Liste ---

def test_index_zeigt_tasks_bereich(client):
    response = client.get("/")
    assert b"Eingehende Nachweise" in response.data


def test_index_zeigt_badge_mit_null_wenn_keine_tasks(client):
    response = client.get("/")
    assert response.status_code == 200
    # Badge-Zähler muss 0 zeigen wenn keine offenen Tasks
    assert b"0" in response.data


def test_index_badge_zaehlt_neu_tasks(client, tmp_path):
    import sqlite3
    from datetime import datetime

    # Erster Request initialisiert die DB
    client.get("/")

    db_path = tmp_path / "checker.db"
    db = sqlite3.connect(db_path)
    db.execute("""
        INSERT INTO tasks (status, empfangen_am, von_email, betreff)
        VALUES ('NEU', ?, 'sender@example.com', 'Test Nachweis')
    """, (datetime.now().isoformat(timespec="seconds"),))
    db.commit()
    db.close()

    response = client.get("/")
    assert b"1" in response.data


def test_task_als_erledigt_markieren(client, tmp_path):
    import sqlite3
    from datetime import datetime

    # Erster Request initialisiert die DB
    client.get("/")

    db_path = tmp_path / "checker.db"
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    db.execute("""
        INSERT INTO tasks (status, empfangen_am, von_email, betreff)
        VALUES ('NEU', ?, 'sender@example.com', 'Test Nachweis')
    """, (datetime.now().isoformat(timespec="seconds"),))
    db.commit()
    task_id = db.execute("SELECT id FROM tasks LIMIT 1").fetchone()["id"]
    db.close()

    response = client.post(f"/tasks/{task_id}/erledigt", follow_redirects=True)
    assert response.status_code == 200

    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
    db.close()
    assert row["status"] == "ERLEDIGT"


def test_task_erledigt_unbekannte_id_gibt_404(client):
    response = client.post("/tasks/9999/erledigt")
    assert response.status_code == 404



def test_index_zeigt_keine_run_elemente(client):
    """Startseite enthält keine Run-Elemente aus dem alten /run-Flow."""
    response = client.get("/")
    body = response.data.decode("utf-8")
    assert "Script ausführen" not in body
    assert "Dry-Run" not in body
    assert "/run" not in body


# --- XLS löschen: Issue #12 ---

def test_loeschen_entfernt_xls_und_name_datei(client, tmp_path):
    (tmp_path / "latest.xls").write_bytes(b"dummy")
    (tmp_path / "latest_name.txt").write_text("export.xls", encoding="utf-8")

    response = client.post("/upload/loeschen", follow_redirects=True)

    assert response.status_code == 200
    assert not (tmp_path / "latest.xls").exists()
    assert not (tmp_path / "latest_name.txt").exists()


def test_loeschen_zeigt_flash_meldung(client, tmp_path):
    (tmp_path / "latest.xls").write_bytes(b"dummy")

    response = client.post("/upload/loeschen", follow_redirects=True)

    assert b"gel\xc3\xb6scht" in response.data


def test_loeschen_ohne_datei_gibt_kein_fehler(client):
    response = client.post("/upload/loeschen", follow_redirects=True)
    assert response.status_code == 200
    assert b"gel\xc3\xb6scht" not in response.data


def test_loeschen_schaltflaeche_sichtbar_wenn_xls_vorhanden(client, tmp_path):
    (tmp_path / "latest.xls").write_bytes(b"dummy")
    (tmp_path / "latest_name.txt").write_text("export.xls", encoding="utf-8")

    response = client.get("/")
    assert b"loeschen" in response.data or b"l\xc3\xb6schen" in response.data.lower()


def test_loeschen_schaltflaeche_nicht_sichtbar_ohne_xls(client):
    response = client.get("/")
    body = response.data.decode("utf-8")
    assert "/upload/loeschen" not in body


# --- SMTP-Test: Issue #13 ---

def test_smtp_test_ruft_send_simple_mail_mit_zusammenfassung_an_auf(client, tmp_path):
    """POST /settings/smtp-test sendet Test-Mail an konfigurierte zusammenfassung_an-Adresse."""
    client.get("/")
    from web.app import save_settings
    with app.app_context():
        save_settings({
            "zusammenfassung_an": "admin@example.com",
            "smtp_host": "smtp.example.com",
            "smtp_port": "587",
            "smtp_user": "user",
            "smtp_password": "pass",
            "smtp_from": "from@example.com",
        })

    with patch("web.app.send_simple_mail") as mock_mail:
        response = client.post("/settings/smtp-test", follow_redirects=True)

    assert response.status_code == 200
    mock_mail.assert_called_once()
    call_kwargs = mock_mail.call_args
    to_addrs = call_kwargs[1]["to_addrs"] if call_kwargs[1] else call_kwargs[0][1]
    assert "admin@example.com" in to_addrs


def test_smtp_test_fehler_wenn_zusammenfassung_an_leer(client):
    """POST /settings/smtp-test zeigt Fehlermeldung wenn zusammenfassung_an nicht konfiguriert."""
    client.get("/")
    from web.app import save_settings
    with app.app_context():
        save_settings({"zusammenfassung_an": ""})

    with patch("web.app.send_simple_mail") as mock_mail:
        response = client.post("/settings/smtp-test", follow_redirects=True)

    assert response.status_code == 200
    mock_mail.assert_not_called()
    assert "Gesamtübersichts-Adresse" in response.data.decode("utf-8")


def test_smtp_test_fehlermeldung_bei_smtp_fehler(client, tmp_path):
    """POST /settings/smtp-test zeigt Fehlermeldung bei SMTP-Verbindungsfehler."""
    client.get("/")
    from web.app import save_settings
    with app.app_context():
        save_settings({"zusammenfassung_an": "admin@example.com"})

    import smtplib
    with patch("web.app.send_simple_mail", side_effect=smtplib.SMTPException("Verbindungsfehler")):
        response = client.post("/settings/smtp-test", follow_redirects=True)

    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "Verbindungsfehler" in body or "SMTP" in body

