"""Integration tests for run_pipeline wiring (Phase-2 §3.5).

Exercises the actual entry point's branching — hybrid path, fit["hybrid"]
attachment, and the rule-7 fallback to legacy — with the heavy stages stubbed
so it runs offline in milliseconds. Complements the real-data proof in the
scratch verification (models + real jobs.db)."""

from __future__ import annotations

import pytest

from backend.matcher import run as run_mod
from backend.matcher.config import load_config


def _cfg(**over):
    cfg = load_config()  # real defaults
    for k, v in over.items():
        object.__setattr__(cfg, k, v)
    return cfg


def _fake_jobs():
    return [
        {"source_ats": "gh", "external_id": "1", "title": "ML Intern",
         "company": "Acme", "description_text": "ML role", "matched_searches": ["ml"]},
    ]


@pytest.fixture
def captured(monkeypatch):
    """Stub prefilter + fit + gate; capture what reaches gate_and_store."""
    monkeypatch.setattr(run_mod, "prefilter_jobs", lambda **kw: _fake_jobs())

    box = {}

    def fake_fit(profile_id, reranked, **kw):
        for item in reranked:
            item["fit"] = {"match_pct": 90, "dimensions": {}}
            item["match_pct"] = 90
        return reranked

    def fake_gate(matches_db_path, profile_id, fitted, **kw):
        box["fitted"] = fitted
        return {"stored": len(fitted), "strong": len(fitted), "stretch": 0}

    monkeypatch.setattr(run_mod, "fit_candidates", fake_fit)
    monkeypatch.setattr(run_mod, "gate_and_store", fake_gate)
    return box


def test_hybrid_path_attaches_hybrid_and_searches_to_fit(monkeypatch, captured):
    def fake_hybrid(profile_id, survivors, cfg, features_db):
        return [{
            "job": survivors[0],
            "stage1_score": 0.7, "stage2_score": 0.8,
            "hybrid": {"total": 79.8,
                       "components": {"skills": 74.5, "bm25": 50, "embedding": 100,
                                      "domain": 100, "level": 100},
                       "explanation": {}, "scoring_version": "v1"},
        }]
    monkeypatch.setattr(run_mod, "_hybrid_rank", fake_hybrid)
    monkeypatch.setattr(run_mod, "load_config", lambda c=None: _cfg(use_hybrid_ranking=True))

    out = run_mod.run_pipeline(profile_id="default", config="")
    assert out["stored"] == 1
    fit = captured["fitted"][0]["fit"]
    assert fit["hybrid"]["components"]["skills"] == 74.5   # round-trips into fit_json
    assert fit["hybrid"]["scoring_version"] == "v1"
    assert fit["matched_searches"] == ["ml"]


def test_falls_back_to_legacy_when_hybrid_raises(monkeypatch, captured):
    def boom(*a, **k):
        raise RuntimeError("features db corrupt")

    legacy_called = {"hit": False}

    def fake_legacy(profile_id, survivors, cfg):
        legacy_called["hit"] = True
        return [{"job": survivors[0], "stage1_score": 0.5, "stage2_score": 0.5}]

    monkeypatch.setattr(run_mod, "_hybrid_rank", boom)
    monkeypatch.setattr(run_mod, "_legacy_rank", fake_legacy)
    monkeypatch.setattr(run_mod, "load_config", lambda c=None: _cfg(use_hybrid_ranking=True))

    out = run_mod.run_pipeline(profile_id="default", config="")
    assert legacy_called["hit"] is True          # rule-7 fallback actually executed
    assert out["stored"] == 1
    assert "hybrid" not in captured["fitted"][0]["fit"]  # legacy items carry no hybrid block


def test_flag_false_uses_legacy_not_hybrid(monkeypatch, captured):
    hybrid_called = {"hit": False}

    def fake_hybrid(*a, **k):
        hybrid_called["hit"] = True
        return []

    monkeypatch.setattr(run_mod, "_hybrid_rank", fake_hybrid)
    monkeypatch.setattr(run_mod, "_legacy_rank",
                       lambda pid, s, cfg: [{"job": s[0], "stage1_score": 0.5, "stage2_score": 0.5}])
    monkeypatch.setattr(run_mod, "load_config", lambda c=None: _cfg(use_hybrid_ranking=False))

    run_mod.run_pipeline(profile_id="default", config="")
    assert hybrid_called["hit"] is False         # flag off → hybrid never invoked
