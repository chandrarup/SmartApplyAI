# Tsenta Research — Doc 2: Methodology, Pipeline Flow & Tech Stack

> Research date: 2026-07-18. CONFIRMED items cite a source; INFERRED items are labeled —
> Tsenta publishes almost nothing about internals (empty GitHub org, no engineering blog,
> no HN launch, models undisclosed even on their AI-disclosure page).

## 0. Company & history (confirmed)

- Founders: Agnay Srivastava (21, CS+AI Rose-Hulman; ex-VIAVI, MLOps at GrapheneAI,
  SWE at Paytm) and Pulkit Gupta (19, CS Rose-Hulman). Team of 2, SF, YC S26, $500K.
- Origin: jointly applied to ~3,000 internships; built the tool during internships; won
  two categories at a Georgia Tech hackathon (1,500 students) → YC referral → accepted
  Dec 2025. Growth: 1,100 → 8,000 users in two months (company-supplied).
- **Originally branded AutoJobs** — the live app still runs on `autojobs.me`
  (`dashboard.autojobs.me`, `api.autojobs.me`); `tsenta.com` is the marketing/SEO layer.

## 1. End-to-end pipeline (Find → Prep → Apply → Track)

1. **Find** — continuously monitor 50,000+ company career pages across ~19 ATSes. Public
   job board shows **2,384,955 jobs** with "Just now"/"1h ago" freshness stamps. New role
   → user's match feed "within seconds" with a match % and "why it matched" breakdown
   (e.g., "New match: Stripe — 94%"). Matches can be pushed via iMessage: reply "yes" →
   application runs in background → receipt returned.
2. **Prep** — reads the JD, identifies keywords the ATS/recruiter screens for, rewrites
   the resume "using only true facts from the résumé you uploaded." Per-role resume +
   cover letter, ATS-safe formatting, diff view before send.
3. **Apply** — "the agent logs in, navigates the full ATS form, answers open-ended
   questions using your real background, uploads documents, and submits." Free-text
   answers drafted "in your voice, drawing on your résumé and prior submissions."
   Handles login flows, multi-page forms, uploads; Startuppedia adds CAPTCHA
   verification. Chrome extension "queues an application in 2–3 seconds"; processing then
   runs **in the cloud** ("continues running after browser closes").
4. **Approval** — two modes (changelog, Jul 1 2026): **Review Before Submit** (see/edit
   filled answers, tailored resume, cover letter; supported on Greenhouse, Workday,
   Ashby, Lever, Oracle, SmartRecruiters) **or auto-approve** ("fully automated. No click
   required from you").
5. **Receipt** — "the fields filled, the answers given, the résumé version sent, the
   timestamp." **This is a self-generated submission log, not an ATS-issued
   confirmation.**
6. **Track** — "recruiter emails are routed back to the right application and the status
   moves on its own"; the inbox becomes "a pipeline you can read." ATS confirmation
   emails presumably arrive via this same routing.

## 2. Per-stage methodology

### Sourcing (confirmed + inference)
- 50,000+ career pages; published ATS list: Workday, Greenhouse, Lever, Ashby,
  SmartRecruiters, iCIMS, Paylocity, Oracle Cloud, Workable, JazzHR, BambooHR, Jobvite,
  Rippling, BreezyHR, UltiPro — "covering 75%+ of Fortune 500 listings." (Homepage says
  19; blogs say "15+"; review-UI covers 6 — **coverage depth clearly varies per ATS**.)
- Crawl cadence undisclosed; "within seconds" latency implies high-frequency polling.
- **Inference:** for Greenhouse/Lever/Ashby/SmartRecruiters/Workable this is almost
  certainly the **public JSON board APIs — the same technique as SmartApplyAI's
  scraper**. Workday/iCIMS/Oracle require per-tenant crawling of hosted career-site APIs.
- The Chrome extension additionally captures postings the *user* browses on
  LinkedIn/Indeed/Glassdoor/HiringCafe — user-initiated capture, not backend scraping.

### Matching (thin public detail)
- "AI scoring that surfaces only roles you plausibly qualify for"; % fit score vs.
  resume + preferences; "why it matched" text. No algorithm, threshold, or model
  disclosed. **Inference:** embedding/LLM rerank over a preference-filtered recall set —
  nothing published distinguishes it from SmartApplyAI's recall → rerank → fit design.

### Resume rewriting / truthfulness (confirmed)
- Keyword alignment from the JD; "only true facts"; diff view before send; no
  hidden-text tricks mentioned; claims "no automated flags indicate agent usage."
- The AI-disclosure page names **no models** and **no enforcement mechanism** for
  truthfulness beyond the human diff gate. (Contrast: SmartApplyAI's evidence rule is an
  actual retrieval-backed mechanism.)

### Form filling (confirmed mechanism = cloud browser automation; tooling undisclosed)
- Logs into ATS accounts (users **upload credentials**), fills every field type via
  "intelligent form recognition," runs applications **in parallel**, in the cloud.
- Product history: earlier iteration was an **on-device desktop app** ("runs locally on
  your machine rather than cloud servers," framed as anti-bot-detection + privacy); the
  current extension listing says processing moved to the cloud. **Inference:**
  Playwright/CDP-driven headful browser workers with LLM field mapping; the
  desktop→cloud migration suggests they judged detection risk manageable or use
  undisclosed evasion (proxies/fingerprinting — nothing published).
- No visible pacing/human-scale ethics; the only rate limiting is commercial tier caps.

### Receipts / ATS confirmation
- Receipt = their own record + timestamp. Actual ATS confirmation is captured indirectly
  via **email routing** ("emails land back on the right application"). **Inference:**
  Gmail OAuth inbox integration or per-application alias/forwarding; undisclosed.

## 3. Tech stack evidence table

| Layer | Evidence | Status |
|---|---|---|
| API backend | `https://api.autojobs.me/api/v1/mcp` (HTTP MCP); 401 unauthenticated; `/docs` + `/openapi.json` 404 | Confirmed endpoint; framework unknown |
| Auth | Google OAuth; short-lived auto-refresh tokens for MCP clients | Confirmed |
| Agent integration | MCP server + CLI for Claude Code, Cursor, Codex | Confirmed |
| Execution model | Cloud application workers, parallel, survives browser close; formerly on-device desktop app | Confirmed |
| Browser automation | Undisclosed; handles logins, multi-page forms, uploads, CAPTCHAs | Inference: Playwright/CDP cloud browsers |
| LLM provider | Undisclosed everywhere, incl. ai-disclosure | Unknown |
| Chrome extension | MV3, v3.1.5 (updated 2026-07-12), ~1,000 users, 1.61 MiB; AI field matching; collects PII/auth info/website content | Confirmed |
| Messaging | iMessage + WhatsApp bots ("reply yes" approval) | Confirmed; inference: third-party iMessage gateway (Sendblue/LoopMessage-class — Apple has no bot API) |
| Mobile | iOS + Android apps | Confirmed |
| GitHub / eng presence | `github.com/tsenta` org exists, zero public repos; no eng blog; no HN launch; no job postings | Confirmed absence |
| Frontend/hosting | No public detection (builtwith empty) | Unknown |

## 3b. Official demo video (founder-published) — observed flow

Source: "Tsenta (YC S26) Demo | Automatic Job Applier," YouTube `zOzDQIbvYLc`, channel
Pulkit Gupta (co-founder). Transcript-verified claims:

1. **User-initiated queueing:** "Simply start by clicking apply on jobs you like. Queue
   up five, 6, 10, 20, even 50 jobs." — the demo flow is user-picks-jobs, not
   fully-autonomous sourcing.
2. **Tailor → approve loop:** "We tailor your resume for each job. Just hit approve." An
   **auto-approve toggle** is shown: "if you trust the AI, flip on auto approve and never
   see this screen again." Resume is directly editable in the review UI.
3. **Fill scope:** work history ("every job, every date, every description"), education,
   skills, screening questions.
4. **Hard-parts claim:** "the stuff that breaks other tools — login, OTPs, verification
   emails — handled automatically."
5. **Execution model in the demo is LOCAL:** "it all happens right here in your browser.
   You see everything… Stop at any time. You have 100% control. Nothing happens in the
   background. Nothing hidden." This matches the on-device/extension story and
   *contradicts* the cloud-worker marketing ("continues after browser closes") — both
   modes evidently exist; the demo showcases the transparent local one.
6. **Receipt = ATS confirmation emails:** the demo's proof-of-work is "six application
   received emails" — i.e., the ATS-confirmation component of receipts is the standard
   applicant confirmation email, routed back into their tracker.

## 4. Open unknowns

- LLM provider(s)/models, prompting, any evidence-grounding beyond marketing language.
- Matching algorithm, score calibration, thresholds.
- Crawl architecture/cadence for 50K pages; how Workday tenants are enumerated.
- Browser-automation framework; proxy/fingerprint strategy; how ATS account creation
  (Workday per-company accounts) scales; how CAPTCHA handling actually works
  (third-party solver?).
- Email-routing mechanism (inbox OAuth vs. aliasing).
- Whether a true ATS-side confirmation (screenshot/email) is stored vs. self-generated
  receipt.
- The 19-vs-15 ATS discrepancy.

## 5. Contrasts with SmartApplyAI's non-negotiables

- Tsenta's **human gate is optional** (auto-approve mode) — ours is absolute (rule 1).
- Submits up to 4,500/mo with **no visible pacing ethics** — we enforce caps (rule 11).
- Extension operates on LinkedIn/Indeed pages (user-initiated) — we forbid
  login-walled scraping (rule 4).
- Stores/uses **user ATS login credentials** server-side — a trust surface we don't have.
- Truthfulness is policy + human diff; ours is retrieval-backed evidence (rule 2) — we
  are *stronger* here.

## Sources

- tsenta.com: / · /ai-disclosure · /ai-agent · /auto-apply · /mcp · /messaging ·
  /changelog · /bug-bounty · /jobs · /sitemap.xml · blog posts (best-auto-apply-tools,
  best-job-application-automation-software)
- ycombinator.com/companies/tsenta (+ /jobs), workatastartup.com/companies/tsenta
- Chrome Web Store `momaobilceffibifldnhndcehhdjgaob`
- loopcv.pro/directory/tsenta, moge.ai/product/tsenta
- startuppedia.in (YC funding piece), rose-hulman.edu news
- autojobs.me · dashboard.autojobs.me · api.autojobs.me/api/v1/mcp (probed)
- github.com/tsenta (empty), HN Algolia (no hits), builtwith.com (no data)
- Unreachable: yourstory.com piece (403; snippets only), founders' LinkedIn/X
