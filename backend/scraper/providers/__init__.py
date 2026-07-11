"""ATS provider plugins — detect(careers_url) + fetch(entry)."""

from __future__ import annotations

from .registry import load_providers, resolve_provider

__all__ = ["load_providers", "resolve_provider"]
