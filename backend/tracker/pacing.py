"""Pacing / velocity gate (M6, CLAUDE.md rule 11).

Approved ≠ released. Dozens of applications in minutes is a bot signature even when a
human clicks submit — and rubber-stamping 30/day destroys quality. This gate promotes
approved rows to 'ready_to_apply' only within human-scale caps: ≤2/company/week,
≤10/day, and a minimum spacing between releases.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from . import store
    from .config import (
        PacingConfig, STATUS_APPROVED, STATUS_READY, load_pacing_config,
    )
except ImportError:  # pragma: no cover
    from tracker import store
    from tracker.config import (
        PacingConfig, STATUS_APPROVED, STATUS_READY, load_pacing_config,
    )

# Statuses that count against caps once released (released or already applied).
_RELEASED_STATES = {STATUS_READY, "applied", "confirmed", "screen", "interview", "offer", "rejected", "ghosted"}


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _release_time(app: dict[str, Any]) -> datetime | None:
    # A row consumes cap capacity at the moment it was *released* or *applied* — not
    # when its status was later edited (e.g. marking a stale row 'rejected' must never
    # count as a release event, or it would freeze the spacing gate).
    return _parse_ts(app.get("date_released")) or _parse_ts(app.get("date_applied"))


def check_caps(
    profile_id: str,
    company: str,
    *,
    now: datetime | None = None,
    cfg: PacingConfig | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Preview whether one more release for `company` is allowed right now."""
    now = now or datetime.now(timezone.utc)
    cfg = cfg or load_pacing_config()
    company_norm = store.normalize_company(company)
    apps = store.list_applications(profile_id, db_path=db_path)

    released = [(a, _release_time(a)) for a in apps if a["status"] in _RELEASED_STATES]
    day_count = sum(1 for _, t in released if t and (now - t) <= timedelta(days=1))
    week_company = sum(
        1 for a, t in released
        if a.get("company_norm") == company_norm and t and (now - t) <= timedelta(days=7)
    )
    last_release = max((t for _, t in released if t), default=None)
    spacing_ok = last_release is None or (now - last_release) >= timedelta(minutes=cfg.min_spacing_minutes)

    reason = ""
    if day_count >= cfg.per_day:
        reason = f"daily cap reached ({day_count}/{cfg.per_day})"
    elif week_company >= cfg.per_company_per_week:
        reason = f"weekly cap for {company} reached ({week_company}/{cfg.per_company_per_week})"
    elif not spacing_ok:
        mins = int((now - last_release).total_seconds() // 60) if last_release else 0
        reason = f"too soon since last release ({mins}m < {cfg.min_spacing_minutes}m spacing)"
    return {"allowed": reason == "", "reason": reason,
            "day_count": day_count, "week_company": week_company}


def release_ready(
    profile_id: str,
    *,
    now: datetime | None = None,
    cfg: PacingConfig | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Promote as many 'approved' rows to 'ready_to_apply' as the caps allow.

    Oldest approvals first (fair queueing). Spacing is enforced across the batch by
    advancing a virtual clock, so a single run never dumps 10 releases at the same
    instant — it still respects min_spacing between each.
    """
    now = now or datetime.now(timezone.utc)
    cfg = cfg or load_pacing_config()
    approved = sorted(
        store.list_applications(profile_id, status=STATUS_APPROVED, db_path=db_path),
        key=lambda a: a.get("date_approved") or a.get("created_at") or "",
    )
    released: list[str] = []
    held: list[dict[str, Any]] = []
    virtual_now = now
    for app in approved:
        caps = check_caps(profile_id, app["company"], now=virtual_now, cfg=cfg, db_path=db_path)
        if not caps["allowed"]:
            held.append({"id": app["id"], "company": app["company"], "reason": caps["reason"]})
            continue
        store.update_application(
            profile_id, app["id"],
            {"status": STATUS_READY, "date_released": virtual_now.isoformat()},
            db_path=db_path,
        )
        released.append(app["id"])
        virtual_now = virtual_now + timedelta(minutes=cfg.min_spacing_minutes)
    return {"released": released, "held": held}
