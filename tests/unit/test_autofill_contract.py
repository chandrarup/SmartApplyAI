"""
/autofill contract tests — the endpoint must return {answers, unanswered} and must
NEVER fabricate an answer for a field the profile can't ground (rule 2: no silent
invention). The LLM is mocked; no live provider calls.
"""

from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

import main  # noqa: E402


@pytest.fixture
def client():
    return TestClient(main.app)


def _post(client, fields, **kw):
    body = {"fields": fields, "host": "jobs.ashbyhq.com", "llm": "ollama"}
    body.update(kw)
    r = client.post("/autofill", json=body, headers={"X-Profile-ID": "default"})
    assert r.status_code == 200, r.text
    return r.json()


def test_returns_answers_and_unanswered_shape(client, monkeypatch):
    # LLM declines the ungrounded field.
    monkeypatch.setattr(main, "call_llm", lambda *a, **k: '{"Favorite ice cream flavor": "SKIP"}')
    data = _post(client, [
        {"label": "Email", "type": "email"},
        {"label": "Favorite ice cream flavor", "type": "text"},
    ])
    assert set(data.keys()) == {"answers", "unanswered"}
    # Email is grounded by rules; flavor is not answerable and must come back to the user.
    assert data["answers"].get("Email")
    labels = [u["label"] for u in data["unanswered"]]
    assert "Favorite ice cream flavor" in labels
    assert "Email" not in labels


def test_ungrounded_field_is_never_fabricated(client, monkeypatch):
    # Even if the LLM returns blank/missing, the field must land in unanswered, not answers.
    monkeypatch.setattr(main, "call_llm", lambda *a, **k: "{}")
    data = _post(client, [{"label": "What is your desired shift preference?", "type": "text"}])
    assert "What is your desired shift preference?" not in data["answers"]
    assert any(u["label"] == "What is your desired shift preference?" for u in data["unanswered"])


def test_llm_failure_degrades_to_rules(client, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("all providers down")
    monkeypatch.setattr(main, "call_llm", _boom)
    data = _post(client, [
        {"label": "First Name", "type": "text"},
        {"label": "Describe a hard problem you solved", "type": "textarea"},
    ])
    assert data["answers"].get("First Name")
    assert any(u["label"] == "Describe a hard problem you solved" for u in data["unanswered"])


def test_learned_answers_list_and_delete(client):
    h = {"X-Profile-ID": "default"}
    # Teach an answer, then confirm it shows in the list-all view and can be deleted.
    client.post("/autofill/learn", json={"host": "test.example", "label": "Shift preference", "value": "Days"}, headers=h)
    listed = client.get("/autofill/learned/all", headers=h).json()
    match = [i for i in listed["items"] if i["host"] == "test.example" and i["label"] == "shift preference"]
    assert match and match[0]["value"] == "Days"
    key = match[0]["key"]
    assert client.request("DELETE", "/autofill/learned", params={"key": key}, headers=h).json()["deleted"] is True
    after = client.get("/autofill/learned/all", headers=h).json()
    assert not any(i["key"] == key for i in after["items"])


def test_options_passed_through_to_unanswered(client, monkeypatch):
    monkeypatch.setattr(main, "call_llm", lambda *a, **k: "{}")
    opts = ["Yes", "No", "Prefer not to say"]
    data = _post(client, [
        {"label": "Do you enjoy working weekends?", "type": "combobox", "options": opts},
    ])
    match = [u for u in data["unanswered"] if u["label"] == "Do you enjoy working weekends?"]
    assert match and match[0]["options"] == opts
