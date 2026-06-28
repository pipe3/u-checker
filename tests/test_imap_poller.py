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
