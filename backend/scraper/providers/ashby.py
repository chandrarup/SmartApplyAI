"""Ashby public job-board API provider."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from .base import CompanyEntry, http_get_json

ID = "ashby"


def detect(entry: CompanyEntry) -> str | None:
    if entry.ats == ID and entry.token:
        return f"https://api.ashbyhq.com/posting-api/job-board/{entry.token}"
    for url in (entry.api, entry.careers_url):
        if not url:
            continue
        try:
            parsed = urlparse(url)
        except Exception:
            continue
        host = (parsed.hostname or "").lower()
        if "ashbyhq.com" not in host:
            continue
        parts = [p for p in (parsed.path or "").split("/") if p]
        token = parts[0] if parts else ""
        if host.startswith("jobs.") and token:
            return f"https://api.ashbyhq.com/posting-api/job-board/{token}"
        if "job-board" in (parsed.path or "") and parts:
            token = parts[-1]
            return f"https://api.ashbyhq.com/posting-api/job-board/{token}"
    return None


def fetch(entry: CompanyEntry) -> list[dict[str, Any]]:
    api = detect(entry)
    if not api and entry.token:
        api = f"https://api.ashbyhq.com/posting-api/job-board/{entry.token}"
    if not api:
        raise ValueError(f"ashby: cannot resolve board for {entry.label}")
    if not entry.token:
        m = re.search(r"/job-board/([^/?]+)", api)
        if m:
            entry.token = m.group(1)
    payload = http_get_json(api)
    if isinstance(payload, dict) and "jobs" in payload:
        return payload["jobs"]
    if isinstance(payload, list):
        return payload
    raise ValueError(f"ashby: unexpected payload for {entry.label}")


class _P:
    id = ID
    detect = staticmethod(detect)
    fetch = staticmethod(fetch)


PROVIDER = _P()
