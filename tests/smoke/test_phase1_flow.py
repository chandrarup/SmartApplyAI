"""
Smoke tests for the supported Phase 1 dashboard workflow.

These tests avoid external AI or PDF tool dependencies by patching the
provider and compiler layers. They verify that the backend surface remains
coherent for local development.
"""

import json
import os
import sys
from types import SimpleNamespace

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

import main  # noqa: E402


def _client():
    return TestClient(main.app)


def _fake_call_llm(messages, temperature=0.3, system="", prefer="ollama", timeout=600):
    prompt = messages[-1]["content"]
    if '"must_have_skills"' in prompt and '"keywords"' in prompt:
        return json.dumps({
            "role": "Machine Learning Engineer",
            "company": "Example AI",
            "level": "Intern",
            "summary": "Builds ML features and collaborates on production data work.",
            "responsibilities": ["Build models", "Work with data", "Support experiments"],
            "must_have_skills": [{"skill": "Python", "matched": True}],
            "nice_to_have_skills": [{"skill": "AWS", "matched": False}],
            "keywords": ["Python", "Machine Learning", "AWS"],
            "match_score": 100,
            "gaps": ["AWS"],
            "recommendations": ["Highlight deployment work"],
        })
    if '"tailored_summary"' in prompt and '"score_estimate"' in prompt and "SOURCE SUMMARY" in prompt:
        return json.dumps({
            "tailored_summary": "Machine learning engineer with production Python and applied AI experience.",
            "summary_diff": {
                "original": "Original summary",
                "tailored": "Machine learning engineer with production Python and applied AI experience.",
            },
            "keywords_inserted": ["Python"],
            "score_estimate": 91,
        })
    if '"experience"' in prompt and '"keywords_inserted"' in prompt and "EXPERIENCE ENTRY TO EDIT" in prompt:
        return json.dumps({
            "experience": [{
                "company": "Accenture (GenWizard Platform)",
                "title": "Advanced App Engineering Analyst - GenAI Specialist",
                "dates": "Aug 2023 - Aug 2025",
                "bullets": [{
                    "text": "Built production Python workflows for machine learning delivery.",
                    "status": "edited",
                    "original": "Built production Python workflows."
                }]
            }],
            "keywords_inserted": ["Python"],
        })
    if '"skills_matched"' in prompt and '"tailored_summary"' in prompt:
        return json.dumps({
            "role": "Machine Learning Engineer",
            "skills_matched": ["Python", "Machine Learning"],
            "missing_skill": "AWS",
            "score": 82,
            "tailored_summary": "Strong Python and machine learning background with room to deepen AWS exposure.",
            "selected_projects": [],
        })
    raise AssertionError(f"Unexpected prompt shape: {prompt[:200]}")


def _ok_validation(*args, **kwargs):
    return SimpleNamespace(ok=True, violations=[], fatal_violations=[])


def test_health_and_profile_load():
    client = _client()
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["message"] == "Server is Online"

    profile = client.get("/profile", headers={"X-Profile-ID": "default"})
    assert profile.status_code == 200
    assert "contact_info" in profile.json()


def test_analyze_deep_and_tailor_resume(monkeypatch):
    monkeypatch.setattr(main, "call_llm", _fake_call_llm)
    monkeypatch.setattr(main.constraints_engine, "validate_tailored_resume", _ok_validation)

    client = _client()

    analysis = client.post(
        "/analyze-deep",
        json={
            "jd_text": "Machine Learning Engineer role requiring Python and AWS experience.",
            "company": "Example AI",
            "role": "Machine Learning Engineer",
            "llm": "ollama",
        },
        headers={"X-Profile-ID": "default"},
    )
    assert analysis.status_code == 200
    analysis_json = analysis.json()
    assert analysis_json["role"] == "Machine Learning Engineer"
    assert analysis_json["must_have_skills"][0]["skill"] == "Python"

    tailored = client.post(
        "/tailor-resume",
        json={
            "jd_text": "Machine Learning Engineer role requiring Python and AWS experience.",
            "company": "Example AI",
            "role": "Machine Learning Engineer",
            "selected_skills": ["Python"],
            "llm": "ollama",
        },
        headers={"X-Profile-ID": "default"},
    )
    assert tailored.status_code == 200
    tailored_json = tailored.json()
    assert tailored_json["score_estimate"] == 91
    assert tailored_json["_validation"]["ok"] is True


def test_generate_pdf_returns_clear_missing_tool_error(monkeypatch):
    def fake_compile_with_retry(*args, **kwargs):
        result = main.compile_loop.CompileResult()
        result.attempts = 1
        result.errors.append({
            "type": "missing_binary",
            "line": None,
            "message": "pdflatex not found",
        })
        return result

    monkeypatch.setattr(main.compile_loop, "compile_with_retry", fake_compile_with_retry)

    client = _client()
    response = client.post(
        "/generate-pdf",
        json={
            "tailored_summary": "Tailored summary",
            "_company": "Example AI",
            "_role": "Machine Learning Engineer",
            "_jd": "Sample JD",
        },
        headers={"X-Profile-ID": "default"},
    )
    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail["error"] == "Local PDF toolchain is missing."
    assert "pdflatex" in detail["missing_tools"]
