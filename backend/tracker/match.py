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
from urllib.parse import parse_qsl, urlparse

try:  # package vs top-level import (mirrors store.py)
    from .store import normalize_company
except ImportError:  # pragma: no cover
    from tracker.store import normalize_company

_ALNUM = re.compile(r"[^a-z0-9]+")
_COMMON_URL_TOKENS = {
    "www", "jobs", "job", "careers", "career", "apply", "application", "applications",
    "boards", "greenhouse", "lever", "ashbyhq", "myworkdayjobs", "workday", "icims",
    "smartrecruiters", "bamboohr", "taleo", "successfactors", "jobdetail", "jobapplication",
    "ftl", "en", "us", "home", "candidate", "requisition",
}
_UUIDISH = re.compile(r"^[0-9a-f]{8,}(?:-[0-9a-f]{4,})*$")


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


def _parse(url: str):
    u = (url or "").strip()
    if not u:
        return None
    if "://" not in u:
        u = "//" + u
    try:
        return urlparse(u)
    except Exception:  # pragma: no cover - defensive
        return None


def _ats_family(host: str) -> str:
    h = (host or "").lower()
    for key in (
        "greenhouse", "lever", "ashbyhq", "myworkdayjobs", "workday", "icims",
        "smartrecruiters", "bamboohr", "taleo", "successfactors", "jobvite",
        "rippling", "paycom", "workable", "recruitee", "paylocity",
    ):
        if key in h:
            return "workday" if key == "myworkdayjobs" else key
    return h


def _url_tokens(url: str) -> set[str]:
    p = _parse(url)
    if not p:
        return set()
    raw: list[str] = []
    raw.extend((p.hostname or "").split("."))
    raw.extend((p.path or "").split("/"))
    for k, v in parse_qsl(p.query or "", keep_blank_values=False):
        raw.extend([k, v])
    tokens: set[str] = set()
    for part in raw:
        for tok in re.split(r"[^a-zA-Z0-9-]+", part.lower()):
            cleaned = tok.strip("-_")
            if len(cleaned) >= 3 and cleaned not in _COMMON_URL_TOKENS:
                tokens.add(cleaned)
    return tokens


def _job_id_tokens(tokens: set[str]) -> set[str]:
    ids: set[str] = set()
    for tok in tokens:
        if tok.isdigit() and len(tok) >= 4:
            ids.add(tok)
        elif _UUIDISH.match(tok):
            ids.add(tok)
        elif any(c.isdigit() for c in tok) and len(tok) >= 8:
            ids.add(tok)
    return ids


def _host_agrees(item_url: str, page_host: str, page_url: str) -> bool:
    item_host = _host_of(item_url)
    current_host = (page_host or _host_of(page_url)).lower()
    if item_host and current_host and item_host == current_host:
        return True
    return bool(item_host and current_host and _ats_family(item_host) == _ats_family(current_host))


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

    item_tokens = _url_tokens(item_url)
    page_tokens = _url_tokens(url)
    host_url = f"//{host}/" if host else ""
    page_tokens |= _url_tokens(host_url)

    # 2. ATS apply URLs often change host/path between posting and form while
    # preserving a strong job id (Greenhouse gh_jid, Lever/Ashby UUIDs, iCIMS ids).
    if item_url and url and _host_agrees(item_url, host, url):
        shared_ids = _job_id_tokens(item_tokens) & _job_id_tokens(page_tokens)
        if shared_ids:
            return 95

    # 3. Same ATS family plus company slug in both URLs covers apply.greenhouse.io,
    # SmartRecruiters application paths, and employer subdomain variants.
    if item_url and url and len(item_slug) >= 3 and _host_agrees(item_url, host, url):
        if item_slug in _ALNUM.sub("", item_url.lower()) and item_slug in _ALNUM.sub("", f"{host} {url}".lower()):
            return 90

    # 4. Company identity embedded in the page host/url (GH path, WD subdomain).
    if len(item_slug) >= 3:
        haystack = _ALNUM.sub("", f"{host} {_norm_url(url)}".lower())
        if item_slug in haystack:
            return 85

    # 5/6. Fall back to company-name agreement between item and detected company.
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
