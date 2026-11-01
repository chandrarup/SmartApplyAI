"""Evidence retrieval and grounding for matcher fit results."""

from __future__ import annotations

import re
from typing import Any, Callable


def retrieve_evidence(
    profile_id: str,
    query: str,
    *,
    search: Callable[..., list[dict[str, Any]]] | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    if search is None:
        try:
            from backend.knowledge import search
        except ImportError:
            from knowledge import search  # type: ignore
    else:
        search = search
    try:
        return list(search(profile_id, query_text=query, k=limit) or [])
    except Exception as exc:
        print(f"[matcher-evidence] retrieval failed={type(exc).__name__}: {exc}")
        return []


def _tokens(value: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9][a-z0-9+#.-]{2,}", value.lower())}


def ground_fit(fit: dict[str, Any], evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """Keep matched claims only when profile evidence supports them."""
    result = dict(fit)
    hits = [
        {
            "evidence_ref": str(item.get("evidence_ref") or "").strip(),
            "text": str(item.get("text") or "").strip()[:300],
            "score": item.get("score"),
        }
        for item in evidence
        if str(item.get("text") or "").strip()
    ]
    evidence_text = " ".join(item["text"] for item in hits)
    evidence_tokens = _tokens(evidence_text)
    grounded: list[dict[str, Any]] = []
    needs_call: list[str] = []
    for item in result.get("matched_skills") or []:
        skill = str(item.get("skill") if isinstance(item, dict) else item).strip()
        ref = str(item.get("evidence_ref") if isinstance(item, dict) else "").strip()
        supported = bool(ref and any(ref == hit["evidence_ref"] for hit in hits))
        supported = supported or bool(_tokens(skill) & evidence_tokens)
        if supported:
            grounded.append({
                "skill": skill[:80],
                "evidence_ref": ref or next(
                    (hit["evidence_ref"] for hit in hits if _tokens(skill) & _tokens(hit["text"])), ""
                ),
            })
        elif skill:
            needs_call.append(skill)
    result["matched_skills"] = grounded
    result["evidence"] = hits
    result["needs_your_call"] = sorted(set(needs_call))
    result["grounding"] = {
        "grounded_count": len(grounded),
        "ungrounded_count": len(needs_call),
        "status": "grounded" if not needs_call else "needs_your_call",
    }
    return result
