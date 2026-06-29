import re
from contextlib import closing
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

import pytest

from u_checker.checker import Person, Pruefung
from web.app import app, get_db


@pytest.fixture
def client(tmp_path):
    app.config["TESTING"] = True
    app.config["DATA_DIR"] = tmp_path
    app.config["SECRET_KEY"] = "test-secret"
    with app.test_client() as c:
        yield c


def _person(pers_nr, vorname, nachname, pruefungen):
    return Person(pers_nr=pers_nr, vorname=vorname, nachname=nachname,
                  email=f"{pers_nr}@example.com", pruefungen=pruefungen)


def _abgelaufen(typ="G25"):
    return Pruefung(typ=typ, beschreibung=typ, datum=date.today() - timedelta(days=1), status="abgelaufen")


def _warnung(typ="G25", tage=30):
    return Pruefung(typ=typ, beschreibung=typ, datum=date.today() + timedelta(days=tage), status="warnung")


# --- GET /faelligkeiten ---

def test_get_faelligkeiten_erreichbar(client):
    response = client.get("/faelligkeiten")
    assert response.status_code == 200


def test_get_faelligkeiten_zeigt_analyse_button(client):
    html = client.get("/faelligkeiten").data.decode()
    assert "Analyse starten" in html


def test_get_faelligkeiten_ohne_xls_zeigt_hinweis(client):
    html = client.get("/faelligkeiten").data.decode()
    assert "XLS" in html or "hochladen" in html


def test_get_faelligkeiten_button_deaktiviert_ohne_xls(client):
    html = client.get("/faelligkeiten").data.decode()
    assert "disabled" in html


def test_get_faelligkeiten_zeigt_keinen_vorschau_bereich(client):
    html = client.get("/faelligkeiten").data.decode()
    assert "Alle auswählen" not in html


# --- POST /faelligkeiten/analyse ohne XLS ---

def test_post_analyse_ohne_xls_zeigt_fehler(client):
    response = client.post("/faelligkeiten/analyse", follow_redirects=True)
    assert b"Keine XLS-Datei" in response.data


def test_post_analyse_ohne_xls_redirect_zu_faelligkeiten(client):
    response = client.post("/faelligkeiten/analyse")
    assert response.status_code == 302
    assert "/faelligkeiten" in response.headers["Location"]


# --- POST /faelligkeiten/analyse mit Personen ---

def test_post_analyse_zeigt_person_in_tabelle(client, tmp_path):
    (tmp_path / "latest.xls").write_bytes(b"dummy")
    persons = [_person("001", "Max", "Muster", [_abgelaufen()])]

    with patch("web.app._analyse_faelligkeiten", return_value=(persons, [])):
        html = client.post("/faelligkeiten/analyse").data.decode()

    assert "Max Muster" in html
    assert "G25" in html


def test_post_analyse_cc_flag_bei_abgelaufen(client, tmp_path):
    (tmp_path / "latest.xls").write_bytes(b"dummy")
    persons = [_person("001", "Max", "Muster", [_abgelaufen()])]

    with patch("web.app._analyse_faelligkeiten", return_value=(persons, [])):
        html = client.post("/faelligkeiten/analyse").data.decode()

    assert "CC" in html


def test_post_analyse_kein_cc_flag_bei_nur_warnung(client, tmp_path):
    (tmp_path / "latest.xls").write_bytes(b"dummy")
    persons = [_person("001", "Anna", "Schmidt", [_warnung()])]

    with patch("web.app._analyse_faelligkeiten", return_value=(persons, [])):
        html = client.post("/faelligkeiten/analyse").data.decode()

    # Kein CC-Eintrag in der Zeile (der Text "CC" taucht nur auf wenn cc_flag gesetzt)
    # Prüfen über Zeilenposition wäre komplex – hier reicht: kein "CC" als Badge/Zelle
    # Der Test prüft indirekt über die Template-Logik: cc_flag=False → kein CC-Text
    assert "CC" not in html or html.count("CC") == 0


def test_post_analyse_abgelaufen_vor_warnung(client, tmp_path):
    (tmp_path / "latest.xls").write_bytes(b"dummy")
    persons = [
        _person("001", "Anna", "Schmidt", [_warnung()]),
        _person("002", "Bob", "Mueller", [_abgelaufen()]),
    ]

    with patch("web.app._analyse_faelligkeiten", return_value=(persons, [])):
        html = client.post("/faelligkeiten/analyse").data.decode()

    pos_mueller = html.index("Mueller")
    pos_schmidt = html.index("Schmidt")
    assert pos_mueller < pos_schmidt


def test_post_analyse_keine_checkboxen_angehakt(client, tmp_path):
    (tmp_path / "latest.xls").write_bytes(b"dummy")
    persons = [_person("001", "Max", "Muster", [_abgelaufen()])]

    with patch("web.app._analyse_faelligkeiten", return_value=(persons, [])):
        html = client.post("/faelligkeiten/analyse").data.decode()

    checkboxen = re.findall(r'<input[^>]+type=["\']checkbox["\'][^>]*>', html, re.IGNORECASE)
    assert all("checked" not in cb for cb in checkboxen)


def test_post_analyse_alle_auswaehlen_button_vorhanden(client, tmp_path):
    (tmp_path / "latest.xls").write_bytes(b"dummy")
    persons = [_person("001", "Max", "Muster", [_abgelaufen()])]

    with patch("web.app._analyse_faelligkeiten", return_value=(persons, [])):
        html = client.post("/faelligkeiten/analyse").data.decode()

    assert "Alle auswählen" in html


def test_post_analyse_fruehestes_datum_sichtbar(client, tmp_path):
    (tmp_path / "latest.xls").write_bytes(b"dummy")
    datum = date.today() - timedelta(days=3)
    pruefung = Pruefung(typ="G25", beschreibung="G25", datum=datum, status="abgelaufen")
    persons = [_person("001", "Max", "Muster", [pruefung])]

    with patch("web.app._analyse_faelligkeiten", return_value=(persons, [])):
        html = client.post("/faelligkeiten/analyse").data.decode()

    # Datum im deutschen Format
    assert datum.strftime("%d.%m.%Y") in html


# --- Personen ohne E-Mail ---

def test_post_analyse_ohne_email_personen_im_hinweisblock(client, tmp_path):
    (tmp_path / "latest.xls").write_bytes(b"dummy")
    ohne_email = [
        {"name": "Karl Noemail", "pruefungen": [{"typ": "G25", "status": "abgelaufen",
                                                   "datum": (date.today() - timedelta(days=1)).isoformat()}]}
    ]

    with patch("web.app._analyse_faelligkeiten", return_value=([], ohne_email)):
        html = client.post("/faelligkeiten/analyse").data.decode()

    assert "Karl Noemail" in html


def test_post_analyse_ohne_email_keine_checkbox(client, tmp_path):
    (tmp_path / "latest.xls").write_bytes(b"dummy")
    ohne_email = [
        {"name": "Karl Noemail", "pruefungen": [{"typ": "G25", "status": "abgelaufen",
                                                   "datum": (date.today() - timedelta(days=1)).isoformat()}]}
    ]

    with patch("web.app._analyse_faelligkeiten", return_value=([], ohne_email)):
        html = client.post("/faelligkeiten/analyse").data.decode()

    # Keine Checkbox für Karl Noemail – kein <input> mit name="pers_nr" im HTML
    assert not re.search(r'<input[^>]+name="pers_nr"', html)


# --- Kein PII (E-Mail-Adressen) ---

def test_post_analyse_keine_email_adressen_sichtbar(client, tmp_path):
    (tmp_path / "latest.xls").write_bytes(b"dummy")
    persons = [_person("001", "Max", "Muster", [_abgelaufen()])]
    # email ist "001@example.com" (aus _person helper)

    with patch("web.app._analyse_faelligkeiten", return_value=(persons, [])):
        html = client.post("/faelligkeiten/analyse").data.decode()

    assert "001@example.com" not in html


# --- Leerzustand nach Analyse ohne Fälligkeiten ---

def test_post_analyse_ohne_faelligkeiten_zeigt_leer_meldung(client, tmp_path):
    (tmp_path / "latest.xls").write_bytes(b"dummy")

    with patch("web.app._analyse_faelligkeiten", return_value=([], [])):
        html = client.post("/faelligkeiten/analyse").data.decode()

    assert "Keine Fälligkeiten" in html


# --- Sortierung nach frühestem Datum innerhalb gleichen Status ---

def test_post_analyse_sortierung_nach_datum_innerhalb_warnung(client, tmp_path):
    (tmp_path / "latest.xls").write_bytes(b"dummy")
    persons = [
        _person("001", "Später", "Warnung", [_warnung(tage=60)]),
        _person("002", "Früher", "Warnung", [_warnung(tage=10)]),
    ]

    with patch("web.app._analyse_faelligkeiten", return_value=(persons, [])):
        html = client.post("/faelligkeiten/analyse").data.decode()

    pos_frueher = html.index("Früher")
    pos_spaeter = html.index("Später")
    assert pos_frueher < pos_spaeter


# --- POST /faelligkeiten/senden ---

def test_senden_ohne_auswahl_zeigt_fehler(client, tmp_path):
    (tmp_path / "latest.xls").write_bytes(b"dummy")
    response = client.post("/faelligkeiten/senden", data={}, follow_redirects=True)
    assert b"Keine Personen" in response.data


def test_senden_ohne_auswahl_kein_versand(client, tmp_path):
    (tmp_path / "latest.xls").write_bytes(b"dummy")
    with patch("web.app.send_notifications") as mock_send:
        client.post("/faelligkeiten/senden", data={})
    mock_send.assert_not_called()


def test_senden_nur_ausgewaehlte_personen_bekommen_mail(client, tmp_path):
    (tmp_path / "latest.xls").write_bytes(b"dummy")
    persons = [
        _person("001", "Max", "Muster", [_abgelaufen()]),
        _person("002", "Anna", "Schmidt", [_warnung()]),
    ]

    with patch("web.app._analyse_faelligkeiten", return_value=(persons, [])), \
         patch("web.app.send_notifications", return_value=1) as mock_send, \
         patch("web.app.send_summary"):
        client.post("/faelligkeiten/senden", data={"pers_nr": ["001"]})

    aufgerufen_mit = mock_send.call_args[0][0]
    assert len(aufgerufen_mit) == 1
    assert aufgerufen_mit[0].pers_nr == "001"


def test_senden_schreibt_erinnerungen_eintraege(client, tmp_path):
    (tmp_path / "latest.xls").write_bytes(b"dummy")
    persons = [_person("001", "Max", "Muster", [_abgelaufen()])]

    with patch("web.app._analyse_faelligkeiten", return_value=(persons, [])), \
         patch("web.app.send_notifications", return_value=1), \
         patch("web.app.send_summary"):
        client.post("/faelligkeiten/senden", data={"pers_nr": ["001"]})

    with app.app_context():
        app.config["DATA_DIR"] = tmp_path
        with closing(get_db()) as db:
            rows = db.execute("SELECT * FROM erinnerungen").fetchall()

    assert len(rows) == 1
    assert rows[0]["pers_nr"] == "001"
    assert rows[0]["mitglied_name"] == "Max Muster"
    assert rows[0]["pruefungstyp"] == "G25"
    assert rows[0]["status"] == "abgelaufen"


def test_senden_erinnerung_pro_person_pro_pruefungstyp(client, tmp_path):
    (tmp_path / "latest.xls").write_bytes(b"dummy")
    persons = [
        _person("001", "Max", "Muster", [_abgelaufen("G25"), _warnung("G26")]),
    ]

    with patch("web.app._analyse_faelligkeiten", return_value=(persons, [])), \
         patch("web.app.send_notifications", return_value=1), \
         patch("web.app.send_summary"):
        client.post("/faelligkeiten/senden", data={"pers_nr": ["001"]})

    with app.app_context():
        app.config["DATA_DIR"] = tmp_path
        with closing(get_db()) as db:
            rows = db.execute("SELECT pruefungstyp FROM erinnerungen ORDER BY pruefungstyp").fetchall()

    typen = [r["pruefungstyp"] for r in rows]
    assert typen == ["G25", "G26"]


def test_senden_ruft_send_summary_auf(client, tmp_path):
    (tmp_path / "latest.xls").write_bytes(b"dummy")
    persons = [_person("001", "Max", "Muster", [_abgelaufen()])]

    with patch("web.app._analyse_faelligkeiten", return_value=(persons, [])), \
         patch("web.app.send_notifications", return_value=1), \
         patch("web.app.send_summary") as mock_summary:
        client.post("/faelligkeiten/senden", data={"pers_nr": ["001"]})

    mock_summary.assert_called_once()


def test_senden_flash_meldung_mit_anzahl(client, tmp_path):
    (tmp_path / "latest.xls").write_bytes(b"dummy")
    persons = [_person("001", "Max", "Muster", [_abgelaufen()])]

    with patch("web.app._analyse_faelligkeiten", return_value=(persons, [])), \
         patch("web.app.send_notifications", return_value=1), \
         patch("web.app.send_summary"):
        response = client.post("/faelligkeiten/senden",
                               data={"pers_nr": ["001"]}, follow_redirects=True)

    assert b"1 E-Mail" in response.data


def test_senden_leitet_zu_faelligkeiten_weiter(client, tmp_path):
    (tmp_path / "latest.xls").write_bytes(b"dummy")
    persons = [_person("001", "Max", "Muster", [_abgelaufen()])]

    with patch("web.app._analyse_faelligkeiten", return_value=(persons, [])), \
         patch("web.app.send_notifications", return_value=1), \
         patch("web.app.send_summary"):
        response = client.post("/faelligkeiten/senden", data={"pers_nr": ["001"]})

    assert response.status_code == 302
    assert "/faelligkeiten" in response.headers["Location"]


def test_senden_seite_kehrt_in_leerzustand_zurueck(client, tmp_path):
    (tmp_path / "latest.xls").write_bytes(b"dummy")
    persons = [_person("001", "Max", "Muster", [_abgelaufen()])]

    with patch("web.app._analyse_faelligkeiten", return_value=(persons, [])), \
         patch("web.app.send_notifications", return_value=1), \
         patch("web.app.send_summary"):
        response = client.post("/faelligkeiten/senden",
                               data={"pers_nr": ["001"]}, follow_redirects=True)

    html = response.data.decode()
    assert "Alle auswählen" not in html
    assert "Analyse starten" in html


# --- Verlauf ---

def test_verlauf_leer_zeigt_hinweis(client):
    html = client.get("/faelligkeiten").data.decode()
    assert "Noch keine Erinnerungen" in html


def test_verlauf_sichtbar_nach_versand(client, tmp_path):
    (tmp_path / "latest.xls").write_bytes(b"dummy")
    persons = [_person("001", "Max", "Muster", [_abgelaufen()])]

    with patch("web.app._analyse_faelligkeiten", return_value=(persons, [])), \
         patch("web.app.send_notifications", return_value=1), \
         patch("web.app.send_summary"):
        client.post("/faelligkeiten/senden", data={"pers_nr": ["001"]},
                    follow_redirects=True)

    html = client.get("/faelligkeiten").data.decode()
    assert "Max Muster" in html
    assert "G25" in html


def test_verlauf_spalten_vorhanden(client, tmp_path):
    (tmp_path / "latest.xls").write_bytes(b"dummy")
    persons = [_person("001", "Max", "Muster", [_abgelaufen()])]

    with patch("web.app._analyse_faelligkeiten", return_value=(persons, [])), \
         patch("web.app.send_notifications", return_value=1), \
         patch("web.app.send_summary"):
        client.post("/faelligkeiten/senden", data={"pers_nr": ["001"]},
                    follow_redirects=True)

    html = client.get("/faelligkeiten").data.decode()
    assert "Datum" in html
    assert "Name" in html
    assert "Prüfungstyp" in html
    assert "Status" in html


# --- Fehlerbehandlung beim Versenden ---

def test_senden_smtp_fehler_zeigt_flash_statt_500(client, tmp_path):
    (tmp_path / "latest.xls").write_bytes(b"dummy")
    persons = [_person("001", "Max", "Muster", [_abgelaufen()])]

    with patch("web.app._analyse_faelligkeiten", return_value=(persons, [])), \
         patch("web.app.send_notifications", side_effect=Exception("SMTP down")), \
         patch("web.app.send_summary"):
        response = client.post("/faelligkeiten/senden",
                               data={"pers_nr": ["001"]}, follow_redirects=True)

    assert response.status_code == 200
    assert b"Fehler" in response.data


def test_senden_smtp_fehler_kein_erinnerungen_eintrag(client, tmp_path):
    (tmp_path / "latest.xls").write_bytes(b"dummy")
    persons = [_person("001", "Max", "Muster", [_abgelaufen()])]

    with patch("web.app._analyse_faelligkeiten", return_value=(persons, [])), \
         patch("web.app.send_notifications", side_effect=Exception("SMTP down")), \
         patch("web.app.send_summary"):
        client.post("/faelligkeiten/senden", data={"pers_nr": ["001"]})

    with app.app_context():
        app.config["DATA_DIR"] = tmp_path
        with closing(get_db()) as db:
            count = db.execute("SELECT COUNT(*) FROM erinnerungen").fetchone()[0]
    assert count == 0


def test_senden_summary_fehler_zeigt_flash(client, tmp_path):
    (tmp_path / "latest.xls").write_bytes(b"dummy")
    persons = [_person("001", "Max", "Muster", [_abgelaufen()])]

    with patch("web.app._analyse_faelligkeiten", return_value=(persons, [])), \
         patch("web.app.send_notifications", return_value=1), \
         patch("web.app.send_summary", side_effect=Exception("Summary-SMTP down")):
        response = client.post("/faelligkeiten/senden",
                               data={"pers_nr": ["001"]}, follow_redirects=True)

    assert response.status_code == 200
    assert b"Fehler" in response.data


# --- TOCTOU-Schutz ---

def test_senden_abbruch_wenn_xls_ausgetauscht(client, tmp_path):
    (tmp_path / "latest.xls").write_bytes(b"dummy")
    (tmp_path / "latest_name.txt").write_text("alte_datei.xls", encoding="utf-8")
    persons = [_person("001", "Max", "Muster", [_abgelaufen()])]

    with patch("web.app._analyse_faelligkeiten", return_value=(persons, [])), \
         patch("web.app.send_notifications") as mock_send, \
         patch("web.app.send_summary"):
        response = client.post(
            "/faelligkeiten/senden",
            data={"pers_nr": ["001"], "xls_dateiname": "neue_datei.xls"},
            follow_redirects=True,
        )

    mock_send.assert_not_called()
    assert b"ausgetauscht" in response.data


def test_analyse_uebergibt_xls_dateiname_an_template(client, tmp_path):
    (tmp_path / "latest.xls").write_bytes(b"dummy")
    (tmp_path / "latest_name.txt").write_text("export.xls", encoding="utf-8")
    persons = [_person("001", "Max", "Muster", [_abgelaufen()])]

    with patch("web.app._analyse_faelligkeiten", return_value=(persons, [])):
        html = client.post("/faelligkeiten/analyse").data.decode()

    assert 'name="xls_dateiname"' in html
    assert 'value="export.xls"' in html
