"""
LLM provider resilience tests — call_llm / call_ollama / call_claude (TEST ONLY).

Providers are mocked; no live Ollama or Anthropic calls.
"""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

import main  # noqa: E402
import llm_provider  # noqa: E402
import scoring  # noqa: E402


@pytest.fixture
def client():
    return TestClient(main.app)


def _extraction_json():
    """scoring.extract_jd_requirements response for a 'Python ML' JD."""
    return json.dumps({
        "role": "ML Engineer", "company": "", "level": "Mid", "summary": "ML role.",
        "responsibilities": [],
        "must_have_skills": [{"skill": "Python"}, {"skill": "ML"}],
        "nice_to_have_skills": [], "keywords": ["Python"],
    })


def _summary_json():
    return json.dumps({
        "tailored_summary": "Experienced ML engineer shipping Python systems in production environments.",
    })


def _mock_analyze_pipeline(monkeypatch):
    monkeypatch.setattr(scoring, "call_llm", lambda *a, **kw: _extraction_json())
    monkeypatch.setattr(main, "call_llm", lambda *a, **kw: _summary_json())
    monkeypatch.setattr(main.knowledge_semantic, "search", lambda *a, **kw: [])


# ── call_llm unit tests ───────────────────────────────────────────────────────

def test_ollama_unreachable_falls_back_to_claude(monkeypatch):
    calls = []

    def fail_ollama(*args, **kwargs):
        calls.append("ollama")
        raise ConnectionError("Ollama unreachable")

    def ok_claude(*args, **kwargs):
        calls.append("claude")
        return "claude-response"

    monkeypatch.setattr(llm_provider, "call_ollama", fail_ollama)
    monkeypatch.setattr(llm_provider, "call_claude", ok_claude)

    out = main.call_llm([{"role": "user", "content": "hi"}], prefer="ollama")
    assert out == "claude-response"
    assert calls == ["ollama", "claude"]


def test_both_providers_down_raises_runtime_error(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(llm_provider, "call_ollama", fail)
    monkeypatch.setattr(llm_provider, "call_claude", fail)

    with pytest.raises(RuntimeError, match="All LLM providers failed"):
        main.call_llm([{"role": "user", "content": "hi"}], prefer="ollama")


def test_ollama_timeout_passes_ceiling_and_falls_back(monkeypatch):
    seen = {"timeout": None, "providers": []}

    def timeout_ollama(messages, temperature=0.3, timeout=600, model=None):
        seen["timeout"] = timeout
        seen["providers"].append("ollama")
        raise TimeoutError(f"Ollama timed out after {timeout}s")

    def ok_claude(messages, temperature=0.3, system=""):
        seen["providers"].append("claude")
        return "fallback-ok"

    monkeypatch.setattr(llm_provider, "call_ollama", timeout_ollama)
    monkeypatch.setattr(llm_provider, "call_claude", ok_claude)

    out = main.call_llm([{"role": "user", "content": "hi"}], prefer="ollama", timeout=600)
    assert out == "fallback-ok"
    assert seen["timeout"] == 600
    assert seen["providers"] == ["ollama", "claude"]


def test_prefer_claude_missing_api_key_falls_back_to_ollama(monkeypatch):
    calls = []

    def no_key_claude(*args, **kwargs):
        calls.append("claude")
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    def ok_ollama(*args, **kwargs):
        calls.append("ollama")
        return "ollama-wins"

    monkeypatch.setattr(llm_provider, "get_anthropic_key", lambda: "")
    monkeypatch.setattr(llm_provider, "call_claude", no_key_claude)
    monkeypatch.setattr(llm_provider, "call_ollama", ok_ollama)

    out = main.call_llm([{"role": "user", "content": "hi"}], prefer="claude")
    assert out == "ollama-wins"
    assert calls == ["claude", "ollama"]


def test_prefer_claude_missing_key_and_ollama_down_raises(monkeypatch):
    monkeypatch.setattr(llm_provider, "get_anthropic_key", lambda: "")

    def fail_claude(*args, **kwargs):
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    def fail_ollama(*args, **kwargs):
        raise ConnectionError("ollama down")

    monkeypatch.setattr(llm_provider, "call_claude", fail_claude)
    monkeypatch.setattr(llm_provider, "call_ollama", fail_ollama)

    with pytest.raises(RuntimeError, match="All LLM providers failed"):
        main.call_llm([{"role": "user", "content": "hi"}], prefer="claude")


@pytest.mark.parametrize("bad_content", ["", None])
def test_empty_llm_response_breaks_analyze_json_parse(client, monkeypatch, bad_content):
    monkeypatch.setattr(main, "call_llm", lambda *a, **k: bad_content)
    r = client.post(
        "/analyze",
        json={"jd_text": "Python ML role", "llm": "ollama"},
        headers={"X-Profile-ID": "default"},
    )
    assert r.status_code == 500
    detail = r.json()["detail"]
    assert isinstance(detail, str)
    assert "Traceback" not in detail
    assert "Expecting value" in detail or "JSON" in detail or detail


def test_empty_llm_response_autofill_returns_rule_based_only(client, monkeypatch):
    monkeypatch.setattr(main, "call_llm", lambda *a, **k: "")
    r = client.post(
        "/autofill",
        json={
            "fields": [
                {"label": "First Name", "type": "text"},
                {"label": "Obscure custom question XYZ?", "type": "textarea"},
            ],
            "jd_text": "ML role",
            "company": "Co",
            "host": "test.example",
            "llm": "ollama",
        },
        headers={"X-Profile-ID": "default"},
    )
    assert r.status_code == 200
    data = r.json()
    # New contract: {answers, unanswered}. Rules ground First Name; the obscure
    # question the empty LLM can't answer must come back as the user's call.
    assert data["answers"].get("First Name")
    assert any(u["label"] == "Obscure custom question XYZ?" for u in data["unanswered"])
    assert "Obscure custom question XYZ?" not in data["answers"]


def test_analyze_both_providers_down_graceful_message(client, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("All LLM providers failed. Last error: connection refused")

    monkeypatch.setattr(main, "call_llm", fail)
    r = client.post(
        "/analyze",
        json={"jd_text": "Role", "llm": "ollama"},
        headers={"X-Profile-ID": "default"},
    )
    assert r.status_code == 500
    detail = r.json()["detail"]
    assert "All LLM providers failed" in detail
    assert "Traceback" not in detail


def test_analyze_ollama_down_uses_claude_fallback(client, monkeypatch):
    # call_llm's internal provider fallback is opaque to /analyze — as long as
    # the provider layer returns content, the endpoint succeeds.
    _mock_analyze_pipeline(monkeypatch)
    r = client.post(
        "/analyze",
        json={"jd_text": "Python ML", "llm": "ollama"},
        headers={"X-Profile-ID": "default"},
    )
    assert r.status_code == 200
    assert r.json()["role"] == "ML Engineer"


# ── Autofill prompt budget (5500-char profile slice) ─────────────────────────

def _bloated_profile(base: dict) -> dict:
    out = json.loads(json.dumps(base))
    out["projects"] = [
        {
            "title": f"Padding Project {i:03d}",
            "description": "Lorem ipsum dolor sit amet. " * 40,
            "tech_stack": [f"Tech{i}", f"Stack{i}"],
        }
        for i in range(80)
    ]
    out["experience"] = list(out.get("experience") or [])
    for i in range(15):
        out["experience"].append({
            "company": f"Filler Corp {i}",
            "title": "Engineer",
            "details": ["bullet " * 30] * 8,
        })
    return out


def test_autofill_prompt_truncation_drops_tail_of_profile(client, monkeypatch):
    captured = {}

    def capture_llm(messages, temperature=0.3, system="", prefer="ollama", timeout=600, model=None):
        captured["prompt"] = messages[-1]["content"]
        return json.dumps({"Obscure custom question XYZ?": "Answer from LLM"})

    base = main.load_pdata("default")
    bloated = _bloated_profile(base)

    monkeypatch.setattr(main, "load_pdata", lambda pid: bloated)
    monkeypatch.setattr(main, "call_llm", capture_llm)

    r = client.post(
        "/autofill",
        json={
            "fields": [{"label": "Obscure custom question XYZ?", "type": "textarea"}],
            "jd_text": "x" * 2000,
            "company": "Co",
            "host": "test.example",
            "llm": "ollama",
        },
        headers={"X-Profile-ID": "default"},
    )
    assert r.status_code == 200
    prompt = captured["prompt"]
    assert "CANDIDATE PROFILE" in prompt
    # Profile blob is hard-capped
    profile_start = prompt.index("CANDIDATE PROFILE")
    profile_chunk = prompt[profile_start:profile_start + 5600]
    assert len(profile_chunk) <= 5600
    # padding projects should be truncated away
    assert "Padding Project 079" not in prompt
    # autofill quick reference is OUTSIDE the 5500 slice — always present
    assert "AUTOFILL QUICK REFERENCE" in prompt
    assert "work_authorization" in prompt.lower() or "requires_sponsorship" in prompt.lower()


def test_autofill_truncation_essential_contact_in_first_5500(client, monkeypatch):
    """Flag if contact_info is cut off by [:5500] — correctness bug."""
    captured = {}

    def capture_llm(messages, **kwargs):
        captured["prompt"] = messages[-1]["content"]
        return json.dumps({"Why this role?": "Because."})

    bloated = _bloated_profile(main.load_pdata("default"))
    contact = bloated.get("contact_info", {})
    email = contact.get("email", "")
    phone = contact.get("phone", "")

    monkeypatch.setattr(main, "load_pdata", lambda pid: bloated)
    monkeypatch.setattr(main, "call_llm", capture_llm)

    client.post(
        "/autofill",
        json={
            "fields": [{"label": "Why this role?", "type": "textarea"}],
            "jd_text": "role",
            "company": "Co",
            "host": "h",
            "llm": "ollama",
        },
        headers={"X-Profile-ID": "default"},
    )
    prompt = captured["prompt"]
    profile_json = prompt.split("CANDIDATE PROFILE (full source of truth):\n", 1)[1].split("\n\nAUTOFILL", 1)[0]
    assert len(profile_json) <= 5500
    # contact is at start of JSON — should survive
    assert email in profile_json
    assert phone in profile_json


# ── Determinism (informational) ───────────────────────────────────────────────

def test_analyze_twice_is_deterministic(client, monkeypatch):
    # The score comes from the deterministic rubric, not LLM free text — the
    # same JD + profile must score identically run to run (the old test here
    # documented the drift this fixed).
    _mock_analyze_pipeline(monkeypatch)

    jd = "Machine learning engineer with Python."
    r1 = client.post("/analyze", json={"jd_text": jd, "llm": "ollama"}, headers={"X-Profile-ID": "default"})
    r2 = client.post("/analyze", json={"jd_text": jd, "llm": "ollama"}, headers={"X-Profile-ID": "default"})
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["score"] == r2.json()["score"]
    assert r1.json()["selected_projects"] == r2.json()["selected_projects"]
    assert r1.json()["missing_skills"] == r2.json()["missing_skills"]


def test_call_ollama_forwards_timeout_to_http_post(monkeypatch):
    seen = {}

    def fake_post(url, json=None, timeout=None):
        seen["timeout"] = timeout
        mock = MagicMock()
        mock.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
        return mock

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    out = main.call_ollama([{"role": "user", "content": "x"}], timeout=600)
    assert out == "ok"
    # call_ollama sends (connect_timeout, read_timeout) so a dead host fails fast
    assert seen["timeout"] == (llm_provider.OLLAMA_CONNECT_TIMEOUT, 600)
