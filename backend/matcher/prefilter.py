"""Stage 0 prefilter over scraped jobs."""

from __future__ import annotations

from pathlib import Path
import re
import sqlite3
from typing import Any

import yaml


def _load_filters(filters_path: str | Path) -> dict[str, Any]:
    path = Path(filters_path)
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _matches_any_token(text: str, tokens: list[str]) -> bool:
    lowered = (text or "").lower()
    for token in tokens:
        token = (token or "").strip().lower()
        if not token:
            continue
        if re.search(rf"\b{re.escape(token)}\b", lowered):
            return True
    return False


def prefilter_jobs(
    jobs_db_path: str | Path,
    role_mode: str = "internship",
    filters_path: str | Path = "backend/scraper/filters.yaml",
) -> list[dict[str, Any]]:
    filters = _load_filters(filters_path)
    fulltime_only_excludes = filters.get("fulltime_only_excludes", []) or []
    role_mode = (role_mode or "internship").lower().strip()
    if role_mode not in {"internship", "fulltime", "both"}:
        role_mode = "internship"

    with sqlite3.connect(str(jobs_db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT source_ats, company, external_id, title, location, description_text, apply_url,
                   is_internship, location_match, sponsorship_knockout, status
            FROM jobs
            WHERE status = 'active'
            """
        ).fetchall()

    survivors: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        if int(item.get("location_match") or 0) != 1:
            continue
        if int(item.get("sponsorship_knockout") or 0) == 1:
            continue

        is_internship = int(item.get("is_internship") or 0) == 1
        title = item.get("title") or ""
        if role_mode == "internship" and not is_internship:
            continue
        if role_mode == "fulltime":
            if is_internship:
                continue
            if fulltime_only_excludes and _matches_any_token(title, fulltime_only_excludes):
                continue

        survivors.append(item)

    print(
        f"[prefilter] active={len(rows)} survivors={len(survivors)} "
        f"(role_mode={role_mode}, location_match=true, sponsorship_knockout=false)"
    )
    return survivors

