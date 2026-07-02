"""Capture helpers to propose and commit profile deltas."""

from __future__ import annotations

import json
from typing import Any

from . import store

_EXTRACTION_SYSTEM_PROMPT = """You extract profile updates from user notes.
Return strict JSON only (no markdown) with this shape:
{
  "proposed_delta": {
    "<section_key>": <value>
  },
  "confidence": 0.0-1.0,
  "notes": ["short note"]
}

Allowed section keys:
contact_info, summary, education, experience, projects, skills, publications,
certifications, awards, leadership, research_interests, autofill, common_answers, learned_answers.

Rules:
- Include only keys confidently supported by the text.
- Keep data concise and factual; do not invent unknown facts.
- If no safe updates are present, return proposed_delta as {}.
"""


def _normalize_delta(delta: Any) -> dict[str, Any]:
    if not isinstance(delta, dict):
        return {}
    return {k: v for k, v in delta.items() if k in store.KNOWN_SECTION_KEYS}


def _extract_json_block(text: str) -> dict[str, Any]:
    body = (text or "").strip()
    if not body:
        return {}
    try:
        parsed = json.loads(body)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass

    start = body.find("{")
    end = body.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        parsed = json.loads(body[start : end + 1])
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _extract_with_llm(raw_text: str) -> tuple[dict[str, Any], str]:
    """Return (proposed_delta, model_used)."""
    if not (raw_text or "").strip():
        return {}, ""

    try:
        from main import call_llm  # Local import avoids import cycle at module load time.
    except Exception:
        return {}, ""

    messages = [
        {"role": "user", "content": raw_text},
    ]
    reply = call_llm(messages, system=_EXTRACTION_SYSTEM_PROMPT, temperature=0.0, prefer="ollama")
    parsed = _extract_json_block(reply)
    if not isinstance(parsed, dict):
        return {}, ""

    proposed = parsed.get("proposed_delta", parsed)
    return _normalize_delta(proposed), "ollama"


def propose(pid: str, raw_text: str, source: str) -> dict[str, Any]:
    """Extract and store a proposed profile delta from freeform text."""
    model_used = ""
    extraction_error = ""
    proposed_delta: dict[str, Any] = {}
    try:
        proposed_delta, model_used = _extract_with_llm(raw_text)
    except Exception as exc:
        extraction_error = str(exc)
        proposed_delta = {}

    with store._connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO events(profile_id, ts, source_type, raw_text, extracted_json, status, provenance)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pid,
                store._utc_now(),
                "capture",
                raw_text or "",
                json.dumps(proposed_delta, ensure_ascii=False),
                "proposed",
                source or "",
            ),
        )
        event_id = int(cur.lastrowid)
        conn.commit()

    return {
        "event_id": event_id,
        "proposed_delta": proposed_delta,
        "source": source or "",
        "model_used": model_used,
        "error": extraction_error or None,
    }


def commit(pid: str, event_id: int, edited_delta: dict[str, Any] | None) -> dict[str, Any]:
    """Apply a captured delta to profile sections and mark the event committed."""
    with store._connect() as conn:
        row = conn.execute(
            "SELECT extracted_json FROM events WHERE id = ? AND profile_id = ?",
            (int(event_id), pid),
        ).fetchone()
        if not row:
            raise KeyError(f"event {event_id} not found for profile {pid}")
        stored_delta = _extract_json_block(row["extracted_json"] or "{}")

    delta = _normalize_delta(edited_delta if edited_delta is not None else stored_delta)
    applied_keys: list[str] = []
    for key, value in delta.items():
        if isinstance(value, dict):
            store.merge_section(pid, key, value)
        else:
            store.replace_section(pid, key, value)
        applied_keys.append(key)

    with store._connect() as conn:
        conn.execute(
            """
            UPDATE events
            SET extracted_json = ?, status = ?
            WHERE id = ? AND profile_id = ?
            """,
            (json.dumps(delta, ensure_ascii=False), "committed", int(event_id), pid),
        )
        conn.commit()

    return {"ok": True, "event_id": int(event_id), "applied_keys": applied_keys, "delta": delta}
