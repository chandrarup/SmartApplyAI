"""
Stage 3: Three-Persona Outreach Engine + Master Excel Sync (harnesses/outreach_harness.py).
Generates 3 plain-text, zero-AI-slop outreach messages per job:
  1. CEO / Exec Delegation Email (<115 words, low-friction SME delegation ask)
  2. Hiring Manager / SME Peer Builder Note (<=300 chars LinkedIn + direct email)
  3. HR / Recruiter Qualification Checklist Note (<=300 chars LinkedIn + direct email)

Persists records to outreach_drafts in smartapply.db and synchronizes
all 3 tabs of SmartApply_Master_Pipeline.xlsx.
"""

import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

from backend.db import (
    DEFAULT_DB_PATH,
    get_db_connection,
    get_job_by_id,
    get_outreach_drafts_for_job,
    init_db,
    list_jobs,
    save_outreach_draft,
)
from backend.excel_sync import sync_db_to_excel

BANNED_WORDS = {
    "spearheaded", "synergized", "paradigm", "holistic",
    "delve", "tapestry", "utilized", "leveraged", "enthusiast"
}


def clean_plaintext(text: str) -> str:
    """Strips markdown and ensures pure human plain text."""
    t = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    t = re.sub(r"\*([^*]+)\*", r"\1", t)
    t = re.sub(r"`([^`]+)`", r"\1", t)
    t = re.sub(r"#{1,6}\s*", "", t)
    t = re.sub(r"<[^>]+>", "", t)
    return t.strip()


def trim_to_char_limit(text: str, max_chars: int = 300) -> str:
    """Trims text to within max_chars without cutting words awkwardly."""
    clean = clean_plaintext(text)
    if len(clean) <= max_chars:
        return clean
    trimmed = clean[:max_chars - 4]
    last_space = trimmed.rfind(" ")
    if last_space != -1:
        trimmed = trimmed[:last_space]
    return trimmed.strip() + "..."


def draft_ceo_delegation_outreach(job: Dict[str, Any]) -> Dict[str, Any]:
    """
    Persona 1: CEO / CTO / VP Delegation Email.
    Strictly plain text, 70-114 words (<115 words), contains 1 concrete metric,
    and ends with a low-friction delegation ask to route to internal SME.
    """
    company = job.get("company", "the team")
    title = job.get("title", "AI Systems Engineer")
    track = job.get("assigned_track", "agentic_swe")

    if track == "bioinformatics_ai":
        problem_area = "spatial transcriptomics and single-cell analytics pipelines"
        metric_line = (
            "Before starting my M.S. in AI at UH, I spent two years at Accenture and currently "
            "collaborate with UH BME streaming 532,392 single cells across 960 genes with sub-second latency."
        )
        ask_sme = "the computational biology lead or SME owning spatial pipelines"
    elif track == "core_aiml":
        problem_area = "post-training alignment and LLM evaluation harnesses"
        metric_line = (
            "Before starting my M.S. in AI at UH, I spent two years at Accenture deploying RAG and "
            "evaluation systems across 30,000+ files, achieving 89% diagnostic coverage."
        )
        ask_sme = "the engineering lead or SME owning model training and evaluation"
    elif track == "data_analyst_ds":
        problem_area = "predictive decision modeling and interactive simulation tools"
        metric_line = (
            "Before my M.S. in AI at UH, I engineered analytics pipelines at Accenture and built "
            "predictive classification on 289,000 employee records with SHAP explainability."
        )
        ask_sme = "the data science or analytics lead"
    else:
        problem_area = "multi-agent workflows and MCP tool integrations"
        metric_line = (
            "Before starting my M.S. in AI at UH, I spent two years at Accenture building production "
            "Python/FastAPI multi-agent workflows across 30,000+ files, achieving 89% diagnostic coverage."
        )
        ask_sme = "the engineering lead or SME owning agent infrastructure"

    subject = f"Question regarding {company}'s {problem_area}"

    body = (
        f"Hi there,\n\n"
        f"Saw that {company} is scaling out {problem_area}.\n\n"
        f"{metric_line}\n\n"
        f"I know your plate is full, so I don't want to take up your calendar—could you point me to "
        f"{ask_sme} so I can share a quick technical note?\n\n"
        f"Best,\nChandra Rup Daka\n+1 (253) 632-3181 | https://chandrarup.github.io/Portfolio/"
    )

    body = clean_plaintext(body)
    word_count = len(body.split())
    # Ensure word count strictly < 115
    if word_count >= 115:
        # Compact closing
        body = body.replace("so I don't want to take up your calendar—", "")

    li_note = (
        f"Saw {company}'s work on {problem_area[:40]}. Spent 2 yrs at Accenture building "
        f"FastAPI multi-agent & RAG systems across 30K+ files (now MS AI @ UH). "
        f"Could you point me to the engineering lead so I can share a quick note?"
    )
    li_note = trim_to_char_limit(li_note, 300)

    return {
        "job_id": job["id"],
        "persona_type": "CEO_DELEGATION",
        "target_person_name": f"{company} Executive",
        "target_person_title": "CEO / CTO / VP Engineering",
        "email_subject": subject,
        "email_body_plaintext": body,
        "linkedin_note_text": li_note,
        "outreach_status": "DRAFTED",
    }


def draft_hiring_manager_sme_outreach(job: Dict[str, Any]) -> Dict[str, Any]:
    """
    Persona 2: Hiring Manager / SME Peer-Builder Note.
    Focuses on architecture, tools, and technical relevance. LinkedIn <= 300 chars.
    """
    company = job.get("company", "the team")
    title = job.get("title", "AI Systems Engineer")
    track = job.get("assigned_track", "agentic_swe")

    if track == "bioinformatics_ai":
        subject = f"Gene-Viewer spatial pipelines / {title} at {company}"
        body = (
            f"Hi,\n\n"
            f"Came across the {title} opening at {company}. I built Gene-Viewer, an open-source visual "
            f"analytics platform streaming 532,392 cells x 960 genes using Python (FastAPI, Canvas) and "
            f"Scanpy with sub-second spatial queries.\n\n"
            f"Prior to my M.S. in AI at UH, I spent two years at Accenture shipping Python/FastAPI microservices. "
            f"Would love to connect and share our open-source benchmark repo if relevant to your roadmap.\n\n"
            f"Best,\nChandra Rup Daka\n+1 (253) 632-3181 | https://chandrarup.github.io/Portfolio/"
        )
        li_note = (
            f"Hi — saw your team's {title} role at {company}. I built Gene-Viewer (Python/FastAPI) "
            f"streaming 532K single cells with Scanpy, following 2 yrs in production GenAI at Accenture. "
            f"Would love to connect and follow your work!"
        )
    elif track == "core_aiml":
        subject = f"Post-training alignment & evaluation / {title} at {company}"
        body = (
            f"Hi,\n\n"
            f"Saw your team's work on model post-training at {company}. I recently benchmarked SFT, DPO, and GRPO "
            f"alignment on Qwen2.5-1.5B in PyTorch/DeepSpeed, studying mathematical stability on GSM8K.\n\n"
            f"At Accenture, I shipped offline LLM evaluation harnesses across 30,000+ files with 89% diagnostic coverage. "
            f"I've attached my 1-page resume—would welcome the chance to exchange notes on your evaluation stack.\n\n"
            f"Best,\nChandra Rup Daka\n+1 (253) 632-3181 | https://chandrarup.github.io/Portfolio/"
        )
        li_note = (
            f"Hi — saw your post-training work at {company}. I recently benchmarked SFT vs DPO vs GRPO "
            f"in PyTorch on Qwen2.5, alongside 2 yrs shipping production LLM evaluation at Accenture. "
            f"Would love to connect and share benchmarks!"
        )
    else:
        subject = f"Multi-agent MCP workflows / {title} at {company}"
        body = (
            f"Hi,\n\n"
            f"Saw your team's {title} opening at {company}. At Accenture, I spent two years building "
            f"production Python/FastAPI multi-agent workflows (LangGraph, AutoGen) and hybrid Neo4j/pgvector "
            f"RAG systems across 30,000+ files.\n\n"
            f"Lately I've been building MCP routing servers and local LLM-as-judge evaluation harnesses. "
            f"I've attached my 1-page resume and would welcome the chance to chat about your agent infrastructure.\n\n"
            f"Best,\nChandra Rup Daka\n+1 (253) 632-3181 | https://chandrarup.github.io/Portfolio/"
        )
        li_note = (
            f"Hi — saw {company}'s {title} role. I spent 2 yrs at Accenture building Python/FastAPI "
            f"multi-agent workflows (LangGraph, MCP) & Neo4j/pgvector RAG across 30K+ files. "
            f"Now doing MS AI @ UH (May 2027). Would love to connect!"
        )

    body = clean_plaintext(body)
    li_note = trim_to_char_limit(li_note, 300)

    return {
        "job_id": job["id"],
        "persona_type": "HIRING_MANAGER_SME",
        "target_person_name": f"{company} Engineering Lead",
        "target_person_title": "Hiring Manager / Tech Lead",
        "email_subject": subject,
        "email_body_plaintext": body,
        "linkedin_note_text": li_note,
        "outreach_status": "DRAFTED",
    }


def draft_hr_recruiter_outreach(job: Dict[str, Any]) -> Dict[str, Any]:
    """
    Persona 3: HR / Technical Recruiter Qualification Note.
    Instant checklist verifying May 2027 grad, 3.9 GPA, F-1 OPT/STEM-OPT, 2+ yrs production GenAI.
    """
    company = job.get("company", "the team")
    title = job.get("title", "AI Systems Engineer")

    subject = f"Candidate Application: Chandra Rup Daka — {title} ({company})"

    body = (
        f"Hi,\n\n"
        f"I recently submitted my application for the {title} opening at {company} and wanted to share "
        f"a quick qualification summary for your review:\n\n"
        f"- Experience: 2 years production GenAI Engineer at Accenture (Python, FastAPI, LangGraph, RAG)\n"
        f"- Education: M.S. in Engineering Data Science & AI at University of Houston (May 2027 grad, 3.9/4.0 GPA)\n"
        f"- Work Authorization: F-1 Visa with 36-month OPT and STEM-OPT work authorization\n"
        f"- Portfolio: https://chandrarup.github.io/Portfolio/ and GitHub: https://github.com/chandrarup\n\n"
        f"I have attached my tailored 1-page resume for your records. Looking forward to hearing from your team.\n\n"
        f"Best regards,\nChandra Rup Daka\n+1 (253) 632-3181 | chandrarupdaka@gmail.com"
    )

    li_note = (
        f"Hi — applied for the {title} role at {company}. Quick summary: 2 yrs production GenAI at "
        f"Accenture, MS AI at University of Houston (May 2027 grad, 3.9 GPA), F-1 OPT/STEM-OPT eligible. "
        f"Would love to connect!"
    )

    body = clean_plaintext(body)
    li_note = trim_to_char_limit(li_note, 300)

    return {
        "job_id": job["id"],
        "persona_type": "HR_RECRUITER",
        "target_person_name": f"{company} Recruiting Team",
        "target_person_title": "Technical Recruiter",
        "email_subject": subject,
        "email_body_plaintext": body,
        "linkedin_note_text": li_note,
        "outreach_status": "DRAFTED",
    }


def generate_outreach_for_job(job_id: int, db_path: str = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    """Generates and saves all 3 outreach drafts for a job, updating smartapply.db and Excel."""
    init_db(db_path)
    job = get_job_by_id(job_id, db_path)
    if not job:
        raise ValueError(f"Job {job_id} not found.")

    p1 = draft_ceo_delegation_outreach(job)
    p2 = draft_hiring_manager_sme_outreach(job)
    p3 = draft_hr_recruiter_outreach(job)

    drafts = [p1, p2, p3]
    for d in drafts:
        save_outreach_draft(d, db_path)

    # Sync to Excel Tab 3
    sync_db_to_excel(db_path)
    return drafts


def generate_outreach_for_all_must_apply(db_path: str = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    """Generates all 3 outreach drafts for every job that meets Must-Apply criteria (>=75% & eligible)."""
    init_db(db_path)
    with get_db_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE is_eligible = 1 AND match_score >= 75 ORDER BY match_score DESC"
        ).fetchall()
        must_apply = [dict(r) for r in rows]

    all_drafts = []
    for j in must_apply:
        print(f"[*] Generating 3-persona outreach for [{j['id']}] {j['company']} — {j['title']}...")
        drafts = generate_outreach_for_job(j["id"], db_path)
        all_drafts.extend(drafts)
    return all_drafts


def main():
    parser = argparse.ArgumentParser(description="SmartApplyAI v2 Outreach Harness")
    parser.add_argument("--job-id", type=int, default=None, help="Target job ID")
    parser.add_argument("--all", action="store_true", help="Generate outreach for all MUST_APPLY jobs")
    args = parser.parse_args()

    print(f"[*] Running Outreach Harness (job_id={args.job_id}, all={args.all})...")
    if args.job_id:
        drafts = generate_outreach_for_job(args.job_id)
        print(f"[+] Successfully drafted {len(drafts)} outreach messages for job {args.job_id}:")
        for d in drafts:
            print(f"  - [{d['persona_type']}] Subject: {d['email_subject']}")
            print(f"      LinkedIn ({len(d['linkedin_note_text'])} chars): {d['linkedin_note_text']}")
    else:
        drafts = generate_outreach_for_all_must_apply()
        print(f"[+] Successfully generated {len(drafts)} outreach messages across all Must-Apply jobs!")


if __name__ == "__main__":
    main()
