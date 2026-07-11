"""HTTP clients for public ATS job board endpoints.

Thin compatibility shim over the provider registry. Prefer
`providers.resolve_provider` + `provider.fetch` for new code.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import time
from typing import Any

from .providers.base import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_CONCURRENCY,
    SLEEP_BETWEEN_CALLS_SECONDS,
    CompanyEntry,
)
from .providers.registry import load_providers, resolve_provider

# Re-exports for existing imports
__all__ = [
    "CompanyTarget",
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_CONCURRENCY",
    "SLEEP_BETWEEN_CALLS_SECONDS",
    "fetch_board",
    "fetch_greenhouse",
    "fetch_lever",
    "fetch_ashby",
    "fetch_many",
]


@dataclass(frozen=True)
class CompanyTarget:
    """Company board identifier for an ATS source."""

    ats: str
    token: str


def fetch_board(ats: str, token: str) -> list[dict[str, Any]]:
    """Fetch jobs for one ATS target via the provider registry."""
    entry = CompanyEntry(ats=ats.lower().strip(), token=token)
    providers = load_providers()
    provider = resolve_provider(entry, providers)
    if not provider:
        raise ValueError(f"Unsupported ATS: {ats}")
    return provider.fetch(entry)


def fetch_greenhouse(token: str) -> list[dict[str, Any]]:
    return fetch_board("greenhouse", token)


def fetch_lever(token: str) -> list[dict[str, Any]]:
    return fetch_board("lever", token)


def fetch_ashby(token: str) -> list[dict[str, Any]]:
    return fetch_board("ashby", token)


def fetch_many(targets: list[CompanyTarget]) -> dict[CompanyTarget, list[dict[str, Any]]]:
    """Fetch many boards with bounded concurrency."""
    results: dict[CompanyTarget, list[dict[str, Any]]] = {}
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as executor:
        future_map = {}
        for target in targets:
            future = executor.submit(fetch_board, target.ats, target.token)
            future_map[future] = target
            time.sleep(SLEEP_BETWEEN_CALLS_SECONDS)
        for future in as_completed(future_map):
            target = future_map[future]
            results[target] = future.result()
    return results
