// LocalHire Agent — Content Script v3.0
// Architecture: MAIN-world interception → schema → adapters → section parser
// Legacy autofill flow preserved (extraction is additive, not replacement).

// ════════════════════════════════════════════════════════════════════════════
// TELEMETRY — buffered event bus, console + (optional) backend sink
// ════════════════════════════════════════════════════════════════════════════
const Telemetry = (() => {
  const BUF = [];
  const MAX = 200;
  let flushTimer = null;

  function event(name, attrs = {}) {
    const rec = {
      name, ts: Date.now(),
      url: (typeof location !== "undefined" ? location.href : ""),
      ...attrs,
    };
    BUF.push(rec);
    if (BUF.length > MAX) BUF.splice(0, BUF.length - MAX);
    try { console.debug("[lh]", name, attrs); } catch {}
    if (!flushTimer) flushTimer = setTimeout(flush, 30_000);
  }

  async function flush() {
    flushTimer = null;
    if (!BUF.length) return;
    const batch = BUF.splice(0, BUF.length);
    try {
      const apiUrl = (typeof DEFAULT_API_URL !== "undefined" ? DEFAULT_API_URL : "http://127.0.0.1:5001");
      await fetch(`${apiUrl}/telemetry/events`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ events: batch }),
        keepalive: true,
      });
    } catch (e) { /* sink may be offline — drop */ }
  }

  return { event, flush, snapshot: () => BUF.slice() };
})();

// ════════════════════════════════════════════════════════════════════════════
// SCHEMA — NormalizedJob canonical shape + hashing
// ════════════════════════════════════════════════════════════════════════════
const SCHEMA_VERSION = 1;

function emptyNormalizedJob() {
  return {
    schemaVersion: SCHEMA_VERSION,
    source: { adapter: "", method: "", capturedAt: new Date().toISOString(), url: location.href },
    identity: { externalId: null, canonicalUrl: location.href, hash: "" },
    title: { raw: "", normalized: "", level: null },
    company: { raw: "", normalized: "", domain: null },
    location: { raw: "", cities: [], countries: [], remote: null },
    employment: { type: null, durationMonths: null },
    compensation: null,
    sections: {},
    skills: { required: [], preferred: [] },
    experience: { yearsMin: null, yearsMax: null, education: [] },
    metadata: { postedAt: null, closesAt: null, sponsorshipAvailable: null },
    extraction: { confidence: {}, selectorVersionsUsed: [], latencyMs: 0, warnings: [] },
  };
}

async function sha1Hex(str) {
  try {
    const buf = await crypto.subtle.digest("SHA-1", new TextEncoder().encode(str));
    return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, "0")).join("");
  } catch {
    let h = 0; for (let i = 0; i < str.length; i++) { h = (h<<5)-h + str.charCodeAt(i); h |= 0; }
    return ("0000000" + (h>>>0).toString(16)).slice(-8);
  }
}

// ════════════════════════════════════════════════════════════════════════════
// SECTION PARSER — deterministic JD section extraction
// ════════════════════════════════════════════════════════════════════════════
const SECTION_PATTERNS = {
  responsibilities: [/^responsibilit/i, /what you('|')?ll do/i, /^the role/i, /day[- ]to[- ]day/i, /^key duties/i, /your impact/i],
  qualifications:   [/qualification/i, /^requirement/i, /^you have/i, /what we('|')?re looking for/i, /must have/i, /minimum (qualifications|requirements)/i],
  preferred:        [/^preferred/i, /nice to have/i, /^bonus/i, /^plus(es)?$/i],
  benefits:         [/^benefits/i, /^perks/i, /what we offer/i, /^compensation/i],
  about:            [/^about (us|the company|the team)/i, /who we are/i, /our mission/i],
  visa:             [/visa|sponsor|h.?1.?b|work auth/i],
};

function classifyHeading(text) {
  if (!text) return null;
  const t = text.trim().slice(0, 100);
  for (const [id, patterns] of Object.entries(SECTION_PATTERNS)) {
    if (patterns.some(p => p.test(t))) return id;
  }
  return null;
}

function parseSections(root) {
  if (!root) return {};
  const sections = {};
  const headingSel = "h1, h2, h3, h4, h5, h6, [role='heading'], strong";
  const candidates = Array.from(root.querySelectorAll(headingSel));

  for (const h of candidates) {
    const text = (h.innerText || h.textContent || "").trim();
    if (!text || text.length > 80) continue;
    const sectionId = classifyHeading(text);
    if (!sectionId || sections[sectionId]) continue;

    const collected = { text: "", bullets: [] };
    let node = h.nextElementSibling;
    let safety = 200;
    while (node && safety-- > 0) {
      if (node.matches && node.matches(headingSel)) {
        const nextText = (node.innerText || node.textContent || "").trim();
        if (classifyHeading(nextText)) break;
      }
      const txt = (node.innerText || node.textContent || "").trim();
      if (txt) collected.text += (collected.text ? "\n" : "") + txt;
      if (node.tagName === "UL" || node.tagName === "OL") {
        node.querySelectorAll("li").forEach(li => {
          const t = (li.innerText || li.textContent || "").trim();
          if (t && t.length > 5) collected.bullets.push(t);
        });
      }
      node = node.nextElementSibling;
    }
    if (collected.text || collected.bullets.length) sections[sectionId] = collected;
  }
  return sections;
}

// ════════════════════════════════════════════════════════════════════════════
// SELECTOR FINDER — versioned chain with confidence
// ════════════════════════════════════════════════════════════════════════════
function findField(selectorChain, root) {
  const scope = root || document;
  for (const candidate of selectorChain) {
    try {
      const el = scope.querySelector(candidate.sel);
      if (el) {
        Telemetry.event("selector.hit", { key: candidate.key, version: candidate.v });
        return { el, version: candidate.v, confidence: candidate.confidence ?? 1.0 };
      }
    } catch (e) {}
  }
  Telemetry.event("selector.miss", { key: selectorChain[0]?.key });
  return null;
}

// ════════════════════════════════════════════════════════════════════════════
// CAPTURE BUS — receives messages from injected.js MAIN world
// ════════════════════════════════════════════════════════════════════════════
const CaptureBus = (() => {
  const buf = [];
  const subscribers = new Set();
  const MAX = 50;

  window.addEventListener("message", (ev) => {
    if (ev.source !== window) return;
    const msg = ev.data;
    if (!msg || msg.source !== "__lh_capture__") return;
    const rec = msg.record;
    buf.push(rec);
    if (buf.length > MAX) buf.shift();
    Telemetry.event("capture.received", { kind: rec.kind, url: rec.url, latencyMs: rec.latencyMs });
    for (const fn of subscribers) {
      try { fn(rec); } catch {}
    }
  }, false);

  return {
    subscribe: (fn) => { subscribers.add(fn); return () => subscribers.delete(fn); },
    snapshot: () => buf.slice(),
    find: (pred) => buf.find(pred),
  };
})();

// Inject the MAIN-world interceptor (must run at document_start)
(function injectInterceptor() {
  if (window.top !== window) return;
  try {
    if (typeof chrome === "undefined" || !chrome.runtime?.getURL) return;
    const url = chrome.runtime.getURL("injected.js");
    const s = document.createElement("script");
    s.src = url;
    s.async = false;
    (document.head || document.documentElement).appendChild(s);
    s.onload = () => { s.remove(); Telemetry.event("interceptor.injected"); };
  } catch (e) {
    Telemetry.event("interceptor.inject_failed", { error: e.message });
  }
})();

// ════════════════════════════════════════════════════════════════════════════
// ADAPTERS — portal-specific extraction
// ════════════════════════════════════════════════════════════════════════════
const ADAPTERS = {};

ADAPTERS.lever = {
  id: "lever",
  detect() {
    const h = location.hostname.toLowerCase();
    let conf = 0;
    if (/lever\.co$/i.test(h) || /jobs\.lever\.co/i.test(h)) conf = 0.95;
    if (document.querySelector(".posting-page, .posting, [class*='lever']")) conf = Math.max(conf, 0.7);
    return { confidence: conf };
  },
  async extract() {
    const job = emptyNormalizedJob();
    job.source.adapter = "lever";
    const t0 = performance.now();

    // 1. Try captured API response first
    const apiRecord = CaptureBus.find(r =>
      r.payloadType === "json" && /lever\.co.*\/postings|api\/v0\/postings/.test(r.url || "")
    );
    if (apiRecord && apiRecord.payload) {
      try {
        const p = apiRecord.payload;
        const posting = p.text ? p : (p.data || p);
        job.source.method = "api";
        job.title.raw = posting.text || posting.title || "";
        job.company.raw = posting.categories?.team || posting.team || "";
        job.location.raw = posting.categories?.location || "";
        job.location.remote = /remote/i.test(job.location.raw) ? "remote" : null;
        job.employment.type = (posting.categories?.commitment || "").toLowerCase().includes("full") ? "fulltime" : null;
        job.sections.about = { text: posting.descriptionPlain || posting.description || "", bullets: [] };
        if (posting.lists) {
          for (const list of posting.lists) {
            const sec = classifyHeading(list.text || "") || "responsibilities";
            if (!job.sections[sec]) {
              job.sections[sec] = {
                text: list.content?.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim() || "",
                bullets: [],
              };
            }
          }
        }
        job.extraction.confidence = { title: 1.0, company: 0.8, location: 0.9, sections: 0.95 };
        job.extraction.latencyMs = Math.round(performance.now() - t0);
        return job;
      } catch (e) {
        job.extraction.warnings.push("api_parse_failed: " + e.message);
      }
    }

    // 2. JSON-LD fallback
    const jsonLd = CaptureBus.find(r => r.kind === "hydration" && r.payload?.jsonLd)?.payload.jsonLd;
    if (jsonLd) {
      job.source.method = "json-ld";
      job.title.raw = jsonLd.title || "";
      job.company.raw = jsonLd.hiringOrganization?.name || "";
      job.location.raw = jsonLd.jobLocation?.address?.addressLocality || "";
      job.sections.responsibilities = { text: (jsonLd.description || "").replace(/<[^>]+>/g, " "), bullets: [] };
      job.extraction.confidence = { title: 1.0, company: 0.9, sections: 0.7 };
      job.extraction.latencyMs = Math.round(performance.now() - t0);
      return job;
    }

    // 3. DOM fallback — Lever posting-page
    job.source.method = "dom";
    const root = document.querySelector(".posting-page, .posting-content, main") || document.body;
    const titleEl = root.querySelector(".posting-headline h2, h1");
    if (titleEl) job.title.raw = (titleEl.innerText || titleEl.textContent || "").trim();
    job.sections = parseSections(root);
    job.extraction.confidence = { title: titleEl ? 0.7 : 0.2, sections: Object.keys(job.sections).length > 0 ? 0.6 : 0.2 };
    job.extraction.latencyMs = Math.round(performance.now() - t0);
    return job;
  },
};

ADAPTERS.workday = {
  id: "workday",
  detect() {
    const h = location.hostname.toLowerCase();
    let conf = 0;
    if (/myworkdayjobs\.com|workday\.com/.test(h)) conf = 0.95;
    if (document.querySelector("[data-automation-id]")) conf = Math.max(conf, 0.85);
    return { confidence: conf };
  },
  async extract() {
    const job = emptyNormalizedJob();
    job.source.adapter = "workday";
    const t0 = performance.now();

    // 1. GraphQL/API capture — Workday emits /jobPosting endpoint
    const apiRecord = CaptureBus.find(r =>
      r.payloadType === "json" && /jobPosting|workday/i.test(r.url || "")
    );
    if (apiRecord && apiRecord.payload?.jobPostingInfo) {
      const j = apiRecord.payload.jobPostingInfo;
      job.source.method = "api";
      job.title.raw = j.title || "";
      job.location.raw = j.location || "";
      job.sections.responsibilities = { text: (j.jobDescription || "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim(), bullets: [] };
      job.extraction.confidence = { title: 1.0, sections: 0.95 };
      job.extraction.latencyMs = Math.round(performance.now() - t0);
      return job;
    }

    // 2. DOM fallback — workday uses data-automation-id everywhere
    job.source.method = "dom";
    const titleEl = document.querySelector('[data-automation-id="jobPostingHeader"], h2[data-automation-id]');
    if (titleEl) job.title.raw = (titleEl.innerText || "").trim();
    const descEl = document.querySelector('[data-automation-id="jobPostingDescription"]');
    if (descEl) job.sections = parseSections(descEl);
    job.extraction.confidence = { title: titleEl ? 0.8 : 0.3, sections: Object.keys(job.sections).length ? 0.7 : 0.2 };
    job.extraction.latencyMs = Math.round(performance.now() - t0);
    return job;
  },
};

ADAPTERS.ashby = {
  id: "ashby",
  detect() {
    const h = location.hostname.toLowerCase();
    if (/ashbyhq\.com/.test(h)) return { confidence: 0.95 };
    return { confidence: 0 };
  },
  async extract() {
    const job = emptyNormalizedJob();
    job.source.adapter = "ashby";
    const t0 = performance.now();

    // Ashby is a React SPA — hydration payload has structured data
    const hyd = CaptureBus.find(r => r.kind === "hydration" && r.payload?.appData);
    const ashby = hyd?.payload?.appData?.posting || hyd?.payload?.ashby;
    if (ashby) {
      job.source.method = "hydration";
      job.title.raw = ashby.title || "";
      job.company.raw = ashby.organizationName || "";
      job.location.raw = ashby.locationName || ashby.secondaryLocations?.[0]?.locationName || "";
      job.employment.type = (ashby.employmentType || "").toLowerCase().includes("full") ? "fulltime" : null;
      job.sections.responsibilities = {
        text: ashby.descriptionPlainText || (ashby.descriptionHtml || "").replace(/<[^>]+>/g, " "),
        bullets: [],
      };
      job.extraction.confidence = { title: 1.0, company: 0.95, location: 0.9, sections: 0.9 };
      job.extraction.latencyMs = Math.round(performance.now() - t0);
      return job;
    }

    // Fallback: try to read window.__appData via injected probe wasn't enough — try directly
    try {
      const a = (typeof window !== "undefined" && window.__appData) || null;
      const p = a?.posting;
      if (p) {
        job.source.method = "global";
        job.title.raw = p.title || "";
        job.sections.responsibilities = { text: p.descriptionPlainText || "", bullets: [] };
        job.extraction.confidence = { title: 0.9, sections: 0.85 };
        job.extraction.latencyMs = Math.round(performance.now() - t0);
        return job;
      }
    } catch (e) {}

    // DOM last resort
    job.source.method = "dom";
    job.sections = parseSections(document.body);
    job.extraction.confidence = { sections: 0.4 };
    job.extraction.latencyMs = Math.round(performance.now() - t0);
    return job;
  },
};

ADAPTERS.generic = {
  id: "generic",
  detect() { return { confidence: 0.1 }; }, // always wins fallback
  async extract() {
    const job = emptyNormalizedJob();
    job.source.adapter = "generic";
    job.source.method = "dom";
    const t0 = performance.now();
    // Try JSON-LD first
    const ld = CaptureBus.find(r => r.kind === "hydration" && r.payload?.jsonLd)?.payload.jsonLd;
    if (ld) {
      job.source.method = "json-ld";
      job.title.raw = ld.title || "";
      job.company.raw = ld.hiringOrganization?.name || "";
      job.location.raw = ld.jobLocation?.address?.addressLocality || "";
      job.sections.responsibilities = { text: (ld.description || "").replace(/<[^>]+>/g, " "), bullets: [] };
      job.extraction.confidence = { title: 1.0, company: 0.9, sections: 0.7 };
    } else {
      job.sections = parseSections(document.body);
      job.extraction.confidence = { sections: 0.3 };
    }
    job.extraction.latencyMs = Math.round(performance.now() - t0);
    return job;
  },
};

// ════════════════════════════════════════════════════════════════════════════
// ORCHESTRATOR — pick adapter by detection confidence, run extract pipeline
// ════════════════════════════════════════════════════════════════════════════
async function extractJob() {
  const t0 = performance.now();
  // Detect: highest confidence adapter wins (generic always has 0.1)
  let best = ADAPTERS.generic, bestConf = 0.1;
  for (const ad of Object.values(ADAPTERS)) {
    if (ad.id === "generic") continue;
    try {
      const det = await ad.detect();
      if (det.confidence > bestConf) { best = ad; bestConf = det.confidence; }
    } catch (e) {}
  }
  Telemetry.event("adapter.selected", { adapter: best.id, confidence: bestConf });

  let job;
  try {
    job = await best.extract();
  } catch (e) {
    Telemetry.event("adapter.failed", { adapter: best.id, error: e.message });
    job = await ADAPTERS.generic.extract();
  }

  // Normalize
  job.title.normalized = (job.title.raw || "").trim();
  job.company.normalized = (job.company.raw || "").trim();
  job.location.cities = job.location.raw ? [job.location.raw.split(",")[0].trim()] : [];
  job.location.remote = job.location.remote || (/remote/i.test(job.location.raw) ? "remote" : null);
  job.identity.hash = await sha1Hex([job.company.normalized, job.title.normalized, job.location.cities[0] || ""].join("|"));

  job.extraction.latencyMs = Math.round(performance.now() - t0);
  Telemetry.event("extraction.done", {
    adapter: job.source.adapter,
    method: job.source.method,
    latencyMs: job.extraction.latencyMs,
    confidence: job.extraction.confidence,
    sectionsFound: Object.keys(job.sections),
  });
  return job;
}

// Expose to legacy code
window.LH = window.LH || {};
window.LH.extract = extractJob;
window.LH.Telemetry = Telemetry;
window.LH.CaptureBus = CaptureBus;

function normalizedJobToJobContext(job) {
  const sectionText = Object.values(job.sections || {})
    .map(sec => [sec?.text || "", ...(sec?.bullets || [])].join("\n"))
    .filter(Boolean)
    .join("\n\n")
    .trim();
  return {
    title: (job.title?.normalized || job.title?.raw || "").trim(),
    company: (job.company?.normalized || job.company?.raw || "").trim(),
    location: (job.location?.raw || "").trim(),
    jdText: sectionText,
    sourceAdapter: job.source?.adapter || "generic",
    confidence: job.extraction?.confidence || {},
  };
}

// ════════════════════════════════════════════════════════════════════════════
// LEGACY (autofill + panel) — preserved below
// ════════════════════════════════════════════════════════════════════════════
// LocalHire Agent — Content Script v2.2
// Platform detection, field mapping, and auto-fill logic for 8 ATS platforms

// ─────────────────────────────────────────────
// FIELD LABEL PATTERN MATCHING
// ─────────────────────────────────────────────
const FIELD_PATTERNS = [
  // Identity — more specific FIRST
  { key: "first_name",  patterns: [/^first\s*name/i, /^first$/i, /given\s*name/i, /forename/i] },
  { key: "last_name",   patterns: [/^last\s*name/i, /^last$/i, /family\s*name/i, /surname/i] },
  { key: "full_name",   patterns: [/^(full\s*)?name$/i, /^your\s*name/i, /applicant\s*name/i] },
  // Contact
  { key: "email",       patterns: [/e[\s-]?mail/i] },
  // Phone — exclude extension/country-code labels
  { key: "phone_extension", patterns: [/phone\s*ext|extension/i] },
  { key: "phone_country",   patterns: [/country\s*phone\s*code|phone\s*code|country\s*code/i] },
  { key: "phone_device",    patterns: [/phone\s*device|device\s*type/i] },
  { key: "phone",           patterns: [/^phone$|phone\s*number|mobile\s*(number|phone)?$|^cell(\s*number)?$|^telephone$/i] },
  // Location — city MUST be before state (avoid "City, State, Zip" matching state)
  { key: "city",        patterns: [/^city$/i, /^town$/i, /location.*city/i, /city.*state/i, /candidate.?location/i] },
  { key: "state",       patterns: [/^state$/i, /^province$/i, /^state\s*[\/\\]?\s*province$/i] },
  { key: "zip",         patterns: [/zip|postal\s*code/i] },
  { key: "country",     patterns: [/^country$/i] },
  // Professional links
  { key: "linkedin",    patterns: [/linkedin/i] },
  { key: "github",      patterns: [/github/i] },
  { key: "website",     patterns: [/website|portfolio|personal\s*url|personal\s*site/i] },
  // Work eligibility — MUST be before salary/state to avoid false matches
  { key: "work_authorization", patterns: [
    /work\s*auth/i, /legally\s*(authorized|eligible)/i,
    /authorized\s*to\s*work/i, /eligible\s*to\s*work/i,
    /currently\s*eligible\s*to\s*work/i,
    /authorized.*without.*sponsor/i,
  ]},
  { key: "requires_sponsorship", patterns: [
    /require.*sponsor/i, /visa\s*sponsor/i, /need.*sponsor/i,
    /now\s*or.*future.*sponsor/i, /future.*require.*sponsor/i,
    /\bsponsor\b/i,
  ]},
  // Compensation & schedule
  { key: "salary",           patterns: [/salary|compensation|pay\s*expect|desired\s*pay|wage|expected\s*salary/i] },
  { key: "years_experience", patterns: [/years?\s*(of\s*)?experience/i, /experience.*years/i] },
  { key: "start_date",       patterns: [/start\s*date|available.*date|earliest\s*start|when.*available/i] },
  { key: "relocate",         patterns: [/relocat/i, /willing\s*to\s*move/i] },
  { key: "notice_period",    patterns: [/notice\s*period|weeks?\s*notice/i] },
  // Address
  { key: "address_line1", patterns: [/^address(\s*line\s*1)?$/i, /street\s*address/i] },
  // Current employment
  { key: "current_company", patterns: [/current\s*(company|employer|organization)/i, /present\s*employer/i] },
  { key: "current_title",   patterns: [/current\s*(job\s*)?(title|position|role)/i, /present\s*title/i, /job\s*title/i] },
  // EEO
  { key: "gender",     patterns: [/^gender$/i, /gender\s*identity/i, /identify.*gender/i, /i\s*identify\s*my\s*gender/i] },
  { key: "veteran",    patterns: [/veteran/i, /military\s*status/i] },
  { key: "disability", patterns: [/disability|disabled/i] },
  { key: "ethnicity",  patterns: [/ethnic|race\b|racial/i, /identify.*ethnicity/i, /i\s*identify\s*my\s*ethnicity/i] },
  // Open-ended
  { key: "summary",      patterns: [/summary|tell\s*us\s*about|about\s*yourself|introduce\s*yourself|professional\s*summary|^background$/i] },
  { key: "cover_letter", patterns: [/cover\s*letter/i] },
  // Referral
  { key: "referral", patterns: [/referral|how\s*did\s*you\s*(hear|find|learn|know)|referred\s*by|source\s*of\s*hire/i] },
  { key: "pronouns", patterns: [/pronouns/i] },
  // Generic date / today
  { key: "today_date", patterns: [/^date$|^\d+\.?\s*date:?$|today.?s?\s*date|signature\s*date/i] },
  { key: "travel_pct", patterns: [/how\s*much\s*%|travel\s*percent|%.*travel/i] },
  // Compliance / legal yes-no
  { key: "age_18_or_over",     patterns: [/18\s*years?\s*of\s*age|at\s*least\s*18|age.*requirement|are\s*you\s*18/i] },
  { key: "has_relatives",      patterns: [/relative|family\s*member.*employ|employ.*family|know\s*anyone\s*(at|who\s*works)/i] },
  { key: "has_noncompete",     patterns: [/non.?compete|restrictive\s*(covenant|agreement)|non.?solicit/i] },
  { key: "currently_employed", patterns: [/currently\s*employ|are\s*you\s*(currently|presently)\s*employ/i] },
  { key: "available_to_start", patterns: [/available.*start|earliest.*start|when.*start|start.*availability/i] },
];

// ─────────────────────────────────────────────
// SENSITIVE FIELD DETECTION (skip — never infer)
// ─────────────────────────────────────────────
const SENSITIVE_PATTERNS = [
  /vaccin|covid|booster|immuniz/i,
  /health\s*(condition|status|insurance)/i,
  /medical\s*(condition|history|record)/i,
  /hiv|aids|cancer|diagnosis/i,
  /political\s*(affili|party|view)/i,
  /religion|religious\s*(belief|practice)/i,
  /pregnan|maternity|parental\s*leave/i,
  /criminal\s*record|felony|arrest|conviction/i,
  /drug\s*(test|screen|use)|substance\s*abuse/i,
  /social\s*security\s*number|\bssn\b/i,
  /passport\s*number/i,
];

function isSensitiveField(labelText) {
  if (!labelText) return false;
  return SENSITIVE_PATTERNS.some(p => p.test(labelText));
}

// ─────────────────────────────────────────────
// LEVER NAME-ATTRIBUTE → LABEL MAP
// Real Lever fields use name= not labels
// ─────────────────────────────────────────────
const LEVER_NAME_LABELS = {
  "name":                "Full Name",
  "email":               "Email",
  "phone":               "Phone",
  "org":                 "Current Company",
  "urls[LinkedIn]":      "LinkedIn URL",
  "urls[GitHub]":        "GitHub URL",
  "urls[Portfolio]":     "Portfolio",
  "urls[Other]":         "Website",
  "comments":            "Tell us about yourself",
};

// ─────────────────────────────────────────────
// BAMBOOHR camelCase id → readable label map
// ─────────────────────────────────────────────
const BAMBOOHR_ID_LABELS = {
  "firstName":          "First Name",
  "lastName":           "Last Name",
  "email":              "Email Address",
  "phoneNumber":        "Phone Number",
  "city":               "City",
  "state":              "State / Province",
  "zip":                "Zip Code",
  "country":            "Country",
  "linkedin":           "LinkedIn Profile",
  "websitePortfolio":   "Website or Portfolio",
  "currentEmployer":    "Current Company",
  "currentJobTitle":    "Current Job Title",
  "workAuthorization":  "Are you authorized to work in the US?",
  "requireSponsorship": "Do you require sponsorship?",
  "yearsExperience":    "Years of Relevant Experience",
  "desiredSalary":      "Desired Salary",
  "howDidYouHearAboutUs": "How did you hear about this position?",
  "coverLetter":        "Tell us about yourself",
};

// ─────────────────────────────────────────────
// TALEO ftl-prefix id → readable label map
// ─────────────────────────────────────────────
const TALEO_ID_LABELS = {
  "ftlFirstName":      "First Name",
  "ftlLastName":       "Last Name",
  "ftlEmail":          "Email Address",
  "ftlPhone":          "Phone Number",
  "ftlCity":           "City",
  "ftlState":          "State / Province",
  "ftlCountry":        "Country",
  "ftlCurrentEmployer": "Current Company",
  "ftlCurrentTitle":   "Current Job Title",
  "ftlLinkedIn":       "LinkedIn URL",
  "ftlYearsExp":       "Years of Experience",
  "ftlSalary":         "Desired Salary",
  "ftlWorkAuth":       "Are you authorized to work in the US?",
  "ftlSponsorship":    "Do you require sponsorship?",
  "ftlGender":         "Gender",
  "ftlVeteran":        "Veteran Status",
  "ftlSummary":        "Tell us about yourself",
};

function matchFieldKey(labelText) {
  if (!labelText) return null;
  const text = labelText.trim();
  for (const { key, patterns } of FIELD_PATTERNS) {
    if (patterns.some(p => p.test(text))) return key;
  }
  return null;
}

// ─────────────────────────────────────────────
// STATE ABBREVIATION MAP (BUG 3 — select fallback)
// ─────────────────────────────────────────────
const STATE_ABBREVIATIONS = {
  "alabama":"AL","alaska":"AK","arizona":"AZ","arkansas":"AR","california":"CA",
  "colorado":"CO","connecticut":"CT","delaware":"DE","florida":"FL","georgia":"GA",
  "hawaii":"HI","idaho":"ID","illinois":"IL","indiana":"IN","iowa":"IA","kansas":"KS",
  "kentucky":"KY","louisiana":"LA","maine":"ME","maryland":"MD","massachusetts":"MA",
  "michigan":"MI","minnesota":"MN","mississippi":"MS","missouri":"MO","montana":"MT",
  "nebraska":"NE","nevada":"NV","new hampshire":"NH","new jersey":"NJ","new mexico":"NM",
  "new york":"NY","north carolina":"NC","north dakota":"ND","ohio":"OH","oklahoma":"OK",
  "oregon":"OR","pennsylvania":"PA","rhode island":"RI","south carolina":"SC",
  "south dakota":"SD","tennessee":"TN","texas":"TX","utah":"UT","vermont":"VT",
  "virginia":"VA","washington":"WA","west virginia":"WV","wisconsin":"WI","wyoming":"WY",
  "district of columbia":"DC",
};

// ─────────────────────────────────────────────
// NATIVE INPUT SETTER (React/Angular/Vue compatible)
// ─────────────────────────────────────────────
function setNativeValue(element, value) {
  // BUG 4: Handle contenteditable divs (SmartRecruiters, newer Lever, BambooHR)
  if (element.contentEditable === "true" || element.getAttribute("role") === "textbox") {
    element.focus();
    try {
      const sel = window.getSelection();
      const range = document.createRange();
      range.selectNodeContents(element);
      sel.removeAllRanges(); sel.addRange(range);
      document.execCommand("insertText", false, String(value));
    } catch (e) {
      element.innerText = String(value);
    }
    element.dispatchEvent(new Event("input",  { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
    element.dispatchEvent(new Event("blur",   { bubbles: true }));
    return;
  }
  const proto = element.tagName === "TEXTAREA"
    ? window.HTMLTextAreaElement.prototype
    : window.HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
  if (setter) setter.call(element, value);
  else element.value = value;
  element.dispatchEvent(new Event("input",  { bubbles: true }));
  element.dispatchEvent(new Event("change", { bubbles: true }));
  element.dispatchEvent(new Event("blur",   { bubbles: true }));
}

function setSelectValue(element, value) {
  if (!value) return false;
  const valLower = value.toString().toLowerCase().trim();
  let bestOption = null, partialMatch = null;
  for (const opt of element.options) {
    const t = opt.text.toLowerCase().trim(), v = opt.value.toLowerCase().trim();
    if (t === valLower || v === valLower) { bestOption = opt; break; }
    if (!partialMatch && (t.includes(valLower) || valLower.includes(t) || v.includes(valLower))) {
      partialMatch = opt;
    }
  }
  // BUG 3: State abbreviation fallback — "TX" matches "Texas" and vice versa
  if (!bestOption && !partialMatch) {
    const abbrev = STATE_ABBREVIATIONS[valLower];
    const fullName = Object.keys(STATE_ABBREVIATIONS).find(k => STATE_ABBREVIATIONS[k] === valLower.toUpperCase());
    const tryVal = (abbrev || fullName || "").toLowerCase();
    if (tryVal) {
      for (const opt of element.options) {
        const t = opt.text.toLowerCase().trim(), v = opt.value.toLowerCase().trim();
        if (t === tryVal || v === tryVal || t.includes(tryVal)) { partialMatch = opt; break; }
      }
    }
  }
  const chosen = bestOption || partialMatch;
  if (chosen) {
    element.value = chosen.value;
    // BUG 3: Fire both input + change — React uses change, Angular/Vue use input
    element.dispatchEvent(new Event("input",  { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
    element.dispatchEvent(new Event("blur",   { bubbles: true }));
    return true;
  }
  return false;
}

function setRadioGroup(container, value) {
  if (!value || !container) return false;
  const valLower = value.toString().toLowerCase();
  const inputs = container.querySelectorAll('input[type="radio"]');
  for (const input of inputs) {
    const lbl = getLabelForInput(input)?.toLowerCase() || "";
    const val = (input.value || "").toLowerCase();
    if (lbl.includes(valLower) || val === valLower || (valLower.includes("yes") && val === "yes") || (valLower.includes("no") && val === "no")) {
      input.checked = true;
      input.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    }
  }
  return false;
}

// ─────────────────────────────────────────────
// LABEL EXTRACTION — platform-aware
// ─────────────────────────────────────────────
function getLabelForInput(input, platform) {
  // 1. aria-label
  const ariaLabel = input.getAttribute("aria-label");
  if (ariaLabel && ariaLabel.trim()) return ariaLabel.trim();

  // 1b. aria-labelledby — resolves space-separated list of IDs (BUG 1 fix)
  // Workday, SuccessFactors, and newer Greenhouse versions use this extensively
  const labelledBy = input.getAttribute("aria-labelledby");
  if (labelledBy) {
    const texts = labelledBy.trim().split(/\s+/)
      .map(id => { const el = document.getElementById(id); return el ? (el.innerText || el.textContent || "").replace(/[\s*✱]+$/, "").trim() : ""; })
      .filter(Boolean);
    if (texts.length) return texts.join(" ");
  }

  // 2. Lever: use name-to-label map
  if (platform === "lever") {
    const name = input.name || "";
    if (LEVER_NAME_LABELS[name]) return LEVER_NAME_LABELS[name];
    // Custom question cards: name=cards[work_authorization][field0]
    const cardMatch = name.match(/cards\[([^\]]+)\]/);
    if (cardMatch) {
      // Fall back to nearby label
      const lbl = input.closest(".application-field, .form-field, .field, div")?.querySelector("label");
      if (lbl) return (lbl.innerText||lbl.textContent||"").replace(/[\s*]+$/, "").trim();
    }
  }

  // 3. BambooHR: camelCase id map
  if (platform === "bamboohr") {
    if (BAMBOOHR_ID_LABELS[input.id]) return BAMBOOHR_ID_LABELS[input.id];
  }

  // 4. Taleo: ftl-prefixed id map
  if (platform === "taleo") {
    if (TALEO_ID_LABELS[input.id]) return TALEO_ID_LABELS[input.id];
    // Also check if name attr starts with a capital (Taleo uses FirstName, LastName)
    if (input.name) {
      const readable = input.name.replace(/([A-Z])/g, " $1").trim();
      if (readable) return readable;
    }
  }

  // 5. label[for=id]
  if (input.id) {
    try {
      const escaped = (typeof CSS !== "undefined" && CSS.escape) ? CSS.escape(input.id) : input.id.replace(/[^\w-]/g, "\\$&");
      const lbl = document.querySelector(`label[for="${escaped}"]`);
      if (lbl) return (lbl.innerText || lbl.textContent || "").replace(/[\s*✱]+$/, "").trim();
    } catch (e) {}
  }

  // 6. Closest wrapping label
  const pLabel = input.closest("label");
  if (pLabel) return (pLabel.innerText||pLabel.textContent||"").replace(/[\s*]+$/, "").trim();

  // 7. iCIMS: look in iCIMS_Label td sibling
  const icimsRow = input.closest("tr");
  if (icimsRow) {
    const lblCell = icimsRow.querySelector("td.iCIMS_Label label, td label");
    if (lblCell) return (lblCell.innerText||lblCell.textContent||"").replace(/[\s*]+$/, "").trim();
  }

  // 8. Previous sibling label
  let prev = input.previousElementSibling;
  while (prev) {
    const tag = prev.tagName;
    if (tag === "LABEL" || prev.classList.contains("label") || prev.getAttribute("role") === "label") {
      return (prev.innerText||prev.textContent||"").replace(/[\s*]+$/, "").trim();
    }
    prev = prev.previousElementSibling;
  }

  // 9. Container label (generic form-group, iCIMS sections, etc.)
  const container = input.closest(".form-group, .field, .question, .form-field, [data-field], .fab-field, .application-field, .sr-field, .li-field, .wd-field");
  if (container) {
    const lbl = container.querySelector("label, .label, .field-label, legend, .question-label");
    if (lbl) return (lbl.innerText||lbl.textContent||"").replace(/[\s*✱]+$/, "").trim();
  }

  // 10. Placeholder or name as last resort
  return input.placeholder || input.name || input.id || "";
}

// ─────────────────────────────────────────────
// PLATFORM DETECTORS
// ─────────────────────────────────────────────
function detectPlatform() {
  const h = location.hostname;
  const path = location.pathname;
  if (/myworkdayjobs\.com|workday\.com/.test(h)) return "workday";
  if (/greenhouse\.io/.test(h)) return "greenhouse";
  if (/lever\.co/.test(h) && /\/apply/.test(path)) return "lever";
  if (/lever\.co/.test(h)) return "lever";
  if (/bamboohr\.com/.test(h)) return "bamboohr";
  if (/icims\.com/.test(h)) return "icims";
  if (/smartrecruiters\.com/.test(h)) return "smartrecruiters";
  if (/linkedin\.com/.test(h) && document.querySelector('.jobs-easy-apply-modal, [data-test-modal-id="easy-apply-modal"]')) return "linkedin";
  if (/taleo\.net/.test(h)) return "taleo";
  if (/successfactors\.(com|eu)/.test(h)) return "successfactors";
  if (/jobvite\.com/.test(h)) return "jobvite";
  if (/ashbyhq\.com/.test(h)) return "ashby";
  if (/rippling\.com|ripplinghris\.com/.test(h)) return "rippling";
  if (/paycom\.com/.test(h)) return "paycom";
  if (/careers\.google\.com/.test(h)) return "greenhouse";
  // Test pages
  if (/test\/lever/.test(path)) return "lever";
  if (/test\/bamboohr/.test(path)) return "bamboohr";
  if (/test\/icims/.test(path)) return "icims";
  if (/test\/smartrecruiters/.test(path)) return "smartrecruiters";
  if (/test\/linkedin/.test(path)) return "linkedin";
  if (/test\/taleo/.test(path)) return "taleo";
  if (/test\/greenhouse/.test(path)) return "greenhouse";
  if (/test\/workday/.test(path)) return "workday";
  return "generic";
}

function getPlatformName(key) {
  const names = {
    workday: "Workday", greenhouse: "Greenhouse", lever: "Lever",
    bamboohr: "BambooHR", icims: "iCIMS", smartrecruiters: "SmartRecruiters",
    linkedin: "LinkedIn Easy Apply", taleo: "Taleo",
    successfactors: "SAP SuccessFactors", jobvite: "Jobvite",
    ashby: "Ashby", rippling: "Rippling", paycom: "Paycom", generic: "Generic",
  };
  return names[key] || "Unknown";
}

// ─────────────────────────────────────────────
// PLATFORM-SPECIFIC FIELD SCANNERS
// ─────────────────────────────────────────────
function getFormFields(platform) {
  let rawInputs = [];

  if (platform === "workday") {
    // Workday: inputs inside [data-automation-id] wrappers OR inputs that themselves have the attribute
    rawInputs = Array.from(document.querySelectorAll(
      '[data-automation-id] input:not([type="hidden"]):not([type="submit"]):not([type="file"]),' +
      '[data-automation-id] textarea,' +
      '[data-automation-id] select,' +
      'input[data-automation-id]:not([type="hidden"]):not([type="submit"]):not([type="file"]),' +
      'textarea[data-automation-id],' +
      'select[data-automation-id]'
    ));
  } else if (platform === "linkedin") {
    // LinkedIn: everything inside the Easy Apply modal
    const modal = document.querySelector('.jobs-easy-apply-modal, [data-test-modal-id="easy-apply-modal"]');
    if (modal) {
      rawInputs = Array.from(modal.querySelectorAll(
        'input:not([type="hidden"]):not([type="submit"]):not([type="file"]):not([type="checkbox"]),' +
        'textarea, select'
      ));
    }
  } else if (platform === "ashby" || platform === "rippling" || platform === "paycom") {
    // Generic React form fields for newer ATS platforms
    rawInputs = Array.from(document.querySelectorAll(
      'input:not([type="hidden"]):not([type="submit"]):not([type="file"]),' +
      'textarea, select'
    ));
  } else if (platform === "icims") {
    // iCIMS: elements with class iCIMS_Input
    rawInputs = Array.from(document.querySelectorAll(
      '.iCIMS_Input, input[name*="applicant"], select[name*="applicant"], textarea[name*="applicant"]'
    ));
  } else if (platform === "smartrecruiters") {
    // SmartRecruiters: data-test-id attributes or generic
    rawInputs = Array.from(document.querySelectorAll(
      'input[data-test-id]:not([type="hidden"]):not([type="file"]),' +
      'select[data-test-id], textarea[data-test-id],' +
      'input:not([type="hidden"]):not([type="submit"]):not([type="file"]):not([type="checkbox"]),' +
      'textarea, select'
    ));
    // Deduplicate
    rawInputs = [...new Set(rawInputs)];
  } else if (platform === "taleo") {
    // Taleo: ftlField class or ftl-prefixed IDs
    rawInputs = Array.from(document.querySelectorAll(
      'input.ftlField:not([type="hidden"]):not([type="submit"]),' +
      'select.ftlField, textarea.ftlField,' +
      'input[id^="ftl"]:not([type="hidden"]),' +
      'select[id^="ftl"], textarea[id^="ftl"]'
    ));
    rawInputs = [...new Set(rawInputs)];
  } else if (platform === "greenhouse") {
    // Greenhouse: standard inputs, exclude resume/file uploads
    rawInputs = Array.from(document.querySelectorAll(
      'input:not([type="hidden"]):not([type="submit"]):not([type="file"]):not([type="checkbox"]):not([type="radio"]),' +
      'textarea, select'
    ));
  } else {
    // Generic, Lever, BambooHR, and everything else
    rawInputs = Array.from(document.querySelectorAll(
      'input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([type="file"]):not([type="image"]),' +
      'textarea, select'
    ));
  }

  // Build field descriptor array
  const fields = [];
  const seen = new Set();
  for (const el of rawInputs) {
    if (el.type === "radio" || el.type === "checkbox") continue;
    const label = getLabelForInput(el, platform);
    // BUG 2: scoped identity — include nearest section container ID
    // so "Job Title" in Work Exp #1 ≠ "Job Title" in Work Exp #2
    const scopeEl = el.closest('[data-automation-id], [data-field], fieldset, .application-field, .iCIMS_TableFields, .wd-field');
    const scopeId = scopeEl?.getAttribute("data-automation-id") || scopeEl?.id || "";
    const identity = `${label || el.name || el.id}::${scopeId}`;
    if (!identity.replace(/::/g, "") || seen.has(identity)) continue;
    seen.add(identity);
    fields.push({
      element: el,
      label: label || el.name || el.id || "",
      type: el.type || el.tagName.toLowerCase(),
      name: el.name || el.id || "",
      options: el.tagName === "SELECT"
        ? Array.from(el.options).map(o => o.text.trim()).filter(t => t && t !== "--" && !t.startsWith("--"))
        : [],
    });
  }
  return fields;
}

// Also scan radio button groups separately
function getRadioGroups(platform) {
  const groups = {};
  let scope = document;
  if (platform === "linkedin") {
    scope = document.querySelector('.jobs-easy-apply-modal, [data-test-modal-id="easy-apply-modal"]') || document;
  }
  const radios = scope.querySelectorAll('input[type="radio"]');
  for (const r of radios) {
    const name = r.name;
    if (!name) continue;
    if (!groups[name]) {
      const container = r.closest("fieldset, .field, .form-group, .application-field, .fab-field, .sr-field, .wd-field, tr, .li-field, [data-automation-id]");
      // BUG 7: Priority — legend > aria-labelledby on container > label without input child > name
      let label = name;
      if (container) {
        const legend = container.querySelector("legend");
        if (legend) {
          label = (legend.innerText || legend.textContent || "").replace(/[\s*✱]+$/, "").trim();
        } else {
          const labelledBy = container.getAttribute("aria-labelledby");
          if (labelledBy) {
            const el = document.getElementById(labelledBy);
            if (el) label = (el.innerText || el.textContent || "").replace(/[\s*✱]+$/, "").trim();
          }
          if (!label || label === name) {
            // Find a label-like element that has no input child (= group question, not option label)
            const groupLabel = Array.from(container.querySelectorAll("label, .label, .field-label, .question-text, [class*='question']"))
              .find(l => !l.querySelector("input, select, textarea"));
            if (groupLabel) label = (groupLabel.innerText || groupLabel.textContent || "").replace(/[\s*✱]+$/, "").trim();
          }
        }
      }
      groups[name] = { container, label: label || name, inputs: [] };
    }
    groups[name].inputs.push(r);
  }
  return groups;
}

// ─────────────────────────────────────────────
// FILL FIELD
// ─────────────────────────────────────────────
function normalizeValue(label, value) {
  if (!value) return value;
  const lbl = (label || "").toLowerCase();
  // Phone: if this is a "phone number" field on a form that ALSO has a country
  // code field nearby, strip the leading +1 and non-digits.
  if (/^phone$|phone\s*number|mobile\s*number/.test(lbl) && !/extension/.test(lbl)) {
    const hasCountryCode = !!document.querySelector(
      'input[name*="phoneCode" i], [data-automation-id*="countryPhoneCode" i], ' +
      '[aria-label*="Country Phone Code" i], [aria-label*="Phone Code" i]'
    );
    if (hasCountryCode) {
      // digits only, drop leading 1 if 11 digits
      let digits = String(value).replace(/\D/g, "");
      if (digits.length === 11 && digits.startsWith("1")) digits = digits.slice(1);
      return digits;
    }
  }
  return value;
}

function fillField(platform, field, value) {
  if (!value || value === "SKIP" || !field.element) return false;
  const el = field.element;
  const finalVal = normalizeValue(field.label, value);
  if (!finalVal) return false;
  try {
    if (el.tagName === "SELECT") return setSelectValue(el, finalVal);
    el.focus();
    setNativeValue(el, String(finalVal));
    return true;
  } catch (e) {
    return false;
  }
}

// ─────────────────────────────────────────────
// CLEAN PAGE TEXT (for JD extraction)
// ─────────────────────────────────────────────
function getCleanText() {
  // ── Structured data: JSON-LD job posting (Lever, most modern ATS)
  try {
    const ldScripts = document.querySelectorAll('script[type="application/ld+json"]');
    for (const s of ldScripts) {
      const data = JSON.parse(s.textContent || "{}");
      const items = Array.isArray(data) ? data : [data];
      for (const item of items) {
        const type = item["@type"] || "";
        if (/JobPosting|Job/i.test(type)) {
          const desc = item.description || item.responsibilities || "";
          if (desc && desc.length > 200) {
            // Strip HTML tags from description
            const clean = desc.replace(/<[^>]+>/g, " ").replace(/&[a-z]+;/g, " ").replace(/\s+/g, " ").trim();
            if (clean.length > 200) return clean;
          }
        }
      }
    }
  } catch (e) {}

  // React/SPA: check window.__appData (Ashby and similar) before touching DOM
  try {
    const appData = window.__appData;
    if (appData) {
      const jdText = appData?.posting?.descriptionPlainText
                  || appData?.posting?.descriptionParts?.descriptionBody?.plain
                  || appData?.job?.descriptionPlainText
                  || "";
      if (jdText && jdText.length > 200) return jdText.replace(/\s+/g, " ").trim();
    }
  } catch (e) {}

  // iCIMS and some other ATS embed JD in same-origin iframes — try those first
  try {
    const frames = document.querySelectorAll('iframe');
    for (const frame of frames) {
      try {
        const fdoc = frame.contentDocument || frame.contentWindow?.document;
        if (!fdoc) continue;
        const fbody = fdoc.body;
        if (!fbody) continue;
        const ftext = (fbody.innerText || fbody.textContent || "").trim();
        if (ftext.length > 500) return ftext.replace(/\s+/g, " ").trim();
      } catch (e) {} // cross-origin iframe — skip
    }
  } catch (e) {}

  const clone = document.body.cloneNode(true);

  // Strip universal noise — always remove the LH panel itself to avoid "LH" being extracted
  [
    "#localhire-floating-panel",
    "script", "style", "noscript", "iframe", "svg",
    "nav", "header", "footer",
    '[role="navigation"]', '[role="banner"]', '[role="contentinfo"]',
    ".nav", ".footer", ".header", ".navigation",
    // iCIMS-specific noise
    ".iCIMS_SocialShareBar", ".iCIMS_Footer", ".iCIMS_Header",
    ".iCIMS_VideoContent", ".iCIMS_Toolbar",
    // Cookie/legal banners, social share, video embeds
    '[class*="cookie"]', '[class*="social"]', '[class*="share"]',
    '[class*="video"]', '[class*="embed"]',
    '[id*="cookie"]', '[id*="footer"]', '[id*="header"]',
  ].forEach(sel => {
    try { clone.querySelectorAll(sel).forEach(el => el.remove()); } catch (e) {}
  });

  // Platform-specific JD containers (most specific → most generic)
  const JD_SELECTORS = [
    // iCIMS
    ".iCIMS_JobContent", ".iCIMS_Content .iCIMS_BodyContent",
    "#iCIMS_Content", ".iCIMS_Expandable_Container",
    '[class*="iCIMS"][class*="Content"]', '[class*="iCIMS"][class*="Job"]',
    // Workday
    '[data-automation-id="jobPostingDescription"]',
    '[data-automation-id="job-posting-details"]',
    // Greenhouse
    "#content", ".job-post",
    // Lever
    ".posting-page", ".posting",
    // Ashby (React SPA — try after JS renders)
    '[data-testid="job-description"]', '[class*="JobPosting"]', '[class*="job-posting"]',
    // Rippling / Paycom
    '[class*="job-post"]', '[class*="JobPost"]',
    // SmartRecruiters
    ".job-description", '[class*="jobDescription"]',
    // BambooHR
    ".BambooHR-ATS-body", "#applicationBody",
    // Generic
    "main", "article",
    '[role="main"]',
    // Class/ID hints
    '[class*="job-description"]', '[class*="job_description"]',
    '[id*="job-description"]', '[id*="jobDescription"]',
    '[class*="job-details"]', '[id*="job-details"]',
    '[class*="job-posting"]', '[id*="job-posting"]',
    '[class*="posting-content"]', '[class*="jd-content"]',
  ];

  // Helper: innerText in browsers, textContent in JSDOM test env
  function elText(el) { return (el?.innerText || el?.textContent || "").trim(); }

  for (const sel of JD_SELECTORS) {
    try {
      const el = clone.querySelector(sel);
      const t = elText(el);
      if (t.length > 200) return t.replace(/\s+/g, " ").trim();
    } catch (e) {}
  }

  // Fallback: find the single largest text-rich div that isn't clearly noise
  const NOISE_PATTERN = /privacy\s*policy|terms\s*and\s*conditions|copyright|all\s*rights\s*reserved|quick\s*links|cookie/i;
  let best = null, bestLen = 0;
  clone.querySelectorAll("div, section").forEach(div => {
    const t = elText(div);
    if (t.length > bestLen && !NOISE_PATTERN.test(t.slice(0, 200))) {
      bestLen = t.length;
      best = div;
    }
  });

  const raw = elText(best || clone);
  // Final strip: remove lines that are footer/legal/minified code
  const JS_PATTERN = /^[{}\[\]\/\*]|function\s*\(|\s*=>\s*{|const |var |let |import |export |require\(/;
  const lines = raw.split("\n").filter(line => {
    const l = line.trim();
    if (!l || l.length < 2) return false;
    if (NOISE_PATTERN.test(l)) return false;
    // Skip lines that look like minified code (short alphanumeric tokens, lots of punctuation)
    if (JS_PATTERN.test(l) && l.length > 60) return false;
    const punctRatio = (l.match(/[{}()\[\];:=<>\/\\]/g) || []).length / l.length;
    if (punctRatio > 0.15 && l.length > 80) return false; // high punctuation density = code
    return true;
  });
  const result = lines.join("\n").replace(/\s+/g, " ").trim();
  // If result starts with code-like characters, we got the wrong element
  if (result.length > 0 && /^[{\/\*]/.test(result)) return "";
  return result;
}

// ─────────────────────────────────────────────
// SETTINGS HELPERS
// ─────────────────────────────────────────────
// Returns true if the extension's runtime is still attached. Reloading the
// extension invalidates old content-script contexts; calls then throw.
function isExtensionAlive() {
  try { return !!(chrome && chrome.runtime && chrome.runtime.id); }
  catch (e) { return false; }
}

const DEFAULT_API_URL = "http://127.0.0.1:5001";

async function getSettings() {
  // If the runtime is invalidated, return safe defaults so callers still work.
  if (!isExtensionAlive()) {
    return { apiUrl: DEFAULT_API_URL, llm: "ollama", autoRetrigger: false };
  }
  return new Promise(resolve => {
    try {
      chrome.runtime.sendMessage({ action: "get_settings" }, res => {
        if (chrome.runtime.lastError) {
          resolve({ apiUrl: DEFAULT_API_URL, llm: "ollama", autoRetrigger: false });
          return;
        }
        resolve({
          apiUrl: (res && res.url) ? res.url : DEFAULT_API_URL,
          llm: (res && res.llm) ? res.llm : "ollama",
          autoRetrigger: !!(res && res.autoRetrigger),
        });
      });
    } catch (e) {
      resolve({ apiUrl: DEFAULT_API_URL, llm: "ollama", autoRetrigger: false });
    }
  });
}

function delay(ms) { return new Promise(r => setTimeout(r, ms)); }

/**
 * BUG 11 FIX: fetch with a hard timeout.
 * Without this, a hung backend leaves the resume spinner spinning forever with no way out.
 * The Cancel button (already in the UI) only works if the error path is reached.
 * An AbortController lets us force that error path after timeoutMs.
 *
 * @param {string}  url
 * @param {object}  options   — same as fetch() options
 * @param {number}  timeoutMs — abort after this many ms (default 45 s)
 */
async function fetchWithTimeout(url, options, timeoutMs = 45000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, { ...options, signal: controller.signal });
    clearTimeout(timer);
    return res;
  } catch (e) {
    clearTimeout(timer);
    if (e.name === "AbortError") {
      throw new Error(`Request timed out after ${timeoutMs / 1000}s — is the backend running?`);
    }
    throw e;
  }
}

/**
 * BUG 12 FIX: Heuristic to detect whether the current page is a JD vs an application form.
 *
 * Why this matters: when a user navigates directly to an apply URL (skipping the JD page),
 * getCleanText() returns form labels like "First Name, Last Name, Submit Application" as the
 * "job description". The LLM then produces garbage resume analysis because it thinks your JD
 * is a list of form field names.
 *
 * Algorithm: count JD signal words vs form signal words. If form signals dominate → warn user.
 * These signal lists were chosen by looking at which words appear in JD pages but NOT forms,
 * and vice versa.
 */
function isJobDescriptionPage() {
  const text = (getCleanText() || "").toLowerCase();
  if (text.length < 100) return false;
  const JD_SIGNALS = [
    "responsibilities", "requirements", "qualifications", "what you'll do",
    "what you will do", "about the role", "about this role", "minimum qualifications",
    "preferred qualifications", "skills required", "we are looking for",
    "you will", "about us", "who we are", "benefits", "what we offer",
    "experience required", "nice to have", "must have",
  ];
  const FORM_SIGNALS = [
    "first name", "last name", "submit application", "upload resume",
    "cover letter", "equal opportunity", "privacy policy", "click submit",
    "required fields", "attach resume", "attach cv", "i certify",
  ];
  const jdCount   = JD_SIGNALS.filter(s => text.includes(s)).length;
  const formCount = FORM_SIGNALS.filter(s => text.includes(s)).length;
  return jdCount >= formCount;
}

/**
 * Score whether extracted text is likely a real JD (vs form chrome / nav noise).
 * Used before Customize / pending-jd so we do not send garbage to the LLM.
 */
function scoreJDQuality(text, sourceAdapter) {
  const reasons = [];
  const t = (text || "").replace(/\s+/g, " ").trim();
  const lower = t.toLowerCase();
  if (t.length < 200) {
    return { ok: false, score: 0, reasons: ["JD too short (<200 characters)"], jdSignals: 0, formSignals: 0 };
  }

  const JD_SIGNALS = [
    "responsibilities", "requirements", "qualifications", "what you'll do",
    "what you will do", "about the role", "minimum qualifications",
    "preferred qualifications", "we are looking for", "you will",
    "experience required", "must have", "nice to have", "years of experience",
    "bachelor", "master's", "phd", "proficiency in", "knowledge of",
  ];
  const FORM_SIGNALS = [
    "first name", "last name", "submit application", "upload resume",
    "attach resume", "attach cv", "cover letter", "equal opportunity",
    "privacy policy", "required field", "i certify", "work authorization",
    "voluntary self", "eeo", "signature", "submit your application",
  ];

  const jdHits = JD_SIGNALS.filter(s => lower.includes(s));
  const formHits = FORM_SIGNALS.filter(s => lower.includes(s));
  let score = 70;

  if (jdHits.length >= 3) score += 20;
  else if (jdHits.length >= 1) score += 8;
  else { score -= 35; reasons.push("Few job-description keywords found"); }

  if (formHits.length >= 2 && jdHits.length === 0) {
    score -= 50;
    reasons.push("Reads like an application form, not a job posting");
  } else if (formHits.length > jdHits.length + 1) {
    score -= 30;
    reasons.push("Form labels outweigh job-description content");
  }

  if (/\bfirst name\b/.test(lower) && jdHits.length < 2) {
    score -= 25;
    reasons.push("Contains form fields (e.g. First Name)");
  }

  const adapter = sourceAdapter || "unknown";
  const fromStructured = /-(api|html)$/.test(adapter) && adapter !== "fallback";
  if (fromStructured) score += 15;
  if (adapter === "fallback") {
    score -= 20;
    reasons.push("No ATS adapter matched — generic page scrape");
  }

  if (t.length < 450 && jdHits.length < 2) {
    score -= 15;
    reasons.push("Text is short and may be incomplete");
  }

  score = Math.max(0, Math.min(100, score));
  const ok = fromStructured
    ? t.length >= 200
    : (score >= 55 && (jdHits.length >= 2 || t.length >= 900));

  return { ok, score, reasons, jdSignals: jdHits.length, formSignals: formHits.length, adapter };
}

/**
 * Get the best JD text available for this tab.
 * Priority: background cache (populated on JD page load) > live page text.
 * If we're on a form page (not a JD), warns the user so they know resume analysis may be poor.
 */
async function getBestJD() {
  const structured = await getBestJobContext();
  if (structured.jdText && structured.jdText.length > 200) {
    return { text: structured.jdText, source: structured.sourceAdapter || "structured", isJd: true };
  }
  if (isExtensionAlive()) {
    const cached = await new Promise(resolve => {
      try {
        chrome.runtime.sendMessage({ action: "get_page_context" }, res => {
          if (chrome.runtime.lastError) { resolve(null); return; }
          resolve(res && res.text ? res.text : null);
        });
      } catch (e) { resolve(null); }
    });
    if (cached && cached.length > 200) return { text: cached, source: "cache", isJd: true };
  }
  const live  = getCleanText();
  const isJd  = isJobDescriptionPage();
  if (!isJd) {
    panelLog("⚠ Tip: navigate to the job posting page first for better resume matching.");
  }
  return { text: live, source: isJd ? "live" : "form-page", isJd };
}

// ── content.js structured logger — writes to console + backend log file ──────
function lhLog(level, module, msg, data) {
  const fn = level === "ERROR" ? console.error : level === "WARN" ? console.warn : console.log;
  fn(`[LH][${level}][${module}] ${msg}`, data ?? "");
  // Fire-and-forget to backend log sink (best-effort)
  try {
    fetch(`${DEFAULT_API_URL}/lh/ext-logs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ logs: [{ ts: new Date().toISOString(), level, module, msg, data: data ?? null }] }),
      keepalive: true,
    }).catch(() => {});
  } catch (_) {}
}

// Fetch JD from Greenhouse public API given board slug + job token
async function fetchGreenhouseJDContent(company, jobId) {
  const url = `https://boards-api.greenhouse.io/v1/boards/${encodeURIComponent(company)}/jobs/${encodeURIComponent(jobId)}`;
  lhLog("INFO", "greenhouse-api", `Fetching JD`, { url, company, jobId });
  const r = await fetch(url);
  if (!r.ok) {
    lhLog("WARN", "greenhouse-api", `API returned ${r.status}`, { company, jobId });
    return null;
  }
  const data = await r.json();
  const div = document.createElement("div");
  div.innerHTML = data.content || "";
  const bodyText = (div.textContent || "").replace(/\s+/g, " ").trim();
  const title = data.title || "";
  const jdText = (title ? title + "\n\n" : "") + bodyText;
  lhLog("INFO", "greenhouse-api", `JD fetched OK`, { chars: jdText.length, title });
  return {
    title,
    company,
    location: (data.location || {}).name || "",
    jdText,
    sourceAdapter: "greenhouse-api",
    confidence: { jd: 1.0 },
  };
}

// Detect if current page is a Greenhouse application form embed, return { company, jobId } or null
function detectGreenhouseEmbed() {
  try {
    const u = new URL(location.href);
    if (!/greenhouse\.io/i.test(u.hostname)) return null;
    if (!/embed\/job_app/i.test(u.pathname)) return null;
    const company = u.searchParams.get("for") || u.searchParams.get("board_token") || "";
    const jobId   = u.searchParams.get("token") || "";
    if (!company || !jobId) return null;
    return { company, jobId };
  } catch (_) { return null; }
}

// ── Lever ─────────────────────────────────────────────────────────────────────
// Form page: jobs.lever.co/COMPANY/UUID/apply
// Public API: https://api.lever.co/v0/postings/COMPANY/UUID
function detectLeverApplyPage() {
  try {
    const u = new URL(location.href);
    if (!/lever\.co$/i.test(u.hostname)) return null;
    const m = u.pathname.match(/^\/([^/]+)\/([0-9a-f-]{36})\/apply/i);
    if (!m) return null;
    return { company: m[1], postingId: m[2] };
  } catch (_) { return null; }
}

async function fetchLeverJDContent(company, postingId) {
  try {
    const url = `https://api.lever.co/v0/postings/${encodeURIComponent(company)}/${encodeURIComponent(postingId)}`;
    lhLog("INFO", "lever-api", "Fetching JD", { url, company, postingId });
    const r = await fetch(url);
    if (!r.ok) {
      lhLog("WARN", "lever-api", `API returned ${r.status}`, { company, postingId });
      return null;
    }
    const data = await r.json();
    const title = data.text || data.title || "";
    const descHtml = data.descriptionPlain || data.description || "";
    const listsText = (data.lists || [])
      .map(l => {
        const heading = l.text || "";
        const content = (l.content || "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
        return heading + "\n" + content;
      })
      .join("\n\n");
    const jdText = [title, descHtml, listsText].filter(Boolean).join("\n\n").replace(/\s+/g, " ").trim();
    const loc = data.categories?.location || "";
    const comp = data.categories?.team || company;
    lhLog("INFO", "lever-api", "JD fetched OK", { chars: jdText.length, title });
    return { title, company: comp, location: loc, jdText, sourceAdapter: "lever-api", confidence: { jd: 1.0 } };
  } catch (e) {
    lhLog("ERROR", "lever-api", "Fetch failed", { error: e.message });
    return null;
  }
}

// ── Workday ───────────────────────────────────────────────────────────────────
// Form page: company.myworkdayjobs.com/.../TITLE_JR-XXXXX/apply
// No public API — fetch JD listing page HTML
function detectWorkdayApplyPage() {
  try {
    const u = new URL(location.href);
    if (!/myworkdayjobs\.com/i.test(u.hostname)) return null;
    if (!/\/apply\s*$/i.test(u.pathname)) return null;
    const jdUrl = u.origin + u.pathname.replace(/\/apply\s*$/i, "");
    return { jdUrl };
  } catch (_) { return null; }
}

async function fetchWorkdayJDContent(jdUrl) {
  try {
    lhLog("INFO", "workday-html", "Fetching JD page", { jdUrl });
    const r = await fetch(jdUrl);
    if (!r.ok) {
      lhLog("WARN", "workday-html", `Fetch returned ${r.status}`, { jdUrl });
      return null;
    }
    const html = await r.text();
    const parser = new DOMParser();
    const doc = parser.parseFromString(html, "text/html");
    const descEl = doc.querySelector('[data-automation-id="jobPostingDescription"]')
                || doc.querySelector('[data-automation-id="job-posting-details"]')
                || doc.querySelector("main")
                || doc.body;
    const rawText = (descEl?.textContent || "").replace(/\s+/g, " ").trim();
    const titleEl = doc.querySelector('[data-automation-id="jobPostingHeader"] h2, h1, h2');
    const title = (titleEl?.textContent || "").trim();
    const jdText = (title ? title + "\n\n" : "") + rawText;
    if (jdText.length < 100) {
      lhLog("WARN", "workday-html", "Extracted text too short", { chars: jdText.length });
      return null;
    }
    lhLog("INFO", "workday-html", "JD extracted from HTML", { chars: jdText.length, title });
    return { title, company: "", location: "", jdText, sourceAdapter: "workday-html", confidence: { jd: 0.8 } };
  } catch (e) {
    lhLog("ERROR", "workday-html", "Fetch failed", { error: e.message });
    return null;
  }
}

// ── Ashby ─────────────────────────────────────────────────────────────────────
// Form page: jobs.ashbyhq.com/COMPANY/UUID/application
// Public API: GET https://api.ashbyhq.com/posting-api/job-board/COMPANY
function detectAshbyApplyPage() {
  try {
    const u = new URL(location.href);
    if (!/ashbyhq\.com/i.test(u.hostname)) return null;
    const m = u.pathname.match(/^\/([^/]+)\/([0-9a-f-]{36})\/application/i);
    if (!m) return null;
    return { company: m[1], jobId: m[2] };
  } catch (_) { return null; }
}

async function fetchAshbyJDContent(company, jobId) {
  try {
    const url = `https://api.ashbyhq.com/posting-api/job-board/${encodeURIComponent(company)}`;
    lhLog("INFO", "ashby-api", "Fetching job board", { url, company, jobId });
    const r = await fetch(url);
    if (!r.ok) {
      lhLog("WARN", "ashby-api", `API returned ${r.status}`, { company, jobId });
      return null;
    }
    const data = await r.json();
    const jobs = data.jobs || data.jobPostings || [];
    const job = jobs.find(j => j.id === jobId || j.jobId === jobId);
    if (!job) {
      lhLog("WARN", "ashby-api", "Job not found in board listing", { jobId, totalJobs: jobs.length });
      return null;
    }
    const title = job.title || "";
    const descHtml = job.descriptionHtml || job.description || job.descriptionPlain || "";
    const bodyText = descHtml.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
    const jdText = (title ? title + "\n\n" : "") + bodyText;
    const loc = job.locationName || job.location || "";
    lhLog("INFO", "ashby-api", "JD fetched OK", { chars: jdText.length, title });
    return { title, company, location: loc, jdText, sourceAdapter: "ashby-api", confidence: { jd: 0.95 } };
  } catch (e) {
    lhLog("ERROR", "ashby-api", "Fetch failed", { error: e.message });
    return null;
  }
}

// ── BambooHR ──────────────────────────────────────────────────────────────────
// Form page: COMPANY.bamboohr.com/careers/JOB_ID/application or /jobs/apply.php?id=JOB_ID
function detectBambooHRApplyPage() {
  try {
    const u = new URL(location.href);
    if (!/bamboohr\.com/i.test(u.hostname)) return null;
    const m1 = u.pathname.match(/\/careers\/(\d+)\/application/i);
    if (m1) {
      const company = u.hostname.split(".")[0];
      return { company, jobId: m1[1], jdUrl: `${u.origin}/careers/${m1[1]}/detail` };
    }
    if (/\/jobs\/apply\.php/i.test(u.pathname)) {
      const jobId = u.searchParams.get("id");
      if (!jobId) return null;
      const company = u.hostname.split(".")[0];
      return { company, jobId, jdUrl: `${u.origin}/careers/${jobId}/detail` };
    }
    return null;
  } catch (_) { return null; }
}

async function fetchBambooHRJDContent(company, jobId, jdUrl) {
  try {
    lhLog("INFO", "bamboohr-api", "Fetching JD", { jdUrl, company, jobId });
    const r = await fetch(jdUrl);
    if (!r.ok) {
      lhLog("WARN", "bamboohr-api", `Fetch returned ${r.status}`, { jdUrl });
      return null;
    }
    const contentType = r.headers.get("content-type") || "";
    let title = "", jdText = "", loc = "";
    if (contentType.includes("json")) {
      const data = await r.json();
      title = data.jobOpeningName || data.title || "";
      const desc = data.description || data.jobDescription || "";
      jdText = (title ? title + "\n\n" : "") + desc.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
      loc = data.location?.city || data.locationCity || "";
    } else {
      const html = await r.text();
      const parser = new DOMParser();
      const doc = parser.parseFromString(html, "text/html");
      const bodyEl = doc.querySelector(".BambooHR-ATS-body, #applicationBody, main, article") || doc.body;
      title = (doc.querySelector("h1, h2")?.textContent || "").trim();
      const rawText = (bodyEl?.textContent || "").replace(/\s+/g, " ").trim();
      jdText = (title ? title + "\n\n" : "") + rawText;
    }
    if (jdText.length < 100) {
      lhLog("WARN", "bamboohr-api", "Extracted text too short", { chars: jdText.length });
      return null;
    }
    lhLog("INFO", "bamboohr-api", "JD fetched OK", { chars: jdText.length, title });
    return { title, company, location: loc, jdText, sourceAdapter: "bamboohr-api", confidence: { jd: 0.9 } };
  } catch (e) {
    lhLog("ERROR", "bamboohr-api", "Fetch failed", { error: e.message });
    return null;
  }
}

// ── SmartRecruiters ───────────────────────────────────────────────────────────
// Form page: careers.smartrecruiters.com/COMPANY/UUID/application
// Public API: GET https://api.smartrecruiters.com/v1/companies/COMPANY/postings/UUID
function detectSmartRecruitersApplyPage() {
  try {
    const u = new URL(location.href);
    if (!/smartrecruiters\.com/i.test(u.hostname)) return null;
    const m = u.pathname.match(/^\/([^/]+)\/([\w-]+)\/application/i);
    if (!m) return null;
    return { company: m[1], postingId: m[2] };
  } catch (_) { return null; }
}

async function fetchSmartRecruitersJDContent(company, postingId) {
  try {
    const url = `https://api.smartrecruiters.com/v1/companies/${encodeURIComponent(company)}/postings/${encodeURIComponent(postingId)}`;
    lhLog("INFO", "sr-api", "Fetching JD", { url, company, postingId });
    const r = await fetch(url);
    if (!r.ok) {
      lhLog("WARN", "sr-api", `API returned ${r.status}`, { company, postingId });
      return null;
    }
    const data = await r.json();
    const title = data.name || "";
    const descHtml = data.jobAd?.sections?.jobDescription?.text
                  || data.jobAd?.sections?.companyDescription?.text
                  || "";
    const qualHtml = data.jobAd?.sections?.qualifications?.text || "";
    const addHtml = data.jobAd?.sections?.additionalInformation?.text || "";
    const bodyText = [descHtml, qualHtml, addHtml]
      .filter(Boolean)
      .map(h => h.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim())
      .join("\n\n");
    const jdText = (title ? title + "\n\n" : "") + bodyText;
    const loc = data.location?.city || data.location?.name || "";
    lhLog("INFO", "sr-api", "JD fetched OK", { chars: jdText.length, title });
    return { title, company, location: loc, jdText, sourceAdapter: "smartrecruiters-api", confidence: { jd: 0.95 } };
  } catch (e) {
    lhLog("ERROR", "sr-api", "Fetch failed", { error: e.message });
    return null;
  }
}

// ── iCIMS ─────────────────────────────────────────────────────────────────────
// Form page: careers-company.icims.com/jobs/12345/title/job?mode=apply
// No public API — strip ?mode=apply and fetch the JD page HTML
function detectICIMSApplyPage() {
  try {
    const u = new URL(location.href);
    if (!/icims\.com/i.test(u.hostname)) return null;
    if (u.searchParams.get("mode") !== "apply") return null;
    const jdUrl = new URL(u.href);
    jdUrl.searchParams.delete("mode");
    jdUrl.searchParams.delete("apply");
    jdUrl.searchParams.delete("iis");
    jdUrl.searchParams.delete("iisn");
    return { jdUrl: jdUrl.toString() };
  } catch (_) { return null; }
}

async function fetchICIMSJDContent(jdUrl) {
  try {
    lhLog("INFO", "icims-html", "Fetching JD page", { jdUrl });
    const r = await fetch(jdUrl);
    if (!r.ok) {
      lhLog("WARN", "icims-html", `Fetch returned ${r.status}`, { jdUrl });
      return null;
    }
    const html = await r.text();
    const parser = new DOMParser();
    const doc = parser.parseFromString(html, "text/html");
    const descEl = doc.querySelector(".iCIMS_JobContent, .iCIMS_Content .iCIMS_BodyContent, #iCIMS_Content")
                || doc.querySelector("main, article")
                || doc.body;
    const rawText = (descEl?.textContent || "").replace(/\s+/g, " ").trim();
    const title = (doc.querySelector("h1, h2, .iCIMS_Header h1")?.textContent || "").trim();
    const jdText = (title ? title + "\n\n" : "") + rawText;
    if (jdText.length < 100) {
      lhLog("WARN", "icims-html", "Extracted text too short", { chars: jdText.length });
      return null;
    }
    lhLog("INFO", "icims-html", "JD extracted from HTML", { chars: jdText.length, title });
    return { title, company: "", location: "", jdText, sourceAdapter: "icims-html", confidence: { jd: 0.75 } };
  } catch (e) {
    lhLog("ERROR", "icims-html", "Fetch failed", { error: e.message });
    return null;
  }
}

// ── Taleo ─────────────────────────────────────────────────────────────────────
// JD page: career.company.taleo.net/careersection/SECTION/jobdetail.ftl?job=JOB_ID
// Form page: same but jobapplication.ftl
// No public API — replace jobapplication.ftl with jobdetail.ftl
function detectTaleoApplyPage() {
  try {
    const u = new URL(location.href);
    if (!/taleo\.net/i.test(u.hostname)) return null;
    if (!/jobapplication\.ftl/i.test(u.pathname)) return null;
    const jdUrl = u.href.replace(/jobapplication\.ftl/i, "jobdetail.ftl");
    return { jdUrl };
  } catch (_) { return null; }
}

async function fetchTaleoJDContent(jdUrl) {
  try {
    lhLog("INFO", "taleo-html", "Fetching JD page", { jdUrl });
    const r = await fetch(jdUrl);
    if (!r.ok) {
      lhLog("WARN", "taleo-html", `Fetch returned ${r.status}`, { jdUrl });
      return null;
    }
    const html = await r.text();
    const parser = new DOMParser();
    const doc = parser.parseFromString(html, "text/html");
    const descEl = doc.querySelector(".jobdescription, .job-description, #jobDescriptionDiv, .requisitionDescription")
                || doc.querySelector("main, article")
                || doc.body;
    const rawText = (descEl?.textContent || "").replace(/\s+/g, " ").trim();
    const title = (doc.querySelector("h1, h2, .jobtitle")?.textContent || "").trim();
    const jdText = (title ? title + "\n\n" : "") + rawText;
    if (jdText.length < 100) {
      lhLog("WARN", "taleo-html", "Extracted text too short", { chars: jdText.length });
      return null;
    }
    lhLog("INFO", "taleo-html", "JD extracted from HTML", { chars: jdText.length, title });
    return { title, company: "", location: "", jdText, sourceAdapter: "taleo-html", confidence: { jd: 0.7 } };
  } catch (e) {
    lhLog("ERROR", "taleo-html", "Fetch failed", { error: e.message });
    return null;
  }
}

// ── Greenhouse listing page (non-embed) ───────────────────────────────────────
// JD page: boards.greenhouse.io/COMPANY/jobs/JOB_ID
function detectGreenhouseListingApplyPage() {
  try {
    const u = new URL(location.href);
    if (!/greenhouse\.io/i.test(u.hostname)) return null;
    if (/embed\/job_app/i.test(u.pathname)) return null; // handled by detectGreenhouseEmbed
    const m = u.pathname.match(/^\/([^/]+)\/jobs\/(\d+)/i);
    if (!m) return null;
    return { company: m[1], jobId: m[2] };
  } catch (_) { return null; }
}

// ── Run all platform apply-page detectors; return first successful JD ─────────
async function tryPlatformApplyPageFetch() {
  const lv = detectLeverApplyPage();
  if (lv) {
    lhLog("INFO", "jd-extract", "Lever apply page detected", lv);
    try {
      const ctx = await fetchLeverJDContent(lv.company, lv.postingId);
      if (ctx && (ctx.jdText || "").length > 200) return ctx;
    } catch (e) { lhLog("ERROR", "jd-extract", "Lever API failed", { error: e.message }); }
  }

  const wd = detectWorkdayApplyPage();
  if (wd) {
    lhLog("INFO", "jd-extract", "Workday apply page detected", wd);
    try {
      const ctx = await fetchWorkdayJDContent(wd.jdUrl);
      if (ctx && (ctx.jdText || "").length > 200) return ctx;
    } catch (e) { lhLog("ERROR", "jd-extract", "Workday HTML fetch failed", { error: e.message }); }
  }

  const ash = detectAshbyApplyPage();
  if (ash) {
    lhLog("INFO", "jd-extract", "Ashby apply page detected", ash);
    try {
      const ctx = await fetchAshbyJDContent(ash.company, ash.jobId);
      if (ctx && (ctx.jdText || "").length > 200) return ctx;
    } catch (e) { lhLog("ERROR", "jd-extract", "Ashby API failed", { error: e.message }); }
  }

  const bhr = detectBambooHRApplyPage();
  if (bhr) {
    lhLog("INFO", "jd-extract", "BambooHR apply page detected", bhr);
    try {
      const ctx = await fetchBambooHRJDContent(bhr.company, bhr.jobId, bhr.jdUrl);
      if (ctx && (ctx.jdText || "").length > 200) return ctx;
    } catch (e) { lhLog("ERROR", "jd-extract", "BambooHR fetch failed", { error: e.message }); }
  }

  const sr = detectSmartRecruitersApplyPage();
  if (sr) {
    lhLog("INFO", "jd-extract", "SmartRecruiters apply page detected", sr);
    try {
      const ctx = await fetchSmartRecruitersJDContent(sr.company, sr.postingId);
      if (ctx && (ctx.jdText || "").length > 200) return ctx;
    } catch (e) { lhLog("ERROR", "jd-extract", "SmartRecruiters API failed", { error: e.message }); }
  }

  const ic = detectICIMSApplyPage();
  if (ic) {
    lhLog("INFO", "jd-extract", "iCIMS apply page detected", ic);
    try {
      const ctx = await fetchICIMSJDContent(ic.jdUrl);
      if (ctx && (ctx.jdText || "").length > 200) return ctx;
    } catch (e) { lhLog("ERROR", "jd-extract", "iCIMS HTML fetch failed", { error: e.message }); }
  }

  const tal = detectTaleoApplyPage();
  if (tal) {
    lhLog("INFO", "jd-extract", "Taleo apply page detected", tal);
    try {
      const ctx = await fetchTaleoJDContent(tal.jdUrl);
      if (ctx && (ctx.jdText || "").length > 200) return ctx;
    } catch (e) { lhLog("ERROR", "jd-extract", "Taleo HTML fetch failed", { error: e.message }); }
  }

  const ghList = detectGreenhouseListingApplyPage();
  if (ghList) {
    lhLog("INFO", "jd-extract", "Greenhouse listing page detected", ghList);
    try {
      const ctx = await fetchGreenhouseJDContent(ghList.company, ghList.jobId);
      if (ctx && (ctx.jdText || "").length > 200) return ctx;
    } catch (e) { lhLog("ERROR", "jd-extract", "Greenhouse listing API failed", { error: e.message }); }
  }

  return null;
}

function withJDQuality(ctx) {
  if (!ctx) return ctx;
  if (!ctx.jdQuality) {
    ctx.jdQuality = scoreJDQuality(ctx.jdText || "", ctx.sourceAdapter || "unknown");
  }
  return ctx;
}

async function getBestJobContext() {
  // Step 1: Background cache (populated when user visited the JD page earlier)
  if (isExtensionAlive()) {
    const cached = await new Promise(resolve => {
      try {
        chrome.runtime.sendMessage({ action: "get_page_context" }, res => {
          if (chrome.runtime.lastError) { resolve(null); return; }
          resolve(res?.jobContext || null);
        });
      } catch (e) { resolve(null); }
    });
    if (cached && (cached.jdText || "").length > 200) {
      lhLog("INFO", "jd-extract", "Cache hit", { chars: cached.jdText.length, source: cached.sourceAdapter });
      return withJDQuality(cached);
    }
    lhLog("INFO", "jd-extract", "Cache miss", { cachedChars: (cached?.jdText || "").length, url: location.href });
  }

  // Step 2: Greenhouse embed URL → Greenhouse public API (the application form page never has JD text in DOM)
  const gh = detectGreenhouseEmbed();
  if (gh) {
    lhLog("INFO", "jd-extract", "Greenhouse embed detected — trying public API", gh);
    try {
      const ctx = await fetchGreenhouseJDContent(gh.company, gh.jobId);
      if (ctx && ctx.jdText.length > 200) {
        // Cache in background so future calls are instant
        if (isExtensionAlive()) {
          chrome.runtime.sendMessage({
            action: "cache_page_context", text: ctx.jdText, jobContext: ctx, platform: "greenhouse"
          }, () => { void chrome.runtime.lastError; });
        }
        return withJDQuality(ctx);
      }
    } catch (e) {
      lhLog("ERROR", "jd-extract", "Greenhouse API fetch failed", { error: e.message });
    }
  }

  // Step 2b: All other ATS apply-page detectors (Lever, Workday, Ashby, BambooHR, SmartRecruiters, iCIMS, Taleo)
  try {
    const platformCtx = await tryPlatformApplyPageFetch();
    if (platformCtx && (platformCtx.jdText || "").length > 200) {
      lhLog("INFO", "jd-extract", "Platform apply-page adapter succeeded", {
        adapter: platformCtx.sourceAdapter, chars: platformCtx.jdText.length
      });
      if (isExtensionAlive()) {
        chrome.runtime.sendMessage({
          action: "cache_page_context", text: platformCtx.jdText, jobContext: platformCtx, platform: platformCtx.sourceAdapter
        }, () => { void chrome.runtime.lastError; });
      }
      return withJDQuality(platformCtx);
    }
  } catch (e) {
    lhLog("WARN", "jd-extract", "Platform apply-page adapters failed", { error: e.message });
  }

  // Step 3: Structured DOM extraction (works on most job listing pages)
  try {
    const job = await extractJob();
    const ctx = normalizedJobToJobContext(job);
    if ((ctx.jdText || "").length > 200) {
      lhLog("INFO", "jd-extract", "extractJob success", { chars: ctx.jdText.length, adapter: ctx.sourceAdapter });
      return withJDQuality(ctx);
    }
    lhLog("WARN", "jd-extract", "extractJob returned short text", { chars: (ctx.jdText || "").length });
  } catch (e) {
    lhLog("WARN", "jd-extract", "extractJob threw", { error: e.message });
  }

  // Step 4: Raw page text (last resort — will be garbage on form pages)
  const rawText = getCleanText();
  lhLog("WARN", "jd-extract", "Falling back to raw page text", { chars: rawText?.length || 0, url: location.href });
  const quality = scoreJDQuality(rawText, "fallback");
  return {
    title: "",
    company: extractCompanyFromPage(),
    location: "",
    jdText: rawText,
    sourceAdapter: "fallback",
    confidence: {},
    jdQuality: quality,
  };
}

function extractCompanyFromPage() {
  const og = document.querySelector('meta[property="og:site_name"]');
  if (og?.content) return og.content;
  const title = document.title;
  const match = title.match(/(?:at|@)\s+(.+?)(?:\s*[-–|]|$)/i);
  if (match) return match[1].trim();
  return title.split(/[-–|]/)[0].trim();
}

// ─────────────────────────────────────────────
// FILLED-FIELD REGISTRY (for inline editing after fill)
// ─────────────────────────────────────────────
const filledRegistry = []; // { id, label, value, ref (WeakRef to element), platform }
let isCurrentlyFilling = false; // BUG 8: re-entrancy guard

function registerFilled(field, value, platform) {
  if (!field || !field.element) return;
  const id = "lh_" + Math.random().toString(36).slice(2, 9);
  filledRegistry.push({
    id,
    label: field.label,
    value: String(value),
    ref: new WeakRef(field.element),
    platform,
    type: field.type,
  });
}

function clearFilledRegistry() {
  filledRegistry.length = 0;
}

// ─────────────────────────────────────────────
// WORKDAY-SPECIFIC HELPERS
// ─────────────────────────────────────────────
async function clickAddButtonsWorkday() {
  // Workday "Add" buttons inside Work Experience, Education, Languages sections
  // Look for buttons with data-automation-id ending in "-Add" or aria-label "Add"
  const sectionLabels = ["Work Experience", "Education", "Languages", "Job Experience", "Schools"];
  const headers = Array.from(document.querySelectorAll("h2, h3, h4, [data-automation-id], legend, label"));
  let clicks = 0;
  for (const sectionName of sectionLabels) {
    const header = headers.find(h => (h.innerText || "").trim() === sectionName);
    if (!header) continue;
    // Find an Add button within the same section
    const container = header.closest("[data-automation-id], section, fieldset, div") || header.parentElement;
    if (!container) continue;
    const addBtn = Array.from(container.querySelectorAll('button')).find(b => {
      const t = (b.innerText || b.getAttribute("aria-label") || "").trim();
      return t === "Add" || t === "+ Add" || /^add$/i.test(t);
    });
    if (addBtn && !addBtn.disabled) {
      try { addBtn.click(); clicks++; await delay(400); } catch (e) {}
    }
  }
  return clicks;
}

async function fillExperienceFromProfile(userData) {
  // After clicking Add, Workday reveals: Job Title, Company, Location, From, To, Role Description
  const exp = (userData.experience || [])[0];
  if (!exp) return 0;
  const map = {
    "Job Title": exp.title || exp.role || "",
    "Title":     exp.title || exp.role || "",
    "Company":   exp.company || "",
    "Employer":  exp.company || "",
    "Location":  exp.location || "",
    "Role Description": (exp.bullets || exp.description || []).slice(0, 4).join(" "),
    "From":      formatExpDate(exp.start_date || exp.from),
    "To":        formatExpDate(exp.end_date || exp.to),
  };
  return fillByLabelMap(map);
}

async function fillEducationFromProfile(userData) {
  const edu = (userData.education || [])[0];
  if (!edu) return 0;
  const map = {
    "School or University": edu.university || edu.school || "",
    "School":   edu.university || edu.school || "",
    "Degree":   degreeShort(edu.degree),
    "Field of Study": edu.field || edu.major || "Computer Science",
    "Overall Result (GPA)": edu.gpa || "",
    "From":     formatYear(edu.start || edu.start_date),
    "To (Actual or Expected)": formatYear(edu.end || edu.end_date || edu.graduation),
    "Graduation": formatYear(edu.graduation || edu.end_date),
  };
  return fillByLabelMap(map);
}

function degreeShort(d) {
  if (!d) return "MS";
  const s = String(d).toLowerCase();
  if (/master|m\.s|msc|m\.eng/.test(s)) return "MS";
  if (/bachelor|b\.s|b\.eng|bs|bachelors/.test(s)) return "BS";
  if (/phd|doctor/.test(s)) return "PhD";
  if (/mba/.test(s)) return "MBA";
  return d;
}

function formatExpDate(d) {
  if (!d) return "";
  // Try to coerce into MM/YYYY for Workday from/to month-pickers
  const m = String(d).match(/(\d{4})-(\d{2})/) || String(d).match(/(\d{1,2})\/(\d{4})/);
  if (m) {
    if (m[0].includes("/")) return `${m[1].padStart(2, "0")}/${m[2]}`;
    return `${m[2]}/${m[1]}`;
  }
  return d;
}
function formatYear(d) {
  if (!d) return "";
  const y = String(d).match(/(\d{4})/);
  return y ? y[1] : d;
}

function fillByLabelMap(labelToValue) {
  let n = 0;
  for (const [lbl, val] of Object.entries(labelToValue)) {
    if (!val) continue;
    const all = Array.from(document.querySelectorAll(
      'input:not([type=hidden]):not([type=submit]):not([type=file]), textarea, select'
    ));
    for (const el of all) {
      const elLabel = getLabelForInput(el, "workday") || "";
      if (elLabel.trim().toLowerCase() !== lbl.toLowerCase()) continue;
      // BUG 5 FIX: Don't skip pre-populated fields unless they already have the CORRECT value.
      // The old `&& !el.value` guard prevented correcting Workday fields pre-filled with wrong data
      // (e.g. School field auto-completed to wrong institution, Country defaulted to wrong country).
      const currentVal = (el.value || "").trim().toLowerCase();
      const targetVal  = String(val).trim().toLowerCase();
      if (currentVal && currentVal === targetVal) {
        n++; // Already correct — count as filled, skip unnecessary re-fill
        break;
      }
      try {
        // Workday MM/YYYY date inputs: detect by placeholder or maxlength
        const isMonthYear =
          (el.placeholder && /MM[\/\s-]?YYYY/i.test(el.placeholder)) ||
          el.getAttribute("maxlength") === "7" ||
          /^(from|to|start|end)$/i.test(lbl);
        if (el.tagName === "SELECT") setSelectValue(el, val);
        else if (isMonthYear && fillMonthYearInput(el, val)) { /* handled */ }
        else setNativeValue(el, String(val));
        flashElement(el);
        n++;
      } catch (e) {}
      break;
    }
  }
  return n;
}

/**
 * Generalized chip/typeahead fill for any [role="combobox"] widget.
 * Used by skills picker, field-of-study, country phone code, etc.
 */
async function fillChipCombobox(selector, value, waitMs = 700) {
  const combo = document.querySelector(selector);
  if (!combo) return false;
  try {
    combo.focus();
    setNativeValue(combo, value);
    combo.dispatchEvent(new KeyboardEvent("keydown", { key: value.slice(-1), bubbles: true }));
    await delay(waitMs);
    const opts = Array.from(document.querySelectorAll(
      '[role="option"], [data-automation-id*="promptOption"], li[role="option"], [aria-role="option"]'
    ));
    const target =
      opts.find(o => (o.innerText || "").trim().toLowerCase() === value.toLowerCase()) ||
      opts.find(o => (o.innerText || "").trim().toLowerCase().includes(value.toLowerCase()));
    if (target) { target.click(); await delay(250); return true; }
  } catch (e) {}
  return false;
}

async function fillSkillsCombobox(skillsToAdd) {
  const selector =
    'input[aria-label*="Skill" i][role="combobox"], ' +
    '[data-automation-id*="skill" i] input[role="combobox"], ' +
    'input[placeholder*="skill" i][aria-autocomplete]';
  let added = 0;
  for (const skill of skillsToAdd.slice(0, 25)) {
    const ok = await fillChipCombobox(selector, skill);
    if (ok) added++;
  }
  return added;
}

/**
 * Fill a single text input that expects MM/YYYY (Workday start/end dates).
 */
async function fillMonthYearInput(el, isoOrFreeform) {
  const str = String(isoOrFreeform || "").trim();
  // BUG 6: "Present/Current" → find and check the "I currently work here" checkbox
  if (!str || /^(present|current|ongoing|now|-)$/i.test(str)) {
    const container = el.closest('[data-automation-id], section, fieldset') || el.parentElement?.parentElement;
    if (container) {
      const cbs = container.querySelectorAll('input[type="checkbox"]');
      for (const cb of cbs) {
        const cbLabel = (getLabelForInput(cb, "workday") || "").toLowerCase();
        if (/current|still\s*work|present|ongoing/.test(cbLabel) || (!cbLabel && cbs.length === 1)) {
          if (!cb.checked) { cb.click(); await delay(300); }
          return true;
        }
      }
    }
    return false;
  }
  if (!str) return false;
  const isoMatch    = str.match(/^(\d{4})[\/\-](\d{2})$/);    // 2022-06
  const slashMatch  = str.match(/^(\d{2})[\/\-](\d{4})$/);    // 06/2022
  const wordMatch   = str.match(/([A-Za-z]+)\s+(\d{4})/);     // June 2022
  let mm, yyyy;
  if (isoMatch)        { yyyy = isoMatch[1]; mm = isoMatch[2]; }
  else if (slashMatch) { mm = slashMatch[1]; yyyy = slashMatch[2]; }
  else if (wordMatch) {
    const months = ["jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec"];
    const idx = months.indexOf(wordMatch[1].toLowerCase().slice(0, 3));
    if (idx >= 0) { mm = String(idx + 1).padStart(2, "0"); yyyy = wordMatch[2]; }
  }
  if (!mm || !yyyy) return false;
  setNativeValue(el, `${mm}/${yyyy}`);
  el.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true }));
  el.dispatchEvent(new Event("blur", { bubbles: true }));
  return true;
}

/**
 * Fill iCIMS 3-part date pickers (separate Month/Day/Year selects).
 */
function fillThreePartDate(container, dateStr) {
  if (!container || !dateStr) return false;
  const str = String(dateStr).trim();
  let month, day, year;
  const isoMatch = str.match(/(\d{4})-(\d{2})-(\d{2})/);
  if (isoMatch) { year = isoMatch[1]; month = String(parseInt(isoMatch[2])); day = String(parseInt(isoMatch[3])); }
  const wordMatch = str.match(/([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})/);
  if (wordMatch) {
    const months = ["","january","february","march","april","may","june",
                    "july","august","september","october","november","december"];
    month = String(months.indexOf(wordMatch[1].toLowerCase()));
    day = wordMatch[2]; year = wordMatch[3];
  }
  if (!month || !year) return false;
  const selects = Array.from(container.querySelectorAll("select"));
  let filled = 0;
  for (const sel of selects) {
    const lbl = (getLabelForInput(sel, "icims") || sel.name || "").toLowerCase();
    if (/month/.test(lbl)) { if (setSelectValue(sel, month)) filled++; }
    else if (/day/.test(lbl)) { if (setSelectValue(sel, day)) filled++; }
    else if (/year/.test(lbl)) { if (setSelectValue(sel, year)) filled++; }
  }
  return filled > 0;
}

/**
 * Retry filling a field after AJAX repopulation (e.g., State after Country).
 */
async function retryCascadingField(fieldKey, value, platform, waitMs = 1200) {
  await delay(waitMs);
  const fields = getFormFields(platform);
  const target = fields.find(f => matchFieldKey(f.label) === fieldKey);
  if (target && target.element) {
    const ok = fillField(platform, target, value);
    if (ok) flashElement(target.element);
    return ok;
  }
  return false;
}

async function fillResumeUpload(userData) {
  // Find file inputs labeled Resume/CV
  const inputs = Array.from(document.querySelectorAll('input[type="file"]'));
  const resumeInput = inputs.find(i => {
    const lbl = (getLabelForInput(i, "") || "").toLowerCase() +
                " " + (i.name || "").toLowerCase() +
                " " + (i.id || "").toLowerCase();
    return /resume|cv\b/.test(lbl);
  }) || inputs[0];
  if (!resumeInput) return false;

  // Try to fetch the most recent generated resume PDF from backend
  try {
    const { apiUrl } = await getSettings();
    const r = await fetch(`${apiUrl}/last-resume`);
    if (!r.ok) return false;
    const blob = await r.blob();
    const file = new File([blob], "resume.pdf", { type: "application/pdf" });
    const dt = new DataTransfer();
    dt.items.add(file);
    resumeInput.files = dt.files;
    resumeInput.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  } catch (e) { return false; }
}

// ─────────────────────────────────────────────
// MAIN AUTOFILL ORCHESTRATOR
// ─────────────────────────────────────────────
async function runAutoFill(preferredLlm) {
  // BUG 8: re-entrancy guard — prevent double fill during fill-triggered DOM mutations
  if (isCurrentlyFilling) {
    panelLog("⚠ Fill already in progress");
    return;
  }
  isCurrentlyFilling = true;
  try { return await _runAutoFillInner(preferredLlm); }
  finally { isCurrentlyFilling = false; }
}

async function _runAutoFillInner(preferredLlm) {
  const platform = detectPlatform();
  const platformName = getPlatformName(platform);

  sendProgress({ status: "detecting", message: `Detected: ${platformName}` });
  await delay(200);

  // Workday: click any visible Add buttons in Experience/Education/Languages BEFORE scanning
  if (platform === "workday") {
    const clicks = await clickAddButtonsWorkday();
    if (clicks > 0) {
      sendProgress({ status: "expanding", message: `Expanded ${clicks} section(s)` });
      panelLog(`Expanded ${clicks} sections (Add clicked)`);
      await delay(800);
    }
  }

  let fields = getFormFields(platform);
  const radioGroups = getRadioGroups(platform);

  if (fields.length === 0 && Object.keys(radioGroups).length === 0) {
    sendProgress({ status: "error", message: "No fillable form fields found on this page." });
    return;
  }

  const totalFields = fields.length + Object.keys(radioGroups).length;
  sendProgress({ status: "scanning", message: `Found ${totalFields} fields on ${platformName}` });

  const jdText = getCleanText();
  const company = extractCompanyFromPage();
  const { apiUrl, llm } = await getSettings();
  const activeLlm = preferredLlm || llm;

  // Build descriptors for API
  const fieldDescriptors = fields.map((f, i) => ({
    index: i,
    label: f.label,
    type: f.type,
    name: f.name,
    options: f.options.slice(0, 20),
    sensitive: isSensitiveField(f.label),
  }));

  // Add radio groups as descriptors
  Object.entries(radioGroups).forEach(([name, grp], i) => {
    fieldDescriptors.push({
      index: fields.length + i,
      label: grp.label,
      type: "radio",
      name,
      options: grp.inputs.map(r => r.value),
    });
  });

  sendProgress({ status: "thinking", message: `AI (${activeLlm}) generating answers...` });

  let answers = {};
  try {
    const res = await fetch(`${apiUrl}/autofill`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fields: fieldDescriptors, jd_text: jdText, company, host: location.host, llm: activeLlm }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    answers = await res.json();
  } catch (err) {
    sendProgress({ status: "warning", message: `Backend unreachable. Using profile fallback.` });
    answers = await getLocalAnswers(fieldDescriptors);
  }

  let filled = 0, skipped = 0;
  clearFilledRegistry();

  // Fill regular fields — visible: scroll into view + flash highlight
  for (let i = 0; i < fields.length; i++) {
    const field = fields[i];
    const answer = answers[field.label] || answers[field.name] || answers[String(i)];
    if (answer && answer !== "SKIP") {
      try { field.element.scrollIntoView({ block: "center", behavior: "smooth" }); } catch (e) {}
      flashElement(field.element);
      await delay(120);
      const ok = fillField(platform, field, answer);
      if (ok) {
        filled++;
        registerFilled(field, answer, platform);
        sendProgress({ status: "filling", message: `✓ ${field.label}`, filled, total: totalFields });
        panelAddFilled(field.label, answer);
      } else skipped++;
    } else skipped++;
  }

  // Fill radio groups
  for (const [name, grp] of Object.entries(radioGroups)) {
    const answer = answers[grp.label] || answers[name];
    if (answer && answer !== "SKIP" && grp.container) {
      try { grp.container.scrollIntoView({ block: "center", behavior: "smooth" }); } catch (e) {}
      await delay(120);
      const ok = setRadioGroup(grp.container, answer);
      if (ok) {
        filled++;
        sendProgress({ status: "filling", message: `✓ ${grp.label} (radio)`, filled, total: totalFields });
        panelAddFilled(grp.label, answer);
      } else skipped++;
    } else skipped++;
  }

  // Cascading-dropdown retry (State after Country gets repopulated by AJAX)
  const stateAnswer = answers["State"] || answers["State / Province"] || answers["state"];
  if (stateAnswer) {
    try {
      const ok = await retryCascadingField("state", stateAnswer, platform);
      if (ok) { filled++; panelLog("✓ State (cascading retry)"); }
    } catch (e) {}
  }

  // Workday-specific extras: experience, education, skills, chip widgets, resume upload
  if (platform === "workday") {
    try {
      const { apiUrl } = await getSettings();
      const profileRes = await fetch(`${apiUrl}/profile`);
      const userData = profileRes.ok ? await profileRes.json() : null;
      if (userData) {
        // Country phone code chip widget
        const phoneCode = userData.autofill?.phone_country_code || "+1";
        await fillChipCombobox(
          '[data-automation-id*="phoneCode" i] input[role="combobox"], ' +
          'input[aria-label*="phone" i][aria-label*="code" i][role="combobox"], ' +
          'input[aria-label*="country" i][aria-label*="code" i][role="combobox"]',
          phoneCode + " (United States of America)",
          900
        ) || await fillChipCombobox(
          '[data-automation-id*="phoneCode" i] input[role="combobox"]',
          "United States",
          900
        );

        const e = await fillExperienceFromProfile(userData);
        if (e) { filled += e; panelLog(`✓ Filled ${e} experience fields`); }
        const ed = await fillEducationFromProfile(userData);
        if (ed) { filled += ed; panelLog(`✓ Filled ${ed} education fields`); }

        // Field of Study chip widget
        const fos = userData.education?.[0]?.field || userData.education?.[0]?.major || "";
        if (fos) {
          await fillChipCombobox(
            '[data-automation-id*="fieldOfStudy" i] input[role="combobox"], ' +
            'input[aria-label*="field of study" i][role="combobox"]',
            fos
          );
        }

        // Skills: combine resume skills + JD-matched skills
        const allSkills = [];
        for (const arr of Object.values(userData.skills || {})) {
          if (Array.isArray(arr)) allSkills.push(...arr);
        }
        if (allSkills.length) {
          const sk = await fillSkillsCombobox(allSkills);
          if (sk) { filled += sk; panelLog(`✓ Added ${sk} skills`); }
        }
      }
      const upl = await fillResumeUpload(userData || {});
      if (upl) { filled += 1; panelLog(`✓ Resume uploaded`); }
    } catch (e) {
      panelLog(`Workday extras failed: ${e.message}`);
    }
  }

  sendProgress({
    status: "done",
    message: `Done! Filled ${filled} of ${totalFields} fields on ${platformName}`,
    filled, skipped, total: totalFields,
  });
}

async function getLocalAnswers(fieldDescriptors) {
  return new Promise(resolve => {
    chrome.storage.local.get(["autofill_profile"], result => {
      const profile = result.autofill_profile || {};
      const answers = {};
      for (const f of fieldDescriptors) {
        const key = matchFieldKey(f.label);
        if (key && profile[key]) answers[f.label] = profile[key];
      }
      resolve(answers);
    });
  });
}

function sendProgress(data) {
  if (!isExtensionAlive()) return;
  try { chrome.runtime.sendMessage({ action: "autofill_progress", data }); }
  catch (e) { /* popup may be closed or context invalidated */ }
}

// ─────────────────────────────────────────────
// MESSAGE LISTENER
// ─────────────────────────────────────────────
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.action === "get_text") {
    sendResponse({ text: getCleanText() });
    return true;
  }
  if (message.action === "get_platform") {
    const key = detectPlatform();
    sendResponse({ platform: getPlatformName(key), key });
    return true;
  }
  if (message.action === "get_job_context") {
    getBestJobContext().then(ctx => sendResponse(ctx)).catch(() => sendResponse(null));
    return true;
  }
  if (message.action === "start_autofill") {
    const llm = message.llm || null;
    runAutoFill(llm).catch(err => sendProgress({ status: "error", message: err.message }));
    sendResponse({ started: true });
    return true;
  }
  return true;
});

// Auto-cache page text into background on load so the popup/panel can read it
// even after the user navigates away from the JD page.
(async function autoCacheJD() {
  if (!isExtensionAlive()) return;
  try {
    let jdText = "";
    let jobContext = null;

    // Platform apply-page adapters (Greenhouse embed, Lever, Workday, Ashby, BambooHR, SmartRecruiters, iCIMS, Taleo)
    const gh = detectGreenhouseEmbed();
    if (gh) {
      lhLog("INFO", "autoCacheJD", "Greenhouse embed — fetching JD from API", gh);
      try {
        const ctx = await fetchGreenhouseJDContent(gh.company, gh.jobId);
        if (ctx && ctx.jdText.length > 200) {
          jdText = ctx.jdText;
          jobContext = ctx;
        }
      } catch (e) {
        lhLog("WARN", "autoCacheJD", "Greenhouse API failed", { error: e.message });
      }
    }

    if (!jdText) {
      try {
        const platformCtx = await tryPlatformApplyPageFetch();
        if (platformCtx && (platformCtx.jdText || "").length > 200) {
          jdText = platformCtx.jdText;
          jobContext = platformCtx;
          lhLog("INFO", "autoCacheJD", "Platform adapter succeeded", { adapter: platformCtx.sourceAdapter, chars: jdText.length });
        }
      } catch (e) {
        lhLog("WARN", "autoCacheJD", "Platform adapters failed", { error: e.message });
      }
    }

    // Fallback: structured DOM extraction
    if (!jdText) {
      const job = await extractJob().catch(() => null);
      jobContext = job ? normalizedJobToJobContext(job) : null;
      jdText = (jobContext?.jdText || getCleanText());
    }

    if (jdText && jdText.length > 200) {
      lhLog("INFO", "autoCacheJD", "Caching JD", { chars: jdText.length, adapter: jobContext?.sourceAdapter || "dom" });
      chrome.runtime.sendMessage({
        action: "cache_page_context", text: jdText, jobContext, platform: detectPlatform()
      }, () => { void chrome.runtime.lastError; });
    } else {
      lhLog("WARN", "autoCacheJD", "JD too short to cache — skipped", { chars: jdText?.length || 0, url: location.href });
    }
  } catch (e) { /* extension not ready yet or context invalidated */ }
})();

// ═════════════════════════════════════════════════════════════════
// IN-PAGE FLOATING PANEL — visible fill, edit, cover letter, Q&A
// Injected into every page (top frame only). Persists across navigation
// via chrome.storage.session keyed by tab origin.
// ═════════════════════════════════════════════════════════════════
function flashElement(el) {
  if (!el) return;
  try {
    const orig = el.style.boxShadow;
    el.style.transition = "box-shadow 0.3s";
    el.style.boxShadow = "0 0 0 3px rgba(249, 115, 22, 0.6)";
    setTimeout(() => { el.style.boxShadow = orig; }, 700);
  } catch (e) {}
}

const PANEL_ID = "localhire-floating-panel";
let panelEl = null;
let panelState = {
  collapsed: true, filled: [], log: [], cover: "", qa: [],
  // Resume customization state
  resumeStep: 0,            // 0=idle, 1=match, 2=align, 3=use
  resumeAnalysis: null,     // /analyze-deep response
  resumeTailored: null,     // /tailor-resume response
  resumeReady: false,       // /generate-pdf succeeded
};

function panelKey() { return "lh_panel_" + location.host; }

async function loadPanelState() {
  if (!isExtensionAlive()) return;
  return new Promise(resolve => {
    try {
      chrome.storage.session.get([panelKey()], r => {
        if (chrome.runtime.lastError) { resolve(); return; }
        const s = r && r[panelKey()];
        if (s) panelState = { ...panelState, ...s };
        resolve();
      });
    } catch (e) { resolve(); }
  });
}

function savePanelState() {
  if (!isExtensionAlive()) return;
  try { chrome.storage.session.set({ [panelKey()]: panelState }, () => { void chrome.runtime.lastError; }); }
  catch (e) {}
}

function panelAddFilled(label, value) {
  panelState.filled.push({ label, value: String(value), ts: Date.now() });
  savePanelState();
  renderPanel();
}

function panelLog(msg) {
  panelState.log.push({ msg, ts: Date.now() });
  if (panelState.log.length > 30) panelState.log.shift();
  savePanelState();
  renderPanel();
}

function injectPanelStyles() {
  if (document.getElementById("lh-panel-styles")) return;
  const s = document.createElement("style");
  s.id = "lh-panel-styles";
  s.textContent = `
  #${PANEL_ID} { position: fixed; right: 16px; bottom: 16px; z-index: 2147483646;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    color: #1f2937; }
  #${PANEL_ID} .lh-pill { width: 52px; height: 52px; border-radius: 50%;
    background: linear-gradient(135deg, #F97316, #EA580C); color: white;
    display: flex; align-items: center; justify-content: center; cursor: pointer;
    box-shadow: 0 8px 24px rgba(249,115,22,0.4); font-size: 22px; font-weight: bold;
    user-select: none; transition: transform 0.15s; }
  #${PANEL_ID} .lh-pill:hover { transform: scale(1.08); }
  #${PANEL_ID} .lh-card { width: 380px; max-height: 600px; background: white;
    border-radius: 14px; box-shadow: 0 12px 40px rgba(0,0,0,0.18);
    display: flex; flex-direction: column; overflow: hidden; border: 1px solid #e5e7eb; }
  #${PANEL_ID} .lh-head { padding: 12px 14px; background: linear-gradient(135deg, #F97316, #EA580C);
    color: white; display: flex; align-items: center; justify-content: space-between; }
  #${PANEL_ID} .lh-head .lh-title { font-weight: 600; font-size: 14px; }
  #${PANEL_ID} .lh-head .lh-sub { font-size: 11px; opacity: 0.9; }
  #${PANEL_ID} .lh-x { cursor: pointer; font-size: 20px; line-height: 1; padding: 0 4px; }
  #${PANEL_ID} .lh-tabs { display: flex; border-bottom: 1px solid #e5e7eb; background: #f9fafb; }
  #${PANEL_ID} .lh-tab { flex: 1; padding: 8px; font-size: 12px; cursor: pointer;
    text-align: center; color: #6b7280; font-weight: 500; }
  #${PANEL_ID} .lh-tab.active { color: #EA580C; border-bottom: 2px solid #EA580C; background: white; }
  #${PANEL_ID} .lh-body { padding: 12px; overflow-y: auto; flex: 1; font-size: 13px; }
  #${PANEL_ID} .lh-btn { background: #F97316; color: white; border: 0; padding: 8px 12px;
    border-radius: 8px; font-size: 13px; font-weight: 600; cursor: pointer; width: 100%; }
  #${PANEL_ID} .lh-btn:hover { background: #EA580C; }
  #${PANEL_ID} .lh-btn.lh-sec { background: #f3f4f6; color: #1f2937; }
  #${PANEL_ID} .lh-btn.lh-sec:hover { background: #e5e7eb; }
  #${PANEL_ID} .lh-row { display: flex; gap: 6px; margin-bottom: 8px; }
  #${PANEL_ID} .lh-fill-item { padding: 8px; background: #f9fafb; border-radius: 6px;
    margin-bottom: 6px; border: 1px solid #e5e7eb; }
  #${PANEL_ID} .lh-fill-label { font-size: 11px; color: #6b7280; margin-bottom: 4px; font-weight: 600; }
  #${PANEL_ID} .lh-fill-input { width: 100%; padding: 6px; border: 1px solid #d1d5db;
    border-radius: 4px; font-size: 12px; box-sizing: border-box; font-family: inherit; }
  #${PANEL_ID} .lh-fill-actions { display: flex; gap: 4px; margin-top: 4px; }
  #${PANEL_ID} .lh-mini { padding: 4px 8px; font-size: 11px; border: 0; border-radius: 4px;
    cursor: pointer; font-weight: 600; }
  #${PANEL_ID} .lh-mini.upd { background: #10b981; color: white; }
  #${PANEL_ID} .lh-mini.del { background: #ef4444; color: white; }
  #${PANEL_ID} .lh-log { font-size: 11px; color: #6b7280; max-height: 80px; overflow-y: auto;
    padding: 6px; background: #f9fafb; border-radius: 6px; margin-top: 8px; }
  #${PANEL_ID} textarea.lh-area { width: 100%; min-height: 100px; padding: 8px;
    border: 1px solid #d1d5db; border-radius: 6px; font-size: 12px; box-sizing: border-box;
    font-family: inherit; resize: vertical; }
  #${PANEL_ID} .lh-out { background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 6px;
    padding: 10px; font-size: 12px; white-space: pre-wrap; max-height: 280px; overflow-y: auto;
    margin-top: 8px; line-height: 1.5; }
  #${PANEL_ID} .lh-input { width: 100%; padding: 8px; border: 1px solid #d1d5db;
    border-radius: 6px; font-size: 12px; box-sizing: border-box; font-family: inherit; }
  #${PANEL_ID} .lh-status { font-size: 11px; color: #6b7280; margin-bottom: 8px; }
  #${PANEL_ID} .lh-spinner { display: inline-block; width: 16px; height: 16px;
    border: 2px solid #e5e7eb; border-top-color: #F97316; border-radius: 50%;
    animation: lh-spin 0.7s linear infinite; margin-right: 8px; vertical-align: middle; }
  @keyframes lh-spin { to { transform: rotate(360deg); } }
  `;
  (document.head || document.documentElement).appendChild(s);
}

function renderPanel() {
  if (!panelEl) return;
  if (panelState.collapsed) {
    panelEl.innerHTML = `<div class="lh-pill" title="LocalHire Agent">LH</div>`;
    panelEl.querySelector(".lh-pill").onclick = () => {
      panelState.collapsed = false; savePanelState(); renderPanel();
    };
    return;
  }
  const platform = getPlatformName(detectPlatform());
  const tab = panelState.tab || "fill";
  const ctxLost = !isExtensionAlive();
  panelEl.innerHTML = `
    <div class="lh-card">
      <div class="lh-head">
        <div>
          <div class="lh-title">LocalHire Agent</div>
          <div class="lh-sub">${platform}</div>
        </div>
        <div class="lh-x" id="lh-close">×</div>
      </div>
      ${ctxLost ? `<div style="padding:8px 12px;background:#fef3c7;color:#92400e;font-size:11px;border-bottom:1px solid #fde68a">⚠ Extension was reloaded — please refresh this page to restore full functionality.</div>` : ""}
      <div class="lh-tabs">
        <div class="lh-tab ${tab==='fill'?'active':''}" data-tab="fill">Fill</div>
        <div class="lh-tab ${tab==='resume'?'active':''}" data-tab="resume">Resume</div>
        <div class="lh-tab ${tab==='cover'?'active':''}" data-tab="cover">Cover</div>
        <div class="lh-tab ${tab==='ask'?'active':''}" data-tab="ask">Ask AI</div>
      </div>
      <div class="lh-body" id="lh-body"></div>
    </div>`;
  panelEl.querySelector("#lh-close").onclick = () => {
    panelState.collapsed = true; savePanelState(); renderPanel();
  };
  panelEl.querySelectorAll(".lh-tab").forEach(t => {
    t.onclick = () => { panelState.tab = t.dataset.tab; savePanelState(); renderPanel(); };
  });
  const body = panelEl.querySelector("#lh-body");
  if (tab === "fill") body.innerHTML = renderFillTab();
  else if (tab === "resume") body.innerHTML = renderResumeTab();
  else if (tab === "cover") body.innerHTML = renderCoverTab();
  else body.innerHTML = renderAskTab();
  bindBodyHandlers(tab);
}

function renderFillTab() {
  const items = panelState.filled.map((f, i) => `
    <div class="lh-fill-item">
      <div class="lh-fill-label">${escapeHtml(f.label)}</div>
      <textarea class="lh-fill-input" data-idx="${i}" rows="${f.value.length>60?3:1}">${escapeHtml(f.value)}</textarea>
      <div class="lh-fill-actions">
        <button class="lh-mini upd" data-upd="${i}">Update on page</button>
        <button class="lh-mini del" data-del="${i}">Remove</button>
      </div>
    </div>`).join("") || `<div class="lh-status">No fields filled yet. Click "Fill This Form" to start.</div>`;
  const log = panelState.log.slice(-8).map(l => `<div>${escapeHtml(l.msg)}</div>`).join("");
  const tracker = buildCompletionTracker(detectPlatform());
  return `
    <div class="lh-row">
      <button class="lh-btn" id="lh-fill">Fill This Form</button>
      <button class="lh-btn lh-sec" id="lh-clear" style="flex:0 0 90px">Clear</button>
    </div>
    <button class="lh-btn lh-sec" id="lh-next" style="margin-top:6px">Next Page →</button>
    <button class="lh-btn lh-sec" id="lh-customize" style="margin-top:6px;background:#fef3c7;color:#92400e;border:1px solid #fde68a">✨ Customize Resume on Web</button>
    ${tracker}
    <div>${items}</div>
    ${log ? `<div class="lh-log">${log}</div>` : ""}
  `;
}

function renderCoverTab() {
  return `
    <input class="lh-input" id="lh-co-company" placeholder="Company" value="${escapeHtml(panelState.coCompany||extractCompanyFromPage())}" style="margin-bottom:6px"/>
    <input class="lh-input" id="lh-co-role" placeholder="Role / Title" value="${escapeHtml(panelState.coRole||'')}" style="margin-bottom:6px"/>
    <button class="lh-btn" id="lh-co-gen">Generate Cover Letter</button>
    ${panelState.cover ? `
      <div class="lh-out" id="lh-co-out">${escapeHtml(panelState.cover)}</div>
      <div class="lh-row" style="margin-top:6px">
        <button class="lh-btn lh-sec" id="lh-co-copy">Copy</button>
        <button class="lh-btn lh-sec" id="lh-co-edit">Edit</button>
      </div>` : ""}
  `;
}

function renderAskTab() {
  const history = (panelState.qa || []).slice(-5).map(q => `
    <div style="margin-bottom:10px">
      <div style="font-weight:600;font-size:11px;color:#6b7280">Q: ${escapeHtml(q.q)}</div>
      <div class="lh-out" style="margin-top:4px">${escapeHtml(q.a)}</div>
    </div>`).join("");
  return `
    <textarea class="lh-area" id="lh-ask-q" placeholder="Ask anything about this job, or get help with an application question..."></textarea>
    <div class="lh-row" style="margin-top:6px">
      <button class="lh-btn" id="lh-ask-go">Get Answer</button>
      <input class="lh-input" id="lh-ask-words" placeholder="words" value="150" style="flex:0 0 70px"/>
    </div>
    <div style="margin-top:10px">${history}</div>
  `;
}

function buildCompletionTracker(platform) {
  try {
    const allFields = getFormFields(platform);
    const required  = allFields.filter(f =>
      f.element?.required ||
      f.element?.getAttribute?.("aria-required") === "true" ||
      (f.label && /\*/.test(f.label))
    );
    const filledLbls = new Set(panelState.filled.map(f => (f.label || "").toLowerCase()));
    const reqDone   = required.filter(f => filledLbls.has((f.label || "").toLowerCase())).length;
    const totalDone = panelState.filled.length;
    const pct       = required.length > 0 ? Math.round((reqDone / required.length) * 100) : 0;
    const color = pct === 100 ? "#10b981" : pct >= 60 ? "#f59e0b" : "#ef4444";
    return `
      <div style="margin:8px 0">
        <div style="display:flex;justify-content:space-between;font-size:11px;color:#6b7280;margin-bottom:4px">
          <span>Required: ${reqDone}/${required.length}</span>
          <span style="font-weight:700;color:${color}">${pct}%</span>
        </div>
        <div style="height:6px;background:#e5e7eb;border-radius:3px;overflow:hidden">
          <div style="height:100%;width:${pct}%;background:${color};border-radius:3px;transition:width 0.3s"></div>
        </div>
        <div style="font-size:10px;color:#9ca3af;margin-top:3px">Total filled: ${totalDone}</div>
      </div>`;
  } catch (e) {
    return `<div class="lh-status">${panelState.filled.length} field(s) filled</div>`;
  }
}

function clickNextButton() {
  const NEXT_SELECTORS = [
    '[data-automation-id="bottom-navigation-next-button"]',
    '[data-automation-id="nextButton"]',
    'button[data-provides="next"]',
    'input[value="Next >"]',
    'a.iCIMS_Anchor:last-of-type',
    'button[data-qa="btn-submit"]',
  ];
  for (const sel of NEXT_SELECTORS) {
    const btn = document.querySelector(sel);
    if (btn && btn.offsetParent !== null) { btn.click(); return true; }
  }
  const allBtns = Array.from(document.querySelectorAll("button, input[type=submit], a[role=button]"));
  const nextBtn = allBtns.find(b => /\b(save\s*and\s*continue|next|continue|proceed)\b/i.test(b.innerText || b.value || ""));
  if (nextBtn) { nextBtn.click(); return true; }
  return false;
}

function escapeHtml(s) {
  return String(s||"").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

function renderResumeTab() {
  const step = panelState.resumeStep || 0;
  const analysis = panelState.resumeAnalysis;
  const tailored = panelState.resumeTailored;
  const ready    = panelState.resumeReady;

  // Shared loading state with cancel button (BUG 11)
  function loadingHtml(msg, sub) {
    return `<div style="text-align:center;padding:20px 10px">
      <div style="width:32px;height:32px;border:3px solid #e5e7eb;border-top-color:#F97316;border-radius:50%;animation:lh-spin 0.7s linear infinite;margin:0 auto 12px"></div>
      <div style="font-size:13px;font-weight:600;color:#374151;margin-bottom:6px">${msg}</div>
      <div style="font-size:11px;color:#9ca3af;margin-bottom:14px">${sub || ""}</div>
      <button class="lh-btn lh-sec" id="lh-res-back" style="width:auto;padding:6px 20px">Cancel</button>
    </div>`;
  }

  if (step === 0) {
    return `
      <div style="text-align:center;padding:16px 0">
        <div style="font-size:28px">📄</div>
        <div style="font-weight:600;margin:8px 0">Customize Your Resume</div>
        <div style="font-size:12px;color:#6b7280;margin-bottom:14px">
          AI rewrites your bullets to match this specific job.
        </div>
        <button class="lh-btn" id="lh-res-start">See Your Match Score</button>
      </div>`;
  }

  if (step === 1) {
    if (!analysis) return loadingHtml("Analyzing job match…", "Usually 10–30 seconds");
    const score = analysis.match_score || 0;
    const color = score >= 75 ? "#10b981" : score >= 50 ? "#f59e0b" : "#ef4444";
    const ring = `<div style="width:66px;height:66px;position:relative;margin:0 auto 4px;flex-shrink:0">
      <svg width="66" height="66" style="transform:rotate(-90deg)">
        <circle cx="33" cy="33" r="27" fill="none" stroke="#e5e7eb" stroke-width="6"/>
        <circle cx="33" cy="33" r="27" fill="none" stroke="${color}" stroke-width="6"
          stroke-dasharray="${Math.round(score/100*169.6)} 169.6" stroke-linecap="round"/>
      </svg>
      <div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:15px;font-weight:800;color:${color}">${score}%</div>
    </div>`;
    const mustHave = (analysis.must_have_skills || []);
    const missing  = mustHave.filter(s => !s.matched).map(s => s.skill).slice(0, 5);
    const matched  = mustHave.filter(s => s.matched).map(s => s.skill).slice(0, 6);
    const keywords = (analysis.keywords || []).slice(0, 5);
    const chip = (s, bg, fg) => {
      // Shorten long keywords: "Large data sets" → "Large data sets" (ok), "Cloud services (AWS/Azure)" → "AWS/Azure"
      const display = s.length > 18
        ? (s.match(/\(([^)]+)\)/)?.[1] || s.split(/[\s\/]/)[0] + (s.split(/[\s\/]/).length > 1 ? "…" : ""))
        : s;
      return `<span style="display:inline-block;background:${bg};color:${fg};border-radius:3px;padding:2px 6px;font-size:10.5px;margin:1px 2px 1px 0;white-space:nowrap" title="${escapeHtml(s)}">${escapeHtml(display)}</span>`;
    };
    return `
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">
        ${ring}
        <div>
          <div style="font-size:13px;font-weight:700;color:${color}">${score}% Match</div>
          <div style="font-size:10px;color:#6b7280;line-height:1.3">${escapeHtml((analysis.role || "").slice(0, 40))}</div>
        </div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:8px">
        <div style="background:#f0fdf4;border-radius:6px;padding:6px">
          <div style="font-size:9.5px;font-weight:700;color:#10b981;margin-bottom:4px;text-transform:uppercase">✓ You Have</div>
          <div style="line-height:1.6">
            ${matched.length ? matched.map(s=>chip(s,"#dcfce7","#166534")).join("") : '<span style="font-size:10px;color:#9ca3af">None</span>'}
          </div>
        </div>
        <div style="background:#fef2f2;border-radius:6px;padding:6px">
          <div style="font-size:9.5px;font-weight:700;color:#ef4444;margin-bottom:4px;text-transform:uppercase">✗ Missing</div>
          <div style="line-height:1.6">
            ${missing.length ? missing.map(s=>chip(s,"#fee2e2","#991b1b")).join("") : '<span style="font-size:10px;color:#9ca3af">Great fit!</span>'}
          </div>
        </div>
      </div>
      ${keywords.length ? `<div style="margin-bottom:8px">
        <div style="font-size:9.5px;font-weight:700;color:#6b7280;text-transform:uppercase;margin-bottom:4px">ATS Keywords</div>
        <div style="line-height:1.8">${keywords.map(k=>chip(k,"#f3f4f6","#374151")).join("")}</div>
      </div>` : ""}
      <button class="lh-btn" id="lh-res-align" style="margin-bottom:6px;font-size:12px;padding:8px">Align My Resume →</button>
      <button class="lh-btn lh-sec" id="lh-res-back" style="font-size:11px;padding:6px">← Back</button>`;
  }

  if (step === 2) {
    if (!tailored) return loadingHtml("Rewriting bullets with AI…", "30–60s with local model");
    const newScore = tailored.score_estimate || 0;
    const oldScore = analysis?.match_score || 0;
    const delta = newScore - oldScore;
    const expDiffs = (tailored.experience || []).map(exp => {
      const changed = (exp.bullets||[]).filter(b=>b.status!=="unchanged");
      if (!changed.length) return "";
      return `<div style="margin-bottom:10px">
        <div style="font-size:11px;font-weight:700;color:#1f2937;margin-bottom:4px">${escapeHtml(exp.company)}</div>
        ${changed.map(b => b.status === "edited"
          ? `<div style="padding:5px 8px;background:#fff7ed;border-left:2px solid #f97316;border-radius:3px;margin:3px 0">
              <div style="font-size:10px;color:#d97706;text-decoration:line-through">${escapeHtml(b.original||"")}</div>
              <div style="font-size:11px;color:#1f2937">→ ${escapeHtml(b.text)}</div>
            </div>`
          : `<div style="padding:5px 8px;background:#f0fdf4;border-left:2px solid #10b981;border-radius:3px;margin:3px 0;font-size:11px;color:#065f46">+ ${escapeHtml(b.text)}</div>`
        ).join("")}
      </div>`;
    }).filter(Boolean).join("");
    return `
      <div style="display:flex;align-items:center;justify-content:space-between;background:#f0fdf4;border-radius:7px;padding:8px 12px;margin-bottom:10px">
        <div style="font-size:12px;color:#374151">Estimated fit</div>
        <div style="font-size:18px;font-weight:800;color:#10b981">${newScore}% <span style="font-size:12px;color:#6b7280">(${delta>=0?"+":""}${delta})</span></div>
      </div>
      <div style="max-height:260px;overflow-y:auto;margin-bottom:10px;padding-right:2px">
        ${expDiffs || '<div style="font-size:12px;color:#9ca3af;padding:8px">No bullets were changed</div>'}
      </div>
      <button class="lh-btn" id="lh-res-use" style="margin-bottom:6px">Use This Resume ✓</button>
      <button class="lh-btn lh-sec" id="lh-res-back">← Back</button>`;
  }

  return `
    <div style="text-align:center;padding:16px 0">
      <div style="font-size:28px">${ready ? "✅" : "⏳"}</div>
      <div style="font-weight:600;margin:8px 0">${ready ? "Resume Ready!" : "Generating…"}</div>
      <div style="font-size:12px;color:#6b7280;margin-bottom:14px">
        ${ready ? "Auto-uploaded next time you Fill a form." : "This takes 30–60s…"}
      </div>
      ${ready ? `<button class="lh-btn lh-sec" id="lh-res-back">Start Over</button>` : `<button class="lh-btn lh-sec" id="lh-res-back">Cancel</button>`}
    </div>`;
}

function bindResumeHandlers() {
  const backBtn = panelEl.querySelector("#lh-res-back");
  if (backBtn) {
    backBtn.onclick = () => {
      panelState.resumeStep = Math.max(0, (panelState.resumeStep || 1) - 1);
      if (panelState.resumeStep === 0) {
        panelState.resumeAnalysis = null;
        panelState.resumeTailored = null;
        panelState.resumeReady = false;
      }
      savePanelState(); renderPanel();
    };
  }
  const startBtn = panelEl.querySelector("#lh-res-start");
  if (startBtn) {
    startBtn.onclick = async () => {
      panelState.resumeStep = 1; panelState.resumeAnalysis = null;
      savePanelState(); renderPanel();
      try {
        const { apiUrl, llm } = await getSettings();
        const best = await getBestJobContext();
        panelLog(`Analyzing JD (${best.sourceAdapter || "structured"}, ${(best.jdText||"").length} chars)...`);
        const jd = (best.jdText || "").slice(0, 7000);
        const res = await fetchWithTimeout(`${apiUrl}/analyze-deep`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ jd_text: jd, company: best.company || extractCompanyFromPage(),
                                 role: panelState.coRole || best.title || "", llm }),
        }, 60000);  // 60 s — LLM analysis can be slow on first cold run
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        panelState.resumeAnalysis = await res.json();
        savePanelState(); renderPanel();
      } catch (e) {
        panelLog("Resume analysis failed: " + e.message);
        panelState.resumeStep = 0; savePanelState(); renderPanel();
      }
    };
  }
  const alignBtn = panelEl.querySelector("#lh-res-align");
  if (alignBtn) {
    alignBtn.onclick = async () => {
      panelState.resumeStep = 2; panelState.resumeTailored = null;
      savePanelState(); renderPanel();
      try {
        const { apiUrl, llm } = await getSettings();
        const best = await getBestJobContext();
        const jd = (best.jdText || "").slice(0, 5000);
        const skills = (panelState.resumeAnalysis?.must_have_skills || [])
                       .filter(s => s.matched).map(s => s.skill);
        const res = await fetchWithTimeout(`${apiUrl}/tailor-resume`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ jd_text: jd, role: panelState.coRole || best.title || "",
                                 company: best.company || extractCompanyFromPage(), selected_skills: skills, llm }),
        }, 90000);  // 90 s — bullet rewrites with local LLM can take 30-60 s
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        panelState.resumeTailored = await res.json();
        savePanelState(); renderPanel();
      } catch (e) {
        panelLog("Tailoring failed: " + e.message);
        panelState.resumeStep = 1; savePanelState(); renderPanel();
      }
    };
  }
  const useBtn = panelEl.querySelector("#lh-res-use");
  if (useBtn) {
    useBtn.onclick = async () => {
      panelState.resumeStep = 3; panelState.resumeReady = false;
      savePanelState(); renderPanel();
      try {
        const { apiUrl } = await getSettings();
        const res = await fetchWithTimeout(`${apiUrl}/generate-pdf`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(panelState.resumeTailored || {}),
        }, 120000);  // 120 s — pdflatex compile can be slow in Docker
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        panelState.resumeReady = true;
        panelLog("✅ Tailored resume ready for upload");
        savePanelState(); renderPanel();
      } catch (e) {
        panelLog("PDF generation failed: " + e.message);
        panelState.resumeStep = 2; savePanelState(); renderPanel();
      }
    };
  }
}

function bindBodyHandlers(tab) {
  if (tab === "fill") {
    panelEl.querySelector("#lh-fill").onclick = async () => {
      panelLog("Starting autofill...");
      const settings = await getSettings();
      runAutoFill(settings.llm).catch(e => panelLog("Error: " + e.message));
    };
    panelEl.querySelector("#lh-clear").onclick = () => {
      panelState.filled = []; panelState.log = []; savePanelState(); renderPanel();
    };
    const nextBtn = panelEl.querySelector("#lh-next");
    if (nextBtn) {
      nextBtn.onclick = () => {
        const ok = clickNextButton();
        panelLog(ok ? "→ Clicked Next" : "⚠ Next button not found");
      };
    }
    const customizeBtn = panelEl.querySelector("#lh-customize");
    if (customizeBtn) {
      customizeBtn.onclick = async () => {
        // Build the URL & open the tab FIRST so the click event isn't blocked
        // by an async settings fetch that might fail (popup blockers + extension
        // context invalidation both kill the open if it's not synchronous).
        let apiUrl = DEFAULT_API_URL;
        try {
          const settings = await Promise.race([
            getSettings(),
            new Promise(r => setTimeout(() => r({ apiUrl: DEFAULT_API_URL }), 800)),
          ]);
          if (settings && settings.apiUrl) apiUrl = settings.apiUrl;
        } catch (e) { /* fall back to default */ }

        let best = await getBestJobContext();
        let jd = (best.jdText || "").slice(0, 8000);
        let company = best.company || extractCompanyFromPage();
        let role = panelState.coRole || best.title || "";
        let quality = best.jdQuality || scoreJDQuality(jd, best.sourceAdapter || "unknown");

        if ((!jd || jd.length < 200) && best.sourceAdapter === "fallback") {
          panelLog("→ Retrying ATS adapters…");
          try {
            const retry = await tryPlatformApplyPageFetch();
            if (retry && (retry.jdText || "").length > 200) {
              best = withJDQuality(retry);
              jd = (best.jdText || "").slice(0, 8000);
              company = best.company || company;
              role = best.title || role;
              quality = best.jdQuality;
              panelLog(`→ Adapter recovered JD (${best.sourceAdapter}, ${jd.length} chars)`);
            }
          } catch (e) {
            lhLog("WARN", "customize", "Adapter retry failed", { error: e.message });
          }
        }

        if (!jd || jd.length < 50) {
          const go = confirm(
            "Could not extract a job description from this page.\n\n" +
            "Open the dashboard to paste the JD manually?"
          );
          if (!go) {
            panelLog("Customize cancelled — no JD found.");
            return;
          }
          panelLog("⚠ No JD — opening dashboard to paste manually.");
          lhLog("WARN", "customize", "JD empty", { url: location.href });
        } else if (!quality.ok) {
          const reason = quality.reasons[0] || "This may be form text, not the job posting.";
          const go = confirm(
            `Job description quality looks low (${quality.score}/100).\n${reason}\n\n` +
            "Open dashboard anyway? (Best: visit the job posting page first, or paste the full JD there.)"
          );
          if (!go) {
            panelLog("Customize cancelled — improve JD source first.");
            return;
          }
          panelLog(`⚠ Low-quality JD (${quality.score}/100) — verify on dashboard.`);
          lhLog("WARN", "customize", "JD quality low — user continued", { quality, url: location.href });
        } else {
          panelLog(`→ JD OK via ${best.sourceAdapter || "page"} — ${jd.length} chars (quality ${quality.score}/100)`);
          lhLog("INFO", "customize", "JD ready", { adapter: best.sourceAdapter, chars: jd.length, quality });
        }

        // Clear stale resume state (best-effort)
        try {
          panelState.resumeAnalysis = null;
          panelState.resumeTailored = null;
          panelState.resumeReady = false;
          panelState.resumeStep = 0;
          savePanelState();
        } catch (e) {}

        // POST JD to backend (avoids cross-origin localStorage issues)
        try {
          const pendingRes = await fetch(`${apiUrl}/pending-jd`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ jd, company, role, jd_quality: quality }),
          });
          const pending = await pendingRes.json().catch(() => ({}));
          const token = pending?.token ? `&token=${encodeURIComponent(pending.token)}` : "";
          const url = `${apiUrl}/dashboard?from=extension${token}`;
          const w = window.open(url, "_blank");
          if (!w) {
            panelLog("⚠ Popup blocked. Click here: " + url);
            alert("Popup blocked. Open this URL manually:\n\n" + url);
          } else {
            panelLog("→ Opened dashboard");
          }
          return;
        } catch (e) { panelLog("⚠ Could not store JD: " + e.message); }

        const url = `${apiUrl}/dashboard?from=extension`;
        const w = window.open(url, "_blank");
        if (!w) {
          panelLog("⚠ Popup blocked. Click here: " + url);
          alert("Popup blocked. Open this URL manually:\n\n" + url);
        } else {
          panelLog("→ Opened dashboard");
        }
      };
    }
    panelEl.querySelectorAll("[data-upd]").forEach(b => {
      b.onclick = async () => {
        const i = parseInt(b.dataset.upd);
        const newVal = panelEl.querySelector(`textarea[data-idx="${i}"]`).value;
        const label = panelState.filled[i].label;
        panelState.filled[i].value = newVal;
        savePanelState();
        // Re-apply on page
        const reg = filledRegistry.find(r => r.label === label);
        const el = reg?.ref?.deref();
        if (el) {
          if (el.tagName === "SELECT") setSelectValue(el, newVal);
          else setNativeValue(el, newVal);
          flashElement(el);
          panelLog("✓ Updated: " + label);
        } else {
          panelLog("⚠ Field not on this page");
        }
        // Persist correction so we use it next time on this domain
        try {
          const { apiUrl } = await getSettings();
          await fetch(`${apiUrl}/autofill/learn`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ host: location.host, label, value: newVal }),
          });
          panelLog("📌 Learned for next time on " + location.host);
        } catch (e) {}
      };
    });
    panelEl.querySelectorAll("[data-del]").forEach(b => {
      b.onclick = () => {
        const i = parseInt(b.dataset.del);
        panelState.filled.splice(i, 1); savePanelState(); renderPanel();
      };
    });
  } else if (tab === "cover") {
    panelEl.querySelector("#lh-co-gen").onclick = async () => {
      const company = panelEl.querySelector("#lh-co-company").value;
      const role = panelEl.querySelector("#lh-co-role").value;
      panelState.coCompany = company; panelState.coRole = role;
      const btn = panelEl.querySelector("#lh-co-gen");
      btn.textContent = "Generating..."; btn.disabled = true;
      try {
        const { apiUrl, llm } = await getSettings();
        const res = await fetch(`${apiUrl}/cover-letter`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ company, role, jd_text: getCleanText().slice(0,3000), llm }),
        });
        const data = await res.json();
        panelState.cover = data.cover_letter || data.detail || "Failed";
        savePanelState(); renderPanel();
      } catch (e) {
        panelState.cover = "Error: " + e.message; renderPanel();
      }
    };
    if (panelEl.querySelector("#lh-co-copy")) {
      panelEl.querySelector("#lh-co-copy").onclick = () => {
        navigator.clipboard.writeText(panelState.cover);
        panelEl.querySelector("#lh-co-copy").textContent = "Copied!";
      };
      panelEl.querySelector("#lh-co-edit").onclick = () => {
        const out = panelEl.querySelector("#lh-co-out");
        out.contentEditable = "true"; out.focus();
        out.onblur = () => { panelState.cover = out.innerText; savePanelState(); };
      };
    }
  } else if (tab === "resume") {
    bindResumeHandlers();
  } else if (tab === "ask") {
    panelEl.querySelector("#lh-ask-go").onclick = async () => {
      const q = panelEl.querySelector("#lh-ask-q").value.trim();
      const words = parseInt(panelEl.querySelector("#lh-ask-words").value) || 150;
      if (!q) return;
      const btn = panelEl.querySelector("#lh-ask-go");
      btn.textContent = "Thinking..."; btn.disabled = true;
      try {
        const { apiUrl, llm } = await getSettings();
        const res = await fetch(`${apiUrl}/answer-question`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question: q, jd_text: getCleanText().slice(0,2000),
            company: extractCompanyFromPage(), word_limit: words, llm }),
        });
        const data = await res.json();
        panelState.qa = panelState.qa || [];
        panelState.qa.push({ q, a: data.answer || data.detail || "Failed" });
        savePanelState(); renderPanel();
      } catch (e) {
        panelState.qa.push({ q, a: "Error: " + e.message }); renderPanel();
      }
    };
  }
}

async function injectPanel() {
  if (window.top !== window) return; // top frame only
  if (document.getElementById(PANEL_ID)) return;
  if (!document.body) {
    setTimeout(injectPanel, 500); return;
  }
  await loadPanelState();
  injectPanelStyles();
  panelEl = document.createElement("div");
  panelEl.id = PANEL_ID;
  document.body.appendChild(panelEl);
  renderPanel();
}

// Listen for autofill_progress messages → also log into panel
try {
  if (isExtensionAlive()) {
    chrome.runtime.onMessage.addListener((msg) => {
      try {
        if (msg && msg.action === "autofill_progress" && panelEl) {
          panelLog(msg.data?.message || "");
        }
      } catch (e) {}
    });
  }
} catch (e) {}

// Inject after DOM ready
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => setTimeout(injectPanel, 800));
} else {
  setTimeout(injectPanel, 800);
}

// ─────────────────────────────────────────────
// SPA AUTO-RETRIGGER — re-fires autofill on each
// new step/page in Workday, Greenhouse, LinkedIn, etc.
// ─────────────────────────────────────────────
(function installSpaRetrigger() {
  if (window.top !== window) return;        // top frame only
  if (window.__lhSpaInstalled) return;
  window.__lhSpaInstalled = true;

  let lastUrl = location.href;

  function wrapHistoryMethod(method) {
    const original = history[method];
    history[method] = function (...args) {
      const result = original.apply(this, args);
      window.dispatchEvent(new Event("lh-urlchange"));
      return result;
    };
  }
  wrapHistoryMethod("pushState");
  wrapHistoryMethod("replaceState");
  window.addEventListener("popstate", () => window.dispatchEvent(new Event("lh-urlchange")));

  let mutationTimer = null;
  const observer = new MutationObserver(() => {
    clearTimeout(mutationTimer);
    mutationTimer = setTimeout(() => {
      const currentUrl = location.href;
      if (currentUrl !== lastUrl) {
        lastUrl = currentUrl;
        window.dispatchEvent(new Event("lh-urlchange"));
      }
    }, 500);
  });
  if (document.body) observer.observe(document.body, { childList: true, subtree: true });

  window.addEventListener("lh-urlchange", async () => {
    try {
      if (isCurrentlyFilling) return; // BUG 8: don't retrigger mid-fill
      const settings = await getSettings();
      if (!settings.autoRetrigger) return;
      await delay(1500);
      if (isCurrentlyFilling) return; // re-check after delay
      const fields = getFormFields(detectPlatform());
      if (fields.length === 0) return;
      panelLog("↻ New step detected — auto-filling...");
      runAutoFill(settings.llm).catch(e => panelLog("Auto-fill error: " + e.message));
    } catch (e) {}
  });
})();
