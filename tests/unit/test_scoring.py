"""Unit tests for the shared hybrid scorer (backend/scoring.py).

No live LLM or embedding calls — knowledge_search and llm_call are injected fakes.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

import scoring  # noqa: E402

PROFILE = {
    "summary": "AI engineer building production GenAI and RAG systems.",
    "autofill": {"current_title": "AI/ML Engineer"},
    "skills": {"languages": ["Python", "SQL"], "ml": ["PyTorch", "LangChain"]},
    "education": [
        {
            "degree": "B.Tech in Computer Science and Engineering (Specialization in Artificial Intelligence)",
            "university": "Amrita",
            "details": "Deep Learning, NLP, Computer Vision coursework.",
        }
    ],
    "publications": [{"title": "Deepfake Detection using Transfer Learning"}],
    "research_interests": ["Large Language Models", "Agentic AI Systems"],
}


def _search_none(pid, query, k):
    return []


def _search_hit(score, text="Built production RAG pipelines with LangChain"):
    def _search(pid, query, k):
        return [{"evidence_ref": "experience_bullet:0:0", "text": text, "score": score}]
    return _search


# ── judge_requirement ────────────────────────────────────────────────
def test_lexical_match_is_met():
    j = scoring.judge_requirements("default", ["Python"], PROFILE, _search_none)[0]
    assert j["verdict"] == "met" and j["basis"] == "lexical"


def test_semantic_high_score_is_met():
    j = scoring.judge_requirement("default", "retrieval systems xyz", "", _search_hit(0.82))
    assert j["verdict"] == "met" and j["basis"] == "semantic"


def test_semantic_mid_score_is_equivalent():
    j = scoring.judge_requirement("default", "retrieval systems xyz", "", _search_hit(0.60))
    assert j["verdict"] == "equivalent"


def test_low_score_is_gap():
    j = scoring.judge_requirement("default", "kubernetes fleet ops", "", _search_hit(0.30))
    assert j["verdict"] == "gap"


def test_degree_field_equivalence():
    # "CS or related field" style requirement met via B.Tech CS-AI degree.
    j = scoring.judge_requirement(
        "default", "Degree in Data Science or related field", "", _search_none, profile=PROFILE
    )
    assert j["verdict"] == "equivalent" and j["basis"] == "degree_rule"


def test_production_llm_equivalence_is_lexical_met():
    j = scoring.judge_requirements(
        "default", ["integrate LLMs into production"], PROFILE, _search_none
    )[0]
    assert j["verdict"] == "met"


# ── compute_match_score ──────────────────────────────────────────────
def _j(req, verdict):
    return {"requirement": req, "verdict": verdict, "basis": "test", "evidence": []}


def test_all_met_scores_100_with_empty_gaps():
    scored = scoring.compute_match_score([_j("a", "met"), _j("b", "met")])
    assert scored["score"] == 100
    assert scored["band"] == "excellent"
    assert scored["gaps"] == []  # empty gaps list is valid and expected


def test_equivalent_scores_high_not_penalized():
    scored = scoring.compute_match_score([_j("a", "met"), _j("b", "equivalent")])
    assert scored["score"] == 95
    assert "b" in scored["equivalent"] and "b" not in scored["gaps"]


def test_met_requirement_never_in_gaps():
    scored = scoring.compute_match_score([_j("a", "met"), _j("b", "gap")])
    assert scored["gaps"] == ["b"]
    assert "a" not in scored["gaps"]


def test_nice_to_haves_weighted_half():
    scored = scoring.compute_match_score([_j("a", "met")], [_j("n", "gap")])
    # (2*1 + 1*0) / 3 = 66.7 → 67
    assert scored["score"] == 67


def test_empty_judgments_returns_none_score():
    scored = scoring.compute_match_score([])
    assert scored["score"] is None and scored["gaps"] == []


# ── score_after_tailoring ────────────────────────────────────────────
def test_tailoring_upgrades_covered_gap_to_partial():
    base = [_j("Kubernetes", "gap"), _j("Python", "met")]
    before = scoring.compute_match_score(base)["score"]  # 50
    after = scoring.score_after_tailoring(base, ["Deployed services on Kubernetes clusters"])
    assert after["score"] > before
    assert "Kubernetes" in after["equivalent"]  # partial groups with equivalent
    # Original judgments untouched.
    assert base[0]["verdict"] == "gap"


def test_tailoring_without_coverage_keeps_score():
    base = [_j("Kubernetes", "gap"), _j("Python", "met")]
    after = scoring.score_after_tailoring(base, ["Wrote Python data pipelines"])
    assert after["score"] == scoring.compute_match_score(base)["score"]


# ── extract_jd_requirements ──────────────────────────────────────────
JD = """Machine Learning Engineer at Initech.
Required: Python, PyTorch, and production RAG experience.
Nice to have: Kubernetes."""


def _fake_llm(response):
    def _call(messages, temperature=0.1, prefer="ollama", **kw):
        return response
    return _call


def test_extraction_drops_fabricated_skills():
    canned = (
        '{"role": "ML Engineer", "company": "Initech", "level": "Mid", "summary": "Builds ML.",'
        '"responsibilities": ["Ship models"],'
        '"must_have_skills": [{"skill": "Python"}, {"skill": "Golang"}],'
        '"nice_to_have_skills": [{"skill": "Kubernetes"}],'
        '"keywords": ["PyTorch", "Terraform"]}'
    )
    out = scoring.extract_jd_requirements(JD, llm_call=_fake_llm(canned))
    musts = [s["skill"] for s in out["must_have_skills"]]
    assert "Python" in musts
    assert "Golang" not in musts  # not in JD → fabricated → dropped
    assert out["keywords"] == ["PyTorch"]  # Terraform not in JD
    assert out["company"] == "Initech"


def test_extraction_no_score_or_gap_keys():
    canned = (
        '{"role": "ML Engineer", "company": "Initech", "level": "Mid", "summary": "s",'
        '"responsibilities": [], "must_have_skills": [], "nice_to_have_skills": [],'
        '"keywords": [], "match_score": 55, "gaps": ["fake gap"]}'
    )
    out = scoring.extract_jd_requirements(JD, llm_call=_fake_llm(canned))
    assert "match_score" not in out and "gaps" not in out


def test_extraction_invalid_json_raises():
    with pytest.raises(Exception):
        scoring.extract_jd_requirements(JD, llm_call=_fake_llm("not json at all {"))


# ── borderline adjudication ──────────────────────────────────────────
def test_borderline_adjudication_upgrades_and_fails_soft():
    search = _search_hit(0.50, text="Research publications on predictive models")
    upgraded = scoring.judge_requirements(
        "default", ["predictive analytics experience zz"], {"skills": {}}, search,
        adjudicate_borderline=True,
        llm_call=_fake_llm('[{"item": 1, "verdict": "equivalent"}]'),
    )
    assert upgraded[0]["verdict"] == "equivalent"
    assert upgraded[0]["basis"] == "llm_adjudicated"

    # LLM garbage → deterministic verdict stands, no exception.
    kept = scoring.judge_requirements(
        "default", ["predictive analytics experience zz"], {"skills": {}}, search,
        adjudicate_borderline=True,
        llm_call=_fake_llm("garbage {{"),
    )
    assert kept[0]["verdict"] == "gap"


# ── Five-dimension scorer ────────────────────────────────────────────
def test_weighted_match_pct_exact():
    dims = {
        "technical_skills": {"score": 100, "note": ""},
        "experience_match": {"score": 100, "note": ""},
        "education_fit": {"score": 100, "note": ""},
        "career_alignment": {"score": 100, "note": ""},
    }
    assert scoring.weighted_match_pct(dims) == 100
    # 0.35*80 + 0.30*70 + 0.15*90 + 0.20*60 = 28+21+13.5+12 = 74.5 → 74 or 75
    dims2 = {
        "technical_skills": {"score": 80},
        "experience_match": {"score": 70},
        "education_fit": {"score": 90},
        "career_alignment": {"score": 60},
    }
    assert scoring.weighted_match_pct(dims2) == 74


def test_queue_band_boundaries_70_and_85():
    assert scoring.queue_band(69) == "below"
    assert scoring.queue_band(70) == "stretch"
    assert scoring.queue_band(84) == "stretch"
    assert scoring.queue_band(85) == "strong"
    assert scoring.queue_band(100) == "strong"


def test_assemble_fit_knockout_zeros_overall():
    fit = scoring.assemble_fit(
        {
            "technical_skills": {"score": 95, "note": "strong"},
            "experience_match": {"score": 90, "note": "strong"},
            "education_fit": {"score": 90, "note": "MS"},
            "career_alignment": {"score": 90, "note": "AI path"},
        },
        knockouts={"location": "fail", "work_auth": "pass"},
    )
    assert fit["match_pct"] == 0
    assert fit["band"] == "below"
    assert fit["dimensions"]["technical_skills"]["score"] == 95  # subscores retained


def test_overqualification_never_lowers_via_assemble():
    # Exceeding requirements → high experience_match is valid; assemble does not
    # clamp down for "too senior".
    fit = scoring.assemble_fit(
        {
            "technical_skills": {"score": 92, "note": "exceeds stack"},
            "experience_match": {"score": 95, "note": "more years than required — still high"},
            "education_fit": {"score": 88, "note": "MS"},
            "career_alignment": {"score": 90, "note": "AI/ML intern stepping stone"},
        }
    )
    assert fit["dimensions"]["experience_match"]["score"] == 95
    assert fit["match_pct"] >= 85
    assert fit["band"] == "strong"


def test_internship_career_alignment_not_penalized_in_prompt():
    prompt = scoring._five_dim_prompt("AI Research Intern", "MS student", title="Intern")
    assert "NEVER penalize internship" in prompt
    assert "OVERQUALIFICATION NEVER lowers" in prompt
    assert "technical_skills (weight 0.35" in prompt
    assert "career_alignment (weight 0.20" in prompt


def test_search_boost_caps_career_alignment():
    fit = scoring.assemble_fit(
        {
            "technical_skills": {"score": 80, "note": ""},
            "experience_match": {"score": 80, "note": ""},
            "education_fit": {"score": 80, "note": ""},
            "career_alignment": {"score": 98, "note": "aligned"},
        },
        search_boost=5,
    )
    assert fit["dimensions"]["career_alignment"]["score"] == 100  # capped
    assert "search boost" in fit["dimensions"]["career_alignment"]["note"].lower()


def test_work_auth_knockout_when_sponsorship_forbidden():
    profile = {
        "contact_info": {"location": "Houston, TX"},
        "autofill": {"requires_sponsorship": True},
    }
    ko = scoring.evaluate_knockouts(
        "Must be a US citizen. Sponsorship not available.", profile
    )
    assert ko["work_auth"] == "fail"


def test_remote_jd_passes_location_knockout():
    profile = {"contact_info": {"location": "Houston, TX"}, "autofill": {}}
    ko = scoring.evaluate_knockouts(
        "Remote US-based Machine Learning Intern. Hybrid OK.", profile
    )
    assert ko["location"] == "pass" and ko["work_auth"] == "pass"


def test_score_job_with_fake_llm():
    canned = json.dumps({
        "dimensions": {
            "technical_skills": {"score": 88, "note": "Python + RAG covered"},
            "experience_match": {"score": 82, "note": "GenAI platform work"},
            "education_fit": {"score": 90, "note": "MS in AI"},
            "career_alignment": {"score": 85, "note": "AI/ML intern path"},
        },
        "matched_skills": [{"skill": "Python", "evidence_ref": "skills"}],
        "missing_skills": [{"skill": "Kubernetes", "evidence_ref": ""}],
        "best_projects": [{"title": "SLM from Scratch", "why": "LLM internals"}],
        "rationale": "Strong AI/ML intern fit",
    })
    fit = scoring.score_job(
        "ML Intern. Required: Python, RAG. Remote US.",
        PROFILE,
        title="ML Intern",
        company="Initech",
        llm_call=_fake_llm(canned),
    )
    assert fit["match_pct"] == scoring.weighted_match_pct(fit["dimensions"])
    assert fit["band"] in ("strong", "stretch")
    assert fit["dimensions"]["technical_skills"]["score"] == 88
    assert fit["matched_skills"][0]["skill"] == "Python"
    assert fit["knockouts"]["location"] == "pass"


def test_score_job_llm_failure_falls_back():
    fit = scoring.score_job("JD", PROFILE, llm_call=_fake_llm("not-json{{{"))
    assert fit["match_pct"] == 0
    assert "failed" in fit["rationale"].lower() or "Fit parsing failed" in fit["rationale"]


def test_fit_from_requirement_score_bridge():
    req = scoring.compute_match_score([_j("Python", "met"), _j("K8s", "gap")])
    fit = scoring.fit_from_requirement_score(req, career_score=80)
    assert fit["dimensions"]["technical_skills"]["score"] == req["score"]
    assert fit["match_pct"] == scoring.weighted_match_pct(fit["dimensions"])


def test_apply_tailoring_to_fit_never_lowers_tech():
    base_fit = scoring.assemble_fit(
        {
            "technical_skills": {"score": 70, "note": "pre"},
            "experience_match": {"score": 80, "note": ""},
            "education_fit": {"score": 80, "note": ""},
            "career_alignment": {"score": 80, "note": ""},
        }
    )
    # Tailoring covers the gap → technical should rise, not fall.
    updated = scoring.apply_tailoring_to_fit(
        base_fit,
        [_j("Kubernetes", "gap"), _j("Python", "met")],
        ["Deployed on Kubernetes"],
    )
    assert updated["dimensions"]["technical_skills"]["score"] >= 70
    assert updated["dimensions"]["experience_match"]["score"] == 80

