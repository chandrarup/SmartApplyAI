import { test, expect } from "@playwright/test";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "../..");
const FIXTURES = path.join(__dirname, "fixtures");
const CONTENT_JS = path.join(ROOT, "extension/content.js");
const BASE = "http://127.0.0.1:8765";

const CHROME_MOCK = `(() => {
  if (window.chrome && window.chrome.runtime && window.chrome.runtime.id) return;
  const noop = () => {};
  const store = { get: (keys, cb) => cb({}), set: (obj, cb) => cb && cb() };
  window.chrome = {
    runtime: {
      id: "jd-test-extension",
      sendMessage: () => {},
      onMessage: { addListener: noop, removeListener: noop },
    },
    storage: { local: store, session: store, sync: store },
  };
})();`;

function listFixtureCases() {
  const cases = [];
  const happyDir = path.join(FIXTURES, "happy");
  if (fs.existsSync(happyDir)) {
    for (const file of fs.readdirSync(happyDir).filter((f) => f.endsWith(".html"))) {
      const slug = file.replace(/^test_/, "").replace(/\.html$/, "");
      cases.push({
        name: `happy/${file}`,
        url: `${BASE}/test/${slug}.html`,
        expectedPath: path.join(happyDir, file.replace(".html", ".expected.json")),
      });
    }
  }
  const advDir = path.join(FIXTURES, "adversarial");
  if (fs.existsSync(advDir)) {
    for (const file of fs.readdirSync(advDir).filter((f) => f.endsWith(".html"))) {
      cases.push({
        name: `adversarial/${file}`,
        url: `${BASE}/adversarial/${file}`,
        expectedPath: path.join(advDir, file.replace(".html", ".expected.json")),
      });
    }
  }
  return cases;
}

async function injectAndExtract(page, waitMs = 0) {
  if (waitMs > 0) await page.waitForTimeout(waitMs);
  await page.evaluate(CHROME_MOCK);
  await page.addScriptTag({ path: CONTENT_JS });
  return page.evaluate(() => ({
    text: typeof getCleanText === "function" ? getCleanText() : "",
    platform: typeof detectPlatform === "function" ? detectPlatform() : "",
  }));
}

function assertExpectations(name, text, platform, expected, errors) {
  const lower = (text || "").toLowerCase();
  for (const phrase of expected.must_include || []) {
    if (!lower.includes(String(phrase).toLowerCase())) {
      errors.push(`${name}: missing must_include "${phrase}"`);
    }
  }
  for (const phrase of expected.must_exclude || []) {
    if (lower.includes(String(phrase).toLowerCase())) {
      errors.push(`${name}: found must_exclude "${phrase}"`);
    }
  }
  const len = (text || "").length;
  if (expected.min_len != null && len < expected.min_len) {
    errors.push(`${name}: length ${len} < min_len ${expected.min_len}`);
  }
  if (expected.max_len != null && len > expected.max_len) {
    errors.push(`${name}: length ${len} > max_len ${expected.max_len}`);
  }
  if (expected.expected_platform && platform !== expected.expected_platform) {
    errors.push(`${name}: platform "${platform}" !== expected "${expected.expected_platform}"`);
  }
}

const results = [];
const lazyLoadedReport = { immediate: null, delayed: null, changed: null };
const RESULTS_FILE = path.join(__dirname, "last-run-partial.json");

function loadResults() {
  try {
    return JSON.parse(fs.readFileSync(RESULTS_FILE, "utf8"));
  } catch {
    return { results: [], lazyLoadedReport: {} };
  }
}

function savePartial(payload) {
  fs.writeFileSync(RESULTS_FILE, JSON.stringify(payload, null, 2));
}

for (const fc of listFixtureCases()) {
  test(`JD extraction: ${fc.name}`, async ({ page }) => {
    if (!fs.existsSync(fc.expectedPath)) {
      test.skip(true, `missing ${fc.expectedPath}`);
    }
    const expected = JSON.parse(fs.readFileSync(fc.expectedPath, "utf8"));
    await page.goto(fc.url, { waitUntil: "domcontentloaded" });

    let text, platform;
    if (fc.name === "adversarial/lazy_loaded.html") {
      const immediate = await injectAndExtract(page, 0);
      // Reload for delayed run (content.js only injects once per navigation)
      await page.goto(fc.url, { waitUntil: "domcontentloaded" });
      await page.evaluate(CHROME_MOCK);
      await page.addScriptTag({ path: CONTENT_JS });
      const delayed = await page.evaluate(async () => {
        await new Promise((r) => setTimeout(r, 1200));
        return {
          text: getCleanText(),
          platform: detectPlatform(),
        };
      });
      lazyLoadedReport.immediate = immediate;
      lazyLoadedReport.delayed = delayed;
      lazyLoadedReport.changed =
        immediate.text !== delayed.text || immediate.platform !== delayed.platform;
      text = delayed.text;
      platform = delayed.platform;
    } else {
      ({ text, platform } = await injectAndExtract(page, 0));
    }

    const errors = [];
    assertExpectations(fc.name, text, platform, expected, errors);
    const entry = {
      name: fc.name,
      pass: errors.length === 0,
      errors,
      len: (text || "").length,
      platform,
      preview: (text || "").slice(0, 120),
    };
    const partial = loadResults();
    partial.results.push(entry);
    if (fc.name === "adversarial/lazy_loaded.html") {
      partial.lazyLoadedReport = lazyLoadedReport;
    }
    savePartial(partial);

    if (errors.length) {
      throw new Error(errors.join("\n"));
    }
  });
}

test.afterAll(async () => {
  const partial = loadResults();
  const results = partial.results || [];
  const lazyLoadedReport = partial.lazyLoadedReport || {};
  const outPath = path.join(ROOT, "FINDINGS_jd_extraction.md");
  const pass = results.filter((r) => r.pass).length;
  const fail = results.filter((r) => !r.pass).length;
  let md = `# JD Extraction Findings\n\n`;
  md += `Generated: ${new Date().toISOString()}\n\n`;
  md += `**Summary:** ${pass} passed, ${fail} failed (${results.length} total)\n\n`;
  if (lazyLoadedReport.immediate) {
    md += `## lazy_loaded.html timing\n\n`;
    md += `- Immediate length: ${(lazyLoadedReport.immediate.text || "").length}\n`;
    md += `- After 1200ms wait length: ${(lazyLoadedReport.delayed?.text || "").length}\n`;
    md += `- Result changed with wait: **${lazyLoadedReport.changed}**\n\n`;
  }
  md += `| Fixture | Pass | Platform | Len | Notes |\n|---------|------|----------|-----|-------|\n`;
  for (const r of results) {
    md += `| ${r.name} | ${r.pass ? "PASS" : "FAIL"} | ${r.platform} | ${r.len} | ${r.errors[0] || ""} |\n`;
  }
  md += `\n## Failure root causes & proposed fixes\n\n`;
  for (const r of results.filter((x) => !x.pass)) {
    md += `### ${r.name}\n\n`;
    md += `- **Errors:** ${r.errors.join("; ")}\n`;
    md += `- **Preview:** \`${r.preview.replace(/`/g, "'")}\`\n`;
    md += `- **Root cause:** ${rootCause(r.name, r.errors, r.preview)}\n`;
    md += `- **Proposed fix:** ${proposedFix(r.name)}\n\n`;
  }
  fs.writeFileSync(outPath, md);
  fs.writeFileSync(path.join(__dirname, "last-run.json"), JSON.stringify({ results, lazyLoadedReport }, null, 2));
  console.log(`\nWrote ${outPath} — ${pass} pass / ${fail} fail`);
});

function rootCause(name, errors, preview) {
  if (name.includes("shadow_dom")) return "Shadow root content is not traversed by querySelector or cloneNode on host element.";
  if (name.includes("iframe") && errors.some((e) => e.includes("must_include"))) {
    return "Cross-origin or stripped iframe: clone path removes iframe nodes before fallback; same-origin srcdoc may work but path-based test may still fail.";
  }
  if (name.includes("lazy_loaded") && lazyLoadedReport.changed === false) {
    return "getCleanText runs once at injection time; lazy DOM not present until ~800ms later (race).";
  }
  if (name.includes("login_wall")) return "Largest-div heuristic returns login wall text or empty; no signal that JD is gated.";
  if (name.includes("pdf_embedded")) return "PDF embed/object content is not parsed; no text extraction from binary PDF.";
  if (name.includes("junk_heavy") || name.includes("multi_posting")) {
    return "Largest-div heuristic selected sidebar/related-jobs block instead of target posting.";
  }
  if (name.includes("collapsed") || name.includes("readmore")) {
    return "Truncated/collapsed content may still be in DOM but deprioritized vs chrome; or overflow-hidden sibling wins heuristic.";
  }
  if (name.startsWith("happy/")) {
    return "Fixture is autofill-form-heavy with little JD body; getCleanText returns form labels/header, not skill-rich JD.";
  }
  if (name.includes("huge_jd")) return "Length cap or noise filter may truncate very large JD text.";
  return errors.join("; ") || "Unknown — inspect preview.";
}

function proposedFix(name) {
  if (name.includes("shadow_dom")) return "Add shadow-root walker (element.shadowRoot) before selector pass.";
  if (name.includes("lazy_loaded")) return "Retry getCleanText after MutationObserver or debounced re-scan on DOM changes.";
  if (name.includes("iframe")) return "Keep same-origin iframe pass; for cross-origin, use adapter API fetch instead of DOM.";
  if (name.includes("login_wall")) return "Detect login-wall phrases and surface low-quality JD score to user.";
  if (name.includes("pdf_embedded")) return "Skip PDF embeds; link to download or OCR pipeline.";
  if (name.includes("junk_heavy") || name.includes("multi_posting")) return "Score candidate blocks by job-keyword density vs nav noise; prefer JSON-LD JobPosting.";
  if (name.startsWith("happy/")) return "Add platform JD container sections to test fixtures or use real board HTML snapshots.";
  return "Tune JD_SELECTORS or add adapter for this page shape.";
}
