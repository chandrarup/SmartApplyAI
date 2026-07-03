"""Tracker page-matching + approved-answer persistence (feature: autofill-approved).

Covers, with literal assertions:
  - best_match scoring: exact URL, company-in-host (Workday subdomain),
    company-in-path (Greenhouse), company-name agreement, and no-match
  - GET /tracker/match returns only ready_to_apply items with their approved package
  - PATCH /applications/{id} merges answers (the pause-and-ask "remember" loop)
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from tracker import match as tracker_match  # noqa: E402
from tracker import store as tracker_store  # noqa: E402
from tracker.config import STATUS_APPROVED, STATUS_READY  # noqa: E402

PID = "default"


@pytest.fixture
def dbs(tmp_path, monkeypatch):
    tracker_db = str(tmp_path / "tracker.db")
    monkeypatch.setenv("TRACKER_DB_PATH", tracker_db)
    return {"tracker": tracker_db}


@pytest.fixture
def client(dbs):
    return TestClient(main.app)


# ── 1. best_match scoring (pure) ──────────────────────────────────────────────
def test_best_match_exact_url_wins():
    items = [
        {"id": "a", "company": "Acme", "url": "https://boards.greenhouse.io/acme/jobs/1"},
        {"id": "b", "company": "Beta", "url": "https://boards.greenhouse.io/beta/jobs/9"},
    ]
    m = tracker_match.best_match(
        items, host="boards.greenhouse.io",
        url="https://boards.greenhouse.io/acme/jobs/1?utm=x",
    )
    assert m["id"] == "a"  # query string ignored; exact host+path match


def test_best_match_company_in_greenhouse_path():
    # Shared host; company only distinguishable via the path slug.
    items = [
        {"id": "a", "company": "Acme Corp", "url": ""},
        {"id": "b", "company": "Beta Inc", "url": ""},
    ]
    m = tracker_match.best_match(
        items, host="boards.greenhouse.io",
        url="https://boards.greenhouse.io/acme/jobs/55",
    )
    assert m["id"] == "a"


def test_best_match_company_in_workday_subdomain():
    items = [{"id": "a", "company": "Acme", "url": ""}]
    m = tracker_match.best_match(
        items, host="acme.wd1.myworkdayjobs.com",
        url="https://acme.wd1.myworkdayjobs.com/careers/job/123",
    )
    assert m and m["id"] == "a"


def test_best_match_company_name_agreement():
    items = [{"id": "a", "company": "Acme Technologies", "url": ""}]
    m = tracker_match.best_match(items, host="jobs.example.com", company="Acme")
    assert m and m["id"] == "a"


def test_best_match_none_when_no_signal():
    items = [{"id": "a", "company": "Acme", "url": "https://boards.greenhouse.io/acme/jobs/1"}]
    # Different company, unrelated host, no company hint → no match (don't guess).
    assert tracker_match.best_match(
        items, host="jobs.other.com", url="https://jobs.other.com/x", company="Zenith"
    ) is None


def test_shared_host_without_company_does_not_match():
    # Two companies on the same ATS host; page gives no company signal → ambiguous → None.
    items = [
        {"id": "a", "company": "Acme", "url": "https://boards.greenhouse.io/acme/jobs/1"},
        {"id": "b", "company": "Beta", "url": "https://boards.greenhouse.io/beta/jobs/2"},
    ]
    assert tracker_match.best_match(
        items, host="boards.greenhouse.io", url="https://boards.greenhouse.io/"
    ) is None


# ── 2. GET /tracker/match end-to-end ──────────────────────────────────────────
def test_tracker_match_endpoint_only_ready(client, dbs):
    # An approved-but-not-yet-released item must NOT be offered for autofill.
    tracker_store.create_application(PID, {
        "company": "Acme", "role": "ML Engineer", "status": STATUS_APPROVED,
        "url": "https://boards.greenhouse.io/acme/jobs/1",
        "resume_variant_id": "acme_v1", "answers": {"Why us?": "Because."},
    })
    r = client.get("/tracker/match", params={
        "host": "boards.greenhouse.io",
        "url": "https://boards.greenhouse.io/acme/jobs/1",
    })
    assert r.status_code == 200
    assert r.json()["match"] is None  # approved != ready_to_apply

    # Promote to ready → now it matches and carries the approved package.
    apps = tracker_store.list_applications(PID)
    tracker_store.update_status(PID, apps[0]["id"], STATUS_READY)
    r = client.get("/tracker/match", params={
        "host": "boards.greenhouse.io",
        "url": "https://boards.greenhouse.io/acme/jobs/1",
    })
    m = r.json()["match"]
    assert m is not None
    assert m["company"] == "Acme"
    assert m["resume_variant_id"] == "acme_v1"
    assert m["answers"] == {"Why us?": "Because."}


# ── 3. PATCH /applications merges answers (remember loop) ──────────────────────
def test_patch_merges_answers(client, dbs):
    app = tracker_store.create_application(PID, {
        "company": "Acme", "role": "ML Engineer", "status": STATUS_READY,
        "answers": {"Q1": "a1"},
    })
    r = client.patch(f"/applications/{app['id']}", json={"answers": {"Q1": "a1", "Q2": "a2"}})
    assert r.status_code == 200
    assert r.json()["answers"] == {"Q1": "a1", "Q2": "a2"}
    # Persisted, not just echoed.
    assert tracker_store.get_application(PID, app["id"])["answers"] == {"Q1": "a1", "Q2": "a2"}
