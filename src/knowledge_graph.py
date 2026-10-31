"""
Candidate Knowledge Graph Loader and Evidence Retrieval Engine.
Provides deterministic evidence retrieval from data/career_knowledge.json,
enforcing domain archetype routing and employer-specific context overrides.
"""

import os
import json
from typing import Dict, Any, List, Optional
from src.schemas import DeconstructedJD, ArchetypeTrack


def load_candidate_knowledge_graph(path: str = "data/career_knowledge.json") -> Dict[str, Any]:
    """Reads data/career_knowledge.json containing Chandra Rup Daka's verified career graph."""
    if not os.path.isabs(path):
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidate_path = os.path.join(base_dir, path)
        if os.path.exists(candidate_path):
            path = candidate_path

    if not os.path.exists(path):
        raise FileNotFoundError(f"Knowledge graph file not found at: {path}")

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def select_evidence_bundle(
    deconstructed_jd: DeconstructedJD,
    kg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Selects evidence bundle based on target archetype track, JD keywords, and company-specific overrides.
    Returns:
        - section_order
        - experience_section_title
        - projects_section_title
        - selected_experience_nodes (ordered and framed)
        - selected_project_nodes
        - selected_publication_nodes
        - summary_starter
        - skill_categories
        - locked_metrics
    """
    if kg is None:
        kg = load_candidate_knowledge_graph()

    cand = kg.get("candidate", {})
    track = deconstructed_jd.selected_track
    company = (deconstructed_jd.company_name or "").lower()
    role = (deconstructed_jd.role_title or "").lower()
    jd_skills = [s.lower() for s in deconstructed_jd.top_hard_skills]

    # Archetype default config
    archetype_configs = cand.get("archetype_routing", {})
    track_cfg = archetype_configs.get(track, archetype_configs.get("agentic_swe", {}))

    section_order = list(track_cfg.get("section_order", ["Summary", "Experience", "Selected Projects", "Education", "Skills"]))
    exp_title = "Experience"
    proj_title = "Selected Projects"

    if track == "bioinformatics_ai":
        exp_title = "Biomedical Research & Experience"
        proj_title = "Selected Projects & Publications"
    elif track == "core_aiml":
        proj_title = "Selected Machine Learning Projects"
    elif track == "data_analyst_ds":
        proj_title = "Selected Data Analytics & Decision Science Projects"

    # Identify employer overrides
    is_humana = "humana" in company
    is_fintech = any(k in company or k in role for k in ["fintech", "charles river", "state street", "bloomberg", "trading"])
    is_hr_tech = any("hr" in s or "employee" in s or "workday" in s for s in jd_skills)

    # Order experience entries
    all_exp = {e["id"]: e for e in cand.get("experience", [])}
    exp_order = list(track_cfg.get("lead_experience_ids", ["exp_accenture", "exp_bauer", "exp_humana", "exp_bme_roysam"]))

    # Apply employer-specific overrides
    if is_humana and "exp_humana" in all_exp:
        # Elevate Humana Institute to #2 position right below Accenture or #1
        exp_order = ["exp_humana", "exp_accenture", "exp_bauer", "exp_bme_roysam"]
    elif is_hr_tech and "exp_bauer" in all_exp:
        exp_order = ["exp_bauer", "exp_accenture", "exp_humana", "exp_bme_roysam"]

    selected_experience = []
    for exp_id in exp_order:
        if exp_id in all_exp:
            exp_data = all_exp[exp_id]
            framing_bullets = exp_data.get("framing_vectors", {}).get(track, [])
            if not framing_bullets:
                framing_bullets = exp_data.get("framing_vectors", {}).get("agentic_swe", [])

            selected_experience.append({
                "id": exp_data["id"],
                "role_title": exp_data["role"],
                "date_range": exp_data["dates"],
                "organization_line": exp_data["organization"],
                "location": exp_data["location"],
                "locked_metrics": exp_data["locked_metrics"],
                "bullets": framing_bullets,
            })

    # Select project entries
    all_proj = {p["id"]: p for p in cand.get("projects", [])}
    default_proj_ids = track_cfg.get("default_project_ids", ["proj_smartapply", "proj_hush", "proj_gpt2"])

    selected_projects = []
    for pid in default_proj_ids:
        if pid in all_proj:
            selected_projects.append(all_proj[pid])

    # Select publications
    selected_pubs = []
    if track in ["bioinformatics_ai", "core_aiml"] or "Publications" in section_order:
        selected_pubs = cand.get("publications", [])

    # Summary template
    summary_text = track_cfg.get("summary_template", "")
    if is_humana:
        summary_text = (
            "Prior Humana Institute Healthcare AI Intern and AI Systems Engineer with 2+ years of production experience "
            "building clinical agentic workflows, voice-first companion agents, and enterprise REST microservices in Python and TypeScript. "
            "Experienced collaborating with clinical RNs, MDs, and enterprise stakeholders. "
            "M.S. in Engineering Data Science & AI candidate at University of Houston (GPA: 3.9/4.0)."
        )

    # Base skill categories
    master_skills = cand.get("skills_master", {})
    skill_categories = [
        {"category_name": "Languages", "skills_csv": ", ".join(master_skills.get("languages", []))},
        {"category_name": "Agentic AI & LLMs", "skills_csv": ", ".join(master_skills.get("agentic_and_llms", []))},
        {"category_name": "AI/ML Frameworks", "skills_csv": ", ".join(master_skills.get("ai_ml_frameworks", []))},
        {"category_name": "Cloud & DevOps", "skills_csv": ", ".join(master_skills.get("cloud_devops", []))},
        {"category_name": "Databases & Storage", "skills_csv": ", ".join(master_skills.get("databases_storage", []))},
        {"category_name": "Web & Microservices", "skills_csv": ", ".join(master_skills.get("web_fullstack", []))},
    ]

    return {
        "candidate_identity": cand.get("identity", {}),
        "education": cand.get("education", []),
        "certifications": cand.get("certifications", []),
        "section_order": section_order,
        "experience_section_title": exp_title,
        "projects_section_title": proj_title,
        "selected_experience": selected_experience,
        "selected_projects": selected_projects,
        "selected_publications": selected_pubs,
        "summary_starter": summary_text,
        "skill_categories": skill_categories,
        "base_language_dependencies": cand.get("base_language_dependencies", {}),
    }
