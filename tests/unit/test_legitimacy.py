"""Unit tests for matcher legitimacy (Block G). No live web calls."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from matcher.legitimacy import (  # noqa: E402
    assess_contractor_language,
    assess_description_quality,
    assess_legitimacy,
    assess_role_plausibility,
    tier_from_signals,
)


def test_tech_specificity_is_info():
    jd = "Required: Python, PyTorch, LangChain, AWS, Kubernetes. You will build RAG pipelines."
    sigs = assess_description_quality(jd)
    codes = {s["code"] for s in sigs}
    assert "tech_specificity" in codes
    assert all(s["severity"] != "concern" or s["code"] != "tech_specificity" for s in sigs)


def test_boilerplate_heavy_is_concern():
    jd = (
        "We are an equal opportunity employer. Affirmative action. Diversity and inclusion. "
        "Reasonable accommodation. All qualified applicants. Without regard to race. "
        "Join our team in a fast-paced environment. Rockstar ninja wanted."
    )
    sigs = assess_description_quality(jd)
    assert any(s["code"] == "boilerplate_heavy" for s in sigs)


def test_contractor_language_is_note_not_accusation():
    sigs = assess_contractor_language("This is a 1099 independent contractor role; W-2 not provided.")
    assert len(sigs) == 1
    assert sigs[0]["code"] == "contractor_language"
    assert sigs[0]["severity"] == "info"
    assert "not a scam" in sigs[0]["detail"].lower() or "note only" in sigs[0]["detail"].lower()


def test_intern_with_many_years_is_concern():
    sigs = assess_role_plausibility(
        "AI Research Intern",
        "Requirements: 7+ years of experience with production ML systems.",
    )
    assert any(s["code"] == "title_years_mismatch" for s in sigs)


def test_tier_suspicious_needs_multiple_concerns():
    assert tier_from_signals([]) == "caution"
    assert tier_from_signals([{"code": "a", "detail": "", "severity": "info"}]) == "high_confidence"
    assert tier_from_signals([{"code": "a", "detail": "", "severity": "concern"}]) == "caution"
    assert (
        tier_from_signals(
            [
                {"code": "a", "detail": "", "severity": "concern"},
                {"code": "b", "detail": "", "severity": "concern"},
                {"code": "c", "detail": "", "severity": "concern"},
            ]
        )
        == "suspicious"
    )


def test_assess_legitimacy_never_changes_match_and_web_only_for_strong():
    job = {
        "title": "ML Intern",
        "company": "Acme",
        "description_text": "Python PyTorch LangChain. You will build models. Responsibilities include training.",
        "first_seen": datetime.now(timezone.utc).isoformat(),
    }
    web_calls: list[str] = []

    def fake_search(q: str):
        web_calls.append(q)
        return [{"title": "hit", "snippet": "layoff"}]

    # Stretch — no web
    out = assess_legitimacy(job, match_pct=80, enable_web=True, web_search=fake_search)
    assert out["tier"] in ("high_confidence", "caution", "suspicious")
    assert web_calls == []
    assert "not accusations" in out["note"].lower() or "Observations" in out["note"]

    # Strong — ≤2 web queries
    out2 = assess_legitimacy(job, match_pct=90, enable_web=True, web_search=fake_search)
    assert len(web_calls) == 2
    assert out2["tier"] in ("high_confidence", "caution", "suspicious")


def test_empty_jd_is_caution_not_auto_suspicious():
    out = assess_legitimacy({"title": "X", "company": "Y", "description_text": ""}, match_pct=90, enable_web=False)
    assert out["tier"] in ("caution", "suspicious")
    # Single empty_jd concern alone → caution
    assert out["tier"] == "caution"


def test_old_posting_age_signal():
    old = (datetime.now(timezone.utc) - timedelta(days=75)).isoformat()
    out = assess_legitimacy(
        {
            "title": "Engineer",
            "company": "Co",
            "description_text": "Python AWS Docker Kubernetes. You will own services. Requirements listed.",
            "first_seen": old,
        },
        match_pct=70,
        enable_web=False,
    )
    assert any(s["code"] == "posting_age" for s in out["signals"])
