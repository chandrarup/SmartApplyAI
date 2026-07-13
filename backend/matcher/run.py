"""Entrypoint for matcher pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import load_config
from .fit import fit_candidates
from .prefilter import prefilter_jobs
from .recall import recall_candidates
from .rerank import rerank_candidates
from .store import gate_and_store

# role_mode -> hybrid candidate target level.
_ROLE_TO_LEVEL = {"internship": "intern", "fulltime": "entry", "both": "intern"}


def _resolve(root: Path, maybe_relative: str) -> Path:
    path = Path(maybe_relative)
    return path if path.is_absolute() else (root / path)


def _legacy_rank(profile_id: str, survivors: list[dict], cfg) -> list[dict]:
    """Stage-1 semantic recall + stage-2 cross-encoder rerank (pre-hybrid path)."""
    recalled = recall_candidates(
        profile_id=profile_id,
        jobs=survivors,
        top_recall=cfg.top_recall,
        evidence_k=cfg.recall_evidence_k,
    )
    if not recalled:
        return []
    reranked = rerank_candidates(
        profile_id=profile_id,
        recalled=recalled,
        rerank_model=cfg.rerank_model,
    )
    print("[stage2] top 10 after rerank:")
    for idx, item in enumerate(reranked[:10], start=1):
        job = item["job"]
        print(
            f"  {idx:02d}. s2={item['stage2_score']:.4f} s1={item['stage1_score']:.4f} | "
            f"{job.get('title', '')} @ {job.get('company', '')}"
        )
    return reranked


def _hybrid_rank(profile_id: str, survivors: list[dict], cfg, features_db: Path) -> list[dict]:
    """Deterministic hybrid ranking over ALL survivors (Phase-2 §3.5).

    Builds/refreshes job features (incremental), builds the cached candidate
    features, scores every survivor, and returns reranked-shaped items so the
    downstream LLM fit + gate stages are unchanged. Raises on any hard failure
    so the caller can fall back to the legacy path (rule 7)."""
    from .candidate_features import build_candidate_features
    from .features import ensure_job_features
    from .hybrid import score_jobs_hybrid
    from .ontology import load_ontology

    ontology = load_ontology()
    stats = ensure_job_features(survivors, features_db, ontology=ontology)
    print(f"[hybrid] features built={stats['built']} reused={stats['reused']} failed={stats['failed']}")

    candidate = build_candidate_features(
        profile_id,
        ontology=ontology,
        target_level=_ROLE_TO_LEVEL.get(cfg.role_mode, "intern"),
        db_path=features_db,
    )
    scored = score_jobs_hybrid(
        candidate, survivors, features_db, weights=cfg.hybrid_weights, ontology=ontology,
    )
    if not scored:
        return []

    print("[hybrid] top 10 by hybrid_total:")
    for idx, s in enumerate(scored[:10], start=1):
        job = s["job"]
        c = s["components"]
        print(
            f"  {idx:02d}. total={s['hybrid_total']:.1f} "
            f"[sk={c['skills']:.0f} bm={c['bm25']:.0f} emb={c['embedding']:.0f} "
            f"dom={c['domain']:.0f} lvl={c['level']:.0f}] | "
            f"{job.get('title', '')} @ {job.get('company', '')}"
        )

    reranked: list[dict] = []
    for s in scored:
        reranked.append(
            {
                "job": s["job"],
                # schema compatibility with the legacy path / matches columns
                "stage1_score": float(s["components"]["embedding"]) / 100.0,
                "stage2_score": float(s["hybrid_total"]) / 100.0,
                "hybrid": {
                    "total": s["hybrid_total"],
                    "components": s["components"],
                    "explanation": s["explanation"],
                    "scoring_version": s["scoring_version"],
                },
            }
        )
    return reranked


def run_pipeline(profile_id: str = "default", config: str = "") -> dict:
    """Full recall → rerank → fit → gate run. Callable from CLI, nightly, or the API."""
    cfg = load_config(config or None)
    root = Path(__file__).resolve().parents[2]

    jobs_db = _resolve(root, cfg.jobs_db_path)
    filters_path = _resolve(root, cfg.filters_path)
    matches_db = _resolve(root, cfg.matches_db_path)
    features_db = _resolve(root, cfg.features_db_path)

    survivors = prefilter_jobs(
        jobs_db_path=jobs_db,
        role_mode=cfg.role_mode,
        filters_path=filters_path,
        search_bypass_internship=cfg.search_bypass_internship,
    )
    if not survivors:
        print("[done] no survivors after prefilter")
        return {"stored": 0, "strong": 0, "stretch": 0, "stage": "prefilter"}

    ranking = "legacy"
    reranked: list[dict] = []
    if cfg.use_hybrid_ranking:
        try:
            reranked = _hybrid_rank(profile_id, survivors, cfg, features_db)
            ranking = "hybrid"
        except Exception as exc:  # noqa: BLE001 — degrade to legacy, never kill the run
            print(f"[hybrid] failed: {type(exc).__name__}: {exc} — falling back to legacy recall/rerank")
            reranked = []
    if not reranked:
        reranked = _legacy_rank(profile_id, survivors, cfg)
        ranking = "legacy" if ranking != "hybrid" else "hybrid_empty→legacy"
    if not reranked:
        print("[done] no candidates after ranking")
        return {"stored": 0, "strong": 0, "stretch": 0, "stage": "rank"}
    print(f"[rank] using {ranking}; candidates={len(reranked)}")

    # Per-job search boost from matched_searches (config default, capped in scorer).
    try:
        from scraper.searches import max_boost_for_names
    except ImportError:
        from backend.scraper.searches import max_boost_for_names  # type: ignore
    for item in reranked:
        job = item.get("job") or {}
        tags = job.get("matched_searches") or []
        if tags:
            # Config default (+5) is the cap; per-search boost from yaml can be lower.
            configured = max_boost_for_names(tags)
            item["search_boost"] = min(int(cfg.search_alignment_boost), configured or int(cfg.search_alignment_boost))

    fitted: list[dict] = []
    fallback_fit = {
        "match_pct": 0,
        "matched_skills": [],
        "missing_skills": [],
        "best_projects": [],
        "rationale": "LLM fit stage unavailable",
    }
    try:
        fitted = fit_candidates(
            profile_id=profile_id,
            reranked=reranked,
            top_fit=cfg.top_fit,
            llm_prefer=cfg.llm_prefer,
            search_boost=cfg.search_alignment_boost,
            strong_threshold=cfg.strong_threshold,
            enable_legitimacy_web=cfg.enable_legitimacy_web,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[stage3] blocked: {exc}")
        print("[stage3] sample fit object:")
        print(json.dumps(fallback_fit, indent=2, ensure_ascii=False))

    if fitted:
        print("[stage3] sample fit object:")
        print(json.dumps(fitted[0].get("fit", {}), indent=2, ensure_ascii=False))
        # Persist matched_searches + hybrid components onto fit (dashboard +
        # calibration read them back out of fit_json).
        for item in fitted:
            tags = (item.get("job") or {}).get("matched_searches") or []
            fit = item.setdefault("fit", {})
            fit["matched_searches"] = tags
            if item.get("hybrid"):
                fit["hybrid"] = item["hybrid"]
        stored = gate_and_store(
            matches_db_path=matches_db,
            profile_id=profile_id,
            fitted=fitted,
            match_threshold=cfg.match_threshold,
            strong_threshold=cfg.strong_threshold,
        )
    else:
        stored = {"stored": 0, "strong": 0, "stretch": 0}

    print(
        f"[store] cleared_threshold={stored['stored']} "
        f"strong={stored['strong']} stretch={stored['stretch']} "
        f"(MATCH_THRESHOLD={cfg.match_threshold}, STRONG={cfg.strong_threshold})"
    )
    return {**stored, "stage": "done"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Matcher pipeline")
    parser.add_argument("--profile-id", default="default")
    parser.add_argument("--config", default="")
    args = parser.parse_args()
    run_pipeline(profile_id=args.profile_id, config=args.config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
