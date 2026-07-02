# Autofill Playwright Harness

Tests `getFormFields()`, `getLabelForInput()`, `fillField()`, `setNativeValue()`, `setSelectValue()`, `setRadioGroup()`, and `getRadioGroups()` from `extension/content.js` **without modifying content.js**.

## Setup

```bash
npm install
npx playwright install chromium
node tests/autofill/build-fixtures.mjs   # copy backend/test_*.html → fixtures/platforms/
```

## Run

```bash
npm run test:autofill
```

Or:

```bash
cd tests/autofill && playwright test --config=playwright.config.mjs
```

## Output

- `FINDINGS_autofill.md` — per platform+variant results (mapped/filled/skipped/wrong)
- `tests/autofill/last-run.json` — machine-readable run data

## Fixtures

| Directory | Contents |
|-----------|----------|
| `fixtures/platforms/` | Copies of `backend/test_*.html` + `*.expected.json` |
| `fixtures/variants/` | Adversarial field scenarios (`field_shadow_dom.html`, etc.) |

Server: `http://127.0.0.1:8766` (see `fixture-server.mjs`).
