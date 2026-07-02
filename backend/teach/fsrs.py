"""Lightweight FSRS-style scheduler helpers (no external deps)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

VALID_GRADES = {"again", "hard", "good", "easy"}


@dataclass
class ReviewState:
    skill: str
    stability: float = 0.6
    difficulty: float = 5.0
    reps: int = 0
    lapses: int = 0
    due_date: str = ""
    state: str = "new"


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def interval_days(state: ReviewState, grade: str) -> int:
    """Compute next interval in days using a compact FSRS-like heuristic."""
    if grade not in VALID_GRADES:
        raise ValueError(f"Invalid grade '{grade}'")

    # First review intervals mirror common FSRS defaults.
    if state.reps == 0:
        return {"again": 0, "hard": 1, "good": 3, "easy": 5}[grade]

    base = max(0.3, state.stability)
    diff_factor = (11.0 - state.difficulty) / 10.0
    if grade == "again":
        return 0
    if grade == "hard":
        return max(1, round(base * 1.2 * diff_factor))
    if grade == "good":
        return max(1, round(base * 2.0 * diff_factor))
    return max(2, round(base * 3.0 * diff_factor))


def apply_review(current: dict, grade: str, today: date | None = None) -> dict:
    """Apply one review grade and return updated serializable state."""
    if grade not in VALID_GRADES:
        raise ValueError(f"Invalid grade '{grade}'")

    day = today or date.today()
    state = ReviewState(
        skill=current.get("skill", ""),
        stability=float(current.get("stability", 0.6)),
        difficulty=float(current.get("difficulty", 5.0)),
        reps=int(current.get("reps", 0)),
        lapses=int(current.get("lapses", 0)),
        due_date=current.get("due_date", ""),
        state=current.get("state", "new"),
    )

    state.reps += 1

    if grade == "again":
        state.lapses += 1
        state.stability = _clamp(state.stability * 0.55 + 0.05, 0.2, 365.0)
        state.difficulty = _clamp(state.difficulty + 0.6, 1.0, 10.0)
        state.state = "learning"
    elif grade == "hard":
        state.stability = _clamp(state.stability * 1.18 + 0.1, 0.2, 365.0)
        state.difficulty = _clamp(state.difficulty + 0.2, 1.0, 10.0)
        state.state = "review"
    elif grade == "good":
        state.stability = _clamp(state.stability * 1.75 + 0.2, 0.2, 365.0)
        state.difficulty = _clamp(state.difficulty - 0.1, 1.0, 10.0)
        state.state = "review"
    else:  # easy
        state.stability = _clamp(state.stability * 2.35 + 0.4, 0.2, 365.0)
        state.difficulty = _clamp(state.difficulty - 0.3, 1.0, 10.0)
        state.state = "review"

    ivl = interval_days(state, grade)
    state.due_date = (day + timedelta(days=ivl)).isoformat()

    return {
        "skill": state.skill,
        "stability": round(state.stability, 4),
        "difficulty": round(state.difficulty, 4),
        "reps": state.reps,
        "lapses": state.lapses,
        "due_date": state.due_date,
        "state": state.state,
        "last_grade": grade,
    }
