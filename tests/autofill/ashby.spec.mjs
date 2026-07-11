// Ashby-specific coverage: full field detection (incl. React comboboxes),
// click-to-open combobox fill (the veteran-status bug), and Shadow-DOM style
// isolation of the floating panel (the "too merged / no space" bug).
import { test, expect } from "@playwright/test";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "../..");
const CONTENT_JS = path.join(ROOT, "extension/content.js");
const BASE = "http://127.0.0.1:8766";
const URL = `${BASE}/variants/ashby_form.html`;

const CHROME_MOCK = `(() => {
  if (window.chrome?.runtime?.id) return;
  const noop = () => {};
  const store = { get: (k, cb) => cb({}), set: (o, cb) => cb && cb() };
  window.chrome = {
    runtime: { id: "ashby-test", sendMessage: noop, onMessage: { addListener: noop } },
    storage: { local: store, session: store, sync: store },
  };
})();`;

async function inject(page) {
  await page.goto(URL, { waitUntil: "domcontentloaded" });
  await page.evaluate(CHROME_MOCK);
  await page.addScriptTag({ path: CONTENT_JS });
}

test("detects every field including custom comboboxes and required flags", async ({ page }) => {
  await inject(page);
  const fields = await page.evaluate(() =>
    getFormFields("ashby").map((f) => ({ label: f.label, type: f.type, widget: f.widget, required: !!f.required }))
  );
  const labels = fields.map((f) => f.label);

  // Natives + both comboboxes + the textarea — the old scanner missed the comboboxes.
  for (const must of ["Full name", "Email", "authorized to work", "Veteran status", "Why do you want"]) {
    expect(labels.some((l) => l.toLowerCase().includes(must.toLowerCase())), `missing "${must}" in [${labels.join(", ")}]`).toBeTruthy();
  }

  const combos = fields.filter((f) => f.widget === "combobox");
  expect(combos.length).toBeGreaterThanOrEqual(2);

  const name = fields.find((f) => f.label.toLowerCase().includes("full name"));
  expect(name.required).toBeTruthy();
  const links = fields.find((f) => f.label.toLowerCase().includes("linkedin"));
  expect(links.required).toBeFalsy();
});

test("fills a click-to-open combobox (veteran status)", async ({ page }) => {
  await inject(page);
  const result = await page.evaluate(async () => {
    const fields = getFormFields("ashby");
    const vet = fields.find((f) => f.label.toLowerCase().includes("veteran"));
    const ok = await fillAshbyCombobox(vet.element, "I am not a protected veteran");
    return { ok, value: vet.element.getAttribute("data-value"), text: vet.element.textContent.trim() };
  });
  expect(result.ok).toBeTruthy();
  expect(result.value).toBe("I am not a protected veteran");
  expect(result.text).toBe("I am not a protected veteran");
});

test("panel resists hostile host-page CSS via its shadow root", async ({ page }) => {
  await inject(page);
  await page.evaluate(async () => { await injectPanel(); });
  await page.waitForFunction(() => {
    const h = document.getElementById("localhire-floating-panel");
    return h && h.shadowRoot && h.shadowRoot.querySelector(".lh-pill");
  }, { timeout: 5000 });

  const styles = await page.evaluate(() => {
    const host = document.getElementById("localhire-floating-panel");
    const pill = host.shadowRoot.querySelector(".lh-pill");
    const cs = getComputedStyle(pill);
    return {
      hasShadow: !!host.shadowRoot,
      hostFindable: document.getElementById("localhire-floating-panel") === host,
      pillFont: cs.fontSize,          // shadow value 22px, NOT the hostile 40px
      pillBox: cs.boxSizing,          // our reset border-box, NOT hostile content-box
      pillWidth: cs.width,            // 52px, unaffected by hostile padding
      hostPos: getComputedStyle(host).position,
    };
  });
  expect(styles.hasShadow).toBeTruthy();
  expect(styles.hostFindable).toBeTruthy();
  expect(styles.pillFont).toBe("22px");
  expect(styles.pillBox).toBe("border-box");
  expect(styles.pillWidth).toBe("52px");
  expect(styles.hostPos).toBe("fixed");
});
