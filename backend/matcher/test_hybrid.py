"""Tests for the deterministic hybrid scorer (Phase-2 §3.4).

Offline only: the hand-computed cases inject get_features/bm25 so no db or model
is needed; the end-to-end case builds a real tmp features.db with a fake embed_fn
and the real 151-skill ontology. No network, no LLM.
"""

from __future__ import annotations

import numpy as np
import pytest

from backend.matcher import hybrid
from backend.matcher.ontology import Skill


# ── fake ontology for hand-computed cases ────────────────────────────────────
def _fake_ontology() -> dict[str, Skill]:
    return {
        "skill:python": Skill(id="skill:python", name="Python", synonyms=["python"]),
        "skill:ml": Skill(id="skill:ml", name="ML", synonyms=["machine learning", "ml"],
                          related=["skill:stats"]),
        "skill:dl": Skill(id="skill:dl", name="Deep Learning", synonyms=["deep learning"],
                         parents=["skill:ml"]),
        "skill:stats": Skill(id="skill:stats", name="Statistics", synonyms=["statistics"],
                            related=["skill:ml"]),
    }


def _candidate(**over):
    base = {
        "skills": {"skill:python": 1.0, "skill:ml": 0.8},
        "domains": ["ml"],
        "target_level": "intern",
        "profile_embedding": np.array([1.0, 0.0, 0.0], dtype=np.float32),
        "experience_embeddings": [np.array([1.0, 0.0, 0.0], dtype=np.float32)],
        "query_terms": ["python", "machine learning"],
    }
    base.update(over)
    return base


# ── weights guard ────────────────────────────────────────────────────────────
def test_weights_must_sum_to_one():
    with pytest.raises(ValueError, match="sum to 1.0"):
        hybrid.score_jobs_hybrid(_candidate(), [], weights={
            "skills": 0.5, "bm25": 0.2, "embedding": 0.2, "domain": 0.1, "level": 0.1})


def test_weights_missing_key_rejected():
    with pytest.raises(ValueError, match="missing keys"):
        hybrid.score_jobs_hybrid(_candidate(), [], weights={"skills": 1.0})


def test_default_weights_sum_to_one():
    assert abs(sum(hybrid.DEFAULT_WEIGHTS.values()) - 1.0) < 1e-9


# ── component unit tests ───────────────────────────────────────────────────────
def test_score_skills_hand_computed():
    ont = _fake_ontology()
    # required python(exact=1.0)+dl(parent=0.7) → cov_req=0.85; pref stats(related=0.5)
    score = hybrid.score_skills(
        {"skill:python": 1.0, "skill:ml": 0.8},
        {"skill:python": 1.0, "skill:dl": 1.0},
        {"skill:stats": 1.0},
        ont,
    )
    assert score == 74.5  # (0.7*0.85 + 0.3*0.5)*100


def test_score_skills_no_required_falls_back_to_all():
    ont = _fake_ontology()
    # empty required → coverage over preferred only: python exact = 1.0 → 100
    score = hybrid.score_skills({"skill:python": 1.0}, {}, {"skill:python": 1.0}, ont)
    assert score == 100.0


def test_score_skills_empty_job_is_zero():
    assert hybrid.score_skills({"skill:python": 1.0}, {}, {}, _fake_ontology()) == 0.0


def test_normalize_bm25_all_equal_is_fifty():
    assert hybrid.normalize_bm25({"a": 0.0, "b": 0.0}, ["a", "b"]) == {"a": 50.0, "b": 50.0}


def test_normalize_bm25_spread():
    out = hybrid.normalize_bm25({"a": 2.0, "b": 4.0, "c": 6.0}, ["a", "b", "c"])
    assert out == {"a": 0.0, "b": 50.0, "c": 100.0}


def test_normalize_bm25_missing_key_treated_zero():
    out = hybrid.normalize_bm25({"a": 10.0}, ["a", "b"])
    assert out["b"] == 0.0 and out["a"] == 100.0


def test_score_embedding_identical_vectors_is_100():
    v = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    assert hybrid.score_embedding(v, [v], v, None) == 100.0


def test_score_embedding_orthogonal_is_zero():
    a = np.array([1.0, 0.0], dtype=np.float32)
    b = np.array([0.0, 1.0], dtype=np.float32)
    assert hybrid.score_embedding(a, [a], b, None) == 0.0


def test_score_domain_ladder():
    assert hybrid.score_domain(["ml"], ["ml"]) == 100.0     # direct
    assert hybrid.score_domain(["ml"], ["cv"]) == 70.0      # adjacent (ml/cv group)
    assert hybrid.score_domain(["ml"], []) == 50.0          # unknown side
    assert hybrid.score_domain(["fintech"], ["biomed"]) == 30.0  # unrelated


def test_score_level_matrix():
    assert hybrid.score_level("intern", "intern") == 100.0
    assert hybrid.score_level("intern", "senior") == 25.0
    assert hybrid.score_level("entry", "mid") == 70.0
    assert hybrid.score_level("intern", "unknown") == 70.0


# ── full pipeline via injection (fully hand-computable) ────────────────────────
def _one_job_features():
    v = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    return {
        "gh:1": {
            "required_skills": {"skill:python": 1.0, "skill:dl": 1.0},
            "preferred_skills": {"skill:stats": 1.0},
            "domain_tags": ["ml"],
            "level": "intern",
            "embedding_main": v,
            "embedding_requirements": v,
        }
    }


def test_score_jobs_hybrid_hand_computed_total():
    jobs = [{"source_ats": "gh", "external_id": "1", "title": "ML Intern"}]
    out = hybrid.score_jobs_hybrid(
        _candidate(), jobs,
        ontology=_fake_ontology(),
        get_features_fn=lambda db, keys: _one_job_features(),
        bm25_fn=lambda db, terms, keys: {"gh:1": 5.0},
    )
    assert len(out) == 1
    item = out[0]
    comp = item["components"]
    assert comp["skills"] == 74.5
    assert comp["bm25"] == 50.0       # single job → all-equal → neutral 50
    assert comp["embedding"] == 100.0
    assert comp["domain"] == 100.0
    assert comp["level"] == 100.0
    # 0.4*74.5 + 0.2*50 + 0.2*100 + 0.1*100 + 0.1*100 = 79.8
    assert item["hybrid_total"] == 79.8
    assert item["scoring_version"] == "v1"
    assert "Python" in item["explanation"]["matched_skills"]


def test_score_jobs_hybrid_is_deterministic():
    jobs = [{"source_ats": "gh", "external_id": "1", "title": "ML Intern"}]
    kw = dict(ontology=_fake_ontology(),
              get_features_fn=lambda db, keys: _one_job_features(),
              bm25_fn=lambda db, terms, keys: {"gh:1": 5.0})
    a = hybrid.score_jobs_hybrid(_candidate(), jobs, **kw)
    b = hybrid.score_jobs_hybrid(_candidate(), jobs, **kw)
    assert a == b


def test_missing_features_are_skipped():
    jobs = [{"source_ats": "gh", "external_id": "99", "title": "X"}]
    out = hybrid.score_jobs_hybrid(
        _candidate(), jobs,
        ontology=_fake_ontology(),
        get_features_fn=lambda db, keys: {},   # nothing built
        bm25_fn=lambda db, terms, keys: {},
    )
    assert out == []


def test_components_within_bounds_on_ranked_batch():
    jobs = [
        {"source_ats": "gh", "external_id": "1", "title": "ML Intern"},
        {"source_ats": "gh", "external_id": "2", "title": "Senior ML Engineer"},
    ]
    feats = _one_job_features()
    feats["gh:2"] = {
        "required_skills": {"skill:stats": 1.0},
        "preferred_skills": {},
        "domain_tags": ["fintech"],
        "level": "senior",
        "embedding_main": np.array([0.0, 1.0, 0.0], dtype=np.float32),
        "embedding_requirements": None,
    }
    out = hybrid.score_jobs_hybrid(
        _candidate(), jobs,
        ontology=_fake_ontology(),
        get_features_fn=lambda db, keys: feats,
        bm25_fn=lambda db, terms, keys: {"gh:1": 8.0, "gh:2": 2.0},
    )
    assert [i["job_key"] for i in out] == ["gh:1", "gh:2"]  # intern ranks above senior
    for item in out:
        for name, val in item["components"].items():
            assert 0.0 <= val <= 100.0, f"{name}={val} out of bounds"
        assert 0.0 <= item["hybrid_total"] <= 100.0


# ── real modules end-to-end (tmp db, fake embeddings, real ontology) ───────────
def test_end_to_end_real_features_db(tmp_path):
    from backend.matcher import features
    from backend.matcher.ontology import load_ontology

    db = tmp_path / "features.db"
    dim = 8

    def fake_embed(texts):
        # deterministic unit vectors; identical so wiring (not values) is under test
        return [list(np.eye(dim, dtype=np.float32)[0]) for _ in texts]

    jobs = [
        {"source_ats": "gh", "external_id": "1",
         "title": "Machine Learning Intern", "company": "Acme", "location": "Remote",
         "description_text": "Requirements:\nPython and machine learning.\n"
                             "Preferred:\nStatistics and deep learning."},
        {"source_ats": "gh", "external_id": "2",
         "title": "Senior Backend Engineer", "company": "Beta", "location": "NYC",
         "description_text": "Requirements:\nJava, microservices, REST APIs."},
    ]
    stats = features.ensure_job_features(jobs, db, embed_fn=fake_embed)
    assert stats["built"] == 2

    candidate = {
        "skills": {"skill:python": 1.0, "skill:machine-learning": 0.9},
        "domains": ["ml"],
        "target_level": "intern",
        "profile_embedding": np.array(np.eye(dim, dtype=np.float32)[0]),
        "experience_embeddings": [np.array(np.eye(dim, dtype=np.float32)[0])],
        "query_terms": ["python", "machine learning"],
    }
    out = hybrid.score_jobs_hybrid(candidate, jobs, db, ontology=load_ontology())
    assert len(out) == 2
    keys = [i["job_key"] for i in out]
    assert "gh:1" in keys and "gh:2" in keys
    by_key = {i["job_key"]: i for i in out}
    # ML intern must out-score the senior backend role for this ML-intern candidate.
    assert by_key["gh:1"]["hybrid_total"] > by_key["gh:2"]["hybrid_total"]
    for item in out:
        assert 0.0 <= item["hybrid_total"] <= 100.0
        assert set(item["components"]) == {"skills", "bm25", "embedding", "domain", "level"}
