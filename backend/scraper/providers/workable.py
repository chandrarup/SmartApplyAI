"""Workable public jobs widget API."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from .base import CompanyEntry, http_get_json

ID = "workable"


def detect(entry: CompanyEntry) -> str | None:
    if entry.ats == ID and entry.token:
        return f"https://apply.workable.com/api/v1/widget/accounts/{entry.token}"
    for url in (entry.api, entry.careers_url):
        if not url:
            continue
        try:
            parsed = urlparse(url)
        except Exception:
            continue
        host = (parsed.hostname or "").lower()
        if "workable.com" not in host:
            continue
        parts = [p for p in (parsed.path or "").split("/") if p]
        token = parts[0] if parts else ""
        # apply.workable.com/{slug} or {slug}.workable.com
        if host.endswith(".workable.com") and host not in ("www.workable.com", "apply.workable.com"):
            token = host.split(".")[0]
        if token and token not in ("api", "www", "apply"):
            return f"https://apply.workable.com/api/v1/widget/accounts/{token}"
    return None


def fetch(entry: CompanyEntry) -> list[dict[str, Any]]:
    api = detect(entry)
    if not api and entry.token:
        api = f"https://apply.workable.com/api/v1/widget/accounts/{entry.token}"
    if not api:
        raise ValueError(f"workable: cannot resolve for {entry.label}")
    if not entry.token:
        m = re.search(r"/accounts/([^/?]+)", api)
        if m:
            entry.token = m.group(1)
    payload = http_get_json(api)
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    return jobs if isinstance(jobs, list) else []


class _P:
    id = ID
    detect = staticmethod(detect)
    fetch = staticmethod(fetch)


PROVIDER = _P()
