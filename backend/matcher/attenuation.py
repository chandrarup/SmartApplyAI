"""Deterministic longevity attenuation for stale job matches."""

from __future__ import annotations

import math
from typing import Any


def ghost_lambda(age_days: float) -> float:
    """Return the exponential freshness multiplier for an expired listing."""
    return math.exp(-float(age_days) / 360.0)


def attenuate_match_score(
    score: float,
    liveness: str,
    *,
    age_days: float = 0.0,
) -> float:
    """Attenuate only expired listings; preserve live and uncertain scores."""
    if str(liveness).lower() != "expired":
        return round(float(score), 2)
    return round(float(score) * ghost_lambda(age_days), 2)
