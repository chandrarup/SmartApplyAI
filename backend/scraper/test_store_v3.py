"""Tests for sourcing-v3 store additions: migration safety, cross-source dedupe,
latest-jobs window, run history, and source-health cooldown (§2.1).

tmp_path databases only — never touches the real jobs.db.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from backend.scraper import store


def _job(external_id, company="Acme", title="ML Intern", location="Remote",
         remote=1, **over):
    job = {
        "external_id": external_id,
        "company": company,
        "title": title,
        "location": location,
        "remote_flag": remote,
        "is_internship": 1,
        "location_match": 1,
        "sponsorship_knockout": 0,
        "department": "Eng",
        "description_text": f"Job {external_id} description",
        "apply_url": f"https://apply/{external_id}",
        "posted_at": None,
        "updated_at": None,
        "raw_json": "{}",
        "matched_searches": [],
    }
    job.update(over)
    return job


# ── migration safety on a pre-v3 database ─────────────────────────────────────
def test_migration_preserves_data_and_adds_v3_objects(tmp_path):
    db = tmp_path / "jobs.db"
    # Build the OLD schema (pre-v3: no dedupe_hash, no v3 tables) and insert a row.
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE jobs (
            source_ats TEXT NOT NULL, company TEXT NOT NULL, external_id TEXT NOT NULL,
            title TEXT, location TEXT, remote_flag INTEGER NOT NULL DEFAULT 0,
            is_internship INTEGER NOT NULL DEFAULT 0, location_match INTEGER NOT NULL DEFAULT 0,
            sponsorship_knockout INTEGER NOT NULL DEFAULT 0, department TEXT,
            description_text TEXT, apply_url TEXT, posted_at TEXT, updated_at TEXT,
            raw_json TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('active', 'expired')),
            PRIMARY KEY (source_ats, external_id)
        )
        """
    )
    conn.execute(
        "INSERT INTO jobs (source_ats, company, external_id, title, location, raw_json, "
        "first_seen, last_seen, status) VALUES "
        "('greenhouse','Acme','OLD1','Old Job','NYC','{}','2020-01-01','2020-01-01','active')"
    )
    conn.commit()
    conn.close()

    # Reopen through the v3 path → migration runs.
    with store.get_conn(db) as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        assert "dedupe_hash" in cols
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert {"source_health", "runs", "job_sources"} <= tables
        # pre-existing row intact
        row = conn.execute("SELECT title, status FROM jobs WHERE external_id='OLD1'").fetchone()
        assert row["title"] == "Old Job" and row["status"] == "active"


def test_init_db_is_idempotent(tmp_path):
    db = tmp_path / "jobs.db"
    with store.get_conn(db):
        pass
    with store.get_conn(db) as conn:  # second open must not raise
        store.init_db(conn)


# ── dedupe hash normalization ─────────────────────────────────────────────────
def test_dedupe_hash_normalizes():
    a = store.compute_dedupe_hash("Acme, Inc.", "ML  Engineer", "New York, NY", 0)
    b = store.compute_dedupe_hash("acme inc", "ml engineer", "new york", 0)
    assert a == b


def test_dedupe_hash_remote_buckets_together():
    a = store.compute_dedupe_hash("Acme", "ML Eng", "San Francisco, CA", 1)
    b = store.compute_dedupe_hash("Acme", "ML Eng", "Austin, TX", 1)
    assert a == b  # both remote → same bucket


def test_dedupe_hash_distinguishes_titles():
    a = store.compute_dedupe_hash("Acme", "ML Engineer", "Remote", 1)
    b = store.compute_dedupe_hash("Acme", "Data Engineer", "Remote", 1)
    assert a != b


# ── cross-source dedupe policies ──────────────────────────────────────────────
def test_ats_supersedes_existing_tracker(tmp_path):
    db = tmp_path / "jobs.db"
    with store.get_conn(db) as conn:
        # tracker sees it first
        store.upsert_company_jobs(conn, "tracker", "Acme", [_job("t1")])
        assert conn.execute(
            "SELECT status FROM jobs WHERE source_ats='tracker'").fetchone()["status"] == "active"
        # then the real ATS posting arrives → tracker superseded
        res = store.upsert_company_jobs(conn, "greenhouse", "Acme", [_job("g1")])
        assert res["superseded"] == 1
        assert conn.execute(
            "SELECT status FROM jobs WHERE source_ats='tracker'").fetchone()["status"] == "expired"
        assert conn.execute(
            "SELECT status FROM jobs WHERE source_ats='greenhouse'").fetchone()["status"] == "active"
        link = conn.execute("SELECT * FROM job_sources").fetchone()
        assert link["canonical_source_ats"] == "greenhouse" and link["alt_source_ats"] == "tracker"


def test_tracker_suppressed_when_ats_already_present(tmp_path):
    db = tmp_path / "jobs.db"
    with store.get_conn(db) as conn:
        store.upsert_company_jobs(conn, "greenhouse", "Acme", [_job("g1")])
        res = store.upsert_company_jobs(conn, "tracker", "Acme", [_job("t1")])
        assert res["suppressed"] == 1
        assert res["new"] == 0
        # tracker row was never inserted as active
        got = conn.execute("SELECT status FROM jobs WHERE source_ats='tracker'").fetchone()
        assert got is None or got["status"] == "expired"
        link = conn.execute("SELECT * FROM job_sources").fetchone()
        assert link["canonical_source_ats"] == "greenhouse"


def test_two_ats_sources_both_kept(tmp_path):
    db = tmp_path / "jobs.db"
    with store.get_conn(db) as conn:
        store.upsert_company_jobs(conn, "greenhouse", "Acme", [_job("g1")])
        res = store.upsert_company_jobs(conn, "lever", "Acme", [_job("l1")])
        assert res["superseded"] == 0 and res["suppressed"] == 0
        active = conn.execute(
            "SELECT COUNT(*) c FROM jobs WHERE status='active'").fetchone()["c"]
        assert active == 2  # ATS-vs-ATS collisions are left alone


def test_distinct_jobs_not_deduped(tmp_path):
    db = tmp_path / "jobs.db"
    with store.get_conn(db) as conn:
        store.upsert_company_jobs(conn, "greenhouse", "Acme", [_job("g1", title="ML Engineer")])
        res = store.upsert_company_jobs(conn, "tracker", "Acme", [_job("t1", title="UX Designer")])
        assert res["suppressed"] == 0 and res["new"] == 1


# ── latest jobs window ────────────────────────────────────────────────────────
def test_latest_jobs_first_seen_window(tmp_path):
    db = tmp_path / "jobs.db"
    with store.get_conn(db) as conn:
        store.upsert_company_jobs(conn, "greenhouse", "Acme", [_job("fresh")])
        # backdate one row beyond the window
        old = (datetime.now(timezone.utc) - timedelta(hours=200)).isoformat()
        conn.execute(
            "INSERT INTO jobs (source_ats, company, external_id, title, location, raw_json, "
            "first_seen, last_seen, status, matched_searches) VALUES "
            "('greenhouse','Acme','stale','Stale','NYC','{}',?,?,'active','[]')", (old, old))
    fresh = store.latest_jobs(db, hours_first_seen=72)
    ids = {j["external_id"] for j in fresh}
    assert "fresh" in ids and "stale" not in ids


def test_latest_jobs_keyword_and_null_posted(tmp_path):
    db = tmp_path / "jobs.db"
    with store.get_conn(db) as conn:
        store.upsert_company_jobs(conn, "greenhouse", "Acme",
                                  [_job("a", title="Machine Learning Intern"),
                                   _job("b", title="Frontend Intern")])
    out = store.latest_jobs(db, keywords=["machine learning"])
    assert {j["external_id"] for j in out} == {"a"}  # NULL posted_at rows still returned


# ── run history ───────────────────────────────────────────────────────────────
def test_run_record_roundtrip(tmp_path):
    db = tmp_path / "jobs.db"
    rid = store.record_run_start(db, mode="scheduled")
    store.record_run_end(db, rid,
                         totals={"fetched": 100, "new": 10, "updated": 5, "expired": 2},
                         provider_stats={"greenhouse": {"fetched": 100}},
                         anomalies=[{"source": "lever", "type": "coverage_drop"}])
    runs = store.recent_runs(db, limit=5)
    assert len(runs) == 1
    r = runs[0]
    assert r["mode"] == "scheduled" and r["jobs_fetched"] == 100
    assert r["provider_stats"]["greenhouse"]["fetched"] == 100
    assert r["anomalies"][0]["type"] == "coverage_drop"
    assert r["finished_at"] is not None


# ── source health + cooldown ──────────────────────────────────────────────────
def test_health_ema_latency(tmp_path):
    db = tmp_path / "jobs.db"
    store.update_source_health(db, "greenhouse", ok=True, latency_ms=100.0)
    store.update_source_health(db, "greenhouse", ok=True, latency_ms=200.0)
    with store.get_conn(db) as conn:
        lat = conn.execute(
            "SELECT avg_latency_ms FROM source_health WHERE source_ats='greenhouse'"
        ).fetchone()["avg_latency_ms"]
    assert lat == pytest.approx(0.3 * 200 + 0.7 * 100)  # 130.0


def test_health_cooldown_enter_and_reset(tmp_path):
    db = tmp_path / "jobs.db"
    for _ in range(3):
        store.update_source_health(db, "workday", ok=False, error="500", cooldown_after=3)
    assert "workday" in store.sources_in_cooldown(db)
    # a success clears the cooldown and the error streak
    store.update_source_health(db, "workday", ok=True, latency_ms=50.0)
    assert "workday" not in store.sources_in_cooldown(db)
    with store.get_conn(db) as conn:
        row = conn.execute(
            "SELECT consecutive_errors, cooldown_until FROM source_health WHERE source_ats='workday'"
        ).fetchone()
    assert row["consecutive_errors"] == 0 and row["cooldown_until"] is None


def test_health_no_cooldown_before_threshold(tmp_path):
    db = tmp_path / "jobs.db"
    store.update_source_health(db, "lever", ok=False, error="timeout", cooldown_after=3)
    store.update_source_health(db, "lever", ok=False, error="timeout", cooldown_after=3)
    assert store.sources_in_cooldown(db) == {}  # 2 < 3, no cooldown yet
