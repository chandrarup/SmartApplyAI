"""Provider registry — load plugins and resolve which one owns a CompanyEntry.

Adapted from career-ops providers/_registry.mjs (patterns only, not vendored).
"""

from __future__ import annotations

from typing import Any, Callable, Protocol

from . import ashby, greenhouse, lever, personio, smartrecruiters, teamtailor, trackers, workday, workable
from .base import CompanyEntry


class Provider(Protocol):
    id: str

    def detect(self, entry: CompanyEntry) -> str | None: ...
    def fetch(self, entry: CompanyEntry) -> list[dict[str, Any]]: ...


# Deterministic load order (detect priority). Trackers last so explicit ATS wins.
_PROVIDER_MODULES = (
    greenhouse,
    lever,
    ashby,
    workday,
    smartrecruiters,
    workable,
    teamtailor,
    personio,
    trackers,
)


def load_providers() -> dict[str, Any]:
    providers: dict[str, Any] = {}
    for mod in _PROVIDER_MODULES:
        p = getattr(mod, "PROVIDER", None)
        if not p or not getattr(p, "id", None):
            continue
        if not callable(getattr(p, "fetch", None)):
            continue
        if p.id in providers:
            continue
        providers[p.id] = p
    return providers


def resolve_provider(
    entry: CompanyEntry,
    providers: dict[str, Any] | None = None,
) -> Any | None:
    """Explicit ats/provider field wins; else first detect() hit."""
    providers = providers or load_providers()
    explicit = (entry.ats or "").strip().lower()
    if explicit:
        p = providers.get(explicit)
        if p:
            return p
        return None

    for p in providers.values():
        detect: Callable = getattr(p, "detect", None)
        if not detect:
            continue
        try:
            hit = detect(entry)
        except Exception:
            continue
        if hit:
            return p
    return None
