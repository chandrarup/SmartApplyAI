"""
Pipeline API & Triage Dashboard Routes for SmartApplyAI v2 (backend/dashboard_api.py).
Provides REST endpoints and an interactive Web UI for:
  - Viewing scored "Must Apply" jobs (>= 75% match, eligibility verified)
  - Inspecting tailored 1-page resumes and per-entry skills & tools
  - Viewing the 3 outreach drafts (CEO Delegation, Hiring Manager SME, HR)
  - Downloading the 3-tab SmartApply_Master_Pipeline.xlsx workbook
  - Serving Base64 PDF blobs for Chrome Extension & Playwright execution
"""

import base64
import json
import os
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from backend.db import (
    DEFAULT_DB_PATH,
    get_db_connection,
    get_job_by_id,
    get_latest_run_for_job,
    get_outreach_drafts_for_job,
    get_tailored_resume_for_job,
    init_db,
    list_jobs,
    update_job_status,
)
from backend.excel_sync import DEFAULT_EXCEL_PATH, sync_db_to_excel
from harnesses.scraper_harness import harvest_jobs
from harnesses.scorer_harness import score_all_jobs

router = APIRouter(tags=["Pipeline & Triage"])


@router.get("/api/pipeline/jobs")
def api_list_pipeline_jobs(
    status: Optional[str] = Query(None, description="Filter by pipeline_status"),
    min_score: Optional[int] = Query(None, description="Minimum match_score"),
    eligible_only: bool = Query(False, description="Filter to eligible only"),
    limit: Optional[int] = Query(50, description="Max jobs to return"),
):
    jobs = list_jobs(status=status, min_score=min_score, eligible_only=eligible_only, limit=limit)
    # Parse json strings into python lists for clean frontend consumption
    for j in jobs:
        j["extracted_jd_skills"] = json.loads(j.get("extracted_jd_skills_json") or "[]")
        j["matched_skills"] = json.loads(j.get("matched_skills_json") or "[]")
        j["missing_gaps"] = json.loads(j.get("missing_gaps_json") or "[]")
    return {"jobs": jobs, "total": len(jobs)}


@router.get("/api/pipeline/jobs/{job_id}")
def api_get_job_details(job_id: int):
    job = get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    job["extracted_jd_skills"] = json.loads(job.get("extracted_jd_skills_json") or "[]")
    job["matched_skills"] = json.loads(job.get("matched_skills_json") or "[]")
    job["missing_gaps"] = json.loads(job.get("missing_gaps_json") or "[]")

    resume = get_tailored_resume_for_job(job_id)
    if resume:
        resume["selected_projects"] = json.loads(resume.get("selected_projects_json") or "[]")
        resume["entry_skills_map"] = json.loads(resume.get("entry_skills_map_json") or "{}")

    outreach = get_outreach_drafts_for_job(job_id)
    latest_run = get_latest_run_for_job(job_id)

    return {
        "job": job,
        "tailored_resume": resume,
        "outreach_drafts": outreach,
        "latest_run": latest_run,
    }


@router.post("/api/pipeline/harvest")
def api_harvest_jobs(source: str = "all", limit: int = 15):
    harvested = harvest_jobs(source=source, limit=limit)
    scored = score_all_jobs()
    excel_path = sync_db_to_excel()
    return {
        "success": True,
        "harvested_count": len(harvested),
        "total_scored": len(scored),
        "excel_path": excel_path,
    }


@router.post("/api/pipeline/score")
def api_score_jobs(job_id: Optional[int] = None):
    scored = score_all_jobs(job_id=job_id)
    excel_path = sync_db_to_excel()
    return {
        "success": True,
        "scored_count": len(scored),
        "excel_path": excel_path,
    }


@router.get("/api/pipeline/export-excel")
def api_export_excel():
    excel_path = sync_db_to_excel()
    if not os.path.exists(excel_path):
        raise HTTPException(status_code=500, detail="Excel export failed to generate.")
    return FileResponse(
        excel_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="SmartApply_Master_Pipeline.xlsx",
    )


@router.get("/api/jobs/{job_id}/pdf-base64")
def api_get_pdf_base64(job_id: int):
    resume = get_tailored_resume_for_job(job_id)
    if not resume or not resume.get("pdf_output_path"):
        raise HTTPException(status_code=404, detail="No tailored PDF exists for this job. Tailor resume first.")
    pdf_path = resume["pdf_output_path"]
    if not os.path.exists(pdf_path):
        raise HTTPException(status_code=404, detail=f"PDF file not found at: {pdf_path}")
    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()
    b64 = base64.b64encode(pdf_bytes).decode("utf-8")
    return {
        "job_id": job_id,
        "filename": os.path.basename(pdf_path),
        "pdf_base64": b64,
        "size_bytes": len(pdf_bytes),
    }


@router.get("/dashboard/pipeline", response_class=HTMLResponse)
@router.get("/triage", response_class=HTMLResponse)
def triage_dashboard_html():
    """Renders the Stage 1 Triage & Execution Dashboard."""
    return HTMLResponse(
        """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>SmartApplyAI v2 — Triage & Execution Dashboard</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
    :root {
      --primary: #2563eb; --primary-dark: #1d4ed8; --bg: #f8fafc; --surface: #ffffff;
      --border: #e2e8f0; --text: #0f172a; --text-muted: #64748b;
      --success: #16a34a; --warning: #d97706; --danger: #dc2626;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Inter', sans-serif; }
    body { background: var(--bg); color: var(--text); padding: 24px; }
    .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid var(--border); }
    .header h1 { font-size: 22px; font-weight: 700; color: #1e293b; }
    .header .subtitle { font-size: 13px; color: var(--text-muted); margin-top: 4px; }
    .btn { background: var(--primary); color: #fff; padding: 8px 16px; border-radius: 6px; border: none; font-weight: 600; cursor: pointer; text-decoration: none; display: inline-flex; align-items: center; gap: 6px; font-size: 13px; }
    .btn:hover { background: var(--primary-dark); }
    .btn-secondary { background: #e2e8f0; color: #1e293b; }
    .btn-secondary:hover { background: #cbd5e1; }
    .grid { display: grid; grid-template-columns: 1fr; gap: 16px; }
    .card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
    .card-top { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px; }
    .company { font-size: 16px; font-weight: 700; color: #0f172a; }
    .title { font-size: 14px; font-weight: 600; color: #334155; margin-top: 2px; }
    .meta { font-size: 12px; color: var(--text-muted); margin-top: 4px; display: flex; gap: 12px; }
    .score-badge { font-size: 14px; font-weight: 700; padding: 4px 10px; border-radius: 20px; }
    .score-high { background: #dcfce7; color: #166534; }
    .score-mid { background: #fef3c7; color: #92400e; }
    .score-low { background: #fee2e2; color: #991b1b; }
    .badges { display: flex; gap: 8px; flex-wrap: wrap; margin: 12px 0; }
    .badge { font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 4px; }
    .badge-track { background: #e0e7ff; color: #3730a3; }
    .badge-opt { background: #dcfce7; color: #15803d; }
    .badge-knockout { background: #fee2e2; color: #b91c1c; }
    .skills-block { margin-top: 10px; font-size: 12px; line-height: 1.6; }
    .matched-tag { background: #f1f5f9; color: #1e293b; padding: 2px 6px; border-radius: 4px; margin-right: 4px; font-weight: 500; }
    .actions { display: flex; gap: 10px; margin-top: 16px; border-top: 1px solid var(--border); padding-top: 12px; }
  </style>
</head>
<body>
  <div class="header">
    <div>
      <h1>SmartApplyAI v2 — Pipeline Triage Web Dashboard</h1>
      <div class="subtitle">Stage 1 Ingestion & Scoring &bull; Stage 2 Applied Skills 1-Page LaTeX &bull; Stage 3 Outreach & Excel</div>
    </div>
    <div style="display: flex; gap: 10px;">
      <a href="/api/pipeline/export-excel" class="btn btn-secondary" target="_blank">&#128196; Download Excel Pipeline</a>
      <button onclick="harvest()" class="btn">&#8635; Harvest & Score Jobs</button>
    </div>
  </div>

  <div id="job-list" class="grid">
    <div style="text-align: center; padding: 40px; color: var(--text-muted);">Loading live scored jobs...</div>
  </div>

  <script>
    async function loadJobs() {
      const res = await fetch('/api/pipeline/jobs?limit=50');
      const data = await res.json();
      const container = document.getElementById('job-list');
      if (!data.jobs.length) {
        container.innerHTML = '<div style="text-align: center; padding: 40px;">No jobs discovered yet. Click "Harvest & Score Jobs".</div>';
        return;
      }
      container.innerHTML = data.jobs.map(j => {
        const scoreClass = j.match_score >= 75 ? 'score-high' : (j.match_score >= 50 ? 'score-mid' : 'score-low');
        const eligBadge = j.is_eligible 
          ? `<span class="badge badge-opt">${j.sponsorship_signal || 'ELIGIBLE'}</span>` 
          : `<span class="badge badge-knockout">KNOCKOUT: ${j.knockout_reason}</span>`;
        const matched = (j.matched_skills || []).slice(0, 10).map(s => `<span class="matched-tag">${s}</span>`).join('');
        return `
          <div class="card">
            <div class="card-top">
              <div>
                <div class="company">${j.company}</div>
                <div class="title">${j.title}</div>
                <div class="meta">
                  <span>&#128205; ${j.location || 'Remote'}</span>
                  <span>&#128197; Grad: May 2027 (F-1 OPT)</span>
                  <span>&#128187; ATS: ${j.ats_platform || 'Direct'}</span>
                </div>
              </div>
              <div class="score-badge ${scoreClass}">${j.match_score}% Match</div>
            </div>
            <div class="badges">
              <span class="badge badge-track">${j.assigned_track.toUpperCase()}</span>
              ${eligBadge}
              <span class="badge" style="background:#f1f5f9; color:#475569;">STATUS: ${j.pipeline_status}</span>
            </div>
            <div class="skills-block">
              <strong>Matched Applied Skills:</strong> ${matched || '<em>None detected</em>'}
            </div>
            <div class="actions">
              <a href="${j.job_url}" target="_blank" class="btn btn-secondary" style="font-size:12px;">View Official JD &rarr;</a>
              <button onclick="tailor(${j.id})" class="btn" style="font-size:12px;">&#9988; Tailor 1-Page Resume & Outreach</button>
            </div>
          </div>
        `;
      }).join('');
    }

    async function harvest() {
      const btn = event.target;
      btn.textContent = 'Harvesting...';
      btn.disabled = true;
      try {
        await fetch('/api/pipeline/harvest', { method: 'POST' });
        await loadJobs();
      } finally {
        btn.textContent = '↻ Harvest & Score Jobs';
        btn.disabled = false;
      }
    }

    async function tailor(jobId) {
      alert(`Triggering Applied-Skills Resume Tailoring + 3 Outreach Messages for Job ID ${jobId}...`);
      window.location.href = `/api/pipeline/jobs/${jobId}`;
    }

    loadJobs();
  </script>
</body>
</html>
"""
    )
