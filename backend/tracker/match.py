"""Match the page the human is on to a ready-to-apply tracker item.

Pure, dependency-light scoring so it can be unit-tested without a DB. The extension
passes the current tab's host / url / detected company; we pick the single best
``ready_to_apply`` application (if any) whose approved package should drive autofill.

Why not host-only: many ATSes share one host (``boards.greenhouse.io/<company>/...``),
so the host alone can't tell two companies apart. The reliable signal is the company
identity appearing in the URL/host (Greenhouse path, Workday subdomain) or an exact URL.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

try:  # package vs top-level import (mirrors store.py)
    from .store import normalize_company
except ImportError:  # pragma: no cover
    from tracker.store import normalize_company

_ALNUM = re.compile(r"[^a-z0-9]+")


def _host_of(url: str) -> str:
    if not url:
        return ""
    u = url.strip()
    if "://" not in u:
        u = "//" + u
    try:
        return (urlparse(u).hostname or "").lower()
    except Exception:  # pragma: no cover - defensive
        return ""


def _norm_url(url: str) -> str:
    """Host + path, lowercased, trailing slash stripped (query/fragment dropped)."""
    if not url:
        return ""
    u = url.strip()
    if "://" not in u:
        u = "//" + u
    try:
        p = urlparse(u)
        return f"{(p.hostname or '').lower()}{(p.path or '').rstrip('/')}".lower()
    except Exception:  # pragma: no cover - defensive
        return url.strip().lower().rstrip("/")


def _slug(text: str) -> str:
    """Alphanumeric-only lowercase form of a normalized company name."""
    return _ALNUM.sub("", normalize_company(text or ""))


def score(item: dict[str, Any], host: str = "", url: str = "", company: str = "") -> int:
    """Score how well a tracker item matches the current page. Higher = better."""
    item_url = str(item.get("url") or "")
    item_slug = _slug(str(item.get("company") or ""))
    page_company_norm = normalize_company(company or "")
    item_company_norm = normalize_company(str(item.get("company") or ""))

    # 1. Exact URL (host + path) is unambiguous.
    if item_url and url and _norm_url(item_url) == _norm_url(url):
        return 100

    # 2. Company identity embedded in the page host/url (GH path, WD subdomain).
    if len(item_slug) >= 3:
        haystack = _ALNUM.sub("", f"{host} {_norm_url(url)}".lower())
        if item_slug in haystack:
            return 85

    # 3/4. Fall back to company-name agreement between item and detected company.
    if item_company_norm and page_company_norm:
        if item_company_norm == page_company_norm:
            return 70
        if item_company_norm in page_company_norm or page_company_norm in item_company_norm:
            return 50

    return 0


def best_match(
    items: list[dict[str, Any]] | None,
    host: str = "",
    url: str = "",
    company: str = "",
    *,
    min_score: int = 50,
) -> dict[str, Any] | None:
    """Return the highest-scoring item at or above ``min_score``, else ``None``."""
    best: dict[str, Any] | None = None
    best_score = 0
    for it in items or []:
        s = score(it, host=host, url=url, company=company)
        if s > best_score:
            best_score = s
            best = it
    return best if best_score >= min_score else None
