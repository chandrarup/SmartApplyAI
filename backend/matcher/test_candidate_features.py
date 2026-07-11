"""Tests for backend/matcher/candidate_features.py (design contract §3.3).

Hermetic: fixture profile dict (never hits knowledge.db), injected fake
embed_fn (no model downloads), injected mapper + domain keyword table
(standalone while ontology.py / features.py are built in parallel),
tmp_path databases only.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import re
import sqlite3

import numpy as np
import pytest

try:
    from backend.matcher import candidate_features as cf
except ImportError:
    from matcher import candidate_features as cf  # type: ignore


# ── fixtures / fakes ──────────────────────────────────────────────────

PROFILE = {
    "contact_info": {"name": "Test Candidate", "email": "t@example.com"},
    "summary": "MS student working on Python and LLM applications.",
    "skills": {
        "languages": ["Python", "SQL"],
        "frameworks": ["PyTorch", "FastAPI"],
        "tools": ["Docker"],
        "databases": ["PostgreSQL"],
        "domains": ["Machine Learning"],
    },
    "experience": [
        {
            "company": "Research Lab",
            "role": "Research Assistant",
            "details": [
                "Built PyTorch segmentation pipelines",
                "Deployed FastAPI services",
            ],
        },
    ],
    "projects": [
        {
            "title": "RAG Search",
            "tech_stack": ["Python", "LangChain"],
            "description": "Retrieval augmented generation over papers.",
        },
    ],
    "education": [{"degree": "MS", "field": "CS", "university": "X University"}],
    "research_interests": ["spatial omics"],
}

KEYWORDS = {
    "skill:python": "python",
    "skill:pytorch": "pytorch",
    "skill:fastapi": "fastapi",
    "skill:sql": "sql",
    "skill:docker": "docker",
    "skill:postgresql": "postgresql",
    "skill:langchain": "langchain",
    "skill:machine-learning": "machine learning",
}

ONTOLOGY = {
    "skill:python": {"name": "Python", "synonyms": ["python", "py", "cpython"]},
    "skill:pytorch": {"name": "PyTorch", "synonyms": ["pytorch", "torch", "torchvision"]},
    "skill:fastapi": {"name": "FastAPI", "synonyms": ["fastapi", "fast api", "fast-api"]},
    "skill:sql": {"name": "SQL", "synonyms": ["sql", "tsql", "plsql"]},
    "skill:docker": {"name": "Docker", "synonyms": ["docker", "containers", "docker compose"]},
    "skill:postgresql": {"name": "PostgreSQL", "synonyms": ["postgres", "postgresql", "psql"]},
    "skill:machine-learning": {"name": "Machine Learning", "synonyms": ["machine learning", "ml", "statistical learning"]},
    "skill:langchain": {"name": "LangChain", "synonyms": ["langchain", "lang chain", "langgraph"]},
}


def fake_mapper(text: str, title: str = "") -> dict[str, float]:
    """Deterministic keyword mapper: 1.0 per keyword found (word-bounded)."""
    text_lower = str(text).lower()
    out = {}
    for sid, kw in KEYWORDS.items():
        if re.search(r"\b" + re.escape(kw) + r"\b", text_lower):
            out[sid] = 1.0
    return out


def make_embed(counter: dict):
    """Deterministic fake embed_fn; counts calls, records batched texts."""

    def fake_embed(texts):
        counter["calls"] += 1
        counter["texts"].append(list(texts))
        out = []
        for t in texts:
            digest = hashlib.sha256(str(t).encode("utf-8")).digest()
            vec = np.frombuffer(digest, dtype=np.uint8).astype(np.float32)[:16]
            norm = float(np.linalg.norm(vec)) or 1.0
            out.append((vec / norm).tolist())
        return out

    return fake_embed


@pytest.fixture
def profile():
    return copy.deepcopy(PROFILE)


@pytest.fixture
def db(tmp_path):
    return tmp_path / "features.db"


@pytest.fixture
def counter():
    return {"calls": 0, "texts": []}


def build(profile, db, embed_fn, **kwargs):
    kwargs.setdefault("mapper", fake_mapper)
    kwargs.setdefault("domain_keywords", cf._FALLBACK_DOMAIN_KEYWORDS)
    return cf.build_candidate_features(
        "default",
        profile=profile,
        embed_fn=embed_fn,
        db_path=db,
        **kwargs,
    )


# ── skills: facet weighting + normalization ───────────────────────────

def test_skills_facet_weighting(profile, db, counter):
    feats = build(profile, db, make_embed(counter))
    skills = feats["skills"]

    # Hand-computed raw sums with facet weights 1.0/0.8/0.7/0.5:
    #   python: skills 1.0 + project 0.7 + summary 0.5 = 2.2  (max)
    #   pytorch/fastapi: skills 1.0 + experience 0.8 = 1.8
    #   sql/docker/postgresql/machine-learning: skills only = 1.0
    #   langchain: project only = 0.7
    assert skills["skill:python"] == 1.0
    assert skills["skill:pytorch"] == pytest.approx(1.8 / 2.2, abs=1e-6)
    assert skills["skill:fastapi"] == pytest.approx(1.8 / 2.2, abs=1e-6)
    assert skills["skill:sql"] == pytest.approx(1.0 / 2.2, abs=1e-6)
    assert skills["skill:langchain"] == pytest.approx(0.7 / 2.2, abs=1e-6)

    # Facet ordering: skills+experience > skills-only > project-only.
    assert skills["skill:pytorch"] > skills["skill:sql"] > skills["skill:langchain"]
    assert set(skills) == set(KEYWORDS)


def test_skills_max_normalized_to_one(profile, db, counter):
    feats = build(profile, db, make_embed(counter))
    assert max(feats["skills"].values()) == 1.0
    assert all(0.0 < w <= 1.0 for w in feats["skills"].values())


# ── domains ───────────────────────────────────────────────────────────

def test_domains_detected_and_ordered(profile, db, counter):
    feats = build(profile, db, make_embed(counter))
    # llm: llm, rag, retrieval augmented, langchain (4 hits)
    # backend: fastapi, postgresql, sql (3) — ml: machine learning, pytorch (2)
    assert feats["domains"] == ["llm", "backend", "ml"]


# ── cache behavior ────────────────────────────────────────────────────

def test_cache_hit_embed_called_once_across_two_calls(profile, db, counter):
    first = build(profile, db, make_embed(counter))
    second = build(profile, db, make_embed(counter))

    assert counter["calls"] == 1  # second call served from cache

    assert second["profile_hash"] == first["profile_hash"]
    assert second["skills"] == first["skills"]
    assert second["domains"] == first["domains"]
    assert second["query_terms"] == first["query_terms"]
    assert isinstance(second["profile_embedding"], np.ndarray)
    assert second["profile_embedding"].dtype == np.float32
    assert np.allclose(second["profile_embedding"], first["profile_embedding"])
    assert len(second["experience_embeddings"]) == len(first["experience_embeddings"])
    for a, b in zip(first["experience_embeddings"], second["experience_embeddings"]):
        assert isinstance(b, np.ndarray) and b.dtype == np.float32
        assert np.allclose(a, b)


def test_cache_invalidation_on_profile_change(profile, db, counter):
    first = build(profile, db, make_embed(counter))
    changed = copy.deepcopy(profile)
    changed["skills"]["tools"].append("Kubernetes")
    second = build(changed, db, make_embed(counter))

    assert counter["calls"] == 2  # hash changed -> rebuilt
    assert second["profile_hash"] != first["profile_hash"]


def test_profile_hash_is_canonical_key_order_independent(profile, db, counter):
    first = build(profile, db, make_embed(counter))
    reordered = dict(reversed(list(profile.items())))
    second = build(reordered, db, make_embed(counter))

    assert second["profile_hash"] == first["profile_hash"]
    assert counter["calls"] == 1  # canonical json -> same hash -> cache hit


def test_target_level_passthrough_and_cache_hit_override(profile, db, counter):
    first = build(profile, db, make_embed(counter), target_level="intern")
    second = build(profile, db, make_embed(counter), target_level="entry")

    assert first["target_level"] == "intern"
    assert second["target_level"] == "entry"  # stamped even on cache hit
    assert counter["calls"] == 1


# ── query terms ───────────────────────────────────────────────────────

def test_query_terms_deterministic_ordering_without_ontology(profile, db, counter, tmp_path):
    # weight desc, then skill id alphabetical; human part of id as fallback.
    expected = [
        "python",                       # 1.0
        "fastapi", "pytorch",           # 0.818182 tie -> id alpha
        "docker", "machine learning",   # 0.454545 tie -> id alpha
        "postgresql", "sql",
        "langchain",                    # 0.318182
    ]
    feats = build(profile, db, make_embed(counter))
    assert feats["query_terms"] == expected

    # Fresh db (no cache) -> identical output: build is deterministic.
    feats2 = build(profile, tmp_path / "other.db", make_embed(counter))
    assert feats2["query_terms"] == expected
    assert feats2["skills"] == feats["skills"]


def test_query_terms_ontology_names_synonyms_dedupe_and_cap(profile, db, counter):
    feats = build(profile, db, make_embed(counter), ontology=ONTOLOGY)
    terms = feats["query_terms"]

    # 8 skills x (name + 3 synonyms, 1 case-insensitive dupe each) = 24 terms.
    assert len(terms) == 24
    assert terms[:6] == ["Python", "py", "cpython", "FastAPI", "fast api", "fast-api"]
    assert "torch" in terms
    # Case-insensitive dedupe: display name "Python" wins over synonym "python".
    assert [t.lower() for t in terms].count("python") == 1


# ── embeddings + batching ─────────────────────────────────────────────

def test_single_embed_batch_profile_plus_experience_texts(profile, db, counter):
    feats = build(profile, db, make_embed(counter))

    assert counter["calls"] == 1
    batch = counter["texts"][0]
    # 1 profile text + 1 experience + 1 project (cap 12).
    assert len(batch) == 3
    assert "MS student" in batch[0] and "Key skills:" in batch[0]
    assert any("Research Assistant" in t for t in batch[1:])
    assert any("RAG Search" in t for t in batch[1:])

    assert feats["profile_embedding"].dtype == np.float32
    assert len(feats["experience_embeddings"]) == 2


# ── cache payload: pickle-free json envelope ──────────────────────────

def test_payload_is_json_envelope_no_pickle(profile, db, counter):
    feats = build(profile, db, make_embed(counter))

    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT profile_hash, payload FROM candidate_features "
            "WHERE profile_id = 'default'"
        ).fetchone()
    assert row is not None
    assert row[0] == feats["profile_hash"]

    doc = json.loads(bytes(row[1]).decode("utf-8"))  # valid JSON, not pickle
    env = doc["profile_embedding"]
    assert set(env) == {"shape", "dtype", "data"}
    assert env["dtype"] == "float32"
    raw = base64.b64decode(env["data"])
    assert len(raw) == math.prod(env["shape"]) * 4
    decoded = np.frombuffer(raw, dtype=np.float32).reshape(env["shape"])
    assert np.allclose(decoded, feats["profile_embedding"])
    assert len(doc["experience_embeddings"]) == 2


def test_only_candidate_features_table_created(profile, db, counter):
    build(profile, db, make_embed(counter))
    with sqlite3.connect(db) as conn:
        names = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            )
        }
    assert names == {"candidate_features"}  # never touches Agent B's job tables


# ── failure modes ─────────────────────────────────────────────────────

def test_empty_profile_raises(db, counter):
    with pytest.raises(ValueError):
        cf.build_candidate_features(
            "default", profile={}, mapper=fake_mapper,
            embed_fn=make_embed(counter), db_path=db,
        )
