"""Teamtailor public job board API."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from .base import CompanyEntry, http_get_json

ID = "teamtailor"


def detect(entry: CompanyEntry) -> str | None:
    if entry.ats == ID and entry.token:
        # token may be full careers host or slug
        if "." in entry.token:
            return f"https://{entry.token}/jobs.json"
        return f"https://{entry.token}.teamtailor.com/jobs.json"
    for url in (entry.api, entry.careers_url):
        if not url:
            continue
        try:
            parsed = urlparse(url if "://" in url else f"https://{url}")
        except Exception:
            continue
        host = (parsed.hostname or "").lower()
        if "teamtailor.com" not in host and not host.endswith(".teamtailor.com"):
            # some use custom domains — require explicit ats+token
            continue
        return f"https://{host}/jobs.json"
    return None


def fetch(entry: CompanyEntry) -> list[dict[str, Any]]:
    api = detect(entry)
    if not api:
        raise ValueError(f"teamtailor: cannot resolve for {entry.label}")
    if not entry.token:
        try:
            entry.token = urlparse(api).hostname or ""
        except Exception:
            pass
    payload = http_get_json(api)
    if isinstance(payload, dict):
        jobs = payload.get("jobs") or payload.get("data") or []
        return jobs if isinstance(jobs, list) else []
    return payload if isinstance(payload, list) else []


class _P:
    id = ID
    detect = staticmethod(detect)
    fetch = staticmethod(fetch)


PROVIDER = _P()
