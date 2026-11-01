"""
End-to-End Resume Generation Pipeline for Antigravity Resume Engine.
Coordinates ingestion, JD deconstruction, knowledge graph retrieval, AST synthesis,
constraint validation, closed-loop LaTeX compilation, and fit auditing.
"""

import os
import re
from typing import Optional, Dict, Any

from src.schemas import (
    GenerateResumeRequest,
    GenerateResumeResponse,
    DeconstructedJD,
    TailoredResumeAST,
    CompilationResult,
    FitAuditReport,
    ArchetypeTrack,
)
from src.jd_analyzer import deconstruct_jd
from src.knowledge_graph import load_candidate_knowledge_graph, select_evidence_bundle
from src.bullet_synthesizer import synthesize_resume_ast, synthesize_deterministic_ast
from src.validator import validate_bullet_constraints
from src.latex_compiler import compile_and_verify_single_page
from src.fit_auditor import audit_resume_fit, render_markdown_audit


def generate_tailored_resume(
    raw_jd: str,
    company_name: Optional[str] = None,
    role_title: Optional[str] = None,
    track_override: Optional[ArchetypeTrack] = None,
    output_dir: str = "output",
    output_stem: Optional[str] = None,
) -> GenerateResumeResponse:
    """
    Executes the 6-stage autonomous resume generation pipeline:
    1. Ingestion & Sanitation.
    2. Requirements Deconstruction & Archetype Selection.
    3. Truth-Grounded Evidence Retrieval from career_knowledge.json.
    4. Qualification Proof Bullet Synthesis (WHAT+HOW+WHY+WHERE).
    5. Structural & Linguistic Constraint Validation.
    6. Closed-Loop pdflatex Compilation, Poppler Rasterization & PIL Fill Audit.
    7. Fit & Compliance Report Generation.
    """
    os.makedirs(output_dir, exist_ok=True)

    # 1. Clean input text
    clean_jd = raw_jd.strip()
    if not clean_jd:
        return GenerateResumeResponse(
            success=False,
            error="Input Job Description cannot be empty.",
        )

    # Stem naming
    if not output_stem:
        company_slug = re.sub(r"[^\w]", "_", (company_name or "target_company").lower())
        output_stem = f"chandra_rup_daka_{company_slug}_resume"

    # 2. Stage 2: Deconstruction
    deconstructed = deconstruct_jd(
        clean_jd,
        company_name=company_name,
        role_title=role_title,
        track_override=track_override,
    )

    # 3. Stage 3: Knowledge Graph Evidence Retrieval
    kg = load_candidate_knowledge_graph()
    evidence_bundle = select_evidence_bundle(deconstructed, kg)

    # 4. Stage 4: Synthesis
    ast = synthesize_resume_ast(deconstructed, evidence_bundle, stem=output_stem)

    # 5. Validation Check
    val_report = validate_bullet_constraints(ast, deconstructed)

    # 6. Stage 5: Closed-Loop Compilation & Verification
    compilation_res = compile_and_verify_single_page(
        ast,
        output_dir=output_dir,
        stem_override=output_stem,
    )

    # LLM output is untrusted input: recover from structural/page-budget violations
    # with the deterministic evidence-grounded assembler instead of returning a
    # success-shaped response or an avoidable client error.
    if compilation_res.status == "validation_error":
        ast = synthesize_deterministic_ast(deconstructed, evidence_bundle, stem=output_stem)
        compilation_res = compile_and_verify_single_page(
            ast,
            output_dir=output_dir,
            stem_override=output_stem,
        )

    # If compilation failed or produced >1 page, report details
    if compilation_res.status != "success":
        return GenerateResumeResponse(
            success=False,
            deconstructed_jd=deconstructed,
            compilation_result=compilation_res,
            error=f"Compilation ended with status '{compilation_res.status}': {'; '.join(compilation_res.errors)}",
        )

    # 7. Stage 6: Fit Audit
    fit_audit = audit_resume_fit(ast, deconstructed, compilation_res)

    # Write Markdown audit report to output_dir
    audit_md_path = os.path.join(output_dir, f"{output_stem}_audit.md")
    with open(audit_md_path, "w", encoding="utf-8") as f:
        f.write(render_markdown_audit(fit_audit, compilation_res))

    return GenerateResumeResponse(
        success=True,
        deconstructed_jd=deconstructed,
        compilation_result=compilation_res,
        fit_audit=fit_audit,
        pdf_path=compilation_res.pdf_path,
        tex_path=compilation_res.tex_path,
        png_preview_path=compilation_res.png_preview_path,
    )
