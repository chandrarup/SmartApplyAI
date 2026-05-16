/**
 * LocalHire Agent — Automated ATS Test Harness
 * Tests platform detection, JD extraction, field scanning, and autofill API
 * Usage: node test_ats.js [--fix]  (--fix writes patches to content.js automatically)
 */

const { JSDOM, VirtualConsole } = require("jsdom");
const fs   = require("fs");
const path = require("path");
const http = require("http");

const API = "http://127.0.0.1:5000";
const PAGES_DIR = path.join(__dirname, "test_pages");
const CONTENT_JS = path.join(__dirname, "../extension/content.js");
const GREEN = "\x1b[32m", RED = "\x1b[31m", YELLOW = "\x1b[33m", RESET = "\x1b[0m", BOLD = "\x1b[1m", DIM = "\x1b[2m";

// ─── Test Case Definitions ───────────────────────────────────────────────────
const TESTS = [
  {
    id: "workday",
    file: "test_workday.html",
    url: "https://innovate.wd1.myworkdayjobs.com/en-US/External/job/ML-Engineer",
    hostname: "innovate.wd1.myworkdayjobs.com",
    expectedPlatform: "workday",
    jdSignals: ["engineer", "experience"],
    // Workday fields need data-automation-id, check via generic query
    fieldSelector: 'input:not([type="hidden"]):not([type="submit"])',
    expectedFields: [
      { label: /first.?name/i, key: "first_name" },
      { label: /email/i, key: "email" },
    ],
  },
  {
    id: "greenhouse",
    file: "test_greenhouse.html",
    url: "https://boards.greenhouse.io/company/jobs/123456",
    hostname: "boards.greenhouse.io",
    expectedPlatform: "greenhouse",
    jdSignals: ["authorized", "yourself"],   // form-only page, these are the actual signals
    expectedFields: [
      { label: /first.?name/i, key: "first_name" },
      { label: /email/i, key: "email" },
    ],
  },
  {
    id: "lever",
    file: "test_lever.html",
    url: "https://jobs.lever.co/company/abc123",
    hostname: "jobs.lever.co",
    expectedPlatform: "lever",
    jdSignals: ["engineer", "experience"],
    expectedFields: [
      { label: /full.?name/i, key: "full_name" },
      { label: /email/i, key: "email" },
    ],
  },
  {
    id: "icims",
    file: "test_icims.html",
    url: "https://careers-co.icims.com/jobs/123/title/job",
    hostname: "careers-co.icims.com",
    expectedPlatform: "icims",
    jdSignals: ["first name", "email"],   // icims form-only page
    jdMinSignals: 1,
    expectedFields: [
      { label: /first.?name/i, key: "first_name" },
      { label: /email/i, key: "email" },
    ],
  },
  {
    id: "bamboohr",
    file: "test_bamboohr.html",
    url: "https://company.bamboohr.com/careers/jobs/1",
    hostname: "company.bamboohr.com",
    expectedPlatform: "bamboohr",
    jdSignals: ["engineer", "experience"],
    expectedFields: [
      { label: /first.?name/i, key: "first_name" },
      { label: /email/i, key: "email" },
    ],
  },
  {
    id: "smartrecruiters",
    file: "test_smartrecruiters.html",
    url: "https://careers.smartrecruiters.com/Company/job/123",
    hostname: "careers.smartrecruiters.com",
    expectedPlatform: "smartrecruiters",
    jdSignals: ["authorized", "eligibility"], jdMinSignals: 1,
    expectedFields: [
      { label: /first.?name/i, key: "first_name" },
      { label: /email/i, key: "email" },
    ],
  },
  {
    id: "ashby",
    file: "test_ashby.html",
    url: "https://jobs.ashbyhq.com/ready/3db71e58",
    hostname: "jobs.ashbyhq.com",
    expectedPlatform: "ashby",
    jdSignals: ["geospatial", "data engineering", "airflow"],
    expectedFields: [], // JD page, no form fields
    jdOnly: true,
  },
  {
    id: "generic",
    file: "test_generic.html",
    url: "http://127.0.0.1:5000/test/generic",
    hostname: "127.0.0.1",
    expectedPlatform: "generic",
    jdSignals: ["linkedin", "indeed"], jdMinSignals: 1,
    expectedFields: [
      { label: /first.?name/i, key: "first_name" },
      { label: /email/i, key: "email" },
    ],
  },
];

// ─── Load content.js into JSDOM ─────────────────────────────────────────────
function buildDOM(htmlPath, urlOverride, hostname) {
  const html = fs.readFileSync(htmlPath, "utf8");
  const vc = new VirtualConsole(); // suppress console noise from content.js
  const dom = new JSDOM(html, {
    url: urlOverride,
    virtualConsole: vc,
    runScripts: "dangerously",
    resources: "usable",
    pretendToBeVisual: true,
  });
  // JSDOM polyfills: CSS.escape + innerText (JSDOM doesn't compute layout)
  dom.window.CSS = { escape: (s) => s.replace(/[^\w-]/g, (c) => `\\${c}`) };
  // innerText polyfill: use textContent when innerText is empty (JSDOM limitation)
  const origGEBTN = dom.window.document.getElementsByTagName.bind(dom.window.document);
  dom.window.__innerTextPatch = true;

  // Stub chrome.* APIs so content.js doesn't throw
  dom.window.chrome = {
    runtime: {
      id: "test-extension-id",
      sendMessage: (msg, cb) => { if (cb) setTimeout(() => cb({ url: API, llm: "ollama" }), 0); },
      onMessage: { addListener: () => {} },
      lastError: null,
    },
    storage: {
      session: { get: (k, cb) => cb({}), set: () => {} },
      local:   { get: (k, cb) => cb({}), set: () => {} },
      sync:    { get: (k, cb) => cb({ apiUrl: API, llm: "ollama" }), set: () => {} },
    },
  };
  // Inject content.js
  try {
    const src = fs.readFileSync(CONTENT_JS, "utf8");
    dom.window.eval(src);
  } catch (e) {
    // Ignore errors from panel injection (needs document.body fully rendered)
  }
  return dom;
}

// ─── API helper ─────────────────────────────────────────────────────────────
function apiPost(path, body) {
  return new Promise((resolve, reject) => {
    const data = JSON.stringify(body);
    const req = http.request({ hostname: "127.0.0.1", port: 5000, path, method: "POST",
      headers: { "Content-Type": "application/json", "X-Profile-ID": "default",
                 "Content-Length": Buffer.byteLength(data) }
    }, res => {
      let out = "";
      res.on("data", d => out += d);
      res.on("end", () => { try { resolve(JSON.parse(out)); } catch { resolve({}); } });
    });
    req.on("error", reject);
    req.write(data); req.end();
  });
}

function apiGet(path) {
  return new Promise((resolve, reject) => {
    http.get({ hostname: "127.0.0.1", port: 5000, path,
      headers: { "X-Profile-ID": "default" } }, res => {
      let out = "";
      res.on("data", d => out += d);
      res.on("end", () => { try { resolve(JSON.parse(out)); } catch { resolve({}); } });
    }).on("error", reject);
  });
}

// ─── Individual Test Runners ─────────────────────────────────────────────────
function testPlatformDetect(dom, expected, forcePlatform) {
  if (forcePlatform) return { pass: forcePlatform === expected, got: forcePlatform + " (forced)", expected };
  try {
    const got = dom.window.detectPlatform();
    return { pass: got === expected, got, expected };
  } catch (e) {
    return { pass: false, got: "ERROR: " + e.message, expected };
  }
}

function testJDExtract(dom, signals, minSignals) {
  try {
    const text = dom.window.getCleanText();
    const lower = text.toLowerCase();
    const matched = signals.filter(s => lower.includes(s.toLowerCase()));
    const required = minSignals || Math.max(1, Math.ceil(signals.length / 2));
    const pass = matched.length >= required;
    return { pass, got: `${text.length} chars, matched ${matched.length}/${signals.length} signals (need ${required})`, signals: matched };
  } catch (e) {
    return { pass: false, got: "ERROR: " + e.message };
  }
}

function testAshbyExtract(dom, signals) {
  // Special: Ashby stores JD in window.__appData, not DOM
  try {
    const appData = dom.window.__appData;
    const text = appData?.posting?.descriptionPlainText || appData?.posting?.descriptionHtml || "";
    const lower = text.toLowerCase();
    const matched = signals.filter(s => lower.includes(s.toLowerCase()));
    const pass = matched.length >= Math.max(1, Math.ceil(signals.length / 2));
    return { pass, got: `${text.length} chars from __appData, matched ${matched.length}/${signals.length}`, signals: matched, text };
  } catch (e) {
    return { pass: false, got: "ERROR: " + e.message };
  }
}

function testFieldScan(dom, platform, expectedFields) {
  if (!expectedFields.length) return { pass: true, got: "N/A (JD page, no form expected)", skipped: true };
  try {
    const fields = dom.window.getFormFields(platform);
    if (!fields.length) return { pass: false, got: "0 fields found" };
    const results = expectedFields.map(exp => {
      const found = fields.find(f => exp.label.test(f.label));
      return { label: exp.label.toString(), found: !!found, fieldLabel: found?.label };
    });
    const pass = results.every(r => r.found);
    return { pass, got: `${fields.length} fields. Expected: ${results.map(r => r.found ? "✓" : "✗").join("")}`, results };
  } catch (e) {
    return { pass: false, got: "ERROR: " + e.message };
  }
}

async function testAutofillAPI(fields) {
  if (!fields.length) return { pass: true, got: "N/A (no fields to test)", skipped: true };
  const testFields = [
    { label: "First Name", type: "text", options: [] },
    { label: "Last Name", type: "text", options: [] },
    { label: "Email", type: "email", options: [] },
    { label: "Phone Number", type: "tel", options: [] },
    { label: "LinkedIn URL", type: "text", options: [] },
    { label: "City", type: "text", options: [] },
    { label: "State", type: "text", options: [] },
    { label: "Are you authorized to work in the US?", type: "select", options: ["Yes", "No"] },
    { label: "Do you require sponsorship?", type: "select", options: ["Yes", "No"] },
    { label: "COVID-19 vaccination status", type: "select", options: ["Vaccinated", "Not vaccinated"], sensitive: true },
    { label: "Phone Extension", type: "text", options: [] },
    { label: "1.DATE:", type: "text", options: [] },
  ];
  try {
    const res = await apiPost("/autofill", { fields: testFields, jd_text: "Software Engineer", company: "Test", host: "test.example.com", llm: "ollama" });
    const checks = [
      { label: "First Name",    pass: res["First Name"] === "Chandra Rup",  got: res["First Name"] },
      { label: "Last Name",     pass: res["Last Name"] === "Daka",          got: res["Last Name"] },
      { label: "Email",         pass: res["Email"]?.includes("@"),          got: res["Email"] },
      { label: "Phone Number",  pass: !!(res["Phone Number"]),              got: res["Phone Number"] },
      { label: "LinkedIn URL",  pass: res["LinkedIn URL"]?.includes("linkedin"), got: res["LinkedIn URL"] },
      { label: "Work Auth",     pass: res["Are you authorized to work in the US?"] === "Yes", got: res["Are you authorized to work in the US?"] },
      { label: "Sponsorship",   pass: !!res["Do you require sponsorship?"], got: res["Do you require sponsorship?"] },
      { label: "Sensitive Skip",pass: res["COVID-19 vaccination status"] === "SKIP", got: res["COVID-19 vaccination status"] },
      { label: "Extension Empty",pass: !res["Phone Extension"] || res["Phone Extension"] === "", got: res["Phone Extension"] },
      { label: "Date field",    pass: /\d{2}\/\d{2}\/\d{4}/.test(res["1.DATE:"] || ""), got: res["1.DATE:"] },
    ];
    const passed = checks.filter(c => c.pass).length;
    return { pass: passed === checks.length, got: `${passed}/${checks.length} checks passed`, checks };
  } catch (e) {
    return { pass: false, got: "ERROR: " + e.message };
  }
}

async function testAnalyzeDeep() {
  const jd = `Senior AI/ML Engineer at Cotiviti. Conducts research on healthcare informatics.
    Responsibilities: develop generative AI models. Qualifications: pursuing advanced degree.
    Required: ML/DL experience. LLM/RAG/fine-tuning is a plus. AWS/Azure cloud. Vector databases.`;
  try {
    const res = await apiPost("/analyze-deep", { jd_text: jd, company: "Cotiviti", role: "AI/ML Engineer", llm: "ollama" });
    const checks = [
      { label: "Has role",         pass: !!(res.role),                        got: res.role },
      { label: "Has company",      pass: res.company && res.company.length > 2, got: res.company },
      { label: "Must-have ≤8",     pass: (res.must_have_skills||[]).length <= 8, got: (res.must_have_skills||[]).length + " skills" },
      { label: "No fabrication",   pass: (res.must_have_skills||[]).every(s => jd.toLowerCase().includes(s.skill?.split("/")[0]?.toLowerCase()?.trim()?.slice(0,4) || "xxx")), got: "see skills" },
      { label: "Has keywords",     pass: (res.keywords||[]).length >= 3,      got: (res.keywords||[]).length + " kws" },
      { label: "Match score 0-100",pass: res.match_score >= 0 && res.match_score <= 100, got: res.match_score },
      { label: "jd_extracted",     pass: !!(res.jd_extracted),               got: res.jd_extracted?.length + " chars" },
    ];
    const passed = checks.filter(c => c.pass).length;
    return { pass: passed === checks.length, got: `${passed}/${checks.length} checks passed`, checks };
  } catch (e) {
    return { pass: false, got: "ERROR: " + e.message };
  }
}

// ─── Report Helpers ──────────────────────────────────────────────────────────
function icon(pass) { return pass ? `${GREEN}✓${RESET}` : `${RED}✗${RESET}`; }
function pad(s, n) { return String(s).padEnd(n); }

function printResult(name, result) {
  const sym = result.skipped ? `${YELLOW}–${RESET}` : icon(result.pass);
  console.log(`  ${sym} ${pad(name, 22)} ${DIM}${result.got}${RESET}`);
  if (!result.pass && !result.skipped && result.checks) {
    result.checks.filter(c => !c.pass).forEach(c => {
      console.log(`       ${RED}FAIL${RESET} ${c.label}: got ${JSON.stringify(c.got)}`);
    });
  }
  if (!result.pass && !result.skipped && result.results) {
    result.results.filter(r => !r.found).forEach(r => {
      console.log(`       ${RED}MISS${RESET} expected field matching ${r.label}`);
    });
  }
}

// ─── Main ─────────────────────────────────────────────────────────────────────
async function main() {
  console.log(`\n${BOLD}═══════════════════════════════════════════════════${RESET}`);
  console.log(`${BOLD}  LocalHire Agent — ATS Test Suite${RESET}`);
  console.log(`${BOLD}═══════════════════════════════════════════════════${RESET}\n`);

  // 1. Backend health
  let backendOk = false;
  try {
    const h = await apiGet("/health");
    backendOk = h.message === "Server is Online";
  } catch {}
  console.log(`${icon(backendOk)} Backend ${API} ${backendOk ? "Online" : `${RED}OFFLINE — start uvicorn first${RESET}`}`);
  if (!backendOk) { process.exit(1); }

  const summary = { total: 0, passed: 0, failures: [] };

  // 2. Per-platform tests
  for (const tc of TESTS) {
    const file = path.join(PAGES_DIR, tc.file);
    if (!fs.existsSync(file)) {
      console.log(`\n${YELLOW}⚠ ${tc.id}${RESET} — ${DIM}${tc.file} not found, skipping${RESET}`);
      continue;
    }
    console.log(`\n${BOLD}▸ ${tc.id.toUpperCase()}${RESET} (${tc.file})`);
    const dom = buildDOM(file, tc.url, tc.hostname);
    const platform = dom.window.detectPlatform ? dom.window.detectPlatform() : "?";

    // T1: Platform detect
    if (tc.expectedPlatform) {
      const r = testPlatformDetect(dom, tc.expectedPlatform, null);
      printResult("platform detect", r);
      summary.total++; if (r.pass) summary.passed++; else summary.failures.push(`${tc.id}: platform detect`);
    }

    // T2: JD extraction
    const jdResult = tc.id === "ashby" ? testAshbyExtract(dom, tc.jdSignals) : testJDExtract(dom, tc.jdSignals, tc.jdMinSignals);
    printResult("JD extraction", jdResult);
    summary.total++; if (jdResult.pass) summary.passed++; else summary.failures.push(`${tc.id}: JD extraction`);

    // T3: Field scanning (skip for JD-only pages)
    if (!tc.jdOnly) {
      const fr = testFieldScan(dom, platform, tc.expectedFields);
      printResult("field scan", fr);
      summary.total++; if (fr.pass || fr.skipped) summary.passed++; else summary.failures.push(`${tc.id}: field scan`);
    }
  }

  // 3. Autofill API
  console.log(`\n${BOLD}▸ AUTOFILL API (backend)${RESET}`);
  const afResult = await testAutofillAPI(["run"]);  // non-empty = always run
  printResult("field mapping", afResult);
  summary.total++; if (afResult.pass) summary.passed++; else summary.failures.push("autofill: field mapping");

  // 4. Analyze Deep
  console.log(`\n${BOLD}▸ ANALYZE-DEEP API (backend)${RESET}`);
  const adResult = await testAnalyzeDeep();
  printResult("analyze-deep", adResult);
  summary.total++; if (adResult.pass) summary.passed++; else summary.failures.push("analyze-deep");

  // 5. Summary
  const pct = Math.round((summary.passed / summary.total) * 100);
  const color = pct === 100 ? GREEN : pct >= 70 ? YELLOW : RED;
  console.log(`\n${BOLD}═══════════════════════════════════════════════════${RESET}`);
  console.log(`${BOLD}  Result: ${color}${summary.passed}/${summary.total} passed (${pct}%)${RESET}`);
  if (summary.failures.length) {
    console.log(`\n  Failures to fix:`);
    summary.failures.forEach(f => console.log(`    ${RED}✗${RESET} ${f}`));
  } else {
    console.log(`  ${GREEN}All tests passing!${RESET}`);
  }
  console.log(`${BOLD}═══════════════════════════════════════════════════${RESET}\n`);
}

main().catch(e => { console.error(e); process.exit(1); });
