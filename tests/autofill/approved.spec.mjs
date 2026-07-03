// Approved-package autofill (feature: autofill-approved).
//
// Proves, in real Chromium against a multi-step multi-ATS-styled fixture, that the
// extension fills from a tracker ready_to_apply package:
//   - the EXACT versioned resume artifact is attached (not /last-resume)
//   - approved answers + contact fields are filled with the right source
//   - a novel field is flagged "needs your call" (never guessed)
//   - answering it persists (approved package + per-host learned) and the next step
//     auto-resolves it from the remembered value
//   - the form is NEVER submitted (rule 1 human gate)
import { test, expect } from "@playwright/test";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "../..");
const CONTENT_JS = path.join(ROOT, "extension/content.js");
const URL = "http://127.0.0.1:8766/approved/multi_ats_demo.html";

const MATCH = {
  id: "app-123",
  company: "Acme",
  role: "Machine Learning Engineer",
  resume_variant_id: "acme_abc12345_1700000000",
  answers: {
    "Why do you want to work at Acme?": "Acme's ML platform is exactly where I want to build.",
    "Are you authorized to work in the US?": "Yes",
    "Years of experience": "4",
  },
};

const PROFILE = {
  contact_info: { name: "Jane Doe", email: "jane@example.com", phone: "512-555-0100" },
  autofill: { first_name: "Jane", last_name: "Doe", city: "Austin", state: "TX" },
};

const CHROME_MOCK = `(() => {
  const store = { get: (k, cb) => cb({}), set: (o, cb) => cb && cb() };
  window.chrome = {
    runtime: {
      id: "approved-test",
      // getSettings() awaits this callback — must invoke it or the promise hangs.
      sendMessage: (msg, cb) => { if (typeof cb === "function") cb({}); },
      onMessage: { addListener: () => {} },
    },
    storage: { local: store, session: store, sync: store },
  };
})();`;

function netStub(match, profile) {
  return `(() => {
    window.__match = ${JSON.stringify(match)};
    window.__net = { learned: {}, patched: null, learnPosts: [], pdfServed: null };
    const jsonRes = (o) => ({ ok: true, status: 200, json: async () => o, text: async () => JSON.stringify(o) });
    window.fetch = async (url, opts = {}) => {
      const u = String(url);
      if (u.includes("/tracker/match")) return jsonRes({ match: window.__match });
      if (u.includes("/resume/versions/") && u.endsWith("/pdf")) {
        window.__net.pdfServed = u;
        return { ok: true, status: 200, blob: async () => new Blob(["%PDF-1.4 fake"], { type: "application/pdf" }) };
      }
      if (u.includes("/autofill/learned")) return jsonRes(window.__net.learned);
      if (u.includes("/autofill/learn")) {
        const b = JSON.parse(opts.body || "{}");
        window.__net.learned[b.label] = b.value;
        window.__net.learnPosts.push(b);
        return jsonRes({ ok: true });
      }
      if (u.includes("/applications/")) {
        window.__net.patched = JSON.parse(opts.body || "{}");
        return jsonRes({ ok: true });
      }
      if (u.endsWith("/profile")) return jsonRes(${JSON.stringify(profile)});
      return jsonRes({});
    };
  })();`;
}

async function setup(page) {
  // Install mocks BEFORE navigation so content.js never hits a real backend on load.
  await page.addInitScript(CHROME_MOCK);
  await page.addInitScript(netStub(MATCH, PROFILE));
  await page.goto(URL);
  await page.addScriptTag({ path: CONTENT_JS });
  // Suppress the content script's own load/SPA auto-fill timers so the test drives it.
  await page.evaluate(() => { try { autoFilledUrls.add(location.href); } catch (e) {} });
  await page.evaluate(async () => { await injectPanel(); });
}

test("attaches the versioned artifact, fills approved + contact, flags the novel field", async ({ page }) => {
  await setup(page);
  await page.evaluate(async () => { await runApprovedFill(window.__match); });

  // Contact fields from profile.
  await expect(page.locator("#fname")).toHaveValue("Jane");
  await expect(page.locator("#lname")).toHaveValue("Doe");
  await expect(page.locator("#email")).toHaveValue("jane@example.com");
  await expect(page.locator("#phone")).toHaveValue("512-555-0100");

  // Approved answer + approved radio.
  await expect(page.locator("#why")).toHaveValue(MATCH.answers["Why do you want to work at Acme?"]);
  await expect(page.locator('input[name="work_auth"][value="Yes"]')).toBeChecked();

  // The EXACT versioned artifact is attached — and the versioned endpoint was hit.
  const fileName = await page.evaluate(() => document.getElementById("resume").files?.[0]?.name || "");
  expect(fileName).toBe(`resume_${MATCH.resume_variant_id}.pdf`);
  const pdfUrl = await page.evaluate(() => window.__net.pdfServed);
  expect(pdfUrl).toContain(`/resume/versions/${MATCH.resume_variant_id}/pdf`);

  // Novel field paused, never guessed.
  const needs = await page.evaluate(() => (panelState.needsCall || []).map(n => n.label));
  expect(needs).toContain("What is your favorite programming paradigm?");
  await expect(page.locator("#paradigm")).toHaveValue("");

  // Rule 1: nothing was submitted.
  expect(await page.evaluate(() => window.__submitted)).toBe(false);

  // Emit a field report (the pre-submit summary the human reviews).
  const report = await page.evaluate(() => ({
    company: panelState.approvedItem?.company,
    role: panelState.approvedItem?.role,
    resume_artifact: panelState.approvedItem?.variantId,
    filled: (panelState.filled || []).map(f => ({ label: f.label, value: f.value, source: f.source })),
    needs_your_call: (panelState.needsCall || []).map(n => n.label),
  }));
  report.resume_attached_from = await page.evaluate(() => window.__net.pdfServed);
  report.submitted = await page.evaluate(() => window.__submitted);
  fs.writeFileSync(
    path.join(__dirname, "approved-report.json"),
    JSON.stringify(report, null, 2),
  );
});

test("pause-and-ask remembers the answer and the next step auto-resolves it", async ({ page }) => {
  await setup(page);
  await page.evaluate(async () => { await runApprovedFill(window.__match); });

  // Answer the paused field through the panel's Save & fill control.
  const need = "#localhire-floating-panel [data-need='0']";
  const save = "#localhire-floating-panel [data-savefill='0']";
  await page.fill(need, "Functional programming");
  await page.click(save);

  // Filled on the page + remembered in BOTH stores.
  await expect(page.locator("#paradigm")).toHaveValue("Functional programming");
  const persisted = await page.evaluate(() => ({
    learned: window.__net.learned["What is your favorite programming paradigm?"],
    patched: window.__net.patched?.answers?.["What is your favorite programming paradigm?"],
  }));
  expect(persisted.learned).toBe("Functional programming");
  expect(persisted.patched).toBe("Functional programming");

  // Advance to step 2 (dynamically injected, like a real portal).
  await page.click("#next");
  await page.waitForSelector("#paradigm2");
  await page.evaluate(async () => { await runApprovedFill(window.__match); });

  // The remembered answer now auto-fills without asking; approved YoE fills too.
  await expect(page.locator("#paradigm2")).toHaveValue("Functional programming");
  await expect(page.locator("#yoe")).toHaveValue("4");
  expect(await page.evaluate(() => window.__submitted)).toBe(false);
});
