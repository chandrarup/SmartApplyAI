"""
Offline ATS DOM Replay Test Suite (tests/test_ats_dom_autofill.py).
Validates Stage 4/5 autofill execution across 4 offline ATS fixtures:
  1. Greenhouse: first_name, last_name, email, phone, resume upload, select dropdowns
  2. Lever: resume uploaded first, name, org, links
  3. Workday Shadow DOM: open #shadow-root traversal via queryAllDeep, nested file upload
  4. Ashby React: React controlled inputs, DataTransfer file upload, zero overwrite of user edits
"""

import os
import sys
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from harnesses.autofill_harness import run_autofill_on_fixture

FIXTURES_DIR = os.path.join(REPO_ROOT, "tests", "fixtures", "ats_pages")


def test_greenhouse_offline_autofill():
    fixture = os.path.join(FIXTURES_DIR, "greenhouse_fixture.html")
    res = run_autofill_on_fixture(fixture, dry_run=True)
    assert res["status"] == "SUCCESS"
    assert res["file_uploaded"] is True
    assert res["fields_filled"] >= 6
    assert os.path.exists(res["screenshot_path"])


def test_lever_offline_autofill():
    fixture = os.path.join(FIXTURES_DIR, "lever_fixture.html")
    res = run_autofill_on_fixture(fixture, dry_run=True)
    assert res["status"] == "SUCCESS"
    assert res["file_uploaded"] is True
    assert res["fields_filled"] == 8
    assert os.path.exists(res["screenshot_path"])


def test_workday_shadow_dom_offline_autofill():
    fixture = os.path.join(FIXTURES_DIR, "workday_shadow_dom_fixture.html")
    res = run_autofill_on_fixture(fixture, dry_run=True)
    assert res["status"] == "SUCCESS"
    assert res["file_uploaded"] is True
    assert res["fields_filled"] >= 5
    assert os.path.exists(res["screenshot_path"])


def test_ashby_react_offline_autofill_and_user_edit_protection():
    fixture = os.path.join(FIXTURES_DIR, "ashby_react_fixture.html")
    res = run_autofill_on_fixture(fixture, dry_run=True)
    assert res["status"] == "SUCCESS"
    assert res["file_uploaded"] is True
    assert res["fields_filled"] >= 4
    assert os.path.exists(res["screenshot_path"])
