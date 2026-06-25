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
