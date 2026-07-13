"""Tests for sourcing-v3 orchestration wiring in execute_run (§2.3).

Providers are faked via a monkeypatched registry; databases are tmp_path. The
run loop's real logic (health updates, run rows, cooldown skip, coverage-drop
detection) is exercised end-to-end without network or real providers.
"""

from __future__ import annotations

import requests
import pytest

from backend.scraper import run as run_mod
from backend.scraper import store
from backend.scraper.providers.base import CompanyEntry


class FakeProvider:
    def __init__(self, pid, jobs=None, exc=None):
        self.id = pid
        self._jobs = jobs or []
        self._exc = exc

    def fetch(self, entry):
        if self._exc:
            raise self._exc
        return self._jobs


def _raw_job(ext, title="ML Intern"):
    return {"id": ext, "title": title, "location": "Remote",
            "absolute_url": f"https://apply/{ext}", "content": "ML role", "updated_at": None}


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """Point execute_run at a tmp db + companies file, and stub the registry."""
    db = tmp_path / "jobs.db"
    companies = tmp_path / "companies.yaml"
    companies.write_text(
        "companies:\n"
        "  - {ats: greenhouse, token: acme}\n"
        "  - {ats: lever, token: beta}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(run_mod, "SLEEP_BETWEEN_CALLS_SECONDS", 0.0)

    # Stub normalize_job to a deterministic pass-through — provider-specific
    # normalizers each want a different raw shape, and this test targets the
    # orchestration loop (health/runs/cooldown), not normalization internals.
    def fake_normalize(pid, token, job):
        return {
            "external_id": str(job["id"]),
            "company": token or pid,
            "title": job.get("title", ""),
            "location": job.get("location", "Remote"),
            "remote_flag": 1,
            "is_internship": 1 if "intern" in job.get("title", "").lower() else 0,
            "location_match": 1,
            "sponsorship_knockout": 0,
            "department": "",
            "description_text": job.get("content", ""),
            "apply_url": job.get("absolute_url", ""),
            "posted_at": None,
            "updated_at": None,
            "raw_json": "{}",
            "matched_searches": [],
        }

    monkeypatch.setattr(run_mod, "normalize_job", fake_normalize)
    return db, companies


def _patch_providers(monkeypatch, mapping):
    """mapping: entry.ats -> FakeProvider."""
    monkeypatch.setattr(run_mod, "load_providers", lambda: {})

    def fake_resolve(entry, providers):
        return mapping.get(entry.ats)

    monkeypatch.setattr(run_mod, "resolve_provider", fake_resolve)


def test_run_records_row_and_health(monkeypatch, wired):
    db, companies = wired
    _patch_providers(monkeypatch, {
        "greenhouse": FakeProvider("greenhouse", jobs=[_raw_job("g1"), _raw_job("g2")]),
        "lever": FakeProvider("lever", jobs=[_raw_job("l1")]),
        "tracker": FakeProvider("tracker", jobs=[]),
    })
    totals = run_mod.execute_run(companies_path=companies, mode="on_demand", db_path=db)
    assert totals["fetched"] == 3

    runs = store.recent_runs(db, limit=5)
    assert len(runs) == 1 and runs[0]["mode"] == "on_demand"
    assert runs[0]["jobs_fetched"] == 3 and runs[0]["finished_at"] is not None

    # healthy sources recorded with a success + latency EMA seed
    with store.get_conn(db) as conn:
        rows = {r["source_ats"]: r for r in conn.execute("SELECT * FROM source_health").fetchall()}
    assert "greenhouse" in rows and rows["greenhouse"]["consecutive_errors"] == 0
    assert rows["greenhouse"]["last_success_at"] is not None


def test_error_source_increments_health(monkeypatch, wired):
    db, companies = wired
    _patch_providers(monkeypatch, {
        "greenhouse": FakeProvider("greenhouse", exc=requests.exceptions.ConnectionError("down")),
        "lever": FakeProvider("lever", jobs=[_raw_job("l1")]),
        "tracker": FakeProvider("tracker", jobs=[]),
    })
    run_mod.execute_run(companies_path=companies, mode="cli", db_path=db)
    with store.get_conn(db) as conn:
        gh = conn.execute(
            "SELECT * FROM source_health WHERE source_ats='greenhouse'").fetchone()
    assert gh["consecutive_errors"] == 1 and gh["last_error_at"] is not None


def test_cooldown_source_is_skipped(monkeypatch, wired):
    db, companies = wired
    # Force greenhouse into cooldown up front.
    for _ in range(3):
        store.update_source_health(db, "greenhouse", ok=False, error="500", cooldown_after=3)
    assert "greenhouse" in store.sources_in_cooldown(db)

    gh_provider = FakeProvider("greenhouse", jobs=[_raw_job("g1")])
    calls = {"gh": 0}
    orig_fetch = gh_provider.fetch

    def counting_fetch(entry):
        calls["gh"] += 1
        return orig_fetch(entry)

    gh_provider.fetch = counting_fetch
    _patch_providers(monkeypatch, {
        "greenhouse": gh_provider,
        "lever": FakeProvider("lever", jobs=[_raw_job("l1")]),
        "tracker": FakeProvider("tracker", jobs=[]),
    })
    totals = run_mod.execute_run(companies_path=companies, mode="on_demand", db_path=db)
    assert calls["gh"] == 0  # greenhouse never fetched while cooling down
    assert totals["by_provider"]["greenhouse"]["skipped_cooldown"] == 1
    assert totals["fetched"] == 1  # only lever ran


def test_coverage_drop_anomaly(monkeypatch, wired):
    db, companies = wired
    # Seed a prior run with a strong greenhouse baseline.
    rid = store.record_run_start(db, "scheduled")
    store.record_run_end(db, rid, totals={"fetched": 100},
                        provider_stats={"greenhouse": {"fetched": 100}}, anomalies=[])
    # Now greenhouse returns almost nothing.
    _patch_providers(monkeypatch, {
        "greenhouse": FakeProvider("greenhouse", jobs=[_raw_job("g1")]),
        "lever": FakeProvider("lever", jobs=[_raw_job("l1")]),
        "tracker": FakeProvider("tracker", jobs=[]),
    })
    totals = run_mod.execute_run(companies_path=companies, mode="on_demand", db_path=db)
    anomalies = totals["anomalies"]
    assert any(a["source"] == "greenhouse" and a["type"] == "coverage_drop" for a in anomalies)


def test_partial_failure_does_not_abort_run(monkeypatch, wired):
    db, companies = wired
    _patch_providers(monkeypatch, {
        "greenhouse": FakeProvider("greenhouse", exc=ValueError("boom")),
        "lever": FakeProvider("lever", jobs=[_raw_job("l1"), _raw_job("l2")]),
        "tracker": FakeProvider("tracker", jobs=[]),
    })
    totals = run_mod.execute_run(companies_path=companies, mode="on_demand", db_path=db)
    assert totals["fetched"] == 2  # lever ingested despite greenhouse failing
    assert totals["by_provider"]["greenhouse"]["errors"] == 1
