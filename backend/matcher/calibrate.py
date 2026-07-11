"""Offline weight calibration for the hybrid scorer (Phase-2 §3.6, §8).

Reads review outcomes + stored hybrid components from matches.db, grid-searches
the weight simplex to maximize how well the fused score separates the jobs you
acted on from the ones you rejected (rank-based AUC), and — only with --write —
bumps the scoring version and persists the new weights to config.yaml.

Deterministic and batch/offline: individual scoring stays reproducible per
version; calibration never runs inside the nightly path and never auto-applies.
No sklearn — AUC is the Mann–Whitney statistic computed in numpy.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from itertools import product
from pathlib import Path
from typing import Any

import numpy as np
import yaml

try:
    from backend.matcher.hybrid import DEFAULT_WEIGHTS
    from backend.matcher.config import load_config
except ImportError:  # pragma: no cover
    from matcher.hybrid import DEFAULT_WEIGHTS  # type: ignore
    from matcher.config import load_config  # type: ignore

COMPONENT_KEYS = ("skills", "bm25", "embedding", "domain", "level")

# Review outcomes → binary relevance label.
POSITIVE_STATES = {"approved", "applied", "accepted", "customized"}
NEGATIVE_STATES = {"rejected", "skipped", "dismissed", "declined"}

GRID_STEP = 0.05
MIN_WEIGHT = 0.05
_UNITS = round(1.0 / GRID_STEP)          # 20
_MIN_UNITS = round(MIN_WEIGHT / GRID_STEP)  # 1


def load_labeled_samples(matches_db: str | Path) -> tuple[np.ndarray, np.ndarray]:
    """Return (X, y): X is (n, 5) component scores, y is 0/1 labels. Only rows
    whose fit_json carries hybrid.components AND a labeled review_status count."""
    rows_x: list[list[float]] = []
    rows_y: list[int] = []
    conn = sqlite3.connect(str(matches_db))
    try:
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT review_status, fit_json FROM matches").fetchall()
        except sqlite3.OperationalError:
            return np.empty((0, 5)), np.empty((0,))
        for row in rows:
            status = str(row["review_status"] or "").lower()
            if status in POSITIVE_STATES:
                label = 1
            elif status in NEGATIVE_STATES:
                label = 0
            else:
                continue
            try:
                fit = json.loads(row["fit_json"] or "{}")
                comps = fit["hybrid"]["components"]
                vec = [float(comps[k]) for k in COMPONENT_KEYS]
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
            rows_x.append(vec)
            rows_y.append(label)
    finally:
        conn.close()
    return np.asarray(rows_x, dtype=float), np.asarray(rows_y, dtype=int)


def rank_auc(scores: np.ndarray, labels: np.ndarray) -> float:
    """AUC via the Mann–Whitney U statistic with average ranks for ties.

    AUC = (sum_of_positive_ranks - n_pos*(n_pos+1)/2) / (n_pos * n_neg)."""
    n = len(labels)
    if n == 0:
        return 0.5
    pos = int(labels.sum())
    neg = n - pos
    if pos == 0 or neg == 0:
        return 0.5
    order = np.argsort(scores, kind="mergesort")
    ranks = np.empty(n, dtype=float)
    ranks[order] = np.arange(1, n + 1, dtype=float)
    # average ranks within tied score groups
    sorted_scores = scores[order]
    i = 0
    while i < n:
        j = i
        while j + 1 < n and sorted_scores[j + 1] == sorted_scores[i]:
            j += 1
        if j > i:
            avg = (ranks[order[i]] + ranks[order[j]]) / 2.0
            for k in range(i, j + 1):
                ranks[order[k]] = avg
        i = j + 1
    pos_rank_sum = ranks[labels == 1].sum()
    return float((pos_rank_sum - pos * (pos + 1) / 2.0) / (pos * neg))


def _weight_grid():
    """All 5-tuples of multiples of GRID_STEP that sum to 1.0, each >= MIN_WEIGHT."""
    span = range(_MIN_UNITS, _UNITS - 3 * _MIN_UNITS + 1)
    for a, b, c, d in product(span, repeat=4):
        e = _UNITS - (a + b + c + d)
        if e < _MIN_UNITS:
            continue
        yield {
            "skills": a * GRID_STEP, "bm25": b * GRID_STEP, "embedding": c * GRID_STEP,
            "domain": d * GRID_STEP, "level": e * GRID_STEP,
        }


def _fused(X: np.ndarray, weights: dict[str, float]) -> np.ndarray:
    w = np.array([weights[k] for k in COMPONENT_KEYS], dtype=float)
    return X @ w


def calibrate(matches_db: str | Path, current_weights: dict[str, float]) -> dict[str, Any]:
    X, y = load_labeled_samples(matches_db)
    n, pos = len(y), int(y.sum()) if len(y) else 0
    result: dict[str, Any] = {
        "n_samples": n, "n_positive": pos, "n_negative": n - pos,
        "current_weights": dict(current_weights),
    }
    if n < 10 or pos == 0 or pos == n:
        result["status"] = "insufficient_data"
        result["current_auc"] = rank_auc(_fused(X, current_weights), y) if n else None
        return result

    current_auc = rank_auc(_fused(X, current_weights), y)
    best_auc, best_w = current_auc, dict(current_weights)
    for w in _weight_grid():
        auc = rank_auc(_fused(X, w), y)
        if auc > best_auc:
            best_auc, best_w = auc, w
    result.update(
        status="ok", current_auc=round(current_auc, 4),
        best_auc=round(best_auc, 4),
        best_weights={k: round(v, 2) for k, v in best_w.items()},
        improvement=round(best_auc - current_auc, 4),
    )
    return result


def _bump_version(version: str) -> str:
    if version and version.startswith("v") and version[1:].isdigit():
        return f"v{int(version[1:]) + 1}"
    return "v2"


def write_config(config_path: Path, weights: dict[str, float], new_version: str) -> None:
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    data["hybrid_weights"] = {k: round(float(weights[k]), 2) for k in COMPONENT_KEYS}
    data["scoring_version"] = new_version
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Calibrate hybrid scoring weights (offline).")
    parser.add_argument("--config", default="")
    parser.add_argument("--write", action="store_true",
                       help="persist the best weights + bump scoring_version in config.yaml")
    args = parser.parse_args()

    cfg = load_config(args.config or None)
    root = Path(__file__).resolve().parents[2]
    matches_db = Path(cfg.matches_db_path)
    if not matches_db.is_absolute():
        matches_db = root / matches_db

    res = calibrate(matches_db, cfg.hybrid_weights)
    print(f"labeled samples: n={res['n_samples']} pos={res['n_positive']} neg={res['n_negative']}")
    if res["status"] == "insufficient_data":
        print("[calibrate] insufficient labeled data (need >=10 samples with both classes "
              "and stored hybrid components). No change.")
        return 0

    print(f"current weights: {res['current_weights']}  AUC={res['current_auc']}")
    print(f"best    weights: {res['best_weights']}  AUC={res['best_auc']}  "
          f"(+{res['improvement']})")

    if not args.write:
        print("[calibrate] dry run — re-run with --write to apply.")
        return 0
    if res["improvement"] <= 0:
        print("[calibrate] no improvement over current weights; not writing.")
        return 0

    config_path = Path(args.config) if args.config else Path(__file__).with_name("config.yaml")
    new_version = _bump_version(cfg.scoring_version)
    write_config(config_path, res["best_weights"], new_version)
    print(f"[calibrate] wrote {config_path.name}: weights updated, scoring_version -> {new_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
