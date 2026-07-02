"""Stage 1 semantic recall using knowledge.search."""

from __future__ import annotations

from typing import Any

from backend.knowledge import search as knowledge_search


def recall_candidates(
    profile_id: str,
    jobs: list[dict[str, Any]],
    top_recall: int = 50,
    evidence_k: int = 8,
) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    for job in jobs:
        jd_text = (job.get("description_text") or "").strip()
        if not jd_text:
            continue
        hits = knowledge_search(profile_id, query_text=jd_text, k=evidence_k)
        if not hits:
            continue
        coarse_score = float(hits[0].get("score", 0.0))
        scored.append(
            {
                "job": job,
                "stage1_score": coarse_score,
                "evidence": hits,
            }
        )

    scored.sort(key=lambda item: item["stage1_score"], reverse=True)
    kept = scored[:top_recall]
    print(f"[stage1] recalled={len(scored)} kept_top={len(kept)}")
    return kept

