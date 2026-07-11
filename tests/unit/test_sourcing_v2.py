"""Scraper / sourcing-v2 unit tests — no live network except optional probes."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from scraper.normalize import lever_created_at_to_iso, normalize_lever, normalize_job  # noqa: E402
from scraper.liveness import classify_liveness  # noqa: E402
from scraper.providers.registry import load_providers, resolve_provider  # noqa: E402
from scraper.providers.base import CompanyEntry  # noqa: E402
from scraper.providers.trackers import parse_markdown_tables, TrackerSchemaError  # noqa: E402
from scraper.providers import workday as workday_mod  # noqa: E402
from scraper.bootstrap_registry import classify_url  # noqa: E402


# ── Lever epoch milliseconds regression ──────────────────────────────
def test_lever_created_at_is_milliseconds_not_seconds():
    # 1710000000000 ms ≈ 2024-03-09 — NOT year 56000
    iso = lever_created_at_to_iso(1710000000000)
    assert iso is not None
    assert iso.startswith("2024-"), iso
    # Seconds-scale values must not be divided again
    iso_s = lever_created_at_to_iso(1710000000)
    assert iso_s is not None and iso_s.startswith("2024-")


def test_normalize_lever_uses_ms_conversion():
    job = normalize_lever(
        "netflix",
        {
            "id": "abc",
            "text": "ML Intern",
            "categories": {"location": "Remote US"},
            "descriptionPlain": "Python internship remote United States",
            "createdAt": 1710000000000,
            "hostedUrl": "https://jobs.lever.co/netflix/abc",
        },
    )
    assert str(job["posted_at"]).startswith("2024-")


# ── Registry ─────────────────────────────────────────────────────────
def test_registry_loads_expected_providers():
    providers = load_providers()
    for pid in (
        "greenhouse", "lever", "ashby", "workday",
        "smartrecruiters", "workable", "teamtailor", "personio", "tracker",
    ):
        assert pid in providers, pid


def test_resolve_explicit_ats_and_careers_url_detect():
    providers = load_providers()
    p = resolve_provider(CompanyEntry(ats="lever", token="netflix"), providers)
    assert p and p.id == "lever"
    p2 = resolve_provider(
        CompanyEntry(careers_url="https://jobs.lever.co/netflix"), providers
    )
    assert p2 and p2.id == "lever"
    p3 = resolve_provider(
        CompanyEntry(careers_url="https://acme.wd5.myworkdayjobs.com/Careers"),
        providers,
    )
    assert p3 and p3.id == "workday"


def test_workday_detect_builds_cxs_url():
    api = workday_mod.detect(
        CompanyEntry(careers_url="https://23andme.wd5.myworkdayjobs.com/23andMe")
    )
    assert api == "https://23andme.wd5.myworkdayjobs.com/wday/cxs/23andme/23andMe/jobs"


# ── Bootstrap URL classification ─────────────────────────────────────
def test_bootstrap_classify_url_shapes():
    assert classify_url("https://boards.greenhouse.io/stripe/jobs/123")["ats"] == "greenhouse"
    assert classify_url("https://jobs.lever.co/netflix/abc")["token"] == "netflix"
    assert classify_url("https://jobs.ashbyhq.com/notion")["ats"] == "ashby"
    assert classify_url("https://acme.wd1.myworkdayjobs.com/Careers")["ats"] == "workday"


# ── Tracker markdown parsing ─────────────────────────────────────────
SAMPLE_MD = """
# Internships

| Company | Role | Location | Application |
|---------|------|----------|-------------|
| Acme | ML Intern | Remote | [Apply](https://boards.greenhouse.io/acme/jobs/1) |
| Beta | AI Co-op | NYC | https://jobs.lever.co/beta/xyz |
"""


def test_tracker_parse_happy_path():
    rows = parse_markdown_tables(SAMPLE_MD, source_url="test.md")
    assert len(rows) == 2
    assert rows[0]["company"] == "Acme"
    assert "greenhouse" in rows[0]["apply_url"]


def test_tracker_schema_fail_loud_when_no_job_table():
    with pytest.raises(TrackerSchemaError):
        parse_markdown_tables("| A | B |\n|---|---|\n| 1 | 2 |\n", source_url="bad.md")


# ── Liveness three-way ───────────────────────────────────────────────
def test_liveness_expired_404():
    r = classify_liveness(status=404, requested_url="https://x", final_url="https://x", body_text="")
    assert r["result"] == "expired"


def test_liveness_bot_challenge_is_uncertain_never_expired():
    r = classify_liveness(
        status=200,
        requested_url="https://x/job/12345",
        final_url="https://x/job/12345",
        body_text="Just a moment... Checking your browser before accessing. cf-ray: abc",
    )
    assert r["result"] == "uncertain"
    assert r["code"] == "bot_challenge"


def test_liveness_hard_expired_body():
    r = classify_liveness(
        status=200,
        requested_url="https://x",
        final_url="https://x",
        body_text="Sorry, this job is no longer available. Position has been filled.",
    )
    assert r["result"] == "expired"


def test_liveness_apply_is_live():
    body = "About the role. " * 40 + " Click Apply to submit application today."
    r = classify_liveness(status=200, requested_url="https://x", final_url="https://x", body_text=body)
    assert r["result"] == "live"


def test_normalize_job_tags_matched_searches():
    job = normalize_job(
        "greenhouse",
        "acme",
        {
            "id": 1,
            "title": "Machine Learning Intern",
            "content": "<p>Remote United States internship</p>",
            "location": {"name": "Remote US"},
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
        },
    )
    assert "machine learning intern" in job["matched_searches"]
