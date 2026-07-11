"""SQLite persistence for unified job records."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import json
import re
import sqlite3
from typing import Any, Iterator

DB_PATH = Path(__file__).resolve().parent / "jobs.db"

# Sources that are always deduped away in favor of a real ATS posting of the
# same role. The GitHub internship tracker aggregates links that usually also
# live on a company ATS board; the ATS row is canonical.
LOW_PRIORITY_SOURCES: frozenset[str] = frozenset({"tracker"})

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^a-z0-9 ]+")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — for dedupe hashing."""
    lowered = _PUNCT_RE.sub(" ", str(text or "").lower())
    return _WS_RE.sub(" ", lowered).strip()


def compute_dedupe_hash(company: str, title: str, location: str, remote_flag: int = 0) -> str:
    """Stable cross-source identity: sha256 of normalized company|title|location
    bucket. Remote roles bucket by "remote" so the same remote posting from two
    sources collides regardless of a differing city string."""
    # Split the raw location on its first comma (the city) BEFORE normalizing —
    # normalization strips commas, so splitting after would never find one.
    loc_bucket = "remote" if int(remote_flag or 0) else _norm(str(location or "").split(",")[0])
    key = f"{_norm(company)}|{_norm(title)}|{loc_bucket}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


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
    _ensure_v3_tables(conn)


def _ensure_v3_tables(conn: sqlite3.Connection) -> None:
    """Sourcing-v3 additive tables: per-source health, run history, and
    cross-source provenance. All CREATE IF NOT EXISTS — safe on a live db."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS source_health (
            source_ats TEXT PRIMARY KEY,
            last_success_at TEXT,
            last_error_at TEXT,
            consecutive_errors INTEGER NOT NULL DEFAULT 0,
            avg_latency_ms REAL,
            cooldown_until TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            mode TEXT NOT NULL DEFAULT 'on_demand',
            jobs_fetched INTEGER DEFAULT 0,
            jobs_new INTEGER DEFAULT 0,
            jobs_updated INTEGER DEFAULT 0,
            jobs_expired INTEGER DEFAULT 0,
            provider_stats_json TEXT,
            anomalies_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS job_sources (
            canonical_source_ats TEXT NOT NULL,
            canonical_external_id TEXT NOT NULL,
            alt_source_ats TEXT NOT NULL,
            alt_external_id TEXT NOT NULL,
            alt_apply_url TEXT,
            first_seen TEXT NOT NULL,
            PRIMARY KEY (alt_source_ats, alt_external_id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_dedupe ON jobs(dedupe_hash)")


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
    # JSON list of search names from searches.yaml (matching-v2 / sourcing-v2).
    if "matched_searches" not in existing_columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN matched_searches TEXT NOT NULL DEFAULT '[]'")
    for col in ("liveness", "liveness_checked_at"):
        if col not in existing_columns:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} TEXT")
    # sourcing-v3 cross-source dedupe key (stamped on upsert; existing rows get
    # it on their next scrape).
    if "dedupe_hash" not in existing_columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN dedupe_hash TEXT")


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
        "matched_searches",
    )
    for field in compare_fields:
        if field == "matched_searches":
            existing_value = existing["matched_searches"] if "matched_searches" in existing.keys() else "[]"
            incoming_value = json.dumps(normalized_job.get("matched_searches") or [], ensure_ascii=True)
            if existing_value != incoming_value:
                return True
            continue
        existing_value = existing[field]
        incoming_value = normalized_job[field]
        if field in {"remote_flag", "is_internship", "location_match", "sponsorship_knockout"}:
            existing_value = int(existing_value)
            incoming_value = int(bool(incoming_value))
        if existing_value != incoming_value:
            return True
    return False


def _find_canonical(
    conn: sqlite3.Connection, dedupe_hash: str, exclude_source: str, exclude_id: str
) -> sqlite3.Row | None:
    """An active, higher-priority (non-tracker) row sharing this dedupe hash."""
    placeholders = ",".join("?" for _ in LOW_PRIORITY_SOURCES)
    return conn.execute(
        f"""
        SELECT source_ats, external_id, apply_url FROM jobs
        WHERE dedupe_hash = ? AND status = 'active'
          AND source_ats NOT IN ({placeholders})
          AND NOT (source_ats = ? AND external_id = ?)
        LIMIT 1
        """,
        (dedupe_hash, *sorted(LOW_PRIORITY_SOURCES), exclude_source, exclude_id),
    ).fetchone()


def _record_job_source(
    conn: sqlite3.Connection, canonical: tuple[str, str], alt: tuple[str, str],
    alt_apply_url: str, now: str
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO job_sources (
            canonical_source_ats, canonical_external_id,
            alt_source_ats, alt_external_id, alt_apply_url, first_seen
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (canonical[0], canonical[1], alt[0], alt[1], alt_apply_url, now),
    )


def _supersede_trackers(
    conn: sqlite3.Connection, dedupe_hash: str, canonical_source: str,
    canonical_id: str, now: str
) -> int:
    """Flip active low-priority (tracker) rows with this hash to expired and
    record them as alternate sources of the canonical ATS posting."""
    placeholders = ",".join("?" for _ in LOW_PRIORITY_SOURCES)
    rows = conn.execute(
        f"""
        SELECT source_ats, external_id, apply_url FROM jobs
        WHERE dedupe_hash = ? AND status = 'active'
          AND source_ats IN ({placeholders})
        """,
        (dedupe_hash, *sorted(LOW_PRIORITY_SOURCES)),
    ).fetchall()
    count = 0
    for row in rows:
        _record_job_source(
            conn, (canonical_source, canonical_id),
            (row["source_ats"], row["external_id"]), row["apply_url"] or "", now,
        )
        conn.execute(
            "UPDATE jobs SET status = 'expired' WHERE source_ats = ? AND external_id = ?",
            (row["source_ats"], row["external_id"]),
        )
        count += 1
    return count


def upsert_company_jobs(
    conn: sqlite3.Connection, source_ats: str, company_scope: str, normalized_jobs: list[dict[str, Any]]
) -> dict[str, int]:
    now = utc_now_iso()
    inserted = 0
    updated = 0
    suppressed = 0
    superseded = 0
    seen_ids: set[str] = set()
    is_low_priority = source_ats in LOW_PRIORITY_SOURCES

    for job in normalized_jobs:
        external_id = job["external_id"]
        existing = _existing_row(conn, source_ats, external_id)
        matched_json = json.dumps(job.get("matched_searches") or [], ensure_ascii=True)
        dedupe_hash = compute_dedupe_hash(
            job["company"], job["title"], job["location"], job.get("remote_flag") or 0
        )

        # Cross-source dedupe: a tracker job that duplicates a live ATS posting
        # is suppressed (the ATS row is canonical). Not added to seen_ids so
        # expire-via-absence leaves it alone.
        if is_low_priority:
            canonical = _find_canonical(conn, dedupe_hash, source_ats, external_id)
            if canonical is not None:
                _record_job_source(
                    conn, (canonical["source_ats"], canonical["external_id"]),
                    (source_ats, external_id), job.get("apply_url") or "", now,
                )
                if existing is not None and existing["status"] == "active":
                    conn.execute(
                        "UPDATE jobs SET status = 'expired', dedupe_hash = ? "
                        "WHERE source_ats = ? AND external_id = ?",
                        (dedupe_hash, source_ats, external_id),
                    )
                suppressed += 1
                continue

        seen_ids.add(external_id)
        if not existing:
            conn.execute(
                """
                INSERT INTO jobs (
                    source_ats, company, external_id, title, location, remote_flag, is_internship,
                    location_match, sponsorship_knockout, department,
                    description_text, apply_url, posted_at, updated_at, raw_json,
                    first_seen, last_seen, status, matched_searches, dedupe_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
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
                    matched_json,
                    dedupe_hash,
                ),
            )
            inserted += 1
        else:
            changed = _is_changed(existing, job) or existing["status"] != "active"
            conn.execute(
                """
                UPDATE jobs
                SET company = ?, title = ?, location = ?, remote_flag = ?, is_internship = ?,
                    location_match = ?, sponsorship_knockout = ?, department = ?,
                    description_text = ?, apply_url = ?, posted_at = ?, updated_at = ?, raw_json = ?,
                    last_seen = ?, status = 'active', matched_searches = ?, dedupe_hash = ?
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
                    matched_json,
                    dedupe_hash,
                    source_ats,
                    external_id,
                ),
            )
            if changed:
                updated += 1

        # A real ATS posting supersedes any tracker duplicates of the same role.
        if not is_low_priority:
            superseded += _supersede_trackers(conn, dedupe_hash, source_ats, external_id, now)

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
    return {
        "new": inserted,
        "updated": updated,
        "expired": expired,
        "suppressed": suppressed,
        "superseded": superseded,
    }


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


# ── "Latest jobs" freshness view (spec §5.3) ──────────────────────────────────

def latest_jobs(
    db_path: Path | str = DB_PATH,
    *,
    hours_first_seen: int = 72,
    days_posted: int | None = None,
    keywords: list[str] | None = None,
    companies: list[str] | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Active jobs first seen within the window, newest first.

    posted_at is often NULL from ATS feeds, so the optional days_posted filter
    keeps NULL-posted rows rather than dropping them. keywords match (case-
    insensitively) against title OR description_text (any term)."""
    where = ["status = 'active'", "first_seen >= ?"]
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=int(hours_first_seen))).isoformat()
    params: list[Any] = [cutoff]

    if days_posted is not None:
        posted_cutoff = (datetime.now(timezone.utc) - timedelta(days=int(days_posted))).isoformat()
        where.append("(posted_at IS NULL OR posted_at >= ?)")
        params.append(posted_cutoff)

    if companies:
        where.append("(" + " OR ".join("company = ?" for _ in companies) + ")")
        params.extend(companies)

    if keywords:
        kw_clauses = []
        for kw in keywords:
            kw_clauses.append("(LOWER(title) LIKE ? OR LOWER(description_text) LIKE ?)")
            like = f"%{str(kw).lower()}%"
            params.extend([like, like])
        where.append("(" + " OR ".join(kw_clauses) + ")")

    sql = (
        "SELECT source_ats, company, external_id, title, location, apply_url, "
        "posted_at, first_seen, last_seen, is_internship, matched_searches "
        f"FROM jobs WHERE {' AND '.join(where)} "
        "ORDER BY first_seen DESC LIMIT ?"
    )
    params.append(int(limit))
    with get_conn(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# ── Run history (spec §9) ─────────────────────────────────────────────────────

def record_run_start(db_path: Path | str = DB_PATH, mode: str = "on_demand") -> int:
    with get_conn(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO runs (started_at, mode) VALUES (?, ?)",
            (utc_now_iso(), mode),
        )
        return int(cur.lastrowid)


def record_run_end(
    db_path: Path | str,
    run_id: int,
    totals: dict[str, Any],
    provider_stats: dict[str, Any],
    anomalies: list[dict[str, Any]],
) -> None:
    with get_conn(db_path) as conn:
        conn.execute(
            """
            UPDATE runs SET finished_at = ?, jobs_fetched = ?, jobs_new = ?,
                jobs_updated = ?, jobs_expired = ?, provider_stats_json = ?, anomalies_json = ?
            WHERE id = ?
            """,
            (
                utc_now_iso(),
                int(totals.get("fetched", 0) or 0),
                int(totals.get("new", 0) or 0),
                int(totals.get("updated", 0) or 0),
                int(totals.get("expired", 0) or 0),
                json.dumps(provider_stats, ensure_ascii=True),
                json.dumps(anomalies or [], ensure_ascii=True),
                int(run_id),
            ),
        )


def recent_runs(db_path: Path | str = DB_PATH, limit: int = 5) -> list[dict[str, Any]]:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        for key in ("provider_stats_json", "anomalies_json"):
            try:
                d[key.replace("_json", "")] = json.loads(d.get(key) or ("[]" if "anomalies" in key else "{}"))
            except (json.JSONDecodeError, TypeError):
                d[key.replace("_json", "")] = None
        out.append(d)
    return out


# ── Per-source health + cooldown (spec §1.3 / §7.1) ───────────────────────────

def update_source_health(
    db_path: Path | str,
    source_ats: str,
    *,
    ok: bool,
    latency_ms: float | None = None,
    error: str | None = None,
    cooldown_hours: float = 6.0,
    cooldown_after: int = 3,
) -> None:
    """Record a source's outcome. On success: reset the error streak, clear any
    cooldown, and fold latency into an EMA (0.3 new / 0.7 old). On failure:
    bump the streak and, once it reaches cooldown_after, set a cooldown_until."""
    now = utc_now_iso()
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT consecutive_errors, avg_latency_ms FROM source_health WHERE source_ats = ?",
            (source_ats,),
        ).fetchone()
        prev_errors = int(row["consecutive_errors"]) if row else 0
        prev_latency = row["avg_latency_ms"] if row and row["avg_latency_ms"] is not None else None

        if ok:
            if latency_ms is None:
                new_latency = prev_latency
            elif prev_latency is None:
                new_latency = float(latency_ms)
            else:
                new_latency = 0.3 * float(latency_ms) + 0.7 * float(prev_latency)
            conn.execute(
                """
                INSERT INTO source_health (source_ats, last_success_at, last_error_at,
                    consecutive_errors, avg_latency_ms, cooldown_until, updated_at)
                VALUES (?, ?, NULL, 0, ?, NULL, ?)
                ON CONFLICT(source_ats) DO UPDATE SET
                    last_success_at = excluded.last_success_at,
                    consecutive_errors = 0,
                    avg_latency_ms = excluded.avg_latency_ms,
                    cooldown_until = NULL,
                    updated_at = excluded.updated_at
                """,
                (source_ats, now, new_latency, now),
            )
        else:
            errors = prev_errors + 1
            cooldown_until = None
            if errors >= int(cooldown_after):
                cooldown_until = (
                    datetime.now(timezone.utc) + timedelta(hours=float(cooldown_hours))
                ).isoformat()
            conn.execute(
                """
                INSERT INTO source_health (source_ats, last_success_at, last_error_at,
                    consecutive_errors, avg_latency_ms, cooldown_until, updated_at)
                VALUES (?, NULL, ?, ?, ?, ?, ?)
                ON CONFLICT(source_ats) DO UPDATE SET
                    last_error_at = excluded.last_error_at,
                    consecutive_errors = ?,
                    cooldown_until = excluded.cooldown_until,
                    updated_at = excluded.updated_at
                """,
                (source_ats, now, errors, prev_latency, cooldown_until, now, errors),
            )


def sources_in_cooldown(db_path: Path | str = DB_PATH) -> dict[str, str]:
    """{source_ats: cooldown_until} for sources whose cooldown is still active."""
    now = utc_now_iso()
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT source_ats, cooldown_until FROM source_health "
            "WHERE cooldown_until IS NOT NULL AND cooldown_until > ?",
            (now,),
        ).fetchall()
    return {r["source_ats"]: r["cooldown_until"] for r in rows}
