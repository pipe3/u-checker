import io
import sqlite3
from pathlib import Path
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


def test_index_zeigt_keine_laeufe_hinweis_initial(client):
    response = client.get("/")
    assert "Noch keine Läufe" in response.data.decode()


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


# --- Run ---

def test_run_ohne_xls_zeigt_fehler(client):
    response = client.post("/run", data={}, follow_redirects=True)
    assert b"Keine XLS-Datei" in response.data


def test_run_legt_db_eintrag_an(client, tmp_path):
    xls_path = tmp_path / "latest.xls"
    xls_path.write_bytes(b"dummy")

    with patch("web.app.check_examinations", return_value=[]) as mock_check, \
         patch("web.app.send_notifications", return_value=0) as mock_send, \
         patch("web.app.send_summary") as mock_summary:
        response = client.post("/run", data={}, follow_redirects=True)

    assert response.status_code == 200
    mock_check.assert_called_once()
    mock_send.assert_called_once()

    db = sqlite3.connect(tmp_path / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    db.close()

    assert row["status"] == "fertig"
    assert row["personen_gefunden"] == 0
    assert row["emails_gesendet"] == 0
    assert row["dry_run"] == 0


def test_run_dry_run_speichert_flag(client, tmp_path):
    xls_path = tmp_path / "latest.xls"
    xls_path.write_bytes(b"dummy")

    with patch("web.app.check_examinations", return_value=[]), \
         patch("web.app.send_notifications", return_value=0), \
         patch("web.app.send_summary"):
        client.post("/run", data={"dry_run": "1"}, follow_redirects=True)

    db = sqlite3.connect(tmp_path / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    db.close()

    assert row["dry_run"] == 1


def test_run_speichert_fehler_bei_exception(client, tmp_path):
    xls_path = tmp_path / "latest.xls"
    xls_path.write_bytes(b"dummy")

    with patch("web.app.check_examinations", side_effect=ValueError("Testfehler")):
        response = client.post("/run", data={}, follow_redirects=True)

    assert b"Fehler" in response.data

    db = sqlite3.connect(tmp_path / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    db.close()

    assert row["status"] == "fehler"
    assert "Testfehler" in row["fehlermeldung"]


def test_run_zeigt_anzahl_in_erfolgsmeldung(client, tmp_path):
    from datetime import date, timedelta
    from u_checker.checker import Person, Pruefung

    xls_path = tmp_path / "latest.xls"
    xls_path.write_bytes(b"dummy")

    persons = [
        Person(pers_nr="001", vorname="Max", nachname="Muster", email="max@example.com", pruefungen=[
            Pruefung(typ="G25", beschreibung="G25", datum=date.today() - timedelta(days=1), status="abgelaufen"),
        ])
    ]

    with patch("web.app.check_examinations", return_value=persons), \
         patch("web.app.send_notifications", return_value=1), \
         patch("web.app.send_summary"):
        response = client.post("/run", data={}, follow_redirects=True)

    assert b"1 Person" in response.data
    assert b"1 E-Mail" in response.data


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
