import sqlite3
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from web.scheduler import naechster_lauf_berechnen


# --- naechster_lauf_berechnen ---

def test_berechnung_manuell_gibt_none():
    assert naechster_lauf_berechnen("manuell") is None


def test_berechnung_unbekanntes_intervall_gibt_none():
    assert naechster_lauf_berechnen("täglich") is None


def test_berechnung_woechentlich():
    von = datetime(2025, 1, 1, 12, 0, 0)
    result = naechster_lauf_berechnen("wöchentlich", von=von)
    assert result == datetime(2025, 1, 8, 12, 0, 0)


def test_berechnung_monatlich():
    von = datetime(2025, 1, 1, 12, 0, 0)
    result = naechster_lauf_berechnen("monatlich", von=von)
    assert result == datetime(2025, 1, 31, 12, 0, 0)


def test_berechnung_ohne_von_liefert_zukunft():
    result = naechster_lauf_berechnen("wöchentlich")
    assert result is not None
    assert result > datetime.now()


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


# --- _do_run schreibt in Run-Historie (Scheduler-Kontext ohne HTTP-Request) ---

def test_do_run_schreibt_in_run_historie(tmp_path):
    from web.app import app, _do_run, init_db

    app.config["TESTING"] = True
    app.config["DATA_DIR"] = tmp_path
    (tmp_path / "latest.xls").write_bytes(b"dummy")
    (tmp_path / "latest_name.txt").write_text("export.xls", encoding="utf-8")

    with app.app_context(), \
         patch("web.app.check_examinations", return_value=[]), \
         patch("web.app.send_notifications", return_value=0), \
         patch("web.app.send_summary"):
        init_db()
        _do_run(dry_run=False)

    db = sqlite3.connect(tmp_path / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    db.close()

    assert row is not None
    assert row["status"] == "fertig"
    assert row["dry_run"] == 0


def test_do_run_fehler_wird_in_db_gespeichert(tmp_path):
    from web.app import app, _do_run, init_db

    app.config["TESTING"] = True
    app.config["DATA_DIR"] = tmp_path
    (tmp_path / "latest.xls").write_bytes(b"dummy")

    with app.app_context(), \
         patch("web.app.check_examinations", side_effect=RuntimeError("Testfehler")):
        init_db()
        with pytest.raises(RuntimeError):
            _do_run(dry_run=False)

    db = sqlite3.connect(tmp_path / "checker.db")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    db.close()

    assert row["status"] == "fehler"
    assert "Testfehler" in row["fehlermeldung"]


# --- Zeitplan-Persistenz: naechster_lauf wird nach Neustart wiederhergestellt ---

def test_zeitplan_wird_nach_neustart_wiederhergestellt(tmp_path):
    from web.app import app, init_db, save_settings
    from web import scheduler as sched

    future_dt = (datetime.now() + timedelta(hours=2)).replace(microsecond=0)

    app.config["TESTING"] = False
    app.config["DATA_DIR"] = tmp_path

    try:
        with app.app_context():
            init_db()
            save_settings({
                "script_intervall": "wöchentlich",
                "naechster_lauf": future_dt.isoformat(timespec="seconds"),
            })

        mock_bg = MagicMock()
        mock_bg.get_job.return_value = None

        with patch("web.scheduler.BackgroundScheduler", return_value=mock_bg):
            sched._scheduler = None
            sched.start(app)

        # Mindestens der Zeitplan-Job muss registriert worden sein
        calls_kwargs = [call[1] for call in mock_bg.add_job.call_args_list]
        zeitplan_calls = [kw for kw in calls_kwargs if kw.get("id") == "automatischer_lauf"]
        assert len(zeitplan_calls) == 1
        assert zeitplan_calls[0]["run_date"] == future_dt
    finally:
        app.config["TESTING"] = True
        sched.stop()


def test_zeitplan_manuell_startet_keinen_job(tmp_path):
    from web.app import app, init_db, save_settings
    from web import scheduler as sched

    app.config["TESTING"] = False
    app.config["DATA_DIR"] = tmp_path

    try:
        with app.app_context():
            init_db()
            save_settings({"script_intervall": "manuell", "naechster_lauf": ""})

        mock_bg = MagicMock()

        with patch("web.scheduler.BackgroundScheduler", return_value=mock_bg):
            sched._scheduler = None
            sched.start(app)

        # Im manuell-Modus darf kein Zeitplan-Job registriert werden (nur Cleanup-Job ist ok)
        calls_kwargs = [call[1] for call in mock_bg.add_job.call_args_list]
        zeitplan_calls = [kw for kw in calls_kwargs if kw.get("id") == "automatischer_lauf"]
        assert len(zeitplan_calls) == 0
    finally:
        app.config["TESTING"] = True
        sched.stop()


# --- naechster_lauf wird in DB gespeichert ---

def test_job_ausfuehren_ruft_do_run_mit_manuell_false(tmp_path):
    """_job_ausfuehren muss _do_run mit manuell=False aufrufen (automatischer Lauf)."""
    from web.app import app, init_db
    from web.scheduler import _job_ausfuehren

    app.config["TESTING"] = True
    app.config["DATA_DIR"] = tmp_path

    with app.app_context():
        init_db()

    with app.app_context(), \
         patch("web.app._do_run", return_value=(0, 0)) as mock_do_run, \
         patch("web.scheduler.reschedule"):
        _job_ausfuehren(app)

    mock_do_run.assert_called_once_with(dry_run=False, manuell=False)


def test_naechster_lauf_wird_in_db_gespeichert_nach_reschedule(tmp_path):
    from web.app import app, init_db, save_settings, get_settings
    from web import scheduler as sched

    app.config["TESTING"] = False
    app.config["DATA_DIR"] = tmp_path

    try:
        with app.app_context():
            init_db()
            save_settings({"script_intervall": "wöchentlich", "naechster_lauf": ""})

        mock_bg = MagicMock()
        mock_bg.get_job.return_value = None

        with patch("web.scheduler.BackgroundScheduler", return_value=mock_bg):
            sched._scheduler = None
            sched.start(app)

        with app.app_context():
            cfg = get_settings()
            naechster = cfg.get("naechster_lauf", "")

        assert naechster != ""
        dt = datetime.fromisoformat(naechster)
        assert dt > datetime.now()
        assert dt < datetime.now() + timedelta(days=8)
    finally:
        app.config["TESTING"] = True
        sched.stop()
