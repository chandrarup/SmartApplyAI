# Tsenta Research — Doc 1: Feature Inventory

> Research date: 2026-07-18. Sources listed at the end. Claims are Tsenta's own marketing
> unless marked otherwise; unverified items are flagged.

## Company snapshot

- **Who:** Tsenta (tsenta.com), Y Combinator **S26** (partner: Jared Friedman). Founders:
  Agnay Srivastava (systems engineering / MLOps) and Pulkit Gupta (full-stack), both
  Rose-Hulman CS. Team of 2, SF Bay Area. X: @TsentaAi.
- **Positioning:** "Apply to every job that fits you, without lifting a finger" / "Be the
  first qualified applicant." YC one-liner: "a web app, an iMessage bot, an MCP, a Chrome
  extension" that "optimizes your profile & applies on your behalf."
- **Problem framing:** job seekers spend 15–30 min/application on data entry and apply to
  200–400 positions per cycle.
- **Traffic:** ~175,900 visits/mo, ~71% US (moge.ai estimate, unverified).
- **Pipeline framing:** four stages — **find → prep → apply → track**.

---

## 1. Job sourcing & matching

- Watches **50,000+ company career pages 24/7**; matching roles hit the user's "match
  feed" within seconds of posting — claimed "often hours before LinkedIn or Indeed index
  them."
- Each match shows a **match percentage plus reasoning**.
- AI scores each newly posted role against the user's resume + preferences (location,
  salary, experience level, role family); "surfacing only jobs you actually qualify for."
- Sources **directly from ATS-hosted career pages**, not job boards. Also: a curated job
  board, user-pasted **external job URLs** processed on demand, and SEO'd job listing
  pages at `tsenta.com/jobs/...`.
- No tier gating on matching. Third-party caveat (LoopCV review): match relevance "can
  vary at high volume."

### ATS coverage ("19 ATSes" claim)

Only **15 are ever named** publicly; the full 19 are never enumerated:

> Workday, Greenhouse, Lever, Ashby, SmartRecruiters, iCIMS, Oracle Cloud, Workable,
> Paylocity, JazzHR, BambooHR, Jobvite, Rippling, BreezyHR, UltiPro

- **Workday** is a marquee claim: logs in, navigates multi-page forms including
  conditional fields and authentication flows "that typically defeat competing tools."
- The **Review-Before-Submit** feature (shipped Jul 1, 2026) supports a *subset*:
  Greenhouse, Workday, Ashby, Lever, Oracle, SmartRecruiters — i.e., the review UI lags
  full submission coverage.

## 2. Profile & preference setup

- Dashboard profile: work history, education, skills, uploaded resume. Preferences:
  location, salary, experience level, role family.
- **Work authorization set once** (OPT, STEM-OPT, H-1B, citizen, …); Tsenta then
  (1) filters out companies that don't sponsor, and (2) auto-answers work-auth screener
  questions. Heavy marketing focus on F-1/OPT international students.
- Onboarding lives at `dashboard.tsenta.com/onboarding` (login-walled SPA; steps not
  observable). Marketing implies a short flow (mocks competitor Massive's "23-step
  onboarding") but publishes no step count.

## 3. Resume tailoring

- Per-role resume generated from the actual JD; user sees a **diff view of every change**
  with an approve/edit gate before anything is sent.
- Mechanism (site FAQ, near-verbatim): reads the JD, identifies keywords/skills the
  recruiter screens for, rewrites the resume to align — **"using only true facts from the
  résumé you uploaded."** Output billed as keyword-aligned, recruiter-readable, ATS-safe.
- Truthfulness is a **stated policy, not a verified mechanism** — no published evidence
  system behind it.
- A standalone resume-optimization tool is also included.

## 4. Cover letters

- Per-role cover letter "in your voice," from the actual JD; same diff/approval treatment;
  included in the submission receipt. All tiers.

## 5. Open-ended & screener question answering

- Drafts free-text answers ("Why this company?") "in your voice"; auto-answers yes/no
  screeners (work auth etc.). Shown in pre-submit review; captured in the receipt. All tiers.

## 6. Submission automation (the core differentiator)

- **End-to-end submission, not autofill**: the agent creates/handles ATS accounts and
  logins, completes every field, answers screeners, uploads tailored resume + cover
  letter, and **submits**.
- Claimed speed: application completed **2–3 seconds after user confirmation** —
  positioning users "inside the first-100-applicants window."
- **Two execution modes** (reconciliation is researcher inference, not stated):
  - **Cloud execution** — server-side; "your device doesn't need to stay open"; applies
    while you sleep.
  - **Local/on-device execution** — desktop app / Chrome extension runs visible browser
    automation on the user's machine with a **"live application overlay"** (watch,
    pause, edit, cancel mid-flow). Claimed benefits: privacy and evading anti-bot
    detection.
- **Automated OTP filling** on every tier (handles one-time-password verification during
  ATS logins).
- **Parallel processing** — multiple applications simultaneously (moge.ai).
- **Stealth posture:** explicitly tells users recruiters **cannot** detect automation
  ("zero recruiter visibility into automation"). The /ai-disclosure page discloses AI use
  to the *user* but has **no employer-facing disclosure**.

## 7. Human-in-the-loop / approval flow

- Default: every change (resume diff, cover letter, filled answers) shown before send;
  approve/edit/reject from any surface — including replying to an iMessage/WhatsApp
  message.
- **"Review Before Submit"** shipped Jul 1, 2026 (changelog): shows filled answers,
  tailored resume, cover letter for editing before approval (6 ATSes at launch).
- **Auto-approve/auto-submit mode exists and is optional-on** — the human gate can be
  turned off entirely ("hands-free applications"). Some marketing leans fully-automatic.
- Third-party con (LoopCV): the approval step "requires time investment."

## 8. Submission receipts & ATS confirmations

- Every submission generates a **receipt**: exact fields filled, answers given, the
  resume and cover letter that went out, what/where/when, **plus "a confirmation back
  from the ATS."**
- Framed as the proof-of-work / anti-scam feature. All tiers.

## 9. Application tracking & pipeline

- Tracker dashboard with pipeline statuses: **Applied, Viewed, Replied, Interview**.
  Dashboard supports filters (`?filter=submitted`).
- **Reply routing:** recruiter emails auto-route back to the correct application and the
  status updates itself ("the inbox stays a pipeline you can read").
- No recruiter-outreach or interview-prep features (LoopCV con).

## 10. Surfaces (8)

| Surface | Detail |
|---|---|
| Web dashboard | dashboard.tsenta.com (login-walled SPA) |
| Desktop apps | Mac, Windows, Linux — the on-device automation surface |
| Android app | Live on Google Play (`com.tsenta.tsenta`) |
| iOS app | Pending App Store review as of research date |
| Chrome extension | ID `momaobilceffibifldnhndcehhdjgaob`, 4.9/5. Companion to dashboard: queue jobs there; extension fills multi-page forms end-to-end (free-text, dropdowns, checkboxes, file uploads); review-and-submit or hands-free auto-submit; pause/resume/cancel mid-flow |
| iMessage bot | Chat with the agent: find jobs, tailor resumes, auto-apply; approve by replying |
| WhatsApp bot | Same conversational capabilities |
| MCP server / CLI | HTTP MCP at `https://api.autojobs.me/api/v1/mcp` (note the autojobs.me infra domain). `claude mcp add --transport http tsenta …` + Google OAuth ("Tsenta never sees a password"). Clients: Claude Code, Claude Desktop/Web, Cursor, Codex CLI, any MCP client. In-chat: surface matches, tailor resume, submit applications |

## 11. Pricing

| Tier | Price | Apps / 30-day cycle | Tagline |
|---|---|---|---|
| Free | $0, no card | 25 (no expiration) | Full product access |
| Starter | $19/mo | 600 | "Enough for a real job search" |
| Pro (most popular) | $39/mo | 1,500 | "For the desperate, the laid-off, the OPT-clocked" |
| Power | $99/mo | 4,500 | "Hit every match before anyone else" |

- One-time **credit packs** (never expire): $19 → 200, $39 → 600, $99 → 2,000 apps.
- Quarterly −32%, annual −36%; cancel anytime.
- **No feature gating by tier** — tiers differ only in volume. "You pay for applications.
  Not the tool."
- (Stale pre-launch snapshot on moge.ai showed "Pro $9.99/mo" desktop pricing — superseded.)

## 12. Changelog

Launched Jun 30, 2026. Entries so far: **Review Before Submit** (Jul 1) and the changelog
announcement itself. Promises to track new ATS support and reliability fixes.

## 13. Content/SEO machine (go-to-market context)

39 blog posts, almost all SEO: **21 head-to-head competitor teardowns** (vs LazyApply,
Simplify, Jobright, AIApply, Massive, Teal, Sonara, Scale.jobs, LoopCV, JobCopilot,
Huntr, ApplyPass, …) plus ~18 "best tools" listicles targeting OPT/H-1B, Workday,
bulk-apply, and agentic-AI queries. These posts are the richest source of Tsenta's own
specs (ATS list, 2–3s claim, OTP filling, cloud execution).

## 14. Social proof & claims

- "Users hired at: Goldman Sachs, JPMorgan, Intel, Netflix, NVIDIA, Stripe, Airbnb,
  Adobe" (homepage, unverified).
- Has: privacy policy, ToS, **bug bounty**, delete-account, AI disclosure pages.
- Independent coverage is thin — main third-party writeup is competitor LoopCV's
  directory review. Cons cited there: new product/limited track record, relevance
  variance at volume, approval time cost, dependence on profile quality, no
  interview-prep/outreach.

## Unverified / not observable

- The full 19-ATS list (only 15 named anywhere).
- Dashboard/onboarding internals (login-walled SPA).
- `tsenta.com/how-it-works`, `/faq`, `/chrome` are 404s (content lives on homepage anchors).
- Google Play listing details (truncated), iOS listing (not live).
- MCP tool inventory, messaging-bot command syntax, exact quarterly/annual prices.
- Cloud-vs-local execution split is researcher inference.

## Sources

- https://tsenta.com/ · /pricing · /changelog · /ai-disclosure · /blog · /mcp
- https://tsenta.com/blog/tsenta-vs-competitors · /tsenta-vs-simplify ·
  /best-ai-agents-workday-ats-portals · /best-tools-submit-job-applications-not-autofill
- https://www.ycombinator.com/companies/tsenta
- https://www.loopcv.pro/directory/tsenta/
- https://moge.ai/product/tsenta
- Chrome Web Store: `momaobilceffibifldnhndcehhdjgaob` (via search snippet)
- Google Play: `com.tsenta.tsenta` (truncated)
- https://dashboard.tsenta.com/ (login-walled)
