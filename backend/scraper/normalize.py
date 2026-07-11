"""Normalization from ATS-specific jobs to unified records."""

from __future__ import annotations

from functools import lru_cache
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from typing import Any

import yaml

BASE_DIR = Path(__file__).resolve().parent
FILTERS_PATH = BASE_DIR / "filters.yaml"


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data:
            self.parts.append(data)

    def get_text(self) -> str:
        return " ".join(" ".join(self.parts).split())


def html_to_text(value: str | None) -> str:
    if not value:
        return ""
    decoded = unescape(value)
    parser = _HTMLTextExtractor()
    parser.feed(decoded)
    parser.close()
    return parser.get_text()


def detect_remote_flag(title: str, location: str, description_text: str) -> bool:
    haystack = f"{title} {location} {description_text}".lower()
    keywords = ("remote", "work from home", "distributed")
    return any(token in haystack for token in keywords)


def _serialize_raw(raw_job: dict[str, Any]) -> str:
    return json.dumps(raw_job, ensure_ascii=True, sort_keys=True)


@lru_cache(maxsize=1)
def _load_filters(path: Path = FILTERS_PATH) -> dict[str, list[str]]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {
        "internship_patterns": [str(x) for x in payload.get("internship_patterns", []) if str(x).strip()],
        "location_allow": [str(x) for x in payload.get("location_allow", []) if str(x).strip()],
        "sponsorship_knockout": [str(x) for x in payload.get("sponsorship_knockout", []) if str(x).strip()],
    }


def _matches_any_substring(value: str, patterns: list[str]) -> bool:
    haystack = value.lower()
    return any(pattern.lower() in haystack for pattern in patterns)


def _matches_any_regex(value: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, value, flags=re.IGNORECASE) for pattern in patterns)


def compute_targeting_flags(title: str, location: str, description_text: str) -> dict[str, bool]:
    filters = _load_filters()
    return {
        "is_internship": _matches_any_substring(title, filters["internship_patterns"]),
        "location_match": _matches_any_substring(location, filters["location_allow"]),
        "sponsorship_knockout": _matches_any_regex(description_text, filters["sponsorship_knockout"]),
    }


def normalize_greenhouse(token: str, raw_job: dict[str, Any]) -> dict[str, Any]:
    description_text = html_to_text(raw_job.get("content"))
    location = (raw_job.get("location") or {}).get("name", "")
    departments = raw_job.get("departments") or []
    department = ", ".join(d.get("name", "") for d in departments if d.get("name"))
    title = raw_job.get("title", "")
    flags = compute_targeting_flags(title=title, location=location, description_text=description_text)
    return {
        "source_ats": "greenhouse",
        "company": raw_job.get("company_name") or token,
        "external_id": str(raw_job.get("id", "")),
        "title": title,
        "location": location,
        "remote_flag": detect_remote_flag(title, location, description_text),
        "is_internship": flags["is_internship"],
        "location_match": flags["location_match"],
        "sponsorship_knockout": flags["sponsorship_knockout"],
        "department": department,
        "description_text": description_text,
        "apply_url": raw_job.get("absolute_url", ""),
        "posted_at": raw_job.get("first_published"),
        "updated_at": raw_job.get("updated_at"),
        "raw_json": _serialize_raw(raw_job),
    }


def normalize_lever(token: str, raw_job: dict[str, Any]) -> dict[str, Any]:
    categories = raw_job.get("categories") or {}
    location = categories.get("location", "")
    title = raw_job.get("text", "")
    description_text = raw_job.get("descriptionPlain") or html_to_text(raw_job.get("description"))
    department = categories.get("team") or categories.get("department") or categories.get("commitment") or ""
    flags = compute_targeting_flags(title=title, location=location, description_text=description_text)
    return {
        "source_ats": "lever",
        "company": token,
        "external_id": str(raw_job.get("id", "")),
        "title": title,
        "location": location,
        "remote_flag": detect_remote_flag(title, location, description_text),
        "is_internship": flags["is_internship"],
        "location_match": flags["location_match"],
        "sponsorship_knockout": flags["sponsorship_knockout"],
        "department": department,
        "description_text": description_text,
        "apply_url": raw_job.get("applyUrl") or raw_job.get("hostedUrl", ""),
        "posted_at": raw_job.get("createdAt"),
        "updated_at": None,
        "raw_json": _serialize_raw(raw_job),
    }


def normalize_ashby(token: str, raw_job: dict[str, Any]) -> dict[str, Any]:
    title = raw_job.get("title", "")
    location = raw_job.get("location", "")
    description_text = raw_job.get("descriptionPlain") or html_to_text(raw_job.get("descriptionHtml"))
    department = raw_job.get("department") or raw_job.get("team") or ""
    is_remote = bool(raw_job.get("isRemote")) or detect_remote_flag(title, location, description_text)
    flags = compute_targeting_flags(title=title, location=location, description_text=description_text)
    return {
        "source_ats": "ashby",
        "company": token,
        "external_id": str(raw_job.get("id", "")),
        "title": title,
        "location": location,
        "remote_flag": is_remote,
        "is_internship": flags["is_internship"],
        "location_match": flags["location_match"],
        "sponsorship_knockout": flags["sponsorship_knockout"],
        "department": department,
        "description_text": description_text,
        "apply_url": raw_job.get("applyUrl") or raw_job.get("jobUrl", ""),
        "posted_at": raw_job.get("publishedAt"),
        "updated_at": raw_job.get("publishedAt"),
        "raw_json": _serialize_raw(raw_job),
    }


def normalize_job(ats: str, token: str, raw_job: dict[str, Any]) -> dict[str, Any]:
    ats_lower = ats.lower().strip()
    if ats_lower == "greenhouse":
        job = normalize_greenhouse(token, raw_job)
    elif ats_lower == "lever":
        job = normalize_lever(token, raw_job)
    elif ats_lower == "ashby":
        job = normalize_ashby(token, raw_job)
    else:
        raise ValueError(f"Unsupported ATS for normalization: {ats}")
    # Tag user search strings (searches.yaml) — flows to matcher/queue.
    try:
        from .searches import match_searches
    except ImportError:  # pragma: no cover
        from scraper.searches import match_searches  # type: ignore
    job["matched_searches"] = match_searches(
        str(job.get("title") or ""),
        str(job.get("description_text") or ""),
    )
    return job

