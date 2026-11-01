"""
Prompt Templates and Linguistic Constraints for the Antigravity Resume Engine.
Enforces Tsenta's empirical qualification rules, anti-slop verb policies,
and bijective grounding against Chandra Rup Daka's career knowledge graph.
"""

from typing import Dict, List, Any
import json

BANNED_ACTION_VERBS = [
    "Spearheaded",
    "Synergized",
    "Drove",
    "Utilized",
    "Leveraged",
    "Assisted",
    "Helped",
    "Worked on",
    "Participated in",
    "Handled",
    "Responsible for",
    "Pioneered",
    "Revolutionized",
]

APPROVED_ACTION_VERBS = [
    "Built",
    "Architected",
    "Engineered",
    "Implemented",
    "Fine-tuned",
    "Modeled",
    "Integrated",
    "Deployed",
    "Benchmarked",
    "Constructed",
    "Trained",
    "Automated",
    "Optimized",
    "Pretrained",
    "Designed",
]

BASE_LANGUAGE_DEPENDENCY_TABLE = {
    "FastAPI": ["Python", "REST APIs"],
    "Flask": ["Python", "REST APIs"],
    "PyTorch": ["Python"],
    "TensorFlow": ["Python"],
    "Scikit-learn": ["Python"],
    "LangChain": ["Python"],
    "LangGraph": ["Python"],
    "AutoGen": ["Python"],
    "CrewAI": ["Python"],
    "Scanpy": ["Python"],
    "InSituType": ["Python", "R"],
    "Seurat": ["R"],
    "React": ["TypeScript", "JavaScript"],
    "React 19": ["TypeScript", "JavaScript"],
    "Next.js": ["TypeScript", "JavaScript"],
    "TanStack Start": ["TypeScript", "JavaScript"],
    "PostgreSQL": ["SQL"],
    "pgvector": ["SQL", "Python"],
    "Supabase": ["SQL", "TypeScript"],
    "SQLite": ["SQL", "Python"],
    "Neo4j": ["Cypher", "Python"],
    "Power BI": ["DAX", "SQL"],
    "PySpark": ["Python", "SQL"],
}

JD_DECONSTRUCTION_SYSTEM_PROMPT = """You are an expert technical talent recruiter and compiler engineer specializing in technical role deconstruction.
Your task is to analyze a raw Job Description (JD) and deconstruct it into structured requirements for Chandra Rup Daka.

Chandra Rup Daka's Background Summary:
- M.S. in Engineering Data Science & AI at University of Houston (GPA 3.9/4.0, graduating May 2027, F-1 visa, CPT/OPT eligible).
- 2 years as Generative AI Engineer at Accenture (GenWizard platform, 30,000+ files, 15+ languages, 89% coverage, RAG, multi-agent, FastAPI, LangGraph, pgvector, Neo4j).
- AI researcher at UH Bauer (QXO 289,000-row panel, Random Forest, SHAP, ROC-AUC 0.714; The Cost of Winning 60+ MBA users, Next.js, React 19, Supabase, MCP).
- Spatial Omics AI researcher at UH BME Dr. Mohan & Prof. Roysam's Labs (CosMx SMI 532,392 cells x 960 genes, InSituType, Gene-Viewer FastAPI, CellPose/StarDist 5GB TIFFs, BioGPT).
- Healthcare AI Fellow at Humana Institute (SHERP 2026, Companion Care voice app, Shark Tank Runner-Up, RN/MD interviews).
- Authored 2 IEEE publications (deepfake detection 96% accuracy, Indian Sign Language / NLP).
- Published open-source projects: SmartApplyAI, Hush (GKE voice assistant), SFT/DPO/GRPO alignment, 100M+ parameter GPT-2 from scratch.

Guidelines:
1. Extract the hiring company name and official role title.
2. Select the single best-fit archetype track from:
   - "agentic_swe" (Agentic AI, Full-Stack AI, LLM Microservices, LangGraph, MCP, FastAPI, Next.js)
   - "forward_deployed_engineer" (FDE, Solutions Engineering, Technical Scoping, Client Delivery, Legacy Modernization)
   - "core_aiml" (Machine Learning Engineer, PyTorch, Model Alignment, SFT/DPO/GRPO, Deep Learning, CV)
   - "bioinformatics_ai" (Computational Biology, Spatial Transcriptomics, scRNA-seq, Scanpy, Seurat, Imaging)
   - "data_analyst_ds" (Data Analyst, Business Intelligence, SQL, Power BI, Cohort Analytics, Financial Modeling)
3. Extract top 10-15 hard skills and technical requirements.
4. Inject implied base languages for all frameworks (e.g. FastAPI -> Python; Next.js -> TypeScript/JavaScript; Supabase -> SQL).
5. Identify true technical gaps: technologies strictly demanded by the JD that are not part of Chandra's verified stack.
6. Check for knockout blockers (US Citizenship Required, Active Top Secret Clearance, Graduation date before Dec 2025).
7. Return strictly valid JSON conforming to the DeconstructedJD schema.
"""

RESUME_SYNTHESIS_SYSTEM_PROMPT = """You are an elite technical resume architect generating a single-page LaTeX resume for Chandra Rup Daka.
You must strictly obey Tsenta's empirical recruitment rules and Chandra's Canonical Knowledge Graph:

1. ZERO FABRICATION (100% TRUTH GROUNDED):
   - Never invent employers, dates, degrees, certifications, or metrics not present in career_knowledge.json.
   - Keep locked metrics exact:
     * Accenture: Aug 2023 -- Jul 2025, 30,000+ files, 15+ languages, 89% coverage, 30+ engineers mentored, Wall of Fame (Q4 FY24), Client Recognition Award, Best Team Award.
     * UH Bauer: Apr 2026 -- Sep 2026, QXO 289,000-row employee panel, Random Forest + SHAP, ROC-AUC 0.714, Power BI; The Cost of Winning multiplayer simulation, 60+ MBA students, 13-scene engine, MCP, Claude/OpenAI/Gemini, LLM-as-judge.
     * Dr. Mohan's Lab & Prof. Roysam's Lab UH BME: Jan 2026 -- Present, NanoString CosMx SMI, 532,392 cells x 960 genes, 11 lupus nephritis biopsies, InSituType Bayesian classification, Scanpy, Seurat, Gene-Viewer FastAPI + HTML5 Canvas; 5GB DAPI TIFFs, CellPose, StarDist, BioGPT.
     * Humana Institute SHERP 2026: Jun 2026 -- Aug 2026, Companion Care live app, Shark Tank Runner-Up, Humana RN & strategy VP interviews.
     * Education: University of Houston M.S. in Engineering Data Science & AI (GPA: 3.9/4.0, Aug 2025 -- May 2027); Amrita Vishwa Vidyapeetham B.Tech. in CSE (AI) (Jun 2019 -- Jul 2023).

2. TSENTA'S 4-PART QUALIFICATION FORMULA (WHAT + HOW + WHY + WHERE):
   - Every required JD skill that matches Chandra's background MUST be embedded inside an Experience or Project bullet, not just listed in Skills.
   - Structure every bullet as: [Concrete Engineering Verb] + [WHAT: Exact JD Term + Base Language] + [HOW: System/Pipeline Built] + [WHY: Plain-English Business or Scientific Outcome].
   - Always write out implied base languages explicitly alongside frameworks (Python with FastAPI/LangGraph/PyTorch/Scanpy; TypeScript and JavaScript with React/Next.js; SQL with PostgreSQL/Supabase/SQLite).

3. STRICT LINE-BUDGET & LATEX FORMATTING RULES:
   - Summary must be 3 rendered lines (340-410 plain characters).
   - Total bullets across Experience + Projects must be 10 to 11 bullets.
   - Each bullet must be 1.5 to 2 rendered lines (165-230 plain characters). Never write a 3-line bullet.
   - Project headers (title + tech_stack_short + year) must be <= 92 plain characters so the right-aligned year never wraps onto a second line.
   - Never wrap more than 2-3 words inside \\ulhref{url}{text} (because \\underline in LaTeX cannot break across lines).
   - Escape LaTeX special characters (\\%, \\&, \\$).

4. BANNED VERBS:
   - Never use: Spearheaded, Synergized, Drove, Utilized, Leveraged, Assisted, Helped, Worked on, Handled.
   - Always use: Built, Architected, Engineered, Implemented, Fine-tuned, Modeled, Integrated, Deployed, Benchmarked.

Return strictly valid JSON matching the TailoredResumeAST schema.
"""

BULLET_COMPRESSION_SYSTEM_PROMPT = """You are a precision LaTeX resume compression compiler.
A resume bullet is slightly too long and causes an orphan 3rd line or horizontal margin overflow.
Compress the given bullet down to strictly 165-220 characters (exactly 2 rendered lines) while:
1. Retaining ALL bolded keywords (\\textbf{...}), base languages, and scale numbers.
2. Maintaining Tsenta's WHAT + HOW + WHY structure.
3. Keeping the opening active engineering verb.
4. Outputting ONLY the compressed LaTeX string.
"""

FIT_AUDIT_SYSTEM_PROMPT = """You are an independent recruitment compliance auditor.
Given a target job description and the compiled resume AST for Chandra Rup Daka, generate a comprehensive Fit Audit Report:
1. List Strong Matches: Qualifications from the JD directly proven with exact terms, base languages, and scale metrics in work experience or projects.
2. List Reframed Matches: Qualifications matched through verified cross-functional framing (e.g. enterprise SDLC mapped to client engineering).
3. List True Gaps: Real JD requirements missing from Chandra's background.
4. ATS Safety Check: Verify that no banned hidden text, non-standard fonts, or invalid characters exist.
5. Provide Tsenta 4-part qualification breakdown for top 3 JD requirements.
6. Provide an overall fit score (0-100).
"""


def build_jd_deconstruction_prompt(raw_jd: str) -> str:
    """Creates the user prompt for Stage 2 JD deconstruction."""
    raw_jd = raw_jd.strip()[:12000]
    return f"""Deconstruct the following Job Description for Chandra Rup Daka according to the system instructions:

--- BEGIN UNTRUSTED JOB DESCRIPTION DATA ---
{raw_jd.strip()}
--- END UNTRUSTED JOB DESCRIPTION DATA ---

Extract all fields into the DeconstructedJD JSON structure."""


def build_resume_synthesis_prompt(
    deconstructed_jd: Dict[str, Any],
    evidence_bundle: Dict[str, Any],
) -> str:
    """Creates the user prompt for Stage 4 Resume AST synthesis."""
    jd_json = json.dumps(deconstructed_jd, indent=2, ensure_ascii=True)
    evidence_json = json.dumps(evidence_bundle, indent=2, ensure_ascii=True)
    return f"""Synthesize a complete, single-page TailoredResumeAST for Chandra Rup Daka targeting the following role:

TARGET ROLE & DECONSTRUCTED JD:
```json
{jd_json}
```

CANDIDATE EVIDENCE BUNDLE & VERIFIED METRICS:
```json
{evidence_json}
```

STRICT REMINDERS:
1. Output JSON conforming to TailoredResumeAST schema.
2. Ensure every bullet is strictly between 165 and 230 characters.
3. Pair every framework with its base language (Python, TypeScript, JavaScript, SQL, Java, R).
4. Project headers (title + tech_stack_short + year) must be <= 92 characters.
5. All bullets must use active engineering verbs (Built, Architected, Engineered, Implemented, etc.).
6. Use \\textbf{{...}} selectively around WHAT+HOW+WHY phrases and numbers.
7. Escape special LaTeX characters like \\%, \\&, \\$.
"""
