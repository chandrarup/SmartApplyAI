"""Schema + helpers for evidence-grounded, user-controlled resume edits.

Three-band stretch_level (interview-backtrack test):
  grounded     — rewrite/reorder/synonym of existing content → accept
  stretch      — JD terminology for similar/adjacent work → Keep/Soften/Drop
  fabrication  — no backing anywhere → auto-reject, never rendered
"""

from __future__ import annotations

import copy
import re
from typing import Any, Callable

STATUSES = {"proposed", "needs_your_call", "accepted", "rejected"}
STRETCH_LEVELS = {"grounded", "stretch", "fabrication"}

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


def _safe_stretch(level: str | None, default: str = "grounded") -> str:
    s = (level or "").strip().lower()
    return s if s in STRETCH_LEVELS else default


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
        "stretch_level": _safe_stretch(edit.get("stretch_level"), default="grounded"),
    }
    if edit.get("stretch_reason"):
        normalized["stretch_reason"] = _norm(edit["stretch_reason"])
    if edit.get("ungrounded_terms"):
        normalized["ungrounded_terms"] = [str(t) for t in edit["ungrounded_terms"]]
    if edit.get("lint_flags"):
        normalized["lint_flags"] = list(edit["lint_flags"])

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


def renderable_edits(edits: list[Any] | None) -> list[dict[str, Any]]:
    """Filter fabrications out of the review list (rejected inventions never render)."""
    return [
        e for e in validate_edits(edits)
        if e.get("stretch_level") != "fabrication"
    ]


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


def _claim_terms_for_edit(
    before: str,
    after: str,
    jd_text: str,
    profile_terms: set[str] | None,
) -> set[str]:
    added_terms = _new_terms(before, after)
    known = profile_terms or set()
    meaningful = {
        t for t in added_terms - STYLE_STOPWORDS if not _known_in_profile(t, known)
    }
    jd_claims = meaningful & _jd_terms(jd_text)
    return jd_claims or meaningful


def ground_edit(
    edit: dict[str, Any],
    *,
    pid: str,
    jd_text: str,
    knowledge_search: Callable[[str, str, int], list[dict[str, Any]]],
    origin_ref: str | None = None,
    profile_terms: set[str] | None = None,
) -> dict[str, Any]:
    """Attach evidence_ref and classify stretch_level (rule 2 + three-band).

    Rewriting EXISTING text is grounded by the original text itself — only
    genuinely NEW claims must be backed by a knowledge hit. Unbacked new claims
    are fabrications (auto-rejected). JD terminology for similar work with
    evidence is a stretch (Keep / Soften / Drop).
    """
    out = copy.deepcopy(validate_edit_object(edit))
    claim_terms = _claim_terms_for_edit(
        out.get("before", ""), out.get("after", ""), jd_text, profile_terms,
    )
    added_terms = _new_terms(out.get("before", ""), out.get("after", ""))

    if not claim_terms and out.get("before", "").strip() and origin_ref:
        # Pure rewrite/reorder of the candidate's own text: self-evidenced.
        out["evidence_ref"] = origin_ref
        out["status"] = "accepted"
        out["stretch_level"] = "grounded"
        out["confidence"] = max(out.get("confidence") or 0.0, 0.72)
        out.pop("ungrounded_terms", None)
        out.pop("stretch_reason", None)
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

    if claim_terms and evidence_ref:
        # Posting's terminology for related work — explainable with care.
        terms = ", ".join(sorted(claim_terms)[:6])
        out["stretch_level"] = "stretch"
        out["stretch_reason"] = (
            f"Uses JD wording ({terms}) for related experience "
            f"(backed by {evidence_ref})"
        )
        out["status"] = "needs_your_call"
        out["confidence"] = max(out.get("confidence") or 0.0, 0.68)
        out.pop("ungrounded_terms", None)
        return out

    if claim_terms and not evidence_ref:
        # No backing anywhere — fabrication; never silently insert (rule 2).
        out["stretch_level"] = "fabrication"
        out["stretch_reason"] = (
            f"No profile evidence for: {', '.join(sorted(claim_terms)[:8])}"
        )
        out["status"] = "rejected"
        out["confidence"] = min(out.get("confidence") or 0.5, 0.4)
        out["ungrounded_terms"] = sorted(claim_terms)
        return out

    # No new claims but nothing retrievable either (e.g. added text with no
    # origin): treat as stretch for human Keep/Soften/Drop.
    out["stretch_level"] = "stretch"
    out["stretch_reason"] = "Added or reframed text without a clear origin bullet"
    out["status"] = "needs_your_call"
    out["confidence"] = min(out.get("confidence") or 0.5, 0.6)
    return out


def soften_edit_text(
    edit: dict[str, Any],
    *,
    llm_call: Callable[..., str],
    llm: str = "ollama",
) -> str:
    """One targeted LLM call: more conservative rewrite of edit.after only."""
    before = (edit.get("before") or "").strip()
    after = (edit.get("after") or "").strip()
    reason = (edit.get("stretch_reason") or edit.get("reason") or "").strip()
    prompt = (
        "Soften this resume edit to a more conservative, interview-safe phrasing. "
        "Keep the same facts as the BEFORE text; do not invent skills or tools. "
        "Prefer the candidate's original wording over JD buzzwords when unsure. "
        "Output ONLY the softened sentence.\n\n"
        f"Stretch reason: {reason or 'adjacent framing'}\n"
        f"BEFORE:\n{before or '(new bullet)'}\n\n"
        f"CURRENT (too strong):\n{after}\n"
    )
    try:
        raw = llm_call([{"role": "user", "content": prompt}], temperature=0.2, prefer=llm)
        line = (raw or "").strip().splitlines()[0].strip().strip('"')
        return line or after
    except Exception:
        return after


def apply_soften(
    edit: dict[str, Any],
    *,
    pid: str,
    jd_text: str,
    knowledge_search: Callable[[str, str, int], list[dict[str, Any]]],
    llm_call: Callable[..., str],
    llm: str = "ollama",
    origin_ref: str | None = None,
    profile_terms: set[str] | None = None,
) -> dict[str, Any]:
    """Soften → re-run ground/band. Softened grounded edits auto-accept."""
    softened = soften_edit_text(edit, llm_call=llm_call, llm=llm)
    next_edit = copy.deepcopy(validate_edit_object(edit))
    next_edit["after"] = softened
    next_edit["status"] = "proposed"
    next_edit["reason"] = next_edit.get("reason") or "Softened stretch"
    # Prefer origin from evidence_ref when rewriting an existing bullet.
    origin = origin_ref or (
        next_edit.get("evidence_ref")
        if next_edit.get("before", "").strip()
        else None
    )
    return ground_edit(
        next_edit,
        pid=pid,
        jd_text=jd_text,
        knowledge_search=knowledge_search,
        origin_ref=origin,
        profile_terms=profile_terms,
    )


def attach_lint_flags_to_edits(
    edits: list[dict[str, Any]],
    lint_flags: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Copy payload-level lint flags onto matching edit cards by field."""
    by_field: dict[str, list[dict[str, Any]]] = {}
    for flag in lint_flags or []:
        field = str(flag.get("field") or "")
        if not field:
            continue
        by_field.setdefault(field, []).append(flag)
    out = []
    for e in edits:
        e2 = copy.deepcopy(e)
        field = e2.get("field") or ""
        matched = list(by_field.get(field) or [])
        if field == "summary":
            matched.extend(by_field.get("summary") or [])
        # Dedup by label+match
        seen = set()
        uniq = []
        for f in matched:
            key = (f.get("label"), f.get("match"), f.get("sentence"))
            if key in seen:
                continue
            seen.add(key)
            uniq.append(f)
        if uniq:
            e2["lint_flags"] = uniq
        out.append(e2)
    return out
