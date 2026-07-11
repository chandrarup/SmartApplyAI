"""Shared types and HTTP helpers for ATS providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import requests

DEFAULT_TIMEOUT_SECONDS = 20
MAX_CONCURRENCY = 4
SLEEP_BETWEEN_CALLS_SECONDS = 0.35
USER_AGENT = "SmartApplyAI-scraper/1.0 (personal job search)"


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


def http_get_json(url: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> Any:
    resp = requests.get(
        url,
        timeout=timeout,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    resp.raise_for_status()
    return resp.json()


def http_post_json(
    url: str,
    body: dict[str, Any] | None = None,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Any:
    resp = requests.post(
        url,
        json=body or {},
        timeout=timeout,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    resp.raise_for_status()
    return resp.json()


def http_get_text(url: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> tuple[int, str, str]:
    """Return (status, final_url, body_text). Does not raise on 4xx/5xx."""
    resp = requests.get(
        url,
        timeout=timeout,
        headers={"User-Agent": USER_AGENT},
        allow_redirects=True,
    )
    return resp.status_code, str(resp.url), resp.text or ""
