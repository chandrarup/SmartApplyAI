"""Greenhouse public boards API provider."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from .base import CompanyEntry, http_get_json

ID = "greenhouse"
_BOARD_RE = re.compile(r"boards(?:-api)?\.greenhouse\.io", re.I)


def detect(entry: CompanyEntry) -> str | None:
    if entry.ats == ID and entry.token:
        return f"https://boards-api.greenhouse.io/v1/boards/{entry.token}/jobs?content=true"
    for url in (entry.api, entry.careers_url):
        if not url:
            continue
        try:
            parsed = urlparse(url)
        except Exception:
            continue
        if not _BOARD_RE.search(parsed.netloc or ""):
            continue
        parts = [p for p in (parsed.path or "").split("/") if p]
        # boards.greenhouse.io/{token} or /embed/job_board?for=token
        token = ""
        if "for=" in (parsed.query or ""):
            m = re.search(r"(?:^|&)for=([^&]+)", parsed.query)
            token = m.group(1) if m else ""
        elif parts:
            token = parts[0]
            if token in ("embed", "jobs") and len(parts) > 1:
                token = parts[1]
        if token:
            return f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    return None


def fetch(entry: CompanyEntry) -> list[dict[str, Any]]:
    api = detect(entry)
    if not api and entry.token:
        api = f"https://boards-api.greenhouse.io/v1/boards/{entry.token}/jobs?content=true"
    if not api:
        raise ValueError(f"greenhouse: cannot resolve board for {entry.label}")
    # Stash token for normalize
    if not entry.token:
        m = re.search(r"/boards/([^/]+)/jobs", api)
        if m:
            entry.token = m.group(1)
    payload = http_get_json(api)
    return payload.get("jobs", []) if isinstance(payload, dict) else []


class _P:
    id = ID
    detect = staticmethod(detect)
    fetch = staticmethod(fetch)


PROVIDER = _P()
