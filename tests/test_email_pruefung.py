import io
import sqlite3
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


def _seed_db(tmp_path, rows):
    """Schreibt direkt Zeilen in email_verifikation."""
    db = sqlite3.connect(tmp_path / "checker.db")
    db.row_factory = sqlite3.Row
    for r in rows:
        db.execute(
            """INSERT INTO email_verifikation (pers_nr, vorname, nachname, email, status,
               gesendet_am, bestaetigt_am, adresse_geaendert)
               VALUES (:pers_nr, :vorname, :nachname, :email, :status,
               :gesendet_am, :bestaetigt_am, :adresse_geaendert)""",
            r,
        )
    db.commit()
    db.close()


# --- Sync nach XLS-Upload ---

def test_upload_legt_neue_mitglieder_in_email_verifikation_an(client, tmp_path):
    """Nach XLS-Upload erscheinen alle aktiven Mitglieder in email_verifikation mit Status nie_geprueft."""
    members = [
        {"pers_nr": "001", "vorname": "Max", "nachname": "Muster", "email": "max@example.com"},
        {"pers_nr": "002", "vorname": "Lisa", "nachname": "Lauf", "email": "lisa@example.com"},
    ]
    with patch("web.app.load_members_from_xls", return_value=members):
        datei = (io.BytesIO(b"dummy xls"), "export.xls")
        client.post("/upload", data={"xls_datei": datei}, content_type="multipart/form-data")

    db = sqlite3.connect(tmp_path / "checker.db")
    db.row_factory = sqlite3.Row
    rows = db.execute("SELECT * FROM email_verifikation ORDER BY pers_nr").fetchall()
    db.close()

    assert len(rows) == 2
    assert rows[0]["pers_nr"] == "001"
    assert rows[0]["status"] == "nie_geprueft"
    assert rows[0]["adresse_geaendert"] == 0
    assert rows[1]["pers_nr"] == "002"


def test_upload_inaktive_mitglieder_nicht_eingetragen(client, tmp_path):
    """load_members_from_xls filtert inaktive Mitglieder; die Sync-Funktion schreibt nur die zurückgegebene Liste."""
    # load_members_from_xls gibt bereits nur aktive Mitglieder zurück (bei EI anzeigen = Ja)
    members = [
        {"pers_nr": "001", "vorname": "Max", "nachname": "Muster", "email": "max@example.com"},
    ]
    with patch("web.app.load_members_from_xls", return_value=members):
        datei = (io.BytesIO(b"dummy xls"), "export.xls")
        client.post("/upload", data={"xls_datei": datei}, content_type="multipart/form-data")

    db = sqlite3.connect(tmp_path / "checker.db")
    rows = db.execute("SELECT COUNT(*) FROM email_verifikation").fetchone()[0]
    db.close()
    assert rows == 1


def test_upload_geaenderte_email_setzt_adresse_geaendert_flag(client, tmp_path):
    """Beim erneuten Upload mit geänderter E-Mail wird adresse_geaendert=1 gesetzt; Status bleibt."""
    # Erster Upload
    members_v1 = [{"pers_nr": "001", "vorname": "Max", "nachname": "Muster", "email": "alt@example.com"}]
    with patch("web.app.load_members_from_xls", return_value=members_v1):
        datei = (io.BytesIO(b"dummy xls"), "export.xls")
        client.post("/upload", data={"xls_datei": datei}, content_type="multipart/form-data")

    # Status manuell auf 'bestaetigt' setzen
    db = sqlite3.connect(tmp_path / "checker.db")
    db.execute("UPDATE email_verifikation SET status='bestaetigt' WHERE pers_nr='001'")
    db.commit()
    db.close()

    # Zweiter Upload mit geänderter E-Mail
    members_v2 = [{"pers_nr": "001", "vorname": "Max", "nachname": "Muster", "email": "neu@example.com"}]
    with patch("web.app.load_members_from_xls", return_value=members_v2):
        datei = (io.BytesIO(b"dummy xls"), "export.xls")
        client.post("/upload", data={"xls_datei": datei}, content_type="multipart/form-data")

    db = sqlite3.connect(tmp_path / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM email_verifikation WHERE pers_nr='001'").fetchone()
    db.close()

    assert row["adresse_geaendert"] == 1
    assert row["status"] == "bestaetigt"  # Status bleibt erhalten
    assert row["email"] == "neu@example.com"


def test_upload_unveraenderte_email_aendert_nichts(client, tmp_path):
    """Beim erneuten Upload mit unveränderter E-Mail ändert sich adresse_geaendert nicht."""
    members = [{"pers_nr": "001", "vorname": "Max", "nachname": "Muster", "email": "max@example.com"}]

    for _ in range(2):
        with patch("web.app.load_members_from_xls", return_value=members):
            datei = (io.BytesIO(b"dummy xls"), "export.xls")
            client.post("/upload", data={"xls_datei": datei}, content_type="multipart/form-data")

    db = sqlite3.connect(tmp_path / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM email_verifikation WHERE pers_nr='001'").fetchone()
    db.close()

    assert row["adresse_geaendert"] == 0


# --- GET /email-pruefung ---

def test_email_pruefung_gibt_200_zurueck(client):
    """`GET /email-pruefung` gibt HTTP 200 zurück."""
    response = client.get("/email-pruefung")
    assert response.status_code == 200


def test_email_pruefung_zeigt_mitgliedertabelle(client, tmp_path):
    """Die Seite zeigt Name, E-Mail, Status, gesendet_am, bestaetigt_am und Adresse-geändert-Flag."""
    client.get("/")  # DB initialisieren
    _seed_db(tmp_path, [
        {
            "pers_nr": "001", "vorname": "Max", "nachname": "Muster",
            "email": "max@example.com", "status": "bestaetigt",
            "gesendet_am": "2026-01-01T10:00:00", "bestaetigt_am": "2026-01-02T12:00:00",
            "adresse_geaendert": 1,
        }
    ])

    response = client.get("/email-pruefung")
    html = response.data.decode("utf-8")

    assert "Muster" in html
    assert "Max" in html
    assert "bestaetigt" in html or "bestätigt" in html
    assert "01.01.2026" in html  # Datum im deutschen Format sichtbar
    assert "Adresse" in html  # Adresse-geändert-Hinweis


def test_email_pruefung_filterbar_nach_status(client, tmp_path):
    """Die Liste ist nach Status filterbar."""
    client.get("/")
    _seed_db(tmp_path, [
        {"pers_nr": "001", "vorname": "Max", "nachname": "A", "email": "a@x.com",
         "status": "nie_geprueft", "gesendet_am": None, "bestaetigt_am": None, "adresse_geaendert": 0},
        {"pers_nr": "002", "vorname": "Lisa", "nachname": "B", "email": "b@x.com",
         "status": "bestaetigt", "gesendet_am": "2026-01-01", "bestaetigt_am": "2026-01-02", "adresse_geaendert": 0},
    ])

    response = client.get("/email-pruefung?status=nie_geprueft")
    html = response.data.decode("utf-8")

    assert "a@x.com" in html
    assert "b@x.com" not in html


def test_email_pruefung_sortierbar_nach_gesendet_am(client, tmp_path):
    """Die Liste ist nach gesendet_am sortierbar."""
    client.get("/")
    _seed_db(tmp_path, [
        {"pers_nr": "001", "vorname": "Früh", "nachname": "A", "email": "a@x.com",
         "status": "ausstehend", "gesendet_am": "2026-01-01T10:00:00", "bestaetigt_am": None, "adresse_geaendert": 0},
        {"pers_nr": "002", "vorname": "Spät", "nachname": "B", "email": "b@x.com",
         "status": "ausstehend", "gesendet_am": "2026-06-01T10:00:00", "bestaetigt_am": None, "adresse_geaendert": 0},
    ])

    response = client.get("/email-pruefung?sort=gesendet_am")
    html = response.data.decode("utf-8")

    # Spät (neueres Datum) erscheint vor Früh bei DESC-Sortierung
    pos_spaet = html.index("Spät")
    pos_frueh = html.index("Früh")
    assert pos_spaet < pos_frueh


def test_email_pruefung_sortierbar_nach_bestaetigt_am(client, tmp_path):
    """Die Liste ist nach bestaetigt_am sortierbar."""
    client.get("/")
    _seed_db(tmp_path, [
        {"pers_nr": "001", "vorname": "Früh", "nachname": "A", "email": "a@x.com",
         "status": "bestaetigt", "gesendet_am": None, "bestaetigt_am": "2026-01-01T10:00:00", "adresse_geaendert": 0},
        {"pers_nr": "002", "vorname": "Spät", "nachname": "B", "email": "b@x.com",
         "status": "bestaetigt", "gesendet_am": None, "bestaetigt_am": "2026-06-01T10:00:00", "adresse_geaendert": 0},
    ])

    response = client.get("/email-pruefung?sort=bestaetigt_am")
    html = response.data.decode("utf-8")

    pos_spaet = html.index("Spät")
    pos_frueh = html.index("Früh")
    assert pos_spaet < pos_frueh


def test_navigation_enthaelt_email_pruefung_link(client):
    """Die Hauptnavigation enthält einen Menüpunkt 'E-Mail-Prüfung'."""
    response = client.get("/")
    html = response.data.decode("utf-8")
    assert "E-Mail-Prüfung" in html or "email-pruefung" in html
