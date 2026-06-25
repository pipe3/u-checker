from __future__ import annotations

import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None
JOB_ID = "automatischer_lauf"

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
        from web.app import get_settings, save_settings

        cfg = get_settings()
        intervall = cfg.get("script_intervall", "manuell")

        if intervall == "manuell":
            return

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
            _ensure_scheduler().add_job(
                _job_ausfuehren,
                trigger="date",
                run_date=naechster,
                id=JOB_ID,
                args=[app],
                replace_existing=True,
            )


def reschedule(app) -> None:
    if app.config.get("TESTING"):
        return

    with app.app_context():
        from web.app import get_settings, save_settings

        cfg = get_settings()
        intervall = cfg.get("script_intervall", "manuell")

        if _scheduler is not None and _scheduler.get_job(JOB_ID):
            _scheduler.remove_job(JOB_ID)

        if intervall == "manuell":
            save_settings({"naechster_lauf": ""})
            return

        naechster = naechster_lauf_berechnen(intervall)
        if naechster:
            save_settings({"naechster_lauf": naechster.isoformat(timespec="seconds")})
            _ensure_scheduler().add_job(
                _job_ausfuehren,
                trigger="date",
                run_date=naechster,
                id=JOB_ID,
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
