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
    """Beim erneuten Upload mit geänderter E-Mail wird adresse_geaendert=1 gesetzt und status auf nie_geprueft zurückgesetzt."""
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
    assert row["status"] == "nie_geprueft"  # Status wird zurückgesetzt, da neue Adresse ungeprüft
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


# --- Migration ---

def test_init_db_ergaenzt_bestaetigung_herkunft_spalte_bei_bestehender_db(client, tmp_path):
    """Eine bestehende DB ohne die Spalte bestaetigung_herkunft bekommt sie beim naechsten
    init_db-Aufruf per ALTER TABLE ergaenzt, ohne bestehende Daten zu verlieren."""
    client.get("/")  # legt DB inkl. neuer Spalte an

    db = sqlite3.connect(tmp_path / "checker.db")
    db.execute("ALTER TABLE email_verifikation DROP COLUMN bestaetigung_herkunft")
    db.execute(
        """INSERT INTO email_verifikation (pers_nr, vorname, nachname, email, status)
           VALUES ('001', 'Max', 'Muster', 'max@example.com', 'bestaetigt')"""
    )
    db.commit()
    db.close()

    from web.app import app as flask_app, init_db
    with flask_app.app_context():
        init_db()

    db = sqlite3.connect(tmp_path / "checker.db")
    db.row_factory = sqlite3.Row
    cols = {row[1] for row in db.execute("PRAGMA table_info(email_verifikation)").fetchall()}
    row = db.execute("SELECT * FROM email_verifikation WHERE pers_nr='001'").fetchone()
    db.close()

    assert "bestaetigung_herkunft" in cols
    assert row["vorname"] == "Max"  # bestehende Daten bleiben erhalten
    assert row["bestaetigung_herkunft"] is None


# --- GET /email-pruefung ---

def test_upload_ungueltige_email_setzt_status_ungueltige_adresse(client, tmp_path):
    """Eine Adresse ohne '@' (z.B. 'vorname-name at domain.de') wird beim Import als ungueltige_adresse markiert."""
    members = [
        {"pers_nr": "001", "vorname": "Max", "nachname": "Muster", "email": "max-muster at example.de"},
    ]
    with patch("web.app.load_members_from_xls", return_value=members):
        datei = (io.BytesIO(b"dummy xls"), "export.xls")
        client.post("/upload", data={"xls_datei": datei}, content_type="multipart/form-data")

    db = sqlite3.connect(tmp_path / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM email_verifikation WHERE pers_nr='001'").fetchone()
    db.close()

    assert row["status"] == "ungueltige_adresse"


def test_upload_korrigierte_email_faellt_auf_nie_geprueft_zurueck(client, tmp_path):
    """Wird eine zuvor ungueltige Adresse per Import auf ein gueltiges Format korrigiert, faellt der Status auf nie_geprueft zurueck."""
    members_v1 = [{"pers_nr": "001", "vorname": "Max", "nachname": "Muster", "email": "max-muster at example.de"}]
    with patch("web.app.load_members_from_xls", return_value=members_v1):
        datei = (io.BytesIO(b"dummy xls"), "export.xls")
        client.post("/upload", data={"xls_datei": datei}, content_type="multipart/form-data")

    members_v2 = [{"pers_nr": "001", "vorname": "Max", "nachname": "Muster", "email": "max@example.de"}]
    with patch("web.app.load_members_from_xls", return_value=members_v2):
        datei = (io.BytesIO(b"dummy xls"), "export.xls")
        client.post("/upload", data={"xls_datei": datei}, content_type="multipart/form-data")

    db = sqlite3.connect(tmp_path / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM email_verifikation WHERE pers_nr='001'").fetchone()
    db.close()

    assert row["status"] == "nie_geprueft"
    assert row["email"] == "max@example.de"


def test_upload_unveraenderte_ungueltige_email_wird_bei_erneutem_sync_erkannt(client, tmp_path):
    """Ein Bestandseintrag mit unveraendert ungueltiger Adresse wird bei jedem Sync-Durchlauf geprueft, nicht nur bei Aenderung."""
    client.get("/")  # DB initialisieren
    _seed_db(tmp_path, [
        {"pers_nr": "001", "vorname": "Max", "nachname": "Muster",
         "email": "max-muster at example.de", "status": "nie_geprueft",
         "gesendet_am": None, "bestaetigt_am": None, "adresse_geaendert": 0},
    ])

    members = [{"pers_nr": "001", "vorname": "Max", "nachname": "Muster", "email": "max-muster at example.de"}]
    with patch("web.app.load_members_from_xls", return_value=members):
        datei = (io.BytesIO(b"dummy xls"), "export.xls")
        client.post("/upload", data={"xls_datei": datei}, content_type="multipart/form-data")

    db = sqlite3.connect(tmp_path / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM email_verifikation WHERE pers_nr='001'").fetchone()
    db.close()

    assert row["status"] == "ungueltige_adresse"


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


# --- POST /email-pruefung/senden ---

def test_senden_ohne_auswahl_kein_versand(client, tmp_path):
    """POST ohne ausgewählte Mitglieder sendet nichts und zeigt eine Warnung."""
    client.get("/")  # DB initialisieren
    with patch("web.app.send_verifikationsmail") as mock_send:
        response = client.post("/email-pruefung/senden", data={}, follow_redirects=True)
    mock_send.assert_not_called()
    html = response.data.decode("utf-8")
    assert "ausgewählt" in html or "Keine" in html


def test_senden_setzt_status_ausstehend(client, tmp_path):
    """Nach erfolgreichem Versand wird Status auf 'ausstehend' gesetzt."""
    client.get("/")
    _seed_db(tmp_path, [
        {"pers_nr": "001", "vorname": "Max", "nachname": "Muster",
         "email": "max@example.com", "status": "nie_geprueft",
         "gesendet_am": None, "bestaetigt_am": None, "adresse_geaendert": 0},
    ])
    with patch("web.app.send_verifikationsmail", return_value="<msg-001@test>"):
        client.post("/email-pruefung/senden", data={"pers_nr": ["001"]})

    db = sqlite3.connect(tmp_path / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM email_verifikation WHERE pers_nr='001'").fetchone()
    db.close()

    assert row["status"] == "ausstehend"
    assert row["gesendet_am"] is not None
    assert row["verifikationsmail_message_id"] == "<msg-001@test>"


def test_senden_an_bereits_bestaetigtes_mitglied_setzt_re_verifikation_ausstehend(client, tmp_path):
    """Erneuter Versand an ein bereits bestaetigtes Mitglied setzt re_verifikation_ausstehend statt ausstehend,
    die alte Bestaetigung (bestaetigt_am) bleibt erhalten."""
    client.get("/")
    _seed_db(tmp_path, [
        {"pers_nr": "001", "vorname": "Max", "nachname": "Muster",
         "email": "max@example.com", "status": "bestaetigt",
         "gesendet_am": "2026-01-01T10:00:00", "bestaetigt_am": "2026-01-02T12:00:00",
         "adresse_geaendert": 0},
    ])
    with patch("web.app.send_verifikationsmail", return_value="<msg-002@test>"):
        client.post("/email-pruefung/senden", data={"pers_nr": ["001"]})

    db = sqlite3.connect(tmp_path / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM email_verifikation WHERE pers_nr='001'").fetchone()
    db.close()

    assert row["status"] == "re_verifikation_ausstehend"
    assert row["bestaetigt_am"] == "2026-01-02T12:00:00"  # alte Bestaetigung bleibt erhalten
    assert row["gesendet_am"] != "2026-01-01T10:00:00"  # neues Sendedatum
    assert row["verifikationsmail_message_id"] == "<msg-002@test>"


def test_senden_an_bereits_re_verifikation_ausstehendes_mitglied_bleibt_re_verifikation_ausstehend(client, tmp_path):
    """Erneuter Versand an ein Mitglied, das schon im Status re_verifikation_ausstehend ist,
    bleibt bei re_verifikation_ausstehend statt auf ausstehend zurueckzufallen –
    die alte Bestaetigung (bestaetigt_am) bleibt weiterhin erhalten und sichtbar."""
    client.get("/")
    _seed_db(tmp_path, [
        {"pers_nr": "001", "vorname": "Max", "nachname": "Muster",
         "email": "max@example.com", "status": "re_verifikation_ausstehend",
         "gesendet_am": "2026-01-05T10:00:00", "bestaetigt_am": "2026-01-02T12:00:00",
         "adresse_geaendert": 0},
    ])
    with patch("web.app.send_verifikationsmail", return_value="<msg-003@test>"):
        client.post("/email-pruefung/senden", data={"pers_nr": ["001"]})

    db = sqlite3.connect(tmp_path / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM email_verifikation WHERE pers_nr='001'").fetchone()
    db.close()

    assert row["status"] == "re_verifikation_ausstehend"
    assert row["bestaetigt_am"] == "2026-01-02T12:00:00"


def test_senden_speichert_gesendet_am(client, tmp_path):
    """gesendet_am wird beim Versand auf den aktuellen Zeitstempel gesetzt."""
    client.get("/")
    _seed_db(tmp_path, [
        {"pers_nr": "001", "vorname": "Max", "nachname": "Muster",
         "email": "max@example.com", "status": "nie_geprueft",
         "gesendet_am": None, "bestaetigt_am": None, "adresse_geaendert": 0},
    ])
    with patch("web.app.send_verifikationsmail", return_value="<msg@test>"):
        client.post("/email-pruefung/senden", data={"pers_nr": ["001"]})

    db = sqlite3.connect(tmp_path / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT gesendet_am FROM email_verifikation WHERE pers_nr='001'").fetchone()
    db.close()
    assert row["gesendet_am"] is not None
    assert len(row["gesendet_am"]) >= 10  # mindestens YYYY-MM-DD


def test_senden_flash_meldung_mit_anzahl(client, tmp_path):
    """Flash-Meldung enthält die Anzahl der versendeten Mails."""
    client.get("/")
    _seed_db(tmp_path, [
        {"pers_nr": "001", "vorname": "Max", "nachname": "A",
         "email": "a@x.com", "status": "nie_geprueft",
         "gesendet_am": None, "bestaetigt_am": None, "adresse_geaendert": 0},
        {"pers_nr": "002", "vorname": "Lisa", "nachname": "B",
         "email": "b@x.com", "status": "nie_geprueft",
         "gesendet_am": None, "bestaetigt_am": None, "adresse_geaendert": 0},
    ])
    with patch("web.app.send_verifikationsmail", return_value="<msg@test>"):
        response = client.post(
            "/email-pruefung/senden",
            data={"pers_nr": ["001", "002"]},
            follow_redirects=True,
        )
    html = response.data.decode("utf-8")
    assert "2" in html


def test_senden_ruft_send_verifikationsmail_mit_korrekten_args_auf(client, tmp_path):
    """send_verifikationsmail wird mit smtp_config, email, vorname, nachname aufgerufen."""
    client.get("/")
    _seed_db(tmp_path, [
        {"pers_nr": "001", "vorname": "Max", "nachname": "Muster",
         "email": "max@example.com", "status": "nie_geprueft",
         "gesendet_am": None, "bestaetigt_am": None, "adresse_geaendert": 0},
    ])
    with patch("web.app.send_verifikationsmail", return_value="<msg@test>") as mock_send:
        client.post("/email-pruefung/senden", data={"pers_nr": ["001"]})

    mock_send.assert_called_once()
    kwargs = mock_send.call_args
    assert kwargs.kwargs.get("to_addr") == "max@example.com" or kwargs.args[1] == "max@example.com"
    assert "Max" in str(kwargs)
    assert "Muster" in str(kwargs)


def test_senden_mehrere_mitglieder(client, tmp_path):
    """Versand an mehrere Mitglieder aktualisiert alle Datensätze."""
    client.get("/")
    _seed_db(tmp_path, [
        {"pers_nr": "001", "vorname": "Max", "nachname": "A",
         "email": "a@x.com", "status": "nie_geprueft",
         "gesendet_am": None, "bestaetigt_am": None, "adresse_geaendert": 0},
        {"pers_nr": "002", "vorname": "Lisa", "nachname": "B",
         "email": "b@x.com", "status": "nie_geprueft",
         "gesendet_am": None, "bestaetigt_am": None, "adresse_geaendert": 0},
        {"pers_nr": "003", "vorname": "Tom", "nachname": "C",
         "email": "c@x.com", "status": "nie_geprueft",
         "gesendet_am": None, "bestaetigt_am": None, "adresse_geaendert": 0},
    ])
    with patch("web.app.send_verifikationsmail", return_value="<msg@test>"):
        client.post("/email-pruefung/senden", data={"pers_nr": ["001", "003"]})

    db = sqlite3.connect(tmp_path / "checker.db")
    db.row_factory = sqlite3.Row
    rows = {r["pers_nr"]: r for r in db.execute("SELECT * FROM email_verifikation").fetchall()}
    db.close()

    assert rows["001"]["status"] == "ausstehend"
    assert rows["003"]["status"] == "ausstehend"
    assert rows["002"]["status"] == "nie_geprueft"  # nicht ausgewählt


def test_email_pruefung_seite_hat_checkboxen(client, tmp_path):
    """Die Seite /email-pruefung enthält Checkboxen für jeden Mitgliedseintrag."""
    client.get("/")
    _seed_db(tmp_path, [
        {"pers_nr": "001", "vorname": "Max", "nachname": "Muster",
         "email": "max@example.com", "status": "nie_geprueft",
         "gesendet_am": None, "bestaetigt_am": None, "adresse_geaendert": 0},
    ])
    response = client.get("/email-pruefung")
    html = response.data.decode("utf-8")
    assert 'type="checkbox"' in html
    assert 'name="pers_nr"' in html
    assert 'value="001"' in html


def test_email_pruefung_seite_hat_senden_button(client, tmp_path):
    """Die Seite enthält einen Senden-Button zum Absenden des Formulars."""
    client.get("/")
    _seed_db(tmp_path, [
        {"pers_nr": "001", "vorname": "Max", "nachname": "Muster",
         "email": "max@example.com", "status": "nie_geprueft",
         "gesendet_am": None, "bestaetigt_am": None, "adresse_geaendert": 0},
    ])
    response = client.get("/email-pruefung")
    html = response.data.decode("utf-8")
    assert "Senden" in html
    assert "/email-pruefung/senden" in html


def test_email_pruefung_seite_hat_alle_auswaehlen_button(client, tmp_path):
    """Die Seite enthält einen 'Alle auswählen'-Button."""
    client.get("/")
    _seed_db(tmp_path, [
        {"pers_nr": "001", "vorname": "Max", "nachname": "Muster",
         "email": "max@example.com", "status": "nie_geprueft",
         "gesendet_am": None, "bestaetigt_am": None, "adresse_geaendert": 0},
    ])
    response = client.get("/email-pruefung")
    html = response.data.decode("utf-8")
    assert "Alle auswählen" in html or "alle" in html.lower()


def test_senden_nutzt_verifikation_betreff_und_template_aus_db(client, tmp_path):
    """send_verifikationsmail erhält konfigurierten Betreff und Template aus den Einstellungen."""
    client.get("/")
    client.post("/settings", data={
        "smtp_host": "", "smtp_port": "587", "smtp_user": "", "smtp_password": "",
        "smtp_from": "", "kommandanten_cc": "", "zusammenfassung_an": "",
        "warn_days": "90", "pruefungstypen": "G25", "archiv_tage": "365",
        "email_betreff": "", "email_template": "",
        "zusammenfassung_betreff": "", "zusammenfassung_template": "",
        "verifikation_betreff": "Test-Verifikations-Betreff",
        "verifikation_template": "Hallo {vorname} {nachname}, Test-Inhalt.",
        "imap_verifikation_ordner": "test-ordner",
    })
    _seed_db(tmp_path, [
        {"pers_nr": "001", "vorname": "Max", "nachname": "Muster",
         "email": "max@example.com", "status": "nie_geprueft",
         "gesendet_am": None, "bestaetigt_am": None, "adresse_geaendert": 0},
    ])

    with patch("web.app.send_verifikationsmail", return_value="<msg@test>") as mock_send:
        client.post("/email-pruefung/senden", data={"pers_nr": ["001"]})

    mock_send.assert_called_once()
    kwargs = mock_send.call_args.kwargs
    assert kwargs.get("betreff") == "Test-Verifikations-Betreff"
    assert kwargs.get("template") == "Hallo {vorname} {nachname}, Test-Inhalt."


# --- Versand-Guard gegen ungueltige Adressen ---

def test_senden_blockt_ungueltige_adresse_ohne_versand(client, tmp_path):
    """Eine ausgewaehlte Person mit ungueltiger Adresse wird nicht an send_verifikationsmail uebergeben."""
    client.get("/")
    _seed_db(tmp_path, [
        {"pers_nr": "001", "vorname": "Max", "nachname": "Muster",
         "email": "max-muster at example.de", "status": "nie_geprueft",
         "gesendet_am": None, "bestaetigt_am": None, "adresse_geaendert": 0},
    ])
    with patch("web.app.send_verifikationsmail") as mock_send:
        response = client.post(
            "/email-pruefung/senden", data={"pers_nr": ["001"]}, follow_redirects=True
        )

    mock_send.assert_not_called()
    html = response.data.decode("utf-8")
    assert "Ungültige" in html or "ungueltig" in html.lower()

    db = sqlite3.connect(tmp_path / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM email_verifikation WHERE pers_nr='001'").fetchone()
    db.close()
    assert row["status"] == "ungueltige_adresse"
    assert row["gesendet_am"] is None


# --- Template: Button, Filter, Herkunfts-Hinweis ---

@pytest.mark.parametrize("status", ["ausstehend", "re_verifikation_ausstehend"])
def test_manuell_bestaetigen_button_sichtbar_bei_ausstehenden_status(client, tmp_path, status):
    """Der Button 'Manuell bestätigen' erscheint bei Status ausstehend und re_verifikation_ausstehend."""
    client.get("/")
    _seed_db(tmp_path, [
        {"pers_nr": "001", "vorname": "Max", "nachname": "Muster",
         "email": "max@example.com", "status": status,
         "gesendet_am": "2026-01-01T10:00:00", "bestaetigt_am": None, "adresse_geaendert": 0},
    ])
    response = client.get("/email-pruefung")
    html = response.data.decode("utf-8")
    assert "Manuell bestätigen" in html
    assert "/email-pruefung/001/manuell-bestaetigen" in html


@pytest.mark.parametrize("status", ["nie_geprueft", "ungueltige_adresse", "bestaetigt"])
def test_manuell_bestaetigen_button_fehlt_bei_anderen_status(client, tmp_path, status):
    """Der Button 'Manuell bestätigen' fehlt bei nie_geprueft, ungueltige_adresse und bestaetigt."""
    client.get("/")
    _seed_db(tmp_path, [
        {"pers_nr": "001", "vorname": "Max", "nachname": "Muster",
         "email": "max@example.com", "status": status,
         "gesendet_am": None, "bestaetigt_am": None, "adresse_geaendert": 0},
    ])
    response = client.get("/email-pruefung")
    html = response.data.decode("utf-8")
    assert "Manuell bestätigen" not in html


def test_email_pruefung_filterbar_nach_re_verifikation_ausstehend(client, tmp_path):
    """Die Liste ist nach Status re_verifikation_ausstehend filterbar."""
    client.get("/")
    _seed_db(tmp_path, [
        {"pers_nr": "001", "vorname": "Max", "nachname": "A", "email": "a@x.com",
         "status": "re_verifikation_ausstehend", "gesendet_am": "2026-01-01", "bestaetigt_am": "2025-01-01",
         "adresse_geaendert": 0},
        {"pers_nr": "002", "vorname": "Lisa", "nachname": "B", "email": "b@x.com",
         "status": "bestaetigt", "gesendet_am": "2026-01-01", "bestaetigt_am": "2026-01-02", "adresse_geaendert": 0},
    ])
    response = client.get("/email-pruefung?status=re_verifikation_ausstehend")
    html = response.data.decode("utf-8")
    assert "a@x.com" in html
    assert "b@x.com" not in html
    assert "Erneute Verifikation ausstehend" in html


def test_bestaetigt_am_zeigt_manuell_hinweis(client, tmp_path):
    """Bei bestaetigung_herkunft='manuell' erscheint ein Hinweis neben dem Bestätigungsdatum."""
    client.get("/")
    db = sqlite3.connect(tmp_path / "checker.db")
    db.execute(
        """INSERT INTO email_verifikation
           (pers_nr, vorname, nachname, email, status, bestaetigt_am, bestaetigung_herkunft)
           VALUES ('001', 'Max', 'Muster', 'max@example.com', 'bestaetigt', '2026-01-02T12:00:00', 'manuell')"""
    )
    db.commit()
    db.close()

    response = client.get("/email-pruefung")
    html = response.data.decode("utf-8")
    assert "manuell" in html.lower()


def test_bestaetigt_am_ohne_herkunft_zeigt_keinen_manuell_hinweis(client, tmp_path):
    """Bei einer automatischen Bestätigung (keine Herkunft gesetzt) erscheint kein 'manuell'-Hinweis."""
    client.get("/")
    _seed_db(tmp_path, [
        {"pers_nr": "001", "vorname": "Max", "nachname": "Muster",
         "email": "max@example.com", "status": "bestaetigt",
         "gesendet_am": "2026-01-01", "bestaetigt_am": "2026-01-02", "adresse_geaendert": 0},
    ])
    response = client.get("/email-pruefung")
    html = response.data.decode("utf-8")
    assert "(manuell)" not in html


# --- Dashboard-Zähler ---

def test_dashboard_zaehlt_re_verifikation_ausstehend_mit(client, tmp_path):
    """email_ausstehend_count auf der Startseite zaehlt Mitglieder im Status
    re_verifikation_ausstehend mit."""
    client.get("/")
    _seed_db(tmp_path, [
        {"pers_nr": "001", "vorname": "Max", "nachname": "Muster",
         "email": "max@example.com", "status": "re_verifikation_ausstehend",
         "gesendet_am": "2026-01-01T10:00:00", "bestaetigt_am": "2025-01-01T10:00:00",
         "adresse_geaendert": 0},
    ])
    response = client.get("/")
    html = response.data.decode("utf-8")
    assert '<div class="kachel-zahl">1</div>' in html


# --- POST /email-pruefung/<pers_nr>/manuell-bestaetigen ---

def test_manuell_bestaetigen_aus_ausstehend(client, tmp_path):
    """Manuelles Bestaetigen aus Status 'ausstehend' setzt Status auf 'bestaetigt' mit Herkunft 'manuell'."""
    client.get("/")
    _seed_db(tmp_path, [
        {"pers_nr": "001", "vorname": "Max", "nachname": "Muster",
         "email": "max@example.com", "status": "ausstehend",
         "gesendet_am": "2026-01-01T10:00:00", "bestaetigt_am": None, "adresse_geaendert": 0},
    ])
    response = client.post("/email-pruefung/001/manuell-bestaetigen", follow_redirects=True)

    assert response.status_code == 200
    db = sqlite3.connect(tmp_path / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM email_verifikation WHERE pers_nr='001'").fetchone()
    db.close()
    assert row["status"] == "bestaetigt"
    assert row["bestaetigt_am"] is not None
    assert row["bestaetigung_herkunft"] == "manuell"


def test_manuell_bestaetigen_aus_re_verifikation_ausstehend(client, tmp_path):
    """Manuelles Bestaetigen aus Status 're_verifikation_ausstehend' setzt Status auf 'bestaetigt',
    aktualisiert bestaetigt_am auf den neuen Zeitpunkt und markiert Herkunft als 'manuell'."""
    client.get("/")
    _seed_db(tmp_path, [
        {"pers_nr": "001", "vorname": "Max", "nachname": "Muster",
         "email": "max@example.com", "status": "re_verifikation_ausstehend",
         "gesendet_am": "2026-02-01T10:00:00", "bestaetigt_am": "2026-01-02T12:00:00", "adresse_geaendert": 0},
    ])
    client.post("/email-pruefung/001/manuell-bestaetigen")

    db = sqlite3.connect(tmp_path / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM email_verifikation WHERE pers_nr='001'").fetchone()
    db.close()
    assert row["status"] == "bestaetigt"
    assert row["bestaetigt_am"] != "2026-01-02T12:00:00"
    assert row["bestaetigung_herkunft"] == "manuell"


@pytest.mark.parametrize("status", ["nie_geprueft", "ungueltige_adresse", "bestaetigt"])
def test_manuell_bestaetigen_abgelehnt_bei_unzulaessigem_status(client, tmp_path, status):
    """Manuelles Bestaetigen wird abgelehnt, wenn noch keine Verifikationsmail gesendet wurde
    oder das Mitglied bereits bestaetigt ist. Status bleibt unveraendert."""
    client.get("/")
    _seed_db(tmp_path, [
        {"pers_nr": "001", "vorname": "Max", "nachname": "Muster",
         "email": "max@example.com", "status": status,
         "gesendet_am": None, "bestaetigt_am": None, "adresse_geaendert": 0},
    ])
    response = client.post("/email-pruefung/001/manuell-bestaetigen", follow_redirects=True)

    html = response.data.decode("utf-8")
    assert "manuell" in html.lower() or "nicht möglich" in html.lower() or "ungültig" in html.lower()
    db = sqlite3.connect(tmp_path / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM email_verifikation WHERE pers_nr='001'").fetchone()
    db.close()
    assert row["status"] == status
    assert row["bestaetigung_herkunft"] is None


def test_manuell_bestaetigen_unbekannte_pers_nr_gibt_404(client, tmp_path):
    """Manuelles Bestaetigen fuer eine nicht existierende pers_nr gibt HTTP 404 zurueck."""
    client.get("/")
    response = client.post("/email-pruefung/999/manuell-bestaetigen")
    assert response.status_code == 404


def test_senden_gemischte_auswahl_versendet_nur_gueltige(client, tmp_path):
    """Bei gemischter Auswahl wird nur die gueltige Adresse versendet, die ungueltige uebersprungen."""
    client.get("/")
    _seed_db(tmp_path, [
        {"pers_nr": "001", "vorname": "Max", "nachname": "A",
         "email": "max-muster at example.de", "status": "nie_geprueft",
         "gesendet_am": None, "bestaetigt_am": None, "adresse_geaendert": 0},
        {"pers_nr": "002", "vorname": "Lisa", "nachname": "B",
         "email": "lisa@example.de", "status": "nie_geprueft",
         "gesendet_am": None, "bestaetigt_am": None, "adresse_geaendert": 0},
    ])
    with patch("web.app.send_verifikationsmail", return_value="<msg@test>") as mock_send:
        client.post("/email-pruefung/senden", data={"pers_nr": ["001", "002"]})

    mock_send.assert_called_once()

    db = sqlite3.connect(tmp_path / "checker.db")
    db.row_factory = sqlite3.Row
    rows = {r["pers_nr"]: r for r in db.execute("SELECT * FROM email_verifikation").fetchall()}
    db.close()
    assert rows["001"]["status"] == "ungueltige_adresse"
    assert rows["002"]["status"] == "ausstehend"
