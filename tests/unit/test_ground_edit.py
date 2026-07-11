"""Evidence-rule tests for tailor_edits.ground_edit (corrected rule 2):
rewrites of existing text are self-evidenced; only genuinely NEW claims gate
needs_your_call. knowledge_search is a fake — no embeddings, no DB.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

import tailor_edits  # noqa: E402

JD = "We need production RAG pipelines, Python, and Kubernetes fleet operations."

PROFILE = {
    "summary": "AI engineer building GenAI platforms.",
    "experience": [{
        "company": "Accenture", "role": "AI Engineer",
        "details": ["Built LLM workflows for enterprise clients using Python and RAG."],
    }],
    "projects": [],
    "skills": {"ml": ["Python", "RAG", "LangChain"]},
}
PROFILE_TERMS = tailor_edits.collect_profile_terms(PROFILE)


def _edit(before, after, section="experience", field="experience.0.bullets.0"):
    return {"section": section, "field": field, "before": before, "after": after,
            "reason": "test", "evidence_ref": None, "confidence": 0.8, "status": "accepted"}


def _no_hits(pid, query, k):
    return []


def _hit(score, text, ref="experience_bullet:0:0"):
    def _search(pid, query, k):
        return [{"evidence_ref": ref, "text": text, "score": score}]
    return _search


def _ground(edit, search=_no_hits, origin_ref="experience_bullet:0:0",
            profile_terms=PROFILE_TERMS):
    return tailor_edits.ground_edit(
        edit, pid="default", jd_text=JD, knowledge_search=search,
        origin_ref=origin_ref, profile_terms=profile_terms)


def test_pure_rewrite_is_self_evidenced():
    # Regression for the "every JD-aligned rewrite flags" bug: rephrasing that
    # adds only style vocabulary + profile-known terms is grounded by the original.
    out = _ground(_edit(
        "Built LLM workflows for enterprise clients using Python and RAG.",
        "Designed production RAG workflows in Python for enterprise clients.",
    ))
    assert out["status"] == "accepted"
    assert out["evidence_ref"] == "experience_bullet:0:0"
    assert "ungrounded_terms" not in out


def test_jd_term_already_in_profile_is_not_a_claim():
    out = _ground(_edit(
        "Built LLM workflows for enterprise clients.",
        "Built LLM workflows for enterprise clients with LangChain.",  # LangChain is in profile skills
    ))
    assert out["status"] == "accepted"
    assert out["evidence_ref"] == "experience_bullet:0:0"


def test_new_unbacked_claim_needs_your_call():
    out = _ground(_edit(
        "Built LLM workflows for enterprise clients.",
        "Built LLM workflows and operated Kubernetes fleet deployments.",
    ))
    assert out["status"] == "needs_your_call"
    assert "kubernetes" in out.get("ungrounded_terms", [])


def test_new_claim_with_evidence_hit_is_grounded():
    search = _hit(0.72, "Deployed services on Kubernetes clusters at Accenture",
                  ref="project:2")
    out = _ground(_edit(
        "Built LLM workflows for enterprise clients.",
        "Built LLM workflows and operated Kubernetes deployments.",
    ), search=search)
    assert out["status"] == "accepted"
    assert out["evidence_ref"] == "project:2"


def test_low_score_hit_does_not_ground():
    search = _hit(0.40, "Deployed services on Kubernetes clusters")
    out = _ground(_edit(
        "Built LLM workflows.",
        "Built LLM workflows on Kubernetes.",
    ), search=search)
    assert out["status"] == "needs_your_call"


def test_added_bullet_without_origin_still_gated():
    # New bullet (empty before, no origin): profile-known content can ground via
    # search, but with no hits it must come back to the human.
    out = _ground(_edit("", "Shipped RAG evaluation harness in Python."),
                  origin_ref=None)
    assert out["status"] == "needs_your_call"


def test_legacy_call_without_new_kwargs_does_not_crash():
    out = tailor_edits.ground_edit(
        _edit("Built LLM workflows.", "Built LLM workflows on Kubernetes."),
        pid="default", jd_text=JD, knowledge_search=_no_hits)
    assert out["status"] == "needs_your_call"


def test_ungrounded_terms_survive_validation():
    out = _ground(_edit(
        "Built LLM workflows.",
        "Built LLM workflows and Terraform provisioning.",
    ))
    revalidated = tailor_edits.validate_edits([out])
    assert revalidated and revalidated[0].get("ungrounded_terms") == out["ungrounded_terms"]
