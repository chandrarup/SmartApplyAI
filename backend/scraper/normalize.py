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


def lever_created_at_to_iso(created_at: Any) -> str | None:
    """Lever createdAt is epoch MILLISECONDS — divide by 1000 before ISO.

    Regression guard: values > 1e12 are treated as ms; seconds pass through.
    """
    if created_at is None or created_at == "":
        return None
    if isinstance(created_at, str) and not created_at.strip().isdigit():
        # Already an ISO-ish string
        return created_at
    try:
        ts = float(created_at)
    except (TypeError, ValueError):
        return str(created_at)
    if ts > 1e12:  # milliseconds
        ts = ts / 1000.0
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


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
        "posted_at": lever_created_at_to_iso(raw_job.get("createdAt")),
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


def normalize_workday(token: str, raw_job: dict[str, Any]) -> dict[str, Any]:
    title = raw_job.get("title", "")
    location = raw_job.get("locationsText") or ""
    path = raw_job.get("externalPath") or ""
    base = raw_job.get("_job_base") or ""
    apply_url = (base + path) if path else ""
    description_text = " ".join(str(x) for x in (raw_job.get("bulletFields") or []) if x)
    company = raw_job.get("_tenant") or (token.split("/")[0] if token else token)
    flags = compute_targeting_flags(title=title, location=location, description_text=description_text)
    return {
        "source_ats": "workday",
        "company": company,
        "external_id": str(raw_job.get("id") or path or title),
        "title": title,
        "location": location,
        "remote_flag": detect_remote_flag(title, location, description_text),
        "is_internship": flags["is_internship"],
        "location_match": flags["location_match"],
        "sponsorship_knockout": flags["sponsorship_knockout"],
        "department": "",
        "description_text": description_text,
        "apply_url": apply_url,
        "posted_at": raw_job.get("postedOn"),
        "updated_at": None,
        "raw_json": _serialize_raw(raw_job),
    }


def normalize_smartrecruiters(token: str, raw_job: dict[str, Any]) -> dict[str, Any]:
    title = raw_job.get("name") or raw_job.get("title") or ""
    loc = raw_job.get("location") or {}
    location = ""
    if isinstance(loc, dict):
        location = loc.get("city") or loc.get("region") or loc.get("country") or ""
    description_text = html_to_text(raw_job.get("jobAd", {}).get("sections", {}).get("jobDescription", {}).get("text") if isinstance(raw_job.get("jobAd"), dict) else "") or str(raw_job.get("description") or "")
    apply_url = raw_job.get("applyUrl") or raw_job.get("ref") or ""
    if apply_url and not str(apply_url).startswith("http"):
        apply_url = f"https://jobs.smartrecruiters.com/{token}/{raw_job.get('id', '')}"
    flags = compute_targeting_flags(title=title, location=str(location), description_text=description_text)
    return {
        "source_ats": "smartrecruiters",
        "company": token,
        "external_id": str(raw_job.get("id") or title),
        "title": title,
        "location": str(location),
        "remote_flag": detect_remote_flag(title, str(location), description_text),
        "is_internship": flags["is_internship"],
        "location_match": flags["location_match"],
        "sponsorship_knockout": flags["sponsorship_knockout"],
        "department": str((raw_job.get("department") or {}).get("label") if isinstance(raw_job.get("department"), dict) else raw_job.get("department") or ""),
        "description_text": description_text,
        "apply_url": apply_url,
        "posted_at": raw_job.get("releasedDate") or raw_job.get("createdOn"),
        "updated_at": None,
        "raw_json": _serialize_raw(raw_job),
    }


def normalize_workable(token: str, raw_job: dict[str, Any]) -> dict[str, Any]:
    title = raw_job.get("title") or ""
    location = raw_job.get("city") or raw_job.get("location") or ""
    if isinstance(location, dict):
        location = location.get("city") or location.get("country") or ""
    description_text = html_to_text(raw_job.get("description")) or str(raw_job.get("shortcode") or "")
    apply_url = raw_job.get("url") or raw_job.get("application_url") or ""
    flags = compute_targeting_flags(title=title, location=str(location), description_text=description_text)
    return {
        "source_ats": "workable",
        "company": token,
        "external_id": str(raw_job.get("shortcode") or raw_job.get("id") or title),
        "title": title,
        "location": str(location),
        "remote_flag": detect_remote_flag(title, str(location), description_text) or bool(raw_job.get("remote")),
        "is_internship": flags["is_internship"],
        "location_match": flags["location_match"],
        "sponsorship_knockout": flags["sponsorship_knockout"],
        "department": str(raw_job.get("department") or ""),
        "description_text": description_text,
        "apply_url": apply_url,
        "posted_at": raw_job.get("created_at") or raw_job.get("published_on"),
        "updated_at": None,
        "raw_json": _serialize_raw(raw_job),
    }


def normalize_teamtailor(token: str, raw_job: dict[str, Any]) -> dict[str, Any]:
    # Teamtailor jobs.json often nests under attributes
    node = raw_job.get("attributes") if isinstance(raw_job.get("attributes"), dict) else raw_job
    title = node.get("title") or raw_job.get("title") or ""
    location = node.get("human_status") or node.get("locations") or ""
    if isinstance(location, list):
        location = ", ".join(str(x) for x in location)
    description_text = html_to_text(node.get("body") or node.get("pitch") or "")
    apply_url = node.get("url") or raw_job.get("links", {}).get("careersite-job-url") if isinstance(raw_job.get("links"), dict) else node.get("url") or ""
    flags = compute_targeting_flags(title=title, location=str(location), description_text=description_text)
    return {
        "source_ats": "teamtailor",
        "company": token.split(".")[0] if token else token,
        "external_id": str(raw_job.get("id") or node.get("id") or title),
        "title": title,
        "location": str(location),
        "remote_flag": detect_remote_flag(title, str(location), description_text),
        "is_internship": flags["is_internship"],
        "location_match": flags["location_match"],
        "sponsorship_knockout": flags["sponsorship_knockout"],
        "department": str(node.get("department") or ""),
        "description_text": description_text,
        "apply_url": apply_url or "",
        "posted_at": node.get("start-date") or node.get("created-at"),
        "updated_at": None,
        "raw_json": _serialize_raw(raw_job),
    }


def normalize_personio(token: str, raw_job: dict[str, Any]) -> dict[str, Any]:
    title = raw_job.get("name") or raw_job.get("title") or ""
    location = raw_job.get("office") or ""
    desc_parts = [
        f"{d.get('name', '')}: {d.get('value', '')}"
        for d in (raw_job.get("jobDescriptions") or [])
        if isinstance(d, dict)
    ]
    description_text = html_to_text("\n".join(desc_parts))
    apply_url = f"https://{token}.jobs.personio.de/job/{raw_job.get('id', '')}" if token else ""
    flags = compute_targeting_flags(title=title, location=str(location), description_text=description_text)
    return {
        "source_ats": "personio",
        "company": token,
        "external_id": str(raw_job.get("id") or title),
        "title": title,
        "location": str(location),
        "remote_flag": detect_remote_flag(title, str(location), description_text),
        "is_internship": flags["is_internship"],
        "location_match": flags["location_match"],
        "sponsorship_knockout": flags["sponsorship_knockout"],
        "department": str(raw_job.get("department") or ""),
        "description_text": description_text,
        "apply_url": apply_url,
        "posted_at": None,
        "updated_at": None,
        "raw_json": _serialize_raw(raw_job),
    }


def normalize_tracker(token: str, raw_job: dict[str, Any]) -> dict[str, Any]:
    title = raw_job.get("title") or ""
    company = raw_job.get("company") or token or "tracker"
    location = raw_job.get("location") or ""
    description_text = raw_job.get("description") or f"{title} at {company}"
    flags = compute_targeting_flags(title=title, location=str(location), description_text=description_text)
    # Tracker rows are overwhelmingly internships
    if not flags["is_internship"]:
        flags["is_internship"] = True
    return {
        "source_ats": "tracker",
        "company": company,
        "external_id": str(raw_job.get("id") or f"{company}:{title}"),
        "title": title,
        "location": str(location),
        "remote_flag": detect_remote_flag(title, str(location), description_text),
        "is_internship": flags["is_internship"],
        "location_match": flags["location_match"],
        "sponsorship_knockout": flags["sponsorship_knockout"],
        "department": "",
        "description_text": description_text,
        "apply_url": raw_job.get("apply_url") or "",
        "posted_at": None,
        "updated_at": None,
        "raw_json": _serialize_raw(raw_job),
    }


def normalize_job(ats: str, token: str, raw_job: dict[str, Any]) -> dict[str, Any]:
    ats_lower = ats.lower().strip()
    dispatch = {
        "greenhouse": normalize_greenhouse,
        "lever": normalize_lever,
        "ashby": normalize_ashby,
        "workday": normalize_workday,
        "smartrecruiters": normalize_smartrecruiters,
        "workable": normalize_workable,
        "teamtailor": normalize_teamtailor,
        "personio": normalize_personio,
        "tracker": normalize_tracker,
    }
    fn = dispatch.get(ats_lower)
    if not fn:
        raise ValueError(f"Unsupported ATS for normalization: {ats}")
    job = fn(token, raw_job)
    try:
        from .searches import match_searches
    except ImportError:  # pragma: no cover
        from scraper.searches import match_searches  # type: ignore
    job["matched_searches"] = match_searches(
        str(job.get("title") or ""),
        str(job.get("description_text") or ""),
    )
    return job

