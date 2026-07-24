from unittest.mock import patch

import pytest


# --- Scheduler startet nicht in Tests ---

def test_scheduler_startet_nicht_in_test_modus(tmp_path):
    from web.app import app, init_db
    from web import scheduler as sched

    sched._scheduler = None  # Reset
    app.config["TESTING"] = True
    app.config["DATA_DIR"] = tmp_path

    with app.app_context():
        init_db()
        sched.start(app)

    assert sched.get_scheduler() is None


# --- IMAP-Retention-Job (Issue #45) ---

def test_imap_retention_job_fehler_wird_geloggt(tmp_path):
    """Ein Fehler beim Retention-Lauf wird geloggt, ohne eine Exception nach außen zu werfen."""
    from web.app import app, init_db
    from web import scheduler as sched

    app.config["TESTING"] = True
    app.config["DATA_DIR"] = tmp_path
    with app.app_context():
        init_db()

    with patch("web.imap_poller.imap_retention_cleanup", side_effect=Exception("IMAP down")):
        sched._imap_retention_job(app)  # darf nicht werfen


def test_imap_retention_job_ruft_cleanup_mit_konfigurierten_ordnern_auf(tmp_path):
    from web.app import app, init_db, save_settings
    from web import scheduler as sched

    app.config["TESTING"] = True
    app.config["DATA_DIR"] = tmp_path
    with app.app_context():
        init_db()
        save_settings({
            "imap_host": "imap.example.com",
            "imap_sent_ordner": "INBOX.Sent",
            "imap_nachweis_ordner": "Nachweise",
            "imap_verifikation_ordner": "u-checker-verifikation",
            "imap_retention_tage": "30",
        })

    with patch("web.imap_poller.imap_retention_cleanup", return_value=0) as mock_cleanup:
        sched._imap_retention_job(app)

    mock_cleanup.assert_called_once()
    args = mock_cleanup.call_args.args
    assert args[1] == ["INBOX.Sent", "Nachweise", "u-checker-verifikation"]
    assert args[2] == 30

