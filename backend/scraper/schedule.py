"""Nightly scheduler for scraper runs."""

from __future__ import annotations

from apscheduler.schedulers.blocking import BlockingScheduler

from .run import execute_run


def start_scheduler(hour: int = 2, minute: int = 0) -> BlockingScheduler:
    """Start a blocking nightly scheduler in local timezone.

    This function is explicit opt-in; nothing starts on module import.
    """
    scheduler = BlockingScheduler()
    # FINDINGS_pipeline: guard against overlapping runs (max_instances=1) and coalesce
    # a run missed while the process was down instead of firing several catch-ups; the
    # grace window lets a late wake (laptop asleep at 02:00) still trigger the run.
    scheduler.add_job(
        execute_run, "cron", hour=hour, minute=minute,
        max_instances=1, coalesce=True, misfire_grace_time=3600,
    )
    scheduler.start()
    return scheduler


if __name__ == "__main__":
    start_scheduler()

