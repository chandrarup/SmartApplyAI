"""LLM access for the matcher fit stage.

Delegates to the shared provider layer (backend/llm_provider.py) per CLAUDE.md
rule 9 — never call a provider directly. Re-exports keep `from .llm import
call_llm, clean_json` working for fit.py.
"""

from __future__ import annotations

try:
    from backend.llm_provider import call_llm, call_ollama, call_claude, clean_json
except ImportError:  # invoked with cwd=backend/ (e.g. python -m matcher.run)
    from llm_provider import call_llm, call_ollama, call_claude, clean_json

__all__ = ["call_llm", "call_ollama", "call_claude", "clean_json"]
