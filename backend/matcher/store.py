"""Gate and persist matcher results."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# M6 queue columns added to matches over time; each is an idempotent ALTER so old
# databases upgrade in place (same pattern as the original `band` migration).
_QUEUE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("band", "TEXT NOT NULL DEFAULT 'stretch'"),
    ("jd_text", "TEXT"),
    ("tailor_status", "TEXT NOT NULL DEFAULT 'pending'"),  # pending | tailored | failed
    ("tailored_json", "TEXT"),
    ("tailor_error", "TEXT"),
    ("review_status", "TEXT NOT NULL DEFAULT 'new'"),  # new | customized | skipped | applied
    ("tailored_at", "TEXT"),
    ("resume_variant_id", "TEXT"),
)


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
            jd_text TEXT,
            tailor_status TEXT NOT NULL DEFAULT 'pending',
            tailored_json TEXT,
            tailor_error TEXT,
            review_status TEXT NOT NULL DEFAULT 'new',
            tailored_at TEXT,
            fit_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(profile_id, source_ats, external_id)
        )
        """
    )
    for col, decl in _QUEUE_COLUMNS:  # upgrade pre-existing databases in place
        try:
            conn.execute(f"ALTER TABLE matches ADD COLUMN {col} {decl}")
        except sqlite3.OperationalError:
            pass
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_matches_profile_match ON matches(profile_id, match_pct DESC)"
    )
    # Migrate legacy review_status vocabulary.
    try:
        conn.execute(
            "UPDATE matches SET review_status = 'customized' WHERE review_status = 'approved'"
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass
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
                    stage1_score, stage2_score, match_pct, band, jd_text, fit_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_id, source_ats, external_id) DO UPDATE SET
                    company = excluded.company,
                    title = excluded.title,
                    apply_url = excluded.apply_url,
                    stage1_score = excluded.stage1_score,
                    stage2_score = excluded.stage2_score,
                    match_pct = excluded.match_pct,
                    band = excluded.band,
                    jd_text = excluded.jd_text,
                    fit_json = excluded.fit_json,
                    created_at = excluded.created_at
                """,
                # Re-matching updates the JD/score but deliberately preserves
                # tailor_status / tailored_json / review_status so a night's review
                # progress is never wiped by the next matcher run (rule 7).
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
                    job.get("description_text", "") or "",
                    json.dumps(row.get("fit", {}), ensure_ascii=False),
                    _utc_now(),
                ),
            )
        conn.commit()
    counts["stored"] = len(survivors)
    return counts


# ── Queue helpers (M6 review queue) ───────────────────────────────────────────

def _row_to_queue_item(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    try:
        d["fit"] = json.loads(d.get("fit_json") or "{}")
    except (json.JSONDecodeError, TypeError):
        d["fit"] = {}
    tj = d.get("tailored_json")
    if tj:
        try:
            d["tailored"] = json.loads(tj)
        except (json.JSONDecodeError, TypeError):
            d["tailored"] = None
    else:
        d["tailored"] = None
    return d


def list_queue(
    matches_db_path: str | Path,
    profile_id: str,
    band: str | None = None,
    review_status: str | None = None,
    *,
    active_only: bool = True,
) -> list[dict[str, Any]]:
    """Return queue items (matches) for a profile, Strong band first then by match_pct."""
    sql = "SELECT * FROM matches WHERE profile_id = ?"
    params: list[Any] = [profile_id]
    if active_only:
        sql += " AND review_status NOT IN ('applied', 'skipped')"
    if band:
        sql += " AND band = ?"
        params.append(band)
    if review_status:
        sql += " AND review_status = ?"
        params.append(review_status)
    # Strong band first (rule 10 review order), then highest match %, then newest.
    sql += " ORDER BY CASE band WHEN 'strong' THEN 0 ELSE 1 END, match_pct DESC, id DESC"
    with _connect(matches_db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_queue_item(r) for r in rows]


def get_queue_item(
    matches_db_path: str | Path, profile_id: str, match_id: int
) -> dict[str, Any] | None:
    with _connect(matches_db_path) as conn:
        row = conn.execute(
            "SELECT * FROM matches WHERE profile_id = ? AND id = ?",
            (profile_id, int(match_id)),
        ).fetchone()
    return _row_to_queue_item(row) if row else None


def list_pending_tailoring(
    matches_db_path: str | Path, profile_id: str | None = None
) -> list[dict[str, Any]]:
    """Queue items still needing an overnight tailoring pass."""
    sql = "SELECT * FROM matches WHERE tailor_status = 'pending'"
    params: list[Any] = []
    if profile_id:
        sql += " AND profile_id = ?"
        params.append(profile_id)
    sql += " ORDER BY band ASC, match_pct DESC, id DESC"
    with _connect(matches_db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_queue_item(r) for r in rows]


def set_tailoring(
    matches_db_path: str | Path,
    match_id: int,
    *,
    status: str,
    tailored: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Record the outcome of a tailoring attempt on a queue item (fail-loud, rule 7)."""
    with _connect(matches_db_path) as conn:
        conn.execute(
            """
            UPDATE matches
               SET tailor_status = ?,
                   tailored_json = ?,
                   tailor_error = ?,
                   tailored_at = ?
             WHERE id = ?
            """,
            (
                status,
                json.dumps(tailored, ensure_ascii=False) if tailored is not None else None,
                error,
                _utc_now(),
                int(match_id),
            ),
        )
        conn.commit()


def set_review_status(
    matches_db_path: str | Path, match_id: int, review_status: str
) -> None:
    with _connect(matches_db_path) as conn:
        conn.execute(
            "UPDATE matches SET review_status = ? WHERE id = ?",
            (review_status, int(match_id)),
        )
        conn.commit()


def set_customized(
    matches_db_path: str | Path,
    match_id: int,
    *,
    resume_variant_id: str,
) -> None:
    """Mark a queue item reviewed: PDF ready, stays in queue until form submit."""
    with _connect(matches_db_path) as conn:
        conn.execute(
            """
            UPDATE matches
               SET review_status = 'customized',
                   resume_variant_id = ?
             WHERE id = ?
            """,
            (resume_variant_id, int(match_id)),
        )
        conn.commit()


def list_customized(
    matches_db_path: str | Path, profile_id: str
) -> list[dict[str, Any]]:
    """Queue items ready for extension autofill (customized + variant PDF)."""
    with _connect(matches_db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM matches
             WHERE profile_id = ?
               AND review_status = 'customized'
               AND resume_variant_id IS NOT NULL
               AND resume_variant_id != ''
             ORDER BY CASE band WHEN 'strong' THEN 0 ELSE 1 END, match_pct DESC, id DESC
            """,
            (profile_id,),
        ).fetchall()
    return [_row_to_queue_item(r) for r in rows]

