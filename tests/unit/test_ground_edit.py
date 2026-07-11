"""Evidence-rule + three-band stretch_level tests for tailor_edits.ground_edit.

Rewrites of existing text are self-evidenced (grounded). New claims with evidence
are stretch (Keep/Soften/Drop). Unbacked new claims are fabrications (auto-reject).
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
    out = _ground(_edit(
        "Built LLM workflows for enterprise clients using Python and RAG.",
        "Designed production RAG workflows in Python for enterprise clients.",
    ))
    assert out["status"] == "accepted"
    assert out["stretch_level"] == "grounded"
    assert out["evidence_ref"] == "experience_bullet:0:0"
    assert "ungrounded_terms" not in out


def test_jd_term_already_in_profile_is_not_a_claim():
    out = _ground(_edit(
        "Built LLM workflows for enterprise clients.",
        "Built LLM workflows for enterprise clients with LangChain.",
    ))
    assert out["status"] == "accepted"
    assert out["stretch_level"] == "grounded"
    assert out["evidence_ref"] == "experience_bullet:0:0"


def test_new_unbacked_claim_is_fabrication():
    out = _ground(_edit(
        "Built LLM workflows for enterprise clients.",
        "Built LLM workflows and operated Kubernetes fleet deployments.",
    ))
    assert out["status"] == "rejected"
    assert out["stretch_level"] == "fabrication"
    assert "kubernetes" in out.get("ungrounded_terms", [])


def test_new_claim_with_evidence_hit_is_stretch():
    search = _hit(0.72, "Deployed services on Kubernetes clusters at Accenture",
                  ref="project:2")
    out = _ground(_edit(
        "Built LLM workflows for enterprise clients.",
        "Built LLM workflows and operated Kubernetes deployments.",
    ), search=search)
    assert out["status"] == "needs_your_call"
    assert out["stretch_level"] == "stretch"
    assert out["evidence_ref"] == "project:2"
    assert "stretch_reason" in out


def test_low_score_hit_is_fabrication():
    search = _hit(0.40, "Deployed services on Kubernetes clusters")
    out = _ground(_edit(
        "Built LLM workflows.",
        "Built LLM workflows on Kubernetes.",
    ), search=search)
    assert out["status"] == "rejected"
    assert out["stretch_level"] == "fabrication"


def test_added_bullet_without_origin_is_stretch():
    # New bullet of profile-known content (no origin): stretch for human Keep/Drop.
    out = _ground(
        _edit("", "Built LLM workflows for enterprise clients using Python and RAG."),
        origin_ref=None,
    )
    assert out["status"] == "needs_your_call"
    assert out["stretch_level"] == "stretch"


def test_legacy_call_without_new_kwargs_does_not_crash():
    out = tailor_edits.ground_edit(
        _edit("Built LLM workflows.", "Built LLM workflows on Kubernetes."),
        pid="default", jd_text=JD, knowledge_search=_no_hits)
    assert out["stretch_level"] == "fabrication"
    assert out["status"] == "rejected"


def test_ungrounded_terms_survive_validation():
    out = _ground(_edit(
        "Built LLM workflows.",
        "Built LLM workflows and Terraform provisioning.",
    ))
    revalidated = tailor_edits.validate_edits([out])
    assert revalidated and revalidated[0].get("ungrounded_terms") == out["ungrounded_terms"]
    assert revalidated[0].get("stretch_level") == "fabrication"


def test_renderable_edits_omits_fabrications():
    fab = _ground(_edit(
        "Built LLM workflows.",
        "Built LLM workflows on Kubernetes.",
    ))
    grounded = _ground(_edit(
        "Built LLM workflows for enterprise clients using Python and RAG.",
        "Designed production RAG workflows in Python for enterprise clients.",
    ))
    visible = tailor_edits.renderable_edits([fab, grounded])
    assert len(visible) == 1
    assert visible[0]["stretch_level"] == "grounded"
