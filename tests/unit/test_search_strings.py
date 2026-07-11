"""Search-string tagging + prefilter bypass tests."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from matcher.prefilter import prefilter_jobs  # noqa: E402
from scraper import searches as search_mod  # noqa: E402
from scraper import store as scraper_store  # noqa: E402


def test_match_searches_plain_and_regex():
    hits = search_mod.match_searches(
        "Machine Learning Intern",
        "Build LLM research prototypes and biomedical AI tools.",
    )
    assert "machine learning intern" in hits
    assert "LLM research intern" in hits
    assert "biomedical AI" in hits


def test_spatial_proteomics_tags():
    hits = search_mod.match_searches(
        "Research Associate",
        "We apply spatial proteomics to tissue atlases.",
    )
    assert hits == ["spatial proteomics"]


def test_search_bypass_lets_non_intern_through(tmp_path):
    db = tmp_path / "jobs.db"
    with scraper_store.get_conn(db) as conn:
        conn.execute(
            """
            INSERT INTO jobs (
                source_ats, company, external_id, title, location, remote_flag,
                is_internship, location_match, sponsorship_knockout, department,
                description_text, apply_url, posted_at, updated_at, raw_json,
                first_seen, last_seen, status, matched_searches
            ) VALUES (?, ?, ?, ?, ?, 0, 0, 1, 0, '', ?, '', NULL, NULL, '{}',
                      '2026-01-01', '2026-01-01', 'active', ?)
            """,
            (
                "greenhouse", "Acme", "1", "ML Engineer", "Remote US",
                "Looking for machine learning engineers building LLM systems.",
                json.dumps(["machine learning intern"]),
            ),
        )
        # Control: non-intern, no search tags → should be filtered in internship mode
        conn.execute(
            """
            INSERT INTO jobs (
                source_ats, company, external_id, title, location, remote_flag,
                is_internship, location_match, sponsorship_knockout, department,
                description_text, apply_url, posted_at, updated_at, raw_json,
                first_seen, last_seen, status, matched_searches
            ) VALUES (?, ?, ?, ?, ?, 0, 0, 1, 0, '', ?, '', NULL, NULL, '{}',
                      '2026-01-01', '2026-01-01', 'active', '[]')
            """,
            (
                "greenhouse", "Acme", "2", "Backend Engineer", "Remote US",
                "Java Spring microservices. No ML.",
            ),
        )

    filters = Path(__file__).resolve().parents[2] / "backend" / "scraper" / "filters.yaml"
    survivors = prefilter_jobs(db, role_mode="internship", filters_path=filters,
                               search_bypass_internship=True)
    ids = {s["external_id"] for s in survivors}
    assert "1" in ids  # search-tagged non-intern bypassed
    assert "2" not in ids


def test_search_bypass_disabled_keeps_internship_gate(tmp_path):
    db = tmp_path / "jobs.db"
    with scraper_store.get_conn(db) as conn:
        conn.execute(
            """
            INSERT INTO jobs (
                source_ats, company, external_id, title, location, remote_flag,
                is_internship, location_match, sponsorship_knockout, department,
                description_text, apply_url, posted_at, updated_at, raw_json,
                first_seen, last_seen, status, matched_searches
            ) VALUES (?, ?, ?, ?, ?, 0, 0, 1, 0, '', ?, '', NULL, NULL, '{}',
                      '2026-01-01', '2026-01-01', 'active', ?)
            """,
            (
                "greenhouse", "Acme", "1", "ML Engineer", "Remote US",
                "machine learning",
                json.dumps(["machine learning intern"]),
            ),
        )
    filters = Path(__file__).resolve().parents[2] / "backend" / "scraper" / "filters.yaml"
    survivors = prefilter_jobs(db, role_mode="internship", filters_path=filters,
                               search_bypass_internship=False)
    assert survivors == []
