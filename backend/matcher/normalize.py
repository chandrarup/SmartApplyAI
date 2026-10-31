"""Canonical job normalization for matcher inputs."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable, Mapping

from .taxonomy import TaxonomyV3, load_taxonomy


_LEVELS: tuple[tuple[str, str], ...] = (
    ("intern", r"\b(?:intern|internship|co[\s-]?op)\b"),
    ("entry", r"\b(?:junior|jr|new[\s-]?grad|entry[\s-]?level|associate)\b"),
    ("senior", r"\b(?:senior|sr|staff|principal|lead|director|manager)\b"),
    ("mid", r"\b(?:ii|iii|mid[\s-]?level)\b"),
)
_ABBREVIATIONS = {
    "sr": "senior",
    "jr": "junior",
    "swe": "software engineer",
    "eng": "engineer",
    "dev": "developer",
    "genai": "generative ai",
    "ai/ml": "artificial intelligence machine learning",
    "ml": "machine learning",
    "co-op": "co op",
}


@dataclass(frozen=True, slots=True)
class CanonicalJob:
    title_raw: str
    title_normalized: str
    family: str
    level: str
    tokens: tuple[str, ...]
    taxonomy_version: str
    metadata: dict[str, Any]
    fallback_used: bool = False


def normalize_title(title: str) -> str:
    text = (title or "").casefold()
    text = text.replace("&", " and ")
    for source, target in sorted(_ABBREVIATIONS.items(), key=lambda item: -len(item[0])):
        text = re.sub(rf"(?<![a-z0-9]){re.escape(source)}(?![a-z0-9])", target, text)
    text = re.sub(r"[/_+]", " ", text)
    text = re.sub(r"[^a-z0-9\s-]", " ", text)
    text = text.replace("-", " ")
    return " ".join(text.split())


def _level(title: str) -> str:
    for value, pattern in _LEVELS:
        if re.search(pattern, title, re.IGNORECASE):
            return value
    return "unknown"


def normalize_job(
    job: Mapping[str, Any],
    *,
    taxonomy: TaxonomyV3 | None = None,
    fallback: Callable[[str], Mapping[str, str]] | None = None,
) -> CanonicalJob:
    raw_title = str(job.get("title") or "")
    normalized = normalize_title(raw_title)
    onto = taxonomy or load_taxonomy()
    matches = onto.match(normalized)
    family = next((token.family for token in matches if token.family), "unknown")
    level = _level(normalized)
    fallback_used = False
    if family == "unknown" or level == "unknown":
        fallback_used = True
        if fallback is not None:
            try:
                suggestion = dict(fallback(raw_title))
                family = str(suggestion.get("family") or family)
                level = str(suggestion.get("level") or level)
            except Exception:
                pass
    metadata = {str(key): value for key, value in job.items() if key != "title"}
    return CanonicalJob(
        title_raw=raw_title,
        title_normalized=normalized,
        family=family,
        level=level,
        tokens=tuple(normalized.split()),
        taxonomy_version=onto.version,
        metadata=metadata,
        fallback_used=fallback_used,
    )
