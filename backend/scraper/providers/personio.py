"""Personio public XML/JSON job board."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urlparse

import requests

from .base import CompanyEntry, USER_AGENT, DEFAULT_TIMEOUT_SECONDS

ID = "personio"


def detect(entry: CompanyEntry) -> str | None:
    if entry.ats == ID and entry.token:
        # token = company subdomain
        return f"https://{entry.token}.jobs.personio.de/xml?language=en"
    for url in (entry.api, entry.careers_url):
        if not url:
            continue
        try:
            parsed = urlparse(url)
        except Exception:
            continue
        host = (parsed.hostname or "").lower()
        m = re.match(r"^([\w-]+)\.jobs\.personio\.(de|com)$", host)
        if m:
            return f"https://{m.group(1)}.jobs.personio.{m.group(2)}/xml?language=en"
        if "personio" in host and entry.token:
            return f"https://{entry.token}.jobs.personio.de/xml?language=en"
    return None


def fetch(entry: CompanyEntry) -> list[dict[str, Any]]:
    api = detect(entry)
    if not api:
        raise ValueError(f"personio: cannot resolve for {entry.label}")
    if not entry.token:
        m = re.search(r"https://([\w-]+)\.jobs\.personio", api)
        if m:
            entry.token = m.group(1)
    resp = requests.get(
        api,
        timeout=DEFAULT_TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT, "Accept": "application/xml"},
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    jobs: list[dict[str, Any]] = []
    for pos in root.findall(".//position"):
        def _text(tag: str) -> str:
            el = pos.find(tag)
            return (el.text or "").strip() if el is not None else ""

        jid = _text("id") or _text("jobPositionId")
        title = _text("name") or _text("title")
        if not title:
            continue
        jobs.append({
            "id": jid or title,
            "name": title,
            "office": _text("office"),
            "department": _text("department"),
            "employmentType": _text("employmentType"),
            "jobDescriptions": [
                {"name": (d.findtext("name") or ""), "value": (d.findtext("value") or "")}
                for d in pos.findall(".//jobDescription")
            ],
            "recruitingCategory": _text("recruitingCategory"),
            "_company": entry.token or entry.name,
        })
    return jobs


class _P:
    id = ID
    detect = staticmethod(detect)
    fetch = staticmethod(fetch)


PROVIDER = _P()
