"""Schema + helpers for evidence-grounded, user-controlled resume edits."""

from __future__ import annotations

import copy
import re
from typing import Any, Callable

STATUSES = {"proposed", "needs_your_call", "accepted", "rejected"}

# Minimum semantic-search score (cosine, same scale on both search paths) for
# a knowledge hit to count as evidence.
EVIDENCE_SCORE_FLOOR = 0.60

# Rewrite vocabulary that carries no factual claim — function words plus the
# connectors and generic resume verbs a rephrase introduces without asserting
# anything new. Tokens shorter than 3 chars never reach this check.
STYLE_STOPWORDS = {
    # function words (>=3 chars, so they survive the token regex)
    "and", "for", "the", "into", "onto", "them", "they", "their", "this",
    "that", "these", "those", "from", "over", "under", "while", "also",
    "with", "within", "across", "through", "between", "using", "each",
    "than", "then", "when", "where", "which", "was", "were", "will", "has",
    "have", "had", "are", "its", "our", "per", "via", "both", "all", "more",
    # generic engineering / resume verbs and fillers
    "built", "build", "building", "delivered", "delivering", "designed",
    "developed", "developing", "improved", "improving", "implemented",
    "created", "shipped", "drove", "driving", "led", "leading", "owned",
    "reduced", "increased", "enhanced", "streamlined", "received", "achieved",
    "production", "scalable", "robust", "end-to-end", "hands-on", "time",
    "experience", "expertise", "strong", "proficient", "skilled", "skills",
    "skill", "team", "teams", "work", "working", "systems", "solutions",
}


def _known_in_profile(term: str, profile_terms: set[str]) -> bool:
    """Singular/plural-tolerant membership: 'llms' matches a profile 'llm'."""
    return (term in profile_terms
            or term.rstrip("s") in profile_terms
            or term + "s" in profile_terms)


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
    if edit.get("ungrounded_terms"):
        normalized["ungrounded_terms"] = [str(t) for t in edit["ungrounded_terms"]]

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
        if float(hit.get("score") or 0) < EVIDENCE_SCORE_FLOOR:
            continue
        text_terms = set(re.findall(r"\b[a-z][a-z0-9\+\#\.\-/]{2,}\b", str(hit.get("text") or "").lower()))
        if must_terms and not (must_terms & text_terms):
            continue
        return hit.get("evidence_ref")
    return None


def collect_profile_terms(profile: dict[str, Any]) -> set[str]:
    """Token set over the whole profile — a term already present anywhere in
    the candidate's data is not a new claim when it shows up in a rewrite."""
    parts: list[str] = [str(profile.get("summary") or "")]
    for exp in profile.get("experience") or []:
        if not isinstance(exp, dict):
            continue
        parts.extend(str(b) for b in (exp.get("details") or exp.get("bullets") or []))
        parts.append(f'{exp.get("company", "")} {exp.get("role") or exp.get("title", "")}')
    for proj in profile.get("projects") or []:
        if isinstance(proj, dict):
            parts.append(" ".join(str(proj.get(k) or "") for k in ("title", "description")))
            parts.extend(str(b) for b in (proj.get("bullets") or []) if isinstance(b, str))
        else:
            parts.append(str(proj))
    for items in (profile.get("skills") or {}).values():
        if isinstance(items, list):
            parts.extend(str(s) for s in items)
    for edu in profile.get("education") or []:
        if isinstance(edu, dict):
            parts.append(" ".join(str(edu.get(k) or "") for k in ("degree", "university", "details")))
    for pub in profile.get("publications") or []:
        if isinstance(pub, dict):
            parts.append(" ".join(str(pub.get(k) or "") for k in ("title", "description")))
    for award in profile.get("awards") or []:
        if isinstance(award, dict):
            parts.append(" ".join(str(award.get(k) or "") for k in ("title", "organization", "description")))
    for cert in profile.get("certifications") or []:
        if isinstance(cert, dict):
            parts.append(" ".join(str(cert.get(k) or "") for k in ("name", "issuer")))
    parts.extend(str(i) for i in (profile.get("research_interests") or []))
    text = " ".join(parts).lower()
    return {t for t in re.findall(r"\b[a-z][a-z0-9\+\#\.\-/]{2,}\b", text) if len(t) >= 3}


def ground_edit(
    edit: dict[str, Any],
    *,
    pid: str,
    jd_text: str,
    knowledge_search: Callable[[str, str, int], list[dict[str, Any]]],
    origin_ref: str | None = None,
    profile_terms: set[str] | None = None,
) -> dict[str, Any]:
    """Attach evidence_ref and enforce the evidence rule (rule 2), correctly:

    Rewriting EXISTING text is grounded by the original text itself — only
    genuinely NEW claims (tokens absent from the whole profile, beyond style
    vocabulary) must be backed by a knowledge hit or flagged needs_your_call.

    origin_ref: evidence_ref of the text being rewritten (e.g.
    "experience_bullet:0:2"); profile_terms: precomputed token set from
    collect_profile_terms, built once per request by the caller.
    """
    out = copy.deepcopy(validate_edit_object(edit))
    added_terms = _new_terms(out.get("before", ""), out.get("after", ""))
    # Strip style vocabulary and profile-known terms FIRST (plural-tolerant),
    # then prefer the JD-overlapping remainder (likely injected keywords).
    known = profile_terms or set()
    meaningful = {
        t for t in added_terms - STYLE_STOPWORDS if not _known_in_profile(t, known)
    }
    jd_claims = meaningful & _jd_terms(jd_text)
    claim_terms = jd_claims or meaningful

    if not claim_terms and out.get("before", "").strip() and origin_ref:
        # Pure rewrite/reorder of the candidate's own text: self-evidenced.
        out["evidence_ref"] = origin_ref
        out["status"] = _safe_status(out.get("status"), default="accepted")
        out["confidence"] = max(out.get("confidence") or 0.0, 0.72)
        return out

    query = " ".join(
        [
            out.get("field", ""),
            out.get("after", "")[:220],
            " ".join(sorted(claim_terms or added_terms)[:12]),
        ]
    ).strip()
    evidence_ref = _pick_evidence_ref(pid, query, claim_terms, knowledge_search)
    out["evidence_ref"] = evidence_ref
    if evidence_ref:
        out["status"] = _safe_status(out.get("status"), default="accepted")
        out["confidence"] = max(out.get("confidence") or 0.0, 0.72)
    elif claim_terms:
        # New claim with no backing evidence — never silently insert (rule 2).
        out["status"] = "needs_your_call"
        out["confidence"] = min(out.get("confidence") or 0.5, 0.55)
        out["ungrounded_terms"] = sorted(claim_terms)
    else:
        # No new claims but nothing retrievable either (e.g. added text with no
        # origin): leave it to the human.
        out["status"] = "needs_your_call"
        out["confidence"] = min(out.get("confidence") or 0.5, 0.6)
    return out
