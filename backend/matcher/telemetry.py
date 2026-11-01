"""Append-only matcher feedback telemetry."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


def append_event(path: str | Path, event: dict[str, Any]) -> None:
    record = dict(event)
    record.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def summarize(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    counts: dict[str, int] = {}
    if not target.exists():
        return {"total": 0, "events": counts}
    total = 0
    for line in target.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = str(event.get("event") or "unknown")
        counts[name] = counts.get(name, 0) + 1
        total += 1
    return {"total": total, "events": counts}
