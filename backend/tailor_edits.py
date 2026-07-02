"""Schema + helpers for evidence-grounded, user-controlled resume edits."""

from __future__ import annotations

import copy
import re
from typing import Any, Callable

STATUSES = {"proposed", "needs_your_call", "accepted", "rejected"}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip())


def _safe_status(status: str | None, default: str = "proposed") -> str:
    s = (status or "").strip().lower()
    return s if s in STATUSES else default


def validate_edit_object(edit: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize one edit object; raises ValueError on shape errors."""
    if not isinstance(edit, dict):
        raise ValueError("Edit must be an object.")

    required = ["section", "field", "before", "after", "reason"]
    missing = [k for k in required if k not in edit]
    if missing:
        raise ValueError(f"Edit missing required fields: {missing}")

    normalized = {
        "section": _norm(edit.get("section")),
        "field": _norm(edit.get("field")),
        "before": str(edit.get("before") or ""),
        "after": str(edit.get("after") or ""),
        "reason": _norm(edit.get("reason")),
        "evidence_ref": edit.get("evidence_ref"),
        "confidence": float(edit.get("confidence") or 0.0),
        "status": _safe_status(edit.get("status"), default="proposed"),
    }

    if not normalized["section"] or not normalized["field"] or not normalized["reason"]:
        raise ValueError("Edit section/field/reason cannot be empty.")

    if normalized["evidence_ref"] in ("", "none", "null"):
        normalized["evidence_ref"] = None

    normalized["confidence"] = max(0.0, min(1.0, normalized["confidence"]))
    return normalized


def validate_edits(edits: list[Any] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in (edits or []):
        try:
            out.append(validate_edit_object(raw))
        except ValueError:
            continue
    return out


def _jd_terms(jd_text: str) -> set[str]:
    return {
        t
        for t in re.findall(r"\b[a-z][a-z0-9\+\#\.\-/]{2,}\b", (jd_text or "").lower())
        if len(t) >= 3
    }


def _new_terms(before: str, after: str) -> set[str]:
    b = set(re.findall(r"\b[a-z][a-z0-9\+\#\.\-/]{2,}\b", (before or "").lower()))
    a = set(re.findall(r"\b[a-z][a-z0-9\+\#\.\-/]{2,}\b", (after or "").lower()))
    return a - b


def _pick_evidence_ref(
    pid: str,
    query: str,
    must_terms: set[str],
    knowledge_search: Callable[[str, str, int], list[dict[str, Any]]],
) -> str | None:
    hits = knowledge_search(pid, query, 5) or []
    if not hits:
        return None
    for hit in hits:
        if float(hit.get("score") or 0) < 0.62:
            continue
        text_terms = set(re.findall(r"\b[a-z][a-z0-9\+\#\.\-/]{2,}\b", str(hit.get("text") or "").lower()))
        if must_terms and not (must_terms & text_terms):
            continue
        return hit.get("evidence_ref")
    return None


def ground_edit(
    edit: dict[str, Any],
    *,
    pid: str,
    jd_text: str,
    knowledge_search: Callable[[str, str, int], list[dict[str, Any]]],
) -> dict[str, Any]:
    """Attach evidence_ref and enforce keyword grounding default rule."""
    out = copy.deepcopy(validate_edit_object(edit))
    query = " ".join(
        [
            out.get("field", ""),
            out.get("after", "")[:220],
            " ".join(sorted(list(_new_terms(out.get("before", ""), out.get("after", ""))))[:12]),
        ]
    ).strip()
    added_terms = _new_terms(out.get("before", ""), out.get("after", ""))
    jd_overlap = added_terms & _jd_terms(jd_text)
    must_terms = jd_overlap or added_terms
    evidence_ref = _pick_evidence_ref(pid, query, must_terms, knowledge_search)
    out["evidence_ref"] = evidence_ref
    if jd_overlap and not evidence_ref:
        out["status"] = "needs_your_call"
        out["confidence"] = min(out.get("confidence") or 0.5, 0.55)
    elif not evidence_ref:
        out["status"] = "needs_your_call"
        out["confidence"] = min(out.get("confidence") or 0.5, 0.6)
    else:
        out["status"] = _safe_status(out.get("status"), default="accepted")
        out["confidence"] = max(out.get("confidence") or 0.0, 0.72)
    return out
