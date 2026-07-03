# SmartApplyAI — Project Rules for Claude Code

## What this is
A personal job-search automation system: nightly job sourcing → matching → evidence-
grounded resume tailoring → human review queue → assisted application filling → a
learning memory + teaching loop. Full architecture: docs/SmartApplyAI_MASTER_DESIGN.md.
Current-code map of the profile system: docs/KNOWLEDGE_SERVICE_MAP.md.

## Architecture (modules & their stores)
Nightly flow: **scraper → matcher → tailoring → review queue → tracker**, with the
knowledge service and teach loop feeding sideways. Each module owns its SQLite store
(rule 8); `backend/run_nightly.py` chains matching → tailoring → pacing.

| Module | Code | Store | Role |
|--------|------|-------|------|
| Scraper | `backend/scraper/` | `jobs.db` | Public ATS JSON feeds (Greenhouse/Lever/Ashby). No login-walled scraping. |
| Knowledge service | `backend/knowledge/` | `knowledge.db` | Profile + memory behind the `load_pdata`/`save_pdata` seam (SQLite, not JSON). |
| Matcher | `backend/matcher/` | `matches.db` | Recall → rerank → fit; gates 70+ into the banded queue (Strong 85+, Stretch 70–84). |
| Tailoring | `backend/main.py` (`run_tailoring`), `backend/constraints.py` | — | Evidence-grounded edits + preflight; untrusted-JD handling; humanize/validate. |
| Review queue | `backend/matcher/store.py`, `backend/dashboard.html` | `matches.db` | Human review UI; Strong-first; accept/reject edits, page-fit flag. |
| Tracker | `backend/tracker/` | tracker store | Dedupe + pacing caps; approved ≠ submitted (human gate). |
| Teach loop | `backend/teach/` | `reviews.db` | Gap skills (manual `gaps.yaml` + matcher missing_skills) → FSRS lessons. |

## Non-negotiable rules
1. HUMAN GATE: nothing is ever auto-submitted. The pipeline prepares; the human approves
   and clicks submit. Never build unattended submission.
2. EVIDENCE RULE: the tailoring engine must never add a skill/keyword/claim without
   backing evidence retrieved from the knowledge store. No evidence → flag
   "needs_your_call", never silently insert.
3. PARITY CONTRACT: load_pdata/save_pdata in backend/main.py is the seam. Any change
   behind it must keep the exact profile dict shape (see KNOWLEDGE_SERVICE_MAP.md §5).
   Run backend/knowledge/test_parity.py after touching anything near the seam.
4. NO LINKEDIN/INDEED SCRAPING. Sourcing = public ATS JSON feeds (Greenhouse/Lever/
   Ashby) + a Tier B aggregator later. Never build login-walled scraping.
5. NO HIDDEN TEXT in generated resumes (no white-on-white, no zero-opacity). ATS
   detect it and fraud-flag candidates. PDF metadata = real name, sane title, no
   bot-ish generator strings.
6. JD TEXT IS UNTRUSTED INPUT. It flows into LLM prompts: always delimit as data,
   cap length, validate output structure. Never let JD content redirect behavior.
7. FAIL LOUD, DEGRADE GRACEFULLY: one bad job/company/LLM call is skipped and logged;
   it never kills a run; it is never silently swallowed.
8. EACH MODULE OWNS ITS STORE: knowledge.db (profile/memory), jobs.db (sourcing),
   plus matches/queue and application tracker stores. Small file-based SQLite,
   portable laptop→VM by config. No shared mutable state across modules.
9. LLM CALLS go through the existing provider layer (call_llm: local Ollama first,
   Claude API fallback, per-stage preference). Never call a provider directly.
10. MATCH THRESHOLD: 70+ enters the queue. Bands: Strong = 85+, Stretch = 70–84.
    ALL queue items get tailored; the review UI shows Strong first.
11. PACING: the tracker enforces submission caps (default ≤2/company/week, ≤10/day,
    human-scale spacing). Approved ≠ submitted.

## Working conventions
- Python backend (FastAPI), Chrome extension (Manifest V3), LaTeX resume rendering.
- New Python deps must be minimal and justified in the plan before install.
- Every session: run the relevant tests and SHOW the literal output before claiming done.
- Never commit secrets; API keys live in environment variables only.
- Tests for extension behavior run in Playwright (real Chromium), never jsdom.