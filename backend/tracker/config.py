"""Tracker configuration — status vocabulary + pacing caps (CLAUDE.md rule 11)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

try:
    import yaml
except ImportError:  # yaml is already a dep via matcher; degrade to defaults
    yaml = None  # type: ignore


# ── Status pipeline ───────────────────────────────────────────────────────────
# Pre-application lifecycle then the human-updated outcome pipeline.
STATUS_APPROVED = "approved"          # cleared review; not yet released by pacing
STATUS_READY = "ready_to_apply"       # pacing released it; human may now apply
STATUS_APPLIED = "applied"            # human submitted (rule 1: human clicks)
STATUS_CONFIRMED = "confirmed"        # employer acknowledged receipt
STATUS_SCREEN = "screen"              # OA / recruiter screen
STATUS_INTERVIEW = "interview"
STATUS_OFFER = "offer"
STATUS_REJECTED = "rejected"
STATUS_GHOSTED = "ghosted"

# Ordered for UI pipeline columns.
STATUS_PIPELINE = [
    STATUS_APPROVED, STATUS_READY, STATUS_APPLIED, STATUS_CONFIRMED,
    STATUS_SCREEN, STATUS_INTERVIEW, STATUS_OFFER, STATUS_REJECTED, STATUS_GHOSTED,
]
ALL_STATUSES = set(STATUS_PIPELINE)

# A "callback" (analytics numerator) = reached a real recruiter touchpoint.
# Decided with the user: screen or beyond; 'confirmed' receipt acks do NOT count.
CALLBACK_STATUSES = {STATUS_SCREEN, STATUS_INTERVIEW, STATUS_OFFER}

# Statuses that count as "an application exists" for dedupe purposes (i.e. we've
# committed to this company/role). Pre-release states count too, so we don't queue
# two approvals for the same job.
ACTIVE_STATUSES = {
    STATUS_APPROVED, STATUS_READY, STATUS_APPLIED, STATUS_CONFIRMED,
    STATUS_SCREEN, STATUS_INTERVIEW, STATUS_OFFER,
}


@dataclass(slots=True)
class PacingConfig:
    # CLAUDE.md rule 11 defaults.
    per_company_per_week: int = 2
    per_day: int = 10
    min_spacing_minutes: int = 45  # human-scale spacing between releases
    rejection_window_days: int = 90  # dedupe: reapply guard after a rejection


def load_pacing_config(config_path: str | Path | None = None) -> PacingConfig:
    base = PacingConfig()
    path = Path(config_path) if config_path else Path(__file__).with_name("config.yaml")
    if yaml is None or not path.is_file():
        return base
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return base
    pacing = data.get("pacing", data) if isinstance(data, dict) else {}
    return PacingConfig(
        per_company_per_week=int(pacing.get("per_company_per_week", base.per_company_per_week)),
        per_day=int(pacing.get("per_day", base.per_day)),
        min_spacing_minutes=int(pacing.get("min_spacing_minutes", base.min_spacing_minutes)),
        rejection_window_days=int(pacing.get("rejection_window_days", base.rejection_window_days)),
    )
