/**
 * Live URL Tester — visits real job pages, injects content script,
 * runs autofill, screenshots every step, reports issues.
 *
 * Usage: node backend/livetest.js
 */

const { chromium } = require('playwright');
const fs   = require('fs');
const path = require('path');
const http = require('http');

const API      = 'http://127.0.0.1:5001';
const EXT_DIR  = path.join(__dirname, '../extension');
const SHOTS    = path.join(__dirname, 'screenshots/live');
fs.mkdirSync(SHOTS, { recursive: true });

const G = '\x1b[32m', R = '\x1b[31m', Y = '\x1b[33m', B = '\x1b[1m', D = '\x1b[2m', X = '\x1b[0m';
const ok   = (m, d='') => console.log(`  ${G}✓${X} ${m}${d ? D+' '+d+X : ''}`);
const fail = (m, d='') => console.log(`  ${R}✗${X} ${m}${d ? D+' '+d+X : ''}`);
const info = (m)       => console.log(`  ${Y}→${X} ${m}`);

// ── Inject content.js (chrome API stubs + actual script) ─────────────────────
async function injectExtension(page) {
  const src = fs.readFileSync(path.join(EXT_DIR, 'content.js'), 'utf8');
  await page.addScriptTag({ content: `
    window.__lhApiUrl = '${API}';
    window.chrome = {
      runtime: {
        id: 'livetest-ext-id',
        sendMessage: function(msg, cb) {
          if (!cb) return;
          if (msg.action === 'get_settings')
            setTimeout(() => cb({ url: '${API}', llm: 'ollama', autoRetrigger: false }), 0);
          else if (msg.action === 'get_page_context')
            setTimeout(() => cb({ text: null }), 0);
          else if (msg.action === 'cache_page_context')
            setTimeout(() => cb({ success: true }), 0);
          else
            setTimeout(() => cb({}), 0);
        },
        onMessage: { addListener: function() {} },
        lastError: null,
      },
      storage: {
        session: {
          get:  function(k, cb) { cb({}); },
          set:  function(v, cb) { if(cb) cb(); },
        },
        local:  { get: function(k,cb){cb({});}, set: function(){} },
        sync:   { get: function(k,cb){cb({ apiUrl:'${API}', llm:'ollama' });}, set: function(){} },
      },
    };
    window.CSS = window.CSS || { escape: function(s){ return s.replace(/[^\\w-]/g,'\\\\$&'); } };
  `});
  await page.addScriptTag({ content: src });
  await page.waitForTimeout(1000); // let panel inject
}

async function shot(page, name) {
  const f = path.join(SHOTS, `${name}.png`);
  await page.screenshot({ path: f, fullPage: false });
  return f;
}

// ── Verify panel exists and open it ──────────────────────────────────────────
async function openPanel(page, site) {
  const pill = page.locator('#localhire-floating-panel .lh-pill');
  const exists = await pill.isVisible({ timeout: 3000 }).catch(() => false);
  if (!exists) { fail(`${site}: Panel pill not found`); return false; }
  ok(`${site}: Panel pill injected`);
  await pill.click();
  await page.waitForTimeout(400);
  await shot(page, `${site}_panel_open`);
  return true;
}

// ── Run autofill and collect results ─────────────────────────────────────────
async function runFill(page, site) {
  // Click Fill This Form
  const fillBtn = page.locator('#lh-fill');
  const fillVisible = await fillBtn.isVisible({ timeout: 3000 }).catch(() => false);
  if (!fillVisible) { fail(`${site}: Fill button not visible`); return null; }

  info(`${site}: Clicking Fill This Form...`);
  await fillBtn.click();

  // Wait for "Done!" or "field(s) filled" in panel log
  const logEl = page.locator('#localhire-floating-panel .lh-log, #localhire-floating-panel [class*="log"]');
  try {
    await page.waitForFunction(() => {
      const panel = document.getElementById('localhire-floating-panel');
      if (!panel) return false;
      const text = panel.innerText || panel.textContent || '';
      return text.includes('Done!') || text.includes('field(s) filled');
    }, { timeout: 90000 });
  } catch {}
  await page.waitForTimeout(1500);
  await shot(page, `${site}_after_fill`);

  // Collect filled field count from panel
  const panelText = await page.locator('#localhire-floating-panel').innerText().catch(() => '');
  // Match "Total filled: 19" or "19 field(s) filled"
  const filledMatch = panelText.match(/Total filled:\s*(\d+)|(\d+)\s+field.*filled/i);
  const filled = filledMatch ? parseInt(filledMatch[1] || filledMatch[2]) : 0;
  return { filled, panelText };
}

// ── Get JD text from page + child frames ─────────────────────────────────────
async function getJD(page) {
  // First try from main frame via content.js
  const mainText = await page.evaluate(() => {
    if (typeof getCleanText === 'function') return getCleanText();
    return (document.body?.innerText || document.body?.textContent || '').slice(0, 3000);
  }).catch(() => '');

  if (mainText && mainText.length > 300) return mainText;

  // Try child frames (iCIMS embeds JD in same-origin iframe)
  for (const frame of page.frames()) {
    if (frame === page.mainFrame()) continue;
    try {
      const frameText = await frame.evaluate(() =>
        (document.body?.innerText || document.body?.textContent || '').trim()
      ).catch(() => '');
      if (frameText && frameText.length > 300) return frameText;
    } catch {}
  }
  return mainText;
}

// ── Main test for each site ───────────────────────────────────────────────────
async function testSite(browser, config, results) {
  const { name, url, expectedPlatform, checks } = config;
  console.log(`\n${B}══ ${name.toUpperCase()} ══${X}`);
  console.log(`  ${D}${url}${X}\n`);

  // bypassCSP so Ashby/other CSP-strict pages allow our script injection
  const page = await browser.newPage({ bypassCSP: true });
  page.setDefaultTimeout(30000);

  // Route all localhost API calls — proxy them to the actual backend
  // This is needed because HTTPS pages can't fetch http://localhost in headless
  await page.route('**/127.0.0.1:5001/**', async route => {
    const url = route.request().url().replace(/^https?:/, 'http:');
    try {
      const response = await fetch(url, {
        method: route.request().method(),
        headers: { ...Object.fromEntries(Object.entries(route.request().headers())), host: '127.0.0.1:5001' },
        body: route.request().postData() || undefined,
      });
      const body = await response.arrayBuffer();
      await route.fulfill({
        status: response.status,
        headers: Object.fromEntries(response.headers.entries()),
        body: Buffer.from(body),
      });
    } catch (e) {
      await route.abort();
    }
  });

  try {
    // 1. Load page
    info(`Loading page...`);
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
    // Accept cookie banners automatically
    await Promise.all([
      page.getByRole('button', { name: /accept|agree|ok|allow/i }).click().catch(() => {}),
      page.locator('button:text-matches("Accept|Agree|OK", "i")').first().click().catch(() => {}),
    ]);
    await page.waitForTimeout(2500); // let React/SPA render
    await shot(page, `${name}_loaded`);
    ok(`${name}: Page loaded — ${await page.title()}`);
    results.total++;  results.pass++;

    // 2. Inject extension
    await injectExtension(page);
    await page.waitForTimeout(800);

    // 3. Platform detection
    results.total++;
    const platform = await page.evaluate(() => {
      if (typeof detectPlatform === 'function') return detectPlatform();
      return 'detectPlatform not available';
    }).catch(() => 'error');
    if (platform === expectedPlatform) { ok(`Platform detected: ${platform}`); results.pass++; }
    else { fail(`Platform: expected "${expectedPlatform}", got "${platform}"`); results.fail++; results.failures.push(`${name}:platform`); }

    // 4. JD extraction
    results.total++;
    const jd = await getJD(page);
    const jdLen = jd.length;
    const hasContent = jdLen > 300;
    if (hasContent) { ok(`JD extracted: ${jdLen} chars — "${jd.slice(0,80)}..."`); results.pass++; }
    else { fail(`JD extraction: only ${jdLen} chars — "${jd.slice(0,80)}"`); results.fail++; results.failures.push(`${name}:jd-extract`); }

    // 5. Open panel
    results.total++;
    const panelOk = await openPanel(page, name);
    if (panelOk) { results.pass++; }
    else { results.fail++; results.failures.push(`${name}:panel`); }

    // 6. Check panel tabs
    if (panelOk) {
      results.total++;
      const tabs = await page.locator('#localhire-floating-panel .lh-tab').allTextContents().catch(() => []);
      const hasAll = ['Fill','Resume','Cover','Ask AI'].every(t => tabs.some(x=>x.includes(t)));
      if (hasAll) { ok(`All 4 tabs present: ${tabs.join(', ')}`); results.pass++; }
      else { fail(`Missing tabs, got: ${tabs.join(', ')}`); results.fail++; results.failures.push(`${name}:tabs`); }
    }

    // 7. Run autofill (only on application pages, not JD listing pages)
    if (config.isApplyPage) {
      results.total++;
      const fillResult = await runFill(page, name);
      if (fillResult && fillResult.filled > 0) {
        ok(`Autofill: ${fillResult.filled} field(s) filled`); results.pass++;
        // Verify specific fields
        for (const [selector, expected, label] of (checks || [])) {
          results.total++;
          const val = await page.inputValue(selector).catch(() => '');
          const pass = typeof expected === 'function' ? expected(val) : val.toLowerCase().includes(expected.toLowerCase());
          if (pass) { ok(`  ${label}: "${val}"`); results.pass++; }
          else { fail(`  ${label}: expected "${expected}", got "${val}"`); results.fail++; results.failures.push(`${name}:field-${label}`); }
        }
      } else {
        fail(`Autofill: 0 fields filled`);
        if (fillResult) info(`  Panel text: ${fillResult.panelText.slice(0,200)}`);
        results.fail++; results.failures.push(`${name}:autofill`);
      }
    } else {
      // JD page — test Customize Resume on Web button
      results.total++;
      const customizeBtn = page.locator('#lh-customize');
      const custVisible = await customizeBtn.isVisible({ timeout: 2000 }).catch(() => false);
      if (custVisible) { ok(`"Customize Resume on Web" button visible`); results.pass++; }
      else { fail(`"Customize Resume on Web" button missing`); results.fail++; results.failures.push(`${name}:customize-btn`); }

      // Test Resume tab match score
      if (panelOk && hasContent) {
        results.total++;
        info(`Testing Resume tab analysis...`);
        const resumeTab = page.locator('#localhire-floating-panel .lh-tab').filter({ hasText: 'Resume' });
        await resumeTab.click().catch(() => {});
        await page.waitForTimeout(300);
        const startBtn = page.locator('#lh-res-start');
        const startVisible = await startBtn.isVisible({ timeout: 2000 }).catch(() => false);
        if (startVisible) {
          await startBtn.click();
          info(`Waiting for match score analysis...`);
          try {
            await page.waitForFunction(() => {
              const panel = document.getElementById('localhire-floating-panel');
              if (!panel) return false;
              return panel.querySelector('#lh-res-align') !== null ||
                     panel.innerText.includes('You Have') ||
                     panel.innerText.includes('Missing');
            }, { timeout: 120000 });
            await shot(page, `${name}_resume_tab_analyzed`);
            const scoreText = await page.locator('#localhire-floating-panel').innerText().catch(() => '');
            const scoreMatch = scoreText.match(/(\d+)%/);
            if (scoreMatch) {
              ok(`Resume tab: Match score = ${scoreMatch[1]}%`); results.pass++;
            } else {
              fail(`Resume tab: No score found`); results.fail++; results.failures.push(`${name}:resume-score`);
            }
          } catch {
            fail(`Resume tab: Analysis timed out`); results.fail++; results.failures.push(`${name}:resume-timeout`);
            await shot(page, `${name}_resume_tab_timeout`);
          }
        } else {
          fail(`Resume tab: Start button not found`); results.fail++; results.failures.push(`${name}:resume-start`);
        }
      }
    }

    await shot(page, `${name}_final`);

  } catch (e) {
    fail(`${name}: Unexpected error — ${e.message}`);
    results.fail++; results.failures.push(`${name}:crash`);
    try { await shot(page, `${name}_crash`); } catch {}
  } finally {
    await page.close();
  }
}

// ── Sites config ─────────────────────────────────────────────────────────────
const SITES = [
  {
    name: 'ashby',
    url: 'https://jobs.ashbyhq.com/ready/3db71e58-063f-461b-bf90-edf08dd53264',
    expectedPlatform: 'ashby',
    isApplyPage: false,
    checks: [],
  },
  {
    name: 'lever-apply',
    url: 'https://jobs.lever.co/endpointclinical/d8e94671-b722-4bd9-94a6-c0f27008acbf/apply?utm_source=jobright&jr_id=69fdab1c52e2b44f558ac1ce',
    expectedPlatform: 'lever',
    isApplyPage: true,
    checks: [
      ['input[name="name"]', v => v.length > 0, 'Name'],
      ['input[name="email"]', '@', 'Email'],
    ],
  },
  {
    name: 'icims-cotiviti',
    url: 'https://careers-cotiviti.icims.com/jobs/18971/intern---generative-ai-research-engineer/job?jr_id=69fe8cde6bcf315dc8f5839e&mobile=false&width=1100&height=500&bga=true&needsRedirect=false&jan1offset=-360&jun1offset=-300',
    expectedPlatform: 'icims',
    isApplyPage: false,
    checks: [],
  },
];

// ── Main ─────────────────────────────────────────────────────────────────────
async function main() {
  console.log(`\n${B}══════════════════════════════════════════════════════${X}`);
  console.log(`${B}  LocalHire Agent — Live Site Automation Tests${X}`);
  console.log(`${B}══════════════════════════════════════════════════════${X}`);
  console.log(`  Screenshots → ${SHOTS}\n`);

  const results = { total: 0, pass: 0, fail: 0, failures: [] };

  const browser = await chromium.launch({
    headless: true,
    args: [
      '--no-sandbox',
      '--disable-web-security',             // bypass CORS
      '--allow-running-insecure-content',   // allow HTTPS page → HTTP localhost
      '--disable-features=VizDisplayCompositor',
      '--ignore-certificate-errors',
    ],
  });

  try {
    for (const site of SITES) {
      await testSite(browser, site, results);
    }
  } finally {
    await browser.close();
  }

  // Summary
  const pct = results.total > 0 ? Math.round((results.pass / results.total) * 100) : 0;
  const col = pct === 100 ? G : pct >= 70 ? Y : R;
  console.log(`\n${B}══════════════════════════════════════════════════════${X}`);
  console.log(`${B}  Result: ${col}${results.pass}/${results.total} passed (${pct}%)${X}`);
  if (results.failures.length) {
    console.log(`\n  Issues to fix:`);
    results.failures.forEach(f => console.log(`    ${R}✗${X} ${f}`));
  } else {
    console.log(`  ${G}All checks passing!${X}`);
  }
  console.log(`${B}══════════════════════════════════════════════════════${X}\n`);
}

main().catch(e => { console.error('Fatal:', e.message); process.exit(1); });
