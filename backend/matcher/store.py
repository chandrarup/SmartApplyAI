"""Gate and persist matcher results."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(path: str | Path) -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id TEXT NOT NULL,
            source_ats TEXT NOT NULL,
            external_id TEXT NOT NULL,
            company TEXT,
            title TEXT,
            apply_url TEXT,
            stage1_score REAL,
            stage2_score REAL,
            match_pct INTEGER NOT NULL,
            fit_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(profile_id, source_ats, external_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_matches_profile_match ON matches(profile_id, match_pct DESC)"
    )
    return conn


def gate_and_store(
    matches_db_path: str | Path,
    profile_id: str,
    fitted: list[dict[str, Any]],
    match_threshold: int = 85,
) -> int:
    survivors = [row for row in fitted if int(row.get("match_pct", 0)) >= int(match_threshold)]
    if not survivors:
        return 0

    with _connect(matches_db_path) as conn:
        for row in survivors:
            job = row["job"]
            conn.execute(
                """
                INSERT INTO matches (
                    profile_id, source_ats, external_id, company, title, apply_url,
                    stage1_score, stage2_score, match_pct, fit_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_id, source_ats, external_id) DO UPDATE SET
                    company = excluded.company,
                    title = excluded.title,
                    apply_url = excluded.apply_url,
                    stage1_score = excluded.stage1_score,
                    stage2_score = excluded.stage2_score,
                    match_pct = excluded.match_pct,
                    fit_json = excluded.fit_json,
                    created_at = excluded.created_at
                """,
                (
                    profile_id,
                    job.get("source_ats", ""),
                    job.get("external_id", ""),
                    job.get("company", ""),
                    job.get("title", ""),
                    job.get("apply_url", ""),
                    float(row.get("stage1_score", 0.0) or 0.0),
                    float(row.get("stage2_score", 0.0) or 0.0),
                    int(row.get("match_pct", 0) or 0),
                    json.dumps(row.get("fit", {}), ensure_ascii=False),
                    _utc_now(),
                ),
            )
        conn.commit()
    return len(survivors)

