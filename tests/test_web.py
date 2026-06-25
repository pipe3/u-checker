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


# --- Blocking-Logik: Issue #9 ---

def test_do_run_automatisch_blockiert_bei_offenen_tasks(tmp_path):
    """Automatischer Run (manuell=False) mit offenen Tasks → status='blockiert' in DB."""
    from web.app import app, _do_run, init_db

    app.config["TESTING"] = True
    app.config["DATA_DIR"] = tmp_path
    (tmp_path / "latest.xls").write_bytes(b"dummy")

    with app.app_context():
        init_db()
        import sqlite3
        db = sqlite3.connect(tmp_path / "checker.db")
        db.execute(
            "INSERT INTO tasks (status, empfangen_am) VALUES ('NEU', '2026-01-01T10:00:00')"
        )
        db.commit()
        db.close()

    with app.app_context(), \
         patch("web.app.check_examinations") as mock_check, \
         patch("web.app._send_blockier_benachrichtigung"):
        result = _do_run(dry_run=False, manuell=False)

    mock_check.assert_not_called()

    db = sqlite3.connect(tmp_path / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    db.close()
    assert row["status"] == "blockiert"


def test_do_run_automatisch_kein_block_ohne_offene_tasks(tmp_path):
    """Automatischer Run (manuell=False) ohne offene Tasks → läuft normal durch."""
    from web.app import app, _do_run, init_db

    app.config["TESTING"] = True
    app.config["DATA_DIR"] = tmp_path
    (tmp_path / "latest.xls").write_bytes(b"dummy")

    with app.app_context(), \
         patch("web.app.check_examinations", return_value=[]) as mock_check, \
         patch("web.app.send_notifications", return_value=0), \
         patch("web.app.send_summary"):
        init_db()
        _do_run(dry_run=False, manuell=False)

    mock_check.assert_called_once()


def test_do_run_manuell_laeuft_trotz_offener_tasks(tmp_path):
    """Manueller Run (manuell=True) läuft trotz offener Tasks durch."""
    from web.app import app, _do_run, init_db

    app.config["TESTING"] = True
    app.config["DATA_DIR"] = tmp_path
    (tmp_path / "latest.xls").write_bytes(b"dummy")

    with app.app_context():
        init_db()
        import sqlite3
        db = sqlite3.connect(tmp_path / "checker.db")
        db.execute(
            "INSERT INTO tasks (status, empfangen_am) VALUES ('NEU', '2026-01-01T10:00:00')"
        )
        db.commit()
        db.close()

    with app.app_context(), \
         patch("web.app.check_examinations", return_value=[]) as mock_check, \
         patch("web.app.send_notifications", return_value=0), \
         patch("web.app.send_summary"):
        _do_run(dry_run=False, manuell=True)

    mock_check.assert_called_once()


def test_do_run_automatisch_sendet_admin_email_bei_blockierung(tmp_path):
    """Blockierter automatischer Run sendet Benachrichtigungs-Email an zusammenfassung_an."""
    from web.app import app, _do_run, init_db, save_settings

    app.config["TESTING"] = True
    app.config["DATA_DIR"] = tmp_path
    (tmp_path / "latest.xls").write_bytes(b"dummy")

    with app.app_context():
        init_db()
        save_settings({"zusammenfassung_an": "admin@example.com"})
        import sqlite3
        db = sqlite3.connect(tmp_path / "checker.db")
        db.execute(
            "INSERT INTO tasks (status, empfangen_am) VALUES ('NEU', '2026-01-01T10:00:00')"
        )
        db.commit()
        db.close()

    with app.app_context(), \
         patch("web.app._send_blockier_benachrichtigung") as mock_notify:
        _do_run(dry_run=False, manuell=False)

    mock_notify.assert_called_once_with(1, dry_run=False)


def test_do_run_automatisch_dry_run_sendet_keine_admin_email(tmp_path):
    """dry_run=True blockiert, aber sendet keine echte Admin-Email."""
    from web.app import app, _do_run, init_db

    app.config["TESTING"] = True
    app.config["DATA_DIR"] = tmp_path
    (tmp_path / "latest.xls").write_bytes(b"dummy")

    with app.app_context():
        init_db()
        import sqlite3
        db = sqlite3.connect(tmp_path / "checker.db")
        db.execute(
            "INSERT INTO tasks (status, empfangen_am) VALUES ('NEU', '2026-01-01T10:00:00')"
        )
        db.commit()
        db.close()

    with app.app_context(), \
         patch("web.app.send_simple_mail") as mock_mail:
        _do_run(dry_run=True, manuell=False)

    mock_mail.assert_not_called()


def test_index_zeigt_neue_labels(client):
    """UI-Labels auf der Startseite verwenden fachliche statt technische Bezeichnungen."""
    response = client.get("/")
    body = response.data.decode("utf-8")
    assert "Fälligkeiten prüfen" in body     # Abschnittsüberschrift
    assert "Jetzt prüfen" in body            # Button (eigenständig von der Überschrift)
    assert "Vorschau (kein Versand)" in body  # Checkbox-Label
    assert "Script ausführen" not in body
    assert "Dry-Run" not in body


def test_manueller_run_route_nicht_blockiert(client, tmp_path):
    """POST /run läuft trotz offener Tasks durch (manueller Run)."""
    xls_path = tmp_path / "latest.xls"
    xls_path.write_bytes(b"dummy")

    # Erst eine Anfrage, um DB zu initialisieren
    client.get("/")

    import sqlite3
    db = sqlite3.connect(tmp_path / "checker.db")
    db.execute(
        "INSERT INTO tasks (status, empfangen_am) VALUES ('NEU', '2026-01-01T10:00:00')"
    )
    db.commit()
    db.close()

    with patch("web.app.check_examinations", return_value=[]) as mock_check, \
         patch("web.app.send_notifications", return_value=0), \
         patch("web.app.send_summary"):
        response = client.post("/run", data={}, follow_redirects=True)

    assert response.status_code == 200
    mock_check.assert_called_once()


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


# --- Ergebnisseite: Issue #14 ---

def test_run_rendert_ergebnisseite_direkt(client, tmp_path):
    """POST /run rendert direkt ergebnis.html (status 200, kein Redirect)."""
    (tmp_path / "latest.xls").write_bytes(b"dummy")

    with patch("web.app.check_examinations", return_value=[]), \
         patch("web.app.send_notifications", return_value=0), \
         patch("web.app.send_summary"):
        response = client.post("/run", data={})

    assert response.status_code == 200


def test_run_ergebnisseite_zeigt_person(client, tmp_path):
    """Ergebnisseite zeigt Namen der betroffenen Person."""
    from datetime import date, timedelta
    from u_checker.checker import Person, Pruefung

    (tmp_path / "latest.xls").write_bytes(b"dummy")

    persons = [
        Person(pers_nr="001", vorname="Max", nachname="Mustermann", email="max@example.com", pruefungen=[
            Pruefung(typ="G25", beschreibung="G25-Untersuchung", datum=date.today() - timedelta(days=1), status="abgelaufen"),
        ])
    ]

    with patch("web.app.check_examinations", return_value=persons), \
         patch("web.app.send_notifications", return_value=1), \
         patch("web.app.send_summary"):
        response = client.post("/run", data={})

    assert response.status_code == 200
    assert b"Mustermann" in response.data


def test_run_ergebnisseite_abgelaufen_vor_warnung(client, tmp_path):
    """Abgelaufene Einträge erscheinen im HTML vor Warnungen (via CSS-Klasse in Tabellenzeile)."""
    from datetime import date, timedelta
    from u_checker.checker import Person, Pruefung

    (tmp_path / "latest.xls").write_bytes(b"dummy")

    persons = [
        Person(pers_nr="001", vorname="Anna", nachname="Schmidt", email="anna@example.com", pruefungen=[
            Pruefung(typ="G25", beschreibung="G25-Frist", datum=date.today() + timedelta(days=30), status="warnung"),
        ]),
        Person(pers_nr="002", vorname="Bob", nachname="Mueller", email="bob@example.com", pruefungen=[
            Pruefung(typ="G25", beschreibung="G25-Ueberfaellig", datum=date.today() - timedelta(days=5), status="abgelaufen"),
        ]),
    ]

    with patch("web.app.check_examinations", return_value=persons), \
         patch("web.app.send_notifications", return_value=2), \
         patch("web.app.send_summary"):
        response = client.post("/run", data={})

    html = response.data.decode("utf-8")
    # class="status-abgelaufen" / class="status-warnung" erscheinen nur in Tabellenzeilen
    pos_abgelaufen = html.index('class="status-abgelaufen"')
    pos_warnung = html.index('class="status-warnung"')
    assert pos_abgelaufen < pos_warnung


def test_run_ergebnisseite_zaehler_stimmen(client, tmp_path):
    """Kurzübersicht-Zähler stimmen mit Mock-Daten überein."""
    from datetime import date, timedelta
    from u_checker.checker import Person, Pruefung

    (tmp_path / "latest.xls").write_bytes(b"dummy")

    persons = [
        Person(pers_nr="001", vorname="Max", nachname="Muster", email="max@example.com", pruefungen=[
            Pruefung(typ="G25", beschreibung="G25", datum=date.today() - timedelta(days=1), status="abgelaufen"),
        ]),
        Person(pers_nr="002", vorname="Lisa", nachname="Lauf", email="lisa@example.com", pruefungen=[
            Pruefung(typ="G25", beschreibung="G25", datum=date.today() + timedelta(days=30), status="warnung"),
        ]),
    ]

    with patch("web.app.check_examinations", return_value=persons), \
         patch("web.app.send_notifications", return_value=2), \
         patch("web.app.send_summary"):
        response = client.post("/run", data={})

    html = response.data.decode("utf-8")
    assert "2 Person" in html
    assert "1 abgelaufen" in html
    assert "1 Warnung" in html
    assert "2 E-Mail" in html


def test_run_ergebnisseite_zaehler_person_mit_gemischtem_status(client, tmp_path):
    """Person mit abgelaufener UND Warnung-Prüfung zählt in beiden Kacheln."""
    from datetime import date, timedelta
    from u_checker.checker import Person, Pruefung

    (tmp_path / "latest.xls").write_bytes(b"dummy")

    persons = [
        Person(pers_nr="001", vorname="Max", nachname="Muster", email="max@example.com", pruefungen=[
            Pruefung(typ="G25", beschreibung="G25", datum=date.today() - timedelta(days=1), status="abgelaufen"),
            Pruefung(typ="G26", beschreibung="G26", datum=date.today() + timedelta(days=30), status="warnung"),
        ]),
    ]

    with patch("web.app.check_examinations", return_value=persons), \
         patch("web.app.send_notifications", return_value=1), \
         patch("web.app.send_summary"):
        response = client.post("/run", data={})

    html = response.data.decode("utf-8")
    assert "1 Person" in html
    assert "1 abgelaufen" in html
    assert "1 Warnung" in html


def test_run_ergebnisseite_dry_run_kennzeichnung(client, tmp_path):
    """Dry-Run ist auf der Ergebnisseite als 'Vorschau' gekennzeichnet."""
    (tmp_path / "latest.xls").write_bytes(b"dummy")

    with patch("web.app.check_examinations", return_value=[]), \
         patch("web.app.send_notifications", return_value=0), \
         patch("web.app.send_summary"):
        response = client.post("/run", data={"dry_run": "1"})

    html = response.data.decode("utf-8")
    assert "Vorschau" in html


def test_run_ergebnisseite_leer_meldung(client, tmp_path):
    """Ergebnisseite zeigt Leermeldung wenn keine Fälligkeiten."""
    (tmp_path / "latest.xls").write_bytes(b"dummy")

    with patch("web.app.check_examinations", return_value=[]), \
         patch("web.app.send_notifications", return_value=0), \
         patch("web.app.send_summary"):
        response = client.post("/run", data={})

    html = response.data.decode("utf-8")
    assert "Keine Fälligkeiten" in html


def test_run_ergebnisseite_link_zur_startseite(client, tmp_path):
    """Ergebnisseite enthält Link zurück zur Startseite."""
    (tmp_path / "latest.xls").write_bytes(b"dummy")

    with patch("web.app.check_examinations", return_value=[]), \
         patch("web.app.send_notifications", return_value=0), \
         patch("web.app.send_summary"):
        response = client.post("/run", data={})

    html = response.data.decode("utf-8")
    assert 'href="/"' in html


def test_run_ergebnisseite_keine_email_adressen(client, tmp_path):
    """E-Mail-Adressen werden nicht auf der Ergebnisseite angezeigt."""
    from datetime import date, timedelta
    from u_checker.checker import Person, Pruefung

    (tmp_path / "latest.xls").write_bytes(b"dummy")

    persons = [
        Person(pers_nr="001", vorname="Max", nachname="Muster", email="max@example.com", pruefungen=[
            Pruefung(typ="G25", beschreibung="G25", datum=date.today() - timedelta(days=1), status="abgelaufen"),
        ]),
    ]

    with patch("web.app.check_examinations", return_value=persons), \
         patch("web.app.send_notifications", return_value=1), \
         patch("web.app.send_summary"):
        response = client.post("/run", data={})

    html = response.data.decode("utf-8")
    assert "max@example.com" not in html


def test_run_fehler_redirectet_zur_startseite(client, tmp_path):
    """Bei Exception weiterhin Flash + Redirect zur Startseite."""
    (tmp_path / "latest.xls").write_bytes(b"dummy")

    with patch("web.app.check_examinations", side_effect=ValueError("Testfehler")):
        response = client.post("/run", data={}, follow_redirects=True)

    assert response.status_code == 200
    assert b"Fehler" in response.data
