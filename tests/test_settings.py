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


# --- Settings-Seite erreichbar ---

def test_settings_erreichbar(client):
    response = client.get("/settings")
    assert response.status_code == 200
    assert "Einstellungen" in response.data.decode()


def test_settings_zeigt_smtp_felder(client):
    html = client.get("/settings").data.decode()
    assert 'name="smtp_host"' in html
    assert 'name="smtp_port"' in html
    assert 'name="smtp_user"' in html
    assert 'name="smtp_from"' in html


def test_settings_zeigt_kommandanten_feld(client):
    html = client.get("/settings").data.decode()
    assert 'name="kommandanten_cc"' in html


def test_settings_zeigt_warn_days_feld(client):
    html = client.get("/settings").data.decode()
    assert 'name="warn_days"' in html


def test_settings_zeigt_pruefungstypen_feld(client):
    html = client.get("/settings").data.decode()
    assert 'name="pruefungstypen"' in html


def test_settings_zeigt_zusammenfassung_feld(client):
    html = client.get("/settings").data.decode()
    assert 'name="zusammenfassung_an"' in html


# --- Settings speichern ---

def test_settings_speichern_redirect(client):
    response = client.post("/settings", data={
        "smtp_host": "smtp.test.de",
        "smtp_port": "587",
        "smtp_user": "user@test.de",
        "smtp_password": "",
        "smtp_from": "from@test.de",
        "kommandanten_cc": "chef@test.de",
        "zusammenfassung_an": "",
        "warn_days": "60",
        "pruefungstypen": "G25,G26",
        "archiv_tage": "365",
        "script_intervall": "wöchentlich",
    })
    assert response.status_code in (302, 200)


def test_settings_werden_in_db_gespeichert(client, tmp_path):
    client.post("/settings", data={
        "smtp_host": "mail.feuerwehr.de",
        "smtp_port": "465",
        "smtp_user": "checker@feuerwehr.de",
        "smtp_password": "geheim",
        "smtp_from": "noreply@feuerwehr.de",
        "kommandanten_cc": "k1@fw.de,k2@fw.de",
        "zusammenfassung_an": "uebersicht@fw.de",
        "warn_days": "45",
        "pruefungstypen": "G25",
        "archiv_tage": "180",
        "script_intervall": "monatlich",
    })
    db = sqlite3.connect(tmp_path / "checker.db")
    db.row_factory = sqlite3.Row
    rows = {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM settings").fetchall()}
    db.close()

    assert rows["smtp_host"] == "mail.feuerwehr.de"
    assert rows["smtp_port"] == "465"
    assert rows["warn_days"] == "45"
    assert rows["pruefungstypen"] == "G25"
    assert rows["kommandanten_cc"] == "k1@fw.de,k2@fw.de"


def test_settings_werden_nach_neustart_geladen(client, tmp_path):
    client.post("/settings", data={
        "smtp_host": "saved.host.de",
        "smtp_port": "587",
        "smtp_user": "",
        "smtp_password": "",
        "smtp_from": "",
        "kommandanten_cc": "",
        "zusammenfassung_an": "",
        "warn_days": "30",
        "pruefungstypen": "G25,FSK",
        "archiv_tage": "365",
        "script_intervall": "wöchentlich",
    })
    html = client.get("/settings").data.decode()
    assert "saved.host.de" in html
    assert "30" in html


def test_settings_speichern_zeigt_erfolgsmeldung(client):
    response = client.post("/settings", data={
        "smtp_host": "smtp.test.de",
        "smtp_port": "587",
        "smtp_user": "",
        "smtp_password": "",
        "smtp_from": "",
        "kommandanten_cc": "",
        "zusammenfassung_an": "",
        "warn_days": "90",
        "pruefungstypen": "G25",
        "archiv_tage": "365",
        "script_intervall": "wöchentlich",
    }, follow_redirects=True)
    assert b"gespeichert" in response.data


# --- Navigation ---

def test_index_hat_link_zu_settings(client):
    html = client.get("/").data.decode()
    assert "/settings" in html


# --- /run nutzt DB-Einstellungen ---

def test_run_nutzt_warn_days_aus_db(client, tmp_path):
    client.post("/settings", data={
        "smtp_host": "",
        "smtp_port": "587",
        "smtp_user": "",
        "smtp_password": "",
        "smtp_from": "",
        "kommandanten_cc": "",
        "zusammenfassung_an": "",
        "warn_days": "120",
        "pruefungstypen": "G25,G26",
        "archiv_tage": "365",
        "script_intervall": "wöchentlich",
    })

    xls_path = tmp_path / "latest.xls"
    xls_path.write_bytes(b"dummy")

    captured_kwargs = {}

    def mock_check(filepath, **kwargs):
        captured_kwargs.update(kwargs)
        return []

    with patch("web.app.check_examinations", side_effect=mock_check), \
         patch("web.app.send_notifications", return_value=0), \
         patch("web.app.send_summary"):
        client.post("/run", data={})

    assert captured_kwargs.get("warn_days") == 120
    assert captured_kwargs.get("pruefungstypen") == ["G25", "G26"]


# --- E-Mail-Template (Issue #16) ---

def test_settings_zeigt_email_betreff_feld(client):
    html = client.get("/settings").data.decode()
    assert 'name="email_betreff"' in html


def test_settings_zeigt_email_template_feld(client):
    html = client.get("/settings").data.decode()
    assert 'name="email_template"' in html


def test_settings_zeigt_platzhalter_hinweis(client):
    html = client.get("/settings").data.decode()
    assert "{vorname}" in html
    assert "{nachname}" in html
    assert "{pruefungen_liste}" in html


def test_settings_speichert_email_betreff(client, tmp_path):
    client.post("/settings", data={
        "smtp_host": "", "smtp_port": "587", "smtp_user": "", "smtp_password": "",
        "smtp_from": "", "kommandanten_cc": "", "zusammenfassung_an": "",
        "warn_days": "90", "pruefungstypen": "G25", "archiv_tage": "365",
        "script_intervall": "wöchentlich",
        "email_betreff": "Mein Betreff Test",
        "email_template": "Hallo {vorname}, bitte handeln.",
    })
    import sqlite3
    db = sqlite3.connect(tmp_path / "checker.db")
    db.row_factory = sqlite3.Row
    rows = {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM settings").fetchall()}
    db.close()
    assert rows["email_betreff"] == "Mein Betreff Test"
    assert rows["email_template"] == "Hallo {vorname}, bitte handeln."


def test_settings_email_felder_nach_reload(client):
    client.post("/settings", data={
        "smtp_host": "", "smtp_port": "587", "smtp_user": "", "smtp_password": "",
        "smtp_from": "", "kommandanten_cc": "", "zusammenfassung_an": "",
        "warn_days": "90", "pruefungstypen": "G25", "archiv_tage": "365",
        "script_intervall": "wöchentlich",
        "email_betreff": "Individueller Betreff",
        "email_template": "Lieber {vorname} {nachname}, {pruefungen_liste}",
    })
    html = client.get("/settings").data.decode()
    assert "Individueller Betreff" in html
    assert "Lieber {vorname}" in html


def test_settings_email_standardwerte_ohne_db(client):
    html = client.get("/settings").data.decode()
    assert "Handlungsbedarf" in html


def test_settings_ungueltige_platzhalter_werden_abgelehnt(client):
    response = client.post("/settings", data={
        "smtp_host": "", "smtp_port": "587", "smtp_user": "", "smtp_password": "",
        "smtp_from": "", "kommandanten_cc": "", "zusammenfassung_an": "",
        "warn_days": "90", "pruefungstypen": "G25", "archiv_tage": "365",
        "script_intervall": "wöchentlich",
        "email_betreff": "Betreff",
        "email_template": "Hallo {vorname}, Kosten: {15,00 EUR}",
    }, follow_redirects=True)
    html = response.data.decode()
    assert "Platzhalter" in html or "Template" in html or "error" in html.lower()


def test_settings_unbekannter_platzhalter_wird_abgelehnt(client):
    response = client.post("/settings", data={
        "smtp_host": "", "smtp_port": "587", "smtp_user": "", "smtp_password": "",
        "smtp_from": "", "kommandanten_cc": "", "zusammenfassung_an": "",
        "warn_days": "90", "pruefungstypen": "G25", "archiv_tage": "365",
        "script_intervall": "wöchentlich",
        "email_betreff": "Betreff",
        "email_template": "Hallo {vorname}, {unbekannt}!",
    }, follow_redirects=True)
    html = response.data.decode()
    assert "Platzhalter" in html or "error" in html.lower()


def test_settings_gueltiges_template_wird_gespeichert(client):
    response = client.post("/settings", data={
        "smtp_host": "", "smtp_port": "587", "smtp_user": "", "smtp_password": "",
        "smtp_from": "", "kommandanten_cc": "", "zusammenfassung_an": "",
        "warn_days": "90", "pruefungstypen": "G25", "archiv_tage": "365",
        "script_intervall": "wöchentlich",
        "email_betreff": "Betreff",
        "email_template": "Hallo {vorname} {nachname},\n{pruefungen_liste}",
    }, follow_redirects=True)
    assert b"gespeichert" in response.data


def test_run_nutzt_email_template_aus_db(client, tmp_path):
    client.post("/settings", data={
        "smtp_host": "", "smtp_port": "587", "smtp_user": "", "smtp_password": "",
        "smtp_from": "", "kommandanten_cc": "", "zusammenfassung_an": "",
        "warn_days": "90", "pruefungstypen": "G25", "archiv_tage": "365",
        "script_intervall": "wöchentlich",
        "email_betreff": "Test-Betreff",
        "email_template": "Test-Template {vorname}",
    })

    xls_path = tmp_path / "latest.xls"
    xls_path.write_bytes(b"dummy")

    captured_kwargs = {}

    def mock_check(filepath, **kwargs):
        captured_kwargs.update(kwargs)
        return []

    def mock_send(persons, **kwargs):
        captured_kwargs.update(kwargs)
        return 0

    with patch("web.app.check_examinations", side_effect=mock_check), \
         patch("web.app.send_notifications", side_effect=mock_send), \
         patch("web.app.send_summary"):
        client.post("/run", data={})

    assert captured_kwargs.get("email_betreff") == "Test-Betreff"
    assert captured_kwargs.get("email_template") == "Test-Template {vorname}"
