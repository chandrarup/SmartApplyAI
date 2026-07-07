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


def _resolve(root: Path, maybe_relative: str) -> Path:
    path = Path(maybe_relative)
    return path if path.is_absolute() else (root / path)


def run_pipeline(profile_id: str = "default", config: str = "") -> dict:
    """Full recall → rerank → fit → gate run. Callable from CLI, nightly, or the API."""
    cfg = load_config(config or None)
    root = Path(__file__).resolve().parents[2]

    jobs_db = _resolve(root, cfg.jobs_db_path)
    filters_path = _resolve(root, cfg.filters_path)
    matches_db = _resolve(root, cfg.matches_db_path)

    survivors = prefilter_jobs(
        jobs_db_path=jobs_db,
        role_mode=cfg.role_mode,
        filters_path=filters_path,
    )
    if not survivors:
        print("[done] no survivors after prefilter")
        return {"stored": 0, "strong": 0, "stretch": 0, "stage": "prefilter"}

    recalled = recall_candidates(
        profile_id=profile_id,
        jobs=survivors,
        top_recall=cfg.top_recall,
        evidence_k=cfg.recall_evidence_k,
    )
    if not recalled:
        print("[done] no candidates after stage1 recall")
        return {"stored": 0, "strong": 0, "stretch": 0, "stage": "recall"}

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
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[stage3] blocked: {exc}")
        print("[stage3] sample fit object:")
        print(json.dumps(fallback_fit, indent=2, ensure_ascii=False))

    if fitted:
        print("[stage3] sample fit object:")
        print(json.dumps(fitted[0].get("fit", {}), indent=2, ensure_ascii=False))
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
