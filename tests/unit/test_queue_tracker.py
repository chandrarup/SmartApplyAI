"""M6 review-queue + application-tracker regression suite.

Covers, with literal assertions:
  - band boundaries (exactly 70 → stretch, exactly 85 → strong, 69 → dropped)
  - nightly tailoring with per-item failure isolation (rule 7)
  - end-to-end walk: match → queue → tailor → approve → release → applied
    (a real versioned PDF is produced and linked when pdflatex is present)
  - dedupe / rejection-history block (rule: warn or block)
  - pacing caps (≤2/company/week, ≤10/day, spacing — rule 11)
  - analytics callback rate (screen+) by band and by company
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from matcher import store as matcher_store  # noqa: E402
from tracker import store as tracker_store  # noqa: E402
from tracker import dedupe as tracker_dedupe  # noqa: E402
from tracker import pacing as tracker_pacing  # noqa: E402
from tracker.config import PacingConfig  # noqa: E402

HAS_PDFLATEX = shutil.which("pdflatex") is not None
PID = "default"


# ── fixtures ──────────────────────────────────────────────────────────────────
@pytest.fixture
def dbs(tmp_path, monkeypatch):
    """Point both stores at throwaway SQLite files for the duration of a test."""
    matches_db = str(tmp_path / "matches.db")
    tracker_db = str(tmp_path / "tracker.db")
    monkeypatch.setenv("MATCHER_DB_PATH", matches_db)
    monkeypatch.setenv("TRACKER_DB_PATH", tracker_db)
    return {"matches": matches_db, "tracker": tracker_db}


@pytest.fixture
def client(dbs):
    return TestClient(main.app)


def _match(external_id, company, title, pct, jd="Some job description."):
    return {
        "job": {
            "source_ats": "greenhouse", "external_id": external_id,
            "company": company, "title": title, "apply_url": f"https://x/{external_id}",
            "description_text": jd,
        },
        "stage1_score": 0.5, "stage2_score": 0.6, "match_pct": pct,
        "fit": {
            "match_pct": pct, "rationale": f"fit for {title}",
            "matched_skills": [{"skill": "python", "evidence_ref": "e1"}],
            "missing_skills": [{"skill": "rust", "evidence_ref": ""}],
            "best_projects": [{"title": "Proj", "why": "relevant"}],
        },
    }


# ── 1. band boundaries (scope item 1) ─────────────────────────────────────────
def test_band_boundaries(dbs):
    fitted = [
        _match("j69", "Below", "Analyst", 69),
        _match("j70", "Floor", "SWE Intern", 70),
        _match("j84", "Mid", "ML Intern", 84),
        _match("j85", "Strong", "AI Intern", 85),
    ]
    counts = matcher_store.gate_and_store(dbs["matches"], PID, fitted, 70, 85)
    assert counts == {"stored": 3, "strong": 1, "stretch": 2}

    q = {i["match_pct"]: i for i in matcher_store.list_queue(dbs["matches"], PID)}
    assert 69 not in q, "match below threshold must not enter the queue"
    assert q[70]["band"] == "stretch"   # exactly 70 → Stretch
    assert q[84]["band"] == "stretch"
    assert q[85]["band"] == "strong"    # exactly 85 → Strong
    # Strong is reviewed first.
    assert matcher_store.list_queue(dbs["matches"], PID)[0]["band"] == "strong"


def test_queue_endpoint_bands_and_fields(client, dbs):
    matcher_store.gate_and_store(
        dbs["matches"], PID,
        [_match("a", "Acme", "SWE Intern", 72), _match("b", "Beta", "ML Intern", 90)],
        70, 85,
    )
    r = client.get("/queue", headers={"X-Profile-ID": PID})
    assert r.status_code == 200
    items = r.json()["items"]
    assert [i["band"] for i in items] == ["strong", "stretch"]  # strong first
    top = items[0]
    assert top["match_pct"] == 90
    assert top["rationale"] == "fit for ML Intern"
    assert top["matched_skills"][0]["skill"] == "python"
    assert top["missing_skills"][0]["skill"] == "rust"
    assert top["tailor_status"] == "pending"
    assert top["review_status"] == "new"


# ── 2. nightly tailoring — per-item failure isolation (rule 7) ─────────────────
def test_nightly_tailor_isolation(dbs, monkeypatch):
    matcher_store.gate_and_store(
        dbs["matches"], PID,
        [_match("ok", "GoodCo", "SWE Intern", 80, jd="normal jd"),
         _match("bad", "BoomCo", "ML Intern", 82, jd="BOOM jd")],
        70, 85,
    )

    async def fake_tailoring(pid, jd_text, role="", company="", **kw):
        if "BOOM" in (jd_text or ""):
            raise RuntimeError("simulated LLM meltdown")
        return {"_edits": [{"section": "summary", "field": "summary",
                            "before": "x", "after": "y", "reason": "r", "status": "accepted"}],
                "tailored_summary": "ok"}

    monkeypatch.setattr(main, "run_tailoring", fake_tailoring)
    counts = asyncio.run(main.tailor_pending_queue(PID, dbs["matches"]))
    assert counts == {"pending": 2, "tailored": 1, "failed": 1}

    by_company = {i["company"]: i for i in matcher_store.list_queue(dbs["matches"], PID)}
    assert by_company["GoodCo"]["tailor_status"] == "tailored"
    assert by_company["GoodCo"]["tailored"]["tailored_summary"] == "ok"
    assert by_company["BoomCo"]["tailor_status"] == "failed"
    assert "simulated LLM meltdown" in by_company["BoomCo"]["tailor_error"]


# ── 3. end-to-end walk: match → queue → tailor → approve → release → applied ───
@pytest.mark.skipif(not HAS_PDFLATEX, reason="pdflatex required for real variant PDF")
def test_end_to_end_walk(client, dbs, monkeypatch):
    matcher_store.gate_and_store(dbs["matches"], PID, [_match("e2e", "Northwind", "ML Intern", 88)], 70, 85)
    match_id = matcher_store.list_queue(dbs["matches"], PID)[0]["id"]

    async def fake_tailoring(pid, jd_text, role="", company="", **kw):
        # Minimal but valid tailored payload; the resume renders mostly from master.
        return {"tailored_summary": "Engineer building production ML systems and data pipelines for teams.",
                "_edits": []}
    monkeypatch.setattr(main, "run_tailoring", fake_tailoring)

    # tailor overnight
    assert client.post("/queue/tailor", headers={"X-Profile-ID": PID}).json()["tailored"] == 1
    assert matcher_store.get_queue_item(dbs["matches"], PID, match_id)["tailor_status"] == "tailored"

    # approve → real versioned PDF, stays in queue as customized (no tracker row yet)
    r = client.post(f"/queue/{match_id}/approve", headers={"X-Profile-ID": PID}, json={})
    assert r.status_code == 200, r.text
    body = r.json()
    variant_id = body["variant_id"]
    assert variant_id
    assert body["review_status"] == "customized"
    assert "application" not in body

    import resume_versions
    pdf_path = resume_versions.get_variant_path(main._profile_dir(PID), variant_id, "tailored.pdf")
    assert pdf_path and os.path.exists(pdf_path)
    import shutil
    shutil.rmtree(os.path.dirname(pdf_path), ignore_errors=True)

    item = matcher_store.get_queue_item(dbs["matches"], PID, match_id)
    assert item["review_status"] == "customized"
    assert item["resume_variant_id"] == variant_id

    # tracker/match resolves customized queue item
    tm = client.get("/tracker/match", params={"host": "x", "url": "https://x/northwind", "company": "Northwind"},
                    headers={"X-Profile-ID": PID}).json()
    assert tm["match"] is not None
    assert tm["match"]["queue_match_id"] == match_id

    # mark-applied → tracker row with applied status
    r = client.post(f"/queue/{match_id}/mark-applied", headers={"X-Profile-ID": PID}, json={})
    assert r.status_code == 200, r.text
    app_row = r.json()["application"]
    assert app_row["status"] == "applied"
    assert app_row["resume_variant_id"] == variant_id
    assert app_row["band"] == "strong"
    assert matcher_store.get_queue_item(dbs["matches"], PID, match_id)["review_status"] == "applied"

    hist = tracker_store.status_history(PID, app_row["id"])
    transitions = [(h["from_status"], h["to_status"]) for h in hist]
    assert (None, "applied") in transitions


def test_single_item_tailor_only(client, dbs, monkeypatch):
    matcher_store.gate_and_store(
        dbs["matches"], PID,
        [_match("a", "Alpha", "SWE", 80), _match("b", "Beta", "ML", 82)],
        70, 85,
    )
    items = matcher_store.list_queue(dbs["matches"], PID)
    mid_a = next(i["id"] for i in items if i["company"] == "Alpha")
    calls = []

    async def fake_tailoring(pid, jd_text, role="", company="", **kw):
        calls.append(company)
        return {"tailored_summary": f"tailored for {company}", "_edits": []}

    monkeypatch.setattr(main, "run_tailoring", fake_tailoring)
    r = client.post(f"/queue/{mid_a}/tailor", headers={"X-Profile-ID": PID})
    assert r.status_code == 200
    assert calls == ["Alpha"]
    by_co = {i["company"]: i for i in matcher_store.list_queue(dbs["matches"], PID)}
    assert by_co["Alpha"]["tailor_status"] == "tailored"
    assert by_co["Beta"]["tailor_status"] == "pending"


# ── 4. dedupe / rejection-history block ───────────────────────────────────────
def test_dedupe_block_active_and_rejection(dbs):
    tracker_store.create_application(PID, {"company": "Acme Inc", "role": "Software Engineer Intern",
                                           "status": "applied", "date_applied": datetime.now(timezone.utc).isoformat()})
    # abbreviation of an active application → blocked
    v = tracker_dedupe.check(PID, "Acme", "SWE Intern")
    assert v["blocked"] and v["kind"] == "duplicate"
    # different company → allowed
    assert tracker_dedupe.check(PID, "Globex", "SWE Intern")["blocked"] is False

    # recent rejection at a company → blocked within window, allowed after
    rej = tracker_store.create_application(PID, {"company": "Zed", "role": "Data Scientist", "status": "applied"})
    tracker_store.update_status(PID, rej["id"], "rejected")
    assert tracker_dedupe.check(PID, "Zed", "Data Scientist", window_days=90)["blocked"] is True
    future = datetime.now(timezone.utc) + timedelta(days=120)
    assert tracker_dedupe.check(PID, "Zed", "Data Scientist", window_days=90, now=future)["blocked"] is False


def test_approve_blocked_by_dedupe_then_force(client, dbs, monkeypatch):
    # existing active application to the same company/role
    tracker_store.create_application(PID, {"company": "Hooli", "role": "Software Engineer Intern", "status": "applied"})
    matcher_store.gate_and_store(dbs["matches"], PID, [_match("dup", "Hooli", "SWE Intern", 80)], 70, 85)
    mid = matcher_store.list_queue(dbs["matches"], PID)[0]["id"]
    matcher_store.set_tailoring(dbs["matches"], mid, status="tailored", tailored={"_edits": []})

    r = client.post(f"/queue/{mid}/approve", headers={"X-Profile-ID": PID}, json={})
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "dedupe_block"

    # force with a pre-supplied variant id (bypass PDF gen) → customized in queue
    r2 = client.post(f"/queue/{mid}/approve", headers={"X-Profile-ID": PID},
                     json={"force": True, "variant_id": "manual-variant-1"})
    assert r2.status_code == 200
    assert r2.json()["variant_id"] == "manual-variant-1"
    assert r2.json()["review_status"] == "customized"
    item = matcher_store.get_queue_item(dbs["matches"], PID, mid)
    assert item["resume_variant_id"] == "manual-variant-1"
    assert len(tracker_store.list_applications(PID)) == 1  # only the pre-existing Hooli row


# ── 5. pacing caps (rule 11) ──────────────────────────────────────────────────
def test_pacing_week_and_day_caps(dbs):
    cfg = PacingConfig(per_company_per_week=2, per_day=10, min_spacing_minutes=45)
    for i in range(3):  # 3 approvals, one company
        tracker_store.create_application(PID, {"company": "Wave", "role": f"Role {i}", "status": "approved"})
    res = tracker_pacing.release_ready(PID, cfg=cfg)
    assert len(res["released"]) == 2
    assert "weekly cap for Wave" in res["held"][0]["reason"]


def test_pacing_daily_cap(dbs):
    cfg = PacingConfig(per_company_per_week=99, per_day=10, min_spacing_minutes=45)
    for i in range(12):  # distinct companies so only the daily cap bites
        tracker_store.create_application(PID, {"company": f"Co{i}", "role": "Intern", "status": "approved"})
    res = tracker_pacing.release_ready(PID, cfg=cfg)
    assert len(res["released"]) == 10
    assert len(res["held"]) == 2
    assert "daily cap" in res["held"][0]["reason"]


# ── 6. analytics: callback rate by band and company (screen+) ─────────────────
def test_analytics_callback_rate(client, dbs):
    def mk(company, band, status):
        a = tracker_store.create_application(PID, {"company": company, "role": "Intern",
                                                   "band": band, "status": "applied"})
        if status != "applied":
            tracker_store.update_status(PID, a["id"], status)
        return a

    mk("A", "strong", "screen")     # callback
    mk("A", "strong", "interview")  # callback
    mk("A", "strong", "rejected")   # applied, no callback
    mk("B", "stretch", "confirmed")  # confirmed does NOT count as callback
    mk("B", "stretch", "applied")

    an = client.get("/tracker/analytics", headers={"X-Profile-ID": PID}).json()
    by_band = {b["band"]: b for b in an["by_band"]}
    assert by_band["strong"]["applied"] == 3 and by_band["strong"]["callbacks"] == 2
    assert by_band["strong"]["callback_rate"] == pytest.approx(2 / 3, abs=1e-4)
    assert by_band["stretch"]["callbacks"] == 0
    by_company = {c["company"]: c for c in an["by_company"]}
    assert by_company["A"]["callback_rate"] == pytest.approx(2 / 3, abs=1e-4)
    assert an["overall"]["applied"] == 5 and an["overall"]["callbacks"] == 2
