import logging
import os
import re
import sqlite3
import threading
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from dotenv import load_dotenv

load_dotenv()

from dataclasses import replace as _dc_replace

from flask import Flask, Response, abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

from u_checker import check_examinations, send_notifications, send_summary
from u_checker.mailer import DEFAULT_EMAIL_BETREFF as _DEFAULT_EMAIL_BETREFF
from u_checker.mailer import DEFAULT_ZUSAMMENFASSUNG_BETREFF as _DEFAULT_ZUSAMMENFASSUNG_BETREFF
from u_checker.mailer import DEFAULT_ZUSAMMENFASSUNG_TEMPLATE as _DEFAULT_ZUSAMMENFASSUNG_TEMPLATE
from u_checker.mailer import DEFAULT_VERIFIKATIONS_BETREFF as _DEFAULT_VERIFIKATIONS_BETREFF
from u_checker.mailer import DEFAULT_VERIFIKATIONS_TEMPLATE as _DEFAULT_VERIFIKATIONS_TEMPLATE
from u_checker.mailer import send_simple_mail, send_task_antwort, send_verifikationsmail
from web.extractor import load_members_from_xls
from web.imap_poller import close_sent_connection, imap_move_to_nachweis, open_sent_connection

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
    "imap_nachweis_ordner": "Nachweise",
    "imap_sent_ordner": "INBOX.Sent",
    "imap_retention_tage": "90",
}


def _data_dir() -> Path:
    return Path(current_app.config["DATA_DIR"])


def _db_path() -> Path:
    return _data_dir() / "checker.db"


def _xls_path() -> Path:
    return _data_dir() / "latest.xls"


def _xls_name_path() -> Path:
    return _data_dir() / "latest_name.txt"


def _xls_upload_zeit_path() -> Path:
    return _data_dir() / "latest_upload_zeit.txt"


def get_db():
    # timeout: Bei gesperrter DB bis zu 30s warten statt sofort mit
    # "database is locked" zu scheitern (z.B. während ein IMAP-Poll schreibt).
    db = sqlite3.connect(_db_path(), timeout=30)
    db.row_factory = sqlite3.Row
    # WAL: Leser (z.B. die Index-Seite) blockieren Schreiber nicht mehr und
    # umgekehrt. busy_timeout deckt zusätzlich Schreiber-gegen-Schreiber ab.
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=30000")
    return db


def init_db():
    _data_dir().mkdir(parents=True, exist_ok=True)
    with closing(get_db()) as db:
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
                verifikationsmail_message_id TEXT,
                bestaetigung_herkunft TEXT
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS erinnerungen (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gesendet_am TEXT NOT NULL,
                pers_nr TEXT NOT NULL,
                mitglied_name TEXT NOT NULL,
                pruefungstyp TEXT NOT NULL,
                status TEXT NOT NULL,
                faelligkeitsdatum TEXT
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS task_nachrichten (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                richtung TEXT NOT NULL,
                zeitstempel TEXT NOT NULL,
                von_email TEXT,
                an_email TEXT,
                betreff TEXT,
                text TEXT,
                raw_email BLOB,
                message_id TEXT,
                in_reply_to TEXT,
                imap_uid TEXT
            )
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_task_nachrichten_task_id
            ON task_nachrichten (task_id)
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_task_nachrichten_message_id
            ON task_nachrichten (message_id)
        """)
        _migrate_tasks(db)
        _migrate_email_verifikation(db)
        _migrate_settings(db)
        db.commit()


_EMAIL_FORMAT_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _email_format_gueltig(email: str) -> bool:
    """Minimalcheck: faengt grobe Tippfehler ab (z.B. fehlendes '@'), keine RFC-5322-Validierung."""
    return bool(_EMAIL_FORMAT_RE.match(email or ""))


def _sync_email_verifikation(members: list) -> None:
    """Synchronisiert aktive Mitglieder in email_verifikation."""
    with closing(get_db()) as db:
        for m in members:
            existing = db.execute(
                "SELECT email, status FROM email_verifikation WHERE pers_nr = ?",
                (m["pers_nr"],),
            ).fetchone()
            gueltig = _email_format_gueltig(m["email"])
            if existing is None:
                status = "nie_geprueft" if gueltig else "ungueltige_adresse"
                db.execute(
                    "INSERT INTO email_verifikation (pers_nr, vorname, nachname, email, status) VALUES (?, ?, ?, ?, ?)",
                    (m["pers_nr"], m["vorname"], m["nachname"], m["email"], status),
                )
            elif existing["email"] != m["email"]:
                status = "nie_geprueft" if gueltig else "ungueltige_adresse"
                db.execute(
                    "UPDATE email_verifikation SET vorname=?, nachname=?, email=?, adresse_geaendert=1, status=? WHERE pers_nr=?",
                    (m["vorname"], m["nachname"], m["email"], status, m["pers_nr"]),
                )
            elif not gueltig and existing["status"] != "ungueltige_adresse":
                db.execute(
                    "UPDATE email_verifikation SET status='ungueltige_adresse' WHERE pers_nr=?",
                    (m["pers_nr"],),
                )
            elif gueltig and existing["status"] == "ungueltige_adresse":
                db.execute(
                    "UPDATE email_verifikation SET status='nie_geprueft' WHERE pers_nr=?",
                    (m["pers_nr"],),
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
        ("imap_uid", "TEXT"),
        ("kandidat_absender_nr", "TEXT"),
        ("kandidat_absender_name", "TEXT"),
        ("kandidat_dokument_nr", "TEXT"),
        ("kandidat_dokument_name", "TEXT"),
    ]
    for col, coltype in new_cols:
        if col not in existing:
            db.execute(f"ALTER TABLE tasks ADD COLUMN {col} {coltype}")


def _migrate_email_verifikation(db):
    """Fügt fehlende Spalten zur email_verifikation-Tabelle hinzu (für bestehende DBs)."""
    existing = {row[1] for row in db.execute("PRAGMA table_info(email_verifikation)").fetchall()}
    if "bestaetigung_herkunft" not in existing:
        db.execute("ALTER TABLE email_verifikation ADD COLUMN bestaetigung_herkunft TEXT")


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


def _sent_ordner(cfg: dict) -> str:
    return (cfg.get("imap_sent_ordner") or "INBOX.Sent").strip()


def _open_sent_connection(cfg: dict):
    """Baut best-effort eine IMAP-Verbindung für den Sent-Ordner-Nachbau auf (None bei Fehler)."""
    sent_ordner = _sent_ordner(cfg)
    try:
        return open_sent_connection(cfg, sent_ordner)
    except Exception:
        logger.warning("Sent-Ordner-Verbindung konnte nicht aufgebaut werden", exc_info=True)
        return None


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
        neu_count = db.execute(
            "SELECT COUNT(*) FROM tasks WHERE status = 'NEU'"
        ).fetchone()[0]
        unklare_count = db.execute(
            "SELECT COUNT(*) FROM tasks WHERE status = 'UNKLARE_ZUORDNUNG'"
        ).fetchone()[0]
        abweichend_count = db.execute(
            "SELECT COUNT(*) FROM tasks WHERE status = 'ABWEICHENDE_ZUORDNUNG'"
        ).fetchone()[0]
        email_ausstehend_count = db.execute(
            "SELECT COUNT(*) FROM email_verifikation WHERE status IN ('nie_geprueft', 'ausstehend', 'ungueltige_adresse', 're_verifikation_ausstehend')"
        ).fetchone()[0]

    xls_vorhanden = _xls_path().exists()
    xls_dateiname = None
    xls_upload_zeitpunkt = None
    if xls_vorhanden:
        name_file = _xls_name_path()
        if name_file.exists():
            xls_dateiname = name_file.read_text(encoding="utf-8").strip()
        zeit_file = _xls_upload_zeit_path()
        if zeit_file.exists():
            try:
                ts = datetime.fromisoformat(zeit_file.read_text(encoding="utf-8").strip())
                xls_upload_zeitpunkt = ts.strftime("%-d.%-m.%Y, %H:%M Uhr")
            except ValueError:
                pass

    return render_template(
        "index.html",
        neu_count=neu_count,
        unklare_count=unklare_count,
        abweichend_count=abweichend_count,
        email_ausstehend_count=email_ausstehend_count,
        xls_vorhanden=xls_vorhanden,
        xls_dateiname=xls_dateiname,
        xls_upload_zeitpunkt=xls_upload_zeitpunkt,
    )


def _nachweise_url() -> str:
    """Redirect-Ziel für /nachweise, erhält aktiven ?typ=-Filter aus dem POST-Formular."""
    typ = request.form.get("typ", "").strip()
    return url_for("nachweise", typ=typ) if typ else url_for("nachweise")


@app.route("/nachweise")
def nachweise():
    typ_filter = request.args.get("typ", "").strip()
    _base = "SELECT * FROM tasks WHERE status IN ('NEU', 'UNKLARE_ZUORDNUNG', 'ABWEICHENDE_ZUORDNUNG')"
    with closing(get_db()) as db:
        typen_rows = db.execute(
            "SELECT DISTINCT pruefungstyp FROM tasks"
            " WHERE status IN ('NEU', 'UNKLARE_ZUORDNUNG', 'ABWEICHENDE_ZUORDNUNG') AND pruefungstyp IS NOT NULL"
            " ORDER BY pruefungstyp"
        ).fetchall()
        if typ_filter:
            tasks = db.execute(
                _base + " AND pruefungstyp = ? ORDER BY empfangen_am DESC",
                (typ_filter,),
            ).fetchall()
        else:
            tasks = db.execute(_base + " ORDER BY empfangen_am DESC").fetchall()

    verfuegbare_typen = [r["pruefungstyp"] for r in typen_rows]

    members = []
    if _xls_path().exists() and any(t["status"] in ("UNKLARE_ZUORDNUNG", "ABWEICHENDE_ZUORDNUNG") for t in tasks):
        members = load_members_from_xls(str(_xls_path()))

    return render_template(
        "nachweise.html",
        tasks=tasks,
        members=members,
        verfuegbare_typen=verfuegbare_typen,
        aktiver_typ=typ_filter,
    )


@app.route("/tasks/<int:task_id>/zuordnen", methods=["POST"])
def task_zuordnen(task_id: int):
    pers_nr = request.form.get("pers_nr", "").strip()
    if not pers_nr:
        flash("Bitte ein Mitglied auswählen.", "error")
        return redirect(_nachweise_url())

    from web.extractor import load_members_from_xls
    members = load_members_from_xls(str(_xls_path())) if _xls_path().exists() else []
    mitglied = next((m for m in members if m["pers_nr"] == pers_nr), None)
    if not mitglied:
        flash("Mitglied nicht gefunden.", "error")
        return redirect(_nachweise_url())

    mitglied_name = f"{mitglied['vorname']} {mitglied['nachname']}"
    with closing(get_db()) as db:
        if db.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone() is None:
            abort(404)
        db.execute(
            """UPDATE tasks SET mitglied_nr = ?, mitglied_name = ?, status = 'NEU',
               kandidat_absender_nr = NULL, kandidat_absender_name = NULL,
               kandidat_dokument_nr = NULL, kandidat_dokument_name = NULL WHERE id = ?""",
            (pers_nr, mitglied_name, task_id),
        )
        db.commit()

    flash(f"Mitglied \"{mitglied_name}\" zugeordnet.", "success")
    return redirect(_nachweise_url())


@app.route("/tasks/<int:task_id>/reanalyse", methods=["POST"])
def task_reanalyse(task_id: int):
    with closing(get_db()) as db:
        row = db.execute("SELECT raw_email FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            abort(404)
        if not row["raw_email"]:
            flash("Kein gespeichertes E-Mail für Re-Analyse vorhanden.", "error")
            return redirect(_nachweise_url())

        import email as email_lib
        from web.extractor import bestimme_zuordnung, extract_from_email, load_members_from_xls, _iter_dokument_parts
        cfg = get_settings()
        members = load_members_from_xls(str(_xls_path())) if _xls_path().exists() else []
        pruefungstypen_list = [t.strip() for t in (cfg.get("pruefungstypen") or "G25").split(",") if t.strip()]

        msg = email_lib.message_from_bytes(bytes(row["raw_email"]))
        extraction = extract_from_email(msg, pruefungstypen_list, members)
        anhang_count = sum(1 for _ in _iter_dokument_parts(msg))

        pruefungstyp = extraction["pruefungstyp"]
        faelligkeitsdatum = extraction["faelligkeitsdatum"]
        raw_text = extraction["raw_text"] or None

        zuordnung = bestimme_zuordnung(extraction, members)
        new_status = zuordnung["status"]
        mitglied_nr = zuordnung["mitglied_nr"]
        mitglied_name = zuordnung["mitglied_name"]

        faelligkeitsdatum_str = faelligkeitsdatum.isoformat() if faelligkeitsdatum else None
        db.execute(
            """UPDATE tasks SET pruefungstyp = ?, faelligkeitsdatum = ?, raw_text = ?,
               mitglied_nr = ?, mitglied_name = ?, status = ?, anhang_count = ?,
               kandidat_absender_nr = ?, kandidat_absender_name = ?,
               kandidat_dokument_nr = ?, kandidat_dokument_name = ? WHERE id = ?""",
            (
                pruefungstyp, faelligkeitsdatum_str, raw_text, mitglied_nr, mitglied_name,
                new_status, anhang_count,
                zuordnung["kandidat_absender_nr"], zuordnung["kandidat_absender_name"],
                zuordnung["kandidat_dokument_nr"], zuordnung["kandidat_dokument_name"],
                task_id,
            ),
        )
        db.commit()

    flash("Re-Analyse abgeschlossen.", "success")
    return redirect(_nachweise_url())


@app.route("/tasks/<int:task_id>/loeschen", methods=["POST"])
def task_loeschen(task_id: int):
    with closing(get_db()) as db:
        row = db.execute(
            "SELECT id, imap_uid FROM tasks WHERE id = ? AND status IN ('NEU', 'UNKLARE_ZUORDNUNG', 'ABWEICHENDE_ZUORDNUNG')",
            (task_id,),
        ).fetchone()
        if row is None:
            abort(404)
        imap_uid = row["imap_uid"]
        db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        db.commit()

    if imap_uid:
        try:
            from web.imap_poller import imap_delete_from_inbox
            imap_delete_from_inbox(get_settings(), imap_uid)
        except Exception:
            logger.exception("IMAP-Löschen für Task %d fehlgeschlagen", task_id)

    flash("Aufgabe gelöscht.", "success")
    return redirect(_nachweise_url())


@app.route("/tasks/<int:task_id>/wiederoeffnen", methods=["POST"])
def task_wiederoeffnen(task_id: int):
    with closing(get_db()) as db:
        row = db.execute(
            "SELECT mitglied_nr, kandidat_absender_nr, imap_uid, message_id FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if row is None:
            abort(404)
        imap_uid = row["imap_uid"]
        message_id = row["message_id"]
        if row["mitglied_nr"]:
            new_status = "NEU"
        elif row["kandidat_absender_nr"]:
            new_status = "ABWEICHENDE_ZUORDNUNG"
        else:
            new_status = "UNKLARE_ZUORDNUNG"
        db.execute(
            "UPDATE tasks SET status = ?, erledigt_am = NULL, imap_uid = NULL WHERE id = ?",
            (new_status, task_id),
        )
        db.commit()

    if imap_uid and message_id and not message_id.startswith("hash:"):
        try:
            cfg = get_settings()
            nachweis_ordner = cfg.get("imap_nachweis_ordner", "Nachweise").strip()
            from web.imap_poller import imap_move_to_inbox
            imap_move_to_inbox(cfg, message_id, nachweis_ordner)
        except Exception:
            logger.exception("IMAP-Move zurück in INBOX für Task %d fehlgeschlagen", task_id)

    flash("Aufgabe wieder geöffnet.", "success")
    return redirect(url_for("nachweise"))


@app.route("/tasks/<int:task_id>/erledigt", methods=["POST"])
def task_erledigt(task_id: int):
    now = datetime.now().isoformat(timespec="seconds")
    with closing(get_db()) as db:
        row = db.execute("SELECT id, imap_uid FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            abort(404)
        imap_uid = row["imap_uid"]
        thread_uids = [
            r["imap_uid"] for r in db.execute(
                """SELECT imap_uid FROM task_nachrichten
                   WHERE task_id = ? AND richtung = 'eingehend' AND imap_uid IS NOT NULL""",
                (task_id,),
            ).fetchall()
        ]
        db.execute(
            "UPDATE tasks SET status = 'ERLEDIGT', erledigt_am = COALESCE(erledigt_am, ?) WHERE id = ?",
            (now, task_id),
        )
        db.commit()

    alle_uids = ([imap_uid] if imap_uid else []) + thread_uids
    if alle_uids:
        cfg = get_settings()
        nachweis_ordner = cfg.get("imap_nachweis_ordner", "Nachweise").strip()
        for uid in alle_uids:
            try:
                imap_move_to_nachweis(cfg, uid, nachweis_ordner)
            except Exception:
                logger.exception("IMAP-Move für Task %d (UID %s) fehlgeschlagen", task_id, uid)

    flash("Aufgabe als erledigt markiert.", "success")
    return redirect(_nachweise_url())


_ANTWORT_BETREFF_RE = re.compile(r"^re:\s*", re.IGNORECASE)


def _re_betreff(betreff: Optional[str]) -> str:
    """Baut den Re:-Betreff für eine Antwort, ohne ein bereits vorhandenes 'Re:' zu verdoppeln."""
    basis = (betreff or "").strip()
    if _ANTWORT_BETREFF_RE.match(basis):
        return basis
    return f"Re: {basis}" if basis else "Re:"


@app.route("/tasks/<int:task_id>/antworten", methods=["POST"])
def task_antworten(task_id: int):
    an_email = request.form.get("an_email", "").strip()
    text = request.form.get("text", "").strip()

    with closing(get_db()) as db:
        row = db.execute("SELECT betreff, message_id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            abort(404)

        if not an_email or not _email_format_gueltig(an_email):
            flash("Bitte eine gültige Empfänger-Adresse angeben.", "error")
            return redirect(_nachweise_url())
        if not text:
            flash("Bitte einen Antworttext eingeben.", "error")
            return redirect(_nachweise_url())

        betreff = _re_betreff(row["betreff"])
        original_message_id = row["message_id"]
        in_reply_to = original_message_id if original_message_id and not original_message_id.startswith("hash:") else None

        cfg = get_settings()
        smtp_config = _build_smtp_config(cfg)
        sent_ordner = _sent_ordner(cfg)
        imap_conn = _open_sent_connection(cfg)
        try:
            neue_message_id = send_task_antwort(
                smtp_config=smtp_config,
                to_addr=an_email,
                betreff=betreff,
                text=text,
                in_reply_to=in_reply_to,
                imap_connection=imap_conn,
                sent_ordner=sent_ordner,
            )
        except Exception as e:
            logger.exception("Antwort auf Task %d fehlgeschlagen", task_id)
            flash(f"Fehler beim Senden der Antwort: {e}", "error")
            return redirect(_nachweise_url())
        finally:
            close_sent_connection(imap_conn)

        now = datetime.now().isoformat(timespec="seconds")
        db.execute(
            """INSERT INTO task_nachrichten
                   (task_id, richtung, zeitstempel, an_email, betreff, text, message_id)
               VALUES (?, 'ausgehend', ?, ?, ?, ?, ?)""",
            (task_id, now, an_email, betreff, text, neue_message_id),
        )
        db.commit()

    flash("Antwort gesendet.", "success")
    return redirect(_nachweise_url())


@app.route("/tasks/<int:task_id>/thread")
def task_thread(task_id: int):
    with closing(get_db()) as db:
        row = db.execute("SELECT von_email, betreff FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            abort(404)
        nachrichten = db.execute(
            """SELECT richtung, zeitstempel, von_email, an_email, betreff, text
               FROM task_nachrichten WHERE task_id = ? ORDER BY zeitstempel ASC, id ASC""",
            (task_id,),
        ).fetchall()

    return jsonify({
        "empfaenger_vorschlag": row["von_email"] or "",
        "betreff": _re_betreff(row["betreff"]),
        "nachrichten": [
            {
                "richtung": n["richtung"],
                "zeitstempel": n["zeitstempel"],
                "von_email": n["von_email"],
                "an_email": n["an_email"],
                "betreff": n["betreff"],
                "text": n["text"],
            }
            for n in nachrichten
        ],
    })


_UMLAUT_TRANSLITERATION = {
    "ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss",
}


def _transliteriere_umlaute(text: str) -> str:
    """Ersetzt deutsche Umlaute/ß durch ihre ASCII-Umschreibung (ö→oe, ß→ss, ...)."""
    for umlaut, ersatz in _UMLAUT_TRANSLITERATION.items():
        text = text.replace(umlaut, ersatz)
    return text


def _task_dateiname_echt(row, suffix: str = "", ext: str = "pdf") -> str:
    """Wie _task_dateiname(), aber ohne Umlaut-Transliteration/ASCII-Reduktion – für filename*=UTF-8''."""
    datum = (row["empfangen_am"] or "")[:10]
    mitglied = re.sub(r"\s+", "-", (row["mitglied_name"] or "unbekannt").strip())
    typ = row["pruefungstyp"] or "Nachweis"
    basis = f"{datum}_{mitglied}_{typ}"
    if suffix:
        basis += f"_{suffix}"
    return f"{basis}.{ext}"


def _task_dateiname(row, suffix: str = "", ext: str = "pdf") -> str:
    """Baut einen Dateinamen aus Empfangsdatum, Mitglied und Prüfungstyp (ASCII, Umlaute transliteriert)."""
    echt = _task_dateiname_echt(row, suffix=suffix, ext=ext)
    clean = re.sub(r"[^\w.\-]", "_", _transliteriere_umlaute(echt).encode("ascii", "ignore").decode())
    return clean or f"unbekannt.{ext}"


def _content_disposition(disposition: str, ascii_fallback: str, echter_name: Optional[str] = None) -> str:
    """Baut einen Content-Disposition-Header mit ASCII-Fallback und RFC-5987-UTF-8-Namen.

    `ascii_fallback` wird als filename="..." für ältere Clients gesetzt, `echter_name`
    (falls abweichend, z.B. mit Umlauten) zusätzlich als filename*=UTF-8''... für moderne Clients.
    """
    safe_fallback = ascii_fallback.replace("\\", "").replace('"', "")
    header = f'{disposition}; filename="{safe_fallback}"'
    if echter_name and echter_name != ascii_fallback:
        header += f"; filename*=UTF-8''{quote(echter_name, safe='')}"
    return header


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
    filename_echt = _task_dateiname_echt(row)

    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": _content_disposition("attachment", filename, filename_echt)},
    )


def _lade_task_und_anhang_parts(task_id: int):
    """Lädt einen Task samt geparsten Anhang-Teilen aus raw_email; bricht mit 404 ab, wenn Task oder raw_email fehlt."""
    with closing(get_db()) as db:
        row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None or not row["raw_email"]:
        abort(404)

    import email as email_lib
    from web.extractor import _iter_dokument_parts
    msg = email_lib.message_from_bytes(bytes(row["raw_email"]))
    return row, list(_iter_dokument_parts(msg))


@app.route("/tasks/<int:task_id>/anhang/<int:index>")
def task_anhang(task_id: int, index: int):
    row, parts = _lade_task_und_anhang_parts(task_id)
    if index >= len(parts):
        abort(404)

    ct, orig_filename, payload = parts[index]
    ext = orig_filename.rsplit(".", 1)[-1].lower() if "." in orig_filename else ("pdf" if ct == "application/pdf" else "jpg")
    if orig_filename:
        ascii_fallback = re.sub(r"[^\w.\-]", "_", orig_filename.encode("ascii", "ignore").decode())
        disposition = _content_disposition("inline", ascii_fallback or f"Anhang.{ext}", orig_filename)
    else:
        disposition = "inline"
    return Response(payload, mimetype=ct, headers={"Content-Disposition": disposition})


@app.route("/tasks/<int:task_id>/anhang/<int:index>/download")
def task_anhang_download(task_id: int, index: int):
    row, parts = _lade_task_und_anhang_parts(task_id)
    if index >= len(parts):
        abort(404)

    ct, orig_filename, payload = parts[index]
    ext = orig_filename.rsplit(".", 1)[-1].lower() if "." in orig_filename else ("pdf" if ct == "application/pdf" else "jpg")
    filename = _task_dateiname(row, suffix=f"Anhang-{index + 1}", ext=ext)
    filename_echt = _task_dateiname_echt(row, suffix=f"Anhang-{index + 1}", ext=ext)
    return Response(payload, mimetype=ct, headers={"Content-Disposition": _content_disposition("attachment", filename, filename_echt)})


@app.route("/tasks/<int:task_id>/anhaenge")
def task_anhaenge(task_id: int):
    _row, parts = _lade_task_und_anhang_parts(task_id)
    return jsonify([
        {"index": i, "filename": filename, "content_type": ct}
        for i, (ct, filename, _payload) in enumerate(parts)
    ])


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
    sent_ordner = _sent_ordner(cfg)
    verifikation_betreff = cfg.get("verifikation_betreff") or _DEFAULT_VERIFIKATIONS_BETREFF
    verifikation_template = cfg.get("verifikation_template") or _DEFAULT_VERIFIKATIONS_TEMPLATE
    gesendet = 0

    with closing(get_db()) as db:
        for pers_nr in pers_nrs:
            row = db.execute(
                "SELECT vorname, nachname, email, status FROM email_verifikation WHERE pers_nr = ?",
                (pers_nr,),
            ).fetchone()
            if row is None:
                continue
            if not _email_format_gueltig(row["email"]):
                db.execute(
                    "UPDATE email_verifikation SET status='ungueltige_adresse' WHERE pers_nr=?",
                    (pers_nr,),
                )
                db.commit()
                flash(f"Ungültige E-Mail-Adresse übersprungen: {row['email']}", "error")
                continue
            imap_conn = _open_sent_connection(cfg)
            try:
                msg_id = send_verifikationsmail(
                    smtp_config=smtp_config,
                    to_addr=row["email"],
                    vorname=row["vorname"],
                    nachname=row["nachname"],
                    betreff=verifikation_betreff,
                    template=verifikation_template,
                    imap_connection=imap_conn,
                    sent_ordner=sent_ordner,
                )
                now = datetime.now().isoformat(timespec="seconds")
                war_bestaetigt = row["status"] in ("bestaetigt", "re_verifikation_ausstehend")
                neuer_status = "re_verifikation_ausstehend" if war_bestaetigt else "ausstehend"
                db.execute(
                    """UPDATE email_verifikation
                       SET status=?, gesendet_am=?, verifikationsmail_message_id=?
                       WHERE pers_nr=?""",
                    (neuer_status, now, msg_id, pers_nr),
                )
                db.commit()
                gesendet += 1
            except Exception as e:
                logger.exception("Verifikationsmail an %s fehlgeschlagen", row["email"])
                flash(f"Fehler beim Senden an {row['email']}: {e}", "error")
            finally:
                close_sent_connection(imap_conn)

    if gesendet > 0:
        flash(f"{gesendet} Verifikationsmail(s) versendet.", "success")
    return redirect(url_for("email_pruefung"))


_MANUELL_BESTAETIGBAR = {"ausstehend", "re_verifikation_ausstehend"}


@app.route("/email-pruefung/<pers_nr>/manuell-bestaetigen", methods=["POST"])
def email_pruefung_manuell_bestaetigen(pers_nr: str):
    with closing(get_db()) as db:
        row = db.execute(
            "SELECT status FROM email_verifikation WHERE pers_nr = ?", (pers_nr,)
        ).fetchone()
        if row is None:
            abort(404)
        if row["status"] not in _MANUELL_BESTAETIGBAR:
            flash("Manuelles Bestätigen ist für diesen Status nicht möglich.", "error")
            return redirect(url_for("email_pruefung"))

        now = datetime.now().isoformat(timespec="seconds")
        db.execute(
            """UPDATE email_verifikation
               SET status='bestaetigt', bestaetigt_am=?, bestaetigung_herkunft='manuell', adresse_geaendert=0
               WHERE pers_nr=?""",
            (now, pers_nr),
        )
        db.commit()

    flash("Mitglied wurde manuell bestätigt.", "success")
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
    _xls_upload_zeit_path().write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")

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
        "imap_nachweis_ordner",
        "imap_sent_ordner",
        "imap_retention_tage",
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

    imap_nachweis = data.get("imap_nachweis_ordner", "")
    if imap_nachweis and re.search(r'[\r\n"\\]', imap_nachweis):
        flash("Ungültiger IMAP-Nachweis-Ordnername: keine Zeilenumbrüche oder Anführungszeichen erlaubt.", "error")
        return redirect(url_for("settings_page"))

    imap_sent = data.get("imap_sent_ordner", "")
    if imap_sent and re.search(r'[\r\n"\\]', imap_sent):
        flash("Ungültiger IMAP-Sent-Ordnername: keine Zeilenumbrüche oder Anführungszeichen erlaubt.", "error")
        return redirect(url_for("settings_page"))

    save_settings(data)

    from web import scheduler
    scheduler.reschedule(app)

    flash("Einstellungen gespeichert.", "success")
    return redirect(url_for("settings_page"))


# Erlaubt jeweils nur einen manuellen Poll gleichzeitig. Verhindert, dass
# mehrfaches Klicken auf "Posteingang abrufen" parallele, langlaufende
# IMAP-Läufe stapelt, die sich gegenseitig und die DB blockieren.
_imap_poll_lock = threading.Lock()


def _do_imap_poll() -> None:
    """Führt einen manuellen IMAP-Poll aus und setzt die Flash-Message. Gemeinsame Logik für
    /imap-poll und /settings/imap-poll, die sich nur im abschließenden Redirect unterscheiden."""
    from web.imap_poller import poll_inbox
    cfg = get_settings()
    if not cfg.get("imap_host", "").strip():
        flash("Bitte zuerst IMAP-Host in den Einstellungen eintragen.", "error")
        return
    if not _imap_poll_lock.acquire(blocking=False):
        flash("Es läuft bereits ein Abruf – bitte einen Moment warten.", "error")
        return
    try:
        new_count = poll_inbox(app)
        if new_count > 0:
            flash(f"{new_count} neue Nachricht(en) abgerufen.", "success")
        else:
            flash("Keine neuen Nachrichten im Posteingang.", "success")
    except Exception as e:
        flash(f"IMAP-Fehler – {type(e).__name__}: {e}", "error")
    finally:
        _imap_poll_lock.release()


@app.route("/imap-poll", methods=["POST"])
def imap_poll():
    _do_imap_poll()
    return redirect(url_for("index"))


@app.route("/settings/imap-poll", methods=["POST"])
def settings_imap_poll():
    _do_imap_poll()
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


def _analyse_faelligkeiten(xls_path: str, warn_days: int, pruefungstypen: list) -> tuple:
    """Returns (personen_mit_email, personen_ohne_email).

    personen_mit_email: list of Person (from check_examinations)
    personen_ohne_email: list of dicts {name, pruefungen: [{typ, status, datum (ISO str)}]}
    """
    import xlrd
    from u_checker.checker import (
        COL_TYP, COL_OK, COL_EI_ANZEIGEN, COL_DATUM, COL_GUELTIG_BIS,
        COL_PERS_NR, COL_VORNAME, COL_NACHNAME, COL_EMAIL, _xl_to_date,
    )

    heute = date.today()
    personen = check_examinations(xls_path, warn_days=warn_days, pruefungstypen=pruefungstypen)

    wb = xlrd.open_workbook(xls_path)
    sh = wb.sheets()[0]

    entries: dict = {}
    for r in range(1, sh.nrows):
        row = sh.row_values(r)
        typ = str(row[COL_TYP]).strip()
        if typ not in pruefungstypen:
            continue
        if str(row[COL_OK]).strip() == "Ja":
            continue
        if str(row[COL_EI_ANZEIGEN]).strip() == "Nein":
            continue
        if str(row[COL_EMAIL]).strip():
            continue  # already included via check_examinations

        relevant = _xl_to_date(wb, row[COL_GUELTIG_BIS]) or _xl_to_date(wb, row[COL_DATUM])
        if not relevant:
            continue

        pers_nr = str(row[COL_PERS_NR]).strip()
        name = f"{str(row[COL_VORNAME]).strip()} {str(row[COL_NACHNAME]).strip()}"
        entries.setdefault(pers_nr, {"name": name, "typen": {}})
        entries[pers_nr]["typen"].setdefault(typ, []).append({"datum": relevant})

    # Status klassifizierung NACH der Dedup – exakt wie in check_examinations
    ohne_email = []
    for info in entries.values():
        pruefungen_info = []
        for typ, kandidaten in info["typen"].items():
            latest = max(kandidaten, key=lambda e: e["datum"])
            datum = latest["datum"]
            if datum <= heute:
                status = "abgelaufen"
            elif datum <= heute + timedelta(days=warn_days):
                status = "warnung"
            else:
                continue  # neuester Eintrag liegt außerhalb Warnfrist → nicht fällig
            pruefungen_info.append({
                "typ": typ,
                "status": status,
                "datum": datum.isoformat(),
            })
        if pruefungen_info:
            ohne_email.append({"name": info["name"], "pruefungen": pruefungen_info})

    return personen, ohne_email


def _personen_zu_vorschau(personen: list) -> list:
    """Converts Person list to view model dicts, sorted for preview table."""
    rows = []
    for p in personen:
        pruefungen = [{"typ": pr.typ, "status": pr.status, "datum": pr.datum.isoformat()} for pr in p.pruefungen]
        naechste_faelligkeit = min((pr.datum for pr in p.pruefungen), default=None)
        rows.append({
            "pers_nr": p.pers_nr,
            "name": f"{p.vorname} {p.nachname}",
            "pruefungen": pruefungen,
            "naechste_faelligkeit": naechste_faelligkeit.isoformat() if naechste_faelligkeit else "",
            "cc_flag": p.hat_abgelaufene,
        })
    rows.sort(key=lambda r: (
        0 if r["cc_flag"] else 1,
        r["naechste_faelligkeit"] or "9999-12-31",
    ))
    return rows


def _get_verlauf() -> list:
    with closing(get_db()) as db:
        return db.execute(
            "SELECT * FROM erinnerungen ORDER BY gesendet_am DESC, id DESC"
        ).fetchall()


def _current_xls_dateiname() -> Optional[str]:
    name_file = _xls_name_path()
    return name_file.read_text(encoding="utf-8").strip() if name_file.exists() else None


@app.route("/faelligkeiten")
def faelligkeiten():
    xls_vorhanden = _xls_path().exists()
    return render_template(
        "faelligkeiten.html",
        xls_vorhanden=xls_vorhanden,
        vorschau=None,
        ohne_email=[],
        verlauf=_get_verlauf(),
        filter_typen={},
    )


@app.route("/faelligkeiten/analyse", methods=["POST"])
def faelligkeiten_analyse():
    if not _xls_path().exists():
        flash("Keine XLS-Datei vorhanden. Bitte zuerst hochladen.", "error")
        return redirect(url_for("faelligkeiten"))

    cfg = get_settings()
    warn_days = _safe_int(cfg.get("warn_days"), 90)
    pruefungstypen = [t.strip() for t in (cfg.get("pruefungstypen") or "G25").split(",") if t.strip()]

    try:
        personen, ohne_email = _analyse_faelligkeiten(str(_xls_path()), warn_days, pruefungstypen)
    except Exception as e:
        flash(f"Fehler beim Analysieren: {e}", "error")
        return redirect(url_for("faelligkeiten"))

    vorschau = _personen_zu_vorschau(personen)

    filter_typen: dict = {}
    for row in vorschau:
        seen: set = set()
        for pr in row["pruefungen"]:
            if pr["typ"] not in seen:
                filter_typen[pr["typ"]] = filter_typen.get(pr["typ"], 0) + 1
                seen.add(pr["typ"])

    return render_template(
        "faelligkeiten.html",
        xls_vorhanden=True,
        vorschau=vorschau,
        ohne_email=ohne_email,
        verlauf=_get_verlauf(),
        xls_dateiname=_current_xls_dateiname(),
        filter_typen=filter_typen,
    )


@app.route("/faelligkeiten/senden", methods=["POST"])
def faelligkeiten_senden():
    pers_nrs = set(request.form.getlist("pers_nr"))
    if not pers_nrs:
        flash("Keine Personen ausgewählt.", "error")
        return redirect(url_for("faelligkeiten"))

    if not _xls_path().exists():
        flash("Keine XLS-Datei vorhanden. Bitte zuerst hochladen.", "error")
        return redirect(url_for("faelligkeiten"))

    # Schutz gegen TOCTOU: XLS-Datei muss dieselbe sein wie beim Analyse-Schritt.
    erwarteter_name = request.form.get("xls_dateiname", "")
    aktueller_name = _current_xls_dateiname() or ""
    if erwarteter_name and aktueller_name != erwarteter_name:
        flash("Die XLS-Datei wurde seit der letzten Analyse ausgetauscht. Bitte erneut analysieren.", "error")
        return redirect(url_for("faelligkeiten"))

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

    try:
        personen, _ = _analyse_faelligkeiten(str(_xls_path()), warn_days, pruefungstypen)
    except Exception as e:
        flash(f"Fehler beim Analysieren: {e}", "error")
        return redirect(url_for("faelligkeiten"))

    ausgewaehlte = [p for p in personen if p.pers_nr in pers_nrs]
    if not ausgewaehlte:
        flash("Keine der ausgewählten Personen hat offene Fälligkeiten.", "error")
        return redirect(url_for("faelligkeiten"))

    filter_typ = request.form.get("filter_typ", "").strip()
    if filter_typ:
        gefiltert = []
        for person in ausgewaehlte:
            passende = [p for p in person.pruefungen if p.typ == filter_typ]
            if passende:
                gefiltert.append(_dc_replace(
                    person,
                    pruefungen=passende,
                    cc_force=person.hat_abgelaufene,
                ))
        ausgewaehlte = gefiltert
        if not ausgewaehlte:
            flash("Keine Personen mit dem gewählten Prüfungstyp unter den Ausgewählten.", "error")
            return redirect(url_for("faelligkeiten"))

    sent_ordner = _sent_ordner(cfg)
    imap_conn = _open_sent_connection(cfg)
    try:
        emails_gesendet = send_notifications(
            ausgewaehlte,
            dry_run=False,
            smtp_config=smtp_config,
            kommandanten_cc=kommandanten_cc,
            email_betreff=email_betreff,
            email_template=email_template,
            imap_connection=imap_conn,
            sent_ordner=sent_ordner,
        )
        send_summary(
            ausgewaehlte,
            dry_run=False,
            smtp_config=smtp_config,
            zusammenfassung_an=zusammenfassung_an,
            zusammenfassung_betreff=zusammenfassung_betreff,
            zusammenfassung_template=zusammenfassung_template,
            imap_connection=imap_conn,
            sent_ordner=sent_ordner,
        )

        gesendet_am = datetime.now().isoformat(timespec="seconds")
        with closing(get_db()) as db:
            for person in ausgewaehlte:
                for pruefung in person.pruefungen:
                    db.execute(
                        """INSERT INTO erinnerungen
                           (gesendet_am, pers_nr, mitglied_name, pruefungstyp, status, faelligkeitsdatum)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            gesendet_am,
                            person.pers_nr,
                            f"{person.vorname} {person.nachname}",
                            pruefung.typ,
                            pruefung.status,
                            pruefung.datum.isoformat(),
                        ),
                    )
            db.commit()
    except Exception as e:
        logger.exception("Fehler beim Versenden")
        flash(f"Fehler beim Versenden: {e}", "error")
        return redirect(url_for("faelligkeiten"))
    finally:
        close_sent_connection(imap_conn)

    flash(f"{emails_gesendet} E-Mail(s) versendet.", "success")
    return redirect(url_for("faelligkeiten"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=os.getenv("FLASK_DEBUG", "0") == "1")
