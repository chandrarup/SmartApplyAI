"""Tracker READMEs as one normalized provider (crowd-sourced rows).

Deterministic markdown-table parsing — NO LLM. Fail LOUD if the expected
table schema changes. source_ats="tracker".
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

import requests

from .base import CompanyEntry, DEFAULT_TIMEOUT_SECONDS, USER_AGENT

ID = "tracker"

# Raw GitHub URLs for the internship tracker READMEs (same sources as bootstrap).
DEFAULT_SOURCES = (
    "https://raw.githubusercontent.com/speedyapply/2027-AI-College-Jobs/main/README.md",
    "https://raw.githubusercontent.com/speedyapply/2027-AI-College-Jobs/main/INTERN_INTL.md",
    "https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/dev/README.md",
)

# Required header tokens (case-insensitive). Table must include these columns.
REQUIRED_HEADERS = ("company", "role", "location", "application")


class TrackerSchemaError(RuntimeError):
    """Raised when a tracker README table no longer matches the expected schema."""


def detect(entry: CompanyEntry) -> str | None:
    """Trackers are opt-in via ats: tracker (or careers_url pointing at a raw README)."""
    if (entry.ats or "").lower() == ID:
        return entry.careers_url or entry.api or DEFAULT_SOURCES[0]
    url = (entry.careers_url or entry.api or "").lower()
    if "summer2027-internships" in url or "2027-ai-college-jobs" in url:
        return entry.careers_url or entry.api
    return None


def _split_row(line: str) -> list[str]:
    line = line.strip().strip("|")
    return [c.strip() for c in line.split("|")]


def _is_separator(cells: list[str]) -> bool:
    if not cells:
        return False
    return all(re.fullmatch(r":?-{3,}:?", c.replace(" ", "")) for c in cells if c)


def _header_index(headers: list[str]) -> dict[str, int]:
    lowered = [h.lower() for h in headers]
    idx: dict[str, int] = {}
    aliases = {
        "company": ("company", "name", "employer"),
        "role": ("role", "title", "position"),
        "location": ("location", "loc", "locations"),
        # speedyapply uses "Posting"; vanshb uses "Application" / "Link"
        "application": ("application", "apply", "link", "url", "application/link", "posting"),
    }
    for key, names in aliases.items():
        for i, h in enumerate(lowered):
            if any(n == h or n in h for n in names):
                idx[key] = i
                break
    missing = [k for k in REQUIRED_HEADERS if k not in idx]
    if missing:
        raise TrackerSchemaError(
            f"tracker table missing required columns {missing}; got headers={headers}"
        )
    return idx


def parse_markdown_tables(md: str, *, source_url: str = "") -> list[dict[str, Any]]:
    """Parse all pipe tables in markdown; assert schema; return raw row dicts."""
    lines = md.splitlines()
    jobs: list[dict[str, Any]] = []
    i = 0
    tables_seen = 0
    while i < len(lines):
        if "|" not in lines[i]:
            i += 1
            continue
        header_cells = _split_row(lines[i])
        if i + 1 >= len(lines) or not _is_separator(_split_row(lines[i + 1])):
            i += 1
            continue
        # Likely a table
        try:
            col = _header_index(header_cells)
        except TrackerSchemaError:
            # Not a jobs table (e.g. legend) — skip without failing the whole file
            # unless NO jobs table is found at end.
            i += 1
            continue
        tables_seen += 1
        i += 2  # skip header + separator
        while i < len(lines) and "|" in lines[i]:
            cells = _split_row(lines[i])
            i += 1
            if _is_separator(cells) or len(cells) < 3:
                continue
            def cell(key: str) -> str:
                j = col[key]
                return cells[j] if j < len(cells) else ""

            company = _strip_md(cell("company"))
            role = _strip_md(cell("role"))
            location = _strip_md(cell("location"))
            apply_raw = cell("application")
            apply_url = _extract_url(apply_raw)
            if not company or not role:
                continue
            if company.lower() in ("company", "--------"):
                continue
            jobs.append({
                "company": company,
                "title": role,
                "location": location,
                "apply_url": apply_url,
                "description": f"{role} at {company}. Location: {location}.",
                "source_url": source_url,
            })
        continue
    if tables_seen == 0:
        raise TrackerSchemaError(
            f"tracker: no job tables with required columns {REQUIRED_HEADERS} "
            f"in {source_url or 'markdown'}"
        )
    return jobs


_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
_URL_RE = re.compile(r"https?://[^\s<>)\"]+")


def _strip_md(text: str) -> str:
    text = _LINK_RE.sub(r"\1", text or "")
    text = re.sub(r"<[^>]+>", "", text)  # HTML tags in tracker cells
    text = re.sub(r"[*_`]", "", text)
    return text.strip()


def _extract_url(text: str) -> str:
    m = _LINK_RE.search(text or "")
    if m:
        return m.group(2).strip()
    m = _URL_RE.search(text or "")
    return m.group(0).strip() if m else ""


def _fetch_url(url: str) -> str:
    resp = requests.get(
        url,
        timeout=DEFAULT_TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT},
    )
    resp.raise_for_status()
    return resp.text


def fetch(entry: CompanyEntry) -> list[dict[str, Any]]:
    sources = []
    if entry.careers_url or entry.api:
        sources = [entry.careers_url or entry.api]
    else:
        sources = list(DEFAULT_SOURCES)

    all_jobs: list[dict[str, Any]] = []
    errors: list[str] = []
    for url in sources:
        try:
            md = _fetch_url(url)
            rows = parse_markdown_tables(md, source_url=url)
            all_jobs.extend(rows)
        except TrackerSchemaError as exc:
            errors.append(str(exc))
        except Exception as exc:  # network — isolate per source
            errors.append(f"{url}: {type(exc).__name__}: {exc}")

    if not all_jobs and errors:
        # Fail loud: schema/network killed every source
        raise TrackerSchemaError("tracker provider failed all sources: " + "; ".join(errors))
    if errors:
        for e in errors:
            print(f"⚠️ tracker: {e}")

    # Dedupe by company+title+url
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for j in all_jobs:
        key = f"{j.get('company')}|{j.get('title')}|{j.get('apply_url')}"
        if key in seen:
            continue
        seen.add(key)
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
        j["id"] = digest
        unique.append(j)
    return unique


class _P:
    id = ID
    detect = staticmethod(detect)
    fetch = staticmethod(fetch)


PROVIDER = _P()
