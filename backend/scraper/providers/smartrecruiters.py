"""SmartRecruiters public postings API."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from .base import CompanyEntry, http_get_json

ID = "smartrecruiters"


def detect(entry: CompanyEntry) -> str | None:
    if entry.ats == ID and entry.token:
        return f"https://api.smartrecruiters.com/v1/companies/{entry.token}/postings"
    for url in (entry.api, entry.careers_url):
        if not url:
            continue
        try:
            parsed = urlparse(url)
        except Exception:
            continue
        host = (parsed.hostname or "").lower()
        if "smartrecruiters.com" not in host:
            continue
        parts = [p for p in (parsed.path or "").split("/") if p]
        # careers.smartrecruiters.com/{company} or api.../companies/{company}/postings
        token = ""
        if "companies" in parts:
            i = parts.index("companies")
            if i + 1 < len(parts):
                token = parts[i + 1]
        elif parts:
            token = parts[0]
        if token:
            return f"https://api.smartrecruiters.com/v1/companies/{token}/postings"
    return None


def fetch(entry: CompanyEntry) -> list[dict[str, Any]]:
    api = detect(entry)
    if not api and entry.token:
        api = f"https://api.smartrecruiters.com/v1/companies/{entry.token}/postings"
    if not api:
        raise ValueError(f"smartrecruiters: cannot resolve for {entry.label}")
    if not entry.token:
        m = re.search(r"/companies/([^/]+)/postings", api)
        if m:
            entry.token = m.group(1)
    # Paginate offset
    jobs: list[dict[str, Any]] = []
    offset = 0
    limit = 100
    while offset < 2000:
        url = f"{api}?limit={limit}&offset={offset}"
        payload = http_get_json(url)
        content = payload.get("content") if isinstance(payload, dict) else None
        if not isinstance(content, list) or not content:
            break
        jobs.extend(content)
        if len(content) < limit:
            break
        offset += limit
    return jobs


class _P:
    id = ID
    detect = staticmethod(detect)
    fetch = staticmethod(fetch)


PROVIDER = _P()
