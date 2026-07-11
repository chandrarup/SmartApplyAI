"""Stage 2 local cross-encoder rerank."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from sentence_transformers import CrossEncoder

try:
    from backend.knowledge import store as knowledge_store
except ImportError:
    from knowledge import store as knowledge_store  # type: ignore


def _profile_to_text(profile: dict[str, Any]) -> str:
    parts: list[str] = []
    summary = profile.get("summary")
    if isinstance(summary, str) and summary.strip():
        parts.append(f"Summary: {summary.strip()}")

    for exp in profile.get("experience", [])[:3]:
        if not isinstance(exp, dict):
            continue
        role = exp.get("role") or ""
        company = exp.get("company") or ""
        details = exp.get("details") or exp.get("bullets") or []
        details_txt = ""
        if isinstance(details, list):
            details_txt = " ".join([str(d) for d in details[:2]])
        parts.append(f"Experience: {role} at {company}. {details_txt}".strip())

    for proj in profile.get("projects", [])[:3]:
        if not isinstance(proj, dict):
            continue
        title = proj.get("title") or ""
        desc = proj.get("description") or ""
        parts.append(f"Project: {title}. {desc}".strip())

    return "\n".join(p for p in parts if p)


@lru_cache(maxsize=4)
def _load_cross_encoder(model_name: str) -> CrossEncoder:
    return CrossEncoder(model_name)


def rerank_candidates(
    profile_id: str,
    recalled: list[dict[str, Any]],
    rerank_model: str,
) -> list[dict[str, Any]]:
    if not recalled:
        return []

    profile = knowledge_store.get_profile(profile_id)
    profile_text = _profile_to_text(profile)

    pairs: list[list[str]] = []
    for item in recalled:
        jd = item["job"].get("description_text") or ""
        evidence_lines = [h.get("text", "") for h in item.get("evidence", [])[:3]]
        evidence_text = "\n".join([ln for ln in evidence_lines if ln])
        right_text = f"{profile_text}\n\nEvidence:\n{evidence_text}".strip()
        pairs.append([jd, right_text])

    model = _load_cross_encoder(rerank_model)
    scores = model.predict(pairs, batch_size=16, show_progress_bar=False)
    for item, score in zip(recalled, scores):
        item["stage2_score"] = float(score)

    ranked = sorted(recalled, key=lambda item: item["stage2_score"], reverse=True)
    print(f"[stage2] reranked={len(ranked)}")
    return ranked

