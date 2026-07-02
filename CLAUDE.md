# SmartApplyAI — CLAUDE.md

## What we're building

**LocalHire Agent** — a personal AI-powered job application assistant for Chandra Rup Daka (AI/ML Engineer, Houston TX). The goal is to match or surpass Jobright.ai across five capabilities:

1. **Autofill** — 1-click form fill for any ATS (Workday, Greenhouse, Lever, iCIMS, Ashby, BambooHR, SmartRecruiters, Taleo, LinkedIn Easy Apply, generic forms)
2. **Resume Customization** — Per-job tailored PDF resume with before/after diff, ATS-optimized, LaTeX-compiled
3. **JD Analysis** — Match score, matched/missing skills, ATS keyword gaps — sourced from JD text only
4. **Cover Letter** — Generated per-job from real profile data
5. **Q&A** — Smart answers to custom application questions

This is a personal tool used during an active job hunt. Every minute saved per application matters.

---

## Architecture

| Layer | Tech | Location |
|-------|------|----------|
| Backend | FastAPI + Python | `backend/main.py` → `http://127.0.0.1:5001` |
| LLM | Ollama (`qwen2.5-coder:7b`) | `http://localhost:11434` |
| Profile | JSON | `backend/profiles/default/master_data.json` |
| Dashboard | Single-page HTML | `backend/dashboard.html` → `/dashboard` |
| Extension | Chrome MV3 | `extension/` |

### Extension structure
- `manifest.json` — MV3, host permissions, service worker
- `background.js` — Service worker; JD caching, badge, message routing
- `content.js` — Injected into job pages; autofill logic, JD extraction, ATS platform detection
- `injected.js` — In-page DOM helpers injected by content.js
- `popup.html` / `popup.js` — Floating panel with 4 tabs: Fill / Resume / Cover / Ask AI

### Backend structure
- `main.py` — All FastAPI endpoints
- `resume_template.tex` — Jinja2 LaTeX template (source of truth for PDF output)
- `compile_loop.py` — LaTeX compile with self-repair (12 retries, 11 error types)
- `constraints.py` — Post-LLM validation: no fact invention, word budget, authenticity
- `latex_ast.py` — AST-level LaTeX manipulation
- `resume_source.py `
— Resume data extraction helpers
- `resume_versions.py` — Version history for generated resumes
- `logger.py` / `logs/` — Structured logging

---

## Resume pipeline (end-to-end)

```
Extension popup → POST /pending-jd → /dashboard?from=extension
  → auto-fetch JD → auto-fire /analyze-deep (score + keywords)
  → "Customize My Resume →" CTA → POST /tailor-resume
  → diff preview (green = added/changed)
  → "Generate PDF" → compile_loop.py → download + save version
```

Key design decisions:
- JD passed via POST to `/pending-jd` (URL params truncate at ~1800 chars; JDs are 3–10KB)
- `/analyze-deep` validates skills against JD text ONLY — no profile dumps, no hallucination
- LaTeX compile is self-repairing: up to 12 attempts, auto-patches common errors
- Constraints validation runs after LLM to catch fabricated facts or out-of-budget edits

---

## Resume template rules (non-negotiable)

The `resume_template.tex` must always match `Original _current _resume/AI_ML_resume.tex` exactly in structure:

- **10.5pt** document class (not 11pt)
- `\Huge \scshape` header
- `\resumeItem` with `\vspace{-1pt}`
- Skills as `\small{...\\[-1pt]...}` — NOT tabularx
- **FROZEN sections**: header (name/contact/links), education (UH + Amrita), research (Roysam lab + 2 publications)
- **DYNAMIC sections**: summary (max 80 words), experience[0] bullets (Accenture, max 6), projects (3 selected from pool of 12), skills (5 categories)
- Jinja2 delimiters: `\VAR{}` and `\BLOCK{}`

---

## Autofill rules

- `getCleanText()` tries 20+ platform-specific selectors before falling back; strips copyright/cookie noise
- `autoCacheJD` caches JD on page load; `getBestJD()` tries cache first, then live page — persists through navigation
- `isExtensionAlive()` guards every `chrome.*` API call to survive extension reloads
- Learned answers stored per-host via `POST /autofill/learn` — applied in Phase 0 of autofill

---

## Key backend endpoints

| Endpoint | Purpose |
|----------|---------|
| `POST /pending-jd` | Cache JD from extension |
| `GET /pending-jd` | Dashboard fetches cached JD |
| `POST /analyze-deep` | JD analysis: score, matched/missing skills, ATS keywords |
| `POST /tailor-resume` | LLM tailors summary + selects bullets + picks projects |
| `POST /generate-pdf` | Render Jinja2 template → compile LaTeX → return PDF |
| `POST /autofill` | Fill form fields from profile + LLM |
| `POST /autofill/learn` | Save user corrections per host |
| `GET /dashboard` | Serve dashboard HTML |

---

## Profile structure (`master_data.json`)

- `experience[0]` — Accenture GenAI Specialist (6 detailed bullets, `role="Advanced App Engineering Analyst - GenAI Specialist"`)
- `projects[0]` — GPT-2 SLM (most important, url: `Build-SLM-From-Scratch`, 2024)
- `projects[1]` — Multi-Agent Deep RL for Highway Merging
- `projects[2]` — Speech Emotion Recognition
- Total: 12 projects in pool; LLM selects best 3 per JD
- `skills.domains` → "LLMs & Agentic AI" (OpenAI, Claude, LangChain, etc.)

---

## Development rules

- Never mock the database or profile in tests — use real `master_data.json`
- When modifying `resume_template.tex`, always verify Jinja2 compiles: `python3 -c "from jinja2 import Environment, BaseLoader; ..."`
- `experience[0]` must have a `"details"` field (not just `"bullets"`) — template checks both
- `/analyze-deep` must validate skills against JD text only — never pull from profile unchecked
- Build artifacts (`tailored_resume.aux/.log/.out/.pdf/.tex`) are gitignored and must stay untracked
- `backend/resume_template.tex` is source code — always tracked

---

## Glossary

| Term | Meaning |
|------|---------|
| ATS | Applicant Tracking System (Workday, Greenhouse, etc.) |
| JD | Job Description |
| LH pill | The floating "LocalHire" trigger button injected on job pages |
| master_data | Canonical profile JSON at `backend/profiles/default/master_data.json` |
| compile_loop | LaTeX self-repair compiler at `backend/compile_loop.py` |
