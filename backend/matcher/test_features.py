"""Tests for backend/matcher/features.py (Phase-2 hybrid filtering §3.2).

Offline by design: fake embed_fn (deterministic small vectors), fake ontology
dict + fake mapper (ontology.py may not exist yet), tmp_path databases only.
"""

from __future__ import annotations

import hashlib
import sqlite3

import numpy as np
import pytest

try:
    from backend.matcher import features
except ImportError:
    from matcher import features  # type: ignore


# --- offline fakes ----------------------------------------------------------

FAKE_ONTOLOGY = {
    "skill:python": {"name": "Python"},
    "skill:pytorch": {"name": "PyTorch"},
    "skill:kubernetes": {"name": "Kubernetes"},
    "skill:rust": {"name": "Rust"},
}

_FAKE_TERMS = {
    "python": "skill:python",
    "pytorch": "skill:pytorch",
    "kubernetes": "skill:kubernetes",
    "rust": "skill:rust",
}


def fake_mapper(text, ontology=None, *, title=""):
    """Tiny deterministic stand-in for ontology.map_text_to_skills."""
    lowered = f"{title}\n{text}".lower()
    return {sid: 1.0 for term, sid in _FAKE_TERMS.items() if term in lowered}


def fake_embed(texts):
    """Deterministic 8-dim unit vectors derived from the text hash."""
    out = []
    for text in texts:
        digest = hashlib.sha256(str(text).encode("utf-8")).digest()
        vec = np.frombuffer(digest[:8], dtype=np.uint8).astype(np.float32) + 1.0
        out.append((vec / np.linalg.norm(vec)).tolist())
    return out


JD_TEXT = """About Acme
We build imaging platforms for biology labs using machine learning.

Requirements:
- 3+ years of Python
- Experience with PyTorch and deep learning
- Ship production model training pipelines

Nice to have
- Kubernetes experience
- Exposure to LLM applications

Responsibilities
- Deploy models and monitor them in production
"""


def make_job(external_id="j1", title="Machine Learning Engineer",
             location="San Francisco, CA", description=JD_TEXT):
    return {
        "source_ats": "greenhouse",
        "company": "Acme",
        "external_id": external_id,
        "title": title,
        "location": location,
        "description_text": description,
        "apply_url": f"https://example.com/{external_id}",
        "is_internship": 0,
    }


def ensure(jobs, db_path, embed_fn=fake_embed):
    return features.ensure_job_features(
        jobs, db_path=db_path, ontology=FAKE_ONTOLOGY,
        embed_fn=embed_fn, mapper=fake_mapper,
    )


# --- schema ------------------------------------------------------------------

def test_schema_idempotent(tmp_path):
    db = tmp_path / "features.db"
    conn = sqlite3.connect(str(db))
    try:
        features.ensure_features_schema(conn)
        features.ensure_features_schema(conn)  # second call must not raise
        names = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE name IN "
                "('job_features', 'jobs_fts')"
            )
        }
    finally:
        conn.close()
    assert names == {"job_features", "jobs_fts"}


# --- build_job_features ------------------------------------------------------

def test_section_split_required_vs_preferred():
    feats = features.build_job_features(make_job(), FAKE_ONTOLOGY, mapper=fake_mapper)
    assert "skill:python" in feats["required_skills"]
    assert "skill:pytorch" in feats["required_skills"]
    assert "skill:kubernetes" not in feats["required_skills"]
    assert "skill:kubernetes" in feats["preferred_skills"]
    assert "skill:python" not in feats["preferred_skills"]
    assert feats["requirements_text"]  # a real requirements section was found
    assert "Python" in feats["requirements_text"]


def test_fallback_to_full_text_when_no_headers():
    job = make_job(description="We want Python and PyTorch experts. No headers here.")
    feats = features.build_job_features(job, FAKE_ONTOLOGY, mapper=fake_mapper)
    assert "skill:python" in feats["required_skills"]
    assert feats["preferred_skills"] == {}
    assert feats["requirements_text"] is None


def test_domain_tags_and_metadata():
    feats = features.build_job_features(make_job(), FAKE_ONTOLOGY, mapper=fake_mapper)
    assert "ml" in feats["domain_tags"]
    assert "llm" in feats["domain_tags"]
    assert "fintech" not in feats["domain_tags"]
    assert feats["job_key"] == "greenhouse:j1"
    assert feats["desc_hash"] == hashlib.sha256(JD_TEXT.encode("utf-8")).hexdigest()
    assert "embedding_main" not in feats  # pure: caller embeds in batch


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Machine Learning Intern", "intern"),
        ("Software Engineering Internship", "intern"),
        ("Co-op Software Developer", "intern"),
        ("Software Engineer, New Grad", "entry"),
        ("Junior Data Scientist", "entry"),
        ("Associate ML Engineer", "entry"),
        ("Senior ML Engineer", "senior"),
        ("Staff Software Engineer", "senior"),
        ("Engineering Manager", "senior"),
        ("Machine Learning Engineer", "unknown"),
    ],
)
def test_level_extraction(title, expected):
    feats = features.build_job_features(
        make_job(title=title), FAKE_ONTOLOGY, mapper=fake_mapper
    )
    assert feats["level"] == expected


@pytest.mark.parametrize(
    "title,location,description,expected",
    [
        ("ML Engineer", "Remote - US", "desc", 1),
        ("ML Engineer (Remote)", "NYC", "desc", 1),
        ("ML Engineer", "Boston, MA", "This role is fully remote.", 1),
        ("ML Engineer", "Boston, MA", "Onsite role in our Boston office.", 0),
    ],
)
def test_remote_extraction(title, location, description, expected):
    feats = features.build_job_features(
        make_job(title=title, location=location, description=description),
        FAKE_ONTOLOGY,
        mapper=fake_mapper,
    )
    assert feats["is_remote"] == expected


# --- ensure_job_features (incremental + storage) -----------------------------

def test_incremental_skip_and_rebuild(tmp_path):
    db = tmp_path / "features.db"
    calls = []

    def counting_embed(texts):
        calls.append(list(texts))
        return fake_embed(texts)

    job1, job2 = make_job("j1"), make_job("j2", title="Senior ML Engineer")
    assert ensure([job1, job2], db, counting_embed) == {
        "built": 2, "reused": 0, "failed": 0,
    }
    assert len(calls) == 1  # one batched embed call

    # Same hash -> reused, no new embed calls.
    assert ensure([job1, job2], db, counting_embed) == {
        "built": 0, "reused": 2, "failed": 0,
    }
    assert len(calls) == 1

    # Changed description -> only that job rebuilt.
    job2b = dict(job2, description_text=JD_TEXT + "\nNow also Rust services.")
    assert ensure([job1, job2b], db, counting_embed) == {
        "built": 1, "reused": 1, "failed": 0,
    }
    assert len(calls) == 2

    # FTS row was refreshed: the new text is findable, keyed to job2 only.
    scores = features.bm25_scores(db, ["rust"], ["greenhouse:j1", "greenhouse:j2"])
    assert scores["greenhouse:j2"] > 0.0
    assert scores["greenhouse:j1"] == 0.0


def test_get_features_roundtrip(tmp_path):
    db = tmp_path / "features.db"
    job_with_sections = make_job("j1")
    job_no_sections = make_job(
        "j2", title="ML Intern", location="Remote",
        description="Python work with no section headers at all.",
    )
    ensure([job_with_sections, job_no_sections], db)

    got = features.get_features(db, ["greenhouse:j1", "greenhouse:j2", "missing:x"])
    assert set(got) == {"greenhouse:j1", "greenhouse:j2"}

    f1 = got["greenhouse:j1"]
    assert f1["required_skills"]["skill:python"] == 1.0
    assert f1["preferred_skills"]["skill:kubernetes"] == 1.0
    assert isinstance(f1["domain_tags"], list) and "ml" in f1["domain_tags"]
    assert f1["level"] == "unknown"
    assert f1["is_remote"] is False
    assert f1["embedding_main"].dtype == np.float32
    expected_main = np.asarray(fake_embed([f1["full_text"]])[0], dtype=np.float32)
    assert np.allclose(f1["embedding_main"], expected_main)
    assert f1["embedding_requirements"] is not None
    assert f1["embedding_requirements"].dtype == np.float32

    f2 = got["greenhouse:j2"]
    assert f2["embedding_requirements"] is None  # no requirements section
    assert f2["level"] == "intern"
    assert f2["is_remote"] is True


def test_per_job_failure_isolation(tmp_path, capsys):
    db = tmp_path / "features.db"

    def poison_embed(texts):
        if any("POISON" in t for t in texts):
            raise RuntimeError("boom")
        return fake_embed(texts)

    good = make_job("good")
    bad = make_job("bad", description="POISON Python job with no headers.")
    result = ensure([good, bad], db, poison_embed)
    assert result == {"built": 1, "reused": 0, "failed": 1}

    out = capsys.readouterr().out
    assert "[features] skip greenhouse:bad" in out

    got = features.get_features(db, ["greenhouse:good", "greenhouse:bad"])
    assert "greenhouse:good" in got
    assert "greenhouse:bad" not in got


def test_bad_job_dict_is_skipped_not_fatal(tmp_path, capsys):
    db = tmp_path / "features.db"

    def exploding_mapper(text, ontology=None, *, title=""):
        raise ValueError("mapper blew up")

    result = features.ensure_job_features(
        [make_job("j1")], db_path=db, ontology=FAKE_ONTOLOGY,
        embed_fn=fake_embed, mapper=exploding_mapper,
    )
    assert result == {"built": 0, "reused": 0, "failed": 1}
    assert "[features] skip greenhouse:j1" in capsys.readouterr().out


# --- bm25 ---------------------------------------------------------------------

def test_bm25_rare_term_ordering(tmp_path):
    db = tmp_path / "features.db"
    with_term = make_job(
        "j1", description="We use Cellpose for segmentation. Cellpose expertise required.",
    )
    without_term = make_job(
        "j2", description="Generic software role writing Python services.",
    )
    ensure([with_term, without_term], db)

    scores = features.bm25_scores(db, ["cellpose"], ["greenhouse:j1", "greenhouse:j2"])
    assert scores["greenhouse:j1"] > scores["greenhouse:j2"]
    assert scores["greenhouse:j2"] == 0.0

    # job_keys=None returns only actual matches.
    all_matches = features.bm25_scores(db, ["cellpose"])
    assert set(all_matches) == {"greenhouse:j1"}
    assert all_matches["greenhouse:j1"] > 0.0


def test_bm25_sanitizes_fts5_operators(tmp_path):
    db = tmp_path / "features.db"
    ensure([make_job("j1")], db)
    hostile_terms = [
        'python" OR job_key:*',
        "NEAR(python, 2)",
        "machine-learning",
        "c++",
        "(((",
        "",
    ]
    scores = features.bm25_scores(db, hostile_terms, ["greenhouse:j1"])
    assert scores["greenhouse:j1"] > 0.0  # python still matched, no crash


def test_bm25_no_valid_terms(tmp_path):
    db = tmp_path / "features.db"
    ensure([make_job("j1")], db)
    assert features.bm25_scores(db, ["(((", ""], ["greenhouse:j1"]) == {
        "greenhouse:j1": 0.0
    }
    assert features.bm25_scores(db, [], None) == {}


# --- DOMAIN_KEYWORDS contract (Agent C imports this) --------------------------

def test_domain_keywords_table_shape():
    expected_tags = {
        "ml", "llm", "cv", "nlp", "biomed", "robotics", "data-eng",
        "backend", "frontend", "security", "fintech", "healthcare",
    }
    assert expected_tags.issubset(set(features.DOMAIN_KEYWORDS))
    for tag, phrases in features.DOMAIN_KEYWORDS.items():
        assert isinstance(phrases, list) and phrases, tag
        assert all(isinstance(p, str) and p for p in phrases), tag
