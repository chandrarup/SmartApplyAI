# Tsenta Research — Doc 4: SmartApplyAI Current-State Snapshot

> Audit date: 2026-07-18, branch `feature/hybrid-filtering` (HEAD `dd50116`). Read-only
> code survey — this is what actually exists, not what the design docs say.

## Headline: the design docs are stale — the code is well ahead

`docs/SmartApplyAI_MASTER_DESIGN.md` §7 claims "no matches.db produced yet," "M5
tailoring not yet rewired to evidence-grounded edit objects," and "M6 queue/tracker not
started." **All three are now false.** The repo has a populated `matches.db` (35 tailored
rows), a fully implemented evidence-grounded edit engine, and a wired
queue→approve→dedupe→tracker flow with a real dashboard UI. The genuine gap is
**orchestration/automation and real-cadence operation**, not module completeness.

## 1. Scraper (`backend/scraper/`) — built, resilient, manual

- Provider registry with **8 ATS plugins built**: Greenhouse, Lever, Ashby, Workday,
  Personio, SmartRecruiters, Teamtailor, Workable, plus internship-tracker repos.
- `jobs.db` holds **6,398 real jobs** from 5 live feeds (greenhouse 5,353 · tracker 550 ·
  lever 274 · ashby 161 · workday 60). Personio/SmartRecruiters/Teamtailor/Workable are
  built-but-unconfigured (no `companies.yaml` entries).
- **29 company tokens configured** (25 Greenhouse incl. Stripe/Databricks/Anthropic,
  2 Ashby, 1 Lever, 1 Workday).
- Resilience is strong: per-company failure isolation, `source_health` cooldowns,
  coverage-drop anomaly alarm vs. baseline, bounded concurrency, change-detection upsert,
  liveness checks.
- **Weak point — cadence:** `schedule.py` (APScheduler) is opt-in and invoked by nothing.
  No cron/launchd anywhere. `runs` table shows **exactly 1 run ever** (2026-07-13).
  Scraping is manual (dashboard button or CLI). Tier B aggregator is a
  `NotImplementedError` stub.

## 2. Matcher (`backend/matcher/`) — complete, marquee feature of the branch

- Chain: `prefilter_jobs` → ranking → LLM fit (`top_fit=30`, ollama-preferred) →
  `gate_and_store`.
- Two config-switchable ranking paths: legacy semantic recall + `bge-reranker-base`
  cross-encoder, and the **new Phase-2 hybrid** (`hybrid.py`): deterministic, LLM-free,
  versioned (v1) fusion of 5 components — ontology skill coverage 0.40, FTS5 BM25 0.20,
  dense embedding 0.20, domain adjacency 0.10, level fit 0.10 — backed by `features.db`,
  a 35 KB skills ontology, and `calibrate.py`. Falls back to legacy on failure. Tested.
- Legitimacy screen (`legitimacy.py`) tiers scam/ghost postings without dropping any.
- Bands live and correct: Strong ≥85 (5 rows), Stretch 70–84 (30 rows), threshold 70.

## 3. Tailoring (`run_tailoring`, `constraints.py`, `tailor_edits.py`) — the mastery piece, real

- **Evidence rule fully implemented** with three-band verdicts: `grounded` (auto-accept),
  `stretch` (knowledge hit ≥0.60 cosine → `needs_your_call`, Keep/Soften/Drop),
  `fabrication` (auto-rejected, never rendered).
- Chain: deterministic project ranking + skills trim → section-isolated LLM prompts with
  style fingerprint → humanize → style lint → preflight → grounded edits with lint flags.
- `constraints.py`: validation, metric-fabrication detection cross-checked against
  evidence, keyword-density caps, protected fields, word budget, auto-repair, and a
  **one-page PDF gate** (`pdfinfo` + progressive trim). Extensive LaTeX-injection
  hardening with a large test corpus.
- Rough edges: only `experience[0]` bullets are tailored; hundreds of injection-test
  artifacts (`inj_*.pdf` etc.) committed into `backend/`.

## 4. Review queue + dashboard — real, usable UI

- Dedicated Review Queue page: strong-first ordering, edits modal with green/red diffs
  grouped by section, job-context header, tailor/skip/approve/release/mark-applied.
- Approve compiles a versioned PDF, runs the dedupe guard, marks `customized`. Human
  always clicks submit (rule 1 intact).
- Separate Job Sourcing page triggers scrape + match from the UI.

## 5. Chrome extension (MV3) — ambitious, least-finished consumer

- `content.js` is 4,034 lines; host permissions for ~18 ATS domains.
- Extraction adapter registry covers only **4 platforms** (lever, workday, ashby,
  generic); legacy autofill engine does rule-based fill from `/autofill` with LLM
  fallback, Workday structured widgets, a `/autofill/learn` correction memory, and deep
  **shadow-DOM traversal** for Workday-class pages.
- Gaps: README marks it "Phase 2/secondary"; hardcoded to the `default` profile; the
  queue's `customized` variant PDF is not cleanly handed to the extension as the upload
  artifact; Workday fill accuracy is the known-hard case.

## 6. Tracker (`backend/tracker/`) — fully built, barely exercised

- Own `tracker.db`; 9-state pipeline (approved→…→offer/rejected/ghosted) with
  status-history audit, resume-variant linkage, answers snapshot, callback-rate
  analytics by band/company.
- Dedupe guard (company-norm + title similarity 0.8; 90-day rejection block; HTTP 409)
  wired into approve/mark-applied. Pacing caps (≤2/company/week, ≤10/day, 45-min
  spacing) with virtual-clock release, wired into `/queue/release` and `run_nightly`.
- **Only 2 application rows ever** — plumbing complete, real usage hasn't happened.

## 7. Knowledge service + teach loop

- Knowledge: mature. `knowledge.db` with sqlite-vec, dual-mode client (in-process /
  HTTP :5100), semantic search (MiniLM), approval-gated capture, parity tests.
- Teach: scaffolded, standalone demo only. FSRS scheduler, lesson generation, gap
  aggregation from matcher `missing_skills` + `gaps.yaml`, review UI, KB write-back on
  "learned." **Not in `run_nightly`** — the loop isn't automated.

## 8. LLM provider layer — solid shared seam

- `call_llm(prefer=…)`: Ollama-first with Claude fallback (or reverse), per-stage model
  override, failover on exception. Matcher stages 1–2 are LLM-free; only stage-3 fit and
  tailoring call LLMs. Claude model pinned to `claude-sonnet-4-6`.

## 9. Orchestration (`run_nightly.py`) — the critical gap

- Chains: matching → tailor all pending → pacing release. Clean skip flags.
- **Missing:** does not run scraping (starts from a pre-populated jobs.db); does not run
  teach; and **nothing invokes it** — no cron/launchd/systemd/script anywhere. "Nightly,
  review-only mornings" is aspirational; today it's a human running scrape, then
  match+tailor, then reviewing.

## 10. Deployment readiness

- Portability: good bones (env-overridable DB paths, local-first LLM, file-based
  stores). But: no auth layer, no Dockerfile, no VM path, no backup automation (design
  calls the memory+tracker irreplaceable).
- Tests: substantial (scraper v3, matcher hybrid suite, knowledge parity, tests/ tree
  with unit/smoke/integration/autofill, six FINDINGS_*.md hardening reports).
- Hygiene problems: `jobs.db` **110 MB committed to git**; hundreds of LaTeX test
  artifacts committed; README/MASTER_DESIGN/code disagree on project phase;
  `apscheduler` only in scraper requirements.

## Biggest gaps to a usable daily pilot (ranked)

1. **No automation of the nightly run** — `run_nightly` exists but nothing triggers it,
   and it doesn't include sourcing. Needs launchd/cron running scrape → run_nightly with
   catch-up-on-wake.
2. **Sourcing decoupled from the nightly chain**; health/coverage alarms have no visible
   observability surface.
3. **Real-world volume is thin** — 1 scraper run, 35 matches, 2 tracker rows; band
   calibration and score drift have never run on real cadence.
4. **Extension least-finished**: 4/18 ATS adapters, single-profile, no clean handoff of
   the approved variant PDF, Workday fill unproven.
5. **Tier B (Google/Amazon/Meta-class mega-employers) is a stub** — the biggest targets
   can't enter the system at all.
6. **Teach loop not in the pipeline** — the learning value-prop isn't automated.
7. **No backups/auth/VM path.**
8. **Doc/repo hygiene** — three conflicting sources of truth; large binaries in git.

**Net:** modules are far more complete than the design admits. The distance to a daily
pilot is almost entirely automation + sustained real-cadence operation + finishing the
autofill/Tier-B edges — not core module construction.
