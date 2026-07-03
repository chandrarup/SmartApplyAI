"""Regression tests for the chore/cleanup-batch pass.

Covers:
  - page-fit flag: preflight overflow → hard, visible queue flag (FINDINGS_tailoring §5)
  - teach gap sourcing: gaps.yaml manual seeds + frequency-weighted matcher missing_skills
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

import constraints as constraints_engine  # noqa: E402
from matcher import store as matcher_store  # noqa: E402
from teach import gaps as teach_gaps  # noqa: E402


# ── page-fit flag ─────────────────────────────────────────────────────────────
def test_page_fit_summary_flags_overflow():
    preflight = {
        "ok": True,
        "issues": [
            {"severity": "warn", "kind": "page_overflow",
             "message": "Experience section grew ~30 words total — 1-page PDF at risk."},
            {"severity": "warn", "kind": "keyword_stuffing", "message": "unrelated"},
        ],
    }
    summary = constraints_engine.page_fit_summary(preflight)
    assert summary["wont_fit_one_page"] is True
    assert any("1-page PDF at risk" in r for r in summary["reasons"])
    # non-page-fit warnings do not leak into the reasons list
    assert all("unrelated" not in r for r in summary["reasons"])


def test_page_fit_summary_clean_when_no_overflow():
    preflight = {"ok": True, "issues": [
        {"severity": "warn", "kind": "authenticity", "message": "buzzword"},
    ]}
    summary = constraints_engine.page_fit_summary(preflight)
    assert summary == {"wont_fit_one_page": False, "reasons": []}


def test_page_fit_summary_handles_none_and_empty():
    assert constraints_engine.page_fit_summary(None) == {"wont_fit_one_page": False, "reasons": []}
    assert constraints_engine.page_fit_summary({}) == {"wont_fit_one_page": False, "reasons": []}


def test_page_fit_summary_catches_all_overflow_kinds():
    for kind in ("bullet_growth", "skills_overflow", "summary_length"):
        pf = {"issues": [{"severity": "warn", "kind": kind, "message": f"{kind} msg"}]}
        assert constraints_engine.page_fit_summary(pf)["wont_fit_one_page"] is True


def test_real_preflight_overflow_surfaces_as_flag():
    """End-to-end: a genuinely overgrown resume trips preflight, which the flag exposes."""
    profile = {"summary": "Engineer.", "experience": []}
    tailored = {
        "tailored_summary": "Engineer.",
        "experience": [{
            "company": "Acme",
            "bullets": [
                {"text": ("Built " + "systems and pipelines and services " * 8),
                 "original": "Built systems.", "status": "edited"},
                {"text": ("Led " + "teams and projects and launches " * 8),
                 "original": "Led teams.", "status": "edited"},
            ],
        }],
    }
    preflight = constraints_engine.preflight_tailored_resume(profile, tailored)
    flag = constraints_engine.page_fit_summary(preflight)
    assert flag["wont_fit_one_page"] is True


# ── teach gap sourcing ────────────────────────────────────────────────────────
def _seed_matches(db_path, pid="default"):
    """Store matches whose fit carries missing_skills at varying frequencies."""
    fitted = [
        {"job": {"source_ats": "gh", "external_id": "1", "company": "A", "title": "MLE"},
         "match_pct": 90, "stage1_score": 0.9, "stage2_score": 0.9,
         "fit": {"missing_skills": ["Kubernetes", "Ray", {"skill": "gRPC"}]}},
        {"job": {"source_ats": "gh", "external_id": "2", "company": "B", "title": "MLE"},
         "match_pct": 80, "stage1_score": 0.8, "stage2_score": 0.8,
         "fit": {"missing_skills": ["Kubernetes", "Ray"]}},
        {"job": {"source_ats": "gh", "external_id": "3", "company": "C", "title": "MLE"},
         "match_pct": 75, "stage1_score": 0.7, "stage2_score": 0.7,
         "fit": {"missing_skills": ["kubernetes"]}},  # casing folds together
    ]
    matcher_store.gate_and_store(db_path, pid, fitted)


def test_matcher_gap_skills_frequency_weighted(tmp_path):
    db = str(tmp_path / "matches.db")
    _seed_matches(db)
    ranked = teach_gaps.matcher_gap_skills(db, "default")
    # Kubernetes appears in 3 matches, Ray in 2, gRPC in 1.
    assert ranked[0] == ("Kubernetes", 3)
    assert ("Ray", 2) in ranked
    assert ("gRPC", 1) in ranked
    # deterministic descending-frequency order
    freqs = [f for _, f in ranked]
    assert freqs == sorted(freqs, reverse=True)


def test_matcher_gap_skills_missing_db_is_empty(tmp_path):
    assert teach_gaps.matcher_gap_skills(str(tmp_path / "nope.db"), "default") == []


def test_merge_keeps_manual_seeds_first():
    manual = ["Graphiti", "temporal knowledge graph"]
    matcher_ranked = [("Kubernetes", 3), ("Graphiti", 5), ("Ray", 2)]
    merged = teach_gaps.merge_gap_skills(manual, matcher_ranked)
    # manual seeds lead, in their original order; matcher gaps follow; no dupes
    assert merged[:2] == ["Graphiti", "temporal knowledge graph"]
    assert merged.count("Graphiti") == 1
    assert "Kubernetes" in merged and "Ray" in merged


def test_load_gap_skills_merged(tmp_path):
    db = str(tmp_path / "matches.db")
    _seed_matches(db)
    gaps_file = tmp_path / "gaps.yaml"
    gaps_file.write_text("skills:\n  - Graphiti\n  - Kubernetes\n", encoding="utf-8")
    merged = teach_gaps.load_gap_skills_merged(str(gaps_file), db, "default")
    assert merged[:2] == ["Graphiti", "Kubernetes"]  # manual seeds first
    assert merged.count("Kubernetes") == 1           # matcher dupe folded in
    assert "Ray" in merged                            # matcher-only gap appended
