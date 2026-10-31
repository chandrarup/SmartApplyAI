# SmartApplyAI — Agent Harness Design

> Companion to `docs/STATE_AUDIT_2026-09-14.md` (what is true today) and
> `docs/research/tsenta/05-gap-analysis-and-path-forward.md` (what the target is).
> This document specifies the *new* layer: a Claude Agent SDK harness plus a two-speed
> browser layer, wrapped around the existing modules.

## 1. The premise, and the thing this design refuses to do

The audit says the modules work and the loop was never closed. The failure mode to avoid
is therefore obvious and specific: **an agent harness that re-implements matching or
tailoring would destroy the moat and repeat the mistake that got us here** — depth in the
interesting parts, nothing running.

So the rule for this layer:

> The harness **orchestrates, recovers, and generalizes**. It never decides what a resume
> claims, what a match scores, or whether an application may be sent.

Three things stay deterministic, LLM-free, and untouched by any agent:

| Stays deterministic | Why |
|---|---|
| `matcher/hybrid.py` stage 1–2 fusion | Versioned + calibratable is the feature. An agent scoring jobs is unreproducible and uncalibratable. |
| `constraints.py` — validation, metric-fabrication cross-check, one-page gate | This *is* rule 2. A mechanism beats a policy. |
| `tracker` dedupe guard + pacing caps | Rule 11 is armor, not a suggestion. |
| Three-band evidence verdicts (`tailor_edits.py`) | Grounded / stretch / fabrication is the moat. |

### Where an agent genuinely earns its place

Only where the work is **unbounded in shape** — where a code change would otherwise be
required for every new case:

1. **Sourcing breadth** — harvesting and validating 300+ ATS tokens. Nobody is going to
   hand-write that list, and it is verifiable (a token either returns jobs or it doesn't).
2. **The unknown tail of application forms** — 4 of ~18 adapters means hand-coded
   heuristics are losing the arms race with form variety.
3. **Run recovery** — a feed changes shape, a provider 500s, a model is unloaded. Today
   that is a silent skip; an agent can diagnose, retry, quarantine, and journal it.

Everything else — cover letters, screening answers, fit rationales — is a plain
`call_llm()` through the existing seam (rule 9). **Do not harness them.** They need the
*evidence rule*, not an agent loop.

## 2. Architecture

```
┌── Tier 2 · Operator surface ─────────────────────────────────────────┐
│  launchd plist  ·  morning digest  ·  dashboard health card          │
└────────────────────────┬─────────────────────────────────────────────┘
                         │ invokes
┌── Tier 1 · Agent harness (claude-agent-sdk) ─────────────────────────┐
│  backend/harness/ — typed missions, each = prompt + bounded toolset  │
│  + output schema + budget + permission callback                     │
│                                                                      │
│   source_expander · run_supervisor · apply_pilot · answer_drafter    │
└────────────────────────┬─────────────────────────────────────────────┘
                         │ calls as MCP tools (never reimplements)
┌── Tier 0 · Deterministic core (existing, unchanged) ─────────────────┐
│  scraper providers · hybrid matcher · constraints/evidence engine    │
│  · knowledge service · tracker dedupe+pacing · LaTeX compile loop    │
└──────────────────────────────────────────────────────────────────────┘
```

Tier 0 is exposed to Tier 1 through `create_sdk_mcp_server()` with `@tool`-decorated
in-process functions — thin wrappers over functions that already exist. No subprocess, no
HTTP hop, no duplicated logic. A mission's `allowed_tools` list is its entire capability
surface.

### Why the Claude Agent SDK specifically

- **Auth is already solved — verified locally, not assumed.** `claude-agent-sdk` 0.2.152
  installed into a throwaway venv and completed a `query()` round-trip on this machine
  with `ANTHROPIC_API_KEY` unset:

  ```
  ANTHROPIC_API_KEY set? False
  [AssistantMessage] HARNESS_AUTH_OK
  ```

  The SDK drives the installed Claude Code CLI and inherits its subscription credential —
  **no API key, no per-token billing to provision.** Ollama keeps doing the per-job bulk
  work for free.
- **`can_use_tool` is a real enforcement point** (§5), not a prompt instruction.
- **Hooks** (`PreToolUse`/`PostToolUse`) give us the run journal for free.

## 3. The missions

Each mission is a module under `backend/harness/missions/` exporting a `run()` that
returns a typed dict and writes a journal row. Missions are budgeted (max turns, wall
clock) and **idempotent** — a killed run can be re-run.

### 3.1 `source_expander` — breadth, 29 → 300+

The single highest-leverage mission, and the safest: it writes **config**, not claims.

- Tools: `WebSearch`/`WebFetch` (public token lists, YC directories, careers pages),
  plus `probe_ats_token(ats, token)` wrapping the existing provider `fetch()`.
- Contract: a candidate is only proposed if `probe_ats_token` returns **≥1 live job** and
  the provider parses it. Unverifiable candidates are dropped, not guessed.
- Output: a diff against `companies.yaml` plus a per-token evidence line (jobs found,
  sample title). Human accepts the diff; the agent never edits the file directly.
- Unblocks the four built-but-unconfigured providers (personio, smartrecruiters,
  teamtailor, workable) at zero code cost.

### 3.2 `run_supervisor` — the clock, with an exception path

The nightly chain runs **deterministically**. The agent is not in the happy path of a
6,000-job run.

```
launchd (nightly + catch-up-on-wake)
  └─ python -m backend.run_nightly --with-sourcing
       step 0  scraper.run.execute_run()      ← new: sourcing joins the chain
       step 1  matcher.run.run_pipeline()
       step 2  tailor_pending_queue()
       step 3  tracker.pacing.release_ready()
       step 4  teach gap generation            ← new
       step 5  morning digest
```

On a step failure (rule 7 already isolates it), the supervisor mission gets **one bounded
turn**: read the run log and `source_health`, decide retry / quarantine-provider /
raise-alarm, write the journal entry, and surface a health card on the dashboard. That is
the whole agentic surface of the nightly run — and it is exactly the part where a code
change would otherwise be needed for every new failure shape.

### 3.3 `apply_pilot` — the last mile

Covered in §4. Human gate enforced structurally in §5.

### 3.4 `answer_drafter` — screening answers + cover letters, through the evidence rule

Not an agent loop — a **pipeline fix** to two endpoints that currently bypass the moat
(`/answer-question`, `/cover-letter` both dump raw profile JSON into a free prompt):

```
detect questions on the JD/form
  → knowledge.semantic retrieve evidence per question
  → call_llm() draft, delimited, capped
  → three-band verdict (grounded / stretch / fabrication)
  → store on the queue item alongside the resume edits
  → review modal shows them next to the diffs
  → approved set flows to the extension as fill values
```

This is what makes the prep stage complete: today the approved payload carries the PDF but
`tailored_data: {}`.

## 4. The browser layer: two speeds, and a learning loop between them

5,788 of 6,398 jobs are Greenhouse / Lever / Ashby. For those, screenshot-driven vision
would be slower, costlier, and *less* reliable than a selector. So:

| Lane | Mechanism | Covers | Cost per application |
|---|---|---|---|
| **Fast** | Deterministic DOM fill via Playwright selector packs, versioned per ATS, fixture-tested (`tests/autofill/*.spec.mjs` already exist) | Greenhouse, Lever, Ashby → ~90% of jobs | **Zero LLM calls** |
| **Slow** | Computer use — screenshot + coordinate, the same Playwright browser | Workday-class shadow DOM, unknown ATSes, any page where the fast lane's post-fill verification fails | One bounded agent session |

**The part that compounds:** when the slow lane completes a form, it emits a *candidate
selector pack* for that ATS — field → selector → source of the value. After N successful
replays against the fixture corpus, the pack is promoted into the fast lane. This is how
4/18 adapters becomes 18/18 without hand-writing fourteen adapters, and it turns every
hard application into permanent cheap capability.

The fast lane always runs first; the slow lane is the fallback, never the default.

Verification after fill is deterministic in both lanes: read back every field, diff
against the intended values, and refuse to hand off on mismatch.

## 5. The human gate, as a property of the code

Rule 1 must not depend on a sentence in a prompt. Three layers:

1. **The toolset has no submit action.** `apply_pilot`'s `allowed_tools` contains
   `fill_field`, `upload_file`, `read_page`, `screenshot` — and nothing that clicks a
   submit control. A capability that does not exist cannot be misused.
2. **`can_use_tool` denies submit-shaped actions**, including a click whose target text or
   role matches submit semantics, with `PermissionResultDeny(interrupt=True)`. This catches
   the case where a fill action is aimed at a submit button.
3. **Navigation allowlist.** The mission may only reach hosts in the ATS allowlist.

The mission's terminal state is `screenshot + handoff` — filled form, resume attached,
answers in place, browser parked on the review screen. **The human reads it and clicks
submit.** Then `POST /queue/{match_id}/mark-applied` records it (which now works — audit §6).

The same reasoning applies to the other rules: `source_expander` cannot write
`companies.yaml` (it proposes a diff); no mission may reach a login-walled source
(rule 4); every mission's LLM access is the existing `call_llm` seam or the SDK's own
model — never a direct provider call (rule 9).

## 6. Per-module improvement plan

| Module | Today (verified) | Change | Mechanism |
|---|---|---|---|
| **Scraper** | 29 tokens, 1 run ever, 4 providers idle | 300+ tokens; runs nightly; all 8 providers live | `source_expander` mission + sourcing as step 0 of `run_nightly` |
| **Orchestration** | `run_nightly` invoked by nothing | Nightly + catch-up-on-wake, journaled, with an exception path | launchd plist + `run_supervisor` |
| **Matcher** | Correct, never run at cadence | Same scoring; real volume; bands calibrated against outcomes | No logic change — `calibrate.py` finally gets data |
| **Tailoring** | Strong; only `experience[0]` tailored; summary guard unverified | Extend beyond `experience[0]`; **fix the vacuous injection guard** (audit §5) | Deterministic; no agent |
| **Knowledge** | `sqlite_vec` was never installed → semantic reindex skipped at startup | **Fixed** — vector search restored (audit §6.2). Re-tailor a sample of the 35 existing matches; their verdicts were produced without retrieval | `pip install sqlite-vec` (already in `requirements.txt`) |
| **Answers / cover letters** | Exist, but bypass the evidence rule and never reach review | Evidence-retrieved, three-band verdicted, stored on the queue item | `answer_drafter` (§3.4) |
| **Review queue** | Good UI; `/matches/approved` returns `tailored_data: {}` | Approved payload carries PDF **+ answers + edits** | Endpoint fix |
| **Extension / apply** | Handoff built; 4/18 adapters; completion was 404 | Fast lane for GH/Lever/Ashby; slow lane + pack learning for the tail | §4 |
| **Tracker** | Complete, 2 rows | Real rows on every apply; email confirmation routing auto-advances status | §6 fix (done) + IMAP poll |
| **Teach** | Standalone demo | Step 4 of the nightly chain | Wiring only |
| **Digest** | None | Morning push: Strong matches, one-line why, deep link into review | Step 5 |

## 7. Sequencing

Ordered by *what unblocks what*, not by interest. Mapped to gap-analysis workstreams 1–8.

| Phase | Work | Acceptance test |
|---|---|---|
| **0** | ~~Restore `sqlite_vec`~~ **done** (audit §6.2); fix the vacuous injection guard; repo hygiene | Startup logs no reindex skip ✓; the guard fails when rule 2 is broken and passes when it holds |
| **1** | Sourcing into `run_nightly` + launchd + run journal + health card | **14 consecutive mornings, queue populated, zero manual steps** |
| **2** | `source_expander` → 300+ verified tokens | ≥5 Strong matches/week reaching review |
| **3** | Fast-lane DOM fill (GH/Lever/Ashby) + approved payload carries answers | **≤3 minutes from queue item to a filled form awaiting one human click** |
| **4** | `answer_drafter` with evidence grounding | Screening answers reviewed next to resume diffs; zero ungrounded claims |
| **5** | Slow lane (computer use) + selector-pack promotion | One previously-unsupported ATS filled, then promoted to the fast lane |
| **6** | Email confirmation routing → tracker status | `applied → confirmed` advances without a human touch |
| **7** | Morning digest | Default path is open → scan → approve, 3 clicks |

Phases 0–2 need no agent code at all. **Build the clock before the agent.**

## 8. Costs and dependencies

- **New Python dep: `claude-agent-sdk`** (one package; rides the installed CLI's auth —
  probe above). Justified per CLAUDE.md before install; that justification is §2. Not yet
  installed into `backend/.venv` — awaiting the go-ahead.
- **`sqlite_vec`** — not a new dep (already in `requirements.txt`), was simply missing from
  the venv. Installed and verified this session.
- **Playwright** — already installed (1.61.0), already used by the autofill fixtures.
- **Token spend:** Phases 0–3 add no metered cost — Ollama does the per-job work locally
  and no agent sits in the happy path. Agent missions run against the Claude Code
  subscription (verified above), so the constraint is subscription rate limits, not a
  bill. `source_expander` is a handful of bounded runs; slow-lane computer use is the only
  recurring agent cost, and only on the tail.

## 9. What this design deliberately does not adopt

From the Tsenta comparison: no auto-approve mode, no cloud credential-holding submission,
no 600–4,500/month volume tier, no login-walled sourcing. The human gate, the evidence
rule, and the pacing caps are the product — the harness exists to make them *operate*, not
to route around them.
