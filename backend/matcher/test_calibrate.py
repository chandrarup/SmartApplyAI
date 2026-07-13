"""Tests for offline weight calibration (Phase-2 §3.6)."""

from __future__ import annotations

import json
import sqlite3

import numpy as np

from backend.matcher import calibrate
from backend.matcher.hybrid import DEFAULT_WEIGHTS


def test_rank_auc_perfect_separation():
    scores = np.array([0.1, 0.2, 0.8, 0.9])
    labels = np.array([0, 0, 1, 1])
    assert calibrate.rank_auc(scores, labels) == 1.0


def test_rank_auc_inverted_is_zero():
    scores = np.array([0.9, 0.8, 0.2, 0.1])
    labels = np.array([0, 0, 1, 1])
    assert calibrate.rank_auc(scores, labels) == 0.0


def test_rank_auc_single_class_is_half():
    assert calibrate.rank_auc(np.array([1.0, 2.0]), np.array([1, 1])) == 0.5


def test_weight_grid_all_valid():
    seen = list(calibrate._weight_grid())
    assert len(seen) > 100
    for w in seen:
        assert abs(sum(w.values()) - 1.0) < 1e-9
        assert all(v >= calibrate.MIN_WEIGHT - 1e-9 for v in w.values())
        assert set(w) == set(calibrate.COMPONENT_KEYS)


def _make_matches_db(tmp_path, samples):
    """samples: list of (review_status, components_dict)."""
    db = tmp_path / "matches.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE matches (id INTEGER PRIMARY KEY, review_status TEXT, fit_json TEXT)"
    )
    for i, (status, comps) in enumerate(samples):
        fit = {"hybrid": {"components": comps}} if comps is not None else {}
        conn.execute(
            "INSERT INTO matches (id, review_status, fit_json) VALUES (?, ?, ?)",
            (i, status, json.dumps(fit)),
        )
    conn.commit()
    conn.close()
    return db


def test_load_labeled_samples_filters_unlabeled_and_missing(tmp_path):
    db = _make_matches_db(tmp_path, [
        ("approved", {"skills": 90, "bm25": 50, "embedding": 70, "domain": 100, "level": 100}),
        ("rejected", {"skills": 20, "bm25": 10, "embedding": 30, "domain": 30, "level": 25}),
        ("new", {"skills": 50, "bm25": 50, "embedding": 50, "domain": 50, "level": 50}),  # unlabeled
        ("approved", None),  # no hybrid components
    ])
    X, y = calibrate.load_labeled_samples(db)
    assert X.shape == (2, 5)  # only the two labeled+component rows
    assert set(y.tolist()) == {0, 1}


def test_calibrate_insufficient_data(tmp_path):
    db = _make_matches_db(tmp_path, [
        ("approved", {"skills": 90, "bm25": 50, "embedding": 70, "domain": 100, "level": 100}),
    ])
    res = calibrate.calibrate(db, dict(DEFAULT_WEIGHTS))
    assert res["status"] == "insufficient_data"


def test_calibrate_finds_separating_weights(tmp_path):
    # Construct data where 'skills' perfectly separates but the current
    # (balanced) weights blur it — calibration should push weight toward skills.
    samples = []
    rng = np.random.default_rng(0)
    for _ in range(20):
        # positives: high skills, noisy elsewhere
        samples.append(("approved", {
            "skills": 95, "bm25": float(rng.uniform(0, 100)),
            "embedding": float(rng.uniform(0, 100)),
            "domain": float(rng.uniform(0, 100)), "level": float(rng.uniform(0, 100))}))
    for _ in range(20):
        samples.append(("rejected", {
            "skills": 5, "bm25": float(rng.uniform(0, 100)),
            "embedding": float(rng.uniform(0, 100)),
            "domain": float(rng.uniform(0, 100)), "level": float(rng.uniform(0, 100))}))
    db = _make_matches_db(tmp_path, samples)
    res = calibrate.calibrate(db, dict(DEFAULT_WEIGHTS))
    assert res["status"] == "ok"
    assert res["best_auc"] >= res["current_auc"]
    assert res["best_weights"]["skills"] >= DEFAULT_WEIGHTS["skills"]  # leaned into the separator
    assert abs(sum(res["best_weights"].values()) - 1.0) < 1e-9


def test_write_config_bumps_version(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("role_mode: internship\nscoring_version: v1\n", encoding="utf-8")
    calibrate.write_config(cfg, dict(DEFAULT_WEIGHTS), calibrate._bump_version("v1"))
    import yaml
    data = yaml.safe_load(cfg.read_text())
    assert data["scoring_version"] == "v2"
    assert abs(sum(data["hybrid_weights"].values()) - 1.0) < 1e-9
    assert data["role_mode"] == "internship"  # untouched keys preserved


def test_bump_version():
    assert calibrate._bump_version("v1") == "v2"
    assert calibrate._bump_version("v9") == "v10"
    assert calibrate._bump_version("") == "v2"
