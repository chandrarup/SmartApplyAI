"""Semantic evidence indexing and retrieval for profile matching/tailoring."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from typing import Any

import numpy as np

try:
    import sqlite_vec
except ImportError:  # pragma: no cover - environment dependent
    sqlite_vec = None

from . import store
from .embeddings import embed

EMBEDDING_DIM = int(os.getenv("SMARTAPPLY_EMBED_DIM", "384"))
ALLOWED_KINDS = {
    "skill", "project", "experience_bullet", "summary",
    "education", "publication", "certification", "award",
    "leadership", "research_interest",
}
SQLITE_VEC_AVAILABLE = sqlite_vec is not None


def _load_vec_extension(conn: sqlite3.Connection) -> None:
    if sqlite_vec is None:
        raise RuntimeError("sqlite_vec is unavailable")
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)


def _migrate_evidence_schema(conn: sqlite3.Connection) -> None:
    """Rebuild the evidence table if it carries the legacy kind CHECK constraint.

    Kinds are validated in Python against ALLOWED_KINDS; a baked-in CHECK cannot
    be altered in SQLite and would reject the expanded kinds. Row ids are copied
    verbatim so vec_evidence rowids remain valid (no re-embedding needed).
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'evidence'"
    ).fetchone()
    if row is None or "CHECK" not in (row["sql"] or ""):
        return
    conn.execute("ALTER TABLE evidence RENAME TO evidence_legacy")
    conn.execute(
        """
        CREATE TABLE evidence (
            id INTEGER PRIMARY KEY,
            profile_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            ref_id TEXT NOT NULL,
            text TEXT NOT NULL,
            hash TEXT NOT NULL,
            UNIQUE(profile_id, kind, ref_id)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO evidence(id, profile_id, kind, ref_id, text, hash)
        SELECT id, profile_id, kind, ref_id, text, hash FROM evidence_legacy
        """
    )
    conn.execute("DROP TABLE evidence_legacy")


def ensure_semantic_schema(conn: sqlite3.Connection) -> None:
    _migrate_evidence_schema(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS evidence (
            id INTEGER PRIMARY KEY,
            profile_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            ref_id TEXT NOT NULL,
            text TEXT NOT NULL,
            hash TEXT NOT NULL,
            UNIQUE(profile_id, kind, ref_id)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_evidence_profile_kind
            ON evidence(profile_id, kind, id)
        """
    )
    _load_vec_extension(conn)
    conn.execute(
        f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS vec_evidence
        USING vec0(embedding float[{EMBEDDING_DIM}])
        """
    )


def _normalize_text(value: Any) -> str:
    text = str(value or "").strip()
    return " ".join(text.split())


def _extract_experience_bullets(exp: dict[str, Any]) -> list[str]:
    bullets: list[str] = []
    raw_bullets = exp.get("details") or exp.get("bullets") or exp.get("highlights") or []
    if not isinstance(raw_bullets, list):
        return bullets
    for item in raw_bullets:
        if isinstance(item, str):
            text = _normalize_text(item)
        elif isinstance(item, dict):
            text = _normalize_text(
                item.get("text")
                or item.get("description")
                or item.get("bullet")
                or item.get("value")
            )
        else:
            text = ""
        if text:
            bullets.append(text)
    return bullets


def _project_text(project: Any) -> str:
    if isinstance(project, str):
        return _normalize_text(project)
    if not isinstance(project, dict):
        return ""

    fields = [
        project.get("title"),
        project.get("name"),
        project.get("description"),
        project.get("summary"),
        project.get("overview"),
        project.get("problem"),
        project.get("impact"),
    ]
    bullets = project.get("bullets")
    if isinstance(bullets, list):
        for item in bullets:
            if isinstance(item, str):
                fields.append(item)
            elif isinstance(item, dict):
                fields.append(item.get("text") or item.get("description"))
    return _normalize_text(" ".join(str(part or "") for part in fields))


def _build_corpus(conn: sqlite3.Connection, pid: str) -> list[tuple[str, str, str]]:
    corpus: list[tuple[str, str, str]] = []

    skill_rows = conn.execute(
        """
        SELECT id, name, evidence
        FROM skills
        WHERE profile_id = ?
        ORDER BY category, sort_index, id
        """,
        (pid,),
    ).fetchall()
    for row in skill_rows:
        name = _normalize_text(row["name"])
        evidence_text = _normalize_text(row["evidence"])
        text = f"{name}. Evidence: {evidence_text}" if evidence_text else name
        if text:
            corpus.append(("skill", str(row["id"]), text))

    profile = store.get_profile(pid)

    projects = profile.get("projects", [])
    if isinstance(projects, list):
        for idx, project in enumerate(projects):
            text = _project_text(project)
            if text:
                ref_id = str(project.get("id") or idx) if isinstance(project, dict) else str(idx)
                corpus.append(("project", ref_id, text))

    experience = profile.get("experience", [])
    if isinstance(experience, list):
        for exp_idx, exp in enumerate(experience):
            if not isinstance(exp, dict):
                continue
            role = _normalize_text(exp.get("role") or exp.get("title"))
            company = _normalize_text(exp.get("company"))
            context = " - ".join(part for part in [role, company] if part)
            for bullet_idx, bullet in enumerate(_extract_experience_bullets(exp)):
                text = f"{context}: {bullet}" if context else bullet
                corpus.append(("experience_bullet", f"{exp_idx}:{bullet_idx}", text))

    summary = profile.get("summary")
    if isinstance(summary, str):
        summary_text = _normalize_text(summary)
        if summary_text:
            corpus.append(("summary", "summary", summary_text))

    def _joined(entry: dict[str, Any], keys: list[str]) -> str:
        return _normalize_text(
            ". ".join(str(entry.get(key) or "") for key in keys if entry.get(key))
        )

    section_specs: list[tuple[str, str, list[str]]] = [
        ("education", "education",
         ["degree", "field", "university", "school", "graduation_date", "graduation", "details"]),
        ("publication", "publications",
         ["title", "conference", "venue", "date", "description"]),
        ("certification", "certifications", ["name", "issuer", "date"]),
        ("award", "awards", ["title", "organization", "description"]),
        ("leadership", "leadership", ["role", "organization", "university", "activities"]),
    ]
    for kind, section_key, keys in section_specs:
        entries = profile.get(section_key, [])
        if not isinstance(entries, list):
            continue
        for idx, entry in enumerate(entries):
            if isinstance(entry, dict):
                text = _joined(entry, keys)
            else:
                text = _normalize_text(entry)
            if text:
                corpus.append((kind, str(idx), text))

    interests = profile.get("research_interests", [])
    if isinstance(interests, list):
        for idx, interest in enumerate(interests):
            text = _normalize_text(interest)
            if text:
                corpus.append(("research_interest", str(idx), f"Research interest: {text}"))

    return corpus


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def embed_profile(pid: str) -> int:
    pid = str(pid or "default")
    with store._connect() as conn:
        ensure_semantic_schema(conn)
        corpus = _build_corpus(conn, pid)
        if not SQLITE_VEC_AVAILABLE:
            return len(corpus)
        desired = {
            (kind, ref_id): (text, _text_hash(text))
            for kind, ref_id, text in corpus
            if kind in ALLOWED_KINDS and text
        }

        existing_rows = conn.execute(
            """
            SELECT id, kind, ref_id, hash
            FROM evidence
            WHERE profile_id = ?
            """,
            (pid,),
        ).fetchall()
        existing = {(row["kind"], row["ref_id"]): row for row in existing_rows}

        remove_ids = [row["id"] for key, row in existing.items() if key not in desired]
        if remove_ids:
            conn.executemany("DELETE FROM vec_evidence WHERE rowid = ?", [(rid,) for rid in remove_ids])
            conn.executemany("DELETE FROM evidence WHERE id = ?", [(rid,) for rid in remove_ids])

        to_embed: list[tuple[int, str]] = []
        for (kind, ref_id), (text, text_hash) in desired.items():
            existing_row = existing.get((kind, ref_id))
            if existing_row and existing_row["hash"] == text_hash:
                continue

            conn.execute(
                """
                INSERT INTO evidence(profile_id, kind, ref_id, text, hash)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(profile_id, kind, ref_id)
                DO UPDATE SET text = excluded.text, hash = excluded.hash
                """,
                (pid, kind, ref_id, text, text_hash),
            )
            evidence_id = conn.execute(
                """
                SELECT id FROM evidence
                WHERE profile_id = ? AND kind = ? AND ref_id = ?
                """,
                (pid, kind, ref_id),
            ).fetchone()["id"]
            to_embed.append((evidence_id, text))

        if to_embed:
            vectors = embed([text for _, text in to_embed])
            packed_rows = [
                (evidence_id, sqlite_vec.serialize_float32(vector))
                for (evidence_id, _), vector in zip(to_embed, vectors)
            ]
            conn.executemany(
                "INSERT OR REPLACE INTO vec_evidence(rowid, embedding) VALUES (?, ?)",
                packed_rows,
            )

        conn.commit()
        return conn.execute(
            "SELECT COUNT(*) AS c FROM evidence WHERE profile_id = ?",
            (pid,),
        ).fetchone()["c"]


def _fallback_search(pid: str, query_text: str, k: int, kind_filter: str | None) -> list[dict[str, Any]]:
    with store._connect() as conn:
        corpus = _build_corpus(conn, pid)
    if kind_filter:
        corpus = [item for item in corpus if item[0] == kind_filter]
    if not corpus:
        return []

    texts = [text for _, _, text in corpus]
    vectors = np.array(embed(texts), dtype=float)
    query_vector = np.array(embed([query_text])[0], dtype=float)
    scores = vectors @ query_vector
    top_indices = np.argsort(scores)[::-1][:k]
    return [
        {
            "evidence_id": int(i),
            "kind": corpus[int(i)][0],
            "ref_id": corpus[int(i)][1],
            "text": corpus[int(i)][2],
            # Raw cosine of unit-norm vectors, clamped to match the vec path.
            "score": max(0.0, min(1.0, float(scores[int(i)]))),
            "evidence_ref": f"{corpus[int(i)][0]}:{corpus[int(i)][1]}",
        }
        for i in top_indices
    ]


def search(pid: str, query_text: str, k: int = 10, kind_filter: str | None = None) -> list[dict[str, Any]]:
    query_text = _normalize_text(query_text)
    if not query_text:
        return []

    pid = str(pid or "default")
    k = max(1, min(int(k or 10), 100))
    kind_filter = _normalize_text(kind_filter) or None
    if kind_filter and kind_filter not in ALLOWED_KINDS:
        raise ValueError(f"Unsupported kind_filter: {kind_filter}")
    if not SQLITE_VEC_AVAILABLE:
        return _fallback_search(pid=pid, query_text=query_text, k=k, kind_filter=kind_filter)

    with store._connect() as conn:
        ensure_semantic_schema(conn)
        query_vector = sqlite_vec.serialize_float32(embed([query_text])[0])
        sql = """
            SELECT
                e.id AS evidence_id,
                e.kind AS kind,
                e.ref_id AS ref_id,
                e.text AS text,
                v.distance AS score
            FROM vec_evidence AS v
            JOIN evidence AS e ON e.id = v.rowid
            WHERE v.embedding MATCH ? AND v.k = ? AND e.profile_id = ?
        """
        params: list[Any] = [query_vector, k, pid]
        if kind_filter:
            sql += " AND e.kind = ?"
            params.append(kind_filter)
        sql += " ORDER BY v.distance ASC"

        rows = conn.execute(sql, tuple(params)).fetchall()
        return [
            {
                "evidence_id": row["evidence_id"],
                "kind": row["kind"],
                "ref_id": row["ref_id"],
                "text": row["text"],
                # vec0 returns L2 distance; embeddings are unit-norm, so
                # cosine = 1 - d^2/2 — same scale as the numpy fallback path.
                "score": max(0.0, min(1.0, 1.0 - (float(row["score"]) ** 2) / 2.0)),
                "evidence_ref": f'{row["kind"]}:{row["ref_id"]}',
            }
            for row in rows
        ]
