import sqlite3
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


# --- _do_run schreibt in Run-Historie ---

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
