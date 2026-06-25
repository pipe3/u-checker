from __future__ import annotations

import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None
JOB_ID = "automatischer_lauf"
IMAP_JOB_ID = "imap_poll"
ARCHIV_CLEANUP_JOB_ID = "archiv_cleanup"
IMAP_POLL_MINUTEN_DEFAULT = 5

INTERVALL_DELTA: dict[str, timedelta] = {
    "wöchentlich": timedelta(weeks=1),
    "monatlich": timedelta(days=30),
}


def naechster_lauf_berechnen(intervall: str, von: datetime | None = None) -> datetime | None:
    delta = INTERVALL_DELTA.get(intervall)
    if delta is None:
        return None
    return (von or datetime.now()) + delta


def get_scheduler() -> BackgroundScheduler | None:
    return _scheduler


def _ensure_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None or not _scheduler.running:
        _scheduler = BackgroundScheduler()
        _scheduler.start()
    return _scheduler


def start(app) -> None:
    if app.config.get("TESTING"):
        return

    with app.app_context():
        from web.app import _safe_int, get_settings, save_settings

        cfg = get_settings()
        intervall = cfg.get("script_intervall", "manuell")
        sched = _ensure_scheduler()

        if intervall != "manuell":
            naechster_str = cfg.get("naechster_lauf", "")
            naechster: datetime | None = None

            if naechster_str:
                try:
                    naechster = datetime.fromisoformat(naechster_str)
                except ValueError:
                    naechster = None

            if naechster is None or naechster <= datetime.now():
                naechster = naechster_lauf_berechnen(intervall)
                if naechster:
                    save_settings({"naechster_lauf": naechster.isoformat(timespec="seconds")})

            if naechster:
                sched.add_job(
                    _job_ausfuehren,
                    trigger="date",
                    run_date=naechster,
                    id=JOB_ID,
                    args=[app],
                    replace_existing=True,
                )

        imap_host = cfg.get("imap_host", "").strip()
        if imap_host:
            minuten = _safe_int(cfg.get("imap_poll_minuten"), IMAP_POLL_MINUTEN_DEFAULT)
            sched.add_job(
                _imap_poll_job,
                trigger="interval",
                minutes=minuten,
                id=IMAP_JOB_ID,
                args=[app],
                replace_existing=True,
            )

        sched.add_job(
            _archiv_cleanup_job,
            trigger="interval",
            hours=24,
            id=ARCHIV_CLEANUP_JOB_ID,
            args=[app],
            replace_existing=True,
        )


def reschedule(app) -> None:
    if app.config.get("TESTING"):
        return

    with app.app_context():
        from web.app import _safe_int, get_settings, save_settings

        cfg = get_settings()
        intervall = cfg.get("script_intervall", "manuell")
        sched = _ensure_scheduler()

        if sched.get_job(JOB_ID):
            sched.remove_job(JOB_ID)

        if intervall == "manuell":
            save_settings({"naechster_lauf": ""})
        else:
            naechster = naechster_lauf_berechnen(intervall)
            if naechster:
                save_settings({"naechster_lauf": naechster.isoformat(timespec="seconds")})
                sched.add_job(
                    _job_ausfuehren,
                    trigger="date",
                    run_date=naechster,
                    id=JOB_ID,
                    args=[app],
                    replace_existing=True,
                )

        if sched.get_job(IMAP_JOB_ID):
            sched.remove_job(IMAP_JOB_ID)

        imap_host = cfg.get("imap_host", "").strip()
        if imap_host:
            minuten = _safe_int(cfg.get("imap_poll_minuten"), IMAP_POLL_MINUTEN_DEFAULT)
            sched.add_job(
                _imap_poll_job,
                trigger="interval",
                minutes=minuten,
                id=IMAP_JOB_ID,
                args=[app],
                replace_existing=True,
            )

        sched.add_job(
            _archiv_cleanup_job,
            trigger="interval",
            hours=24,
            id=ARCHIV_CLEANUP_JOB_ID,
            args=[app],
            replace_existing=True,
        )


def stop() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None


def _job_ausfuehren(app) -> None:
    try:
        with app.app_context():
            from web.app import _do_run
            logger.info("Automatischer Lauf gestartet")
            _do_run(dry_run=False)
    except Exception:
        logger.exception("Automatischer Lauf fehlgeschlagen")
    finally:
        reschedule(app)


def _imap_poll_job(app) -> None:
    try:
        from web.imap_poller import poll_inbox
        count = poll_inbox(app)
        if count:
            logger.info("IMAP-Polling: %d neue Nachweise verarbeitet", count)
    except Exception:
        logger.exception("IMAP-Polling fehlgeschlagen")


def _archiv_cleanup_job(app) -> None:
    try:
        with app.app_context():
            from web.app import archiv_cleanup
            deleted = archiv_cleanup()
            if deleted:
                logger.info("Archiv-Cleanup: %d veraltete Tasks gelöscht", deleted)
    except Exception:
        logger.exception("Archiv-Cleanup fehlgeschlagen")
