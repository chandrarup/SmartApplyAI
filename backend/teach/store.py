"""SQLite storage for teach/FSRS review state."""

from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime, timezone

DB_PATH = os.path.join(os.path.dirname(__file__), "reviews.db")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS review_state (
            profile_id TEXT NOT NULL,
            skill TEXT NOT NULL,
            stability REAL NOT NULL DEFAULT 0.6,
            difficulty REAL NOT NULL DEFAULT 5.0,
            reps INTEGER NOT NULL DEFAULT 0,
            lapses INTEGER NOT NULL DEFAULT 0,
            due_date TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL DEFAULT 'new',
            last_grade TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (profile_id, skill)
        );
        CREATE INDEX IF NOT EXISTS idx_review_due ON review_state(profile_id, due_date);
        """
    )
    conn.commit()


def get_state(pid: str, skill: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM review_state WHERE profile_id = ? AND lower(skill) = lower(?)",
            (pid, skill.strip()),
        ).fetchone()
    return dict(row) if row else None


def ensure_state(pid: str, skill: str) -> dict:
    skill = (skill or "").strip()
    if not skill:
        raise ValueError("skill is required")
    existing = get_state(pid, skill)
    if existing:
        return existing
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO review_state(profile_id, skill, updated_at)
            VALUES (?, ?, ?)
            """,
            (pid, skill, _utc_now()),
        )
        conn.commit()
    created = get_state(pid, skill)
    if not created:
        raise RuntimeError("Failed to create review state")
    return created


def save_state(pid: str, skill: str, state: dict) -> dict:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO review_state(
                profile_id, skill, stability, difficulty, reps, lapses,
                due_date, state, last_grade, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(profile_id, skill) DO UPDATE SET
                stability = excluded.stability,
                difficulty = excluded.difficulty,
                reps = excluded.reps,
                lapses = excluded.lapses,
                due_date = excluded.due_date,
                state = excluded.state,
                last_grade = excluded.last_grade,
                updated_at = excluded.updated_at
            """,
            (
                pid,
                skill.strip(),
                float(state.get("stability", 0.6)),
                float(state.get("difficulty", 5.0)),
                int(state.get("reps", 0)),
                int(state.get("lapses", 0)),
                str(state.get("due_date", "")),
                str(state.get("state", "new")),
                state.get("last_grade"),
                _utc_now(),
            ),
        )
        conn.commit()
    return get_state(pid, skill) or {}


def due_today(pid: str, today: date | None = None) -> list[dict]:
    cutoff = (today or date.today()).isoformat()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM review_state
            WHERE profile_id = ?
              AND (due_date = '' OR due_date <= ?)
            ORDER BY CASE WHEN due_date = '' THEN 0 ELSE 1 END, due_date, skill
            """,
            (pid, cutoff),
        ).fetchall()
    return [dict(r) for r in rows]
