"""tracker.db — application rows, status history, and outcome analytics.

Mirrors the matcher store idioms (`_connect` + idempotent schema). One SQLite file
owned by this module (rule 8); path overridable via TRACKER_DB_PATH so it moves
laptop→VM by config. Rows carry a link to the exact resume artifact
(`resume_variant_id`) so Chandra can always see what a company actually received.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any

try:
    from .config import ALL_STATUSES, CALLBACK_STATUSES, STATUS_APPLIED, STATUS_APPROVED
except ImportError:  # pragma: no cover - import as top-level package
    from tracker.config import ALL_STATUSES, CALLBACK_STATUSES, STATUS_APPLIED, STATUS_APPROVED


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_db_path() -> str:
    env_path = (os.getenv("TRACKER_DB_PATH") or "").strip()
    if env_path:
        return env_path
    return os.path.join(os.path.dirname(__file__), "tracker.db")


# ── normalization for dedupe ──────────────────────────────────────────────────
_COMPANY_SUFFIXES = re.compile(
    r"\b(inc|inc\.|llc|l\.l\.c|ltd|ltd\.|corp|corporation|co|company|gmbh|plc|"
    r"technologies|technology|labs|holdings)\b",
    re.IGNORECASE,
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_company(name: str) -> str:
    s = (name or "").lower()
    s = _COMPANY_SUFFIXES.sub(" ", s)
    s = _NON_ALNUM.sub(" ", s)
    return " ".join(s.split())


def normalize_title(title: str) -> str:
    s = (title or "").lower()
    s = _NON_ALNUM.sub(" ", s)
    return " ".join(s.split())


# ── connection / schema ───────────────────────────────────────────────────────
_APP_COLUMNS: tuple[tuple[str, str], ...] = (
    ("company_norm", "TEXT"),
    ("title_norm", "TEXT"),
    ("band", "TEXT"),
    ("match_pct", "INTEGER"),
    ("resume_variant_id", "TEXT"),
    ("jd_text", "TEXT"),
    ("answers_json", "TEXT"),
    ("match_ref", "TEXT"),
    ("date_approved", "TEXT"),
    ("date_released", "TEXT"),
)


def _connect(path: str | Path | None = None) -> sqlite3.Connection:
    db_path = Path(path or default_db_path())
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS applications (
            id TEXT PRIMARY KEY,
            profile_id TEXT NOT NULL,
            company TEXT NOT NULL,
            role TEXT NOT NULL,
            company_norm TEXT,
            title_norm TEXT,
            band TEXT,
            match_pct INTEGER,
            platform TEXT DEFAULT 'Other',
            status TEXT NOT NULL DEFAULT 'approved',
            resume_variant_id TEXT,
            jd_text TEXT,
            answers_json TEXT,
            match_ref TEXT,
            location TEXT,
            salary TEXT,
            url TEXT,
            notes TEXT,
            date_approved TEXT,
            date_released TEXT,
            date_applied TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    for col, decl in _APP_COLUMNS:  # upgrade older databases in place
        try:
            conn.execute(f"ALTER TABLE applications ADD COLUMN {col} {decl}")
        except sqlite3.OperationalError:
            pass
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS status_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            application_id TEXT NOT NULL,
            from_status TEXT,
            to_status TEXT NOT NULL,
            ts TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_apps_profile_status ON applications(profile_id, status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_apps_company ON applications(profile_id, company_norm)"
    )
    return conn


# ── serialization ─────────────────────────────────────────────────────────────
def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    aj = d.get("answers_json")
    if aj:
        try:
            d["answers"] = json.loads(aj)
        except (json.JSONDecodeError, TypeError):
            d["answers"] = {}
    else:
        d["answers"] = {}
    return d


# ── CRUD ──────────────────────────────────────────────────────────────────────
def create_application(
    profile_id: str, fields: dict[str, Any], *, db_path: str | Path | None = None
) -> dict[str, Any]:
    now = _utc_now()
    company = str(fields.get("company", "") or "")
    role = str(fields.get("role", "") or "")
    status = str(fields.get("status") or STATUS_APPROVED)
    answers = fields.get("answers")
    row = {
        "id": str(fields.get("id") or uuid.uuid4()),
        "profile_id": profile_id,
        "company": company,
        "role": role,
        "company_norm": normalize_company(company),
        "title_norm": normalize_title(role),
        "band": fields.get("band"),
        "match_pct": fields.get("match_pct"),
        "platform": str(fields.get("platform") or "Other"),
        "status": status,
        "resume_variant_id": fields.get("resume_variant_id"),
        "jd_text": fields.get("jd_text"),
        "answers_json": json.dumps(answers, ensure_ascii=False) if answers is not None else None,
        "match_ref": fields.get("match_ref"),
        "location": fields.get("location", ""),
        "salary": fields.get("salary", ""),
        "url": fields.get("url", ""),
        "notes": fields.get("notes", ""),
        "date_approved": fields.get("date_approved") or (now if status == STATUS_APPROVED else None),
        "date_released": fields.get("date_released"),
        "date_applied": fields.get("date_applied") or (now if status == STATUS_APPLIED else None),
        "created_at": fields.get("created_at") or now,
        "updated_at": now,
    }
    with _connect(db_path) as conn:
        conn.execute(
            f"""
            INSERT INTO applications ({", ".join(row.keys())})
            VALUES ({", ".join("?" for _ in row)})
            """,
            list(row.values()),
        )
        conn.execute(
            "INSERT INTO status_history (application_id, from_status, to_status, ts) VALUES (?,?,?,?)",
            (row["id"], None, status, now),
        )
        conn.commit()
    return get_application(profile_id, row["id"], db_path=db_path)  # type: ignore[return-value]


def get_application(
    profile_id: str, app_id: str, *, db_path: str | Path | None = None
) -> dict[str, Any] | None:
    with _connect(db_path) as conn:
        r = conn.execute(
            "SELECT * FROM applications WHERE profile_id = ? AND id = ?",
            (profile_id, app_id),
        ).fetchone()
    return _row_to_dict(r) if r else None


def list_applications(
    profile_id: str, status: str | None = None, *, db_path: str | Path | None = None
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM applications WHERE profile_id = ?"
    params: list[Any] = [profile_id]
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY COALESCE(date_applied, date_approved, created_at) DESC, created_at DESC"
    with _connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


# Fields a plain PATCH may touch (status goes through update_status for history).
_UPDATABLE = {
    "company", "role", "platform", "salary", "location", "url", "notes",
    "resume_variant_id", "band", "match_pct", "date_applied", "date_released",
    "date_approved",
}


def update_application(
    profile_id: str, app_id: str, fields: dict[str, Any], *, db_path: str | Path | None = None
) -> dict[str, Any] | None:
    existing = get_application(profile_id, app_id, db_path=db_path)
    if not existing:
        return None
    # Status transitions are audited separately.
    if "status" in fields and fields["status"] != existing["status"]:
        update_status(profile_id, app_id, fields["status"], db_path=db_path)
    sets: dict[str, Any] = {}
    for k, v in fields.items():
        if k in _UPDATABLE:
            sets[k] = v
    if "answers" in fields:
        sets["answers_json"] = json.dumps(fields["answers"], ensure_ascii=False)
    if "company" in sets:
        sets["company_norm"] = normalize_company(str(sets["company"]))
    if "role" in sets:
        sets["title_norm"] = normalize_title(str(sets["role"]))
    if sets:
        sets["updated_at"] = _utc_now()
        assignments = ", ".join(f"{k} = ?" for k in sets)
        with _connect(db_path) as conn:
            conn.execute(
                f"UPDATE applications SET {assignments} WHERE profile_id = ? AND id = ?",
                [*sets.values(), profile_id, app_id],
            )
            conn.commit()
    return get_application(profile_id, app_id, db_path=db_path)


def update_status(
    profile_id: str, app_id: str, new_status: str, *, db_path: str | Path | None = None
) -> dict[str, Any] | None:
    if new_status not in ALL_STATUSES:
        raise ValueError(f"unknown status: {new_status!r}")
    existing = get_application(profile_id, app_id, db_path=db_path)
    if not existing:
        return None
    now = _utc_now()
    extra = {}
    if new_status == STATUS_APPLIED and not existing.get("date_applied"):
        extra["date_applied"] = now
    with _connect(db_path) as conn:
        assignments = "status = ?, updated_at = ?" + (
            ", date_applied = ?" if "date_applied" in extra else ""
        )
        params = [new_status, now]
        if "date_applied" in extra:
            params.append(extra["date_applied"])
        params += [profile_id, app_id]
        conn.execute(
            f"UPDATE applications SET {assignments} WHERE profile_id = ? AND id = ?",
            params,
        )
        conn.execute(
            "INSERT INTO status_history (application_id, from_status, to_status, ts) VALUES (?,?,?,?)",
            (app_id, existing["status"], new_status, now),
        )
        conn.commit()
    return get_application(profile_id, app_id, db_path=db_path)


def delete_application(
    profile_id: str, app_id: str, *, db_path: str | Path | None = None
) -> bool:
    with _connect(db_path) as conn:
        cur = conn.execute(
            "DELETE FROM applications WHERE profile_id = ? AND id = ?",
            (profile_id, app_id),
        )
        conn.execute("DELETE FROM status_history WHERE application_id = ?", (app_id,))
        conn.commit()
        return cur.rowcount > 0


def status_history(
    profile_id: str, app_id: str, *, db_path: str | Path | None = None
) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT h.* FROM status_history h
            JOIN applications a ON a.id = h.application_id
            WHERE a.profile_id = ? AND h.application_id = ?
            ORDER BY h.id ASC
            """,
            (profile_id, app_id),
        ).fetchall()
    return [dict(r) for r in rows]


# ── one-time migration from legacy applications.json ──────────────────────────
# Map the old free-form status labels onto the new pipeline vocabulary.
_LEGACY_STATUS_MAP = {
    "applied": STATUS_APPLIED,
    "phone screen": "screen",
    "screen": "screen",
    "oa": "screen",
    "interview": "interview",
    "offer": "offer",
    "rejected": "rejected",
    "withdrawn": "ghosted",
    "ghosted": "ghosted",
}


def migrate_from_json(
    profile_id: str, apps: list[dict[str, Any]], *, db_path: str | Path | None = None
) -> int:
    """Import legacy applications.json rows once (idempotent: skips if any rows exist)."""
    if not apps:
        return 0
    if list_applications(profile_id, db_path=db_path):
        return 0  # already migrated / has data — never double-import
    count = 0
    for a in apps:
        status = _LEGACY_STATUS_MAP.get(str(a.get("status", "")).strip().lower(), STATUS_APPLIED)
        create_application(
            profile_id,
            {
                "id": a.get("id"),
                "company": a.get("company", ""),
                "role": a.get("role", ""),
                "platform": a.get("platform", "Other"),
                "status": status,
                "salary": a.get("salary", ""),
                "location": a.get("location", ""),
                "url": a.get("url", ""),
                "notes": a.get("notes", ""),
                "date_applied": a.get("date_applied"),
            },
            db_path=db_path,
        )
        count += 1
    return count


# ── analytics: callback rate by band and by company ───────────────────────────
def analytics(profile_id: str, *, db_path: str | Path | None = None) -> dict[str, Any]:
    """Callback rate = (# reached screen+) / (# applied-or-beyond), grouped by band and company.

    Denominator counts anything the human actually submitted (applied and later
    states), so pre-release approvals don't dilute the rate.
    """
    apps = list_applications(profile_id, db_path=db_path)
    applied_states = {STATUS_APPLIED, "confirmed", "screen", "interview", "offer", "rejected", "ghosted"}

    def _bucket() -> dict[str, int]:
        return {"applied": 0, "callbacks": 0}

    by_band: dict[str, dict[str, int]] = {}
    by_company: dict[str, dict[str, Any]] = {}
    for a in apps:
        if a["status"] not in applied_states:
            continue
        is_cb = a["status"] in CALLBACK_STATUSES
        band = a.get("band") or "unknown"
        b = by_band.setdefault(band, _bucket())
        b["applied"] += 1
        b["callbacks"] += 1 if is_cb else 0
        company = a.get("company") or "—"
        c = by_company.setdefault(company, {"company": company, **_bucket()})
        c["applied"] += 1
        c["callbacks"] += 1 if is_cb else 0

    def _rate(bucket: dict[str, int]) -> float:
        return round(bucket["callbacks"] / bucket["applied"], 4) if bucket["applied"] else 0.0

    band_rows = [
        {"band": band, "applied": v["applied"], "callbacks": v["callbacks"], "callback_rate": _rate(v)}
        for band, v in sorted(by_band.items())
    ]
    company_rows = [
        {**v, "callback_rate": _rate(v)}
        for v in sorted(by_company.values(), key=lambda x: (-x["applied"], x["company"]))
    ]
    total_applied = sum(v["applied"] for v in by_band.values())
    total_cb = sum(v["callbacks"] for v in by_band.values())
    return {
        "by_band": band_rows,
        "by_company": company_rows,
        "overall": {
            "applied": total_applied,
            "callbacks": total_cb,
            "callback_rate": round(total_cb / total_applied, 4) if total_applied else 0.0,
        },
    }
