"""Human-readable fit explanations derived from scored evidence."""

from __future__ import annotations

from typing import Any


def add_fit_explanation(fit: dict[str, Any]) -> dict[str, Any]:
    result = dict(fit)
    matched = result.get("matched_skills") or []
    missing = result.get("missing_skills") or []
    evidence = result.get("evidence") or []
    result["explanation"] = {
        "why_fit": [
            f"{item.get('skill')} — {item.get('evidence_ref') or 'profile evidence'}"
            for item in matched if isinstance(item, dict) and item.get("skill")
        ],
        "gaps": [
            str(item.get("skill") if isinstance(item, dict) else item)
            for item in missing if str(item.get("skill") if isinstance(item, dict) else item).strip()
        ],
        "evidence_count": len(evidence),
        "needs_your_call": list(result.get("needs_your_call") or []),
    }
    return result
