"""Thin dual-mode client for knowledge operations.

If `KNOWLEDGE_SERVICE_URL` is unset, delegates to in-process modules.
If set, sends equivalent requests to the standalone knowledge service.
"""

from __future__ import annotations

import os
from typing import Any

import requests

from . import capture, rating, semantic, store


def _base_url() -> str:
    return os.getenv("KNOWLEDGE_SERVICE_URL", "").rstrip("/")


def _use_http() -> bool:
    return bool(_base_url())


def _headers(pid: str) -> dict[str, str]:
    return {"X-Profile-ID": pid}


def _request(method: str, path: str, pid: str, **kwargs: Any) -> Any:
    url = f"{_base_url()}{path}"
    headers = dict(kwargs.pop("headers", {}) or {})
    headers.update(_headers(pid))
    resp = requests.request(method, url, headers=headers, timeout=15, **kwargs)
    resp.raise_for_status()
    if resp.content:
        return resp.json()
    return None


def get_profile(pid: str) -> dict[str, Any]:
    if not _use_http():
        return store.get_profile(pid)
    return _request("GET", f"/profile/{pid}", pid) or {}


def save_profile(pid: str, data: dict[str, Any]) -> None:
    if not _use_http():
        store.save_profile(pid, data or {})
        return
    _request("PUT", f"/profile/{pid}", pid, json=data or {})


def merge_section(pid: str, key: str, partial: Any) -> None:
    if not _use_http():
        store.merge_section(pid, key, partial)
        return

    if key == "contact_info":
        _request("PUT", f"/profile/{pid}/contact", pid, json={"contact_info": partial})
    elif key == "autofill":
        _request("PUT", f"/profile/{pid}/autofill", pid, json={"autofill": partial})
    elif key == "skills":
        _request("PUT", f"/profile/{pid}/skills", pid, json={"skills": partial})
    elif key == "common_answers":
        _request("PUT", f"/profile/{pid}/answers", pid, json={"common_answers": partial})
    else:
        store.merge_section(pid, key, partial)


def replace_section(pid: str, key: str, value: Any) -> None:
    if not _use_http():
        store.replace_section(pid, key, value)
        return

    if key == "summary":
        _request("PUT", f"/profile/{pid}/contact", pid, json={"summary": value})
    elif key == "experience":
        _request("PUT", f"/profile/{pid}/experience", pid, json={"experience": value})
    elif key == "education":
        _request("PUT", f"/profile/{pid}/education", pid, json={"education": value})
    else:
        store.replace_section(pid, key, value)


def set_learned_answer(pid: str, host: str, label: str, value: str) -> str:
    if not _use_http():
        return store.set_learned_answer(pid, host, label, value)
    data = _request(
        "POST",
        "/autofill/learn",
        pid,
        json={"host": host, "label": label, "value": value},
    ) or {}
    return str(data.get("saved", ""))


def get_learned_answers(pid: str, host_prefix: str) -> dict[str, Any]:
    if not _use_http():
        return store.get_learned_answers(pid, host_prefix)
    data = _request(
        "GET",
        "/autofill/learned",
        pid,
        params={"host": host_prefix},
    )
    return data if isinstance(data, dict) else {}


def list_unrated(pid: str) -> list[dict[str, Any]]:
    if not _use_http():
        return rating.list_unrated(pid)
    data = _request("GET", "/skills/unrated", pid, params={"pid": pid})
    return data if isinstance(data, list) else []


def set_rating(pid: str, skill_id: int, proficiency: int, evidence: str | None = None) -> dict[str, Any]:
    if not _use_http():
        return rating.set_rating(pid, skill_id, proficiency, evidence)
    data = _request(
        "POST",
        f"/skills/{int(skill_id)}/rate",
        pid,
        params={"pid": pid},
        json={"proficiency": proficiency, "evidence": evidence},
    )
    return data if isinstance(data, dict) else {}


def propose(pid: str, raw_text: str, source: str) -> dict[str, Any]:
    if not _use_http():
        return capture.propose(pid, raw_text, source)
    data = _request(
        "POST",
        "/capture/propose",
        pid,
        params={"pid": pid},
        json={"raw_text": raw_text, "source": source},
    )
    return data if isinstance(data, dict) else {}


def commit(pid: str, event_id: int, edited_delta: dict[str, Any] | None) -> dict[str, Any]:
    if not _use_http():
        return capture.commit(pid, event_id, edited_delta)
    data = _request(
        "POST",
        "/capture/commit",
        pid,
        params={"pid": pid},
        json={"event_id": int(event_id), "edited_delta": edited_delta or {}},
    )
    return data if isinstance(data, dict) else {}


def search(pid: str, query_text: str, k: int = 5, kind_filter: str | None = None) -> list[dict[str, Any]]:
    if not _use_http():
        return semantic.search(pid, query_text, k, kind_filter)
    data = _request(
        "POST",
        "/search",
        pid,
        params={"pid": pid},
        json={"query_text": query_text, "k": int(k), "kind_filter": kind_filter},
    )
    return data if isinstance(data, list) else []


def create_stub(pid: str, name: str) -> None:
    """Create a new profile stub with parity to in-process behavior."""
    save_profile(
        pid,
        {
            "contact_info": {"name": name},
            "autofill": {},
            "experience": [],
            "education": [],
            "skills": {},
            "common_answers": {},
            "summary": "",
        },
    )
