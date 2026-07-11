"""Deterministic hybrid job scoring (Phase-2 §3.4).

Fuses five LLM-free components — ontology skill coverage, FTS5 BM25 lexical
relevance, dense-embedding similarity, domain alignment, and level fit — into a
single 0–100 score with fixed, versioned weights. Runs over EVERY prefilter
survivor so the expensive LLM five-dimension fit (matcher stage 3) is spent only
on the hybrid top-K rather than a noisy semantic top-50.

No network, no LLM, no randomness: the same (candidate, job, features, weights)
always yields the same score, which is what makes calibration and cross-run
comparison meaningful.
"""

from __future__ import annotations

from typing import Any, Callable

import numpy as np

try:
    from backend.matcher.features import FEATURES_DB, bm25_scores, get_features
    from backend.matcher.ontology import load_ontology, match_strength
except ImportError:  # pragma: no cover - package-style import
    from matcher.features import FEATURES_DB, bm25_scores, get_features  # type: ignore
    from matcher.ontology import load_ontology, match_strength  # type: ignore

SCORING_VERSION = "v1"

# Fusion weights (must sum to 1.0). Skills dominate; BM25 + embeddings carry
# lexical/semantic recall; domain + level are light nudges. Tuned only in
# discrete, logged steps by calibrate.py (never silently).
DEFAULT_WEIGHTS: dict[str, float] = {
    "skills": 0.40,
    "bm25": 0.20,
    "embedding": 0.20,
    "domain": 0.10,
    "level": 0.10,
}

# Skill-coverage blend: required coverage dominates, preferred tops up.
_ALPHA_REQUIRED = 0.7
_BETA_PREFERRED = 0.3

# Domains treated as adjacent (partial domain credit). Symmetric closure built
# at import time from these unordered groups.
_ADJACENCY_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"ml", "cv", "nlp", "llm"}),
    frozenset({"biomed", "healthcare"}),
    frozenset({"data-eng", "backend"}),
)


def _build_adjacency() -> dict[str, set[str]]:
    adj: dict[str, set[str]] = {}
    for group in _ADJACENCY_GROUPS:
        for a in group:
            adj.setdefault(a, set()).update(group - {a})
    return adj


_ADJACENCY = _build_adjacency()

# Level fit matrices keyed by the candidate's target level. Off-target is
# penalized, never hard-zeroed (a senior role stays reviewable). Unknown job
# level is treated as neutral-ish, not as a gap.
_LEVEL_MATRIX: dict[str, dict[str, float]] = {
    "intern": {"intern": 100, "entry": 85, "mid": 60, "senior": 25, "unknown": 70},
    "entry": {"entry": 100, "intern": 80, "mid": 70, "senior": 30, "unknown": 70},
}


def _validate_weights(weights: dict[str, float]) -> dict[str, float]:
    missing = set(DEFAULT_WEIGHTS) - set(weights)
    if missing:
        raise ValueError(f"hybrid weights missing keys: {sorted(missing)}")
    total = sum(float(weights[k]) for k in DEFAULT_WEIGHTS)
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"hybrid weights must sum to 1.0, got {total:.6f}")
    return {k: float(weights[k]) for k in DEFAULT_WEIGHTS}


def _coverage(
    candidate_skills: dict[str, float],
    job_skills: dict[str, float],
    ontology: dict[str, Any],
) -> float | None:
    """Weighted fraction of a job's skill demand the candidate covers.

    Each job skill earns its weight × match_strength (1.0 exact, 0.7 parent/
    child, 0.5 related, else 0). Returns None when the job lists no skills so
    the caller can fall back rather than divide by zero.
    """
    if not job_skills:
        return None
    total = 0.0
    earned = 0.0
    for skill_id, weight in job_skills.items():
        w = float(weight)
        total += w
        earned += w * match_strength(candidate_skills, skill_id, ontology)
    if total <= 0:
        return None
    return earned / total


def score_skills(
    candidate_skills: dict[str, float],
    required_skills: dict[str, float],
    preferred_skills: dict[str, float],
    ontology: dict[str, Any],
) -> float:
    """0–100 ontology coverage. Required dominates; preferred tops up. When the
    job lists no required skills, fall back to coverage over all detected skills
    so a purely 'preferred' JD still scores."""
    cov_req = _coverage(candidate_skills, required_skills, ontology)
    cov_pref = _coverage(candidate_skills, preferred_skills, ontology)

    if cov_req is None:
        # No required section parsed — fall back to all detected skills.
        merged = {**preferred_skills, **required_skills}
        cov_all = _coverage(candidate_skills, merged, ontology)
        if cov_all is None:
            return 0.0
        return round(min(1.0, cov_all) * 100.0, 2)

    if cov_pref is None:
        blended = cov_req
    else:
        blended = _ALPHA_REQUIRED * cov_req + _BETA_PREFERRED * cov_pref
    return round(max(0.0, min(1.0, blended)) * 100.0, 2)


def normalize_bm25(raw: dict[str, float], job_keys: list[str]) -> dict[str, float]:
    """Min–max normalize raw BM25 to 0–100 within this batch. An all-equal batch
    (including all-zero: no lexical hits) maps to a neutral 50."""
    vals = [float(raw.get(k, 0.0)) for k in job_keys]
    if not vals:
        return {}
    lo, hi = min(vals), max(vals)
    if hi - lo <= 1e-12:
        return {k: 50.0 for k in job_keys}
    span = hi - lo
    return {k: round((float(raw.get(k, 0.0)) - lo) / span * 100.0, 2) for k in job_keys}


def score_embedding(
    profile_embedding: np.ndarray,
    experience_embeddings: list[np.ndarray],
    embedding_main: np.ndarray,
    embedding_requirements: np.ndarray | None,
) -> float:
    """0–100 dense similarity: 0.6 profile↔full-text + 0.4 best-experience↔
    requirements. All vectors are L2-normalized upstream, so cosine == dot."""
    def _dot(a: np.ndarray, b: np.ndarray) -> float:
        if a is None or b is None or a.size == 0 or b.size == 0 or a.shape != b.shape:
            return 0.0
        return float(np.dot(a, b))

    sim_profile = _dot(profile_embedding, embedding_main)
    req_vec = embedding_requirements if embedding_requirements is not None else embedding_main
    best_exp = sim_profile
    if experience_embeddings:
        sims = [_dot(exp, req_vec) for exp in experience_embeddings]
        if sims:
            best_exp = max(sims)
    sim = 0.6 * sim_profile + 0.4 * best_exp
    return round(max(0.0, sim) * 100.0, 2)


def score_domain(candidate_domains: list[str], job_domains: list[str]) -> float:
    """100 direct overlap · 70 adjacent · 50 unknown (either side blank) · 30 unrelated."""
    cand = {d for d in candidate_domains if d}
    job = {d for d in job_domains if d}
    if cand & job:
        return 100.0
    for c in cand:
        if _ADJACENCY.get(c, set()) & job:
            return 70.0
    if not cand or not job:
        return 50.0
    return 30.0


def score_level(target_level: str, job_level: str) -> float:
    matrix = _LEVEL_MATRIX.get(str(target_level or "").lower(), _LEVEL_MATRIX["intern"])
    return float(matrix.get(str(job_level or "unknown").lower(), matrix["unknown"]))


def _explain(
    candidate_skills: dict[str, float],
    required_skills: dict[str, float],
    preferred_skills: dict[str, float],
    ontology: dict[str, Any],
    job_domains: list[str],
    job_level: str,
) -> dict[str, Any]:
    def _name(skill_id: str) -> str:
        skill = ontology.get(skill_id) if ontology else None
        return getattr(skill, "name", None) or skill_id
    matched: list[str] = []
    gaps: list[str] = []
    for skill_id in {**required_skills, **preferred_skills}:
        if match_strength(candidate_skills, skill_id, ontology) > 0:
            matched.append(_name(skill_id))
        else:
            gaps.append(_name(skill_id))
    return {
        "matched_skills": sorted(matched),
        "gap_skills": sorted(gaps),
        "domain_tags": list(job_domains),
        "level": job_level,
    }


def score_jobs_hybrid(
    candidate: dict[str, Any],
    jobs: list[dict[str, Any]],
    db_path: str | Any = FEATURES_DB,
    *,
    weights: dict[str, float] | None = None,
    ontology: dict[str, Any] | None = None,
    bm25_fn: Callable[..., dict[str, float]] | None = None,
    get_features_fn: Callable[..., dict[str, dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Score every job deterministically; return items sorted by hybrid_total desc.

    Jobs whose features are not yet built are skipped with a loud log line (they
    are picked up on the next feature-build pass). One malformed job never kills
    the batch (rule 7).
    """
    weights = _validate_weights(weights or DEFAULT_WEIGHTS)
    if ontology is None:
        ontology = load_ontology()
    bm25_fn = bm25_fn or bm25_scores
    get_features_fn = get_features_fn or get_features

    key_to_job: dict[str, dict[str, Any]] = {}
    ordered_keys: list[str] = []
    for job in jobs:
        key = f"{job.get('source_ats') or ''}:{job.get('external_id') or ''}"
        key_to_job[key] = job
        ordered_keys.append(key)

    feats = get_features_fn(db_path, ordered_keys)
    present_keys = [k for k in ordered_keys if k in feats]
    missing = len(ordered_keys) - len(present_keys)
    if missing:
        print(f"[hybrid] {missing} job(s) missing features — skipped this run")
    if not present_keys:
        return []

    query_terms = candidate.get("query_terms") or []
    raw_bm25 = bm25_fn(db_path, query_terms, present_keys)
    bm25_norm = normalize_bm25(raw_bm25, present_keys)

    cand_skills = candidate.get("skills") or {}
    cand_domains = candidate.get("domains") or []
    target_level = candidate.get("target_level") or "intern"
    profile_emb = candidate.get("profile_embedding")
    exp_embs = candidate.get("experience_embeddings") or []

    results: list[dict[str, Any]] = []
    for key in present_keys:
        try:
            feat = feats[key]
            components = {
                "skills": score_skills(
                    cand_skills,
                    feat.get("required_skills") or {},
                    feat.get("preferred_skills") or {},
                    ontology,
                ),
                "bm25": bm25_norm.get(key, 50.0),
                "embedding": score_embedding(
                    profile_emb,
                    exp_embs,
                    feat.get("embedding_main"),
                    feat.get("embedding_requirements"),
                ),
                "domain": score_domain(cand_domains, feat.get("domain_tags") or []),
                "level": score_level(target_level, feat.get("level") or "unknown"),
            }
            total = round(sum(weights[k] * components[k] for k in weights), 2)
            results.append(
                {
                    "job_key": key,
                    "job": key_to_job[key],
                    "hybrid_total": total,
                    "components": components,
                    "explanation": _explain(
                        cand_skills,
                        feat.get("required_skills") or {},
                        feat.get("preferred_skills") or {},
                        ontology,
                        feat.get("domain_tags") or [],
                        feat.get("level") or "unknown",
                    ),
                    "scoring_version": SCORING_VERSION,
                }
            )
        except Exception as exc:  # noqa: BLE001 — one bad job never kills the batch
            print(f"[hybrid] skip {key}: {type(exc).__name__}: {exc}")

    results.sort(key=lambda item: item["hybrid_total"], reverse=True)
    return results
