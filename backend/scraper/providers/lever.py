"""Lever public postings API provider."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from .base import CompanyEntry, http_get_json

ID = "lever"
_HOST_RE = re.compile(r"^jobs\.(?:eu\.)?lever\.co$", re.I)


def detect(entry: CompanyEntry) -> str | None:
    if entry.ats == ID and entry.token:
        return f"https://api.lever.co/v0/postings/{entry.token}?mode=json"
    for url in (entry.api, entry.careers_url):
        if not url:
            continue
        try:
            parsed = urlparse(url)
        except Exception:
            continue
        host = parsed.hostname or ""
        if host in ("api.lever.co", "api.eu.lever.co"):
            return url.split("?")[0] + ("?mode=json" if "mode=" not in url else "")
        if not _HOST_RE.match(host):
            continue
        slug = next((p for p in (parsed.path or "").split("/") if p), "")
        if slug:
            api_host = "api.eu.lever.co" if "eu.lever" in host else "api.lever.co"
            return f"https://{api_host}/v0/postings/{slug}?mode=json"
    return None


def fetch(entry: CompanyEntry) -> list[dict[str, Any]]:
    api = detect(entry)
    if not api and entry.token:
        api = f"https://api.lever.co/v0/postings/{entry.token}?mode=json"
    if not api:
        raise ValueError(f"lever: cannot resolve board for {entry.label}")
    if not entry.token:
        m = re.search(r"/postings/([^/?]+)", api)
        if m:
            entry.token = m.group(1)
    payload = http_get_json(api)
    if not isinstance(payload, list):
        raise ValueError(f"lever: expected list for {entry.label}")
    return payload


class _P:
    id = ID
    detect = staticmethod(detect)
    fetch = staticmethod(fetch)


PROVIDER = _P()
