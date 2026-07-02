# SmartApplyAI — Master High-Level Design
### Job Sourcing → Matching → Tailoring → Human Review → Assisted Application, with a Learning Memory

**Status of this document:** design reference, not implementation spec. No code, no file paths.
Tech mentions are *current picks or candidates* — swappable until we finalize the stack per module.
Living document: update as phases complete and findings come back.

---

## 1 · Vision

A personal system that (1) continuously discovers freshly posted, relevant jobs, (2) ranks them
honestly against a living memory of Chandra's real skills and evidence, (3) tailors the resume
per job with every change explained and approved, (4) assists in filling the application, and
(5) teaches the skills the market keeps asking for that Chandra doesn't yet have — closing the
loop by writing newly learned skills back into the memory.

**North-star metric: interviews per week — never applications per day.**
Market research on failed auto-apply tools shows mass automation yields ~0.5–6% callback rates
versus 10–20% for targeted, tailored applications. Every design decision below optimizes signal
per application, not volume.

## 2 · Non-Negotiable Design Principles

1. **Human gate before anything irreversible.** The pipeline runs unattended up to a review
   queue. Nothing is submitted, and no memory is committed, without explicit approval.
   (This is also the #1 differentiator vs. the failed tools: they submit unattended; we never do.)
2. **No claim without evidence.** The tailoring engine may not add a skill/keyword unless the
   knowledge store holds backing evidence. Unbacked suggestions are flagged for a human call.
   Corollary — *interview defensibility rule*: never let the resume claim anything Chandra
   can't discuss for five minutes. Trust collapses in interviews otherwise.
3. **Decoupled memory.** The knowledge store is an independent service with its own API, usable
   by any current or future tool. Everything else is a consumer.
4. **Own the raw data; indexes are disposable.** Immutable event log + raw job JSON are the
   source of truth. Embeddings, ranks, extractions can always be rebuilt.
5. **Local-first, portable by config.** Runs on a laptop today; moving to a VM is a deploy step,
   not a redesign. Local LLM for bulk work, cloud LLM for quality-critical passes — selectable
   per stage.
6. **Fail loud, degrade gracefully.** A bad job, a changed website, a down LLM: skip, log,
   continue. One failure never kills a run; no failure is ever silent.
7. **Everything auditable.** Every application ever made is recorded with the exact resume
   version, answers, timestamp, and outcome. If you can't audit what was applied and why, you
   lose control — the reviewed tools that skip this are the ones that burn users.

## 3 · System Overview

```
                        ┌────────────────────────────────────────┐
                        │        KNOWLEDGE SERVICE (M1)          │
                        │  profile · skills+evidence · events    │
                        │  embeddings · semantic search API      │
                        └───▲───────────────▲───────────────▲────┘
        writes learnings    │      reads    │        reads  │ writes proficiency
              ┌─────────────┘               │               └─────────────┐
      ┌───────┴───────┐             ┌───────┴───────┐             ┌───────┴───────┐
      │ CAPTURE (M2)  │             │ MATCHER (M4)  │             │ TEACHING (M8) │
      │ daily learn-  │             │ 3-stage rank  │◄── gaps ────│ lessons+FSRS  │
      │ ings inbox    │             └───────▲───────┘             └───────────────┘
      └───────────────┘                     │ jobs
                                    ┌───────┴───────┐
                                    │ SOURCING (M3) │  nightly scheduler
                                    │ Tier A + B    │
                                    └───────────────┘
             matches ≥ threshold ──────────►┌──────────────────────────┐
                                            │ REVIEW QUEUE + TRACKER   │
                                            │ (M6)  approve · dedupe · │
                                            │ version · audit · pace   │
                                            └───────────┬──────────────┘
                       tailored + approved              │ approved items only
                                    ┌───────────────────▼───────┐
                                    │ TAILORING (M5) ──► AUTOFILL│
                                    │ evidence-grounded  (M7)    │
                                    │ per-edit diffs     browser │
                                    └────────────────────────────┘
```

Data stores (each module owns its own; no shared mutable state): profile/memory store,
jobs store, matches/queue store, application tracker store. All small, file-based, portable.

---

## 4 · Module Designs

### M1 · Knowledge Service — the living memory
**Purpose:** single source of truth for who Chandra is: contact, education, experience,
projects, publications, skills (each with self-rated 1–5 proficiency, an evidence snippet,
provenance, timestamps), autofill answers, and an append-only event log of every change.

**Design steps:**
1. Structured store + faithful profile assembly (byte-parity with the legacy format so all
   existing consumers keep working). *(done — parity passed)*
2. Capture pipeline (see M2) writing through an approval gate. *(built — `knowledge/capture.py`)*
3. Semantic layer: embed every evidence-bearing text (skill+evidence, project descriptions,
   experience bullets, summary) with a small local embedding model; expose a search API:
   given any query text (e.g., a JD), return the most relevant profile evidence with scores.
   *(built — `knowledge/semantic.py` + `embeddings.py`)*
4. Decouple: stand it up as its own small HTTP service so any tool — this app, the teaching
   module, future portfolio site, anything — reads/writes through one API.
   *(built — `knowledge/service.py` on :5100 with dual-mode `client.py`; backend uses
   in-process mode unless `KNOWLEDGE_SERVICE_URL` is set)*

**Current picks:** embedded single-file DB with a vector extension; small local
sentence-embedding model; upgrade path to a temporal knowledge graph (Zep/Graphiti-class)
only if "how did my profile evolve" queries become necessary — not before.

**Real-world challenges & mitigations:**
- *Memory drift* (the store said "GenAI engineer" while reality had moved to spatial
  proteomics): provenance + timestamps on every claim; periodic "stale claims" review
  surfacing anything not touched in N months.
- *Corruption/loss — this file IS the career record*: automatic timestamped backups before
  every migration and on schedule; the event log allows full rebuild.
- *Two writers at once* (dashboard + extension): last-write-wins semantics with the event
  log as arbiter; single-writer service once decoupled.

### M2 · Capture & Learning Loop
**Purpose:** Chandra pastes his own summary of what he learned (from YouTube, calls,
internships, courses); the system proposes structured deltas (new/updated skills with
suggested rating + evidence + source); he edits/approves; only then it commits.

**Design steps:** propose (local LLM extraction → structured delta + event row, status
"proposed") → review UI (editable rows) → commit (event → committed; tables updated).
Skill-rating flow for anything unrated.

**Real-world challenges:**
- *Extraction hallucination* (local model invents a skill not in the paste): the approval
  diff is the control — nothing lands unseen.
- *Duplicate skills under different names* ("RAG" vs "Retrieval-Augmented Generation"):
  propose-time fuzzy match against existing skills; suggest "update existing" over "add new".
- *Capture friction kills the habit*: the inbox must be one paste + one click. If it takes
  more than 30 seconds, it won't be used, and the memory rots again.

### M3 · Job Sourcing + Scheduler
**Purpose:** discover freshly posted, in-scope jobs nightly, from durable sources.

**Design steps:**
1. Tier A: poll public, no-auth ATS JSON feeds (Greenhouse / Lever / Ashby boards) for a
   curated H1B-sponsor-heavy company list. Official endpoints; stable; free.
2. Tier B: one aggregator API for breadth — critically, the mega-sponsors (Google, Amazon,
   Meta, Microsoft, Apple, Nvidia, big consultancies) run custom/Workday career sites that
   Tier A cannot reach. Tier B is *how the biggest targets enter the system at all*.
3. Normalize into one unified job shape; upsert with change detection (new / updated /
   expired); compute flags: internship?, location-match?, sponsorship-knockout?
4. Nightly schedule with manual-run support; laptop-aware (see challenges).
5. Optional last-resort adapter: agentic fetch (Crawl4AI/Firecrawl-class) for a single
   must-have company on no public feed. Never the primary engine.

**Explicitly out:** DIY LinkedIn/Indeed scraping — login-walled, ToS-prohibited, account-ban
risk; every failed tool that touched it drew bot flags.

**Real-world challenges:**
- *Laptop asleep at 2 AM*: catch-up-on-wake semantics, or accept empty mornings and surface
  "last successful run" in the UI so silence is never mistaken for "no jobs".
- *Feed shape changes / token rot*: per-company failure isolation; loud mapping errors;
  dropped-token report each run.
- *Ghost/stale postings* (reposted for months, never hiring): track first-seen age and
  repost patterns; downrank or flag "posted 90+ days / reposted 3×".
- *Scam postings* (fake jobs harvesting PII — a real, rising hazard for auto-appliers):
  heuristic screen before the queue: unknown domain, pay-to-apply, off-platform contact,
  data requests beyond a normal application → flag "suspicious, verify employer".
- *Duplicate postings across sources* (same job via Tier A and Tier B): dedupe on
  company+title+location fingerprint, keep the richer record.

### M4 · Matching & Ranking
**Purpose:** from hundreds of collected jobs, produce a short honest list worth Chandra's
attention — internships first, F1-compatible, in target locations, ≥ threshold fit.

**Design steps:** hard prefilter (flags from M3) → Stage 1 recall: embed JD, retrieve
against profile evidence via M1 search (cheap, local) → Stage 2 rerank: local cross-encoder
reads (JD, profile) pairs for real ordering → Stage 3 fit (LLM, survivors only): structured
verdict — match %, matched skills each pointing at its evidence, missing skills (→ M8),
best-fit projects, one-line rationale. Threshold 70+ gates entry to the queue, banded:
**Strong = 85+, Stretch = 70–84** (decided — see CLAUDE.md rule 10). All queue items get
tailored; the review UI shows Strong first.

**Real-world challenges:**
- *Score inflation/drift* (LLM says 90 for everything): anchor the rubric with definitions
  per band; spot-audit weekly; track score distribution over time.
- *Empty queue seasons* (internship cycles are seasonal — Summer-2027 postings ramp in
  fall): the UI must distinguish "nothing cleared the bar" from "nothing was fetched";
  near-misses enter the queue as the Stretch band (70–84) rather than being dropped.
- *JD says one thing, title another* ("intern" title, "5 years required" body): stage 3
  reads the body; knockout phrases override title matches.
- *Cost control*: stages 1–2 always local; stage 3 on ≤30 jobs/night; per-run LLM spend
  logged (see cross-cutting).

### M5 · Tailoring Engine — the mastery piece
**Purpose:** per approved-for-tailoring job, rework the resume honestly: reorder and
re-emphasize real experience toward the JD, select best-fit projects, rewrite the summary —
emitting every change as an explainable, individually approvable edit.

**Design steps:**
1. Derive the JD's demand set (skills, responsibilities, seniority signals).
2. For each demand, retrieve profile evidence (M1 search). Evidence found → propose a
   grounded edit citing it. No evidence → emit as "needs your call", never silently insert.
3. Emit edit objects: section, before, after, reason, evidence reference, confidence.
4. Diff UI: accept / reject / modify each edit; only accepted edits build the final resume.
5. Render: single-column, parser-safe layout; clean real-name metadata; hard guarantee of
   zero hidden/white text (modern ATS detect it and can fraud-flag the candidate record).
6. Quality pass on the final wording by the cloud LLM; bulk drafting can stay local.
7. **Consistency check (new):** before an application is marked ready, verify resume, cover
   letter, and stored form answers don't contradict each other (dates, titles, skills).
   Cross-document contradictions are a top human-visible automation tell.
8. **Anti-template variation (new):** recruiters pattern-match identical sentences across
   applicants and across one applicant's applications. Vary phrasing from the evidence bank
   rather than reusing one polished sentence everywhere.

**Real-world challenges:** LaTeX/renderer breakage from JD-sourced special characters
(escape everything, compile-test before delivering); one-page overflow (graceful trim
rules); malformed LLM JSON (recover or fail loud, never half-apply); wrong-page input
(a privacy policy instead of a JD → detect non-JD text and refuse to tailor).

### M6 · Review Queue + Application Tracker  ★ promoted to a first-class module
**Purpose:** the morning cockpit and the system's memory of *actions taken*. Research
verdict was unanimous: auditability and duplicate control are what separate tools that
help from tools that quietly burn candidacies.

**Responsibilities:**
- Queue: each match with fit rationale, tailoring status, per-edit diffs, approve/reject.
- **Dedupe & history guard:** before anything is tailored or filled — have we already
  applied to this company for this/similar role? Were we rejected recently? (Reapplying
  to a rejection within weeks looks spammy and starts you at a trust deficit.) Warn or block.
- **Resume versioning:** store the exact resume artifact + answers sent per application.
  When a recruiter calls three weeks later, Chandra must see precisely what that company
  saw — also the input for interview prep.
- **Pacing / velocity caps (new):** per-company and per-day submission limits with human-
  scale spacing. Dozens of applications in minutes is a bot signature even when a human
  clicks the button; caps also protect quality (approving 30/day means rubber-stamping).
- **Status tracking:** applied → confirmed → OA/screen → interview → offer/rejected/ghosted,
  manually updated at first. (Optional later: email integration to auto-detect
  confirmations/rejections — kept out of v1 for privacy/scope.)
- **Outcome analytics:** callback rate by company type, match-score band, resume variant —
  the feedback loop that tunes the threshold and tailoring over time, so the system
  optimizes interviews, not sends.

### M7 · Autofill & Submission Assist
**Purpose:** on an approved application's page, fill the form from the approved package
(tailored resume data + stored answers), platform-aware across the 8 supported ATS.

**Design (already largely built — evolves, not rebuilt):** platform detection → field/label
mapping → rule-based fill for knowns, LLM fallback for odd questions → human reviews the
filled form → **human always clicks submit** (assisted-submission, not auto-submit — lower
account risk, catches the last 1% of mapping errors, keeps CAPTCHAs a non-issue since
a real person is present).

**Real-world challenges (harvested from tool failure reports + our own code review):**
Workday's shadow-DOM components (worst-in-class: a leading competitor manages ~50% field
accuracy there); JDs and fields inside iframes; custom dropdowns/typeaheads that need
keystrokes not value-sets; multi-step wizards requiring re-scan per step; file-upload
fields (attach the *versioned* tailored resume, never a stale one); never overwrite
user-typed values; wrong-field disasters (salary in the wrong box can auto-disqualify —
mitigation: the review-before-submit pass plus a "filled fields" summary panel);
knowledge/screening questions answered from stored truth only — an unanswerable question
pauses for the human rather than guessing.

### M8 · Teaching Module
**Purpose:** turn the matcher's recurring "missing skills" into lessons in Chandra's own
learning format (intuition → everyday analogy → biomedical analogy → depth/why → interview
line), scheduled by spaced repetition (FSRS), with "learned" writing proficiency back to M1
through its API — so next week's tailoring can honestly use the new skill.

**Design steps:** gap aggregation (frequency-weighted across the week's matches — teach
what the market keeps asking) → skip anything already rated high → lesson generation →
review scheduling → write-back on graduation.

**Real-world challenges:** hallucinated lesson content (ground lessons in the skill's
public docs later; v1 relies on frontier-model quality + Chandra's own verification);
motivation decay (due-today list stays short — FSRS naturally does this); gap noise
(a skill wanted by one job ≠ a trend; weight by recurrence).

---

## 5 · Cross-Cutting Concerns

- **LLM abstraction:** one provider layer, local-first with cloud fallback, per-stage
  preference config, timeouts, and empty/malformed-response handling everywhere. Prompt
  templates versioned; a change to a prompt is a change to system behavior and gets
  regression-checked against saved fixtures (drift is real across model updates).
- **Prompt-injection defense:** every JD is untrusted input that flows into prompts.
  Delimit as data, instruct against instruction-following from it, cap length, validate
  output structure. (Hardening prompt H2 exists for exactly this.)
- **Security & privacy:** the memory holds PII + visa status; the tracker holds everywhere
  applied. Local-only by default; secrets in environment/keychain, never in the store;
  when moved to a VM: disk encryption, no public ports without auth. Scam-posting screen
  (M3) protects PII from fake employers.
- **Cost & observability:** per-run log line (jobs fetched, matched, tokens spent, $ spent,
  failures); a single "system health" view: last successful run per module, error counts.
  Silent failure is the enemy — an empty morning queue must always be explainable.
- **Backups:** the memory store and tracker are irreplaceable; scheduled file-level backups
  with retention, tested restore.
- **Testing:** the six hardening harnesses (JD extraction, injection, tailoring correctness,
  autofill robustness, LLM resilience, pipeline integrity) run as a regression suite before
  any significant change — findings triaged jointly, fixes targeted per failing case.

## 6 · Blind Spots Caught in This Pass (now folded in above)

1. **Application Tracker as a first-class module** — dedupe, rejection-history guard,
   resume versioning, status pipeline, outcome analytics. Was implicit; now M6.
2. **Exact-artifact versioning** — you must be able to see precisely what each company
   received, or interviews start with a guessing game.
3. **Pacing/velocity caps** — volume patterns read as bot behavior even with a human gate.
4. **Cross-document consistency check** — resume vs cover letter vs form answers.
5. **Anti-template phrasing variation** — identical sentences across applications get
   pattern-matched by recruiters.
6. **Scam/ghost-posting screening** — auto-pipelines are prime PII-harvest targets.
7. **Interview-defensibility rule** — nothing on the resume Chandra can't speak to for
   five minutes; ties tailoring to M8 (if it was taught and learned, it's defensible).
8. **Interview prep handoff (future)** — when a company responds, bundle the exact resume
   version + JD + company research into a prep pack. Natural later extension.
9. **Outcome feedback loop** — callback analytics tune threshold/tailoring; without it the
   system can't learn whether it's actually working.

## 7 · Phasing (maps to the existing prompt files)

- **Phase I — Memory:** M1 stages 1–3 + M2. *(all stages + capture built; parity green)*
- **Phase II — Sourcing:** M3 Tier A + flags + scheduler; Tier B next.
  *(Tier A built and producing data in `jobs.db`; Tier B placeholder only)*
- **Phase III — Judgment:** M4 three-stage matching; threshold tuning.
  *(all three stages coded in `backend/matcher/`; end-to-end run not yet proven —
  no `matches.db` produced yet)*
- **Phase IV — Craft:** M5 evidence-grounded tailoring + diff control + consistency check.
  *(legacy tailoring hardened; NOT yet rewired to evidence-grounded edit objects — next
  major build after M6)*
- **Phase V — Action:** M6 queue/tracker, then M7 rewire to approved packages.
  *(not started — biggest missing module)*
- **Phase VI — Growth:** M8 teaching + write-back; outcome analytics maturing in M6.
  *(scaffolded early: `backend/teach/` has FSRS, lesson generation, review UI)*
- **Continuous:** hardening suite; backups; observability.

## 8 · Open Decisions (to close as we reach each module)

- Tier B aggregator selection + budget cap (needed when M3 Tier A is proven).
- ~~Near-miss band surfaced as "stretch" items~~ **Decided yes:** queue threshold 70+,
  Strong = 85+, Stretch = 70–84 (CLAUDE.md rule 10).
- ~~Default pacing caps~~ **Adopted:** ≤2/company/week, ≤10/day, human-scale spacing
  (CLAUDE.md rule 11).
- Email-based status detection: future opt-in or never.
- VM migration trigger + hosting choice (config-ready from day one).
