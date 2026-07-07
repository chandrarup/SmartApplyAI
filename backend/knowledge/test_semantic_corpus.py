"""Tests for the expanded semantic evidence corpus and score-scale parity.

Uses a deterministic fake embedder so no model download/load is required.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import sys

import numpy as np
import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from knowledge import semantic, store  # noqa: E402

FIXTURE_PROFILE = {
    "contact_info": {"name": "Test User"},
    "summary": "ML engineer building production LLM systems.",
    "education": [
        {
            "degree": "M.S. in Engineering Data Science and Artificial Intelligence",
            "university": "University of Houston",
            "graduation_date": "Expected 2027",
            "details": "Focus on AI/ML, Advanced NLP, and Large Language Models.",
        }
    ],
    "experience": [
        {
            "role": "AI Engineer",
            "company": "Accenture",
            "details": ["Built GenAI platform features for enterprise clients."],
        }
    ],
    "projects": [{"title": "RAG Search", "description": "Retrieval-augmented search."}],
    "skills": {"languages": ["Python"]},
    "publications": [
        {
            "title": "Deepfake Detection via Transfer Learning",
            "conference": "GCAT",
            "date": "October 2024",
            "description": "Transfer learning with temporal models.",
        }
    ],
    "certifications": [{"name": "Azure AI Engineer Associate", "issuer": "Microsoft", "date": "2025"}],
    "awards": [{"title": "Wall of Fame", "organization": "Accenture", "description": "GenAI delivery"}],
    "leadership": [{"role": "Volunteer", "organization": "Nature Club", "activities": "Campus drives"}],
    "research_interests": ["Natural Language Processing", "Agentic AI Systems"],
}


def _fake_embed(texts: list[str]) -> list[list[float]]:
    """Deterministic pseudo-embeddings: hash-seeded unit vectors."""
    out = []
    for text in texts:
        seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")
        rng = np.random.default_rng(seed)
        v = rng.standard_normal(semantic.EMBEDDING_DIM)
        out.append((v / np.linalg.norm(v)).tolist())
    return out


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "knowledge.db"))
    monkeypatch.setattr(store, "get_profile", lambda pid: FIXTURE_PROFILE)
    monkeypatch.setattr(semantic, "embed", _fake_embed)
    return tmp_path


def test_corpus_includes_expanded_kinds(temp_db):
    with store._connect() as conn:
        corpus = semantic._build_corpus(conn, "default")
    kinds = {kind for kind, _, _ in corpus}
    assert {"education", "publication", "certification", "award",
            "leadership", "research_interest"} <= kinds

    edu = [text for kind, _, text in corpus if kind == "education"][0]
    assert "Engineering Data Science and Artificial Intelligence" in edu
    assert "University of Houston" in edu

    interests = [text for kind, _, text in corpus if kind == "research_interest"]
    assert len(interests) == 2
    assert any("Natural Language Processing" in t for t in interests)


def test_all_corpus_kinds_are_allowed(temp_db):
    with store._connect() as conn:
        corpus = semantic._build_corpus(conn, "default")
    assert {kind for kind, _, _ in corpus} <= semantic.ALLOWED_KINDS


def test_migration_drops_legacy_check_and_preserves_rows(tmp_path):
    db = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE evidence (
            id INTEGER PRIMARY KEY,
            profile_id TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('skill','project','experience_bullet','summary')),
            ref_id TEXT NOT NULL,
            text TEXT NOT NULL,
            hash TEXT NOT NULL,
            UNIQUE(profile_id, kind, ref_id)
        )
        """
    )
    conn.execute(
        "INSERT INTO evidence(id, profile_id, kind, ref_id, text, hash) VALUES (7, 'default', 'skill', '1', 'Python', 'h')"
    )
    conn.commit()

    semantic._migrate_evidence_schema(conn)

    row = conn.execute("SELECT * FROM evidence WHERE id = 7").fetchone()
    assert row["kind"] == "skill" and row["text"] == "Python"
    # New kinds must now be insertable (the legacy CHECK rejected them).
    conn.execute(
        "INSERT INTO evidence(profile_id, kind, ref_id, text, hash) VALUES ('default', 'education', '0', 'MS', 'h2')"
    )
    # Migration is idempotent.
    semantic._migrate_evidence_schema(conn)
    assert conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 2
    conn.close()


@pytest.mark.skipif(not semantic.SQLITE_VEC_AVAILABLE, reason="sqlite_vec not installed")
def test_vec_and_fallback_scores_agree(temp_db, monkeypatch):
    semantic.embed_profile("default")
    query = "large language models in production"
    vec_hits = semantic.search("default", query, k=5)

    monkeypatch.setattr(semantic, "SQLITE_VEC_AVAILABLE", False)
    fallback_hits = semantic.search("default", query, k=5)

    vec_scores = {h["evidence_ref"]: h["score"] for h in vec_hits}
    fb_scores = {h["evidence_ref"]: h["score"] for h in fallback_hits}
    shared = set(vec_scores) & set(fb_scores)
    assert shared, "expected overlapping results between vec and fallback paths"
    for ref in shared:
        assert vec_scores[ref] == pytest.approx(fb_scores[ref], abs=1e-4)
