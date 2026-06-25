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
_scheduler_started = False

SETTINGS_DEFAULTS = {
    "smtp_host": os.getenv("SMTP_HOST", ""),
    "smtp_port": os.getenv("SMTP_PORT", "587"),
    "smtp_user": os.getenv("SMTP_USER", ""),
    "smtp_password": os.getenv("SMTP_PASSWORD", ""),
    "smtp_from": os.getenv("SMTP_FROM", ""),
    "imap_host": os.getenv("IMAP_HOST", ""),
    "imap_port": os.getenv("IMAP_PORT", "993"),
    "imap_user": os.getenv("IMAP_USER", ""),
    "imap_password": os.getenv("IMAP_PASSWORD", ""),
    "kommandanten_cc": os.getenv("KOMMANDANTEN_CC", ""),
    "zusammenfassung_an": os.getenv("ZUSAMMENFASSUNG_AN", ""),
    "warn_days": os.getenv("WARN_DAYS", "90"),
    "pruefungstypen": os.getenv("PRUEFUNGSTYPEN", "G25"),
    "archiv_tage": "365",
    "script_intervall": "wöchentlich",
    "naechster_lauf": "",
}


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
        db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT ''
            )
        """)
        db.commit()


def get_settings() -> dict:
    result = dict(SETTINGS_DEFAULTS)
    with closing(get_db()) as db:
        rows = db.execute("SELECT key, value FROM settings").fetchall()
    for row in rows:
        result[row["key"]] = row["value"]
    return result


def save_settings(data: dict):
    with closing(get_db()) as db:
        for key, value in data.items():
            db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
        db.commit()


def _safe_int(val, default: int) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _do_run(dry_run: bool = False) -> tuple:
    """Lauf ausführen; schreibt immer einen DB-Eintrag, auch bei FileNotFoundError."""
    gestartet = datetime.now().isoformat(timespec="seconds")
    with closing(get_db()) as db:
        cursor = db.execute(
            "INSERT INTO runs (gestartet_am, dry_run, status) VALUES (?, ?, 'laufend')",
            (gestartet, int(dry_run)),
        )
        run_id = cursor.lastrowid
        db.commit()

    try:
        if not _xls_path().exists():
            raise FileNotFoundError("Keine XLS-Datei vorhanden")

        name_file = _xls_name_path()
        xls_dateiname = name_file.read_text(encoding="utf-8").strip() if name_file.exists() else "latest.xls"

        cfg = get_settings()
        warn_days = _safe_int(cfg.get("warn_days"), 90)
        pruefungstypen = [t.strip() for t in (cfg.get("pruefungstypen") or "G25").split(",") if t.strip()]
        smtp_config = {
            "host": cfg.get("smtp_host", ""),
            "port": _safe_int(cfg.get("smtp_port"), 587),
            "user": cfg.get("smtp_user", ""),
            "password": cfg.get("smtp_password", ""),
            "from_addr": cfg.get("smtp_from", ""),
        }
        kommandanten_cc = [e.strip() for e in (cfg.get("kommandanten_cc") or "").split(",") if e.strip()]
        zusammenfassung_an = [e.strip() for e in (cfg.get("zusammenfassung_an") or "").split(",") if e.strip()]

        persons = check_examinations(str(_xls_path()), warn_days=warn_days, pruefungstypen=pruefungstypen)
        emails_gesendet = send_notifications(
            persons, dry_run=dry_run, smtp_config=smtp_config, kommandanten_cc=kommandanten_cc
        )
        send_summary(persons, dry_run=dry_run, smtp_config=smtp_config, zusammenfassung_an=zusammenfassung_an)

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

        return len(persons), emails_gesendet
    except Exception as e:
        abgeschlossen = datetime.now().isoformat(timespec="seconds")
        with closing(get_db()) as db:
            db.execute(
                "UPDATE runs SET abgeschlossen_am=?, status='fehler', fehlermeldung=? WHERE id=?",
                (abgeschlossen, str(e), run_id),
            )
            db.commit()
        raise


@app.before_request
def _ensure_db():
    global _scheduler_started
    db_path = str(_db_path())
    if db_path not in _initialized_dbs:
        init_db()
        _initialized_dbs.add(db_path)
    if not _scheduler_started and not current_app.config.get("TESTING"):
        from web import scheduler
        scheduler.start(app)
        _scheduler_started = True


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
    cfg = get_settings()
    naechster_lauf = cfg.get("naechster_lauf") or None
    script_intervall = cfg.get("script_intervall", "manuell")
    return render_template(
        "index.html",
        runs=runs,
        xls_vorhanden=xls_vorhanden,
        xls_dateiname=xls_dateiname,
        naechster_lauf=naechster_lauf,
        script_intervall=script_intervall,
    )


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


@app.route("/settings", methods=["GET"])
def settings_page():
    cfg = get_settings()
    return render_template("settings.html", cfg=cfg)


@app.route("/settings", methods=["POST"])
def settings_save():
    keys = [
        "smtp_host", "smtp_port", "smtp_user", "smtp_password", "smtp_from",
        "imap_host", "imap_port", "imap_user", "imap_password",
        "kommandanten_cc", "zusammenfassung_an",
        "warn_days", "pruefungstypen", "archiv_tage", "script_intervall",
    ]
    data = {k: request.form.get(k, "") for k in keys}
    save_settings(data)

    from web import scheduler
    scheduler.reschedule(app)

    flash("Einstellungen gespeichert.", "success")
    return redirect(url_for("settings_page"))


@app.route("/run", methods=["POST"])
def run():
    if not _xls_path().exists():
        flash("Keine XLS-Datei vorhanden. Bitte zuerst hochladen.", "error")
        return redirect(url_for("index"))

    dry_run = request.form.get("dry_run") == "1"

    try:
        personen, emails = _do_run(dry_run=dry_run)
        modus = " (DRY-RUN)" if dry_run else ""
        flash(
            f"Lauf abgeschlossen{modus}: {personen} Person(en) mit Handlungsbedarf, "
            f"{emails} E-Mail(s) verarbeitet.",
            "success",
        )
    except Exception as e:
        flash(f"Fehler beim Ausführen: {e}", "error")

    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=os.getenv("FLASK_DEBUG", "0") == "1")
