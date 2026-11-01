"""Deterministic employer sponsorship scoring from DOL LCA aggregates."""

from __future__ import annotations

import re

_KNOCKOUTS = (
    re.compile(r"\b(?:u\.?\s*s\.?|united states)\s+citizens?\s+only\b", re.I),
    re.compile(r"\bno sponsorship\b", re.I),
    re.compile(r"\b(?:cannot|can not|not able to)\s+sponsor\b", re.I),
    re.compile(r"\bgreen card\s+required\b", re.I),
    re.compile(r"\bsecurity clearance\b", re.I),
)


def calculate_sponsorship_score(
    *,
    total_cases: int,
    certified_cases: int,
    recent_cases: int = 0,
    matching_domain_cases: int = 0,
    matching_soc_cases: int = 0,
) -> float:
    """Score 0-100 using approval, volume, recency, domain, and SOC evidence."""
    total = max(0, int(total_cases))
    if not total:
        return 0.0
    approval = min(1.0, max(0.0, certified_cases / total))
    volume = min(1.0, total / 20.0)
    recency = min(1.0, max(0.0, recent_cases) / 10.0)
    domain = min(1.0, max(0.0, matching_domain_cases) / 10.0)
    soc = min(1.0, max(0.0, matching_soc_cases) / 5.0)
    return round(100 * (0.40 * approval + 0.20 * volume + 0.15 * recency + 0.15 * domain + 0.10 * soc), 1)


def sponsorship_badge(score: float) -> str:
    score = float(score)
    if score >= 85:
        return "strong"
    if score >= 70:
        return "moderate"
    if score > 0:
        return "limited"
    return "unknown"


def sponsorship_knockout_reason(text: str | None) -> str | None:
    for pattern in _KNOCKOUTS:
        match = pattern.search(text or "")
        if match:
            return match.group(0)
    return None
