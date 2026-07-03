"""Gap-skill sourcing for the teach loop.

Two sources, merged in this order:
  1. Manual seeds — `gaps.yaml`, skills the user deliberately wants to study.
  2. Matcher-derived — `missing_skills` aggregated across the matcher's `matches.db`,
     frequency-weighted so the gaps that keep costing real matches rise to the top.

Manual seeds always win position and are never dropped; matcher gaps fill in behind
them, de-duplicated case-insensitively. Reading matches.db here is a read-only
cross-feed — the matcher still owns that store (rule 8).
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from .lesson import load_gap_skills


def _default_matches_db() -> str:
    return str(Path(__file__).resolve().parent.parent / "matcher" / "matches.db")


def _skill_name(entry: Any) -> str:
    """missing_skills entries may be plain strings or {'skill': ...} dicts."""
    if isinstance(entry, dict):
        return str(entry.get("skill") or entry.get("name") or "").strip()
    return str(entry or "").strip()


def matcher_gap_skills(
    matches_db_path: str | None = None,
    profile_id: str | None = None,
) -> list[tuple[str, int]]:
    """Aggregate `missing_skills` across stored matches, most frequent first.

    Frequency = number of matched jobs that flagged the skill as missing. Ties break
    alphabetically for deterministic output. Returns [] if the store is absent (the
    matcher may not have run yet) — never raises on a missing DB (rule 7).
    """
    path = matches_db_path or _default_matches_db()
    if not Path(path).exists():
        return []

    sql = "SELECT fit_json FROM matches"
    params: list[Any] = []
    if profile_id:
        sql += " WHERE profile_id = ?"
        params.append(profile_id)

    counts: Counter[str] = Counter()
    seen_display: dict[str, str] = {}
    conn = sqlite3.connect(path)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []  # table not created yet
    finally:
        conn.close()

    for row in rows:
        try:
            fit = json.loads(row["fit_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        for entry in fit.get("missing_skills") or []:
            name = _skill_name(entry)
            if not name:
                continue
            key = name.lower()
            seen_display.setdefault(key, name)  # keep first-seen casing
            counts[key] += 1

    return [
        (seen_display[key], n)
        for key, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def merge_gap_skills(
    manual: list[str],
    matcher_ranked: list[tuple[str, int]],
) -> list[str]:
    """Manual seeds first (order preserved), then matcher gaps by frequency, deduped."""
    out: list[str] = []
    seen: set[str] = set()
    for s in manual:
        key = s.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(s.strip())
    for name, _freq in matcher_ranked:
        key = name.lower()
        if key and key not in seen:
            seen.add(key)
            out.append(name)
    return out


def load_gap_skills_merged(
    gaps_path: str | None = None,
    matches_db_path: str | None = None,
    profile_id: str | None = None,
) -> list[str]:
    """Full gap list for the teach loop: manual `gaps.yaml` + matcher-derived missing_skills."""
    manual = load_gap_skills(gaps_path) if gaps_path else load_gap_skills()
    matcher_ranked = matcher_gap_skills(matches_db_path, profile_id)
    return merge_gap_skills(manual, matcher_ranked)
