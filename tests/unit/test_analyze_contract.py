"""/analyze contract tests — grounded analysis, no forced gaps, backward-compatible
response shape. All LLM and embedding calls are mocked; no live providers.
"""

from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

import main  # noqa: E402
import scoring  # noqa: E402

JD = """Machine Learning Engineer at Initech.
Required: Python, PyTorch, production RAG experience.
Nice to have: Kubernetes."""

PROFILE = {
    "contact_info": {"name": "Test User"},
    "summary": "AI engineer building production GenAI and RAG systems with Python and PyTorch.",
    "autofill": {"current_title": "AI/ML Engineer"},
    "skills": {"languages": ["Python"], "ml": ["PyTorch", "RAG"]},
    "education": [],
    "experience": [],
    "projects": [{"title": "RAG Search", "description": "Retrieval-augmented search with Python"}],
}

EXTRACTION_JSON = (
    '{"role": "ML Engineer", "company": "Initech", "level": "Mid", "summary": "Builds ML systems.",'
    '"responsibilities": ["Ship models"],'
    '"must_have_skills": [{"skill": "Python"}, {"skill": "PyTorch"}, {"skill": "RAG"}],'
    '"nice_to_have_skills": [{"skill": "Kubernetes"}],'
    '"keywords": ["Python", "PyTorch"]}'
)
SUMMARY_JSON = (
    '{"tailored_summary": "Machine learning engineer shipping production RAG systems in Python, '
    'with hands-on PyTorch model development and deployment."}'
)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "load_pdata", lambda pid: PROFILE)
    monkeypatch.setattr(main.knowledge_semantic, "search",
                        lambda pid, q, k=10, kind_filter=None: [])

    FIVE_DIM_JSON = (
        '{"dimensions":{'
        '"technical_skills":{"score":92,"note":"Python PyTorch RAG"},'
        '"experience_match":{"score":88,"note":"GenAI work"},'
        '"education_fit":{"score":80,"note":"relevant"},'
        '"career_alignment":{"score":90,"note":"AI/ML path"}},'
        '"matched_skills":[{"skill":"Python","evidence_ref":""}],'
        '"missing_skills":[],'
        '"best_projects":[{"title":"RAG Search","why":"direct"}],'
        '"rationale":"Strong ML fit"}'
    )

    def _scoring_llm(messages, temperature=0.1, prefer="ollama", **kw):
        text = " ".join(str(m.get("content", "")) for m in (messages or []))
        if "Score EACH dimension" in text or "ANCHORED BANDS" in text:
            return FIVE_DIM_JSON
        return EXTRACTION_JSON

    monkeypatch.setattr(scoring, "call_llm", _scoring_llm)
    monkeypatch.setattr(main, "call_llm", lambda *a, **kw: SUMMARY_JSON)
    return TestClient(main.app)


def _post(client):
    r = client.post("/analyze", json={"jd_text": JD, "llm": "ollama"},
                    headers={"X-Profile-ID": "default"})
    assert r.status_code == 200, r.text
    return r.json()


def test_legacy_contract_keys_present(client):
    out = _post(client)
    for key in ("role", "score", "skills_matched", "missing_skill",
                "tailored_summary", "selected_projects"):
        assert key in out, f"legacy key {key} missing"
    assert out["role"] == "ML Engineer"
    assert out["score"].endswith("%")


def test_met_requirements_never_gaps_and_empty_gaps_valid(client):
    out = _post(client)
    # Python/PyTorch/RAG/Kubernetes... Kubernetes is not in the profile.
    assert "Python" in out["skills_matched"]
    assert "Python" not in out["missing_skills"]
    assert "PyTorch" not in out["missing_skills"]
    # No forced minimum: only the genuinely absent requirement may appear.
    assert set(out["missing_skills"]) <= {"Kubernetes"}
    assert out["missing_skill"] in ("", "Kubernetes")


def test_all_met_gives_empty_gaps(client, monkeypatch):
    profile = dict(PROFILE, skills={"all": ["Python", "PyTorch", "RAG", "Kubernetes"]})
    monkeypatch.setattr(main, "load_pdata", lambda pid: profile)
    out = _post(client)
    assert out["missing_skills"] == []
    assert out["missing_skill"] == ""
    assert int(out["score"].rstrip("%")) >= 85


def test_summary_from_own_call_and_fallback(client, monkeypatch):
    out = _post(client)
    assert out["summary_fallback"] is False
    assert "Machine learning engineer" in out["tailored_summary"]

    # Summary call dies → master summary, request still succeeds.
    def _boom(*a, **kw):
        raise RuntimeError("summary provider down")
    monkeypatch.setattr(main, "call_llm", _boom)
    out2 = _post(client)
    assert out2["summary_fallback"] is True
    assert out2["tailored_summary"] == PROFILE["summary"]


def test_extraction_failure_is_500(client, monkeypatch):
    def _boom(*a, **kw):
        raise RuntimeError("extraction provider down")
    monkeypatch.setattr(scoring, "call_llm", _boom)
    r = client.post("/analyze", json={"jd_text": JD, "llm": "ollama"},
                    headers={"X-Profile-ID": "default"})
    assert r.status_code == 500


def test_deep_block_matches_analyze_deep_contract(client):
    out = _post(client)
    deep = out["deep"]
    for key in ("role", "company", "must_have_skills", "nice_to_have_skills",
                "keywords", "match_score", "gaps", "jd_extracted"):
        assert key in deep
    musts = {s["skill"]: s["matched"] for s in deep["must_have_skills"]}
    assert musts["Python"] is True
    assert deep["match_score"] == int(out["score"].rstrip("%"))
