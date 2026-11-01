"""
Stage 1: Job Deconstruction, Knockout Gate & Scoring Harness (harnesses/scorer_harness.py).
Evaluates raw JD postings against Chandra Rup Daka's career knowledge graph:
  - Evaluates May 2027 graduation & F-1 OPT/STEM-OPT knockout rules
  - Assigns one of 5 Base Tracks: agentic_swe, fde, core_aiml, bioinformatics_ai, data_analyst_ds
  - Deconstructs required & preferred JD skills
  - Computes 0-100 composite match score and updates pipeline_status ('MUST_APPLY' if >=75% & eligible)
  - Syncs results into smartapply.db and SmartApply_Master_Pipeline.xlsx
"""

import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

from backend.db import (
    DEFAULT_DB_PATH,
    get_db_connection,
    get_job_by_id,
    init_db,
    list_jobs,
    upsert_job,
)
from backend.excel_sync import sync_db_to_excel

# Track trigger dictionaries
TRACK_TRIGGERS = {
    "bioinformatics_ai": [
        "bioinformatics", "spatial transcriptomics", "single-cell", "genomics", "xenium",
        "cosmx", "cellpose", "stardist", "scanpy", "seurat", "computational biology",
        "insitutype", "anndata", "biomedical", "pathology", "immunology"
    ],
    "core_aiml": [
        "post-training", "alignment", "sft", "dpo", "grpo", "rlhf", "pre-training",
        "transformer architecture", "foundation model", "deep learning", "pytorch",
        "deepspeed", "lora", "peft", "model optimization", "hugging face"
    ],
    "fde": [
        "forward deployed", "solutions engineer", "customer engineering", "fde",
        "client-facing", "field engineer", "enterprise deployment", "technical solutions",
        "solution architect", "partner engineering"
    ],
    "data_analyst_ds": [
        "business intelligence", "bi developer", "data analyst", "power bi", "tableau",
        "decision science", "dax", "dashboard", "reporting", "analytics engineer",
        "data analytics", "predictive analytics", "scikit-learn"
    ],
    "agentic_swe": [
        "agent", "agentic", "multi-agent", "mcp", "model context protocol", "langgraph",
        "langchain", "autogen", "crewai", "rag", "fastapi", "rest api", "full stack",
        "software engineer", "ai engineer", "backend", "typescript", "react"
    ],
}

# Known skill vocabulary for Chandra's canonical profile
CANONICAL_SKILLS = {
    "python", "typescript", "javascript", "sql", "r", "java", "c++", "bash", "html", "css",
    "fastapi", "flask", "react", "react 19", "next.js", "tanstack start", "rest apis",
    "langchain", "langgraph", "autogen", "crewai", "model context protocol", "mcp", "rag",
    "llm-as-judge", "prompt engineering", "sft", "dpo", "grpo", "rlhf", "lora", "peft",
    "openai api", "claude", "anthropic claude api", "gemini", "google gemini api", "azure openai",
    "aws bedrock", "ollama", "pytorch", "tensorflow", "hugging face", "hugging face transformers",
    "scikit-learn", "bertopic", "sentence transformers", "shap", "cellpose", "stardist",
    "opencv", "scanpy", "seurat", "insitutype", "anndata", "docker", "kubernetes",
    "google kubernetes engine", "gke", "git", "ci/cd", "azure apim", "postgresql", "pgvector",
    "supabase", "neo4j", "pinecone", "chromadb", "milvus", "sqlite", "power bi", "dax", "excel"
}

KNOCKOUT_PATTERNS = [
    (r"\b(?:us citizenship (?:is )?required|must be a us citizen|only us citizens|citizenship: us)\b", "Requires US Citizenship"),
    (r"\b(?:active (?:top )?secret(?: clearance)?|security clearance required|must possess (?:an active )?clearance)\b", "Active Security Clearance Required"),
    (r"\b(?:will not sponsor|no visa sponsorship|no sponsorship available|must be legally authorized to work in the united states without visa sponsorship)\b", "No Visa Sponsorship"),
]

SPONSORSHIP_FRIENDLY_PATTERNS = [
    r"\b(?:opt|stem-opt|cpt|visa sponsorship (?:is )?available|f-1|international students welcome)\b",
]


def evaluate_knockouts(jd_text: str) -> Tuple[bool, Optional[str], str]:
    """
    Evaluates candidate eligibility:
    Chandra Rup Daka: M.S. AI at UH, Graduation: May 2027, F-1 Visa (CPT/OPT eligible, no US citizenship).
    """
    text_lower = jd_text.lower()
    for pattern, reason in KNOCKOUT_PATTERNS:
        if re.search(pattern, text_lower):
            # Check if reason is "No Visa Sponsorship" but allows OPT
            if "sponsorship" in reason.lower() and re.search(r"\b(?:cpt|opt)\b", text_lower):
                continue
            return False, reason, "NO_SPONSORSHIP" if "sponsorship" in reason.lower() else "CLEARANCE_BLOCKED"

    # Graduation date check
    if re.search(r"\b(?:graduating by|graduated between|graduation date:?)\s*(?:2023|2024|december 2025)\b", text_lower):
        if not re.search(r"\b(?:2026|2027|current student|master)\b", text_lower):
            return False, "Graduation date prior to May 2027 required", "INELIGIBLE_GRAD_DATE"

    sponsorship_signal = "SILENT"
    for pat in SPONSORSHIP_FRIENDLY_PATTERNS:
        if re.search(pat, text_lower):
            sponsorship_signal = "OPT_FRIENDLY"
            break

    return True, None, sponsorship_signal


def assign_archetype_track(jd_text: str, role_title: str) -> str:
    """Classifies the job into 1 of the 5 Base Tracks."""
    combined = f"{role_title} {jd_text}".lower()

    # Score each track by trigger matches
    scores = {}
    for track, triggers in TRACK_TRIGGERS.items():
        score = sum(1 for trig in triggers if trig in combined)
        # Give higher weight to title matches
        for trig in triggers:
            if trig in role_title.lower():
                score += 3
        scores[track] = score

    # Return highest scoring track, defaulting to agentic_swe
    best_track = max(scores, key=scores.get)
    if scores[best_track] == 0:
        return "agentic_swe"
    return best_track


def extract_skills_and_score(
    jd_text: str,
    track: str,
    company: str,
) -> Dict[str, Any]:
    """
    Extracts technical terms from JD, maps to Chandra's skills, and computes 0-100 score.
    """
    text_lower = jd_text.lower()

    # Find matched canonical skills
    matched_skills = []
    for skill in CANONICAL_SKILLS:
        # Match as word boundary or exact phrase
        pat = rf"\b{re.escape(skill)}\b"
        if re.search(pat, text_lower):
            matched_skills.append(skill.title() if len(skill) > 3 else skill.upper())

    # Detect some common missing skills / gaps in tech industry
    common_other_skills = [
        "golang", "rust", "c#", ".net", "ruby", "swift", "kotlin", "spark", "hadoop",
        "kafka", "salesforce", "graphql", "terraform", "ansible", "solidity"
    ]
    missing_gaps = []
    for gap in common_other_skills:
        if re.search(rf"\b{re.escape(gap)}\b", text_lower) and gap not in [s.lower() for s in matched_skills]:
            missing_gaps.append(gap.title())

    # Scoring breakdown
    # Technical: 0-40 (based on matched technical skills count)
    tech_score = min(40, int(len(matched_skills) * 3.5))

    # Domain: 0-30 (based on track trigger matches)
    triggers = TRACK_TRIGGERS.get(track, [])
    domain_matches = sum(1 for t in triggers if t in text_lower)
    domain_score = min(30, domain_matches * 6)

    # Experience: 0-20 (Chandra has 2 yrs GenAI experience at Accenture + 1 yr research at UH)
    exp_score = 18
    if re.search(r"\b(?:5\+|7\+|10\+|principal|director)\b", text_lower):
        exp_score = 12

    # Soft skills & workflow: 0-10
    soft_matches = sum(1 for w in ["agile", "scrum", "mentoring", "presentation", "cross-functional", "leadership"] if w in text_lower)
    soft_score = min(10, 6 + soft_matches * 2)

    composite_score = min(100, tech_score + domain_score + exp_score + soft_score)

    return {
        "technical_score": tech_score,
        "domain_score": domain_score,
        "experience_score": exp_score,
        "soft_skill_score": soft_score,
        "match_score": composite_score,
        "matched_skills": sorted(list(set(matched_skills))),
        "missing_gaps": sorted(list(set(missing_gaps))),
    }


def score_job_record(job_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Processes a single job record, calculates eligibility, track, and scores."""
    raw_jd = job_dict.get("raw_jd_text", "")
    title = job_dict.get("title", "")
    company = job_dict.get("company", "")

    is_eligible, knockout_reason, sponsorship_signal = evaluate_knockouts(raw_jd)
    track = assign_archetype_track(raw_jd, title)
    score_data = extract_skills_and_score(raw_jd, track, company)

    match_score = score_data["match_score"] if is_eligible else min(score_data["match_score"], 45)

    pipeline_status = "MUST_APPLY" if (is_eligible and match_score >= 75) else ("RULED_OUT" if not is_eligible else "DISCOVERED")

    updated = {
        "id": job_dict["id"],
        "canonical_hash": job_dict["canonical_hash"],
        "company": company,
        "title": title,
        "location": job_dict.get("location"),
        "workplace_type": job_dict.get("workplace_type"),
        "ats_platform": job_dict.get("ats_platform"),
        "job_url": job_dict.get("job_url"),
        "raw_jd_text": raw_jd,
        "is_eligible": is_eligible,
        "knockout_reason": knockout_reason,
        "sponsorship_signal": sponsorship_signal,
        "assigned_track": track,
        "match_score": match_score,
        "technical_score": score_data["technical_score"],
        "domain_score": score_data["domain_score"],
        "experience_score": score_data["experience_score"],
        "soft_skill_score": score_data["soft_skill_score"],
        "extracted_jd_skills_json": json.dumps(score_data["matched_skills"] + score_data["missing_gaps"]),
        "matched_skills_json": json.dumps(score_data["matched_skills"]),
        "missing_gaps_json": json.dumps(score_data["missing_gaps"]),
        "pipeline_status": pipeline_status,
    }
    upsert_job(updated)
    return updated


def score_all_jobs(job_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Scores a specific job or all discovered jobs and updates smartapply.db and Excel."""
    init_db()
    if job_id:
        job = get_job_by_id(job_id)
        if not job:
            raise ValueError(f"Job with id {job_id} not found in smartapply.db")
        scored_jobs = [score_job_record(job)]
    else:
        all_jobs = list_jobs()
        scored_jobs = [score_job_record(j) for j in all_jobs]

    # Mirror updates to SmartApply_Master_Pipeline.xlsx
    sync_db_to_excel()
    return scored_jobs


def main():
    parser = argparse.ArgumentParser(description="SmartApplyAI v2 Scorer Harness")
    parser.add_argument("--job-id", type=int, default=None, help="Specific job ID to score")
    parser.add_argument("--all", action="store_true", help="Score all jobs in smartapply.db")
    args = parser.parse_args()

    print(f"[*] Running Scorer Harness (job_id={args.job_id}, all={args.all})...")
    scored = score_all_jobs(job_id=args.job_id)
    print(f"[+] Scored {len(scored)} jobs. Top scored jobs:\n")
    for s in sorted(scored, key=lambda x: x["match_score"], reverse=True):
        elig_badge = "[ELIGIBLE]" if s["is_eligible"] else f"[KNOCKOUT: {s['knockout_reason']}]"
        status_badge = f"({s['pipeline_status']})"
        print(f"  - [{s['id']}] {s['match_score']}% | {s['assigned_track']} | {s['company']} — {s['title']} {elig_badge} {status_badge}")
        if s["is_eligible"] and s["match_score"] >= 75:
            matched = json.loads(s["matched_skills_json"])
            print(f"      Matched: {', '.join(matched[:8])}")


if __name__ == "__main__":
    main()
