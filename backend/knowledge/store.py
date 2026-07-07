"""SQLite-backed profile store — drop-in replacement for master_data.json reads/writes."""

from __future__ import annotations

import json
import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

DB_PATH = os.path.join(os.path.dirname(__file__), "knowledge.db")

KNOWN_SECTION_KEYS = frozenset({
    "contact_info",
    "summary",
    "education",
    "experience",
    "projects",
    "skills",
    "publications",
    "certifications",
    "awards",
    "leadership",
    "research_interests",
    "autofill",
    "common_answers",
    "learned_answers",
})

SCHEMA_VERSION = 1


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sections (
            profile_id TEXT NOT NULL,
            key TEXT NOT NULL,
            json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (profile_id, key)
        );
        CREATE TABLE IF NOT EXISTS skills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id TEXT NOT NULL,
            category TEXT NOT NULL,
            name TEXT NOT NULL,
            sort_index INTEGER NOT NULL DEFAULT 0,
            proficiency INTEGER,
            evidence TEXT,
            source TEXT,
            first_seen TEXT,
            last_used TEXT,
            UNIQUE(profile_id, category, name)
        );
        CREATE INDEX IF NOT EXISTS idx_skills_profile_category
            ON skills(profile_id, category, sort_index, id);
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id TEXT NOT NULL,
            ts TEXT NOT NULL,
            source_type TEXT,
            raw_text TEXT,
            extracted_json TEXT,
            status TEXT,
            provenance TEXT
        );
        """
    )
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,))
        conn.commit()


def profile_exists(pid: str) -> bool:
    with _connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM sections WHERE profile_id = ?",
            (pid,),
        ).fetchone()["c"]
        return count > 0


def _section_row(conn: sqlite3.Connection, pid: str, key: str) -> Any | None:
    row = conn.execute(
        "SELECT json FROM sections WHERE profile_id = ? AND key = ?",
        (pid, key),
    ).fetchone()
    if not row:
        return None
    return json.loads(row["json"])


def _write_section(conn: sqlite3.Connection, pid: str, key: str, value: Any) -> None:
    conn.execute(
        """
        INSERT INTO sections(profile_id, key, json, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(profile_id, key) DO UPDATE SET
            json = excluded.json,
            updated_at = excluded.updated_at
        """,
        (pid, key, json.dumps(value, ensure_ascii=False), _utc_now()),
    )


def _sync_skills_table(conn: sqlite3.Connection, pid: str, skills: dict | None) -> int:
    conn.execute("DELETE FROM skills WHERE profile_id = ?", (pid,))
    count = 0
    if not isinstance(skills, dict):
        return count
    now = _utc_now()
    for category, items in skills.items():
        if not isinstance(items, list):
            continue
        for idx, name in enumerate(items):
            if not isinstance(name, str) or not name.strip():
                continue
            conn.execute(
                """
                INSERT INTO skills(
                    profile_id, category, name, sort_index,
                    proficiency, evidence, source, first_seen, last_used
                ) VALUES (?, ?, ?, ?, NULL, NULL, NULL, ?, NULL)
                """,
                (pid, category, name, idx, now),
            )
            count += 1
    return count


def _project_skills(conn: sqlite3.Connection, pid: str) -> dict:
    section_skills = _section_row(conn, pid, "skills")
    category_order: list[str] = []
    if isinstance(section_skills, dict):
        category_order = list(section_skills.keys())

    rows = conn.execute(
        """
        SELECT category, name
        FROM skills
        WHERE profile_id = ?
        ORDER BY category, sort_index, id
        """,
        (pid,),
    ).fetchall()

    by_category: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        by_category[row["category"]].append(row["name"])

    if not by_category:
        return section_skills if isinstance(section_skills, dict) else {}

    projected: dict[str, list[str]] = {}
    for cat in category_order:
        if cat in by_category:
            projected[cat] = by_category[cat]
    for cat, names in by_category.items():
        if cat not in projected:
            projected[cat] = names
    return projected


def get_profile(pid: str) -> dict:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT key, json FROM sections WHERE profile_id = ? ORDER BY rowid",
            (pid,),
        ).fetchall()
        if not rows:
            return {}

        profile: dict[str, Any] = {}
        for row in rows:
            profile[row["key"]] = json.loads(row["json"])

        if "skills" in profile or conn.execute(
            "SELECT 1 FROM skills WHERE profile_id = ? LIMIT 1", (pid,)
        ).fetchone():
            profile["skills"] = _project_skills(conn, pid)

        return profile


def save_profile(pid: str, data: dict) -> None:
    data = data or {}
    with _connect() as conn:
        conn.execute("DELETE FROM sections WHERE profile_id = ?", (pid,))
        conn.execute("DELETE FROM skills WHERE profile_id = ?", (pid,))

        for key, value in data.items():
            _write_section(conn, pid, key, value)

        _sync_skills_table(conn, pid, data.get("skills"))
        conn.commit()


def merge_section(pid: str, key: str, partial: Any) -> None:
    with _connect() as conn:
        current = _section_row(conn, pid, key)
        if key == "skills":
            base = current if isinstance(current, dict) else {}
            patch = partial if isinstance(partial, dict) else {}
            merged = {**base, **patch}
        else:
            base = current if isinstance(current, dict) else {}
            patch = partial if isinstance(partial, dict) else {}
            merged = {**base, **patch}

        _write_section(conn, pid, key, merged)
        if key == "skills":
            _sync_skills_table(conn, pid, merged)
        conn.commit()


def replace_section(pid: str, key: str, value: Any) -> None:
    with _connect() as conn:
        _write_section(conn, pid, key, value)
        if key == "skills":
            _sync_skills_table(conn, pid, value if isinstance(value, dict) else {})
        conn.commit()


def set_learned_answer(pid: str, host: str, label: str, value: str) -> str:
    key = f"{host}::{label.strip().lower()}"
    with _connect() as conn:
        learned = _section_row(conn, pid, "learned_answers")
        if not isinstance(learned, dict):
            learned = {}
        learned[key] = value
        _write_section(conn, pid, "learned_answers", learned)
        conn.commit()
    return key


def get_learned_answers(pid: str, host_prefix: str) -> dict:
    with _connect() as conn:
        learned = _section_row(conn, pid, "learned_answers")
    if not isinstance(learned, dict):
        return {}
    prefix = host_prefix + "::"
    return {
        k.split("::", 1)[1]: v
        for k, v in learned.items()
        if isinstance(k, str) and k.startswith(prefix)
    }


def list_all_learned_answers(pid: str) -> dict:
    """Every learned answer for a profile, keyed by the raw 'host::label' key."""
    with _connect() as conn:
        learned = _section_row(conn, pid, "learned_answers")
    return learned if isinstance(learned, dict) else {}


def delete_learned_answer(pid: str, key: str) -> bool:
    with _connect() as conn:
        learned = _section_row(conn, pid, "learned_answers")
        if not isinstance(learned, dict) or key not in learned:
            return False
        del learned[key]
        _write_section(conn, pid, "learned_answers", learned)
        conn.commit()
    return True


def create_stub(pid: str, name: str) -> None:
    save_profile(
        pid,
        {
            "contact_info": {"name": name},
            "autofill": {},
            "experience": [],
            "education": [],
            "skills": {},
            "common_answers": {},
            "summary": "",
        },
    )


def list_events(pid: str, limit: int = 100) -> list[dict]:
    """Memory log: capture events newest-first, for dashboard browsing."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, ts, source_type, raw_text, extracted_json, status, provenance
            FROM events
            WHERE profile_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (pid, max(1, min(int(limit), 500))),
        ).fetchall()
    out = []
    for row in rows:
        try:
            delta = json.loads(row["extracted_json"] or "{}")
        except (ValueError, TypeError):
            delta = {}
        out.append(
            {
                "id": int(row["id"]),
                "ts": row["ts"],
                "source_type": row["source_type"],
                "raw_text": row["raw_text"],
                "delta": delta,
                "status": row["status"],
                "provenance": row["provenance"],
            }
        )
    return out


def delete_event(pid: str, event_id: int) -> bool:
    """Remove one memory event. Committed profile changes are NOT rolled back."""
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM events WHERE id = ? AND profile_id = ?",
            (int(event_id), pid),
        )
        conn.commit()
    return cur.rowcount > 0
