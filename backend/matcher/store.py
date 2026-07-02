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
            band TEXT NOT NULL DEFAULT 'stretch',
            fit_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(profile_id, source_ats, external_id)
        )
        """
    )
    try:  # pre-band databases
        conn.execute("ALTER TABLE matches ADD COLUMN band TEXT NOT NULL DEFAULT 'stretch'")
    except sqlite3.OperationalError:
        pass
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_matches_profile_match ON matches(profile_id, match_pct DESC)"
    )
    return conn


def band_for(match_pct: int, strong_threshold: int = 85) -> str:
    # CLAUDE.md rule 10: Strong = 85+, Stretch = 70–84
    return "strong" if int(match_pct) >= int(strong_threshold) else "stretch"


def gate_and_store(
    matches_db_path: str | Path,
    profile_id: str,
    fitted: list[dict[str, Any]],
    match_threshold: int = 70,
    strong_threshold: int = 85,
) -> dict[str, int]:
    survivors = [row for row in fitted if int(row.get("match_pct", 0)) >= int(match_threshold)]
    counts = {"stored": 0, "strong": 0, "stretch": 0}
    if not survivors:
        return counts

    with _connect(matches_db_path) as conn:
        for row in survivors:
            job = row["job"]
            band = band_for(row.get("match_pct", 0), strong_threshold)
            counts[band] += 1
            conn.execute(
                """
                INSERT INTO matches (
                    profile_id, source_ats, external_id, company, title, apply_url,
                    stage1_score, stage2_score, match_pct, band, fit_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_id, source_ats, external_id) DO UPDATE SET
                    company = excluded.company,
                    title = excluded.title,
                    apply_url = excluded.apply_url,
                    stage1_score = excluded.stage1_score,
                    stage2_score = excluded.stage2_score,
                    match_pct = excluded.match_pct,
                    band = excluded.band,
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
                    band,
                    json.dumps(row.get("fit", {}), ensure_ascii=False),
                    _utc_now(),
                ),
            )
        conn.commit()
    counts["stored"] = len(survivors)
    return counts

