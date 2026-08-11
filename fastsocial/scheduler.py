from __future__ import annotations

import logging
from functools import lru_cache

from apscheduler.schedulers.background import BackgroundScheduler

from fastsocial.config import settings
from fastsocial.reporting import run_due_reports
from fastsocial.services import (
    check_account_health,
    collect_metrics,
    process_due_autolists,
    publish_due_posts,
    run_async,
)

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def scheduler() -> BackgroundScheduler:
    value = BackgroundScheduler(timezone="UTC", daemon=True)
    value.add_job(
        lambda: run_async(publish_due_posts()),
        "interval",
        seconds=15,
        id="publish_due_posts",
        max_instances=1,
        coalesce=True,
    )
    value.add_job(
        lambda: run_async(process_due_autolists()),
        "interval",
        seconds=60,
        id="process_due_autolists",
        max_instances=1,
        coalesce=True,
    )
    value.add_job(
        lambda: run_async(run_due_reports()),
        "interval",
        minutes=15,
        id="run_due_reports",
        max_instances=1,
        coalesce=True,
    )
    value.add_job(
        lambda: run_async(collect_metrics()),
        "cron",
        minute=17,
        id="collect_metrics",
        max_instances=1,
        coalesce=True,
    )
    value.add_job(
        lambda: run_async(check_account_health()),
        "interval",
        hours=6,
        id="account_health",
        max_instances=1,
        coalesce=True,
    )
    return value


def start_scheduler() -> None:
    if settings().scheduler_enabled and not scheduler().running:
        scheduler().start()
        log.info("FastSocial scheduler started")
