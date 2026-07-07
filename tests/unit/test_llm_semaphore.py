"""Concurrency tests for the narrowed lock architecture: _run_llm caps
in-flight LLM calls at LLM_CONCURRENCY; processing_lock still serializes the
PDF compile path. No live providers.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

import main  # noqa: E402


def test_run_llm_caps_concurrency(monkeypatch):
    state = {"in_flight": 0, "max_in_flight": 0}
    lock = threading.Lock()

    def slow_llm(messages, temperature=0.3, system="", prefer="ollama",
                 timeout=600, model=None):
        with lock:
            state["in_flight"] += 1
            state["max_in_flight"] = max(state["max_in_flight"], state["in_flight"])
        time.sleep(0.05)
        with lock:
            state["in_flight"] -= 1
        return "ok"

    monkeypatch.setattr(main, "call_llm", slow_llm)

    async def _fan_out():
        return await asyncio.gather(*[
            main._run_llm([{"role": "user", "content": f"q{i}"}]) for i in range(6)
        ])

    results = asyncio.run(_fan_out())
    assert results == ["ok"] * 6
    assert state["max_in_flight"] <= main.LLM_CONCURRENCY
    assert state["max_in_flight"] >= 2  # calls genuinely overlapped


def test_llm_endpoints_do_not_hold_processing_lock(monkeypatch):
    """/suggest-questions must proceed even while the PDF lock is held —
    the compile lock no longer serializes LLM endpoints."""
    from fastapi.testclient import TestClient

    monkeypatch.setattr(main, "call_llm",
                        lambda *a, **kw: '["Q1", "Q2", "Q3"]')
    client = TestClient(main.app)

    async def _hold_lock_and_call():
        async with main.processing_lock:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    client.post, "/suggest-questions",
                    json={"jd_text": "Python role", "llm": "ollama"},
                    headers={"X-Profile-ID": "default"},
                ),
                timeout=5.0,  # would deadlock/timeout if the endpoint took the lock
            )

    r = asyncio.run(_hold_lock_and_call())
    assert r.status_code == 200
    assert r.json() == ["Q1", "Q2", "Q3"]
