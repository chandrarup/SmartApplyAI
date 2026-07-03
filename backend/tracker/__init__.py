"""M6 application tracker — the system's memory of actions taken.

Owns tracker.db (CLAUDE.md rule 8): application rows with exact-artifact resume
versioning, a manual status pipeline, dedupe/rejection-history guards, and
human-scale pacing that releases approved items to "ready to apply" (never
auto-submits — rule 1).
"""
