# Tsenta Research — Doc 5: Gap Analysis — SmartApplyAI vs. Tsenta, Why We're Stuck, What to Change

> Synthesis of Docs 1–4 (2026-07-18). This is the decision document; the pilot plan will
> be built from the "What to change" section.

## 0. The one-paragraph verdict

Tsenta and SmartApplyAI converged on the **same trust architecture** — diff-view
approval, truthful tailoring, receipts, direct-ATS sourcing — and ours is *stronger* on
the trust axis (retrieval-backed evidence vs. their "only true facts" policy; a hard
human gate vs. their optional one; enforced pacing vs. none). Where a 2-person team
lapped us is **operationalization**: their pipeline runs continuously end-to-end and is
consumable in one tap, while ours is a set of well-built modules that a human must
manually chain every day. **We are not behind on capability. We are behind on the loop
being closed.** The gap analysis below is therefore mostly about automation, the
last-mile apply step, and product surface — not about rebuilding anything.

## 1. Side-by-side capability matrix

| Capability | Tsenta | SmartApplyAI today | Verdict |
|---|---|---|---|
| Sourcing breadth | 50K+ career pages, ~15–19 ATSes, "75% of F500," 2.38M-job board | 8 ATS plugins built, 5 live, **29 companies configured**, 6,398 jobs, 1 run ever | **Big gap** — breadth + cadence, not tech |
| Sourcing freshness | Continuous, "within seconds," speed-to-apply is the core pitch | Manual button press; APScheduler exists but nothing invokes it | **Critical gap** |
| Matching | % score + "why it matched"; nothing published on method | Deterministic hybrid v1 (ontology/BM25/dense/domain/level) + LLM fit + bands + legitimacy screen | **We match or lead** (more principled, versioned, calibratable) |
| Tailoring truthfulness | "Only true facts" policy + human diff | Evidence-grounded 3-band verdicts, cosine floor, fabrication auto-reject, metric-fabrication detection, style lint, 1-page gate | **We lead** — this is our moat |
| Cover letters | Per-role, in-your-voice, all tiers | **None** | **Gap** |
| Screening/open-ended answers | Auto-drafted "in your voice," incl. work-auth screeners | Extension has LLM fallback for odd questions; no first-class Q&A drafting/review | **Gap** |
| Apply/submission | End-to-end: login, OTP, multi-page Workday, uploads, submit; local (demo) + cloud modes; 2–3s per app | Extension autofills (4 extraction adapters, Workday shadow-DOM WIP); human submits; approved-PDF handoff not wired | **Big gap** (by design we keep the human click — but our *assist* is unfinished) |
| Approval UX | Diff view; approve from iMessage by replying "yes"; optional auto-approve | Dashboard review queue with per-edit diffs, keep/soften/drop | We're deeper per-edit; they're 10× lower-friction per-application |
| Receipts / confirmations | Fields+answers+docs receipt; ATS "application received" emails routed to tracker | Tracker stores variant + answers snapshot; **no email confirmation loop** | **Gap** — reply/confirmation routing |
| Tracking | Applied/Viewed/Replied/Interview; auto status from recruiter emails | 9-state pipeline, dedupe, pacing, callback analytics — **2 rows ever** | We lead on design; unproven in use |
| Learning loop | "Drawing on your résumé and prior submissions" | Knowledge capture + FSRS teach loop (demo-only) + autofill correction memory | We lead conceptually; not automated |
| Surfaces | 8 (web, iOS, Android, desktop, extension, iMessage, WhatsApp, MCP) | Dashboard + extension | Gap — but only 1–2 matter for a personal pilot |
| Ops maturity | Cloud workers, parallel, OAuth, mobile, bug bounty | Laptop-only, no scheduler, no auth, no backups, 110MB DBs in git | **Gap** for any deployment beyond laptop |

## 2. Why we're still stuck (root causes, not symptoms)

1. **The loop was never closed.** Every module was built to "mastery" depth
   (evidence bands, injection corpus, hybrid scoring) but the connective tissue —
   scheduler → scrape → match → tailor → morning review — was never turned on. Tsenta
   shipped a shallow loop first and deepened it later (their review UI covers only 6 of
   their ATSes!); we did the opposite. Result: world-class components, zero sustained
   operation, 2 real applications ever.
2. **Breadth starvation.** 29 companies ≈ a few dozen new relevant jobs/week. At our
   70+ gate that yields a handful of queue items — too few to feel alive, too few to
   calibrate bands, too few to justify the daily habit. Tsenta's entire pitch (be first,
   every fit) only works because sourcing is wide and fresh.
3. **The last mile is the roughest mile.** Our differentiated work ends at an approved
   PDF; the actual "apply" is still ~15–30 min of manual form-filling — the exact pain
   Tsenta erased. The extension exists but isn't finished for the top ATSes and doesn't
   even receive the approved variant. So the payoff of the whole pipeline is blunted at
   the final step, which kills motivation to run the earlier steps.
4. **Perfection-first sequencing.** Evidence: docs describe the project as "Phase 1
   resume tailoring"; six hardening FINDINGS reports and hundreds of LaTeX-injection
   artifacts predate the first sustained nightly run. Hardening beat operating.
5. **No feedback pressure.** With no daily cadence there's no data (callback rates,
   band calibration, source health) forcing prioritization — so effort flowed to
   interesting problems (ontology, FSRS) over blocking ones (a launchd plist).

## 3. What Tsenta does that we should adopt (and how)

### A. Close the loop — automation first (highest priority, smallest work)
- Make sourcing step 0 of `run_nightly` (call `scraper.run.execute_run` before
  matching), teach-gap generation a later step.
- Add a **launchd plist** (macOS-native, handles laptop sleep better than cron) that
  runs the full chain nightly with catch-up-on-wake; log to a run journal; surface
  scraper `source_health`/coverage alarms on the dashboard as a system-health card.
- Success criterion: 14 consecutive mornings where the review queue is populated with
  zero manual steps.

### B. Widen sourcing 10× (config, not code)
- We already have 8 providers; 3 are unconfigured. Expand `companies.yaml` from 29 to
  **300+ tokens** — harvest Greenhouse/Lever/Ashby/SmartRecruiters/Workable tokens
  (bootstrap_registry pattern, public token lists, YC company pages). Tsenta proves the
  public-ATS-feed approach scales to tens of thousands of companies.
- Prioritize Workday tenant coverage for target employers (our provider exists; 60 jobs
  prove it works). Keep Tier B for later; it stops blocking the pilot once direct
  coverage is wide.

### C. Finish the last mile — "assisted apply" (our human-gated answer to auto-submit)
- Wire the **approved variant PDF into the extension**: queue item → "Apply now" opens
  the job URL, extension pulls the exact `customized` PDF + profile + drafted answers
  from the backend, fills everything, human reviews and clicks submit. (Human gate
  intact — we automate everything *up to* the click, like Tsenta's demo mode but with a
  mandatory gate.)
- Add **screening-answer drafting** as a first-class tailoring output: draft answers to
  detected questions (work auth, "why us," salary) during tailoring, show them in the
  review modal next to the resume edits, store the approved set for the extension to
  fill. Evidence rule applies to answers too.
- Focus fill coverage on the 3 ATSes where our jobs actually are (Greenhouse, Lever,
  Ashby — 5,788 of 6,398 jobs) before Workday heroics.

### D. Confirmation + reply routing (their "receipt" trick, cheaply)
- The ATS "application received" email **is** the receipt. Add a lightweight IMAP/Gmail
  poll (or manual forward address) that matches confirmation/recruiter emails to tracker
  rows and auto-advances status (applied→confirmed→screen). This closes our tracker's
  observe loop and powers the callback analytics we already built.

### E. One-tap review ergonomics (their real UX insight)
- Their simplicity trick is collapsing review to "notification → approve → receipt."
  Ours can be: a **morning digest** (email/ntfy push) listing Strong matches with
  one-line "why it matched," linking straight into the review modal. Keep per-edit
  depth available, but make the default path 3 clicks: open → scan diff → approve.
- Explicitly do NOT copy: auto-approve mode (violates rule 1), 600–4,500/mo volume
  (violates rule 11 and feeds the spam problem Greenhouse is actively countering),
  credential-holding cloud submission, stealth-toward-employers posture.

### F. Cover letters (small, bounded)
- Per-role cover letter generation from the same evidence base, same three-band
  verdicts, only for Strong matches or where the ATS requires one.

## 4. What we should NOT change

- **Human gate (rule 1).** Tsenta's auto-approve + 4,500/mo tiers are exactly what 67%
  of hiring managers now say is slowing hiring, and what Greenhouse fraud detection,
  application limits, and honeypot questions are being built to kill. Our
  low-volume/high-quality/hard-gate stance is both the ethical position and the
  durable one — Tsenta's demo video itself markets the *watchable, controllable, local*
  mode because that's what users trust.
- **Evidence rule (rule 2).** Their truthfulness is a marketing sentence; ours is a
  mechanism. In a world of AI-application backlash, provable grounding is the moat.
- **Pacing caps (rule 11).** Human-scale volume is also anti-detection armor (their own
  early on-device positioning conceded cloud-scale submission gets flagged).
- **Public-feed-only sourcing (rule 4).** Their extension touches LinkedIn/Indeed pages;
  we stay off login-walled sources.
- The matcher and tailoring engines — no rework needed; they only need real data flow.

## 5. Priority order for the pilot plan (proposal)

| # | Workstream | Effort | Unblocks |
|---|---|---|---|
| 1 | Nightly automation (scrape into run_nightly + launchd + health card) | Small | Everything — the habit, the data, calibration |
| 2 | Sourcing breadth to 300+ companies | Small (config/harvest) | Enough Strong matches daily to matter |
| 3 | Approved-PDF → extension handoff + GH/Lever/Ashby fill polish | Medium | The 15-min→2-min apply payoff |
| 4 | Screening-answer drafting in tailoring + review modal | Medium | Complete per-job prep, parity with Tsenta's prep stage |
| 5 | Email confirmation/reply routing into tracker | Medium | Real receipts, callback analytics, status auto-advance |
| 6 | Morning digest notification | Small | One-tap ergonomics |
| 7 | Cover letters (Strong matches only) | Small-medium | ATSes that require them |
| 8 | Repo/ops hygiene (DBs out of git, artifacts gitignored, docs reconciled, backups) | Small | Deployability, sanity |

Deliberately deferred: Tier B aggregator, teach-loop automation, multi-profile
extension, any VM deployment, any additional surfaces (mobile/messaging/MCP).

**Pilot definition to aim at:** 2 weeks of hands-off nightly runs over 300+ companies,
≥5 Strong matches/week in the morning digest, apply flow ≤3 minutes per application via
the extension with the approved PDF, every submission dedupe-checked, paced, tracked,
and confirmed by email routing. That is Tsenta's core value delivered at personal scale
— with a hard human gate and provable truthfulness they don't have.
