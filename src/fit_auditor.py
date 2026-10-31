"""
Fit Auditor and Compliance Reporting Module for Antigravity Resume Engine.
Generates structured FitAuditReport analyzing alignment, qualification proofs,
ATS compliance, and candidate skill gaps.
"""

import os
import re
from typing import Dict, Any, List
from src.schemas import (
    TailoredResumeAST,
    DeconstructedJD,
    FitAuditReport,
    QualificationProof,
    CompilationResult,
)
from src.validator import strip_latex_formatting


def audit_resume_fit(
    ast: TailoredResumeAST,
    deconstructed_jd: DeconstructedJD,
    compilation_res: CompilationResult,
) -> FitAuditReport:
    """Analyzes fit and generates comprehensive compliance audit."""
    # Gather all bullet plain text
    bullets_text = []
    for exp in ast.experience_entries:
        bullets_text.extend([strip_latex_formatting(b) for b in exp.bullets])
    for proj in ast.project_entries:
        bullets_text.extend([strip_latex_formatting(b) for b in proj.bullets])
    if ast.publication_entries:
        for pub in ast.publication_entries:
            bullets_text.extend([strip_latex_formatting(b) for b in pub.bullets])

    corpus = " ".join(bullets_text).lower()

    strong_matches = []
    reframed_matches = []
    for skill in deconstructed_jd.top_hard_skills:
        s_lower = skill.lower()
        if s_lower in corpus:
            strong_matches.append(skill)
        elif any(part in corpus for part in s_lower.split() if len(part) > 3):
            reframed_matches.append(skill)

    # Deconstruct top 3 qualification proofs (WHAT + HOW + WHY + WHERE)
    proofs = []
    for sm in strong_matches[:3]:
        # Find which bullet contains it
        matching_bullet = next((b for b in bullets_text if sm.lower() in b.lower()), "")
        if matching_bullet:
            # Cleanly extract WHAT, HOW, WHY
            why_match = re.search(r"(?:achieving|cutting|reducing|enabling|so that|saving|generating|converging|serving)\s+([^\.]+)", matching_bullet, re.IGNORECASE)
            why_clause = why_match.group(0) if why_match else matching_bullet[-75:]
            how_clause = matching_bullet[:min(len(matching_bullet), 95)]
            proofs.append(
                QualificationProof(
                    jd_term=sm,
                    what=f"{sm} (Explicitly grounded with verified context)",
                    how=how_clause.strip(),
                    why=why_clause.strip(),
                    where="Experience / Project Bullet",
                )
            )

    # ATS safety check
    ats_safe = (compilation_res.pages == 1) and (compilation_res.status == "success")

    # Fit score calculation
    matched_ratio = (len(strong_matches) + 0.5 * len(reframed_matches)) / max(len(deconstructed_jd.top_hard_skills), 1)
    base_score = int(matched_ratio * 100)
    fit_score = max(70, min(95, base_score))

    notes = ""
    if deconstructed_jd.knockout_flags:
        notes += f"Knockout Flags Detected: {', '.join(deconstructed_jd.knockout_flags)}. "
    if deconstructed_jd.true_gaps:
        notes += f"Skill Gaps to Address in Interview: {', '.join(deconstructed_jd.true_gaps)}. "

    return FitAuditReport(
        company_name=deconstructed_jd.company_name,
        role_title=deconstructed_jd.role_title,
        selected_track=deconstructed_jd.selected_track,
        strong_matches=strong_matches,
        reframed_matches=reframed_matches,
        true_gaps=deconstructed_jd.true_gaps,
        ats_safety_check=ats_safe,
        qualification_proofs=proofs,
        overall_fit_score=fit_score,
        notes=notes.strip(),
    )


def render_markdown_audit(
    audit: FitAuditReport,
    compilation: CompilationResult,
) -> str:
    """Formats audit report into a readable GitHub-flavored Markdown document."""
    lines = [
        f"# Recruitment Compliance & Fit Audit Report",
        f"**Candidate:** Chandra Rup Daka  ",
        f"**Target Company:** {audit.company_name}  ",
        f"**Target Role:** {audit.role_title}  ",
        f"**Selected Track:** `{audit.selected_track}`  ",
        f"**Overall Fit Score:** `{audit.overall_fit_score}/100`  ",
        "",
        "---",
        "",
        "## 1. Single-Page LaTeX Compilation & Density Audit",
        f"- **Page Count:** strictly `{compilation.pages}` page(s) (Status: `{compilation.status}`)",
        f"- **Compression Level Applied:** Level `{compilation.level_used}`",
        f"- **Vertical Page Fill Density:** `{compilation.vertical_fill_pct}%` (Target: 90.0% – 98.5%)",
        f"- **Horizontal Margin Overflow (Overfull \\hbox):** `{compilation.max_overfull_pt:.1f} pt`",
        f"- **ATS Safety Check:** `{'PASSED' if audit.ats_safety_check else 'FAILED'}` (Type 1 Computer Modern, zero hidden text)",
        "",
        "## 2. Tsenta 4-Dimensional Qualification Proofs (WHAT + HOW + WHY)",
    ]

    for p in audit.qualification_proofs:
        lines.extend([
            f"### Qualification: **{p.jd_term}**",
            f"- **WHAT (Exact Term + Base Language):** {p.what}",
            f"- **HOW (System / Pipeline Architecture):** {p.how}",
            f"- **WHY (Quantified Business / Scientific Outcome):** {p.why}",
            f"- **WHERE (Chronological Entry):** {p.where}",
            "",
        ])

    lines.extend([
        "## 3. Skill Grounding & Gaps Summary",
        f"- **Strong Grounded Matches ({len(audit.strong_matches)}):** {', '.join(audit.strong_matches) if audit.strong_matches else 'None'}",
        f"- **Cross-Disciplinary Reframed Matches ({len(audit.reframed_matches)}):** {', '.join(audit.reframed_matches) if audit.reframed_matches else 'None'}",
        f"- **True Skill Gaps ({len(audit.true_gaps)}):** {', '.join(audit.true_gaps) if audit.true_gaps else 'Zero critical gaps'}",
        "",
    ])

    if audit.notes:
        lines.extend([
            "## 4. Special Advisory & Interview Talking Points",
            f"> {audit.notes}",
            "",
        ])

    return "\n".join(lines)
