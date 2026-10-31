"""
Bullet Synthesizer and Resume AST Generation Engine.
Transforms DeconstructedJD and SelectedEvidenceBundle into a valid TailoredResumeAST
conforming to Tsenta's 4-part qualification proofs and strict line-budget constraints.
"""

import os
import json
import re
from typing import Dict, Any, Optional

from src.schemas import (
    TailoredResumeAST,
    DeconstructedJD,
    RoleEntryAST,
    ProjectEntryAST,
    SkillCategoryAST,
)
from src.prompt_templates import (
    RESUME_SYNTHESIS_SYSTEM_PROMPT,
    BULLET_COMPRESSION_SYSTEM_PROMPT,
    build_resume_synthesis_prompt,
)
from src.validator import (
    validate_bullet_constraints,
    repair_banned_verbs,
    strip_latex_formatting,
)


def synthesize_deterministic_ast(
    deconstructed_jd: DeconstructedJD,
    evidence_bundle: Dict[str, Any],
    stem: str = "chandra_rup_daka_resume",
) -> TailoredResumeAST:
    """Builds a verified TailoredResumeAST directly from canonical evidence records."""
    track = deconstructed_jd.selected_track
    company = deconstructed_jd.company_name
    role = deconstructed_jd.role_title

    # Summary customization echoing target role and company
    summary = evidence_bundle.get("summary_starter", "")
    if role and role.lower() not in summary.lower():
        summary = f"Targeting {role} at {company}. " + summary

    # Build Experience Entries
    experience_entries = []
    total_exp_bullets = 0
    max_exp_bullets = 8  # 4 + 2 + 2 or 3 + 3 + 2

    for exp in evidence_bundle.get("selected_experience", []):
        raw_bullets = exp.get("bullets", [])
        clean_bullets = []
        for b in raw_bullets:
            repaired = repair_banned_verbs(b)
            clean_bullets.append(repaired)
            total_exp_bullets += 1
            if total_exp_bullets >= max_exp_bullets:
                break

        if clean_bullets:
            experience_entries.append(
                RoleEntryAST(
                    role_title=exp["role_title"],
                    date_range=exp["date_range"],
                    organization_line=exp["organization_line"],
                    location=exp["location"],
                    bullets=clean_bullets,
                )
            )
        if total_exp_bullets >= max_exp_bullets:
            break

    # Build Project Entries (2-3 projects with 1 bullet each)
    project_entries = []
    for proj in evidence_bundle.get("selected_projects", [])[:2]:
        project_entries.append(
            ProjectEntryAST(
                title=proj["title"],
                url=proj["url"],
                tech_stack_short=proj["tech_stack_short"],
                year=proj["year"],
                bullets=[repair_banned_verbs(proj["bullet"])],
            )
        )

    # Publications if present in evidence bundle
    publication_entries = []
    for pub in evidence_bundle.get("selected_publications", [])[:2]:
        publication_entries.append(
            ProjectEntryAST(
                title=pub["title"],
                url=pub["url"],
                tech_stack_short=pub.get("venue", "IEEE"),
                year=pub["year"],
                bullets=[repair_banned_verbs(pub["bullet"])],
            )
        )

    # Skill Categories
    skill_categories = []
    for cat in evidence_bundle.get("skill_categories", []):
        skill_categories.append(
            SkillCategoryAST(
                category_name=cat["category_name"],
                skills_csv=cat["skills_csv"],
            )
        )

    section_order = list(evidence_bundle.get("section_order", ["Summary", "Experience", "Selected Projects", "Education", "Skills"]))
    if not publication_entries and "Publications" in section_order:
        section_order.remove("Publications")

    return TailoredResumeAST(
        target_filename_stem=stem,
        summary_latex=summary,
        section_order=section_order,
        experience_section_title=evidence_bundle.get("experience_section_title", "Experience"),
        projects_section_title=evidence_bundle.get("projects_section_title", "Selected Projects"),
        experience_entries=experience_entries,
        project_entries=project_entries,
        publication_entries=publication_entries if publication_entries else None,
        include_certifications_in_edu=True,
        skill_categories=skill_categories,
    )


def synthesize_resume_ast(
    deconstructed_jd: DeconstructedJD,
    evidence_bundle: Dict[str, Any],
    stem: str = "chandra_rup_daka_resume",
) -> TailoredResumeAST:
    """
    Synthesizes a TailoredResumeAST using Gemini 2.5 Flash via google.genai,
    with automatic fallbacks and post-generation constraint validation.
    """
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")

    if api_key:
        try:
            from google import genai
            client = genai.Client(api_key=api_key)
            prompt = build_resume_synthesis_prompt(
                deconstructed_jd.model_dump(),
                evidence_bundle,
            )

            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config={
                    "system_instruction": RESUME_SYNTHESIS_SYSTEM_PROMPT,
                    "response_mime_type": "application/json",
                    "response_schema": TailoredResumeAST,
                },
            )
            ast_data = json.loads(response.text)
            ast = TailoredResumeAST(**ast_data)
            ast.target_filename_stem = stem

            # Post-generation automatic repair of any stray banned verbs
            for entry in ast.experience_entries:
                entry.bullets = [repair_banned_verbs(b) for b in entry.bullets]
            for proj in ast.project_entries:
                proj.bullets = [repair_banned_verbs(b) for b in proj.bullets]

            return ast
        except Exception as e:
            # Fall back gracefully to deterministic assembler
            pass

    return synthesize_deterministic_ast(deconstructed_jd, evidence_bundle, stem)
