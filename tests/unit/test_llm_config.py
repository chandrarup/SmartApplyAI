"""Provider-config layer tests (llm_provider): load/save round-trip, key
masking, prefer-string parsing, and generic OpenAI-compatible routing.
No live providers.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

import llm_provider  # noqa: E402


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    path = str(tmp_path / "llm_config.json")
    monkeypatch.setattr(llm_provider, "LLM_CONFIG_PATH", path)
    monkeypatch.setattr(llm_provider, "_llm_config_cache",
                        {"path": None, "mtime": None, "data": None})
    return path


def test_missing_file_synthesizes_default(tmp_config):
    cfg = llm_provider.load_llm_config()
    assert cfg["active_provider"] == "ollama"
    assert {"ollama", "anthropic", "openai", "google", "groq", "openrouter"} <= set(cfg["providers"])
    for entry in cfg["providers"].values():
        assert entry["type"] in ("openai", "anthropic")


def test_save_load_round_trip_and_merge(tmp_config):
    cfg = llm_provider.default_llm_config()
    cfg["active_provider"] = "groq"
    cfg["providers"]["groq"]["api_key"] = "gsk_secret1234"
    cfg["providers"]["myproxy"] = {"type": "openai", "base_url": "https://llm.example/v1",
                                   "api_key": "k-abc", "models": ["m1"]}
    llm_provider.save_llm_config(cfg)

    loaded = llm_provider.load_llm_config()
    assert loaded["active_provider"] == "groq"
    assert loaded["providers"]["groq"]["api_key"] == "gsk_secret1234"
    assert loaded["providers"]["myproxy"]["models"] == ["m1"]
    # File permissions restrict to the owner (holds API keys).
    assert oct(os.stat(tmp_config).st_mode & 0o777) == "0o600"


def test_mask_api_key_never_leaks_full_key():
    assert llm_provider.mask_api_key("") == ""
    masked = llm_provider.mask_api_key("sk-abcdefghijklmnop")
    assert masked == "****mnop"
    assert "abcdefghijkl" not in masked


def test_normalize_prefer_legacy_strings_unchanged(tmp_config):
    table = {
        "ollama": ("ollama", None),
        "claude": ("claude", None),
        "ollama/llama3:8b": ("ollama", "llama3:8b"),
        "claude/claude-haiku-4-5-20251001": ("claude", "claude-haiku-4-5-20251001"),
        "qwen2.5:3b": ("ollama", "qwen2.5:3b"),  # bare unknown = ollama model
    }
    for prefer, expected in table.items():
        assert llm_provider.normalize_llm_prefer(prefer) == expected, prefer


def test_normalize_prefer_resolves_configured_providers(tmp_config):
    assert llm_provider.normalize_llm_prefer("groq") == ("groq", None)
    assert llm_provider.normalize_llm_prefer("groq/llama-3.3-70b-versatile") == (
        "groq", "llama-3.3-70b-versatile")
    # Empty prefer → config's active provider/model.
    cfg = llm_provider.default_llm_config()
    cfg["active_provider"], cfg["model"] = "openai", "gpt-4o-mini"
    llm_provider.save_llm_config(cfg)
    assert llm_provider.normalize_llm_prefer("") == ("openai", "gpt-4o-mini")


def test_call_llm_routes_configured_provider_to_openai_compat(tmp_config, monkeypatch):
    cfg = llm_provider.default_llm_config()
    cfg["providers"]["groq"]["api_key"] = "gsk_x"
    llm_provider.save_llm_config(cfg)

    seen = {}

    def fake_compat(messages, temperature=0.3, timeout=600, system="", *,
                    base_url, api_key="", model, connect_timeout=None,
                    provider_name="openai"):
        seen.update(base_url=base_url, api_key=api_key, model=model,
                    provider_name=provider_name)
        return "ok"

    monkeypatch.setattr(llm_provider, "call_openai_compat", fake_compat)
    out = llm_provider.call_llm([{"role": "user", "content": "hi"}],
                                prefer="groq/llama-3.3-70b-versatile")
    assert out == "ok"
    assert seen["provider_name"] == "groq"
    assert seen["model"] == "llama-3.3-70b-versatile"
    assert seen["api_key"] == "gsk_x"
    assert seen["base_url"].startswith("https://api.groq.com")


def test_call_llm_unknown_provider_falls_back(tmp_config, monkeypatch):
    calls = []
    monkeypatch.setattr(llm_provider, "call_ollama",
                        lambda *a, **kw: calls.append("ollama") or "from-ollama")
    out = llm_provider.call_llm([{"role": "user", "content": "hi"}], prefer="nosuch/model")
    assert out == "from-ollama"
    assert calls == ["ollama"]


# ── /llm-settings endpoints ──────────────────────────────────────────
def test_llm_settings_endpoints_mask_and_keep_keys(tmp_config):
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)

    body = llm_provider.default_llm_config()
    body["providers"]["groq"]["api_key"] = "gsk_supersecret99"
    body["active_provider"] = "groq"
    body["model"] = "llama-3.3-70b-versatile"

    r = client.put("/llm-settings", json=body)
    assert r.status_code == 200, r.text
    masked = r.json()["providers"]["groq"]["api_key"]
    assert masked == "****et99" and "supersecret" not in masked

    # GET never leaks the key either.
    r2 = client.get("/llm-settings")
    assert "supersecret" not in r2.text
    assert r2.json()["active_provider"] == "groq"

    # Re-saving the masked value keeps the stored key on disk.
    r3 = client.put("/llm-settings", json=r2.json())
    assert r3.status_code == 200
    on_disk = json.loads(open(tmp_config).read())
    assert on_disk["providers"]["groq"]["api_key"] == "gsk_supersecret99"


def test_llm_settings_rejects_bad_active_provider(tmp_config):
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)
    body = llm_provider.default_llm_config()
    body["active_provider"] = "nonexistent"
    r = client.put("/llm-settings", json=body)
    assert r.status_code == 400
