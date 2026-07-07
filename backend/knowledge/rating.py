"""Skill rating helpers for the knowledge service."""

from __future__ import annotations

from typing import Any

from . import store


def list_unrated(pid: str) -> list[dict[str, Any]]:
    """Return skills that do not yet have a proficiency rating."""
    with store._connect() as conn:
        rows = conn.execute(
            """
            SELECT id, category, name, sort_index, source, first_seen
            FROM skills
            WHERE profile_id = ? AND proficiency IS NULL
            ORDER BY category, sort_index, id
            """,
            (pid,),
        ).fetchall()

    return [
        {
            "id": int(row["id"]),
            "category": row["category"],
            "name": row["name"],
            "sort_index": int(row["sort_index"]),
            "source": row["source"],
            "first_seen": row["first_seen"],
        }
        for row in rows
    ]


def set_rating(
    pid: str,
    skill_id: int,
    proficiency: int,
    evidence: str | None = None,
) -> dict[str, Any]:
    """Set proficiency and optional evidence for a skill row."""
    proficiency_int = int(proficiency)
    if proficiency_int < 1 or proficiency_int > 5:
        raise ValueError("proficiency must be in range 1..5")

    with store._connect() as conn:
        row = conn.execute(
            """
            SELECT id, name, category
            FROM skills
            WHERE profile_id = ? AND id = ?
            """,
            (pid, int(skill_id)),
        ).fetchone()
        if not row:
            raise KeyError(f"skill {skill_id} not found for profile {pid}")

        conn.execute(
            """
            UPDATE skills
            SET proficiency = ?, evidence = ?, source = ?, last_used = ?
            WHERE profile_id = ? AND id = ?
            """,
            (proficiency_int, (evidence or "").strip(), "manual", store._utc_now(), pid, int(skill_id)),
        )
        conn.commit()

    return {
        "ok": True,
        "skill_id": int(skill_id),
        "name": row["name"],
        "category": row["category"],
        "proficiency": proficiency_int,
        "evidence": (evidence or "").strip(),
    }


def get_skill_by_name(pid: str, skill_name: str) -> dict[str, Any] | None:
    with store._connect() as conn:
        row = conn.execute(
            """
            SELECT id, profile_id, category, name, sort_index, proficiency, evidence,
                   source, first_seen, last_used
            FROM skills
            WHERE profile_id = ? AND lower(name) = lower(?)
            ORDER BY sort_index, id
            LIMIT 1
            """,
            (pid, (skill_name or "").strip()),
        ).fetchone()
    return dict(row) if row else None


def get_proficiency(pid: str, skill_name: str) -> int | None:
    row = get_skill_by_name(pid, skill_name)
    if not row:
        return None
    val = row.get("proficiency")
    return int(val) if val is not None else None


def ensure_skill(pid: str, skill_name: str, category: str = "domains") -> dict[str, Any]:
    """Ensure a named skill exists using the knowledge store API."""
    clean_name = (skill_name or "").strip()
    if not clean_name:
        raise ValueError("skill name is required")

    existing = get_skill_by_name(pid, clean_name)
    if existing:
        return existing

    profile = store.get_profile(pid) if store.profile_exists(pid) else {}
    section = profile.get("skills", {}) if isinstance(profile, dict) else {}
    if not isinstance(section, dict):
        section = {}
    names = section.get(category, [])
    if not isinstance(names, list):
        names = []
    if clean_name not in names:
        names = [*names, clean_name]
    store.merge_section(pid, "skills", {category: names})

    created = get_skill_by_name(pid, clean_name)
    if not created:
        raise RuntimeError(f"failed to create skill '{clean_name}'")
    return created


def set_rating_by_name(
    pid: str,
    skill_name: str,
    proficiency: int,
    evidence: str | None = None,
    source: str = "manual",
    category: str = "domains",
) -> dict[str, Any]:
    """Set proficiency by skill name, creating the skill if needed."""
    row = ensure_skill(pid, skill_name, category=category)
    proficiency_int = int(proficiency)
    if proficiency_int < 1 or proficiency_int > 5:
        raise ValueError("proficiency must be in range 1..5")

    with store._connect() as conn:
        conn.execute(
            """
            UPDATE skills
            SET proficiency = ?, evidence = ?, source = ?, last_used = ?
            WHERE profile_id = ? AND id = ?
            """,
            (
                proficiency_int,
                (evidence or "").strip(),
                source,
                store._utc_now(),
                pid,
                int(row["id"]),
            ),
        )
        conn.commit()

    updated = get_skill_by_name(pid, skill_name) or {}
    return {
        "ok": True,
        "skill_id": int(updated.get("id", row["id"])),
        "name": updated.get("name", skill_name),
        "category": updated.get("category", category),
        "proficiency": proficiency_int,
        "evidence": (evidence or "").strip(),
    }


def list_all(pid: str) -> list[dict[str, Any]]:
    """All skill rows with rating state, for the dashboard knowledge view."""
    with store._connect() as conn:
        rows = conn.execute(
            """
            SELECT id, category, name, sort_index, proficiency, evidence, source, first_seen, last_used
            FROM skills
            WHERE profile_id = ?
            ORDER BY category, sort_index, id
            """,
            (pid,),
        ).fetchall()

    return [
        {
            "id": int(row["id"]),
            "category": row["category"],
            "name": row["name"],
            "sort_index": int(row["sort_index"]),
            "proficiency": row["proficiency"],
            "evidence": row["evidence"],
            "source": row["source"],
            "first_seen": row["first_seen"],
            "last_used": row["last_used"],
        }
        for row in rows
    ]
