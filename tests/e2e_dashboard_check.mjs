// One-shot dashboard verification: visits every page, exercises the new
// Job Sourcing + Knowledge & Memory features, and reports console errors.
// Run: node tests/e2e_dashboard_check.mjs [--scrape]
import { chromium } from 'playwright';
import fs from 'node:fs';

const BASE = 'http://127.0.0.1:5001';
const DO_SCRAPE = process.argv.includes('--scrape');
const SHOTS = 'screenshots/e2e';
fs.mkdirSync(SHOTS, { recursive: true });

const errors = [];
const browser = await chromium.launch({ channel: 'chrome', headless: true }).catch(() => chromium.launch({ headless: true }));
const page = await browser.newPage();
// The dashboard redirects to /login without a selected profile — seed one like the login page would.
await page.addInitScript(() => {
  localStorage.setItem('lh_profile_id', 'default');
  localStorage.setItem('lh_profile_name', 'E2E Check');
  localStorage.setItem('lh_profile_color', '#F97316');
});
page.on('console', m => { if (m.type() === 'error') errors.push(`[console] ${m.text().slice(0, 300)}`); });
page.on('pageerror', e => errors.push(`[pageerror] ${String(e).slice(0, 300)}`));
page.on('requestfailed', r => errors.push(`[requestfailed] ${r.method()} ${r.url()} — ${r.failure()?.errorText}`));

const ok = (name, cond) => console.log(`${cond ? '✓' : '✗ FAIL'} ${name}`);

await page.goto(`${BASE}/dashboard`, { waitUntil: 'networkidle' });
ok('dashboard loads', (await page.title()) !== '');

// 1. Walk every sidebar page
const navs = await page.locator('.ni').all();
for (const n of navs) {
  const label = (await n.textContent())?.trim().replace(/\d+$/, '');
  await n.click();
  await page.waitForTimeout(400);
  const pgId = await n.getAttribute('data-pg');
  const visible = await page.locator(`#${pgId}`).isVisible();
  ok(`page "${label}" (${pgId}) becomes visible`, visible);
}

// 2. Job Sourcing page specifics
await page.locator('[data-pg="pg-sourcing"]').click();
await page.waitForTimeout(1500);
const active = await page.locator('#js-active').textContent();
ok(`sourcing stats populated (active=${active})`, active !== '—' && Number(active) >= 0);
await page.screenshot({ path: `${SHOTS}/sourcing.png`, fullPage: true });

if (DO_SCRAPE) {
  await page.locator('#scrapeNowBtn').click();
  await page.waitForTimeout(1000);
  const st = await page.locator('#scrapeStatus').textContent();
  ok(`scrape starts (status: ${st.trim()})`, /Running/i.test(st));
  // wait for completion (max 4 min)
  await page.waitForFunction(
    () => /Done|Failed/.test(document.getElementById('scrapeStatus').textContent),
    { timeout: 240000 },
  );
  const final = await page.locator('#scrapeStatus').textContent();
  ok(`scrape finishes (${final.trim()})`, /Done/.test(final));
  const res = await page.locator('#scrapeResult').textContent();
  ok('scrape result JSON shown', res.includes('fetched'));
  await page.screenshot({ path: `${SHOTS}/sourcing_after_scrape.png`, fullPage: true });
}

// 3. Knowledge & Memory page specifics
await page.locator('[data-pg="pg-knowledge"]').click();
await page.waitForTimeout(1500);
const skillsTxt = await page.locator('#kn-skillsCount').textContent();
ok(`skills table loads (${skillsTxt})`, /\d+ skills/.test(skillsTxt));
const evTxt = await page.locator('#kn-events').textContent();
ok('memory log renders', evTxt.trim().length > 0);

await page.fill('#kn-query', 'machine learning deployment');
await page.locator('#kn-searchBtn').click();
await page.waitForFunction(
  () => !/Searching…/.test(document.getElementById('kn-results').textContent),
  { timeout: 60000 },
);
const results = await page.locator('#kn-results').textContent();
ok('semantic search returns content', results.trim().length > 30 && !/failed/i.test(results));
await page.screenshot({ path: `${SHOTS}/knowledge.png`, fullPage: true });

// 4. Analyze page still works (regression)
await page.locator('[data-pg="pg-analyze"]').click();
await page.waitForTimeout(300);
ok('analyze page visible', await page.locator('#pg-analyze').isVisible());

await browser.close();

console.log('\n──── console/page errors ────');
if (!errors.length) console.log('(none)');
else errors.forEach(e => console.log(e));
process.exit(errors.filter(e => e.startsWith('[pageerror]')).length ? 1 : 0);
