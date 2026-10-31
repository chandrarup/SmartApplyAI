"""
Pydantic Schemas for the Antigravity Autonomous Resume Engine.
Defines data contracts for JD deconstruction, Resume AST representation,
compilation verification, and fit auditing.
"""

from typing import List, Literal, Optional, Dict, Any
from pydantic import BaseModel, Field

ArchetypeTrack = Literal[
    "agentic_swe",
    "bioinformatics_ai",
    "core_aiml",
    "data_analyst_ds",
    "forward_deployed_engineer",
]

SectionName = Literal[
    "Summary",
    "Education",
    "Experience",
    "Selected Projects",
    "Publications",
    "Skills",
]


class DeconstructedJD(BaseModel):
    """Structured extraction of requirements and metadata from a raw Job Description."""
    company_name: str = Field(description="Name of the hiring organization")
    role_title: str = Field(description="Official job title")
    selected_track: ArchetypeTrack = Field(
        description="Best-fit archetype track based on JD trigger keywords and core responsibilities"
    )
    top_hard_skills: List[str] = Field(
        default_factory=list,
        description="Top 10-15 exact technical terms and frameworks from JD ranked by priority",
    )
    implied_base_languages: List[str] = Field(
        default_factory=list,
        description="Base programming languages required by JD frameworks (e.g. Python, TypeScript, JavaScript, SQL, Java, R)",
    )
    soft_skills_and_workflow: List[str] = Field(
        default_factory=list,
        description="Agile/Scrum, stakeholder presentations, cross-functional collaboration, mentoring, etc.",
    )
    true_gaps: List[str] = Field(
        default_factory=list,
        description="Required JD skills that are absent from Chandra's canonical knowledge graph",
    )
    knockout_flags: List[str] = Field(
        default_factory=list,
        description="Any hard knockout filters found in JD (e.g. US Citizenship Required, Active Secret Clearance)",
    )
    archetype_justification: str = Field(
        default="",
        description="Plain-English explanation of why the selected track was chosen",
    )


class RoleEntryAST(BaseModel):
    """Represents a single employment or lab appointment entry in the resume AST."""
    role_title: str = Field(description="E.g. Generative AI Engineer")
    date_range: str = Field(description="E.g. Aug 2023 -- Jul 2025")
    organization_line: str = Field(description="E.g. Accenture — GenWizard Platform")
    location: str = Field(description="E.g. Chennai, India or Houston, TX")
    bullets: List[str] = Field(
        description="List of LaTeX-formatted bullets (165-230 characters each) proving WHAT+HOW+WHY with selective \\textbf{}"
    )


class ProjectEntryAST(BaseModel):
    """Represents a project or publication entry in the resume AST."""
    title: str = Field(description="Project title, e.g. SmartApplyAI — Multi-LLM Agentic Browser Copilot")
    url: str = Field(description="Target hyperlink URL")
    tech_stack_short: str = Field(
        description="Concise stack string so Title | Stack + Year fits on 1 single line (<= 92 chars total)"
    )
    year: str = Field(description="Year or date, e.g. 2026")
    bullets: List[str] = Field(
        description="1-2 LaTeX-formatted bullets (165-230 chars each) proving WHAT+HOW+WHY"
    )


class SkillCategoryAST(BaseModel):
    """A category block in the bottom Skills section."""
    category_name: str = Field(description="E.g. Languages, AI/ML & Agentic, Cloud & DevOps, Databases")
    skills_csv: str = Field(description="Comma-separated list of skills")


class TailoredResumeAST(BaseModel):
    """Complete Abstract Syntax Tree representing a tailored 1-page LaTeX resume."""
    target_filename_stem: str = Field(
        default="chandra_rup_daka_resume",
        description="Output stem name for generated .tex, .pdf, and .png files",
    )
    summary_latex: str = Field(
        description="3-line LaTeX summary (340-410 plain characters) echoing exact JD role title and top skills"
    )
    section_order: List[SectionName] = Field(
        default=["Summary", "Experience", "Selected Projects", "Education", "Skills"],
        description="Ordered list of top-level sections enforcing archetype prioritization",
    )
    experience_section_title: str = Field(
        default="Experience",
        description="Heading title for the experience section (e.g. Experience or Biomedical Research & Experience)",
    )
    projects_section_title: str = Field(
        default="Selected Projects",
        description="Heading title for the projects section",
    )
    experience_entries: List[RoleEntryAST] = Field(
        description="Chronological role entries tailored to the target track"
    )
    project_entries: List[ProjectEntryAST] = Field(
        description="Top 2-3 project entries selected for the target track"
    )
    publication_entries: Optional[List[ProjectEntryAST]] = Field(
        default=None,
        description="Optional publication entries (typically included for Core AI/ML or Bioinformatics tracks)",
    )
    include_certifications_in_edu: bool = Field(
        default=True,
        description="Whether to include Azure/AWS certifications under Education",
    )
    skill_categories: List[SkillCategoryAST] = Field(
        description="Grouped technical skills with bijective grounding to bullet proofs"
    )


class QualificationProof(BaseModel):
    """Tsenta 4-part qualification proof decomposition."""
    jd_term: str
    what: str
    how: str
    why: str
    where: str


class FitAuditReport(BaseModel):
    """Audit report analyzing alignment between the tailored resume and target JD."""
    company_name: str
    role_title: str
    selected_track: ArchetypeTrack
    strong_matches: List[str] = Field(default_factory=list)
    reframed_matches: List[str] = Field(default_factory=list)
    true_gaps: List[str] = Field(default_factory=list)
    ats_safety_check: bool = Field(default=True)
    qualification_proofs: List[QualificationProof] = Field(default_factory=list)
    overall_fit_score: int = Field(default=85, ge=0, le=100)
    notes: str = Field(default="")


class CompilationResult(BaseModel):
    """Metrics and verification outcome from the closed-loop LaTeX compiler."""
    status: Literal["success", "spillover_error", "compile_error", "validation_error"]
    level_used: int = Field(description="Compression level index (0..3)")
    pages: int = Field(description="Total page count reported by pdfinfo")
    vertical_fill_pct: float = Field(description="Vertical page density percentage from PIL scan")
    max_overfull_pt: float = Field(description="Maximum Overfull \\hbox points from LaTeX log")
    pdf_path: str = Field(description="Absolute or relative path to compiled .pdf")
    tex_path: str = Field(description="Absolute or relative path to generated .tex")
    png_preview_path: Optional[str] = Field(default=None, description="Path to 150-DPI rasterized PNG")
    spillover_text: Optional[str] = Field(default=None, description="Spillover lines if pages > 1")
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


class GenerateResumeRequest(BaseModel):
    """Request payload for resume synthesis."""
    raw_jd: str = Field(description="Raw text of the target job description")
    company_name: Optional[str] = Field(default=None, description="Optional override for company name")
    role_title: Optional[str] = Field(default=None, description="Optional override for target role title")
    track_override: Optional[ArchetypeTrack] = Field(default=None, description="Manual archetype selection")
    output_stem: Optional[str] = Field(default=None, description="Custom filename stem for outputs")


class GenerateResumeResponse(BaseModel):
    """Response payload returned by the resume synthesis engine."""
    success: bool
    deconstructed_jd: Optional[DeconstructedJD] = None
    compilation_result: Optional[CompilationResult] = None
    fit_audit: Optional[FitAuditReport] = None
    pdf_path: Optional[str] = None
    tex_path: Optional[str] = None
    png_preview_path: Optional[str] = None
    error: Optional[str] = None
