"""
Job Description Analyzer and Requirements Deconstruction Engine.
Deconstructs raw JD text into a structured DeconstructedJD schema using Google Gemini
with an offline deterministic rule-based fallback.
"""

import os
import re
import json
from typing import Optional, Dict, Any, List

from src.schemas import DeconstructedJD, ArchetypeTrack
from src.prompt_templates import (
    JD_DECONSTRUCTION_SYSTEM_PROMPT,
    build_jd_deconstruction_prompt,
    BASE_LANGUAGE_DEPENDENCY_TABLE,
)
from src.knowledge_graph import load_candidate_knowledge_graph


def rule_based_jd_deconstruction(raw_jd: str, company_name: Optional[str] = None, role_title: Optional[str] = None) -> DeconstructedJD:
    """Deterministic fallback extractor when LLM API is unavailable."""
    text_lower = raw_jd.lower()
    kg = load_candidate_knowledge_graph()
    cand = kg.get("candidate", {})

    # 1. Company Name & Title extraction
    if not company_name:
        lines = [line.strip() for line in raw_jd.splitlines() if line.strip()]
        company_name = "Target Company"
        for l in lines[:5]:
            if any(term in l.lower() for term in ["at ", "inc", "corp", "technologies", "health", "labs", "company"]):
                company_name = l.strip()
                break

    if not role_title:
        role_title = "AI Software Engineer"
        for l in lines[:5]:
            if any(term in l.lower() for term in ["engineer", "developer", "scientist", "analyst", "fellow"]):
                role_title = l.strip()
                break

    # 2. Archetype Track Classification
    routing = cand.get("archetype_routing", {})
    track_scores: Dict[ArchetypeTrack, int] = {
        "agentic_swe": 0,
        "forward_deployed_engineer": 0,
        "core_aiml": 0,
        "bioinformatics_ai": 0,
        "data_analyst_ds": 0,
    }

    for track, config in routing.items():
        for trigger in config.get("triggers", []):
            if trigger in text_lower:
                track_scores[track] += 1

    selected_track: ArchetypeTrack = max(track_scores, key=track_scores.get)
    if track_scores[selected_track] == 0:
        selected_track = "agentic_swe"

    # 3. Extract Top Hard Skills
    master_skills = cand.get("skills_master", {})
    all_known_skills = []
    for cat, slist in master_skills.items():
        all_known_skills.extend(slist)

    found_skills = []
    for skill in all_known_skills:
        clean_skill = re.sub(r"\(.*\)", "", skill).strip().lower()
        if clean_skill and re.search(rf"\b{re.escape(clean_skill)}\b", text_lower):
            found_skills.append(skill)

    # 4. Implied Base Languages
    implied_languages = set()
    for skill in found_skills:
        for framework, base_langs in BASE_LANGUAGE_DEPENDENCY_TABLE.items():
            if framework.lower() in skill.lower():
                implied_languages.update(base_langs)

    # 5. Knockout Checks
    knockout_flags = []
    if "us citizen" in text_lower or "u.s. citizen" in text_lower or "citizenship required" in text_lower:
        knockout_flags.append("US Citizenship Required")
    if "clearance" in text_lower and ("secret" in text_lower or "ts/sci" in text_lower):
        knockout_flags.append("Active Security Clearance Required")

    # 6. Gaps identification (technologies in JD not in Chandra's inventory)
    unmatched_techs = []
    sample_gaps = ["Rust", "Golang", "Scala", "Solidity", "COBOL 2000", "Kubernetes Operator SDK"]
    for g in sample_gaps:
        if g.lower() in text_lower and g not in found_skills:
            unmatched_techs.append(g)

    return DeconstructedJD(
        company_name=company_name,
        role_title=role_title,
        selected_track=selected_track,
        top_hard_skills=found_skills[:12],
        implied_base_languages=list(implied_languages),
        soft_skills_and_workflow=["Agile/Scrum", "Mentoring junior engineers", "Stakeholder communications"],
        true_gaps=unmatched_techs,
        knockout_flags=knockout_flags,
        archetype_justification=f"Matched track {selected_track} with trigger keywords in job description.",
    )


def deconstruct_jd(
    raw_jd: str,
    company_name: Optional[str] = None,
    role_title: Optional[str] = None,
    track_override: Optional[ArchetypeTrack] = None,
) -> DeconstructedJD:
    """
    Deconstructs raw JD using Gemini 2.5 Flash via google.genai,
    falling back to rule-based analysis if API key is not configured.
    """
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")

    if api_key:
        try:
            from google import genai
            client = genai.Client(api_key=api_key)
            prompt = build_jd_deconstruction_prompt(raw_jd)
            if company_name:
                prompt += f"\nNote: The user explicitly specifies the company name is: '{company_name}'."
            if role_title:
                prompt += f"\nNote: The user explicitly specifies the target role title is: '{role_title}'."
            if track_override:
                prompt += f"\nNote: The user explicitly overrides the selected archetype track to: '{track_override}'."

            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config={
                    "system_instruction": JD_DECONSTRUCTION_SYSTEM_PROMPT,
                    "response_mime_type": "application/json",
                    "response_schema": DeconstructedJD,
                },
            )
            data = json.loads(response.text)
            deconstructed = DeconstructedJD(**data)
            if company_name:
                deconstructed.company_name = company_name
            if role_title:
                deconstructed.role_title = role_title
            if track_override:
                deconstructed.selected_track = track_override
            return deconstructed
        except Exception as e:
            # Fall back gracefully to rule-based extractor
            pass

    # Deterministic fallback
    result = rule_based_jd_deconstruction(raw_jd, company_name, role_title)
    if track_override:
        result.selected_track = track_override
    return result
