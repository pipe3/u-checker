from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None
IMAP_JOB_ID = "imap_poll"
ARCHIV_CLEANUP_JOB_ID = "archiv_cleanup"
IMAP_POLL_MINUTEN_DEFAULT = 5


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
        from web.app import _safe_int, get_settings

        cfg = get_settings()
        sched = _ensure_scheduler()

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
        from web.app import _safe_int, get_settings

        cfg = get_settings()
        sched = _ensure_scheduler()

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

        if not sched.get_job(ARCHIV_CLEANUP_JOB_ID):
            sched.add_job(
                _archiv_cleanup_job,
                trigger="interval",
                hours=24,
                id=ARCHIV_CLEANUP_JOB_ID,
                args=[app],
            )


def stop() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None


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
