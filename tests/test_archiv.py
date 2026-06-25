"""Tests für Archiv-Ansicht, PDF-Export und automatische Löschung."""
import email
import io
import sqlite3
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from unittest.mock import patch

import pytest

from web.app import app


@pytest.fixture
def client(tmp_path):
    app.config["TESTING"] = True
    app.config["DATA_DIR"] = tmp_path
    app.config["SECRET_KEY"] = "test-secret"

    with app.test_client() as c:
        c.get("/")  # DB initialisieren
        yield c


def _insert_task(tmp_path, status="ERLEDIGT", raw_email=None, erledigt_am=None):
    db = sqlite3.connect(tmp_path / "checker.db")
    db.row_factory = sqlite3.Row
    db.execute("""
        INSERT INTO tasks (status, empfangen_am, von_email, betreff, raw_email, erledigt_am)
        VALUES (?, ?, 'sender@example.com', 'Testnachweis G25', ?, ?)
    """, (
        status,
        datetime.now().isoformat(timespec="seconds"),
        raw_email,
        erledigt_am,
    ))
    db.commit()
    task_id = db.execute("SELECT id FROM tasks ORDER BY id DESC LIMIT 1").fetchone()["id"]
    db.close()
    return task_id


def _build_simple_raw_email(subject="Testnachweis", body="G25 gültig bis 31.12.2026"):
    msg = MIMEMultipart()
    msg["From"] = "Max Mustermann <max@example.com>"
    msg["To"] = "nachweise@feuerwehr.de"
    msg["Subject"] = subject
    msg["Date"] = "Mon, 01 Jan 2024 10:00:00 +0000"
    msg.attach(MIMEText(body, "plain", "utf-8"))
    return msg.as_bytes()


# ── Archiv-Ansicht ──────────────────────────────────────────────────────────

def test_archiv_erreichbar(client):
    response = client.get("/archiv")
    assert response.status_code == 200
    assert "Archiv" in response.data.decode()


def test_archiv_zeigt_erledigte_tasks(client, tmp_path):
    _insert_task(tmp_path, status="ERLEDIGT")
    response = client.get("/archiv")
    assert b"Testnachweis G25" in response.data


def test_archiv_zeigt_keine_offenen_tasks(client, tmp_path):
    _insert_task(tmp_path, status="NEU")
    response = client.get("/archiv")
    assert b"Testnachweis G25" not in response.data


def test_index_zeigt_archiv_link(client):
    response = client.get("/")
    assert b"archiv" in response.data.lower()


# ── erledigt_am wird gesetzt ────────────────────────────────────────────────

def test_task_erledigt_setzt_erledigt_am(client, tmp_path):
    task_id = _insert_task(tmp_path, status="NEU")

    before = datetime.now().replace(microsecond=0)
    client.post(f"/tasks/{task_id}/erledigt", follow_redirects=True)

    db = sqlite3.connect(tmp_path / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT erledigt_am FROM tasks WHERE id = ?", (task_id,)).fetchone()
    db.close()

    assert row["erledigt_am"] is not None
    erledigt_dt = datetime.fromisoformat(row["erledigt_am"])
    assert erledigt_dt >= before


# ── PDF-Export ──────────────────────────────────────────────────────────────

def test_pdf_download_gibt_pdf_zurueck(client, tmp_path):
    raw = _build_simple_raw_email()
    task_id = _insert_task(tmp_path, status="ERLEDIGT", raw_email=raw)

    response = client.get(f"/tasks/{task_id}/pdf")
    assert response.status_code == 200
    assert response.content_type == "application/pdf"
    assert response.data[:4] == b"%PDF"


def test_pdf_download_ohne_raw_email_gibt_404(client, tmp_path):
    task_id = _insert_task(tmp_path, status="ERLEDIGT", raw_email=None)
    response = client.get(f"/tasks/{task_id}/pdf")
    assert response.status_code == 404


def test_pdf_download_unbekannte_id_gibt_404(client):
    response = client.get("/tasks/9999/pdf")
    assert response.status_code == 404


def test_pdf_download_enthaelt_dateinamen(client, tmp_path):
    raw = _build_simple_raw_email(subject="G25 Nachweis Muster")
    task_id = _insert_task(tmp_path, status="ERLEDIGT", raw_email=raw)

    response = client.get(f"/tasks/{task_id}/pdf")
    assert response.status_code == 200
    cd = response.headers.get("Content-Disposition", "")
    assert "attachment" in cd
    assert ".pdf" in cd


# ── Auto-Löschung ───────────────────────────────────────────────────────────

def test_archiv_cleanup_loescht_alte_tasks(tmp_path):
    from web.app import app as flask_app, archiv_cleanup

    flask_app.config["TESTING"] = True
    flask_app.config["DATA_DIR"] = tmp_path

    with flask_app.app_context():
        flask_app.test_client().get("/")  # DB initialisieren

        alter_zeitpunkt = (datetime.now() - timedelta(days=400)).isoformat(timespec="seconds")
        task_id = _insert_task(tmp_path, status="ERLEDIGT", erledigt_am=alter_zeitpunkt)

        archiv_cleanup(archiv_tage=365)

        db = sqlite3.connect(tmp_path / "checker.db")
        row = db.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        db.close()
        assert row is None


def test_archiv_cleanup_behaelt_neue_tasks(tmp_path):
    from web.app import app as flask_app, archiv_cleanup

    flask_app.config["TESTING"] = True
    flask_app.config["DATA_DIR"] = tmp_path

    with flask_app.app_context():
        flask_app.test_client().get("/")

        junger_zeitpunkt = (datetime.now() - timedelta(days=10)).isoformat(timespec="seconds")
        task_id = _insert_task(tmp_path, status="ERLEDIGT", erledigt_am=junger_zeitpunkt)

        archiv_cleanup(archiv_tage=365)

        db = sqlite3.connect(tmp_path / "checker.db")
        row = db.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        db.close()
        assert row is not None


def test_archiv_cleanup_behaelt_offene_tasks(tmp_path):
    from web.app import app as flask_app, archiv_cleanup

    flask_app.config["TESTING"] = True
    flask_app.config["DATA_DIR"] = tmp_path

    with flask_app.app_context():
        flask_app.test_client().get("/")

        alter_zeitpunkt = (datetime.now() - timedelta(days=400)).isoformat(timespec="seconds")
        task_id = _insert_task(tmp_path, status="NEU", erledigt_am=alter_zeitpunkt)

        archiv_cleanup(archiv_tage=365)

        db = sqlite3.connect(tmp_path / "checker.db")
        row = db.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        db.close()
        assert row is not None
