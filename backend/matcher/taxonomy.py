"""Deterministic title taxonomy used by the matcher.

TaxonomyV3 intentionally has no network or LLM dependency.  It is a small,
stable lookup seam that can later be backed by a richer classifier without
changing callers.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import ClassVar


@dataclass(frozen=True, slots=True)
class TaxonomyToken:
    canonical: str
    family: str
    level: str | None = None


@dataclass(frozen=True, slots=True)
class TaxonomyV3:
    version: str = "v3"

    _TOKENS: ClassVar[tuple[tuple[str, TaxonomyToken], ...]] = (
        ("machine learning", TaxonomyToken("machine learning", "machine_learning")),
        ("artificial intelligence", TaxonomyToken("artificial intelligence", "machine_learning")),
        ("generative ai", TaxonomyToken("generative ai", "machine_learning")),
        ("full stack", TaxonomyToken("full stack", "software")),
        ("software engineer", TaxonomyToken("software engineer", "software")),
        ("software developer", TaxonomyToken("software developer", "software")),
        ("data scientist", TaxonomyToken("data scientist", "data")),
        ("data engineer", TaxonomyToken("data engineer", "data")),
        ("bioinformatics", TaxonomyToken("bioinformatics", "data")),
        ("backend", TaxonomyToken("backend", "software")),
        ("back end", TaxonomyToken("backend", "software")),
        ("frontend", TaxonomyToken("frontend", "software")),
        ("front end", TaxonomyToken("frontend", "software")),
        ("platform", TaxonomyToken("platform", "software")),
        ("engineer", TaxonomyToken("engineer", "software")),
        ("developer", TaxonomyToken("developer", "software")),
        ("scientist", TaxonomyToken("scientist", "data")),
        ("ml", TaxonomyToken("machine learning", "machine_learning")),
        ("ai", TaxonomyToken("artificial intelligence", "machine_learning")),
        ("genai", TaxonomyToken("generative ai", "machine_learning")),
    )

    _ALIASES: ClassVar[dict[str, str]] = {
        "ml": "machine learning",
        "ai": "artificial intelligence",
        "genai": "generative ai",
        "gen ai": "generative ai",
        "back end": "backend",
        "front end": "frontend",
        "swe": "software engineer",
        "dev": "developer",
    }

    @staticmethod
    def normalize_text(value: str) -> str:
        value = (value or "").casefold().replace("&", " and ")
        value = re.sub(r"[/_+]", " ", value)
        value = re.sub(r"[^a-z0-9\s-]", " ", value)
        value = re.sub(r"(?<=\w)-(?=\w)", " ", value)
        return " ".join(value.split())

    def lookup(self, token: str) -> TaxonomyToken | None:
        normalized = self.normalize_text(token)
        normalized = self._ALIASES.get(normalized, normalized)
        for key, value in self._TOKENS:
            if normalized == key:
                return value
        return None

    def match(self, text: str) -> tuple[TaxonomyToken, ...]:
        normalized = self.normalize_text(text)
        matches: list[tuple[int, TaxonomyToken]] = []
        for key, token in self._TOKENS:
            if re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", normalized):
                matches.append((len(key), token))
        matches.sort(key=lambda item: (-item[0], item[1].canonical))
        unique: list[TaxonomyToken] = []
        seen: set[str] = set()
        for _, token in matches:
            if token.canonical not in seen:
                unique.append(token)
                seen.add(token.canonical)
        return tuple(unique)


def load_taxonomy() -> TaxonomyV3:
    return TaxonomyV3()
