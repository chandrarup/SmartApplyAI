"""Configuration loader for matcher pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(slots=True)
class MatcherConfig:
    role_mode: str = "internship"  # internship | fulltime | both
    # CLAUDE.md rule 10: 70+ enters the queue; Strong = 85+, Stretch = 70–84.
    # match_threshold gates queue entry; a match is Strong iff match_pct >= strong_threshold,
    # otherwise Stretch down to stretch_threshold (== match_threshold, the queue floor).
    match_threshold: int = 70
    strong_threshold: int = 85
    stretch_threshold: int = 70
    top_recall: int = 50
    top_fit: int = 30
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    rerank_model: str = "BAAI/bge-reranker-base"
    llm_prefer: str = "ollama"
    recall_evidence_k: int = 8
    filters_path: str = "backend/scraper/filters.yaml"
    jobs_db_path: str = "backend/scraper/jobs.db"
    matches_db_path: str = "backend/matcher/matches.db"


def _merge(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(config_path: str | Path | None = None) -> MatcherConfig:
    path = Path(config_path) if config_path else Path(__file__).with_name("config.yaml")
    base = MatcherConfig()
    if not path.is_file():
        return base

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return base

    merged = _merge(asdict(base), data)
    return MatcherConfig(
        role_mode=str(merged.get("role_mode", base.role_mode)).lower(),
        match_threshold=int(merged.get("match_threshold", base.match_threshold)),
        strong_threshold=int(merged.get("strong_threshold", base.strong_threshold)),
        stretch_threshold=int(merged.get("stretch_threshold", base.stretch_threshold)),
        top_recall=int(merged.get("top_recall", base.top_recall)),
        top_fit=int(merged.get("top_fit", base.top_fit)),
        embedding_model=str(merged.get("embedding_model", base.embedding_model)),
        rerank_model=str(merged.get("rerank_model", base.rerank_model)),
        llm_prefer=str(merged.get("llm_prefer", base.llm_prefer)),
        recall_evidence_k=int(merged.get("recall_evidence_k", base.recall_evidence_k)),
        filters_path=str(merged.get("filters_path", base.filters_path)),
        jobs_db_path=str(merged.get("jobs_db_path", base.jobs_db_path)),
        matches_db_path=str(merged.get("matches_db_path", base.matches_db_path)),
    )

