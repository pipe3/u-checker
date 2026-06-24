import os
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from flask import Flask, current_app, flash, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

from u_checker import check_examinations, send_notifications, send_summary

app = Flask(__name__)
app.config.from_mapping(
    SECRET_KEY=os.getenv("SECRET_KEY", "dev-secret-change-in-production"),
    DATA_DIR=Path(os.getenv("DATA_DIR", "/data")),
)

_initialized_dbs: set = set()


def _data_dir() -> Path:
    return Path(current_app.config["DATA_DIR"])


def _db_path() -> Path:
    return _data_dir() / "checker.db"


def _xls_path() -> Path:
    return _data_dir() / "latest.xls"


def _xls_name_path() -> Path:
    return _data_dir() / "latest_name.txt"


def get_db():
    db = sqlite3.connect(_db_path())
    db.row_factory = sqlite3.Row
    return db


def init_db():
    _data_dir().mkdir(parents=True, exist_ok=True)
    with closing(get_db()) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gestartet_am TEXT NOT NULL,
                abgeschlossen_am TEXT,
                xls_dateiname TEXT,
                personen_gefunden INTEGER DEFAULT 0,
                emails_gesendet INTEGER DEFAULT 0,
                dry_run INTEGER DEFAULT 0,
                status TEXT DEFAULT 'laufend',
                fehlermeldung TEXT
            )
        """)
        db.commit()


@app.before_request
def _ensure_db():
    db_path = str(_db_path())
    if db_path not in _initialized_dbs:
        init_db()
        _initialized_dbs.add(db_path)


@app.route("/")
def index():
    with closing(get_db()) as db:
        runs = db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 20").fetchall()
    xls_vorhanden = _xls_path().exists()
    xls_dateiname = None
    if xls_vorhanden:
        name_file = _xls_name_path()
        if name_file.exists():
            xls_dateiname = name_file.read_text(encoding="utf-8").strip()
    return render_template("index.html", runs=runs, xls_vorhanden=xls_vorhanden, xls_dateiname=xls_dateiname)


@app.route("/upload", methods=["POST"])
def upload():
    if "xls_datei" not in request.files:
        flash("Keine Datei ausgewählt.", "error")
        return redirect(url_for("index"))

    datei = request.files["xls_datei"]
    if not datei.filename:
        flash("Keine Datei ausgewählt.", "error")
        return redirect(url_for("index"))

    dateiname = secure_filename(datei.filename)
    if not dateiname.lower().endswith(".xls"):
        flash("Nur XLS-Dateien erlaubt (MP-Feuer exportiert im .xls-Format).", "error")
        return redirect(url_for("index"))

    _data_dir().mkdir(parents=True, exist_ok=True)
    datei.save(_xls_path())
    _xls_name_path().write_text(dateiname, encoding="utf-8")
    flash(f"Datei \"{dateiname}\" erfolgreich hochgeladen.", "success")
    return redirect(url_for("index"))


@app.route("/run", methods=["POST"])
def run():
    if not _xls_path().exists():
        flash("Keine XLS-Datei vorhanden. Bitte zuerst hochladen.", "error")
        return redirect(url_for("index"))

    dry_run = request.form.get("dry_run") == "1"
    gestartet = datetime.now().isoformat(timespec="seconds")

    name_file = _xls_name_path()
    xls_dateiname = name_file.read_text(encoding="utf-8").strip() if name_file.exists() else "latest.xls"

    with closing(get_db()) as db:
        cursor = db.execute(
            "INSERT INTO runs (gestartet_am, dry_run, status) VALUES (?, ?, 'laufend')",
            (gestartet, int(dry_run)),
        )
        run_id = cursor.lastrowid
        db.commit()

    try:
        persons = check_examinations(str(_xls_path()))
        emails_gesendet = send_notifications(persons, dry_run=dry_run)
        send_summary(persons, dry_run=dry_run)

        abgeschlossen = datetime.now().isoformat(timespec="seconds")
        with closing(get_db()) as db:
            db.execute(
                """UPDATE runs SET
                   abgeschlossen_am=?, xls_dateiname=?,
                   personen_gefunden=?, emails_gesendet=?, status='fertig'
                   WHERE id=?""",
                (abgeschlossen, xls_dateiname, len(persons), emails_gesendet, run_id),
            )
            db.commit()

        modus = " (DRY-RUN)" if dry_run else ""
        flash(
            f"Lauf abgeschlossen{modus}: {len(persons)} Person(en) mit Handlungsbedarf, "
            f"{emails_gesendet} E-Mail(s) verarbeitet.",
            "success",
        )
    except Exception as e:
        abgeschlossen = datetime.now().isoformat(timespec="seconds")
        with closing(get_db()) as db:
            db.execute(
                "UPDATE runs SET abgeschlossen_am=?, status='fehler', fehlermeldung=? WHERE id=?",
                (abgeschlossen, str(e), run_id),
            )
            db.commit()
        flash(f"Fehler beim Ausführen: {e}", "error")

    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=os.getenv("FLASK_DEBUG", "0") == "1")
