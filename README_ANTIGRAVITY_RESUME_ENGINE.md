# Antigravity Autonomous Resume Engine for Chandra Rup Daka

An autonomous, truth-grounded qualification compiler and closed-loop single-page LaTeX resume synthesis engine built specifically for **Chandra Rup Daka**.

Based on empirical recruitment research and the formal specifications in:
- `docs-new/chandra_rup_daka_autonomous_resume_engine_whitepaper.md`
- `docs-new/chandra_rup_daka_resume_engine_tools_and_technologies.md`

---

## 1. System Architecture

The engine implements a 6-stage deterministic pipeline:

```
Raw JD (Text or File)
        │
        ▼
[Stage 1: Ingestion & Sanitation]
        │
        ▼
[Stage 2: Requirements Deconstruction (`src/jd_analyzer.py`)]
   ├── Archetype Track Classification (Agentic SWE, Core AI/ML, Bioinformatics, FDE, Data Analyst)
   ├── Top 10-15 Hard Skills Ranking
   ├── Deterministic Base Language Expansion (FastAPI -> Python, Next.js -> TypeScript/JS, etc.)
   └── Knockout Filtering (Citizenship, Clearance, Degree window)
        │
        ▼
[Stage 3: Evidence Retrieval (`src/knowledge_graph.py`)]
   ├── Truth-grounded retrieval from `data/career_knowledge.json`
   ├── Archetype section ordering ("Hamburger before Hot Dog")
   └── Employer-specific contextual overrides (e.g., Humana, FinTech, HR Tech)
        │
        ▼
[Stage 4: Qualification AST Synthesis (`src/bullet_synthesizer.py`)]
   ├── Tsenta 4-part qualification proof: WHAT + HOW + WHY + WHERE
   ├── Active engineering verbs only (banned slop verbs eliminated)
   └── Plain-text budget: 165–230 chars per bullet (strictly 1.5–2 rendered lines)
        │
        ▼
[Stage 5: Closed-Loop Compilation (`src/latex_compiler.py`)]
   ├── Two-pass pdflatex compilation in /tmp
   ├── Poppler `pdfinfo` verification (Strictly Pages == 1)
   ├── TeX log parser (Overfull \hbox <= 2.0 pt)
   ├── Poppler `pdftoppm` 150-DPI rasterization
   └── Pillow RGB vertical page fill scanner (Target: 90%–98.5%)
        │
        ▼
[Stage 6: Compliance & Fit Audit (`src/fit_auditor.py`)]
   ├── Strong Matches & Reframed Matches
   ├── Honest Gaps Identification
   └── Markdown audit report generation (`output/<stem>_audit.md`)
```

---

## 2. Repository Layout

```
SmartApplyAI/
├── data/
│   └── career_knowledge.json          # Canonical verified inventory for Chandra Rup Daka
├── src/
│   ├── schemas.py                     # Pydantic data contracts (AST, JD, Audit, Compilation)
│   ├── prompt_templates.py            # System prompts, Tsenta qualification rules, banned verb checks
│   ├── knowledge_graph.py             # Knowledge graph loader & evidence bundle selector
│   ├── jd_analyzer.py                 # Gemini 2.5 Flash deconstruction with rule-based fallback
│   ├── bullet_synthesizer.py          # Qualification proof synthesizer & repair passes
│   ├── validator.py                   # Character length, base language, and bijective grounding validator
│   ├── latex_compiler.py              # 5-tier closed-loop pdflatex + Poppler + Pillow compiler
│   ├── fit_auditor.py                 # Fit scoring and Markdown audit reporter
│   ├── pipeline.py                    # End-to-end orchestrator
│   ├── api.py                         # FastAPI server exposing REST endpoints
│   └── cli.py                         # Command-line interface
├── cli.py                             # Root CLI entrypoint
├── tests/
│   ├── test_antigravity_resume_engine.py  # Unit & integration test suite (100% passing)
│   ├── test_jd_agentic.txt            # Real-world Agentic AI JD
│   └── test_jd_bioinformatics.txt     # Real-world Spatial Omics JD
└── output/                            # Compiled .tex, 1-page .pdf, 150-DPI .png, and _audit.md
```

---

## 3. Quickstart & CLI Usage

### Basic CLI Run from Job Description File
```bash
python cli.py --jd-file tests/test_jd_agentic.txt --company "Anthropic" --title "Senior Agentic AI Systems Engineer"
```

### Direct Pasted Text Run
```bash
python cli.py --jd-text "We are seeking a Bioinformatics Scientist with Scanpy and Seurat..." --company "10x Genomics"
```

### Manual Track Override
```bash
python cli.py --jd-file my_jd.txt --track core_aiml --out-dir output/ --stem chandra_core_ml
```

Options:
- `--jd-file`, `-f`: Path to job description text file.
- `--jd-text`, `-t`: Raw job description text string.
- `--company`, `-c`: Company name override.
- `--title`, `-r`: Role title override.
- `--track`: `agentic_swe`, `forward_deployed_engineer`, `core_aiml`, `bioinformatics_ai`, `data_analyst_ds`.
- `--out-dir`, `-o`: Directory for output artifacts (default: `output`).
- `--stem`, `-s`: Output filename prefix.
- `--open-pdf`: Opens the compiled PDF upon success (macOS).

---

## 4. FastAPI REST Server

### Start the Server
```bash
uvicorn src.api:app --host 0.0.0.0 --port 8000 --reload
```

### API Endpoints
- `GET /api/health`: System health check verifying TeX Live, Poppler utilities, and Gemini API.
- `GET /api/candidate-knowledge`: Returns Chandra's canonical experience graph and verified metrics.
- `POST /api/generate-resume`: Primary generation endpoint accepting `GenerateResumeRequest`.
- `POST /api/verify-latex`: Compiles and verifies any custom `TailoredResumeAST`.
- `GET /api/download/{filename}`: Downloads compiled `.pdf`, `.tex`, `.png`, or `.md` files.
- `POST /api/tailor-v2`: Chrome extension adapter compatible with SmartApplyAI.

### Sample API Request
```bash
curl -X POST "http://localhost:8000/api/generate-resume" \
     -H "Content-Type: application/json" \
     -d '{
       "raw_jd": "Looking for an Agentic AI Engineer with LangGraph and FastAPI experience.",
       "company_name": "OpenAI",
       "role_title": "AI Systems Engineer",
       "track_override": "agentic_swe"
     }'
```

---

## 5. Test Suite Verification

Run the full pytest suite:
```bash
PYTHONPATH=. pytest tests/test_antigravity_resume_engine.py -v
```

All 7 test suites verify:
1. `test_knowledge_graph_structure`: Verifies canonical records, locked metrics, and tracks.
2. `test_evidence_bundle_selection`: Tests archetype routing and section ordering.
3. `test_humana_company_override`: Confirms employer-specific dynamic ranking overrides.
4. `test_strip_latex_formatting`: Validates plain-text character extraction.
5. `test_bullet_constraints_validator`: Validates 165–230 char budget and anti-slop verb check.
6. `test_latex_compiler_single_page`: Compiles vector PDF, checks `Pages == 1`, runs `pdftoppm`, and verifies Pillow vertical fill.
7. `test_fastapi_endpoints`: Verifies all REST endpoints and PDF download streams.
