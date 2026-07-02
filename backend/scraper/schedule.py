"""Nightly scheduler for scraper runs."""

from __future__ import annotations

from apscheduler.schedulers.blocking import BlockingScheduler

from .run import execute_run


def start_scheduler(hour: int = 2, minute: int = 0) -> BlockingScheduler:
    """Start a blocking nightly scheduler in local timezone.

    This function is explicit opt-in; nothing starts on module import.
    """
    scheduler = BlockingScheduler()
    scheduler.add_job(execute_run, "cron", hour=hour, minute=minute)
    scheduler.start()
    return scheduler


if __name__ == "__main__":
    start_scheduler()

