"""
Relational Database Layer for SmartApplyAI v2 (smartapply.db).
Manages tables: jobs, tailored_resumes, outreach_drafts, application_runs.
Provides thread-safe connections, deduplication via canonical_hash, and CRUD helpers.
"""

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "smartapply.db")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_canonical_hash(company: str, title: str, location: Optional[str] = None) -> str:
    """Computes a SHA-256 hash from normalized company, title, and location."""
    c = (company or "").strip().lower()
    t = (title or "").strip().lower()
    loc = (location or "").strip().lower()
    # Normalize common abbreviations
    c = "".join(ch for ch in c if ch.isalnum() or ch.isspace())
    t = "".join(ch for ch in t if ch.isalnum() or ch.isspace())
    raw = f"{c}|{t}|{loc}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_db_connection(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    """Initializes smartapply.db with all required tables and indexes."""
    with get_db_connection(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                canonical_hash TEXT UNIQUE NOT NULL,
                company TEXT NOT NULL,
                title TEXT NOT NULL,
                location TEXT,
                workplace_type TEXT,
                ats_platform TEXT,
                job_url TEXT NOT NULL,
                posted_at TIMESTAMP,
                discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                raw_jd_text TEXT NOT NULL,
                
                -- Knockout & Eligibility Gate
                is_eligible BOOLEAN DEFAULT 1,
                knockout_reason TEXT,
                sponsorship_signal TEXT,
                
                -- Scoring & Track Routing
                assigned_track TEXT NOT NULL,
                match_score INTEGER NOT NULL,
                technical_score INTEGER,
                domain_score INTEGER,
                experience_score INTEGER,
                soft_skill_score INTEGER,
                extracted_jd_skills_json TEXT NOT NULL,
                matched_skills_json TEXT NOT NULL,
                missing_gaps_json TEXT NOT NULL,
                
                -- Lifecycle State Machine
                pipeline_status TEXT DEFAULT 'DISCOVERED'
            );

            CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(match_score DESC);
            CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(pipeline_status);
            CREATE INDEX IF NOT EXISTS idx_jobs_track ON jobs(assigned_track);

            CREATE TABLE IF NOT EXISTS tailored_resumes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                track_used TEXT NOT NULL,
                selected_projects_json TEXT NOT NULL,
                entry_skills_map_json TEXT NOT NULL,
                tex_source_path TEXT NOT NULL,
                pdf_output_path TEXT NOT NULL,
                png_preview_path TEXT NOT NULL,
                page_count INTEGER NOT NULL CHECK(page_count = 1),
                vertical_fill_pct REAL NOT NULL,
                max_overfull_hbox_pt REAL NOT NULL DEFAULT 0.0,
                bijective_skill_coverage_pct REAL NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_resumes_job ON tailored_resumes(job_id);

            CREATE TABLE IF NOT EXISTS outreach_drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                persona_type TEXT NOT NULL,
                target_person_name TEXT,
                target_person_title TEXT,
                target_linkedin_url TEXT,
                target_email TEXT,
                email_subject TEXT NOT NULL,
                email_body_plaintext TEXT NOT NULL,
                linkedin_note_text TEXT NOT NULL,
                outreach_status TEXT DEFAULT 'DRAFTED',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_outreach_job ON outreach_drafts(job_id);

            CREATE TABLE IF NOT EXISTS application_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                resume_id INTEGER REFERENCES tailored_resumes(id),
                execution_mode TEXT NOT NULL,
                fields_detected INTEGER DEFAULT 0,
                fields_filled INTEGER DEFAULT 0,
                file_uploaded BOOLEAN DEFAULT 0,
                custom_qa_json TEXT,
                confirmation_screenshot_path TEXT,
                run_status TEXT NOT NULL,
                error_log TEXT,
                executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_runs_job ON application_runs(job_id);
            """
        )
        conn.commit()


def upsert_job(job_data: Dict[str, Any], db_path: str = DEFAULT_DB_PATH) -> int:
    """Inserts or updates a job based on canonical_hash. Returns job id."""
    init_db(db_path)
    canonical_hash = job_data.get("canonical_hash")
    if not canonical_hash:
        canonical_hash = compute_canonical_hash(
            job_data["company"], job_data["title"], job_data.get("location")
        )
        job_data["canonical_hash"] = canonical_hash

    extracted = job_data.get("extracted_jd_skills_json", "[]")
    if isinstance(extracted, list):
        extracted = json.dumps(extracted)
    matched = job_data.get("matched_skills_json", "[]")
    if isinstance(matched, list):
        matched = json.dumps(matched)
    missing = job_data.get("missing_gaps_json", "[]")
    if isinstance(missing, list):
        missing = json.dumps(missing)

    with get_db_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO jobs (
                canonical_hash, company, title, location, workplace_type,
                ats_platform, job_url, posted_at, raw_jd_text,
                is_eligible, knockout_reason, sponsorship_signal,
                assigned_track, match_score, technical_score, domain_score,
                experience_score, soft_skill_score,
                extracted_jd_skills_json, matched_skills_json, missing_gaps_json,
                pipeline_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(canonical_hash) DO UPDATE SET
                company = excluded.company,
                title = excluded.title,
                location = excluded.location,
                workplace_type = excluded.workplace_type,
                ats_platform = excluded.ats_platform,
                job_url = excluded.job_url,
                raw_jd_text = excluded.raw_jd_text,
                is_eligible = excluded.is_eligible,
                knockout_reason = excluded.knockout_reason,
                sponsorship_signal = excluded.sponsorship_signal,
                assigned_track = excluded.assigned_track,
                match_score = excluded.match_score,
                technical_score = excluded.technical_score,
                domain_score = excluded.domain_score,
                experience_score = excluded.experience_score,
                soft_skill_score = excluded.soft_skill_score,
                extracted_jd_skills_json = excluded.extracted_jd_skills_json,
                matched_skills_json = excluded.matched_skills_json,
                missing_gaps_json = excluded.missing_gaps_json,
                pipeline_status = CASE 
                    WHEN jobs.pipeline_status IN ('APPLIED', 'INTERVIEW', 'OUTREACH_SENT') THEN jobs.pipeline_status
                    ELSE excluded.pipeline_status
                END
            RETURNING id;
            """,
            (
                canonical_hash,
                job_data["company"],
                job_data["title"],
                job_data.get("location", ""),
                job_data.get("workplace_type", "Unknown"),
                job_data.get("ats_platform", "Other"),
                job_data["job_url"],
                job_data.get("posted_at", _utc_now()),
                job_data.get("raw_jd_text", ""),
                1 if job_data.get("is_eligible", True) else 0,
                job_data.get("knockout_reason"),
                job_data.get("sponsorship_signal", "SILENT"),
                job_data.get("assigned_track", "agentic_swe"),
                job_data.get("match_score", 0),
                job_data.get("technical_score", 0),
                job_data.get("domain_score", 0),
                job_data.get("experience_score", 0),
                job_data.get("soft_skill_score", 0),
                extracted,
                matched,
                missing,
                job_data.get("pipeline_status", "DISCOVERED"),
            ),
        )
        row = cursor.fetchone()
        job_id = row[0] if row else None
        conn.commit()
        return job_id


def get_job_by_id(job_id: int, db_path: str = DEFAULT_DB_PATH) -> Optional[Dict[str, Any]]:
    init_db(db_path)
    with get_db_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None


def get_job_by_hash(canonical_hash: str, db_path: str = DEFAULT_DB_PATH) -> Optional[Dict[str, Any]]:
    init_db(db_path)
    with get_db_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM jobs WHERE canonical_hash = ?", (canonical_hash,)).fetchone()
        return dict(row) if row else None


def list_jobs(
    status: Optional[str] = None,
    min_score: Optional[int] = None,
    eligible_only: bool = False,
    limit: Optional[int] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> List[Dict[str, Any]]:
    init_db(db_path)
    query = "SELECT * FROM jobs WHERE 1=1"
    params: List[Any] = []

    if status:
        query += " AND pipeline_status = ?"
        params.append(status)
    if min_score is not None:
        query += " AND match_score >= ?"
        params.append(min_score)
    if eligible_only:
        query += " AND is_eligible = 1"

    query += " ORDER BY match_score DESC, discovered_at DESC"
    if limit:
        query += " LIMIT ?"
        params.append(limit)

    with get_db_connection(db_path) as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
        return [dict(r) for r in rows]


def update_job_status(job_id: int, status: str, db_path: str = DEFAULT_DB_PATH) -> None:
    init_db(db_path)
    with get_db_connection(db_path) as conn:
        conn.execute("UPDATE jobs SET pipeline_status = ? WHERE id = ?", (status, job_id))
        conn.commit()


def save_tailored_resume(resume_data: Dict[str, Any], db_path: str = DEFAULT_DB_PATH) -> int:
    init_db(db_path)
    sel_proj = resume_data.get("selected_projects_json", "[]")
    if isinstance(sel_proj, (list, dict)):
        sel_proj = json.dumps(sel_proj)
    entry_skills = resume_data.get("entry_skills_map_json", "{}")
    if isinstance(entry_skills, (list, dict)):
        entry_skills = json.dumps(entry_skills)

    with get_db_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO tailored_resumes (
                job_id, track_used, selected_projects_json, entry_skills_map_json,
                tex_source_path, pdf_output_path, png_preview_path,
                page_count, vertical_fill_pct, max_overfull_hbox_pt,
                bijective_skill_coverage_pct
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id;
            """,
            (
                resume_data["job_id"],
                resume_data["track_used"],
                sel_proj,
                entry_skills,
                resume_data["tex_source_path"],
                resume_data["pdf_output_path"],
                resume_data.get("png_preview_path", ""),
                resume_data.get("page_count", 1),
                resume_data.get("vertical_fill_pct", 94.0),
                resume_data.get("max_overfull_hbox_pt", 0.0),
                resume_data.get("bijective_skill_coverage_pct", 100.0),
            ),
        )
        row = cursor.fetchone()
        resume_id = row[0]
        # Also update job pipeline_status if appropriate
        conn.execute(
            """
            UPDATE jobs SET pipeline_status = 'TAILORED_AND_DRAFTED'
            WHERE id = ? AND pipeline_status IN ('DISCOVERED', 'MUST_APPLY')
            """,
            (resume_data["job_id"],),
        )
        conn.commit()
        return resume_id


def get_tailored_resume_for_job(job_id: int, db_path: str = DEFAULT_DB_PATH) -> Optional[Dict[str, Any]]:
    init_db(db_path)
    with get_db_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM tailored_resumes WHERE job_id = ? ORDER BY id DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        return dict(row) if row else None


def save_outreach_draft(draft_data: Dict[str, Any], db_path: str = DEFAULT_DB_PATH) -> int:
    init_db(db_path)
    with get_db_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO outreach_drafts (
                job_id, persona_type, target_person_name, target_person_title,
                target_linkedin_url, target_email, email_subject,
                email_body_plaintext, linkedin_note_text, outreach_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id;
            """,
            (
                draft_data["job_id"],
                draft_data["persona_type"],
                draft_data.get("target_person_name"),
                draft_data.get("target_person_title"),
                draft_data.get("target_linkedin_url"),
                draft_data.get("target_email"),
                draft_data["email_subject"],
                draft_data["email_body_plaintext"],
                draft_data["linkedin_note_text"],
                draft_data.get("outreach_status", "DRAFTED"),
            ),
        )
        row = cursor.fetchone()
        draft_id = row[0]
        conn.commit()
        return draft_id


def get_outreach_drafts_for_job(job_id: int, db_path: str = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    init_db(db_path)
    with get_db_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM outreach_drafts WHERE job_id = ? ORDER BY id ASC",
            (job_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def record_application_run(run_data: Dict[str, Any], db_path: str = DEFAULT_DB_PATH) -> int:
    init_db(db_path)
    custom_qa = run_data.get("custom_qa_json", "{}")
    if isinstance(custom_qa, (list, dict)):
        custom_qa = json.dumps(custom_qa)

    with get_db_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO application_runs (
                job_id, resume_id, execution_mode, fields_detected,
                fields_filled, file_uploaded, custom_qa_json,
                confirmation_screenshot_path, run_status, error_log
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id;
            """,
            (
                run_data["job_id"],
                run_data.get("resume_id"),
                run_data["execution_mode"],
                run_data.get("fields_detected", 0),
                run_data.get("fields_filled", 0),
                1 if run_data.get("file_uploaded", False) else 0,
                custom_qa,
                run_data.get("confirmation_screenshot_path"),
                run_data["run_status"],
                run_data.get("error_log"),
            ),
        )
        row = cursor.fetchone()
        run_id = row[0]
        if run_data["run_status"] == "SUCCESS_SUBMITTED":
            conn.execute("UPDATE jobs SET pipeline_status = 'APPLIED' WHERE id = ?", (run_data["job_id"],))
        conn.commit()
        return run_id


def get_latest_run_for_job(job_id: int, db_path: str = DEFAULT_DB_PATH) -> Optional[Dict[str, Any]]:
    init_db(db_path)
    with get_db_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM application_runs WHERE job_id = ? ORDER BY id DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        return dict(row) if row else None
