import { test, expect } from "@playwright/test";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "../..");
const FIXTURES = path.join(__dirname, "fixtures");
const CONTENT_JS = path.join(ROOT, "extension/content.js");
const BASE = "http://127.0.0.1:8766";
const RESULTS_FILE = path.join(__dirname, "last-run.json");

const CHROME_MOCK = `(() => {
  if (window.chrome?.runtime?.id) return;
  const noop = () => {};
  const store = { get: (k, cb) => cb({}), set: (o, cb) => cb && cb() };
  window.chrome = {
    runtime: { id: "autofill-test", sendMessage: noop, onMessage: { addListener: noop } },
    storage: { local: store, session: store, sync: store },
  };
})();`;

const PLATFORM_URLS = {
  test_greenhouse_real: `${BASE}/test/greenhouse_real.html`,
  test_lever: `${BASE}/test/lever.html`,
  test_icims: `${BASE}/test/icims.html`,
  test_bamboohr: `${BASE}/test/bamboohr.html`,
  test_generic: `${BASE}/test/generic.html`,
  test_smartrecruiters: `${BASE}/test/smartrecruiters.html`,
  test_taleo: `${BASE}/test/taleo.html`,
  test_workday: `${BASE}/test/workday.html`,
};

const VARIANT_URLS = {
  field_shadow_dom: `${BASE}/variants/field_shadow_dom.html`,
  field_dynamic: `${BASE}/variants/field_dynamic.html`,
  field_typeahead: `${BASE}/variants/field_typeahead.html`,
  field_dropdown_native: `${BASE}/variants/field_dropdown_native.html`,
  field_dropdown_custom: `${BASE}/variants/field_dropdown_custom.html`,
  field_file_upload: `${BASE}/variants/field_file_upload.html`,
  field_prefilled: `${BASE}/variants/field_prefilled.html`,
  field_multistep: `${BASE}/variants/field_multistep.html`,
};

const allResults = [];

async function injectContent(page) {
  await page.evaluate(CHROME_MOCK);
  await page.addScriptTag({ path: CONTENT_JS });
}

function listCases() {
  const cases = [];
  for (const [slug, url] of Object.entries(PLATFORM_URLS)) {
    const expectedPath = path.join(FIXTURES, "platforms", `${slug}.expected.json`);
    cases.push({ kind: "platform", name: slug, url, expectedPath });
  }
  for (const [slug, url] of Object.entries(VARIANT_URLS)) {
    const expectedPath = path.join(FIXTURES, "variants", `${slug}.expected.json`);
    cases.push({ kind: "variant", name: slug, url, expectedPath });
  }
  return cases;
}

async function runAutofillHarness(page, platform, config) {
  return page.evaluate(
    async ({ platform, config }) => {
      function readValue(el) {
        if (!el) return "";
        if (el.tagName === "SELECT") {
          const opt = el.options[el.selectedIndex];
          return (opt?.text || el.value || "").trim();
        }
        if (el.type === "file") return el.files?.[0]?.name || "";
        if (el.contentEditable === "true" || el.getAttribute("role") === "textbox") {
          return (el.innerText || el.textContent || "").trim();
        }
        return (el.value || "").trim();
      }

      const report = {
        mapped: [],
        filled: [],
        skipped: [],
        wrong: [],
        unsupported: [],
        events: {},
        errors: [],
        completed: true,
        fileScan: null,
      };

      try {
        const fields = getFormFields(platform);
        const radioGroups = getRadioGroups(platform);
        for (const f of fields) {
          report.mapped.push({ label: f.label, type: f.type, name: f.name });
        }
        for (const [name, grp] of Object.entries(radioGroups)) {
          report.mapped.push({ label: grp.label, type: "radio", name });
        }

        const answers = config.fill || {};
        const expect = config.expect_values || {};
        const mustNot = config.must_not_overwrite || {};

        for (const f of fields) {
          const label = f.label;
          const answer = answers[label];
          if (answer === undefined) {
            report.skipped.push({ label, reason: "no_answer" });
            continue;
          }
          const el = f.element;
          const before = readValue(el);
          const ev = { input: 0, change: 0 };
          el.addEventListener("input", () => ev.input++);
          el.addEventListener("change", () => ev.change++);

          let ok = false;
          try {
            ok = fillField(platform, f, answer);
          } catch (e) {
            report.errors.push(`${label}: ${e.message || e}`);
          }
          const after = readValue(el);
          report.events[label] = ev;

          if (mustNot[label] && before === mustNot[label] && after !== before) {
            report.wrong.push({ label, kind: "clobber", expected: before, got: after });
          }

          if (ok) {
            const exp = expect[label];
            if (exp !== undefined && after !== exp && !after.includes(exp)) {
              report.wrong.push({ label, kind: "value_mismatch", expected: exp, got: after });
            } else {
              report.filled.push({ label, value: after, events: ev });
            }
          } else {
            report.unsupported.push({ label, reason: "fillField_false" });
          }
        }

        for (const [name, grp] of Object.entries(radioGroups)) {
          const answer = answers[grp.label];
          if (!answer) {
            report.skipped.push({ label: grp.label, reason: "no_answer" });
            continue;
          }
          const ok = setRadioGroup(grp.container, answer);
          if (ok) report.filled.push({ label: grp.label, value: answer, type: "radio" });
          else report.unsupported.push({ label: grp.label, reason: "setRadioGroup_false" });
        }

        // File input scan (not in getFormFields)
        const fileInputs = Array.from(document.querySelectorAll('input[type="file"]'));
        report.fileScan = {
          count: fileInputs.length,
          labels: fileInputs.map((i) => getLabelForInput(i, platform)),
          inFormFields: fields.some((f) => f.type === "file"),
        };
      } catch (e) {
        report.completed = false;
        report.errors.push(String(e.message || e));
      }
      return report;
    },
    { platform, config },
  );
}

function assertCase(name, expected, report, errors) {
  const labels = report.mapped.map((m) => m.label);

  if (expected.expect_zero_fields) {
    if (report.mapped.length > 0) {
      errors.push(`${name}: expected 0 mapped fields, got ${report.mapped.length}: ${labels.join(", ")}`);
    }
    return;
  }

  for (const must of expected.must_map_labels || []) {
    const found = labels.some((l) => l.toLowerCase().includes(must.toLowerCase()) || must.toLowerCase().includes(l.toLowerCase()));
    if (!found) errors.push(`${name}: missing mapped label "${must}" (have: ${labels.slice(0, 8).join(", ")})`);
  }

  for (const label of expected.expect_clobber || []) {
    const w = report.wrong.find((x) => x.label === label && x.kind === "clobber");
    if (!w) errors.push(`${name}: expected clobber of prefilled "${label}" but value was preserved`);
  }

  if (expected.custom_dropdown_unsupported && report.mapped.length > 0) {
    errors.push(`${name}: custom dropdown should not map inputs, got ${labels.join(", ")}`);
  }

  if (expected.file_input_present && (!report.fileScan || report.fileScan.count < 1)) {
    errors.push(`${name}: expected file input in DOM`);
  }
  if (expected.file_in_getFormFields === false && report.fileScan?.inFormFields) {
    errors.push(`${name}: file input incorrectly included in getFormFields`);
  }

  for (const f of report.filled) {
    if (f.events && (f.events.input < 1 || f.events.change < 1)) {
      errors.push(`${name}: "${f.label}" fill did not fire input+change events`);
    }
  }

  if (!report.completed) {
    errors.push(`${name}: harness did not complete: ${report.errors.join("; ")}`);
  }
}

for (const fc of listCases()) {
  test(`autofill: ${fc.kind}/${fc.name}`, async ({ page }) => {
    if (!fs.existsSync(fc.expectedPath)) test.skip(true, `missing ${fc.expectedPath}`);
    const expected = JSON.parse(fs.readFileSync(fc.expectedPath, "utf8"));
    const platform = expected.platform || "generic";

    await page.goto(fc.url, { waitUntil: "domcontentloaded" });
    if (expected.wait_ms) await page.waitForTimeout(expected.wait_ms);

    if (expected.multistep) {
      await injectContent(page);
      let r1 = await runAutofillHarness(page, platform, { fill: expected.fill_step1 || {} });
      await page.click("#next-btn");
      await page.waitForTimeout(400);
      await injectContent(page);
      let r2 = await runAutofillHarness(page, platform, { fill: expected.fill_step2 || {}, expect_values: expected.expect_step2_values || {} });
      const errors = [];
      for (const must of expected.step1_labels || []) {
        if (!r1.mapped.some((m) => m.label.includes(must))) errors.push(`step1 missing ${must}`);
      }
      for (const must of expected.step2_labels || []) {
        if (!r2.mapped.some((m) => m.label.includes(must))) errors.push(`step2 missing ${must} after navigation`);
      }
      const entry = { name: `${fc.kind}/${fc.name}`, pass: errors.length === 0, errors, step1: r1, step2: r2 };
      allResults.push(entry);
      if (errors.length) throw new Error(errors.join("\n"));
      return;
    }

    if (fc.name === "field_typeahead") {
      await injectContent(page);
      const report = await runAutofillHarness(page, platform, expected);
      // Typeahead: plain setNativeValue may not select option — document outcome
      const errors = [];
      assertCase(fc.name, expected, report, errors);
      if (expected.typeahead_unsupported_ok && report.wrong.length === 0 && report.filled.length === 0) {
        // acceptable: value set but not from list click
      }
      allResults.push({ name: `${fc.kind}/${fc.name}`, pass: errors.length === 0, errors, report });
      if (errors.length) throw new Error(errors.join("\n"));
      return;
    }

    await injectContent(page);
    const report = await runAutofillHarness(page, platform, expected);
    const errors = [];
    assertCase(fc.name, expected, report, errors);
    const entry = { name: `${fc.kind}/${fc.name}`, pass: errors.length === 0, errors, report };
    allResults.push(entry);
    if (errors.length) throw new Error(errors.join("\n"));
  });
}

test.afterAll(async () => {
  const out = path.join(ROOT, "FINDINGS_autofill.md");
  const pass = allResults.filter((r) => r.pass).length;
  const fail = allResults.filter((r) => !r.pass).length;
  let md = `# Autofill Findings\n\n`;
  md += `**TEST ONLY** — \`extension/content.js\` was not modified.\n\n`;
  md += `Generated: ${new Date().toISOString()}\n`;
  md += `Harness: \`tests/autofill/harness.spec.mjs\` (Playwright + Chromium)\n\n`;
  md += `**Summary:** ${pass} passed, ${fail} failed (${allResults.length} total; smartrecruiters/taleo/workday skipped — no expected.json)\n\n`;
  md += `## Severity legend\n\n| Severity | Meaning |\n|----------|--------|\n| **Critical** | Overwrites user-entered data |\n| **High** | Invisible/unsupported on common ATS widgets |\n| **Medium** | Timing/multistep/typeahead gaps |\n| **Low** | Mapping works; partial test coverage |\n\n`;
  md += `| Case | Pass | Severity | Mapped | Filled | Skipped | Wrong | Unsupported |\n`;
  md += `|------|------|----------|--------|--------|---------|-------|-------------|\n`;
  for (const r of allResults) {
    const rep = r.report || r.step2 || {};
    md += `| ${r.name} | ${r.pass ? "PASS" : "FAIL"} | ${severity(r.name, r)} | ${(rep.mapped || []).length} | ${(rep.filled || []).length} | ${(rep.skipped || []).length} | ${(rep.wrong || []).length} | ${(rep.unsupported || []).length} |\n`;
  }
  md += `\n## Per-case detail\n\n`;
  for (const r of allResults) {
    md += `### ${r.name}\n\n`;
    md += `- **Pass:** ${r.pass}\n`;
    if (r.errors?.length) md += `- **Errors:** ${r.errors.join("; ")}\n`;
    const rep = r.report || r.step2;
    if (rep) {
      md += `- **Mapped:** ${(rep.mapped || []).map((m) => m.label).join(", ") || "(none)"}\n`;
      md += `- **Filled:** ${JSON.stringify(rep.filled || [])}\n`;
      md += `- **Skipped:** ${JSON.stringify(rep.skipped || [])}\n`;
      md += `- **Wrong:** ${JSON.stringify(rep.wrong || [])}\n`;
      md += `- **Unsupported:** ${JSON.stringify(rep.unsupported || [])}\n`;
    }
    md += `- **Root cause:** ${rootCause(r.name, r)}\n`;
    md += `- **Proposed fix:** ${proposedFix(r.name, r)}\n\n`;
  }
  fs.writeFileSync(out, md);
  fs.writeFileSync(RESULTS_FILE, JSON.stringify(allResults, null, 2));
  console.log(`Wrote ${out} — ${pass} pass / ${fail} fail`);
});

function severity(name, r) {
  if (name.includes("prefilled")) return "**Critical**";
  if (name.includes("shadow_dom") || name.includes("dropdown_custom") || name.includes("file_upload")) return "**High**";
  if (name.includes("dynamic") || name.includes("typeahead") || name.includes("multistep")) return "Medium";
  return "Low";
}

function rootCause(name, r) {
  if (name.includes("shadow_dom")) return "getFormFields uses document.querySelector; shadow roots are not traversed.";
  if (name.includes("dynamic")) return r.pass ? "Dynamic field present after wait_ms before scan." : "Fields added post-load missed if scan runs before DOM injection.";
  if (name.includes("typeahead")) return "fillField uses setNativeValue only; combobox needs fillChipCombobox keystroke+option click.";
  if (name.includes("dropdown_custom")) return "Custom div dropdown has no input/select element; scanner cannot see it.";
  if (name.includes("file_upload")) return "getFormFields explicitly excludes type=file; fillResumeUpload only runs on workday path with backend PDF.";
  if (name.includes("prefilled")) return "Main fillField path does not skip non-empty fields (BUG 5 fix only in Workday fillByLabelMap).";
  if (name.includes("multistep")) return r.pass ? "Step 2 fields visible after navigation; no auto re-scan unless SPA retrigger enabled." : "Second step fields not discovered without re-running getFormFields after step change.";
  if (!r.pass) return (r.errors || []).join("; ") || "Label mismatch or fill failure.";
  return "Labels resolved and values applied with input/change events.";
}

function proposedFix(name) {
  if (name.includes("shadow_dom")) return "Add shadowRoot query walker for workday/greenhouse web components.";
  if (name.includes("dynamic")) return "MutationObserver re-scan before fill or retry getFormFields after delay.";
  if (name.includes("typeahead")) return "Detect role=combobox and route to fillChipCombobox for all platforms.";
  if (name.includes("dropdown_custom")) return "Add ARIA listbox/button handlers or platform adapters for div-based selects.";
  if (name.includes("file_upload")) return "Call fillResumeUpload outside workday-only block; surface skip reason in UI.";
  if (name.includes("prefilled")) return "Skip fill when field has user value unless force-overwrite flag set.";
  if (name.includes("multistep")) return "Auto re-scan on lh-urlchange / step button detection for all platforms.";
  return "Tune getLabelForInput platform maps or FIELD_RULES.";
}
