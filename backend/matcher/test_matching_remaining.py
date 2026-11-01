from __future__ import annotations

from backend.matcher.evidence import ground_fit, retrieve_evidence
from backend.matcher.explanations import add_fit_explanation
from backend.matcher.ingest import ingest_jobs
from backend.matcher.telemetry import append_event, summarize


def test_ingest_jobs_normalizes_scraper_aliases():
    rows = ingest_jobs([{
        "source": "greenhouse", "id": 42, "name": " ML  Intern ",
        "description": "<p>Python</p>", "locationsText": " Remote ",
    }])
    assert rows[0]["source_ats"] == "greenhouse"
    assert rows[0]["external_id"] == "42"
    assert rows[0]["title"] == "ML Intern"
    assert rows[0]["description_text"] == "<p>Python</p>"


def test_grounding_flags_unbacked_claims():
    fit = {"matched_skills": [
        {"skill": "Python", "evidence_ref": "skill:python"},
        {"skill": "Kubernetes", "evidence_ref": ""},
    ]}
    grounded = ground_fit(fit, [{"evidence_ref": "skill:python", "text": "Built Python APIs"}])
    assert [x["skill"] for x in grounded["matched_skills"]] == ["Python"]
    assert grounded["needs_your_call"] == ["Kubernetes"]
    assert add_fit_explanation(grounded)["explanation"]["evidence_count"] == 1


def test_evidence_retrieval_and_telemetry(tmp_path):
    hits = retrieve_evidence("p1", "Python", search=lambda *a, **k: [{"text": "Python"}])
    assert hits[0]["text"] == "Python"
    path = tmp_path / "events.jsonl"
    append_event(path, {"event": "accepted"})
    assert summarize(path)["events"] == {"accepted": 1}
