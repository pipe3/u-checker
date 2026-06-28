import logging
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

from flask import Flask, Response, abort, current_app, flash, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

from u_checker import check_examinations, send_notifications, send_summary
from u_checker.mailer import DEFAULT_EMAIL_BETREFF as _DEFAULT_EMAIL_BETREFF
from u_checker.mailer import DEFAULT_ZUSAMMENFASSUNG_BETREFF as _DEFAULT_ZUSAMMENFASSUNG_BETREFF
from u_checker.mailer import DEFAULT_ZUSAMMENFASSUNG_TEMPLATE as _DEFAULT_ZUSAMMENFASSUNG_TEMPLATE
from u_checker.mailer import DEFAULT_VERIFIKATIONS_BETREFF as _DEFAULT_VERIFIKATIONS_BETREFF
from u_checker.mailer import DEFAULT_VERIFIKATIONS_TEMPLATE as _DEFAULT_VERIFIKATIONS_TEMPLATE
from u_checker.mailer import send_simple_mail, send_verifikationsmail
from web.extractor import load_members_from_xls

logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config.from_mapping(
    SECRET_KEY=os.getenv("SECRET_KEY", "dev-secret-change-in-production"),
    DATA_DIR=Path(os.getenv("DATA_DIR", "/data")),
)

@app.template_filter("datum_de")
def datum_de(value: str) -> str:
    """Wandelt ISO-Datum (yyyy-mm-dd) in deutsches Format (dd.mm.yyyy) um."""
    if not value:
        return value
    try:
        y, m, d = value[:10].split("-")
        return f"{d}.{m}.{y}"
    except Exception:
        return value


_initialized_dbs: set = set()
_scheduler_started = False

_DEFAULT_EMAIL_TEMPLATE = (
    "Hallo {vorname} {nachname},\n\n"
    "bei der Prüfung Ihrer Untersuchungsfristen wurden folgende Punkte festgestellt:\n\n"
    "{pruefungen_liste}\n\n"
    "Bitte kümmern Sie sich zeitnah um eine Verlängerung bzw. Erneuerung der entsprechenden Untersuchung(en).\n\n"
    "Bei Fragen wenden Sie sich bitte an den Kommandanten.\n\n"
    "Mit freundlichen Grüßen\n"
    "Ihre Feuerwehr"
)

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
    "imap_poll_minuten": os.getenv("IMAP_POLL_MINUTEN", "5"),
    "kommandanten_cc": os.getenv("KOMMANDANTEN_CC", ""),
    "zusammenfassung_an": os.getenv("ZUSAMMENFASSUNG_AN", ""),
    "warn_days": os.getenv("WARN_DAYS", "90"),
    "pruefungstypen": os.getenv("PRUEFUNGSTYPEN", "G25"),
    "archiv_tage": "365",
    "email_betreff": _DEFAULT_EMAIL_BETREFF,
    "email_template": _DEFAULT_EMAIL_TEMPLATE,
    "zusammenfassung_betreff": _DEFAULT_ZUSAMMENFASSUNG_BETREFF,
    "zusammenfassung_template": _DEFAULT_ZUSAMMENFASSUNG_TEMPLATE,
    "verifikation_betreff": _DEFAULT_VERIFIKATIONS_BETREFF,
    "verifikation_template": _DEFAULT_VERIFIKATIONS_TEMPLATE,
    "imap_verifikation_ordner": "u-checker-verifikation",
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
        db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT NOT NULL DEFAULT 'NEU',
                empfangen_am TEXT NOT NULL,
                von_email TEXT,
                von_name TEXT,
                betreff TEXT,
                message_id TEXT UNIQUE,
                raw_email BLOB,
                anhang_count INTEGER DEFAULT 0,
                pruefungstyp TEXT,
                faelligkeitsdatum TEXT,
                mitglied_nr TEXT,
                mitglied_name TEXT,
                hinweis TEXT
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS email_verifikation (
                pers_nr TEXT PRIMARY KEY,
                vorname TEXT NOT NULL,
                nachname TEXT NOT NULL,
                email TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'nie_geprueft',
                gesendet_am TEXT,
                bestaetigt_am TEXT,
                adresse_geaendert INTEGER NOT NULL DEFAULT 0,
                verifikationsmail_message_id TEXT
            )
        """)
        _migrate_tasks(db)
        _migrate_settings(db)
        db.commit()


def _sync_email_verifikation(members: list) -> None:
    """Synchronisiert aktive Mitglieder in email_verifikation."""
    with closing(get_db()) as db:
        for m in members:
            existing = db.execute(
                "SELECT email FROM email_verifikation WHERE pers_nr = ?",
                (m["pers_nr"],),
            ).fetchone()
            if existing is None:
                db.execute(
                    "INSERT INTO email_verifikation (pers_nr, vorname, nachname, email) VALUES (?, ?, ?, ?)",
                    (m["pers_nr"], m["vorname"], m["nachname"], m["email"]),
                )
            elif existing["email"] != m["email"]:
                db.execute(
                    "UPDATE email_verifikation SET vorname=?, nachname=?, email=?, adresse_geaendert=1 WHERE pers_nr=?",
                    (m["vorname"], m["nachname"], m["email"], m["pers_nr"]),
                )
        db.commit()


def _migrate_settings(db):
    """Entfernt veraltete Settings-Keys aus bestehenden DBs."""
    for key in ("script_intervall", "naechster_lauf"):
        db.execute("DELETE FROM settings WHERE key = ?", (key,))


def _migrate_tasks(db):
    """Fügt fehlende Spalten zur tasks-Tabelle hinzu (für bestehende DBs)."""
    existing = {row[1] for row in db.execute("PRAGMA table_info(tasks)").fetchall()}
    new_cols = [
        ("pruefungstyp", "TEXT"),
        ("faelligkeitsdatum", "TEXT"),
        ("mitglied_nr", "TEXT"),
        ("mitglied_name", "TEXT"),
        ("hinweis", "TEXT"),
        ("erledigt_am", "TEXT"),
        ("raw_text", "TEXT"),
    ]
    for col, coltype in new_cols:
        if col not in existing:
            db.execute(f"ALTER TABLE tasks ADD COLUMN {col} {coltype}")


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


def archiv_cleanup(archiv_tage: Optional[int] = None) -> int:
    """Löscht ERLEDIGT-Tasks, deren erledigt_am älter als archiv_tage Tage ist."""
    if archiv_tage is None:
        cfg = get_settings()
        archiv_tage = _safe_int(cfg.get("archiv_tage"), 365)
    grenze = (datetime.now() - timedelta(days=archiv_tage)).isoformat(timespec="seconds")
    with closing(get_db()) as db:
        cursor = db.execute(
            "DELETE FROM tasks WHERE status = 'ERLEDIGT' AND erledigt_am IS NOT NULL AND erledigt_am < ?",
            (grenze,),
        )
        db.commit()
        return cursor.rowcount


def _build_smtp_config(cfg: dict) -> dict:
    return {
        "host": cfg.get("smtp_host", ""),
        "port": _safe_int(cfg.get("smtp_port"), 587),
        "user": cfg.get("smtp_user", ""),
        "password": cfg.get("smtp_password", ""),
        "from_addr": cfg.get("smtp_from", ""),
    }


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
        smtp_config = _build_smtp_config(cfg)
        kommandanten_cc = [e.strip() for e in (cfg.get("kommandanten_cc") or "").split(",") if e.strip()]
        zusammenfassung_an = [e.strip() for e in (cfg.get("zusammenfassung_an") or "").split(",") if e.strip()]

        email_betreff = cfg.get("email_betreff") or _DEFAULT_EMAIL_BETREFF
        email_template = cfg.get("email_template") or _DEFAULT_EMAIL_TEMPLATE
        zusammenfassung_betreff = cfg.get("zusammenfassung_betreff") or _DEFAULT_ZUSAMMENFASSUNG_BETREFF
        zusammenfassung_template = cfg.get("zusammenfassung_template") or _DEFAULT_ZUSAMMENFASSUNG_TEMPLATE

        persons = check_examinations(str(_xls_path()), warn_days=warn_days, pruefungstypen=pruefungstypen)
        emails_gesendet = send_notifications(
            persons, dry_run=dry_run, smtp_config=smtp_config, kommandanten_cc=kommandanten_cc,
            email_betreff=email_betreff, email_template=email_template,
        )
        send_summary(
            persons, dry_run=dry_run, smtp_config=smtp_config, zusammenfassung_an=zusammenfassung_an,
            zusammenfassung_betreff=zusammenfassung_betreff, zusammenfassung_template=zusammenfassung_template,
        )

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

        return persons, emails_gesendet
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
        tasks = db.execute("SELECT * FROM tasks ORDER BY empfangen_am DESC").fetchall()
        offene_tasks_count = db.execute(
            "SELECT COUNT(*) FROM tasks WHERE status IN ('NEU', 'UNKLARE_ZUORDNUNG')"
        ).fetchone()[0]
    xls_vorhanden = _xls_path().exists()
    xls_dateiname = None
    if xls_vorhanden:
        name_file = _xls_name_path()
        if name_file.exists():
            xls_dateiname = name_file.read_text(encoding="utf-8").strip()

    members = []
    if xls_vorhanden and any(t["status"] == "UNKLARE_ZUORDNUNG" for t in tasks):
        members = load_members_from_xls(str(_xls_path()))

    return render_template(
        "index.html",
        runs=runs,
        tasks=tasks,
        offene_tasks_count=offene_tasks_count,
        xls_vorhanden=xls_vorhanden,
        xls_dateiname=xls_dateiname,
        members=members,
    )


@app.route("/tasks/<int:task_id>/zuordnen", methods=["POST"])
def task_zuordnen(task_id: int):
    pers_nr = request.form.get("pers_nr", "").strip()
    if not pers_nr:
        flash("Bitte ein Mitglied auswählen.", "error")
        return redirect(url_for("index"))

    from web.extractor import load_members_from_xls
    members = load_members_from_xls(str(_xls_path())) if _xls_path().exists() else []
    mitglied = next((m for m in members if m["pers_nr"] == pers_nr), None)
    if not mitglied:
        flash("Mitglied nicht gefunden.", "error")
        return redirect(url_for("index"))

    mitglied_name = f"{mitglied['vorname']} {mitglied['nachname']}"
    with closing(get_db()) as db:
        if db.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone() is None:
            abort(404)
        db.execute(
            "UPDATE tasks SET mitglied_nr = ?, mitglied_name = ?, status = 'NEU' WHERE id = ?",
            (pers_nr, mitglied_name, task_id),
        )
        db.commit()

    flash(f"Mitglied \"{mitglied_name}\" zugeordnet.", "success")
    return redirect(url_for("index"))


@app.route("/tasks/<int:task_id>/reanalyse", methods=["POST"])
def task_reanalyse(task_id: int):
    with closing(get_db()) as db:
        row = db.execute("SELECT raw_email FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            abort(404)
        if not row["raw_email"]:
            flash("Kein gespeichertes E-Mail für Re-Analyse vorhanden.", "error")
            return redirect(url_for("index"))

        import email as email_lib
        from web.extractor import extract_from_email, load_members_from_xls, _iter_dokument_parts
        cfg = get_settings()
        members = load_members_from_xls(str(_xls_path())) if _xls_path().exists() else []
        pruefungstypen_list = [t.strip() for t in (cfg.get("pruefungstypen") or "G25").split(",") if t.strip()]

        msg = email_lib.message_from_bytes(bytes(row["raw_email"]))
        extraction = extract_from_email(msg, pruefungstypen_list, members)
        anhang_count = sum(1 for _ in _iter_dokument_parts(msg))

        pruefungstyp = extraction["pruefungstyp"]
        faelligkeitsdatum = extraction["faelligkeitsdatum"]
        raw_text = extraction["raw_text"] or None
        matched_member = extraction["mitglied"]
        match_score = extraction["match_score"]

        from web.extractor import MATCH_THRESHOLD as threshold

        if members and match_score < threshold:
            new_status = "UNKLARE_ZUORDNUNG"
            mitglied_nr = None
            mitglied_name = None
        elif matched_member:
            new_status = "NEU"
            mitglied_nr = matched_member["pers_nr"]
            mitglied_name = f"{matched_member['vorname']} {matched_member['nachname']}"
        else:
            new_status = "NEU"
            mitglied_nr = None
            mitglied_name = None

        faelligkeitsdatum_str = faelligkeitsdatum.isoformat() if faelligkeitsdatum else None
        db.execute(
            """UPDATE tasks SET pruefungstyp = ?, faelligkeitsdatum = ?, raw_text = ?,
               mitglied_nr = ?, mitglied_name = ?, status = ?, anhang_count = ? WHERE id = ?""",
            (pruefungstyp, faelligkeitsdatum_str, raw_text, mitglied_nr, mitglied_name, new_status, anhang_count, task_id),
        )
        db.commit()

    flash("Re-Analyse abgeschlossen.", "success")
    return redirect(url_for("index"))


@app.route("/tasks/<int:task_id>/erledigt", methods=["POST"])
def task_erledigt(task_id: int):
    now = datetime.now().isoformat(timespec="seconds")
    with closing(get_db()) as db:
        row = db.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            abort(404)
        db.execute(
            "UPDATE tasks SET status = 'ERLEDIGT', erledigt_am = COALESCE(erledigt_am, ?) WHERE id = ?",
            (now, task_id),
        )
        db.commit()
    flash("Aufgabe als erledigt markiert.", "success")
    return redirect(url_for("index"))


def _task_dateiname(row, suffix: str = "", ext: str = "pdf") -> str:
    """Baut einen Dateinamen aus Empfangsdatum, Mitglied und Prüfungstyp."""
    datum = (row["empfangen_am"] or "")[:10]
    mitglied = re.sub(r"\s+", "-", (row["mitglied_name"] or "unbekannt").strip())
    typ = row["pruefungstyp"] or "Nachweis"
    basis = f"{datum}_{mitglied}_{typ}"
    if suffix:
        basis += f"_{suffix}"
    clean = re.sub(r"[^\w\-]", "_", basis.encode("ascii", "ignore").decode())
    return f"{clean}.{ext}"


@app.route("/tasks/<int:task_id>/pdf")
def task_pdf(task_id: int):
    with closing(get_db()) as db:
        row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        abort(404)
    if not row["raw_email"]:
        abort(404)

    from web.pdf_export import email_to_pdf
    pdf_bytes = email_to_pdf(bytes(row["raw_email"]))
    filename = _task_dateiname(row)

    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.route("/tasks/<int:task_id>/anhang/<int:index>")
def task_anhang(task_id: int, index: int):
    with closing(get_db()) as db:
        row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None or not row["raw_email"]:
        abort(404)

    import email as email_lib
    from web.extractor import _iter_dokument_parts
    msg = email_lib.message_from_bytes(bytes(row["raw_email"]))
    parts = list(_iter_dokument_parts(msg))
    if index >= len(parts):
        abort(404)

    ct, orig_filename, payload = parts[index]
    ext = orig_filename.rsplit(".", 1)[-1].lower() if "." in orig_filename else ("pdf" if ct == "application/pdf" else "jpg")
    disposition = f'inline; filename="{orig_filename}"' if orig_filename else "inline"
    return Response(payload, mimetype=ct, headers={"Content-Disposition": disposition})


@app.route("/tasks/<int:task_id>/anhang/<int:index>/download")
def task_anhang_download(task_id: int, index: int):
    with closing(get_db()) as db:
        row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None or not row["raw_email"]:
        abort(404)

    import email as email_lib
    from web.extractor import _iter_dokument_parts
    msg = email_lib.message_from_bytes(bytes(row["raw_email"]))
    parts = list(_iter_dokument_parts(msg))
    if index >= len(parts):
        abort(404)

    ct, orig_filename, payload = parts[index]
    ext = orig_filename.rsplit(".", 1)[-1].lower() if "." in orig_filename else ("pdf" if ct == "application/pdf" else "jpg")
    filename = _task_dateiname(row, suffix=f"Anhang-{index + 1}", ext=ext)
    return Response(payload, mimetype=ct, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


_VALID_SORTS = {"gesendet_am", "bestaetigt_am"}


@app.route("/email-pruefung")
def email_pruefung():
    status_filter = request.args.get("status", "")
    sort = request.args.get("sort", "")

    query = "SELECT * FROM email_verifikation"
    params: list = []
    if status_filter:
        query += " WHERE status = ?"
        params.append(status_filter)

    if sort in _VALID_SORTS:
        query += f" ORDER BY {sort} DESC"
    else:
        query += " ORDER BY nachname, vorname"

    with closing(get_db()) as db:
        mitglieder = db.execute(query, params).fetchall()

    return render_template(
        "email_pruefung.html",
        mitglieder=mitglieder,
        status_filter=status_filter,
        sort=sort,
    )


@app.route("/email-pruefung/senden", methods=["POST"])
def email_pruefung_senden():
    pers_nrs = request.form.getlist("pers_nr")
    if not pers_nrs:
        flash("Keine Mitglieder ausgewählt.", "error")
        return redirect(url_for("email_pruefung"))

    cfg = get_settings()
    smtp_config = _build_smtp_config(cfg)
    verifikation_betreff = cfg.get("verifikation_betreff") or _DEFAULT_VERIFIKATIONS_BETREFF
    verifikation_template = cfg.get("verifikation_template") or _DEFAULT_VERIFIKATIONS_TEMPLATE
    gesendet = 0

    with closing(get_db()) as db:
        for pers_nr in pers_nrs:
            row = db.execute(
                "SELECT vorname, nachname, email FROM email_verifikation WHERE pers_nr = ?",
                (pers_nr,),
            ).fetchone()
            if row is None:
                continue
            try:
                msg_id = send_verifikationsmail(
                    smtp_config=smtp_config,
                    to_addr=row["email"],
                    vorname=row["vorname"],
                    nachname=row["nachname"],
                    betreff=verifikation_betreff,
                    template=verifikation_template,
                )
                now = datetime.now().isoformat(timespec="seconds")
                db.execute(
                    """UPDATE email_verifikation
                       SET status='ausstehend', gesendet_am=?, verifikationsmail_message_id=?
                       WHERE pers_nr=?""",
                    (now, msg_id, pers_nr),
                )
                db.commit()
                gesendet += 1
            except Exception as e:
                logger.exception("Verifikationsmail an %s fehlgeschlagen", row["email"])
                flash(f"Fehler beim Senden an {row['email']}: {e}", "error")

    if gesendet > 0:
        flash(f"{gesendet} Verifikationsmail(s) versendet.", "success")
    return redirect(url_for("email_pruefung"))


@app.route("/archiv")
def archiv():
    with closing(get_db()) as db:
        tasks = db.execute(
            "SELECT * FROM tasks WHERE status = 'ERLEDIGT' ORDER BY erledigt_am DESC"
        ).fetchall()
    return render_template("archiv.html", tasks=tasks)


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

    members = load_members_from_xls(str(_xls_path()))
    if not members:
        logger.warning("XLS-Upload: load_members_from_xls lieferte keine Mitglieder – Sync übersprungen")
    _sync_email_verifikation(members)

    flash(f"Datei \"{dateiname}\" erfolgreich hochgeladen.", "success")
    return redirect(url_for("index"))


@app.route("/upload/loeschen", methods=["POST"])
def upload_loeschen():
    xls = _xls_path()
    existed = xls.exists()
    xls.unlink(missing_ok=True)
    _xls_name_path().unlink(missing_ok=True)
    if existed:
        flash("XLS-Datei gelöscht.", "success")
    return redirect(url_for("index"))


@app.route("/settings", methods=["GET"])
def settings_page():
    cfg = get_settings()
    return render_template("settings.html", cfg=cfg)


@app.route("/settings", methods=["POST"])
def settings_save():
    keys = [
        "smtp_host", "smtp_port", "smtp_user", "smtp_password", "smtp_from",
        "imap_host", "imap_port", "imap_user", "imap_password", "imap_poll_minuten",
        "kommandanten_cc", "zusammenfassung_an",
        "warn_days", "pruefungstypen", "archiv_tage",
        "email_betreff", "email_template",
        "zusammenfassung_betreff", "zusammenfassung_template",
        "verifikation_betreff", "verifikation_template",
        "imap_verifikation_ordner",
    ]
    data = {k: request.form.get(k, "") for k in keys}

    email_template = data.get("email_template", "")
    if email_template:
        try:
            email_template.format(vorname="X", nachname="X", pruefungen_liste="X")
        except (KeyError, ValueError, IndexError) as e:
            flash(
                f"Ungültiger Platzhalter im E-Mail-Template: {e}. "
                "Erlaubt sind: {vorname}, {nachname}, {pruefungen_liste}",
                "error",
            )
            return redirect(url_for("settings_page"))

    zusammenfassung_template = data.get("zusammenfassung_template", "")
    if zusammenfassung_template:
        try:
            zusammenfassung_template.format(
                datum="X", zusammenfassung="X",
                anzahl_personen=0, anzahl_abgelaufen=0, anzahl_warnung=0,
            )
        except (KeyError, ValueError, IndexError) as e:
            flash(
                f"Ungültiger Platzhalter im Zusammenfassungs-Template: {e}. "
                "Erlaubt sind: {datum}, {zusammenfassung}, {anzahl_personen}, {anzahl_abgelaufen}, {anzahl_warnung}",
                "error",
            )
            return redirect(url_for("settings_page"))

    verifikation_template = data.get("verifikation_template", "")
    if verifikation_template:
        try:
            verifikation_template.format(vorname="X", nachname="X")
        except (KeyError, ValueError, IndexError) as e:
            flash(
                f"Ungültiger Platzhalter im Verifikations-Template: {e}. "
                "Erlaubt sind: {vorname}, {nachname}",
                "error",
            )
            return redirect(url_for("settings_page"))

    imap_ordner = data.get("imap_verifikation_ordner", "")
    if imap_ordner and re.search(r'[\r\n"\\]', imap_ordner):
        flash("Ungültiger IMAP-Ordnername: keine Zeilenumbrüche oder Anführungszeichen erlaubt.", "error")
        return redirect(url_for("settings_page"))

    save_settings(data)

    from web import scheduler
    scheduler.reschedule(app)

    flash("Einstellungen gespeichert.", "success")
    return redirect(url_for("settings_page"))


@app.route("/settings/imap-poll", methods=["POST"])
def settings_imap_poll():
    from web.imap_poller import poll_inbox
    cfg = get_settings()
    if not cfg.get("imap_host", "").strip():
        flash("Bitte zuerst IMAP-Host in den Einstellungen eintragen.", "error")
        return redirect(url_for("settings_page"))
    try:
        new_count = poll_inbox(app)
        if new_count > 0:
            flash(f"{new_count} neue Nachricht(en) abgerufen.", "success")
        else:
            flash("Keine neuen Nachrichten im Posteingang.", "success")
    except Exception as e:
        flash(f"IMAP-Fehler – {type(e).__name__}: {e}", "error")
    return redirect(url_for("settings_page"))


@app.route("/settings/smtp-test", methods=["POST"])
def settings_smtp_test():
    cfg = get_settings()
    zusammenfassung_an = [e.strip() for e in (cfg.get("zusammenfassung_an") or "").split(",") if e.strip()]
    if not zusammenfassung_an:
        flash("Bitte zuerst eine Gesamtübersichts-Adresse unter \"Empfänger\" eintragen.", "error")
        return redirect(url_for("settings_page"))
    try:
        send_simple_mail(
            smtp_config=_build_smtp_config(cfg),
            to_addrs=zusammenfassung_an,
            subject="Test-Mail – Untersuchungs-Checker",
            body="Dies ist eine Test-Mail vom Untersuchungs-Checker.\n\nDie SMTP-Konfiguration funktioniert korrekt.",
        )
        flash(f"Test-Mail erfolgreich gesendet an {', '.join(zusammenfassung_an)}.", "success")
    except Exception as e:
        smtp_cfg = _build_smtp_config(cfg)
        host = smtp_cfg.get("host") or "localhost"
        port = smtp_cfg.get("port") or 587
        flash(f"SMTP-Fehler ({host}:{port}) – {type(e).__name__}: {e}", "error")
    return redirect(url_for("settings_page"))


@app.route("/run", methods=["POST"])
def run():
    if not _xls_path().exists():
        flash("Keine XLS-Datei vorhanden. Bitte zuerst hochladen.", "error")
        return redirect(url_for("index"))

    dry_run = request.form.get("dry_run") == "1"

    try:
        persons, emails = _do_run(dry_run=dry_run)

        rows = []
        for person in persons:
            for pruefung in person.pruefungen:
                rows.append({
                    "name": f"{person.vorname} {person.nachname}",
                    "beschreibung": pruefung.beschreibung,
                    "datum": pruefung.datum,
                    "status": pruefung.status,
                })
        rows.sort(key=lambda r: (0 if r["status"] == "abgelaufen" else 1, r["datum"]))

        abgelaufen_count = sum(1 for p in persons if p.hat_abgelaufene)
        warnung_count = sum(1 for p in persons if any(pr.status == "warnung" for pr in p.pruefungen))

        return render_template(
            "ergebnis.html",
            dry_run=dry_run,
            personen_count=len(persons),
            abgelaufen_count=abgelaufen_count,
            warnung_count=warnung_count,
            emails_count=emails,
            rows=rows,
        )
    except Exception as e:
        flash(f"Fehler beim Ausführen: {e}", "error")
        return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=os.getenv("FLASK_DEBUG", "0") == "1")
