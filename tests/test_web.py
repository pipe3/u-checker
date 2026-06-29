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

def test_index_zeigt_keine_tasks_sektion(client):
    """Tasks-Sektion wurde aus der Startseite entfernt – sie lebt jetzt auf /nachweise."""
    response = client.get("/")
    # Der Nav-Link darf bleiben; die h2-Überschrift darf nicht mehr auf dem Dashboard stehen
    assert b"<h2>Eingehende Nachweise" not in response.data


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


# --- /nachweise: Issue #30 ---

import sqlite3 as _sqlite3
from datetime import datetime as _dt


def _db_insert_task(db_path, **kwargs):
    """Hilfsfunktion: Task in die Test-DB einfügen."""
    defaults = {
        "status": "NEU",
        "empfangen_am": _dt.now().isoformat(timespec="seconds"),
        "von_email": "sender@example.com",
        "betreff": "Test-Nachweis",
        "pruefungstyp": None,
        "faelligkeitsdatum": None,
        "mitglied_name": None,
        "mitglied_nr": None,
        "raw_email": None,
        "raw_text": None,
        "anhang_count": 0,
    }
    defaults.update(kwargs)
    db = _sqlite3.connect(db_path)
    cursor = db.execute(
        """INSERT INTO tasks
           (status, empfangen_am, von_email, betreff,
            pruefungstyp, faelligkeitsdatum, mitglied_name, mitglied_nr,
            raw_email, raw_text, anhang_count)
           VALUES (:status, :empfangen_am, :von_email, :betreff,
                   :pruefungstyp, :faelligkeitsdatum, :mitglied_name, :mitglied_nr,
                   :raw_email, :raw_text, :anhang_count)""",
        defaults,
    )
    task_id = cursor.lastrowid
    db.commit()
    db.close()
    return task_id


def test_index_badge_ist_link_zu_nachweise(client):
    """Badge 'Offene Aufgaben' auf der Startseite ist ein Link zu /nachweise."""
    response = client.get("/")
    body = response.data.decode()
    assert 'href="/nachweise"' in body


def test_nachweise_in_navigation(client):
    """/nachweise erscheint in der Navigation der Startseite."""
    response = client.get("/")
    assert b"/nachweise" in response.data


def test_nachweise_erreichbar(client):
    """GET /nachweise gibt HTTP 200 zurück."""
    response = client.get("/nachweise")
    assert response.status_code == 200


def test_nachweise_zeigt_nur_offene_tasks(client, tmp_path):
    """Nur NEU und UNKLARE_ZUORDNUNG erscheinen – ERLEDIGT nicht."""
    client.get("/")
    db_path = tmp_path / "checker.db"
    _db_insert_task(db_path, status="NEU", betreff="Offener Nachweis")
    _db_insert_task(db_path, status="UNKLARE_ZUORDNUNG", betreff="Unklarer Nachweis")
    _db_insert_task(db_path, status="ERLEDIGT", betreff="Erledigter Nachweis")

    response = client.get("/nachweise")
    body = response.data.decode()
    assert "Offener Nachweis" in body
    assert "Unklarer Nachweis" in body
    assert "Erledigter Nachweis" not in body


def test_nachweise_karte_zeigt_mitglied_pruefungstyp_datum(client, tmp_path):
    """Karte zeigt Mitglied, Prüfungstyp und Fälligkeitsdatum prominent."""
    client.get("/")
    db_path = tmp_path / "checker.db"
    _db_insert_task(
        db_path,
        status="NEU",
        mitglied_name="Max Mustermann",
        pruefungstyp="G25",
        faelligkeitsdatum="2025-06-30",
    )

    response = client.get("/nachweise")
    body = response.data.decode()
    assert "Max Mustermann" in body
    assert "G25" in body
    assert "2025" in body  # Datum irgendwie enthalten


def test_nachweise_karte_neu_css_klasse(client, tmp_path):
    """NEU-Karten tragen die CSS-Klasse karte-neu."""
    client.get("/")
    db_path = tmp_path / "checker.db"
    _db_insert_task(db_path, status="NEU")

    response = client.get("/nachweise")
    assert b"karte-neu" in response.data


def test_nachweise_karte_unklare_zuordnung_css_klasse(client, tmp_path):
    """UNKLARE_ZUORDNUNG-Karten tragen die CSS-Klasse karte-unklare-zuordnung."""
    client.get("/")
    db_path = tmp_path / "checker.db"
    _db_insert_task(db_path, status="UNKLARE_ZUORDNUNG")

    response = client.get("/nachweise")
    assert b"karte-unklare-zuordnung" in response.data


def test_nachweise_karte_ocr_rohtext_aufklappbar(client, tmp_path):
    """OCR-Rohtext ist in einem aufklappbaren Element vorhanden."""
    client.get("/")
    db_path = tmp_path / "checker.db"
    _db_insert_task(db_path, status="NEU", raw_text="Erkannter Text aus OCR")

    response = client.get("/nachweise")
    body = response.data.decode()
    assert "<details" in body
    assert "Erkannter Text aus OCR" in body


def test_nachweise_erledigt_button_auf_neu_karten(client, tmp_path):
    """Erledigt-Button erscheint auf NEU-Karten."""
    client.get("/")
    db_path = tmp_path / "checker.db"
    task_id = _db_insert_task(db_path, status="NEU")

    response = client.get("/nachweise")
    body = response.data.decode()
    assert f"/tasks/{task_id}/erledigt" in body


def test_nachweise_zuordnen_dropdown_auf_unklare_karten(client, tmp_path):
    """Zuordnen-Dropdown erscheint auf UNKLARE_ZUORDNUNG-Karten wenn XLS vorhanden."""
    client.get("/")
    db_path = tmp_path / "checker.db"
    task_id = _db_insert_task(db_path, status="UNKLARE_ZUORDNUNG")

    # Dummy-XLS damit members geladen werden; echte XLS-Datei wird gemockt
    (tmp_path / "latest.xls").write_bytes(b"dummy")
    with patch("web.app.load_members_from_xls", return_value=[
        {"pers_nr": "001", "vorname": "Max", "nachname": "Mustermann"}
    ]):
        response = client.get("/nachweise")
    body = response.data.decode()
    assert f"/tasks/{task_id}/zuordnen" in body


def test_nachweise_unklare_zuordnung_fallback_erledigt_ohne_xls(client, tmp_path):
    """UNKLARE_ZUORDNUNG-Karte zeigt Erledigt-Fallback wenn kein XLS geladen ist."""
    client.get("/")
    db_path = tmp_path / "checker.db"
    task_id = _db_insert_task(db_path, status="UNKLARE_ZUORDNUNG")

    response = client.get("/nachweise")
    body = response.data.decode()
    assert f"/tasks/{task_id}/erledigt" in body


def test_nachweise_pdf_anhang_reanalyse_buttons(client, tmp_path):
    """PDF, Anhang-Link und Re-Analyse-Button sind auf Karten vorhanden."""
    import email as _email_lib
    client.get("/")
    db_path = tmp_path / "checker.db"

    # raw_email nötig für PDF und Anhang
    raw = b"From: test@example.com\r\nSubject: Test\r\n\r\nBody"
    task_id = _db_insert_task(db_path, status="NEU", raw_email=raw, anhang_count=1)

    response = client.get("/nachweise")
    body = response.data.decode()
    assert f"/tasks/{task_id}/pdf" in body
    assert f"/tasks/{task_id}/anhang/" in body
    assert f"/tasks/{task_id}/reanalyse" in body


# --- /nachweise?typ=: Issue #31 ---

def test_filter_chip_alle_vorhanden(client, tmp_path):
    """'Alle'-Chip ist immer vorhanden, auch wenn keine Prüfungstypen in der DB sind."""
    client.get("/")
    response = client.get("/nachweise")
    body = response.data.decode()
    assert "Alle" in body


def test_filter_chips_aus_db_typen_generiert(client, tmp_path):
    """Filter-Chips werden dynamisch aus den in der DB vorhandenen Prüfungstypen generiert."""
    client.get("/")
    db_path = tmp_path / "checker.db"
    _db_insert_task(db_path, status="NEU", pruefungstyp="G25")
    _db_insert_task(db_path, status="NEU", pruefungstyp="G26")

    response = client.get("/nachweise")
    body = response.data.decode()
    assert "G25" in body
    assert "G26" in body


def test_filter_nach_typ_zeigt_nur_passende_karten(client, tmp_path):
    """GET /nachweise?typ=G25 zeigt nur Karten mit pruefungstyp G25."""
    client.get("/")
    db_path = tmp_path / "checker.db"
    _db_insert_task(db_path, status="NEU", pruefungstyp="G25", betreff="G25-Nachweis")
    _db_insert_task(db_path, status="NEU", pruefungstyp="G26", betreff="G26-Nachweis")

    response = client.get("/nachweise?typ=G25")
    body = response.data.decode()
    assert "G25-Nachweis" in body
    assert "G26-Nachweis" not in body


def test_filter_ohne_parameter_zeigt_alle_tasks(client, tmp_path):
    """GET /nachweise ohne Parameter zeigt alle offenen Tasks."""
    client.get("/")
    db_path = tmp_path / "checker.db"
    _db_insert_task(db_path, status="NEU", pruefungstyp="G25", betreff="G25-Nachweis")
    _db_insert_task(db_path, status="NEU", pruefungstyp="G26", betreff="G26-Nachweis")

    response = client.get("/nachweise")
    body = response.data.decode()
    assert "G25-Nachweis" in body
    assert "G26-Nachweis" in body


def test_aktiver_chip_hervorgehoben(client, tmp_path):
    """Der aktive Filter-Chip trägt eine eigene CSS-Klasse."""
    client.get("/")
    db_path = tmp_path / "checker.db"
    _db_insert_task(db_path, status="NEU", pruefungstyp="G25")

    response = client.get("/nachweise?typ=G25")
    body = response.data.decode()
    assert "chip-aktiv" in body


def test_alle_chip_aktiv_ohne_parameter(client, tmp_path):
    """'Alle'-Chip trägt chip-aktiv-Klasse wenn kein Filter gesetzt ist."""
    client.get("/")
    response = client.get("/nachweise")
    body = response.data.decode()
    # Alle-Chip mit chip-aktiv-Klasse muss in einem Element gemeinsam vorkommen
    assert 'chip-aktiv' in body
    # Prüfen dass der Alle-Link chip-aktiv trägt: href=/nachweise und chip-aktiv müssen nahe beieinander sein
    import re
    alle_chip = re.search(r'href="/nachweise"[^>]*chip-aktiv|chip-aktiv[^"]*"[^>]*href="/nachweise"', body)
    assert alle_chip is not None, "Alle-Chip hat keine chip-aktiv-Klasse"


def test_filter_chip_ist_direkt_verlinkbar(client, tmp_path):
    """Filter-Chips sind Links mit ?typ=... Query-Parameter."""
    client.get("/")
    db_path = tmp_path / "checker.db"
    _db_insert_task(db_path, status="NEU", pruefungstyp="G25")

    response = client.get("/nachweise")
    body = response.data.decode()
    assert 'href="/nachweise?typ=G25"' in body


def test_filter_ignoriert_erledigte_tasks(client, tmp_path):
    """ERLEDIGT-Tasks erscheinen auch bei passendem Filter nicht."""
    client.get("/")
    db_path = tmp_path / "checker.db"
    _db_insert_task(db_path, status="ERLEDIGT", pruefungstyp="G25", betreff="Erledigter G25")

    response = client.get("/nachweise?typ=G25")
    body = response.data.decode()
    assert "Erledigter G25" not in body


def test_filter_chips_nur_bei_vorhandenen_typen(client, tmp_path):
    """Wenn kein Task einen Prüfungstyp hat, erscheinen keine Typ-Chips."""
    client.get("/")
    db_path = tmp_path / "checker.db"
    _db_insert_task(db_path, status="NEU", pruefungstyp=None, betreff="Typ unbekannt")

    response = client.get("/nachweise")
    body = response.data.decode()
    # Kein spezifischer Typ-Chip – nur Alle-Chip
    assert 'href="/nachweise?typ=' not in body

