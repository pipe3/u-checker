from datetime import date
from unittest.mock import MagicMock, patch

from u_checker.checker import Person, Pruefung
from u_checker.mailer import (
    send_notifications,
    send_summary,
    send_task_antwort,
    send_verifikationsmail,
)

_SMTP_CONFIG = {"host": "smtp.example.com", "port": 587, "from_addr": "noreply@example.com"}


def _make_person(pers_nr="001", email="max@example.com", status="warnung"):
    return Person(
        pers_nr=pers_nr,
        vorname="Max",
        nachname="Muster",
        email=email,
        pruefungen=[
            Pruefung(typ="G25", beschreibung="G25-Test", datum=date(2026, 1, 1), status=status),
        ],
    )


# --- send_verifikationsmail ---

@patch("u_checker.mailer.smtplib.SMTP")
def test_send_verifikationsmail_appended_mit_imap_connection(mock_smtp):
    mock_imap = MagicMock()
    send_verifikationsmail(
        _SMTP_CONFIG, "max@example.com", "Max", "Muster",
        imap_connection=mock_imap, sent_ordner="INBOX.Sent",
    )
    mock_imap.append.assert_called_once()
    args = mock_imap.append.call_args.args
    assert args[0] == "INBOX.Sent"
    assert args[1] == "\\Seen"


@patch("u_checker.mailer.smtplib.SMTP")
def test_send_verifikationsmail_ohne_imap_connection_kein_append(mock_smtp):
    send_verifikationsmail(_SMTP_CONFIG, "max@example.com", "Max", "Muster")
    mock_smtp.return_value.__enter__.return_value.sendmail.assert_called_once()


@patch("u_checker.mailer.smtplib.SMTP")
def test_send_verifikationsmail_append_fehler_wird_abgefangen(mock_smtp):
    mock_imap = MagicMock()
    mock_imap.append.side_effect = Exception("IMAP down")
    msg_id = send_verifikationsmail(
        _SMTP_CONFIG, "max@example.com", "Max", "Muster",
        imap_connection=mock_imap, sent_ordner="INBOX.Sent",
    )
    assert msg_id
    mock_smtp.return_value.__enter__.return_value.sendmail.assert_called_once()


# --- send_task_antwort ---

@patch("u_checker.mailer.smtplib.SMTP")
def test_send_task_antwort_appended_mit_imap_connection(mock_smtp):
    mock_imap = MagicMock()
    send_task_antwort(
        _SMTP_CONFIG, "max@example.com", "Re: Betreff", "Text",
        imap_connection=mock_imap, sent_ordner="INBOX.Sent",
    )
    mock_imap.append.assert_called_once()
    assert mock_imap.append.call_args.args[0] == "INBOX.Sent"
    assert mock_imap.append.call_args.args[1] == "\\Seen"


@patch("u_checker.mailer.smtplib.SMTP")
def test_send_task_antwort_ohne_imap_connection_kein_append(mock_smtp):
    send_task_antwort(_SMTP_CONFIG, "max@example.com", "Re: Betreff", "Text")
    mock_smtp.return_value.__enter__.return_value.sendmail.assert_called_once()


@patch("u_checker.mailer.smtplib.SMTP")
def test_send_task_antwort_append_fehler_wird_abgefangen(mock_smtp):
    mock_imap = MagicMock()
    mock_imap.append.side_effect = Exception("IMAP down")
    msg_id = send_task_antwort(
        _SMTP_CONFIG, "max@example.com", "Re: Betreff", "Text",
        imap_connection=mock_imap, sent_ordner="INBOX.Sent",
    )
    assert msg_id
    mock_smtp.return_value.__enter__.return_value.sendmail.assert_called_once()


# --- send_notifications: Batch nutzt eine gemeinsame Connection ---

@patch("u_checker.mailer.smtplib.SMTP")
def test_send_notifications_batch_appended_pro_person_gleiche_connection(mock_smtp):
    mock_imap = MagicMock()
    persons = [_make_person("001", "a@x.com"), _make_person("002", "b@x.com"), _make_person("003", "c@x.com")]

    send_notifications(
        persons, dry_run=False, smtp_config=_SMTP_CONFIG,
        imap_connection=mock_imap, sent_ordner="INBOX.Sent",
    )

    assert mock_imap.append.call_count == 3
    for call in mock_imap.append.call_args_list:
        assert call.args[0] == "INBOX.Sent"


def test_mailer_importiert_kein_imaplib():
    """mailer.py bleibt IMAP-/Settings-agnostisch: es baut nie selbst eine IMAP-Verbindung auf."""
    import u_checker.mailer as mailer_module

    assert not hasattr(mailer_module, "imaplib")


@patch("u_checker.mailer.smtplib.SMTP")
def test_send_notifications_ohne_imap_connection_kein_append(mock_smtp):
    persons = [_make_person("001", "a@x.com")]
    send_notifications(persons, dry_run=False, smtp_config=_SMTP_CONFIG)
    mock_smtp.return_value.__enter__.return_value.sendmail.assert_called_once()


# --- send_summary ---

@patch("u_checker.mailer.smtplib.SMTP")
def test_send_summary_appended_in_sent_ordner(mock_smtp):
    mock_imap = MagicMock()
    persons = [_make_person("001", "a@x.com", status="abgelaufen")]

    send_summary(
        persons, dry_run=False, smtp_config=_SMTP_CONFIG,
        zusammenfassung_an=["chef@example.com"],
        imap_connection=mock_imap, sent_ordner="INBOX.Sent",
    )

    mock_imap.append.assert_called_once()
    assert mock_imap.append.call_args.args[0] == "INBOX.Sent"
    assert mock_imap.append.call_args.args[1] == "\\Seen"


@patch("u_checker.mailer.smtplib.SMTP")
def test_send_summary_ohne_imap_connection_kein_append(mock_smtp):
    persons = [_make_person("001", "a@x.com", status="abgelaufen")]
    send_summary(persons, dry_run=False, smtp_config=_SMTP_CONFIG, zusammenfassung_an=["chef@example.com"])
    mock_smtp.return_value.__enter__.return_value.sendmail.assert_called_once()
