from __future__ import annotations

import email
import email.header
import email.utils
import hashlib
import imaplib
import logging
import re
import smtplib
from contextlib import closing
from datetime import datetime, timedelta
from email.mime.text import MIMEText

from web.extractor import (
    bestimme_zuordnung,
    collect_body_text_from_email,
    extract_from_email,
    load_members_from_xls,
    _iter_dokument_parts,
)

logger = logging.getLogger(__name__)


def _extract_uid(fetch_response_part: bytes) -> str | None:
    """Extracts the IMAP UID from a FETCH response fragment like b'1 (UID 42 RFC822 {100})'."""
    m = re.search(rb'UID (\d+)', fetch_response_part)
    return m.group(1).decode() if m else None


def _imap_connect(cfg: dict):
    """Opens and returns an authenticated IMAP4_SSL connection, or None if not configured."""
    host = cfg.get("imap_host", "").strip()
    user = cfg.get("imap_user", "").strip()
    password = cfg.get("imap_password", "").strip()
    if not host or not user or not password:
        return None
    port = 993
    try:
        port = int(cfg.get("imap_port") or 993)
    except (TypeError, ValueError):
        pass
    imap = imaplib.IMAP4_SSL(host, port)
    try:
        imap.login(user, password)
    except Exception:
        try:
            imap.logout()
        except Exception:
            pass
        raise
    return imap


def imap_move_to_nachweis(cfg: dict, imap_uid: str, nachweis_ordner: str) -> None:
    """Verschiebt Email von INBOX in den Nachweis-Ordner via gespeicherter UID (best-effort)."""
    imap = _imap_connect(cfg)
    if imap is None:
        return
    try:
        status, _ = imap.select("INBOX")
        if status != "OK":
            logger.warning("INBOX konnte nicht selektiert werden (Status: %s)", status)
            return
        _ensure_imap_ordner(imap, nachweis_ordner)
        uid_bytes = imap_uid.encode()
        typ, _ = imap.uid("COPY", uid_bytes, nachweis_ordner)
        if typ == "OK":
            imap.uid("STORE", uid_bytes, "+FLAGS", "\\Deleted")
            imap.expunge()
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def imap_move_to_inbox(cfg: dict, message_id: str, nachweis_ordner: str) -> None:
    """Sucht Email im Nachweis-Ordner per Message-ID und verschiebt sie zurück in INBOX (best-effort)."""
    imap = _imap_connect(cfg)
    if imap is None:
        return
    try:
        status, _ = imap.select(nachweis_ordner)
        if status != "OK":
            logger.warning("IMAP-Ordner %r nicht gefunden beim Wiedereröffnen", nachweis_ordner)
            return
        _, data = imap.uid("SEARCH", None, "HEADER", "Message-ID", message_id)
        uids = data[0].split() if data[0] else []
        if not uids:
            logger.warning("Email mit Message-ID %r nicht in %r gefunden", message_id, nachweis_ordner)
            return
        uid = uids[0]
        typ, _ = imap.uid("COPY", uid, "INBOX")
        if typ == "OK":
            imap.uid("STORE", uid, "+FLAGS", "\\Deleted")
            imap.expunge()
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def imap_delete_from_inbox(cfg: dict, imap_uid: str) -> None:
    """Löscht Email aus INBOX via gespeicherter UID (best-effort)."""
    imap = _imap_connect(cfg)
    if imap is None:
        return
    try:
        status, _ = imap.select("INBOX")
        if status != "OK":
            logger.warning("INBOX konnte nicht selektiert werden (Status: %s)", status)
            return
        uid_bytes = imap_uid.encode()
        imap.uid("STORE", uid_bytes, "+FLAGS", "\\Deleted")
        imap.expunge()
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def open_sent_connection(cfg: dict, sent_ordner: str):
    """Baut eine IMAP-Verbindung für Sent-APPENDs auf und stellt den Ordner sicher (best-effort).

    Gibt None zurück, wenn IMAP nicht konfiguriert ist oder der Verbindungsaufbau fehlschlägt,
    damit ein IMAP-Ausfall nie den SMTP-Versand verhindert.
    """
    try:
        imap = _imap_connect(cfg)
    except Exception:
        logger.warning("IMAP-Verbindung für Sent-Ordner konnte nicht aufgebaut werden", exc_info=True)
        return None
    if imap is None:
        return None
    try:
        _ensure_imap_ordner(imap, sent_ordner)
    except Exception:
        logger.warning("Sent-Ordner %r konnte nicht sichergestellt werden", sent_ordner, exc_info=True)
        try:
            imap.logout()
        except Exception:
            pass
        return None
    return imap


def close_sent_connection(imap) -> None:
    """Schließt eine per open_sent_connection geöffnete Verbindung (best-effort)."""
    if imap is None:
        return
    try:
        imap.logout()
    except Exception:
        pass


def _retention_cleanup_ordner(imap, ordner: str, grenze: str) -> int:
    """Löscht in einem Ordner Mails vor `grenze` (IMAP-Datumsformat, z.B. '01-Jan-2025'). Best-effort pro Ordner."""
    status, _ = imap.select(ordner)
    if status != "OK":
        logger.warning("IMAP-Ordner %r für Retention nicht gefunden", ordner)
        return 0
    _, data = imap.search(None, "BEFORE", grenze)
    uids = data[0].split() if data and data[0] else []
    if not uids:
        return 0
    imap.uid("STORE", b",".join(uids), "+FLAGS", "\\Deleted")
    imap.expunge()
    return len(uids)


def imap_retention_cleanup(cfg: dict, ordner_liste: list[str], retention_tage: int) -> int:
    """Löscht in den übergebenen App-eigenen IMAP-Ordnern Mails älter als retention_tage (best-effort).

    INBOX, Spam und Trash sind nie Teil von ordner_liste (Aufrufer-Verantwortung), damit sie
    von der automatischen Löschung ausgenommen bleiben.
    """
    imap = _imap_connect(cfg)
    if imap is None:
        return 0
    grenze = (datetime.now() - timedelta(days=retention_tage)).strftime("%d-%b-%Y")
    deleted = 0
    try:
        for ordner in ordner_liste:
            try:
                deleted += _retention_cleanup_ordner(imap, ordner, grenze)
            except Exception:
                logger.exception("IMAP-Retention für Ordner %r fehlgeschlagen", ordner)
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    return deleted


def process_email(
    db,
    raw_bytes: bytes,
    *,
    xls_path: str | None = None,
    pruefungstypen: list[str] | None = None,
    imap_uid: str | None = None,
) -> bool:
    """Parse raw email bytes, extrahiert Inhalt und speichert Task. True = neu, False = Duplikat."""
    msg = email.message_from_bytes(raw_bytes)
    message_id = (msg.get("Message-ID") or "").strip()

    # Fall back to content hash when Message-ID absent to prevent duplicate tasks
    dedup_key = message_id if message_id else ("hash:" + hashlib.sha256(raw_bytes).hexdigest())

    if db.execute("SELECT id FROM tasks WHERE message_id = ?", (dedup_key,)).fetchone():
        return False

    from_raw = msg.get("From", "")
    von_name, von_email = email.utils.parseaddr(from_raw)
    betreff = _decode_header_value(msg.get("Subject", ""))

    anhang_count = sum(1 for _ in _iter_dokument_parts(msg))

    members = load_members_from_xls(xls_path) if xls_path else []
    valid_types = pruefungstypen or ["G25"]
    extraction = extract_from_email(msg, valid_types, members)

    pruefungstyp = extraction["pruefungstyp"]
    faelligkeitsdatum = extraction["faelligkeitsdatum"]

    zuordnung = bestimme_zuordnung(extraction, members)
    status = zuordnung["status"]
    mitglied_nr = zuordnung["mitglied_nr"]
    mitglied_name = zuordnung["mitglied_name"]

    faelligkeitsdatum_str = faelligkeitsdatum.isoformat() if faelligkeitsdatum else None

    empfangen_am = datetime.now().isoformat(timespec="seconds")
    raw_text = extraction["raw_text"] or None
    cursor = db.execute(
        """INSERT INTO tasks
               (status, empfangen_am, von_email, von_name, betreff, message_id, raw_email,
                anhang_count, pruefungstyp, faelligkeitsdatum, mitglied_nr, mitglied_name, raw_text,
                imap_uid, kandidat_absender_nr, kandidat_absender_name,
                kandidat_dokument_nr, kandidat_dokument_name)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            status, empfangen_am, von_email or None, von_name or None, betreff or None,
            dedup_key, raw_bytes, anhang_count,
            pruefungstyp, faelligkeitsdatum_str, mitglied_nr, mitglied_name, raw_text,
            imap_uid, zuordnung["kandidat_absender_nr"], zuordnung["kandidat_absender_name"],
            zuordnung["kandidat_dokument_nr"], zuordnung["kandidat_dokument_name"],
        ),
    )
    task_id = cursor.lastrowid

    # Nachweis als implizite E-Mail-Bestätigung: nur wenn die zugeordnete Person
    # tatsächlich mit dem Absender übereinstimmt (ADR 0003, präzisiert in Issue #38).
    if mitglied_nr and zuordnung["sender_bestaetigt"]:
        db.execute(
            "UPDATE email_verifikation SET bestaetigt_am=?, status='bestaetigt', bestaetigung_herkunft='automatisch', adresse_geaendert=0 WHERE pers_nr=?",
            (empfangen_am, mitglied_nr),
        )

    # Duplikaterkennung: gleicher Absender + gleicher Typ innerhalb von 14 Tagen
    if pruefungstyp and von_email:
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=14)).isoformat(timespec="seconds")
        existing = db.execute(
            """SELECT id FROM tasks
               WHERE von_email = ? AND pruefungstyp = ? AND empfangen_am >= ?
               AND id != ? AND status != 'ERLEDIGT'""",
            (von_email, pruefungstyp, cutoff, task_id),
        ).fetchone()
        if existing:
            db.execute(
                "UPDATE tasks SET hinweis = 'Mögliches Duplikat' WHERE id = ?",
                (task_id,),
            )

    return True


def _decode_header_value(value: str) -> str:
    parts = email.header.decode_header(value)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return "".join(decoded)


def _send_admin_notification(smtp_config: dict, admin_emails: list[str], new_count: int) -> None:
    if not admin_emails or not smtp_config.get("host"):
        return

    subject = f"[Nachweis-Checker] {new_count} neue Nachweise eingegangen"
    body = (
        f"Es sind {new_count} neue Nachweisemail(s) eingegangen.\n\n"
        "Bitte die Aufgaben in der App prüfen."
    )

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = smtp_config.get("from_addr", "")
    msg["To"] = ", ".join(admin_emails)

    try:
        with smtplib.SMTP(smtp_config["host"], smtp_config.get("port", 587)) as server:
            if smtp_config.get("user") and smtp_config.get("password"):
                server.starttls()
                server.login(smtp_config["user"], smtp_config["password"])
            server.sendmail(smtp_config.get("from_addr", ""), admin_emails, msg.as_string())
    except Exception:
        logger.exception("Admin-Benachrichtigung fehlgeschlagen")


def _ensure_imap_ordner(imap, folder_name: str) -> None:
    """Legt den IMAP-Ordner an, falls er noch nicht existiert."""
    _, folders = imap.list('""', folder_name)
    if not (folders and folders[0]):
        imap.create(folder_name)


def _move_email_to_folder(imap, msg_id: bytes, folder_name: str) -> None:
    """Markiert eine Nachricht für Verschiebung per COPY + \\Deleted; Expunge erfolgt außerhalb der Schleife."""
    typ, _ = imap.copy(msg_id, folder_name)
    if typ != "OK":
        raise RuntimeError(f"IMAP COPY fehlgeschlagen (Status: {typ})")
    imap.store(msg_id, "+FLAGS", "\\Deleted")


def poll_inbox(app) -> int:
    """Poll IMAP inbox and create tasks for new emails. Returns count of new tasks."""
    with app.app_context():
        from web.app import _safe_int, get_db, get_settings

        cfg = get_settings()
        imap_host = cfg.get("imap_host", "").strip()
        imap_user = cfg.get("imap_user", "").strip()
        imap_password = cfg.get("imap_password", "").strip()

        if not imap_host or not imap_user or not imap_password:
            return 0

        imap_port = _safe_int(cfg.get("imap_port"), 993)
        verifikation_ordner = (cfg.get("imap_verifikation_ordner") or "u-checker-verifikation").strip()

        # Fetch emails; keep connection open so we can mark Seen after DB commit
        imap = None
        fetched: list[tuple[bytes, bytes, str | None]] = []  # (imap_msg_id, raw_bytes, imap_uid)
        try:
            imap = imaplib.IMAP4_SSL(imap_host, imap_port, timeout=30)
            imap.login(imap_user, imap_password)
            imap.select("INBOX")

            _, data = imap.search(None, "UNSEEN")
            msg_ids = data[0].split() if data[0] else []

            for msg_id in msg_ids:
                _, msg_data = imap.fetch(msg_id, "(UID RFC822)")
                for part in msg_data:
                    if isinstance(part, tuple):
                        uid = _extract_uid(part[0])
                        fetched.append((msg_id, part[1], uid))
        except Exception:
            logger.exception("IMAP-Abruf fehlgeschlagen")
            if imap:
                try:
                    imap.logout()
                except Exception:
                    pass
            raise

        # Separate Verifikationsantworten, Task-Thread-Folgenachrichten und normale Mails
        verif_replies: list[tuple[str, bytes]] = []   # (pers_nr, imap_msg_id)
        task_replies: list[tuple[int, bytes, bytes, str | None]] = []  # (task_id, imap_msg_id, raw_bytes, imap_uid)
        normal_emails: list[tuple[bytes, bytes, str | None]] = []  # (imap_msg_id, raw_bytes, imap_uid)

        # Message-IDs bereits im aktuellen Batch erkannter Task-Folgenachrichten, damit eine
        # mehrstufige Kette (A -> B -> C) auch dann vollständig zugeordnet wird, wenn B und C
        # im selben Poll-Durchlauf eintreffen (B ist zu diesem Zeitpunkt noch nicht in der DB).
        batch_message_id_to_task: dict[str, int] = {}

        with closing(get_db()) as db:
            for imap_msg_id, raw, uid in fetched:
                msg = email.message_from_bytes(raw)
                in_reply_to = (msg.get("In-Reply-To") or "").strip()
                message_id = (msg.get("Message-ID") or "").strip()
                if in_reply_to:
                    row = db.execute(
                        "SELECT pers_nr FROM email_verifikation WHERE verifikationsmail_message_id = ?",
                        (in_reply_to,),
                    ).fetchone()
                    if row:
                        verif_replies.append((row["pers_nr"], imap_msg_id))
                        continue
                    batch_task_id = batch_message_id_to_task.get(in_reply_to)
                    task_row = db.execute(
                        """SELECT t.id AS task_id FROM tasks t
                           WHERE t.message_id = ? AND t.status != 'ERLEDIGT'
                           UNION
                           SELECT tn.task_id FROM task_nachrichten tn
                           JOIN tasks t ON t.id = tn.task_id
                           WHERE tn.message_id = ? AND t.status != 'ERLEDIGT'""",
                        (in_reply_to, in_reply_to),
                    ).fetchone()
                    task_id = task_row["task_id"] if task_row else batch_task_id
                    if task_id:
                        task_replies.append((task_id, imap_msg_id, raw, uid))
                        if message_id:
                            batch_message_id_to_task[message_id] = task_id
                        continue
                normal_emails.append((imap_msg_id, raw, uid))

        # Task-Thread-Folgenachrichten als eingehende task_nachrichten speichern (keine erneute
        # Zuordnungslogik, kein neuer Task, Task bleibt in INBOX bis "Erledigt").
        if task_replies:
            with closing(get_db()) as db:
                for task_id, _imap_msg_id, raw, uid in task_replies:
                    msg = email.message_from_bytes(raw)
                    from_raw = msg.get("From", "")
                    von_name, von_email = email.utils.parseaddr(from_raw)
                    betreff = _decode_header_value(msg.get("Subject", ""))
                    message_id = (msg.get("Message-ID") or "").strip() or None
                    in_reply_to = (msg.get("In-Reply-To") or "").strip() or None
                    text = collect_body_text_from_email(msg)
                    zeitstempel = datetime.now().isoformat(timespec="seconds")
                    db.execute(
                        """INSERT INTO task_nachrichten
                               (task_id, richtung, zeitstempel, von_email, betreff, text, raw_email,
                                message_id, in_reply_to, imap_uid)
                           VALUES (?, 'eingehend', ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (task_id, zeitstempel, von_email or None, betreff or None, text, raw,
                         message_id, in_reply_to, uid),
                    )
                db.commit()
            for _task_id, imap_msg_id, _raw, _uid in task_replies:
                try:
                    imap.store(imap_msg_id, "+FLAGS", "\\Seen")
                except Exception:
                    logger.warning("IMAP Seen-Markierung fehlgeschlagen für Task-Folgenachricht")

        # Verifikationsantworten verarbeiten: Status setzen + in Ordner verschieben
        if verif_replies:
            try:
                now = datetime.now().isoformat(timespec="seconds")
                _ensure_imap_ordner(imap, verifikation_ordner)
                for pers_nr, imap_msg_id in verif_replies:
                    with closing(get_db()) as db:
                        db.execute(
                            "UPDATE email_verifikation SET status='bestaetigt', bestaetigt_am=?, bestaetigung_herkunft='automatisch', adresse_geaendert=0 WHERE pers_nr=?",
                            (now, pers_nr),
                        )
                        db.commit()
                    try:
                        _move_email_to_folder(imap, imap_msg_id, verifikation_ordner)
                    except Exception:
                        logger.warning("IMAP-Verschiebung fehlgeschlagen für Mitglied %s", pers_nr)
                imap.expunge()
            except Exception:
                logger.exception("Verifikationsantworten konnten nicht verarbeitet werden")

        # Commit to DB before marking emails as Seen on the server
        new_count = 0
        from web.app import _xls_path
        xls_path = str(_xls_path()) if _xls_path().exists() else None
        pruefungstypen_list = [t.strip() for t in (cfg.get("pruefungstypen") or "G25").split(",") if t.strip()]
        with closing(get_db()) as db:
            for _, raw, uid in normal_emails:
                if process_email(db, raw, xls_path=xls_path, pruefungstypen=pruefungstypen_list, imap_uid=uid):
                    new_count += 1
            db.commit()

        # Only mark Seen after successful DB commit
        try:
            for imap_msg_id, _, _uid in normal_emails:
                imap.store(imap_msg_id, "+FLAGS", "\\Seen")
        except Exception:
            logger.warning("IMAP Seen-Markierung fehlgeschlagen für %d Nachrichten", len(normal_emails))

        try:
            imap.logout()
        except Exception:
            pass

        if new_count > 0:
            smtp_config = {
                "host": cfg.get("smtp_host", ""),
                "port": _safe_int(cfg.get("smtp_port"), 587),
                "user": cfg.get("smtp_user", ""),
                "password": cfg.get("smtp_password", ""),
                "from_addr": cfg.get("smtp_from", ""),
            }
            admin_emails = [
                e.strip() for e in (cfg.get("zusammenfassung_an") or "").split(",") if e.strip()
            ]
            _send_admin_notification(smtp_config, admin_emails, new_count)

        return new_count
