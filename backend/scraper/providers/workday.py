"""Workday CXS public jobs API — POST with paging.

Adapted from career-ops providers/workday.mjs (patterns only).
"""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import urlparse

from .base import CompanyEntry, http_post_json

ID = "workday"
PAGE_SIZE = 20
DEFAULT_MAX_PAGES = 50
MAX_PAGES_CAP = 200
INTER_PAGE_DELAY_S = 0.15

_WD_RE = re.compile(
    r"^https://([\w-]+)\.(wd[\w-]*)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([^/?#]+)",
    re.I,
)


def _resolve_endpoint(entry: CompanyEntry) -> dict[str, str] | None:
    for url in (entry.api, entry.careers_url):
        if not isinstance(url, str) or not url:
            continue
        m = _WD_RE.match(url.strip())
        if not m:
            continue
        tenant, instance, site = m.group(1), m.group(2), m.group(3)
        origin = f"https://{tenant}.{instance}.myworkdayjobs.com"
        return {
            "api": f"{origin}/wday/cxs/{tenant}/{site}/jobs",
            "job_base": f"{origin}/{site}",
            "tenant": tenant,
            "site": site,
        }
    # Explicit ats+token form: token = "tenant/site" or "tenant.wdN/site"
    if entry.ats == ID and entry.token:
        tok = entry.token.strip("/")
        if "/" in tok:
            left, site = tok.split("/", 1)
            if ".wd" in left:
                tenant, instance = left.split(".", 1)
            else:
                tenant, instance = left, "myworkdayjobs"
                # fallback common pattern needs full careers_url
                return None
            origin = f"https://{tenant}.{instance}.myworkdayjobs.com"
            return {
                "api": f"{origin}/wday/cxs/{tenant}/{site}/jobs",
                "job_base": f"{origin}/{site}",
                "tenant": tenant,
                "site": site,
            }
    return None


def detect(entry: CompanyEntry) -> str | None:
    ep = _resolve_endpoint(entry)
    return ep["api"] if ep else None


def fetch(entry: CompanyEntry) -> list[dict[str, Any]]:
    ep = _resolve_endpoint(entry)
    if not ep:
        raise ValueError(f"workday: cannot resolve CXS endpoint for {entry.label}")
    if not entry.token:
        entry.token = f"{ep['tenant']}/{ep['site']}"
    if not entry.name:
        entry.name = ep["tenant"]

    max_pages = entry.max_pages or DEFAULT_MAX_PAGES
    max_pages = min(max(1, max_pages), MAX_PAGES_CAP)

    body0 = {"limit": PAGE_SIZE, "offset": 0, "searchText": "", "appliedFacets": {}}
    first = http_post_json(ep["api"], body0)
    jobs = _parse_page(first, ep)
    total = first.get("total") if isinstance(first, dict) else None
    first_n = len((first or {}).get("jobPostings") or []) if isinstance(first, dict) else 0

    if isinstance(total, int) and total >= 0:
        pages = min((total + PAGE_SIZE - 1) // PAGE_SIZE, max_pages)
    else:
        pages = max_pages if first_n >= PAGE_SIZE else 1

    for page in range(1, pages):
        time.sleep(INTER_PAGE_DELAY_S)
        try:
            payload = http_post_json(
                ep["api"],
                {"limit": PAGE_SIZE, "offset": page * PAGE_SIZE, "searchText": "", "appliedFacets": {}},
            )
        except Exception as exc:
            print(f"⚠️ workday: {entry.label} truncated at page {page + 1}: {exc}")
            break
        page_jobs = _parse_page(payload, ep)
        jobs.extend(page_jobs)
        postings = (payload or {}).get("jobPostings") if isinstance(payload, dict) else []
        if not isinstance(postings, list) or len(postings) < PAGE_SIZE:
            break
    return jobs


def _parse_page(json_payload: Any, ep: dict[str, str]) -> list[dict[str, Any]]:
    postings = json_payload.get("jobPostings") if isinstance(json_payload, dict) else None
    if not isinstance(postings, list):
        return []
    out: list[dict[str, Any]] = []
    for j in postings:
        if not isinstance(j, dict):
            continue
        path = j.get("externalPath") or ""
        title = (j.get("title") or "").strip()
        if not path or not title:
            continue
        out.append({
            "title": title,
            "externalPath": path,
            "locationsText": j.get("locationsText") or "",
            "postedOn": j.get("postedOn"),
            "bulletFields": j.get("bulletFields") or [],
            "_job_base": ep["job_base"],
            "_tenant": ep["tenant"],
            "_site": ep["site"],
            "id": path,  # stable-ish within site
        })
    return out


class _P:
    id = ID
    detect = staticmethod(detect)
    fetch = staticmethod(fetch)


PROVIDER = _P()
