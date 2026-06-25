from __future__ import annotations

import email
import email.header
import email.utils
import hashlib
import imaplib
import logging
import smtplib
from contextlib import closing
from datetime import datetime
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


def process_email(db, raw_bytes: bytes) -> bool:
    """Parse raw email bytes and store as task. Returns True if new, False if duplicate."""
    msg = email.message_from_bytes(raw_bytes)
    message_id = (msg.get("Message-ID") or "").strip()

    # Fall back to content hash when Message-ID absent to prevent duplicate tasks
    dedup_key = message_id if message_id else ("hash:" + hashlib.sha256(raw_bytes).hexdigest())

    if db.execute("SELECT id FROM tasks WHERE message_id = ?", (dedup_key,)).fetchone():
        return False

    from_raw = msg.get("From", "")
    von_name, von_email = email.utils.parseaddr(from_raw)

    betreff = _decode_header_value(msg.get("Subject", ""))

    anhang_count = sum(
        1 for part in msg.walk()
        if part.get_content_disposition() == "attachment"
    )

    empfangen_am = datetime.now().isoformat(timespec="seconds")
    db.execute(
        """INSERT INTO tasks (status, empfangen_am, von_email, von_name, betreff, message_id, raw_email, anhang_count)
           VALUES ('NEU', ?, ?, ?, ?, ?, ?, ?)""",
        (empfangen_am, von_email or None, von_name or None, betreff or None,
         dedup_key, raw_bytes, anhang_count),
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

        # Fetch emails; keep connection open so we can mark Seen after DB commit
        imap = None
        fetched: list[tuple[bytes, bytes]] = []  # (imap_msg_id, raw_bytes)
        try:
            imap = imaplib.IMAP4_SSL(imap_host, imap_port)
            imap.login(imap_user, imap_password)
            imap.select("INBOX")

            _, data = imap.search(None, "UNSEEN")
            msg_ids = data[0].split() if data[0] else []

            for msg_id in msg_ids:
                _, msg_data = imap.fetch(msg_id, "(RFC822)")
                for part in msg_data:
                    if isinstance(part, tuple):
                        fetched.append((msg_id, part[1]))
        except Exception:
            logger.exception("IMAP-Abruf fehlgeschlagen")
            if imap:
                try:
                    imap.logout()
                except Exception:
                    pass
            return 0

        # Commit to DB before marking emails as Seen on the server
        new_count = 0
        with closing(get_db()) as db:
            for _, raw in fetched:
                if process_email(db, raw):
                    new_count += 1
            db.commit()

        # Only mark Seen after successful DB commit
        try:
            for imap_msg_id, _ in fetched:
                imap.store(imap_msg_id, "+FLAGS", "\\Seen")
        except Exception:
            logger.warning("IMAP Seen-Markierung fehlgeschlagen für %d Nachrichten", len(fetched))

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
