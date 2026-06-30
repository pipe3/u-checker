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


def _insert_task(tmp_path, status="ERLEDIGT", raw_email=None, erledigt_am=None, imap_uid=None, message_id=None):
    db = sqlite3.connect(tmp_path / "checker.db")
    db.row_factory = sqlite3.Row
    db.execute("""
        INSERT INTO tasks (status, empfangen_am, von_email, betreff, raw_email, erledigt_am, imap_uid, message_id)
        VALUES (?, ?, 'sender@example.com', 'Testnachweis G25', ?, ?, ?, ?)
    """, (
        status,
        datetime.now().isoformat(timespec="seconds"),
        raw_email,
        erledigt_am,
        imap_uid,
        message_id,
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


# ── Löschen (Spam) ──────────────────────────────────────────────────────────

def test_loeschen_button_auf_nachweise_karte(client, tmp_path):
    _insert_task(tmp_path, status="NEU")
    response = client.get("/nachweise")
    assert b"loeschen" in response.data.lower()


def test_loeschen_entfernt_task_aus_db(client, tmp_path):
    task_id = _insert_task(tmp_path, status="NEU")
    response = client.post(f"/tasks/{task_id}/loeschen", follow_redirects=True)
    assert response.status_code == 200

    db = sqlite3.connect(tmp_path / "checker.db")
    row = db.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
    db.close()
    assert row is None


def test_loeschen_task_nicht_mehr_auf_nachweise(client, tmp_path):
    task_id = _insert_task(tmp_path, status="NEU")
    client.post(f"/tasks/{task_id}/loeschen", follow_redirects=True)
    response = client.get("/nachweise")
    assert b"Testnachweis G25" not in response.data


def test_loeschen_task_nicht_im_archiv(client, tmp_path):
    task_id = _insert_task(tmp_path, status="NEU")
    client.post(f"/tasks/{task_id}/loeschen", follow_redirects=True)
    response = client.get("/archiv")
    assert b"Testnachweis G25" not in response.data


def test_loeschen_unbekannte_id_gibt_404(client):
    response = client.post("/tasks/9999/loeschen")
    assert response.status_code == 404


def test_loeschen_erledigt_task_gibt_404(client, tmp_path):
    task_id = _insert_task(tmp_path, status="ERLEDIGT")
    response = client.post(f"/tasks/{task_id}/loeschen")
    assert response.status_code == 404

    db = sqlite3.connect(tmp_path / "checker.db")
    row = db.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
    db.close()
    assert row is not None


# ── Wieder öffnen ────────────────────────────────────────────────────────────

def test_wiederoeffnen_button_im_archiv(client, tmp_path):
    _insert_task(tmp_path, status="ERLEDIGT")
    response = client.get("/archiv")
    assert b"wiederoeffnen" in response.data.lower()


def test_wiederoeffnen_setzt_erledigt_am_auf_null(client, tmp_path):
    task_id = _insert_task(tmp_path, status="ERLEDIGT",
                           erledigt_am=datetime.now().isoformat(timespec="seconds"))
    response = client.post(f"/tasks/{task_id}/wiederoeffnen", follow_redirects=True)
    assert response.status_code == 200

    db = sqlite3.connect(tmp_path / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT erledigt_am FROM tasks WHERE id = ?", (task_id,)).fetchone()
    db.close()
    assert row["erledigt_am"] is None


def test_wiederoeffnen_erscheint_auf_nachweise(client, tmp_path):
    task_id = _insert_task(tmp_path, status="ERLEDIGT",
                           erledigt_am=datetime.now().isoformat(timespec="seconds"))
    client.post(f"/tasks/{task_id}/wiederoeffnen", follow_redirects=True)
    response = client.get("/nachweise")
    assert b"Testnachweis G25" in response.data


def test_wiederoeffnen_nicht_mehr_im_archiv(client, tmp_path):
    task_id = _insert_task(tmp_path, status="ERLEDIGT",
                           erledigt_am=datetime.now().isoformat(timespec="seconds"))
    client.post(f"/tasks/{task_id}/wiederoeffnen", follow_redirects=True)
    response = client.get("/archiv")
    assert b"Testnachweis G25" not in response.data


def test_wiederoeffnen_unbekannte_id_gibt_404(client):
    response = client.post("/tasks/9999/wiederoeffnen")
    assert response.status_code == 404


def test_wiederoeffnen_ohne_mitglied_setzt_unklare_zuordnung(client, tmp_path):
    """Task ohne Mitglied-Zuordnung erhält beim Wiedereröffnen UNKLARE_ZUORDNUNG zurück."""
    db = sqlite3.connect(tmp_path / "checker.db")
    db.execute("""
        INSERT INTO tasks (status, empfangen_am, von_email, betreff, mitglied_nr, erledigt_am)
        VALUES ('ERLEDIGT', ?, 'sender@example.com', 'Testnachweis G25', NULL, ?)
    """, (datetime.now().isoformat(timespec="seconds"), datetime.now().isoformat(timespec="seconds")))
    db.commit()
    task_id = db.execute("SELECT id FROM tasks ORDER BY id DESC LIMIT 1").fetchone()[0]
    db.close()

    client.post(f"/tasks/{task_id}/wiederoeffnen", follow_redirects=True)

    db = sqlite3.connect(tmp_path / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
    db.close()
    assert row["status"] == "UNKLARE_ZUORDNUNG"


def test_wiederoeffnen_mit_mitglied_setzt_neu(client, tmp_path):
    """Task mit gesetzter Mitglied-Nummer erhält beim Wiedereröffnen NEU zurück."""
    db = sqlite3.connect(tmp_path / "checker.db")
    db.execute("""
        INSERT INTO tasks (status, empfangen_am, von_email, betreff, mitglied_nr, erledigt_am)
        VALUES ('ERLEDIGT', ?, 'sender@example.com', 'Testnachweis G25', '12345', ?)
    """, (datetime.now().isoformat(timespec="seconds"), datetime.now().isoformat(timespec="seconds")))
    db.commit()
    task_id = db.execute("SELECT id FROM tasks ORDER BY id DESC LIMIT 1").fetchone()[0]
    db.close()

    client.post(f"/tasks/{task_id}/wiederoeffnen", follow_redirects=True)

    db = sqlite3.connect(tmp_path / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
    db.close()
    assert row["status"] == "NEU"


# ── IMAP-Sync bei Statuswechsel ─────────────────────────────────────────────

def test_task_erledigt_ruft_imap_move_auf(client, tmp_path):
    """Wenn imap_uid gesetzt ist, wird imap_move_to_nachweis aufgerufen."""
    task_id = _insert_task(tmp_path, status="NEU", imap_uid="42")

    with patch("web.imap_poller.imap_move_to_nachweis") as mock_move:
        client.post(f"/tasks/{task_id}/erledigt", follow_redirects=True)

    mock_move.assert_called_once()
    args = mock_move.call_args
    assert args[0][1] == "42"


def test_task_erledigt_ohne_uid_ueberspringt_imap(client, tmp_path):
    """Ohne imap_uid wird kein IMAP-Aufruf gemacht."""
    task_id = _insert_task(tmp_path, status="NEU", imap_uid=None)

    with patch("web.imap_poller.imap_move_to_nachweis") as mock_move:
        response = client.post(f"/tasks/{task_id}/erledigt", follow_redirects=True)

    assert response.status_code == 200
    mock_move.assert_not_called()


def test_task_erledigt_imap_fehler_blockiert_db_nicht(client, tmp_path):
    """IMAP-Fehler bei erledigt verhindert nicht die DB-Aktualisierung."""
    task_id = _insert_task(tmp_path, status="NEU", imap_uid="42")

    with patch("web.imap_poller.imap_move_to_nachweis", side_effect=RuntimeError("IMAP down")):
        client.post(f"/tasks/{task_id}/erledigt", follow_redirects=True)

    db = sqlite3.connect(tmp_path / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
    db.close()
    assert row["status"] == "ERLEDIGT"


def test_task_wiederoeffnen_ruft_imap_move_auf(client, tmp_path):
    """Wenn imap_uid und message_id gesetzt sind, wird imap_move_to_inbox aufgerufen."""
    task_id = _insert_task(
        tmp_path, status="ERLEDIGT",
        imap_uid="42", message_id="<test@example.com>",
        erledigt_am=datetime.now().isoformat(timespec="seconds"),
    )

    with patch("web.imap_poller.imap_move_to_inbox") as mock_move:
        client.post(f"/tasks/{task_id}/wiederoeffnen", follow_redirects=True)

    mock_move.assert_called_once()
    args = mock_move.call_args
    assert args[0][1] == "<test@example.com>"


def test_task_wiederoeffnen_ohne_uid_ueberspringt_imap(client, tmp_path):
    """Ohne imap_uid wird kein IMAP-Aufruf gemacht."""
    task_id = _insert_task(
        tmp_path, status="ERLEDIGT", imap_uid=None,
        erledigt_am=datetime.now().isoformat(timespec="seconds"),
    )

    with patch("web.imap_poller.imap_move_to_inbox") as mock_move:
        client.post(f"/tasks/{task_id}/wiederoeffnen", follow_redirects=True)

    mock_move.assert_not_called()


def test_task_wiederoeffnen_imap_fehler_blockiert_db_nicht(client, tmp_path):
    """IMAP-Fehler bei wiederoeffnen verhindert nicht die DB-Aktualisierung."""
    task_id = _insert_task(
        tmp_path, status="ERLEDIGT",
        imap_uid="42", message_id="<test@example.com>",
        erledigt_am=datetime.now().isoformat(timespec="seconds"),
    )

    with patch("web.imap_poller.imap_move_to_inbox", side_effect=RuntimeError("IMAP down")):
        client.post(f"/tasks/{task_id}/wiederoeffnen", follow_redirects=True)

    db = sqlite3.connect(tmp_path / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
    db.close()
    assert row["status"] in ("NEU", "UNKLARE_ZUORDNUNG")


def test_task_loeschen_ruft_imap_delete_auf(client, tmp_path):
    """Wenn imap_uid gesetzt ist, wird imap_delete_from_inbox aufgerufen."""
    task_id = _insert_task(tmp_path, status="NEU", imap_uid="99")

    with patch("web.imap_poller.imap_delete_from_inbox") as mock_delete:
        client.post(f"/tasks/{task_id}/loeschen", follow_redirects=True)

    mock_delete.assert_called_once()
    args = mock_delete.call_args
    assert args[0][1] == "99"


def test_task_loeschen_ohne_uid_ueberspringt_imap(client, tmp_path):
    """Ohne imap_uid wird kein IMAP-Delete ausgeführt."""
    task_id = _insert_task(tmp_path, status="NEU", imap_uid=None)

    with patch("web.imap_poller.imap_delete_from_inbox") as mock_delete:
        client.post(f"/tasks/{task_id}/loeschen", follow_redirects=True)

    mock_delete.assert_not_called()


def test_task_loeschen_imap_fehler_blockiert_db_nicht(client, tmp_path):
    """IMAP-Fehler bei loeschen verhindert nicht das Löschen aus der DB."""
    task_id = _insert_task(tmp_path, status="NEU", imap_uid="99")

    with patch("web.imap_poller.imap_delete_from_inbox", side_effect=RuntimeError("IMAP down")):
        client.post(f"/tasks/{task_id}/loeschen", follow_redirects=True)

    db = sqlite3.connect(tmp_path / "checker.db")
    row = db.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
    db.close()
    assert row is None


def test_settings_hat_imap_nachweis_ordner_feld(client):
    """Settings-Seite enthält das imap_nachweis_ordner Eingabefeld."""
    response = client.get("/settings")
    assert b"imap_nachweis_ordner" in response.data


def test_settings_speichert_imap_nachweis_ordner(client, tmp_path):
    """imap_nachweis_ordner wird korrekt in die DB gespeichert."""
    client.post("/settings", data={"imap_nachweis_ordner": "Abgeschlossen"})

    db = sqlite3.connect(tmp_path / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT value FROM settings WHERE key = 'imap_nachweis_ordner'").fetchone()
    db.close()
    assert row is not None
    assert row["value"] == "Abgeschlossen"
