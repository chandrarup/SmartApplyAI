"""Tier B adapter extension point (disabled by default)."""

from __future__ import annotations

from typing import Any


def fetch_tierb_jobs(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Fetch Tier B sources.

    This interface is intentionally left unimplemented. Add logic here only when
    you are ready to support additional non-core sources.
    """
    raise NotImplementedError("Tier B scraping is intentionally not implemented yet.")

