"""User search-string matching for scraped jobs.

Editable via searches.yaml — no code changes required to add queries.
Tags land on the job as matched_searches: list[str] (search names).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re
from typing import Any

import yaml

SEARCHES_PATH = Path(__file__).resolve().parent / "searches.yaml"


@lru_cache(maxsize=4)
def load_searches(path: str | None = None) -> list[dict[str, Any]]:
    p = Path(path) if path else SEARCHES_PATH
    if not p.is_file():
        return []
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    raw = data.get("searches") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        query = str(entry.get("query") or "").strip()
        if not name or not query:
            continue
        fields = str(entry.get("fields") or "both").lower().strip()
        if fields not in {"title", "description", "both"}:
            fields = "both"
        try:
            boost = int(entry.get("boost", 5))
        except (TypeError, ValueError):
            boost = 5
        out.append({"name": name, "query": query, "fields": fields, "boost": boost})
    return out


def _compile_query(query: str) -> re.Pattern[str]:
    """Plain terms → substring regex; strings that look like regex are used as-is."""
    q = query.strip()
    # If it contains regex metacharacters beyond spaces, treat as regex.
    if re.search(r"[|\\.*+?()\[\]{}]", q):
        try:
            return re.compile(q, re.I)
        except re.error:
            pass
    return re.compile(re.escape(q), re.I)


def match_searches(
    title: str,
    description: str,
    *,
    searches: list[dict[str, Any]] | None = None,
    path: str | None = None,
) -> list[str]:
    """Return list of search *names* that match this job."""
    entries = searches if searches is not None else load_searches(path)
    title = title or ""
    description = description or ""
    hit: list[str] = []
    for entry in entries:
        fields = entry["fields"]
        hay = title if fields == "title" else description if fields == "description" else f"{title}\n{description}"
        if _compile_query(entry["query"]).search(hay):
            hit.append(entry["name"])
    return hit


def max_boost_for_names(names: list[str], *, path: str | None = None) -> int:
    """Largest configured boost among matched search names (default 0)."""
    if not names:
        return 0
    by_name = {e["name"]: int(e.get("boost") or 0) for e in load_searches(path)}
    return max((by_name.get(n, 5) for n in names), default=0)
