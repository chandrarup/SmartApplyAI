"""Unit tests for resume tailoring helpers in main.py."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

import main  # noqa: E402
import constraints as constraints_engine  # noqa: E402


def test_trim_skills_drops_low_priority():
    skills = {
        "domains": ["Python", "LangChain", "OpenAI", "Claude", "Gemini", "AutoGen", "CrewAI", "RAG", "Extra1", "Extra2", "Extra3"],
        "languages": ["Python (Expert)", "SQL", "Java"],
    }
    jd = "Seeking LangChain and RAG experience with Python."
    trimmed, removed = main._trim_skills_lists(skills, jd, selected_skill_names=["RAG"], max_per_category=8)
    assert "LangChain" in trimmed["domains"]
    assert "RAG" in trimmed["domains"]
    assert len(removed) >= 1


def test_ensure_project_bullets_splits_description():
    projects = [{"title": "X", "description": "First part --- second part"}]
    out = main._ensure_project_bullets(projects)
    assert len(out[0]["bullets"]) == 2


def test_preflight_flags_long_summary():
    profile = {"summary": "Short."}
    tailored = {"tailored_summary": " ".join(["word"] * 90)}
    pf = constraints_engine.preflight_tailored_resume(profile, tailored)
    assert pf["ok"] is False
    assert any(i["kind"] == "summary_length" for i in pf["issues"])


def test_humanize_strips_cliche():
    text = "Passionate cutting-edge engineer with proven track record."
    out = constraints_engine.humanize_text(text)
    assert "passionate" not in out.lower()
    assert "cutting-edge" not in out.lower()
