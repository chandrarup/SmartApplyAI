# Live ATS Autofill Testing

These tests exercise the Chrome extension on public, no-login ATS application pages. They never click Submit.

## Prerequisites

Start the backend from the repo root:

```bash
cd backend
source .venv/bin/activate
uvicorn main:app --host 127.0.0.1 --port 5001
```

Install Playwright browsers if needed:

```bash
python -m playwright install chromium
```

## Run

```bash
LIVE_ATS=1 HEADLESS=0 pytest tests/integration/test_live_ats.py -v --tb=short
```

The live test mocks only `GET /tracker/match`, `GET /autofill/learned`, and the exact versioned resume PDF. The local backend still supplies the real profile via `GET /profile`.

## Refresh URLs

Live postings close. Update `tests/fixtures/live_ats_urls.yaml` when a case stops exposing a public form.

1. Find a public posting that does not require employer login before the first form page.
2. Prefer direct apply URLs:
   - Greenhouse: `job-boards.greenhouse.io/.../jobs/<id>` or `boards.greenhouse.io/.../jobs/<id>`
   - Lever: append `/apply` to the posting URL.
   - Ashby: use the `/application` URL when available.
   - iCIMS: use a URL with `mode=apply` when available.
3. Set `min_filled` to the smallest reliable number of non-sensitive fields expected after clicking Fill.
4. Leave login-gated examples in the file with `enabled: false` and a note/source URL.

## Debug Checklist

If a live case fails:

- Confirm the extension loaded with no errors in `chrome://extensions`.
- Run with `HEADLESS=0` and watch for the `#localhire-floating-panel`.
- Check whether the URL is still an application form, not a closed-job page or login wall.
- Inspect the panel log for `No queue match`, `Backend offline`, or field-fill counts.
- Confirm the manifest has an explicit host pattern for the ATS. Do not add `<all_urls>`.
- Do not submit the form while debugging. Use only Fill and, after a manual submit, Mark as Applied.
