"""Stage 3 fit scoring — thin wrapper over the shared five-dimension scorer.

ONE implementation: backend.scoring.score_job. Analyze, Tailor, and matcher
all use the same dimensions + weighted match_pct + knockouts.

After scoring, attaches a legitimacy assessment (Block G) that never changes
match_pct and never drops the job.
"""

from __future__ import annotations

from typing import Any

try:
    from backend.knowledge import store as knowledge_store
    import backend.scoring as scoring
    from backend.matcher.legitimacy import assess_legitimacy
except ImportError:
    from knowledge import store as knowledge_store  # type: ignore
    import scoring  # type: ignore
    from matcher.legitimacy import assess_legitimacy  # type: ignore


def fit_candidates(
    profile_id: str,
    reranked: list[dict[str, Any]],
    top_fit: int = 30,
    llm_prefer: str = "ollama",
    search_boost: int = 0,
    strong_threshold: int = 85,
    enable_legitimacy_web: bool = True,
    web_search=None,
) -> list[dict[str, Any]]:
    if not reranked:
        return []

    profile = knowledge_store.get_profile(profile_id)
    selected = reranked[:top_fit]

    for item in selected:
        job = item.get("job") or {}
        boost = int(item.get("search_boost") or 0)
        if not boost and search_boost:
            tags = job.get("matched_searches") or []
            if tags:
                boost = int(search_boost)
        try:
            fit_obj = scoring.score_job(
                job.get("description_text") or "",
                profile,
                title=str(job.get("title") or ""),
                company=str(job.get("company") or ""),
                llm=llm_prefer,
                search_boost=boost,
            )
        except Exception:  # noqa: BLE001 — one bad job never kills the run
            fit_obj = scoring.fallback_fit("Fit stage exception")

        # Legitimacy is additive — never mutates match_pct, never drops.
        try:
            fit_obj["legitimacy"] = assess_legitimacy(
                job,
                match_pct=int(fit_obj.get("match_pct") or 0),
                strong_threshold=strong_threshold,
                enable_web=enable_legitimacy_web,
                web_search=web_search,
            )
        except Exception as exc:  # noqa: BLE001
            fit_obj["legitimacy"] = {
                "tier": "caution",
                "signals": [
                    {"code": "legitimacy_error", "detail": type(exc).__name__, "severity": "info"}
                ],
                "note": "Legitimacy check failed soft — treat as caution.",
            }

        item["fit"] = fit_obj
        item["match_pct"] = int(fit_obj.get("match_pct", 0) or 0)

    print(f"[stage3] fit_scored={len(selected)}")
    return selected
