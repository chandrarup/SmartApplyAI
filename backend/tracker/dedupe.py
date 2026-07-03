"""Dedupe / rejection-history guard (M6).

Before an approval becomes a tracked application, ask: have we already committed to
this company for a similar role, or were we rejected there recently? Reapplying into
a fresh rejection reads as spam and starts at a trust deficit. We warn/block; the
human can override with force=True (rule 1 keeps them in control).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

try:
    from . import store
    from .config import ACTIVE_STATUSES, STATUS_REJECTED, load_pacing_config
except ImportError:  # pragma: no cover
    from tracker import store
    from tracker.config import ACTIVE_STATUSES, STATUS_REJECTED, load_pacing_config

SIMILAR_TITLE_RATIO = 0.8

# Expand common role acronyms so "SWE Intern" ≈ "Software Engineer Intern".
_ACRONYMS = {
    "swe": "software engineer",
    "sde": "software development engineer",
    "sre": "site reliability engineer",
    "ml": "machine learning",
    "ai": "artificial intelligence",
    "ds": "data science",
    "nlp": "natural language processing",
    "pm": "product manager",
    "qa": "quality assurance",
    "eng": "engineer",
    "sr": "senior",
    "jr": "junior",
}


def _expand(norm_title: str) -> str:
    return " ".join(_ACRONYMS.get(tok, tok) for tok in norm_title.split())


def _title_similar(a: str, b: str) -> float:
    na = _expand(store.normalize_title(a))
    nb = _expand(store.normalize_title(b))
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    # Token overlap OR sequence ratio — either catches "SWE Intern" vs "Software Engineer Intern".
    ta, tb = set(na.split()), set(nb.split())
    jaccard = len(ta & tb) / len(ta | tb) if (ta | tb) else 0.0
    return max(jaccard, SequenceMatcher(None, na, nb).ratio())


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def check(
    profile_id: str,
    company: str,
    title: str,
    *,
    window_days: int | None = None,
    now: datetime | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Return {blocked, reason, matches:[...]} for a proposed application.

    Blocked when an *active* application to the same company for a similar title
    already exists, or a rejection to a similar role landed within window_days.
    """
    now = now or datetime.now(timezone.utc)
    if window_days is None:
        window_days = load_pacing_config().rejection_window_days
    company_norm = store.normalize_company(company)

    same_company = [
        a for a in store.list_applications(profile_id, db_path=db_path)
        if a.get("company_norm") == company_norm
    ]

    dup_matches: list[dict[str, Any]] = []
    rejection_matches: list[dict[str, Any]] = []
    for a in same_company:
        ratio = _title_similar(title, a.get("role", ""))
        if ratio < SIMILAR_TITLE_RATIO:
            continue
        info = {
            "id": a["id"], "company": a["company"], "role": a["role"],
            "status": a["status"], "similarity": round(ratio, 3),
        }
        if a["status"] in ACTIVE_STATUSES:
            dup_matches.append(info)
        elif a["status"] == STATUS_REJECTED:
            ref = _parse_ts(a.get("updated_at")) or _parse_ts(a.get("created_at"))
            if ref and (now - ref) <= timedelta(days=window_days):
                info["days_ago"] = (now - ref).days
                rejection_matches.append(info)

    if dup_matches:
        return {
            "blocked": True,
            "reason": (
                f"Already have an active application to {company} for a similar role "
                f"({dup_matches[0]['role']}, status={dup_matches[0]['status']})."
            ),
            "kind": "duplicate",
            "matches": dup_matches + rejection_matches,
        }
    if rejection_matches:
        m = rejection_matches[0]
        return {
            "blocked": True,
            "reason": (
                f"Rejected by {company} for a similar role ({m['role']}) {m['days_ago']} days ago "
                f"(within the {window_days}-day window)."
            ),
            "kind": "recent_rejection",
            "matches": rejection_matches,
        }
    return {"blocked": False, "reason": "", "kind": None, "matches": []}
