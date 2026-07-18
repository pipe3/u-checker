import email.message
import email.mime.application
import email.mime.multipart
import email.mime.text
import sqlite3
from contextlib import closing
from unittest.mock import MagicMock, patch

import pytest

from web.app import app, init_db


@pytest.fixture
def db_app(tmp_path):
    app.config["TESTING"] = True
    app.config["DATA_DIR"] = tmp_path
    with app.app_context():
        init_db()
    return tmp_path


def _make_raw_email(
    from_addr: str = "Max Mustermann <max@example.com>",
    subject: str = "G25 Nachweis",
    body: str = "Anbei mein Nachweis.",
    message_id: str = "<test-1@example.com>",
) -> bytes:
    msg = email.message.EmailMessage()
    msg["From"] = from_addr
    msg["Subject"] = subject
    msg["Message-ID"] = message_id
    msg["Date"] = "Mon, 01 Jan 2025 12:00:00 +0000"
    msg.set_content(body)
    return msg.as_bytes()


# --- process_email ---

def test_neue_email_erstellt_task(db_app):
    from web.imap_poller import process_email
    from web.app import get_db

    raw = _make_raw_email()
    with app.app_context():
        with closing(get_db()) as db:
            result = process_email(db, raw)
            db.commit()

    assert result is True
    db = sqlite3.connect(db_app / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM tasks LIMIT 1").fetchone()
    db.close()
    assert row is not None
    assert row["status"] == "NEU"
    assert row["von_email"] == "max@example.com"
    assert row["von_name"] == "Max Mustermann"
    assert "G25 Nachweis" in row["betreff"]


def test_doppelte_email_wird_ignoriert(db_app):
    from web.imap_poller import process_email
    from web.app import get_db

    raw = _make_raw_email()
    with app.app_context():
        with closing(get_db()) as db:
            process_email(db, raw)
            db.commit()
        with closing(get_db()) as db:
            result = process_email(db, raw)
            db.commit()

    assert result is False
    db = sqlite3.connect(db_app / "checker.db")
    count = db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    db.close()
    assert count == 1


def test_raw_email_wird_gespeichert(db_app):
    from web.imap_poller import process_email
    from web.app import get_db

    raw = _make_raw_email()
    with app.app_context():
        with closing(get_db()) as db:
            process_email(db, raw)
            row = db.execute("SELECT raw_email FROM tasks LIMIT 1").fetchone()
            db.commit()

    assert row["raw_email"] == raw


def test_anhang_count_wird_gezaehlt(db_app):
    from web.imap_poller import process_email
    from web.app import get_db

    msg = email.mime.multipart.MIMEMultipart()
    msg["From"] = "max@example.com"
    msg["Subject"] = "Nachweis mit Anhang"
    msg["Message-ID"] = "<attach-test@example.com>"
    msg.attach(email.mime.text.MIMEText("Body"))
    att = email.mime.application.MIMEApplication(b"pdfdata", _subtype="pdf")
    att.add_header("Content-Disposition", "attachment", filename="nachweis.pdf")
    msg.attach(att)
    raw = msg.as_bytes()

    with app.app_context():
        with closing(get_db()) as db:
            process_email(db, raw)
            row = db.execute("SELECT anhang_count FROM tasks LIMIT 1").fetchone()
            db.commit()

    assert row["anhang_count"] == 1


def test_email_ohne_message_id_wird_trotzdem_gespeichert(db_app):
    from web.imap_poller import process_email
    from web.app import get_db

    msg = email.message.EmailMessage()
    msg["From"] = "no-id@example.com"
    msg["Subject"] = "Kein Message-ID"
    msg.set_content("Test ohne ID")
    raw = msg.as_bytes()

    with app.app_context():
        with closing(get_db()) as db:
            result = process_email(db, raw)
            db.commit()

    assert result is True


def test_email_ohne_message_id_kein_duplikat(db_app):
    """Zweite Verarbeitung derselben Bytes muss via Content-Hash dedupliziert werden."""
    from web.imap_poller import process_email
    from web.app import get_db

    msg = email.message.EmailMessage()
    msg["From"] = "no-id@example.com"
    msg["Subject"] = "Kein Message-ID"
    msg.set_content("Test ohne ID")
    raw = msg.as_bytes()

    with app.app_context():
        with closing(get_db()) as db:
            r1 = process_email(db, raw)
            db.commit()
        with closing(get_db()) as db:
            r2 = process_email(db, raw)
            db.commit()

    assert r1 is True
    assert r2 is False

    db = sqlite3.connect(db_app / "checker.db")
    count = db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    db.close()
    assert count == 1


# --- poll_inbox ---

def test_poll_uebersprungen_wenn_imap_nicht_konfiguriert(db_app):
    from web.imap_poller import poll_inbox
    from web.app import save_settings

    with app.app_context():
        save_settings({"imap_host": "", "imap_user": "", "imap_password": ""})
        result = poll_inbox(app)

    assert result == 0


def test_poll_verarbeitet_neue_email(db_app):
    from web.imap_poller import poll_inbox
    from web.app import save_settings

    raw = _make_raw_email()
    mock_imap = MagicMock()
    mock_imap.search.return_value = ("OK", [b"1"])
    mock_imap.fetch.return_value = ("OK", [(b"1 (RFC822 {100})", raw)])

    with app.app_context():
        save_settings({
            "imap_host": "imap.example.com",
            "imap_port": "993",
            "imap_user": "test@example.com",
            "imap_password": "pass",
        })
        with patch("web.imap_poller.imaplib.IMAP4_SSL", return_value=mock_imap), \
             patch("web.imap_poller._send_admin_notification") as mock_notify:
            result = poll_inbox(app)

    assert result == 1
    mock_notify.assert_called_once()


def test_poll_keine_benachrichtigung_bei_keinen_neuen_emails(db_app):
    from web.imap_poller import poll_inbox
    from web.app import save_settings

    mock_imap = MagicMock()
    mock_imap.search.return_value = ("OK", [b""])

    with app.app_context():
        save_settings({
            "imap_host": "imap.example.com",
            "imap_port": "993",
            "imap_user": "test@example.com",
            "imap_password": "pass",
        })
        with patch("web.imap_poller.imaplib.IMAP4_SSL", return_value=mock_imap), \
             patch("web.imap_poller._send_admin_notification") as mock_notify:
            result = poll_inbox(app)

    assert result == 0
    mock_notify.assert_not_called()


def test_poll_ignoriert_duplikate(db_app):
    from web.imap_poller import poll_inbox
    from web.app import save_settings

    raw = _make_raw_email()
    mock_imap = MagicMock()
    mock_imap.search.return_value = ("OK", [b"1"])
    mock_imap.fetch.return_value = ("OK", [(b"1 (RFC822 {100})", raw)])

    with app.app_context():
        save_settings({
            "imap_host": "imap.example.com",
            "imap_port": "993",
            "imap_user": "test@example.com",
            "imap_password": "pass",
        })
        with patch("web.imap_poller.imaplib.IMAP4_SSL", return_value=mock_imap), \
             patch("web.imap_poller._send_admin_notification"):
            poll_inbox(app)  # erster Lauf

        with patch("web.imap_poller.imaplib.IMAP4_SSL", return_value=mock_imap), \
             patch("web.imap_poller._send_admin_notification") as mock_notify:
            result = poll_inbox(app)  # zweiter Lauf mit derselben Email

    assert result == 0
    mock_notify.assert_not_called()


def test_poll_fehler_beim_imap_abruf_gibt_null_zurueck(db_app):
    from web.imap_poller import poll_inbox
    from web.app import save_settings

    with app.app_context():
        save_settings({
            "imap_host": "imap.example.com",
            "imap_port": "993",
            "imap_user": "test@example.com",
            "imap_password": "pass",
        })
        with patch("web.imap_poller.imaplib.IMAP4_SSL", side_effect=ConnectionRefusedError("Verbindung abgelehnt")):
            result = poll_inbox(app)

    assert result == 0


# --- Extraktion + UNKLARE_ZUORDNUNG + Duplikate ---

_MEMBERS = [
    {"pers_nr": "001", "vorname": "Max", "nachname": "Mustermann", "email": "max@example.com"},
]


def test_process_email_extrahiert_pruefungstyp_und_datum(db_app):
    from web.imap_poller import process_email
    from web.app import get_db

    raw = _make_raw_email(
        from_addr="Max Mustermann <max@example.com>",
        subject="G25 Nachweis",
        body="G25 Untersuchung\nGültig bis: 31.12.2026",
    )
    with app.app_context():
        with patch("web.imap_poller.load_members_from_xls", return_value=_MEMBERS):
            with patch("web.imap_poller.extract_from_email") as mock_extract:
                from datetime import date
                mock_extract.return_value = {
                    "pruefungstyp": "G25",
                    "faelligkeitsdatum": date(2026, 12, 31),
                    "mitglied": _MEMBERS[0],
                    "match_score": 1.0,
                    "raw_text": "G25 Gültig bis 31.12.2026",
                }
                with closing(get_db()) as db:
                    process_email(db, raw, xls_path="/fake/path.xls", pruefungstypen=["G25"])
                    db.commit()

    import sqlite3
    db = sqlite3.connect(db_app / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM tasks LIMIT 1").fetchone()
    db.close()
    assert row["pruefungstyp"] == "G25"
    assert row["faelligkeitsdatum"] == "2026-12-31"
    assert row["mitglied_nr"] == "001"
    assert row["mitglied_name"] == "Max Mustermann"
    assert row["status"] == "NEU"


def test_process_email_unklare_zuordnung(db_app):
    from web.imap_poller import process_email
    from web.app import get_db

    raw = _make_raw_email(
        from_addr="Unbekannt <nobody@example.com>",
        subject="Nachweis",
        body="G25 Gültig bis: 31.12.2026",
        message_id="<unklar@example.com>",
    )
    with app.app_context():
        with patch("web.imap_poller.extract_from_email") as mock_extract:
            mock_extract.return_value = {
                "pruefungstyp": "G25",
                "faelligkeitsdatum": None,
                "mitglied": None,
                "match_score": 0.3,
                "raw_text": "G25",
            }
            with closing(get_db()) as db:
                # Mitgliederliste vorhanden aber kein Match → UNKLARE_ZUORDNUNG
                process_email(db, raw, xls_path=None, pruefungstypen=["G25"])
                db.commit()
                # Da xls_path=None → members=[], score<threshold trifft nicht zu
                # Wir simulieren mit einem gesetzten Mock direkt:

    # Zweiter Versuch: members vorhanden, schlechter Score
    import sqlite3
    raw2 = _make_raw_email(
        from_addr="Unbekannt <nobody2@example.com>",
        subject="Nachweis",
        body="G25 Gültig bis: 31.12.2026",
        message_id="<unklar2@example.com>",
    )
    with app.app_context():
        with patch("web.imap_poller.load_members_from_xls", return_value=_MEMBERS), \
             patch("web.imap_poller.extract_from_email") as mock_extract:
            mock_extract.return_value = {
                "pruefungstyp": "G25",
                "faelligkeitsdatum": None,
                "mitglied": None,
                "match_score": 0.3,
                "raw_text": "G25",
            }
            with closing(get_db()) as db:
                process_email(db, raw2, xls_path="/fake/path.xls", pruefungstypen=["G25"])
                db.commit()

    db = sqlite3.connect(db_app / "checker.db")
    db.row_factory = sqlite3.Row
    rows = db.execute("SELECT * FROM tasks ORDER BY id DESC").fetchall()
    db.close()
    assert rows[0]["status"] == "UNKLARE_ZUORDNUNG"


def test_process_email_duplikat_markierung(db_app):
    from web.imap_poller import process_email
    from web.app import get_db
    import sqlite3

    def _raw(mid):
        return _make_raw_email(
            from_addr="Max Mustermann <max@example.com>",
            subject="G25 Nachweis",
            body="G25 Gültig bis 31.12.2026",
            message_id=mid,
        )

    extraction = {
        "pruefungstyp": "G25",
        "faelligkeitsdatum": None,
        "mitglied": _MEMBERS[0],
        "match_score": 1.0,
        "raw_text": "G25",
    }

    with app.app_context():
        with patch("web.imap_poller.extract_from_email", return_value=extraction):
            with closing(get_db()) as db:
                process_email(db, _raw("<dup-1@x.com>"), pruefungstypen=["G25"])
                db.commit()
            with closing(get_db()) as db:
                process_email(db, _raw("<dup-2@x.com>"), pruefungstypen=["G25"])
                db.commit()

    db = sqlite3.connect(db_app / "checker.db")
    db.row_factory = sqlite3.Row
    rows = db.execute("SELECT * FROM tasks ORDER BY id").fetchall()
    db.close()
    assert rows[0]["hinweis"] is None
    assert rows[1]["hinweis"] == "Mögliches Duplikat"


# --- Verifikationsantworten ---

def _make_reply_email(
    from_addr: str = "Max Mustermann <max@example.com>",
    subject: str = "Re: Bitte bestätigen Sie Ihre E-Mail-Adresse",
    body: str = "Ja, das bin ich.",
    message_id: str = "<reply-1@example.com>",
    in_reply_to: str = "<verif-123@example.com>",
) -> bytes:
    msg = email.message.EmailMessage()
    msg["From"] = from_addr
    msg["Subject"] = subject
    msg["Message-ID"] = message_id
    msg["In-Reply-To"] = in_reply_to
    msg["Date"] = "Mon, 01 Jan 2025 12:00:00 +0000"
    msg.set_content(body)
    return msg.as_bytes()


def _insert_verifikation(db_path, pers_nr="001", status="ausstehend", message_id="<verif-123@example.com>"):
    db = sqlite3.connect(db_path)
    db.execute(
        """INSERT INTO email_verifikation
           (pers_nr, vorname, nachname, email, status, verifikationsmail_message_id)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (pers_nr, "Max", "Mustermann", "max@example.com", status, message_id),
    )
    db.commit()
    db.close()


_IMAP_SETTINGS = {
    "imap_host": "imap.example.com",
    "imap_port": "993",
    "imap_user": "test@example.com",
    "imap_password": "pass",
    "imap_verifikation_ordner": "u-checker-verifikation",
}


def _make_mock_imap(raw: bytes, folder_exists: bool = True):
    mock_imap = MagicMock()
    mock_imap.search.return_value = ("OK", [b"1"])
    mock_imap.fetch.return_value = ("OK", [(b"1 (RFC822 {100})", raw)])
    mock_imap.copy.return_value = ("OK", [b""])
    if folder_exists:
        mock_imap.list.return_value = ("OK", [b'(\\HasNoChildren) "." "u-checker-verifikation"'])
    else:
        mock_imap.list.return_value = ("OK", [b""])
    return mock_imap


def test_verifikationsantwort_setzt_status_bestaetigt(db_app):
    """Eingehende Mail mit passendem In-Reply-To setzt Status → bestaetigt."""
    from web.imap_poller import poll_inbox
    from web.app import save_settings

    _insert_verifikation(db_app / "checker.db")
    raw = _make_reply_email(in_reply_to="<verif-123@example.com>")
    mock_imap = _make_mock_imap(raw)

    with app.app_context():
        save_settings(_IMAP_SETTINGS)
        with patch("web.imap_poller.imaplib.IMAP4_SSL", return_value=mock_imap):
            poll_inbox(app)

    db = sqlite3.connect(db_app / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM email_verifikation WHERE pers_nr = '001'").fetchone()
    db.close()
    assert row["status"] == "bestaetigt"
    assert row["bestaetigt_am"] is not None


def test_verifikationsantwort_setzt_adresse_geaendert_zurueck(db_app):
    """Eingehende Mail mit passendem In-Reply-To setzt adresse_geaendert zurueck."""
    from web.imap_poller import poll_inbox
    from web.app import save_settings

    _insert_verifikation(db_app / "checker.db")
    db = sqlite3.connect(db_app / "checker.db")
    db.execute("UPDATE email_verifikation SET adresse_geaendert = 1 WHERE pers_nr = '001'")
    db.commit()
    db.close()

    raw = _make_reply_email(in_reply_to="<verif-123@example.com>")
    mock_imap = _make_mock_imap(raw)

    with app.app_context():
        save_settings(_IMAP_SETTINGS)
        with patch("web.imap_poller.imaplib.IMAP4_SSL", return_value=mock_imap):
            poll_inbox(app)

    db = sqlite3.connect(db_app / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM email_verifikation WHERE pers_nr = '001'").fetchone()
    db.close()
    assert row["adresse_geaendert"] == 0


def test_verifikationsantwort_auf_re_verifikation_setzt_status_bestaetigt(db_app):
    """Eingehende Antwort mit passendem In-Reply-To auf ein Mitglied im Status
    re_verifikation_ausstehend setzt Status → bestaetigt, aktualisiert bestaetigt_am
    und markiert die Herkunft als automatisch."""
    from web.imap_poller import poll_inbox
    from web.app import save_settings

    _insert_verifikation(db_app / "checker.db", status="re_verifikation_ausstehend")
    db = sqlite3.connect(db_app / "checker.db")
    db.execute(
        "UPDATE email_verifikation SET bestaetigt_am='2026-01-02T12:00:00' WHERE pers_nr='001'"
    )
    db.commit()
    db.close()

    raw = _make_reply_email(in_reply_to="<verif-123@example.com>")
    mock_imap = _make_mock_imap(raw)

    with app.app_context():
        save_settings(_IMAP_SETTINGS)
        with patch("web.imap_poller.imaplib.IMAP4_SSL", return_value=mock_imap):
            poll_inbox(app)

    db = sqlite3.connect(db_app / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM email_verifikation WHERE pers_nr = '001'").fetchone()
    db.close()
    assert row["status"] == "bestaetigt"
    assert row["bestaetigt_am"] != "2026-01-02T12:00:00"  # aktualisiert auf neue Antwort
    assert row["bestaetigung_herkunft"] == "automatisch"


def test_verifikationsantwort_erstellt_keinen_task(db_app):
    """Für Verifikationsantworten wird kein Task erstellt."""
    from web.imap_poller import poll_inbox
    from web.app import save_settings

    _insert_verifikation(db_app / "checker.db")
    raw = _make_reply_email(in_reply_to="<verif-123@example.com>")
    mock_imap = _make_mock_imap(raw)

    with app.app_context():
        save_settings(_IMAP_SETTINGS)
        with patch("web.imap_poller.imaplib.IMAP4_SSL", return_value=mock_imap):
            result = poll_inbox(app)

    assert result == 0
    db = sqlite3.connect(db_app / "checker.db")
    count = db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    db.close()
    assert count == 0


def test_verifikationsantwort_wird_in_ordner_verschoben(db_app):
    """Die Antwortmail wird in den konfigurierten IMAP-Ordner verschoben."""
    from web.imap_poller import poll_inbox
    from web.app import save_settings

    _insert_verifikation(db_app / "checker.db")
    raw = _make_reply_email(in_reply_to="<verif-123@example.com>")
    mock_imap = _make_mock_imap(raw)

    with app.app_context():
        save_settings(_IMAP_SETTINGS)
        with patch("web.imap_poller.imaplib.IMAP4_SSL", return_value=mock_imap):
            poll_inbox(app)

    mock_imap.copy.assert_called_once_with(b"1", "u-checker-verifikation")
    mock_imap.store.assert_any_call(b"1", "+FLAGS", "\\Deleted")
    mock_imap.expunge.assert_called_once()


def test_imap_ordner_wird_erstellt_wenn_nicht_vorhanden(db_app):
    """Existiert der IMAP-Ordner nicht, wird er automatisch per CREATE angelegt."""
    from web.imap_poller import poll_inbox
    from web.app import save_settings

    _insert_verifikation(db_app / "checker.db")
    raw = _make_reply_email(in_reply_to="<verif-123@example.com>")
    mock_imap = _make_mock_imap(raw, folder_exists=False)

    with app.app_context():
        save_settings(_IMAP_SETTINGS)
        with patch("web.imap_poller.imaplib.IMAP4_SSL", return_value=mock_imap):
            poll_inbox(app)

    mock_imap.create.assert_called_once_with("u-checker-verifikation")


def test_normale_mail_laeuft_durch_nachweis_flow(db_app):
    """Eingehende Mails ohne In-Reply-To-Treffer laufen unverändert durch den Nachweis-Flow."""
    from web.imap_poller import poll_inbox
    from web.app import save_settings

    raw = _make_raw_email()  # keine In-Reply-To Header
    mock_imap = MagicMock()
    mock_imap.search.return_value = ("OK", [b"1"])
    mock_imap.fetch.return_value = ("OK", [(b"1 (RFC822 {100})", raw)])

    with app.app_context():
        save_settings(_IMAP_SETTINGS)
        with patch("web.imap_poller.imaplib.IMAP4_SSL", return_value=mock_imap), \
             patch("web.imap_poller._send_admin_notification"):
            result = poll_inbox(app)

    assert result == 1
    db = sqlite3.connect(db_app / "checker.db")
    count = db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    db.close()
    assert count == 1


def test_verifikation_mit_nicht_passendem_in_reply_to_laeuft_normal(db_app):
    """Mail mit In-Reply-To, das keiner Verifikations-Message-ID entspricht, wird als Task angelegt."""
    from web.imap_poller import poll_inbox
    from web.app import save_settings

    _insert_verifikation(db_app / "checker.db", message_id="<verif-999@example.com>")
    # Reply-To zeigt auf eine andere ID als die gespeicherte
    raw = _make_reply_email(in_reply_to="<andere-id@example.com>")
    mock_imap = MagicMock()
    mock_imap.search.return_value = ("OK", [b"1"])
    mock_imap.fetch.return_value = ("OK", [(b"1 (RFC822 {100})", raw)])

    with app.app_context():
        save_settings(_IMAP_SETTINGS)
        with patch("web.imap_poller.imaplib.IMAP4_SSL", return_value=mock_imap), \
             patch("web.imap_poller._send_admin_notification"):
            result = poll_inbox(app)

    assert result == 1
    db = sqlite3.connect(db_app / "checker.db")
    count = db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    db.close()
    assert count == 1


# --- Nachweis als implizite E-Mail-Bestätigung ---

def _insert_verifikation_for_member(db_path, pers_nr="001", status="ausstehend"):
    db = sqlite3.connect(db_path)
    db.execute(
        """INSERT INTO email_verifikation
           (pers_nr, vorname, nachname, email, status)
           VALUES (?, ?, ?, ?, ?)""",
        (pers_nr, "Max", "Mustermann", "max@example.com", status),
    )
    db.commit()
    db.close()


def test_nachweis_setzt_bestaetigt_am_bei_zugeordnetem_mitglied(db_app):
    """Zugeordneter Nachweis aktualisiert bestaetigt_am und setzt Status auf bestaetigt."""
    from web.imap_poller import process_email
    from web.app import get_db

    _insert_verifikation_for_member(db_app / "checker.db", status="ausstehend")

    raw = _make_raw_email(from_addr="Max Mustermann <max@example.com>", message_id="<nachweis-1@example.com>")
    extraction = {
        "pruefungstyp": "G25",
        "faelligkeitsdatum": None,
        "mitglied": _MEMBERS[0],
        "match_score": 1.0,
        "raw_text": "G25",
    }

    with app.app_context():
        with patch("web.imap_poller.load_members_from_xls", return_value=_MEMBERS), \
             patch("web.imap_poller.extract_from_email", return_value=extraction):
            with closing(get_db()) as db:
                process_email(db, raw, xls_path="/fake/path.xls", pruefungstypen=["G25"])
                db.commit()

    db = sqlite3.connect(db_app / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM email_verifikation WHERE pers_nr = '001'").fetchone()
    db.close()
    assert row["status"] == "bestaetigt"
    assert row["bestaetigt_am"] is not None
    assert row["bestaetigung_herkunft"] == "automatisch"


def test_nachweis_setzt_adresse_geaendert_zurueck(db_app):
    """Zugeordneter Nachweis setzt adresse_geaendert zurueck, auch wenn der Nachweis
    von einer anderen Adresse kam als der gespeicherten (kein Adressabgleich, ADR 0010)."""
    from web.imap_poller import process_email
    from web.app import get_db

    _insert_verifikation_for_member(db_app / "checker.db", status="ausstehend")
    db = sqlite3.connect(db_app / "checker.db")
    db.execute("UPDATE email_verifikation SET adresse_geaendert = 1 WHERE pers_nr = '001'")
    db.commit()
    db.close()

    raw = _make_raw_email(from_addr="Max Andere <andere-adresse@example.com>", message_id="<nachweis-adr-1@example.com>")
    extraction = {
        "pruefungstyp": "G25",
        "faelligkeitsdatum": None,
        "mitglied": _MEMBERS[0],
        "match_score": 1.0,
        "raw_text": "G25",
    }

    with app.app_context():
        with patch("web.imap_poller.load_members_from_xls", return_value=_MEMBERS), \
             patch("web.imap_poller.extract_from_email", return_value=extraction):
            with closing(get_db()) as db:
                process_email(db, raw, xls_path="/fake/path.xls", pruefungstypen=["G25"])
                db.commit()

    db = sqlite3.connect(db_app / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM email_verifikation WHERE pers_nr = '001'").fetchone()
    db.close()
    assert row["adresse_geaendert"] == 0


def test_nachweis_aktualisiert_bestaetigt_am_wenn_bereits_bestaetigt(db_app):
    """Bereits bestätigte Mitglieder bekommen bestaetigt_am aktualisiert."""
    from web.imap_poller import process_email
    from web.app import get_db

    _insert_verifikation_for_member(db_app / "checker.db", status="bestaetigt")

    db = sqlite3.connect(db_app / "checker.db")
    db.execute("UPDATE email_verifikation SET bestaetigt_am = '2020-01-01T00:00:00' WHERE pers_nr = '001'")
    db.commit()
    db.close()

    raw = _make_raw_email(from_addr="Max Mustermann <max@example.com>", message_id="<nachweis-2@example.com>")
    extraction = {
        "pruefungstyp": "G25",
        "faelligkeitsdatum": None,
        "mitglied": _MEMBERS[0],
        "match_score": 1.0,
        "raw_text": "G25",
    }

    with app.app_context():
        with patch("web.imap_poller.load_members_from_xls", return_value=_MEMBERS), \
             patch("web.imap_poller.extract_from_email", return_value=extraction):
            with closing(get_db()) as db:
                process_email(db, raw, xls_path="/fake/path.xls", pruefungstypen=["G25"])
                db.commit()

    db = sqlite3.connect(db_app / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM email_verifikation WHERE pers_nr = '001'").fetchone()
    db.close()
    assert row["status"] == "bestaetigt"
    assert row["bestaetigt_am"] != "2020-01-01T00:00:00"


def test_nachweis_ueberschreibt_stale_manuelle_herkunft(db_app):
    """Ein eingehender Nachweis fuer ein zuvor manuell bestaetigtes Mitglied setzt
    bestaetigung_herkunft auf 'automatisch', statt die alte 'manuell'-Herkunft stehen zu lassen."""
    from web.imap_poller import process_email
    from web.app import get_db

    _insert_verifikation_for_member(db_app / "checker.db", status="bestaetigt")

    db = sqlite3.connect(db_app / "checker.db")
    db.execute("UPDATE email_verifikation SET bestaetigung_herkunft = 'manuell' WHERE pers_nr = '001'")
    db.commit()
    db.close()

    raw = _make_raw_email(from_addr="Max Mustermann <max@example.com>", message_id="<nachweis-3@example.com>")
    extraction = {
        "pruefungstyp": "G25",
        "faelligkeitsdatum": None,
        "mitglied": _MEMBERS[0],
        "match_score": 1.0,
        "raw_text": "G25",
    }

    with app.app_context():
        with patch("web.imap_poller.load_members_from_xls", return_value=_MEMBERS), \
             patch("web.imap_poller.extract_from_email", return_value=extraction):
            with closing(get_db()) as db:
                process_email(db, raw, xls_path="/fake/path.xls", pruefungstypen=["G25"])
                db.commit()

    db = sqlite3.connect(db_app / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM email_verifikation WHERE pers_nr = '001'").fetchone()
    db.close()
    assert row["bestaetigung_herkunft"] == "automatisch"


def test_unklare_zuordnung_aktualisiert_verifikation_nicht(db_app):
    """Nicht zugeordnete Nachweise ändern email_verifikation nicht."""
    from web.imap_poller import process_email
    from web.app import get_db

    _insert_verifikation_for_member(db_app / "checker.db", status="ausstehend")

    raw = _make_raw_email(from_addr="Unbekannt <nobody@example.com>", message_id="<unklar-3@example.com>")
    extraction = {
        "pruefungstyp": "G25",
        "faelligkeitsdatum": None,
        "mitglied": None,
        "match_score": 0.3,
        "raw_text": "G25",
    }

    with app.app_context():
        with patch("web.imap_poller.load_members_from_xls", return_value=_MEMBERS), \
             patch("web.imap_poller.extract_from_email", return_value=extraction):
            with closing(get_db()) as db:
                process_email(db, raw, xls_path="/fake/path.xls", pruefungstypen=["G25"])
                db.commit()

    db = sqlite3.connect(db_app / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM email_verifikation WHERE pers_nr = '001'").fetchone()
    db.close()
    assert row["status"] == "ausstehend"
    assert row["bestaetigt_am"] is None


def test_nachweis_ohne_mitglied_match_aktualisiert_verifikation_nicht(db_app):
    """Nachweis ohne Mitgliederliste (mitglied_nr=None) ändert email_verifikation nicht."""
    from web.imap_poller import process_email
    from web.app import get_db

    _insert_verifikation_for_member(db_app / "checker.db", status="ausstehend")

    raw = _make_raw_email(message_id="<no-member-4@example.com>")
    extraction = {
        "pruefungstyp": "G25",
        "faelligkeitsdatum": None,
        "mitglied": None,
        "match_score": 0.0,
        "raw_text": "G25",
    }

    with app.app_context():
        with patch("web.imap_poller.extract_from_email", return_value=extraction):
            with closing(get_db()) as db:
                # Keine XLS-Datei → members=[], kein UNKLARE_ZUORDNUNG-Zweig
                process_email(db, raw, xls_path=None, pruefungstypen=["G25"])
                db.commit()

    db = sqlite3.connect(db_app / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM email_verifikation WHERE pers_nr = '001'").fetchone()
    db.close()
    assert row["status"] == "ausstehend"
    assert row["bestaetigt_am"] is None


# --- Abweichende Zuordnung (Issue #38) ---

_MEMBER_B = {"pers_nr": "002", "vorname": "Erika", "nachname": "Musterfrau", "email": "erika@example.com"}
_MEMBERS_AB = [_MEMBERS[0], _MEMBER_B]


def test_process_email_abweichende_zuordnung(db_app):
    """Absender matched Mitglied A, Dokument nennt eindeutig Mitglied B → ABWEICHENDE_ZUORDNUNG."""
    from web.imap_poller import process_email
    from web.app import get_db

    raw = _make_raw_email(from_addr="Max Mustermann <max@example.com>", message_id="<abweichend-1@example.com>")
    extraction = {
        "pruefungstyp": "G25",
        "faelligkeitsdatum": None,
        "mitglied": _MEMBERS[0],
        "match_score": 0.95,
        "dokument_mitglied": _MEMBER_B,
        "dokument_match_score": 0.9,
        "raw_text": "G25",
    }

    with app.app_context():
        with patch("web.imap_poller.load_members_from_xls", return_value=_MEMBERS_AB), \
             patch("web.imap_poller.extract_from_email", return_value=extraction):
            with closing(get_db()) as db:
                process_email(db, raw, xls_path="/fake/path.xls", pruefungstypen=["G25"])
                db.commit()

    db = sqlite3.connect(db_app / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM tasks LIMIT 1").fetchone()
    db.close()
    assert row["status"] == "ABWEICHENDE_ZUORDNUNG"
    assert row["mitglied_nr"] is None
    assert row["kandidat_absender_nr"] == "001"
    assert row["kandidat_absender_name"] == "Max Mustermann"
    assert row["kandidat_dokument_nr"] == "002"
    assert row["kandidat_dokument_name"] == "Erika Musterfrau"


def test_process_email_abweichende_zuordnung_aendert_verifikation_nicht(db_app):
    """ABWEICHENDE_ZUORDNUNG darf email_verifikation weder für A noch B ändern."""
    from web.imap_poller import process_email
    from web.app import get_db

    _insert_verifikation_for_member(db_app / "checker.db", pers_nr="001", status="ausstehend")
    _insert_verifikation_for_member(db_app / "checker.db", pers_nr="002", status="ausstehend")

    raw = _make_raw_email(from_addr="Max Mustermann <max@example.com>", message_id="<abweichend-2@example.com>")
    extraction = {
        "pruefungstyp": "G25",
        "faelligkeitsdatum": None,
        "mitglied": _MEMBERS[0],
        "match_score": 0.95,
        "dokument_mitglied": _MEMBER_B,
        "dokument_match_score": 0.9,
        "raw_text": "G25",
    }

    with app.app_context():
        with patch("web.imap_poller.load_members_from_xls", return_value=_MEMBERS_AB), \
             patch("web.imap_poller.extract_from_email", return_value=extraction):
            with closing(get_db()) as db:
                process_email(db, raw, xls_path="/fake/path.xls", pruefungstypen=["G25"])
                db.commit()

    db = sqlite3.connect(db_app / "checker.db")
    db.row_factory = sqlite3.Row
    rows = db.execute("SELECT * FROM email_verifikation").fetchall()
    db.close()
    for row in rows:
        assert row["status"] == "ausstehend"
        assert row["bestaetigt_am"] is None


def test_process_email_nur_dokument_match_bestaetigt_absender_nicht(db_app):
    """Absender matched niemanden, Dokument nennt eindeutig B → NEU mit B, aber
    E-Mail-Bestätigung von B wird NICHT ausgelöst (B hat die Mail nicht selbst geschickt)."""
    from web.imap_poller import process_email
    from web.app import get_db

    _insert_verifikation_for_member(db_app / "checker.db", pers_nr="002", status="ausstehend")

    raw = _make_raw_email(from_addr="Weiterleiter <weiterleiter@example.com>", message_id="<nur-dok-1@example.com>")
    extraction = {
        "pruefungstyp": "G25",
        "faelligkeitsdatum": None,
        "mitglied": None,
        "match_score": 0.2,
        "dokument_mitglied": _MEMBER_B,
        "dokument_match_score": 0.9,
        "raw_text": "G25",
    }

    with app.app_context():
        with patch("web.imap_poller.load_members_from_xls", return_value=_MEMBERS_AB), \
             patch("web.imap_poller.extract_from_email", return_value=extraction):
            with closing(get_db()) as db:
                process_email(db, raw, xls_path="/fake/path.xls", pruefungstypen=["G25"])
                db.commit()

    db = sqlite3.connect(db_app / "checker.db")
    db.row_factory = sqlite3.Row
    task = db.execute("SELECT * FROM tasks LIMIT 1").fetchone()
    verif = db.execute("SELECT * FROM email_verifikation WHERE pers_nr = '002'").fetchone()
    db.close()

    assert task["status"] == "NEU"
    assert task["mitglied_nr"] == "002"
    assert verif["status"] == "ausstehend"
    assert verif["bestaetigt_am"] is None


def test_process_email_nur_absender_match_bestaetigt_wie_bisher(db_app):
    """Nur Absender matched (kein Dokument-Match) → NEU, implizite Bestätigung wie bisher."""
    from web.imap_poller import process_email
    from web.app import get_db

    _insert_verifikation_for_member(db_app / "checker.db", pers_nr="001", status="ausstehend")

    raw = _make_raw_email(from_addr="Max Mustermann <max@example.com>", message_id="<nur-abs-1@example.com>")
    extraction = {
        "pruefungstyp": "G25",
        "faelligkeitsdatum": None,
        "mitglied": _MEMBERS[0],
        "match_score": 0.95,
        "dokument_mitglied": None,
        "dokument_match_score": 0.1,
        "raw_text": "G25",
    }

    with app.app_context():
        with patch("web.imap_poller.load_members_from_xls", return_value=_MEMBERS_AB), \
             patch("web.imap_poller.extract_from_email", return_value=extraction):
            with closing(get_db()) as db:
                process_email(db, raw, xls_path="/fake/path.xls", pruefungstypen=["G25"])
                db.commit()

    db = sqlite3.connect(db_app / "checker.db")
    db.row_factory = sqlite3.Row
    task = db.execute("SELECT * FROM tasks LIMIT 1").fetchone()
    verif = db.execute("SELECT * FROM email_verifikation WHERE pers_nr = '001'").fetchone()
    db.close()

    assert task["status"] == "NEU"
    assert task["mitglied_nr"] == "001"
    assert verif["status"] == "bestaetigt"
    assert verif["bestaetigt_am"] is not None


# --- IMAP-UID Speicherung ---

def test_poll_speichert_imap_uid(db_app):
    """IMAP-UID aus FETCH-Antwort wird in tasks.imap_uid gespeichert."""
    from web.imap_poller import poll_inbox
    from web.app import save_settings

    raw = _make_raw_email()
    mock_imap = MagicMock()
    mock_imap.search.return_value = ("OK", [b"1"])
    mock_imap.fetch.return_value = ("OK", [(b"1 (UID 42 RFC822 {100})", raw)])

    with app.app_context():
        save_settings({
            "imap_host": "imap.example.com",
            "imap_port": "993",
            "imap_user": "test@example.com",
            "imap_password": "pass",
        })
        with patch("web.imap_poller.imaplib.IMAP4_SSL", return_value=mock_imap), \
             patch("web.imap_poller._send_admin_notification"):
            poll_inbox(app)

    db = sqlite3.connect(db_app / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT imap_uid FROM tasks LIMIT 1").fetchone()
    db.close()
    assert row["imap_uid"] == "42"


def test_poll_speichert_keine_uid_wenn_nicht_in_antwort(db_app):
    """imap_uid ist NULL wenn FETCH-Antwort kein UID-Fragment enthält."""
    from web.imap_poller import poll_inbox
    from web.app import save_settings

    raw = _make_raw_email(message_id="<no-uid@example.com>")
    mock_imap = MagicMock()
    mock_imap.search.return_value = ("OK", [b"1"])
    mock_imap.fetch.return_value = ("OK", [(b"1 (RFC822 {100})", raw)])

    with app.app_context():
        save_settings({
            "imap_host": "imap.example.com",
            "imap_port": "993",
            "imap_user": "test@example.com",
            "imap_password": "pass",
        })
        with patch("web.imap_poller.imaplib.IMAP4_SSL", return_value=mock_imap), \
             patch("web.imap_poller._send_admin_notification"):
            poll_inbox(app)

    db = sqlite3.connect(db_app / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT imap_uid FROM tasks LIMIT 1").fetchone()
    db.close()
    assert row["imap_uid"] is None


def test_extract_uid_parsiert_korrekt():
    """_extract_uid extrahiert UID korrekt aus FETCH-Antwort-Fragment."""
    from web.imap_poller import _extract_uid

    assert _extract_uid(b"1 (UID 42 RFC822 {100})") == "42"
    assert _extract_uid(b"5 (UID 1001 RFC822 {512})") == "1001"
    assert _extract_uid(b"1 (RFC822 {100})") is None
    assert _extract_uid(b"") is None


# --- Hilfsfunktionen für IMAP-Statuswechsel ---

def test_imap_move_to_nachweis_verschiebt_email(db_app):
    """imap_move_to_nachweis: COPY nach Nachweis-Ordner + DELETE aus INBOX."""
    from web.imap_poller import imap_move_to_nachweis

    mock_imap = MagicMock()
    mock_imap.select.return_value = ("OK", [b""])
    mock_imap.uid.return_value = ("OK", [b""])
    mock_imap.list.return_value = ("OK", [b'(\\HasNoChildren) "." "Nachweise"'])

    cfg = {
        "imap_host": "imap.example.com",
        "imap_port": "993",
        "imap_user": "test@example.com",
        "imap_password": "pass",
    }

    with patch("web.imap_poller.imaplib.IMAP4_SSL", return_value=mock_imap):
        imap_move_to_nachweis(cfg, "42", "Nachweise")

    mock_imap.select.assert_called_with("INBOX")
    mock_imap.uid.assert_any_call("COPY", b"42", "Nachweise")
    mock_imap.uid.assert_any_call("STORE", b"42", "+FLAGS", "\\Deleted")
    mock_imap.expunge.assert_called_once()


def test_imap_move_to_nachweis_kein_imap_konfiguriert(db_app):
    """imap_move_to_nachweis tut nichts wenn IMAP nicht konfiguriert."""
    from web.imap_poller import imap_move_to_nachweis

    with patch("web.imap_poller.imaplib.IMAP4_SSL") as mock_ssl:
        imap_move_to_nachweis({}, "42", "Nachweise")
        mock_ssl.assert_not_called()


def test_imap_move_to_inbox_sucht_per_message_id(db_app):
    """imap_move_to_inbox: Suche per Message-ID in Nachweis-Ordner, dann COPY nach INBOX."""
    from web.imap_poller import imap_move_to_inbox

    mock_imap = MagicMock()
    mock_imap.select.return_value = ("OK", [b""])
    mock_imap.uid.side_effect = [
        ("OK", [b"7"]),        # SEARCH response: UID 7 gefunden
        ("OK", [b""]),         # COPY response
        ("OK", [b""]),         # STORE response
    ]

    cfg = {
        "imap_host": "imap.example.com",
        "imap_port": "993",
        "imap_user": "test@example.com",
        "imap_password": "pass",
    }

    with patch("web.imap_poller.imaplib.IMAP4_SSL", return_value=mock_imap):
        imap_move_to_inbox(cfg, "<test-1@example.com>", "Nachweise")

    mock_imap.select.assert_called_with("Nachweise")
    mock_imap.uid.assert_any_call("SEARCH", None, "HEADER", "Message-ID", "<test-1@example.com>")
    mock_imap.uid.assert_any_call("COPY", b"7", "INBOX")
    mock_imap.uid.assert_any_call("STORE", b"7", "+FLAGS", "\\Deleted")
    mock_imap.expunge.assert_called_once()


def test_imap_move_to_inbox_email_nicht_gefunden(db_app):
    """imap_move_to_inbox: Kein COPY wenn Email nicht im Ordner."""
    from web.imap_poller import imap_move_to_inbox

    mock_imap = MagicMock()
    mock_imap.select.return_value = ("OK", [b""])
    mock_imap.uid.return_value = ("OK", [b""])  # leeres SEARCH-Ergebnis

    cfg = {
        "imap_host": "imap.example.com",
        "imap_port": "993",
        "imap_user": "test@example.com",
        "imap_password": "pass",
    }

    with patch("web.imap_poller.imaplib.IMAP4_SSL", return_value=mock_imap):
        imap_move_to_inbox(cfg, "<nicht-vorhanden@example.com>", "Nachweise")

    assert not any(
        call[0][0] == "COPY" for call in mock_imap.uid.call_args_list
    )


def test_imap_delete_from_inbox_loescht_email(db_app):
    """imap_delete_from_inbox: STORE +Deleted + Expunge in INBOX."""
    from web.imap_poller import imap_delete_from_inbox

    mock_imap = MagicMock()
    mock_imap.select.return_value = ("OK", [b""])
    mock_imap.uid.return_value = ("OK", [b""])

    cfg = {
        "imap_host": "imap.example.com",
        "imap_port": "993",
        "imap_user": "test@example.com",
        "imap_password": "pass",
    }

    with patch("web.imap_poller.imaplib.IMAP4_SSL", return_value=mock_imap):
        imap_delete_from_inbox(cfg, "99")

    mock_imap.select.assert_called_with("INBOX")
    mock_imap.uid.assert_any_call("STORE", b"99", "+FLAGS", "\\Deleted")
    mock_imap.expunge.assert_called_once()


# --- Task-Reply-Erkennung (Thread-Folgenachrichten, Issue #41) ---

def _insert_task_mit_ausgehender_nachricht(
    db_path, task_status="NEU", ausgehende_message_id="<antwort-1@example.com>"
):
    db = sqlite3.connect(db_path)
    db.execute(
        """INSERT INTO tasks (status, empfangen_am, von_email, von_name, betreff, message_id)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (task_status, "2026-01-01T10:00:00", "max@example.com", "Max Mustermann",
         "G25 Nachweis", "<original-1@example.com>"),
    )
    task_id = db.execute("SELECT id FROM tasks WHERE message_id = ?", ("<original-1@example.com>",)).fetchone()[0]
    db.execute(
        """INSERT INTO task_nachrichten
           (task_id, richtung, zeitstempel, von_email, an_email, betreff, text, message_id)
           VALUES (?, 'ausgehend', ?, ?, ?, ?, ?, ?)""",
        (task_id, "2026-01-02T09:00:00", "admin@example.com", "max@example.com",
         "Re: G25 Nachweis", "Bitte Nachweis nachreichen.", ausgehende_message_id),
    )
    db.commit()
    db.close()
    return task_id


def test_thread_reply_erstellt_keinen_neuen_task(db_app):
    """Eingehende Mail mit In-Reply-To-Treffer auf eine ausgehende Task-Nachricht erzeugt keinen neuen Task."""
    from web.imap_poller import poll_inbox
    from web.app import save_settings

    task_id = _insert_task_mit_ausgehender_nachricht(db_app / "checker.db")
    raw = _make_reply_email(in_reply_to="<antwort-1@example.com>", message_id="<folge-1@example.com>")
    mock_imap = MagicMock()
    mock_imap.search.return_value = ("OK", [b"1"])
    mock_imap.fetch.return_value = ("OK", [(b"1 (UID 55 RFC822 {100})", raw)])

    with app.app_context():
        save_settings(_IMAP_SETTINGS)
        with patch("web.imap_poller.imaplib.IMAP4_SSL", return_value=mock_imap):
            result = poll_inbox(app)

    assert result == 0
    db = sqlite3.connect(db_app / "checker.db")
    count = db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    db.close()
    assert count == 1  # nur der ursprüngliche Task, kein neuer


def test_thread_reply_speichert_eingehende_task_nachricht(db_app):
    """Eingehende Folgenachricht wird als eingehende task_nachrichten-Zeile am bestehenden Task gespeichert."""
    from web.imap_poller import poll_inbox
    from web.app import save_settings

    task_id = _insert_task_mit_ausgehender_nachricht(db_app / "checker.db")
    raw = _make_reply_email(
        in_reply_to="<antwort-1@example.com>",
        message_id="<folge-1@example.com>",
        body="Das Datum stimmt nicht.",
    )
    mock_imap = MagicMock()
    mock_imap.search.return_value = ("OK", [b"1"])
    mock_imap.fetch.return_value = ("OK", [(b"1 (UID 55 RFC822 {100})", raw)])

    with app.app_context():
        save_settings(_IMAP_SETTINGS)
        with patch("web.imap_poller.imaplib.IMAP4_SSL", return_value=mock_imap):
            poll_inbox(app)

    db = sqlite3.connect(db_app / "checker.db")
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT * FROM task_nachrichten WHERE task_id = ? AND richtung = 'eingehend'", (task_id,)
    ).fetchall()
    db.close()
    assert len(rows) == 1
    assert rows[0]["von_email"] == "max@example.com"
    assert rows[0]["message_id"] == "<folge-1@example.com>"
    assert rows[0]["in_reply_to"] == "<antwort-1@example.com>"
    assert rows[0]["imap_uid"] == "55"
    assert rows[0]["raw_email"] == raw


def test_thread_reply_aendert_task_status_nicht(db_app):
    """Der Task-Status bleibt durch eine Thread-Folgenachricht unverändert."""
    from web.imap_poller import poll_inbox
    from web.app import save_settings

    task_id = _insert_task_mit_ausgehender_nachricht(db_app / "checker.db", task_status="UNKLARE_ZUORDNUNG")
    raw = _make_reply_email(in_reply_to="<antwort-1@example.com>", message_id="<folge-1@example.com>")
    mock_imap = MagicMock()
    mock_imap.search.return_value = ("OK", [b"1"])
    mock_imap.fetch.return_value = ("OK", [(b"1 (UID 55 RFC822 {100})", raw)])

    with app.app_context():
        save_settings(_IMAP_SETTINGS)
        with patch("web.imap_poller.imaplib.IMAP4_SSL", return_value=mock_imap):
            poll_inbox(app)

    db = sqlite3.connect(db_app / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
    db.close()
    assert row["status"] == "UNKLARE_ZUORDNUNG"


def test_thread_reply_bleibt_in_inbox(db_app):
    """Eingehende Thread-Folgenachrichten werden beim Polling nicht aus der INBOX verschoben."""
    from web.imap_poller import poll_inbox
    from web.app import save_settings

    _insert_task_mit_ausgehender_nachricht(db_app / "checker.db")
    raw = _make_reply_email(in_reply_to="<antwort-1@example.com>", message_id="<folge-1@example.com>")
    mock_imap = MagicMock()
    mock_imap.search.return_value = ("OK", [b"1"])
    mock_imap.fetch.return_value = ("OK", [(b"1 (UID 55 RFC822 {100})", raw)])

    with app.app_context():
        save_settings(_IMAP_SETTINGS)
        with patch("web.imap_poller.imaplib.IMAP4_SSL", return_value=mock_imap):
            poll_inbox(app)

    assert not mock_imap.copy.called


def test_reply_auf_erledigten_task_erzeugt_neuen_task(db_app):
    """Eine Antwort auf einen bereits ERLEDIGT-Task wird nicht mehr als Thread-Folgenachricht erkannt,
    sondern läuft wie eine Mail ohne Header-Treffer als neuer Task durch."""
    from web.imap_poller import poll_inbox
    from web.app import save_settings

    task_id = _insert_task_mit_ausgehender_nachricht(db_app / "checker.db", task_status="ERLEDIGT")
    raw = _make_reply_email(in_reply_to="<antwort-1@example.com>", message_id="<folge-1@example.com>")
    mock_imap = MagicMock()
    mock_imap.search.return_value = ("OK", [b"1"])
    mock_imap.fetch.return_value = ("OK", [(b"1 (UID 55 RFC822 {100})", raw)])

    with app.app_context():
        save_settings(_IMAP_SETTINGS)
        with patch("web.imap_poller.imaplib.IMAP4_SSL", return_value=mock_imap), \
             patch("web.imap_poller._send_admin_notification"):
            result = poll_inbox(app)

    assert result == 1
    db = sqlite3.connect(db_app / "checker.db")
    count = db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    thread_count = db.execute(
        "SELECT COUNT(*) FROM task_nachrichten WHERE task_id = ? AND richtung = 'eingehend'", (task_id,)
    ).fetchone()[0]
    db.close()
    assert count == 2  # ursprünglicher Task + neuer Task für die unzuordenbare Antwort
    assert thread_count == 0


def test_verifikation_match_hat_vorrang_vor_task_reply_match(db_app):
    """Prüfreihenfolge: Verifikationsmail-Match wird zuerst geprüft, auch wenn zufällig auch ein Task existiert."""
    from web.imap_poller import poll_inbox
    from web.app import save_settings

    _insert_verifikation(db_app / "checker.db", message_id="<verif-123@example.com>")
    _insert_task_mit_ausgehender_nachricht(db_app / "checker.db", ausgehende_message_id="<andere-antwort@example.com>")
    raw = _make_reply_email(in_reply_to="<verif-123@example.com>", message_id="<folge-1@example.com>")
    mock_imap = _make_mock_imap(raw)

    with app.app_context():
        save_settings(_IMAP_SETTINGS)
        with patch("web.imap_poller.imaplib.IMAP4_SSL", return_value=mock_imap):
            poll_inbox(app)

    db = sqlite3.connect(db_app / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT status FROM email_verifikation").fetchone()
    nachrichten_count = db.execute("SELECT COUNT(*) FROM task_nachrichten WHERE richtung = 'eingehend'").fetchone()[0]
    db.close()
    assert row["status"] == "bestaetigt"
    assert nachrichten_count == 0
