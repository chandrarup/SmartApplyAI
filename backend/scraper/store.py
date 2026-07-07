"""SQLite persistence for unified job records."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any, Iterator

DB_PATH = Path(__file__).resolve().parent / "jobs.db"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_conn(db_path: Path | str = DB_PATH) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        init_db(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            source_ats TEXT NOT NULL,
            company TEXT NOT NULL,
            external_id TEXT NOT NULL,
            title TEXT,
            location TEXT,
            remote_flag INTEGER NOT NULL DEFAULT 0,
            is_internship INTEGER NOT NULL DEFAULT 0,
            location_match INTEGER NOT NULL DEFAULT 0,
            sponsorship_knockout INTEGER NOT NULL DEFAULT 0,
            department TEXT,
            description_text TEXT,
            apply_url TEXT,
            posted_at TEXT,
            updated_at TEXT,
            raw_json TEXT NOT NULL,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('active', 'expired')),
            PRIMARY KEY (source_ats, external_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_jobs_source_company_status ON jobs(source_ats, company, status)"
    )
    _ensure_jobs_columns(conn)


def _ensure_jobs_columns(conn: sqlite3.Connection) -> None:
    required_int_columns: dict[str, str] = {
        "is_internship": "0",
        "location_match": "0",
        "sponsorship_knockout": "0",
    }
    table_info = conn.execute("PRAGMA table_info(jobs)").fetchall()
    existing_columns = {str(row["name"]) for row in table_info}
    for column_name, default_value in required_int_columns.items():
        if column_name in existing_columns:
            continue
        conn.execute(
            f"ALTER TABLE jobs ADD COLUMN {column_name} INTEGER NOT NULL DEFAULT {default_value}"
        )


def _existing_row(conn: sqlite3.Connection, source_ats: str, external_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM jobs WHERE source_ats = ? AND external_id = ?",
        (source_ats, external_id),
    ).fetchone()


def _is_changed(existing: sqlite3.Row, normalized_job: dict[str, Any]) -> bool:
    compare_fields = (
        "company",
        "title",
        "location",
        "remote_flag",
        "is_internship",
        "location_match",
        "sponsorship_knockout",
        "department",
        "description_text",
        "apply_url",
        "posted_at",
        "updated_at",
        "raw_json",
    )
    for field in compare_fields:
        existing_value = existing[field]
        incoming_value = normalized_job[field]
        if field in {"remote_flag", "is_internship", "location_match", "sponsorship_knockout"}:
            existing_value = int(existing_value)
            incoming_value = int(bool(incoming_value))
        if existing_value != incoming_value:
            return True
    return False


def upsert_company_jobs(
    conn: sqlite3.Connection, source_ats: str, company_scope: str, normalized_jobs: list[dict[str, Any]]
) -> dict[str, int]:
    now = utc_now_iso()
    inserted = 0
    updated = 0
    seen_ids: set[str] = set()

    for job in normalized_jobs:
        external_id = job["external_id"]
        seen_ids.add(external_id)
        existing = _existing_row(conn, source_ats, external_id)
        if not existing:
            conn.execute(
                """
                INSERT INTO jobs (
                    source_ats, company, external_id, title, location, remote_flag, is_internship,
                    location_match, sponsorship_knockout, department,
                    description_text, apply_url, posted_at, updated_at, raw_json,
                    first_seen, last_seen, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
                """,
                (
                    source_ats,
                    job["company"],
                    external_id,
                    job["title"],
                    job["location"],
                    int(bool(job["remote_flag"])),
                    int(bool(job["is_internship"])),
                    int(bool(job["location_match"])),
                    int(bool(job["sponsorship_knockout"])),
                    job["department"],
                    job["description_text"],
                    job["apply_url"],
                    job["posted_at"],
                    job["updated_at"],
                    job["raw_json"],
                    now,
                    now,
                ),
            )
            inserted += 1
            continue

        changed = _is_changed(existing, job) or existing["status"] != "active"
        conn.execute(
            """
            UPDATE jobs
            SET company = ?, title = ?, location = ?, remote_flag = ?, is_internship = ?,
                location_match = ?, sponsorship_knockout = ?, department = ?,
                description_text = ?, apply_url = ?, posted_at = ?, updated_at = ?, raw_json = ?,
                last_seen = ?, status = 'active'
            WHERE source_ats = ? AND external_id = ?
            """,
            (
                job["company"],
                job["title"],
                job["location"],
                int(bool(job["remote_flag"])),
                int(bool(job["is_internship"])),
                int(bool(job["location_match"])),
                int(bool(job["sponsorship_knockout"])),
                job["department"],
                job["description_text"],
                job["apply_url"],
                job["posted_at"],
                job["updated_at"],
                job["raw_json"],
                now,
                source_ats,
                external_id,
            ),
        )
        if changed:
            updated += 1

    if seen_ids:
        placeholders = ",".join("?" for _ in seen_ids)
        params = [source_ats, company_scope, *sorted(seen_ids)]
        query = f"""
            UPDATE jobs
            SET status = 'expired'
            WHERE source_ats = ?
              AND company = ?
              AND status = 'active'
              AND external_id NOT IN ({placeholders})
        """
        cursor = conn.execute(query, params)
    else:
        cursor = conn.execute(
            """
            UPDATE jobs
            SET status = 'expired'
            WHERE source_ats = ?
              AND company = ?
              AND status = 'active'
            """,
            (source_ats, company_scope),
        )

    expired = cursor.rowcount if cursor.rowcount != -1 else 0
    return {"new": inserted, "updated": updated, "expired": expired}


def count_all_rows(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS c FROM jobs").fetchone()
    return int(row["c"]) if row else 0



def stats(db_path: Path | str = DB_PATH) -> dict[str, Any]:
    """Read-only jobs.db summary for the dashboard sourcing page."""
    with get_conn(db_path) as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active,
                SUM(CASE WHEN status = 'active' AND is_internship = 1 THEN 1 ELSE 0 END) AS internships,
                SUM(CASE WHEN status = 'active' AND location_match = 1 THEN 1 ELSE 0 END) AS location_matches,
                SUM(CASE WHEN first_seen >= datetime('now', '-1 day') THEN 1 ELSE 0 END) AS new_24h,
                COUNT(DISTINCT company) AS companies,
                MAX(last_seen) AS last_seen
            FROM jobs
            """
        ).fetchone()
    return {
        "total": row["total"] or 0,
        "active": row["active"] or 0,
        "internships": row["internships"] or 0,
        "location_matches": row["location_matches"] or 0,
        "new_24h": row["new_24h"] or 0,
        "companies": row["companies"] or 0,
        "last_seen": row["last_seen"],
    }
