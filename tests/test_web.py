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
        "message_id": None,
        "pruefungstyp": None,
        "faelligkeitsdatum": None,
        "mitglied_name": None,
        "mitglied_nr": None,
        "raw_email": None,
        "raw_text": None,
        "anhang_count": 0,
        "kandidat_absender_nr": None,
        "kandidat_absender_name": None,
        "kandidat_dokument_nr": None,
        "kandidat_dokument_name": None,
    }
    defaults.update(kwargs)
    db = _sqlite3.connect(db_path)
    cursor = db.execute(
        """INSERT INTO tasks
           (status, empfangen_am, von_email, betreff, message_id,
            pruefungstyp, faelligkeitsdatum, mitglied_name, mitglied_nr,
            raw_email, raw_text, anhang_count,
            kandidat_absender_nr, kandidat_absender_name,
            kandidat_dokument_nr, kandidat_dokument_name)
           VALUES (:status, :empfangen_am, :von_email, :betreff, :message_id,
                   :pruefungstyp, :faelligkeitsdatum, :mitglied_name, :mitglied_nr,
                   :raw_email, :raw_text, :anhang_count,
                   :kandidat_absender_nr, :kandidat_absender_name,
                   :kandidat_dokument_nr, :kandidat_dokument_name)""",
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


def test_nachweise_karte_abweichende_zuordnung_css_klasse(client, tmp_path):
    """ABWEICHENDE_ZUORDNUNG-Karten tragen die CSS-Klasse karte-abweichende-zuordnung."""
    client.get("/")
    db_path = tmp_path / "checker.db"
    _db_insert_task(db_path, status="ABWEICHENDE_ZUORDNUNG")

    response = client.get("/nachweise")
    assert b"karte-abweichende-zuordnung" in response.data


def test_nachweise_abweichende_zuordnung_zeigt_zwei_kandidaten(client, tmp_path):
    """Beide Kandidaten erscheinen als anklickbare Vorschläge mit Rollenbezeichnung."""
    client.get("/")
    db_path = tmp_path / "checker.db"
    task_id = _db_insert_task(
        db_path,
        status="ABWEICHENDE_ZUORDNUNG",
        kandidat_absender_nr="001",
        kandidat_absender_name="Max Mustermann",
        kandidat_dokument_nr="002",
        kandidat_dokument_name="Erika Musterfrau",
    )

    response = client.get("/nachweise")
    body = response.data.decode()
    assert "Max Mustermann" in body
    assert "Erika Musterfrau" in body
    assert "Absender" in body
    assert "im Dokument erkannt" in body
    assert body.count(f'/tasks/{task_id}/zuordnen') >= 2


def test_nachweise_abweichende_zuordnung_zeigt_erledigt_button(client, tmp_path):
    """ABWEICHENDE_ZUORDNUNG-Karte kann direkt über Erledigt abgeschlossen werden."""
    client.get("/")
    db_path = tmp_path / "checker.db"
    task_id = _db_insert_task(db_path, status="ABWEICHENDE_ZUORDNUNG")

    response = client.get("/nachweise")
    body = response.data.decode()
    assert f"/tasks/{task_id}/erledigt" in body


def test_zuordnen_via_kandidat_button_setzt_status_neu(client, tmp_path):
    """Klick auf einen Kandidaten-Button (POST /zuordnen mit vorbelegter pers_nr) löst den Task auf."""
    import sqlite3
    client.get("/")
    db_path = tmp_path / "checker.db"
    task_id = _db_insert_task(
        db_path,
        status="ABWEICHENDE_ZUORDNUNG",
        kandidat_absender_nr="001",
        kandidat_absender_name="Max Mustermann",
        kandidat_dokument_nr="002",
        kandidat_dokument_name="Erika Musterfrau",
    )

    (tmp_path / "latest.xls").write_bytes(b"dummy")
    with patch("web.extractor.load_members_from_xls", return_value=[
        {"pers_nr": "002", "vorname": "Erika", "nachname": "Musterfrau"}
    ]):
        response = client.post(f"/tasks/{task_id}/zuordnen", data={"pers_nr": "002"}, follow_redirects=True)
    assert response.status_code == 200

    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    db.close()
    assert row["status"] == "NEU"
    assert row["mitglied_nr"] == "002"
    assert row["kandidat_absender_nr"] is None
    assert row["kandidat_dokument_nr"] is None


def test_abweichende_zuordnung_loeschbar(client, tmp_path):
    """ABWEICHENDE_ZUORDNUNG-Tasks können wie UNKLARE_ZUORDNUNG gelöscht werden."""
    import sqlite3
    client.get("/")
    db_path = tmp_path / "checker.db"
    task_id = _db_insert_task(db_path, status="ABWEICHENDE_ZUORDNUNG")

    response = client.post(f"/tasks/{task_id}/loeschen", follow_redirects=True)
    assert response.status_code == 200

    db = sqlite3.connect(db_path)
    row = db.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
    db.close()
    assert row is None


def test_abweichende_zuordnung_erledigt_ohne_zuordnung(client, tmp_path):
    """ABWEICHENDE_ZUORDNUNG kann direkt über Erledigt abgeschlossen werden, ohne vorherige Zuordnung."""
    import sqlite3
    client.get("/")
    db_path = tmp_path / "checker.db"
    task_id = _db_insert_task(db_path, status="ABWEICHENDE_ZUORDNUNG")

    response = client.post(f"/tasks/{task_id}/erledigt", follow_redirects=True)
    assert response.status_code == 200

    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
    db.close()
    assert row["status"] == "ERLEDIGT"


def test_dashboard_zaehlt_abweichende_zuordnung(client, tmp_path):
    """Dashboard zeigt eine Zählung für offene ABWEICHENDE_ZUORDNUNG-Tasks."""
    client.get("/")
    db_path = tmp_path / "checker.db"
    _db_insert_task(db_path, status="ABWEICHENDE_ZUORDNUNG")
    _db_insert_task(db_path, status="ABWEICHENDE_ZUORDNUNG")

    response = client.get("/")
    body = response.data.decode()
    assert "2" in body
    assert "abweichende" in body.lower()


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


def test_tasks_anhaenge_liefert_json_metadaten(client, tmp_path):
    """GET /tasks/<id>/anhaenge liefert Index, Dateiname und Content-Type für Bild- und PDF-Anhang."""
    import email.mime.multipart
    import email.mime.image
    import email.mime.application

    client.get("/")
    db_path = tmp_path / "checker.db"

    msg = email.mime.multipart.MIMEMultipart()
    msg["From"] = "test@example.com"
    msg["Subject"] = "Test"
    bild = email.mime.image.MIMEImage(b"fakeimgdata", _subtype="jpeg")
    bild.add_header("Content-Disposition", "attachment", filename="foto.jpg")
    msg.attach(bild)
    pdf = email.mime.application.MIMEApplication(b"%PDF-1.4 fake", _subtype="pdf")
    pdf.add_header("Content-Disposition", "attachment", filename="nachweis.pdf")
    msg.attach(pdf)

    task_id = _db_insert_task(db_path, status="NEU", raw_email=msg.as_bytes(), anhang_count=2)

    response = client.get(f"/tasks/{task_id}/anhaenge")
    assert response.status_code == 200
    data = response.get_json()
    assert data == [
        {"index": 0, "filename": "foto.jpg", "content_type": "image/jpeg"},
        {"index": 1, "filename": "nachweis.pdf", "content_type": "application/pdf"},
    ]


def test_tasks_anhaenge_404_bei_fehlendem_task(client, tmp_path):
    """GET /tasks/<id>/anhaenge liefert 404 bei nicht existierendem Task."""
    client.get("/")
    response = client.get("/tasks/999999/anhaenge")
    assert response.status_code == 404


def test_tasks_anhaenge_404_ohne_raw_email(client, tmp_path):
    """GET /tasks/<id>/anhaenge liefert 404, wenn kein raw_email vorhanden ist."""
    client.get("/")
    db_path = tmp_path / "checker.db"
    task_id = _db_insert_task(db_path, status="NEU", raw_email=None)

    response = client.get(f"/tasks/{task_id}/anhaenge")
    assert response.status_code == 404


def test_reanalyse_erzeugt_abweichende_zuordnung(client, tmp_path):
    """Re-Analyse erzeugt denselben ABWEICHENDE_ZUORDNUNG-Status wie der reguläre IMAP-Eingang (Issue #38)."""
    import sqlite3
    client.get("/")
    db_path = tmp_path / "checker.db"

    raw = b"From: Max Mustermann <max@example.com>\r\nSubject: Nachweis\r\n\r\nBody"
    task_id = _db_insert_task(db_path, status="NEU", raw_email=raw, mitglied_nr="001", mitglied_name="Max Mustermann")

    members = [
        {"pers_nr": "001", "vorname": "Max", "nachname": "Mustermann", "email": "max@example.com"},
        {"pers_nr": "002", "vorname": "Erika", "nachname": "Musterfrau", "email": "erika@example.com"},
    ]
    extraction = {
        "pruefungstyp": "G25",
        "faelligkeitsdatum": None,
        "mitglied": members[0],
        "match_score": 0.95,
        "dokument_mitglied": members[1],
        "dokument_match_score": 0.9,
        "raw_text": "G25",
    }
    (tmp_path / "latest.xls").write_bytes(b"dummy")
    with patch("web.extractor.load_members_from_xls", return_value=members), \
         patch("web.extractor.extract_from_email", return_value=extraction):
        response = client.post(f"/tasks/{task_id}/reanalyse", follow_redirects=True)
    assert response.status_code == 200

    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    db.close()
    assert row["status"] == "ABWEICHENDE_ZUORDNUNG"
    assert row["mitglied_nr"] is None
    assert row["kandidat_absender_nr"] == "001"
    assert row["kandidat_dokument_nr"] == "002"


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


# --- Manuelle Bestätigung ---

def _db_insert_verifikation(db_path, **kwargs):
    """Hilfsfunktion: email_verifikation-Zeile in die Test-DB einfügen."""
    defaults = {
        "pers_nr": "001",
        "vorname": "Max",
        "nachname": "Mustermann",
        "email": "max@example.com",
        "status": "ausstehend",
        "adresse_geaendert": 0,
    }
    defaults.update(kwargs)
    db = _sqlite3.connect(db_path)
    db.execute(
        """INSERT INTO email_verifikation
           (pers_nr, vorname, nachname, email, status, adresse_geaendert)
           VALUES (:pers_nr, :vorname, :nachname, :email, :status, :adresse_geaendert)""",
        defaults,
    )
    db.commit()
    db.close()


def test_manuell_bestaetigen_setzt_adresse_geaendert_zurueck(client, tmp_path):
    """Manuelle Bestätigung setzt adresse_geaendert zurück (ADR 0010)."""
    client.get("/")
    db_path = tmp_path / "checker.db"
    _db_insert_verifikation(db_path, status="ausstehend", adresse_geaendert=1)

    response = client.post("/email-pruefung/001/manuell-bestaetigen", follow_redirects=True)
    assert response.status_code == 200

    db = _sqlite3.connect(db_path)
    db.row_factory = _sqlite3.Row
    row = db.execute("SELECT * FROM email_verifikation WHERE pers_nr = '001'").fetchone()
    db.close()
    assert row["status"] == "bestaetigt"
    assert row["adresse_geaendert"] == 0


# --- Task-Antworten / Thread-Verlauf: Issue #41 ---

def _db_insert_task_nachricht(db_path, **kwargs):
    defaults = {
        "task_id": None,
        "richtung": "eingehend",
        "zeitstempel": _dt.now().isoformat(timespec="seconds"),
        "von_email": "max@example.com",
        "an_email": None,
        "betreff": "G25 Nachweis",
        "text": None,
        "raw_email": None,
        "message_id": None,
        "in_reply_to": None,
        "imap_uid": None,
    }
    defaults.update(kwargs)
    db = _sqlite3.connect(db_path)
    db.execute(
        """INSERT INTO task_nachrichten
           (task_id, richtung, zeitstempel, von_email, an_email, betreff, text,
            raw_email, message_id, in_reply_to, imap_uid)
           VALUES (:task_id, :richtung, :zeitstempel, :von_email, :an_email, :betreff, :text,
                   :raw_email, :message_id, :in_reply_to, :imap_uid)""",
        defaults,
    )
    db.commit()
    db.close()


def test_antworten_sendet_und_speichert_ausgehende_nachricht(client, tmp_path):
    """POST /tasks/<id>/antworten versendet und speichert eine ausgehende Task-Nachricht mit Re:-Betreff."""
    client.get("/")
    db_path = tmp_path / "checker.db"
    task_id = _db_insert_task(
        db_path, status="NEU", von_email="max@example.com", betreff="G25 Nachweis",
        message_id="<original-1@example.com>",
    )

    with patch("web.app.send_task_antwort", return_value="<neue-antwort@example.com>") as mock_send:
        response = client.post(
            f"/tasks/{task_id}/antworten",
            data={"an_email": "max@example.com", "text": "Bitte Nachweis nachreichen."},
            follow_redirects=True,
        )
    assert response.status_code == 200
    mock_send.assert_called_once()
    _, kwargs = mock_send.call_args
    assert kwargs["to_addr"] == "max@example.com"
    assert kwargs["betreff"] == "Re: G25 Nachweis"
    assert kwargs["text"] == "Bitte Nachweis nachreichen."

    db = _sqlite3.connect(db_path)
    db.row_factory = _sqlite3.Row
    row = db.execute(
        "SELECT * FROM task_nachrichten WHERE task_id = ? AND richtung = 'ausgehend'", (task_id,)
    ).fetchone()
    task_row = db.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
    db.close()
    assert row is not None
    assert row["an_email"] == "max@example.com"
    assert row["betreff"] == "Re: G25 Nachweis"
    assert row["text"] == "Bitte Nachweis nachreichen."
    assert row["message_id"] == "<neue-antwort@example.com>"
    assert task_row["status"] == "NEU"  # Status bleibt unverändert


def test_antworten_smtp_fehler_hinterlaesst_keine_nachricht(client, tmp_path):
    """Schlägt der SMTP-Versand fehl, wird keine Task-Nachricht gespeichert und ein Fehler gemeldet."""
    client.get("/")
    db_path = tmp_path / "checker.db"
    task_id = _db_insert_task(db_path, status="NEU", von_email="max@example.com", betreff="G25 Nachweis")

    with patch("web.app.send_task_antwort", side_effect=Exception("SMTP down")):
        response = client.post(
            f"/tasks/{task_id}/antworten",
            data={"an_email": "max@example.com", "text": "Bitte Nachweis nachreichen."},
            follow_redirects=True,
        )
    assert response.status_code == 200

    db = _sqlite3.connect(db_path)
    count = db.execute("SELECT COUNT(*) FROM task_nachrichten").fetchone()[0]
    db.close()
    assert count == 0


def test_antworten_ohne_empfaenger_zeigt_fehler(client, tmp_path):
    """Leerer Empfänger wird abgelehnt, ohne SMTP-Versand auszulösen."""
    client.get("/")
    db_path = tmp_path / "checker.db"
    task_id = _db_insert_task(db_path, status="NEU", betreff="G25 Nachweis")

    with patch("web.app.send_task_antwort") as mock_send:
        response = client.post(
            f"/tasks/{task_id}/antworten",
            data={"an_email": "", "text": "Text"},
            follow_redirects=True,
        )
    assert response.status_code == 200
    mock_send.assert_not_called()


def test_antworten_ungueltige_email_zeigt_fehler(client, tmp_path):
    """Ein Empfänger ohne gültiges E-Mail-Format wird abgelehnt."""
    client.get("/")
    db_path = tmp_path / "checker.db"
    task_id = _db_insert_task(db_path, status="NEU", betreff="G25 Nachweis")

    with patch("web.app.send_task_antwort") as mock_send:
        response = client.post(
            f"/tasks/{task_id}/antworten",
            data={"an_email": "keine-email", "text": "Text"},
            follow_redirects=True,
        )
    assert response.status_code == 200
    mock_send.assert_not_called()


def test_antworten_ohne_text_zeigt_fehler(client, tmp_path):
    """Leerer Antworttext wird abgelehnt."""
    client.get("/")
    db_path = tmp_path / "checker.db"
    task_id = _db_insert_task(db_path, status="NEU", von_email="max@example.com", betreff="G25 Nachweis")

    with patch("web.app.send_task_antwort") as mock_send:
        response = client.post(
            f"/tasks/{task_id}/antworten",
            data={"an_email": "max@example.com", "text": "  "},
            follow_redirects=True,
        )
    assert response.status_code == 200
    mock_send.assert_not_called()


def test_antworten_unbekannte_id_gibt_404(client):
    response = client.post(
        "/tasks/9999/antworten", data={"an_email": "x@example.com", "text": "Text"}
    )
    assert response.status_code == 404


def test_antworten_verwendet_in_reply_to_wenn_letzte_nachricht_eingehend(client, tmp_path):
    """Betreff-Ableitung nutzt den Task-Betreff; In-Reply-To wird an send_task_antwort weitergereicht."""
    client.get("/")
    db_path = tmp_path / "checker.db"
    task_id = _db_insert_task(
        db_path, status="NEU", von_email="max@example.com", betreff="Re: G25 Nachweis",
        message_id="<original-1@example.com>",
    )

    with patch("web.app.send_task_antwort", return_value="<neue-antwort@example.com>") as mock_send:
        client.post(
            f"/tasks/{task_id}/antworten",
            data={"an_email": "max@example.com", "text": "Text"},
            follow_redirects=True,
        )

    _, kwargs = mock_send.call_args
    assert kwargs["betreff"] == "Re: G25 Nachweis"  # kein doppeltes "Re: Re:"
    assert kwargs["in_reply_to"] == "<original-1@example.com>"


def test_thread_route_liefert_empfaenger_betreff_und_verlauf(client, tmp_path):
    """GET /tasks/<id>/thread liefert Empfänger-Vorschlag, Re:-Betreff und chronologischen Verlauf."""
    client.get("/")
    db_path = tmp_path / "checker.db"
    task_id = _db_insert_task(
        db_path, status="NEU", von_email="max@example.com", betreff="G25 Nachweis",
    )
    _db_insert_task_nachricht(
        db_path, task_id=task_id, richtung="eingehend",
        zeitstempel="2026-01-01T10:00:00", von_email="max@example.com",
        betreff="G25 Nachweis", text=None,
    )
    _db_insert_task_nachricht(
        db_path, task_id=task_id, richtung="ausgehend",
        zeitstempel="2026-01-02T10:00:00", an_email="max@example.com",
        betreff="Re: G25 Nachweis", text="Bitte nachreichen.",
    )

    response = client.get(f"/tasks/{task_id}/thread")
    assert response.status_code == 200
    data = response.get_json()
    assert data["empfaenger_vorschlag"] == "max@example.com"
    assert data["betreff"] == "Re: G25 Nachweis"
    assert len(data["nachrichten"]) == 2
    assert data["nachrichten"][0]["richtung"] == "eingehend"
    assert data["nachrichten"][1]["richtung"] == "ausgehend"
    assert data["nachrichten"][1]["text"] == "Bitte nachreichen."


def test_thread_route_404_bei_fehlendem_task(client):
    response = client.get("/tasks/9999/thread")
    assert response.status_code == 404


def test_erledigt_verschiebt_alle_thread_uids(client, tmp_path):
    """POST /tasks/<id>/erledigt verschiebt die ursprüngliche UID und alle eingehenden Thread-UIDs einzeln."""
    client.get("/")
    db_path = tmp_path / "checker.db"
    task_id = _db_insert_task(db_path, status="NEU")
    db = _sqlite3.connect(db_path)
    db.execute("UPDATE tasks SET imap_uid = '10' WHERE id = ?", (task_id,))
    db.commit()
    db.close()
    _db_insert_task_nachricht(db_path, task_id=task_id, richtung="eingehend", imap_uid="20")
    _db_insert_task_nachricht(db_path, task_id=task_id, richtung="eingehend", imap_uid="21")
    _db_insert_task_nachricht(db_path, task_id=task_id, richtung="ausgehend", imap_uid=None)

    with patch("web.app.imap_move_to_nachweis") as mock_move:
        response = client.post(f"/tasks/{task_id}/erledigt", follow_redirects=True)
    assert response.status_code == 200

    verschobene_uids = {call.args[1] for call in mock_move.call_args_list}
    assert verschobene_uids == {"10", "20", "21"}


def test_erledigt_ein_fehlschlag_blockiert_andere_uids_nicht(client, tmp_path):
    """Ein IMAP-Fehler bei einer Thread-UID hindert die übrigen nicht am Verschieben."""
    client.get("/")
    db_path = tmp_path / "checker.db"
    task_id = _db_insert_task(db_path, status="NEU")
    db = _sqlite3.connect(db_path)
    db.execute("UPDATE tasks SET imap_uid = '10' WHERE id = ?", (task_id,))
    db.commit()
    db.close()
    _db_insert_task_nachricht(db_path, task_id=task_id, richtung="eingehend", imap_uid="20")

    def _move_side_effect(cfg, uid, ordner):
        if uid == "10":
            raise Exception("IMAP kaputt")

    with patch("web.app.imap_move_to_nachweis", side_effect=_move_side_effect) as mock_move:
        response = client.post(f"/tasks/{task_id}/erledigt", follow_redirects=True)
    assert response.status_code == 200

    verschobene_uids = {call.args[1] for call in mock_move.call_args_list}
    assert verschobene_uids == {"10", "20"}

    db = _sqlite3.connect(db_path)
    db.row_factory = _sqlite3.Row
    row = db.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
    db.close()
    assert row["status"] == "ERLEDIGT"

