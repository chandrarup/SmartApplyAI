"""Tailoring-quality: style lint, soften, page-fit gate (unit-level)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

import constraints as constraints_engine  # noqa: E402
import style_lint  # noqa: E402
import tailor_edits  # noqa: E402


PLANTED = (
    "I am passionate about leveraging synergies with a proven track record "
    "to drive results as a team player."
)


def test_style_lint_detects_banned_phrases():
    hits = style_lint.find_style_violations(PLANTED)
    labels = {h["label"] for h in hits}
    assert "passionate about" in labels or any("passionate" in h["label"] for h in hits)
    assert any("leverage" in h["label"] or "synergies" in h["label"] for h in hits)
    assert any("proven track record" in h["label"] for h in hits)


def test_style_lint_rewrite_loop_clears_or_flags(monkeypatch):
    def fake_llm(messages, temperature=0.2, prefer="ollama", **kwargs):
        return "I ship reliable systems with measurable outcomes."

    cleaned, flags = style_lint.lint_and_rewrite_text(PLANTED, llm_call=fake_llm)
    assert "passionate about" not in cleaned.lower()
    assert "synergies" not in cleaned.lower()
    # Either fully cleaned or remaining flags are explicit
    remaining = style_lint.find_style_violations(cleaned)
    assert remaining == [] or flags


def test_cover_letter_company_claim_flagged():
    letter = "I admire Acme Corp as a leading pioneer in AI and am excited about your mission."
    flags = style_lint.flag_cover_letter_company_claims(letter, "Acme Corp")
    assert any(f.get("kind") == "company_claim" for f in flags)


def test_page_fit_blocks_multi_page():
    pf = constraints_engine.page_fit_summary({"ok": True, "issues": []}, pdf_page_count=2)
    assert pf["blocked"] is True
    assert pf["wont_fit_one_page"] is True
    assert pf["badge"] == "overflow — fix before download"
    assert pf["pdf_page_count"] == 2


def test_page_fit_ok_for_one_page():
    pf = constraints_engine.page_fit_summary({"ok": True, "issues": []}, pdf_page_count=1)
    assert pf["blocked"] is False
    assert pf["badge"] is None


def test_soften_then_reground_can_become_grounded():
    edit = {
        "section": "experience",
        "field": "experience.0.bullets.0",
        "before": "Built LLM workflows for enterprise clients using Python and RAG.",
        "after": "Built LLM workflows and operated Kubernetes fleet deployments.",
        "reason": "JD align",
        "status": "needs_your_call",
        "stretch_level": "stretch",
        "stretch_reason": "Uses JD wording (kubernetes)",
        "confidence": 0.7,
        "evidence_ref": "project:2",
    }
    profile = {
        "summary": "AI engineer",
        "experience": [{"details": [edit["before"]]}],
        "skills": {"ml": ["Python", "RAG"]},
    }
    terms = tailor_edits.collect_profile_terms(profile)

    def fake_llm(messages, temperature=0.2, prefer="ollama", **kwargs):
        # Drop the fabricated Kubernetes claim
        return "Built LLM workflows for enterprise clients using Python and RAG."

    def search(pid, query, k):
        return [{"evidence_ref": "project:2", "text": "Kubernetes clusters", "score": 0.8}]

    out = tailor_edits.apply_soften(
        edit,
        pid="default",
        jd_text="Kubernetes fleet operations Python RAG",
        knowledge_search=search,
        llm_call=fake_llm,
        origin_ref="experience_bullet:0:0",
        profile_terms=terms,
    )
    assert out["stretch_level"] == "grounded"
    assert out["status"] == "accepted"
    assert "kubernetes" not in (out.get("after") or "").lower()
