# JD Extraction Playwright Harness

Tests `getCleanText()` and `detectPlatform()` from `extension/content.js` **without modifying** that file.

## Run

```bash
npm run test:jd-extraction
```

Or manually:

```bash
node tests/jd_extraction/build-fixtures.mjs
cd tests/jd_extraction && npx playwright test --config=playwright.config.mjs
```

Requires `npx playwright install chromium` once.

## Layout

- `fixtures/happy/` — copies of `backend/test_*.html` + `*.expected.json`
- `fixtures/adversarial/` — 11 breakage scenarios + `*.expected.json`
- `harness.spec.mjs` — loads fixture in Chromium, injects `content.js`, asserts
- `FINDINGS_jd_extraction.md` — generated at repo root after each run

## lazy_loaded.html

The harness runs extraction immediately (len ~10) and after 1200ms wait (len ~56). **Timing changes the result** — documents the race users hit on SPAs.
