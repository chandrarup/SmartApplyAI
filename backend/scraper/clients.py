"""HTTP clients for public ATS job board endpoints."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import time
from typing import Any

import requests

DEFAULT_TIMEOUT_SECONDS = 20
MAX_CONCURRENCY = 4
SLEEP_BETWEEN_CALLS_SECONDS = 0.35


@dataclass(frozen=True)
class CompanyTarget:
    """Company board identifier for an ATS source."""

    ats: str
    token: str


def _get_json(url: str) -> Any:
    response = requests.get(url, timeout=DEFAULT_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def fetch_greenhouse(token: str) -> list[dict[str, Any]]:
    """Fetch Greenhouse board jobs for a token."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    payload = _get_json(url)
    return payload.get("jobs", [])


def fetch_lever(token: str) -> list[dict[str, Any]]:
    """Fetch Lever board jobs for a token."""
    url = f"https://api.lever.co/v0/postings/{token}?mode=json"
    payload = _get_json(url)
    if not isinstance(payload, list):
        raise ValueError(f"Expected list payload for lever token={token}")
    return payload


def fetch_ashby(token: str) -> list[dict[str, Any]]:
    """Fetch Ashby board jobs for a token."""
    url = f"https://api.ashbyhq.com/posting-api/job-board/{token}"
    payload = _get_json(url)
    if isinstance(payload, dict) and "jobs" in payload:
        return payload["jobs"]
    if isinstance(payload, list):
        return payload
    raise ValueError(f"Unexpected Ashby payload shape for token={token}")


def fetch_board(ats: str, token: str) -> list[dict[str, Any]]:
    """Fetch jobs for one ATS target."""
    ats_lower = ats.lower().strip()
    if ats_lower == "greenhouse":
        return fetch_greenhouse(token)
    if ats_lower == "lever":
        return fetch_lever(token)
    if ats_lower == "ashby":
        return fetch_ashby(token)
    raise ValueError(f"Unsupported ATS: {ats}")


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
