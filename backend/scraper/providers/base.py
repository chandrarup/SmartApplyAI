"""Shared types and HTTP helpers for ATS providers."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any

import requests

DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_RETRIES = 3
MAX_CONCURRENCY = 4
SLEEP_BETWEEN_CALLS_SECONDS = 0.35
USER_AGENT = "SmartApplyAI-scraper/1.0 (personal job search)"

# Retry/backoff bounds (sourcing-v3 contract §2.2).
BACKOFF_CAP_SECONDS = 30.0
RETRY_AFTER_CAP_SECONDS = 60.0


@dataclass
class CompanyEntry:
    """One companies.yaml row — either {ats, token} or {careers_url} (or both)."""

    name: str = ""
    ats: str = ""
    token: str = ""
    careers_url: str = ""
    api: str = ""
    cluster: str = ""
    source: str = ""
    max_pages: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        if self.ats and self.token:
            return f"{self.ats}:{self.token}"
        if self.name:
            return self.name
        return self.careers_url or self.api or "?"


def _backoff_seconds(attempt: int, retry_after: str | None = None) -> float:
    """Seconds to wait before the retry that follows failed attempt `attempt` (0-based).

    Exponential backoff with jitter, capped at BACKOFF_CAP_SECONDS. A numeric
    Retry-After header value (seconds) overrides the computed backoff and is
    capped at RETRY_AFTER_CAP_SECONDS; non-numeric values (HTTP-dates) are
    ignored and fall through to the exponential schedule.
    """
    if retry_after is not None:
        try:
            seconds = float(retry_after)
        except (TypeError, ValueError):
            pass  # non-numeric Retry-After → use exponential backoff
        else:
            return min(max(seconds, 0.0), RETRY_AFTER_CAP_SECONDS)
    return min(1.0 * 2**attempt + random.uniform(0, 0.5), BACKOFF_CAP_SECONDS)


def _request_json(
    method: str,
    url: str,
    *,
    retries: int,
    timeout: float,
    json_body: dict[str, Any] | None = None,
) -> tuple[Any, float]:
    """GET/POST `url` expecting a JSON payload, with retry + backoff.

    Retries up to `retries` times after the initial attempt (total attempts =
    retries + 1) on requests timeout/connection errors, HTTP 429, and HTTP 5xx.
    Other 4xx are config errors and fail immediately (single attempt). After
    the final attempt the last exception is (re-)raised. Returns
    (payload, latency_ms of the successful attempt). No shared mutable state.
    """
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    kwargs: dict[str, Any] = {"timeout": timeout, "headers": headers}
    if method == "POST":
        headers["Content-Type"] = "application/json"
        kwargs["json"] = json_body or {}
    attempts = max(1, retries + 1)
    for attempt in range(attempts):
        started = time.monotonic()
        try:
            if method == "POST":
                resp = requests.post(url, **kwargs)
            else:
                resp = requests.get(url, **kwargs)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            if attempt + 1 >= attempts:
                raise  # final attempt: re-raise the last exception
            time.sleep(_backoff_seconds(attempt))
            continue
        # Inspect the status BEFORE raise_for_status() to decide retry vs fail.
        status = resp.status_code
        retryable = status == 429 or 500 <= status < 600
        if retryable and attempt + 1 < attempts:
            time.sleep(_backoff_seconds(attempt, resp.headers.get("Retry-After")))
            continue
        # Non-retryable 4xx (immediately) and exhausted 429/5xx raise here.
        resp.raise_for_status()
        latency_ms = (time.monotonic() - started) * 1000.0
        return resp.json(), latency_ms
    raise AssertionError(f"unreachable: no attempt made for {url}")  # pragma: no cover


def timed_get_json(
    url: str,
    *,
    retries: int = DEFAULT_RETRIES,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[Any, float]:
    """GET JSON with retries; returns (payload, latency_ms of the successful attempt)."""
    return _request_json("GET", url, retries=retries, timeout=timeout)


def timed_post_json(
    url: str,
    json_body: dict[str, Any] | None = None,
    *,
    retries: int = DEFAULT_RETRIES,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[Any, float]:
    """POST JSON with retries; returns (payload, latency_ms of the successful attempt)."""
    return _request_json("POST", url, retries=retries, timeout=timeout, json_body=json_body)


def http_get_json(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    retries: int = DEFAULT_RETRIES,
) -> Any:
    """Thin wrapper over timed_get_json discarding latency (legacy provider seam)."""
    payload, _latency_ms = timed_get_json(url, retries=retries, timeout=timeout)
    return payload


def http_post_json(
    url: str,
    body: dict[str, Any] | None = None,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    retries: int = DEFAULT_RETRIES,
) -> Any:
    """Thin wrapper over timed_post_json discarding latency (legacy provider seam)."""
    payload, _latency_ms = timed_post_json(url, body, retries=retries, timeout=timeout)
    return payload


def http_get_text(url: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> tuple[int, str, str]:
    """Return (status, final_url, body_text). Does not raise on 4xx/5xx."""
    resp = requests.get(
        url,
        timeout=timeout,
        headers={"User-Agent": USER_AGENT},
        allow_redirects=True,
    )
    return resp.status_code, str(resp.url), resp.text or ""
