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

