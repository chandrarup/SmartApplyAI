"""
Live ATS autofill smoke tests.

Run manually only:
    LIVE_ATS=1 HEADLESS=0 pytest tests/integration/test_live_ats.py -v --tb=short

These tests navigate to public, no-auth application pages. They mock only
/tracker/match and the versioned resume PDF so the extension behaves as though
a reviewed queue item is ready for the current page. The local backend must
still be running because /profile supplies the real user profile.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import requests

playwright = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright.sync_playwright

pytestmark = pytest.mark.live

if os.environ.get("LIVE_ATS") != "1":
    pytest.skip("Set LIVE_ATS=1 to run public live-site ATS tests.", allow_module_level=True)

BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:5001").rstrip("/")
EXTENSION_PATH = os.path.abspath(os.environ.get("EXTENSION_PATH", "./extension"))
HEADLESS = os.environ.get("HEADLESS", "0") != "0"
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "live_ats_urls.yaml"

PDF_BYTES = (
    b"%PDF-1.4\n"
    b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n"
    b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n"
    b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>endobj\n"
    b"trailer<< /Root 1 0 R >>\n%%EOF\n"
)


def _strip(value: str) -> str:
    return value.strip().strip("'\"")


def _load_live_cases() -> list[dict]:
    cases: list[dict] = []
    current: dict | None = None
    for raw in FIXTURE.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line == "urls:":
            continue
        if line.startswith("- "):
            if current:
                cases.append(current)
            current = {}
            line = line[2:].strip()
            if not line:
                continue
        if current is not None and ":" in line:
            key, value = line.split(":", 1)
            current[key.strip()] = _strip(value)
    if current:
        cases.append(current)
    enabled = []
    for case in cases:
        if str(case.get("enabled", "true")).lower() == "false":
            continue
        case["min_filled"] = int(case.get("min_filled") or 3)
        enabled.append(case)
    return enabled


def _backend_ready() -> bool:
    try:
        res = requests.get(f"{BACKEND_URL}/health", timeout=5)
        return res.status_code < 500
    except requests.RequestException:
        return False


@pytest.fixture
def browser_ctx(tmp_path):
    if not _backend_ready():
        pytest.skip(f"Backend not running at {BACKEND_URL}")
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(tmp_path / "live-profile"),
            headless=HEADLESS,
            args=[
                f"--load-extension={EXTENSION_PATH}",
                f"--disable-extensions-except={EXTENSION_PATH}",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        yield ctx
        ctx.close()


def _approved_answers() -> dict[str, str]:
    return {
        "First Name": "Chandra",
        "Last Name": "Daka",
        "Full name": "Chandra Rup Daka",
        "Full Name": "Chandra Rup Daka",
        "Legal Name": "Chandra Rup Daka",
        "Email": "chandrarupdaka@gmail.com",
        "Phone": "+1 253-632-3181",
        "Why are you interested in this role?": "I am excited by the team, product impact, and the chance to contribute reliable engineering work.",
        "Tell us about yourself": "I build production software, data systems, and applied AI workflows with strong ownership and communication.",
        "Are you legally authorized to work in the United States?": "Yes",
        "Are you legally authorized to work in the country for which you are applying?": "Yes",
        "Will you now or in the future require sponsorship for employment visa status?": "No",
        "Will you now or in the future require sponsorship for employment visa status (e.g., H-1B visa status)?": "No",
        "LinkedIn Profile": "https://www.linkedin.com/in/example",
        "LinkedIn URL": "https://www.linkedin.com/in/example",
        "Portfolio URL": "https://github.com/example",
        "Current location": "Houston, TX",
        "Location (City)": "Houston",
    }


def _install_backend_routes(ctx, case: dict):
    def tracker_match(route):
        parsed = urlparse(route.request.url)
        qs = parse_qs(parsed.query)
        page_url = qs.get("url", [case["url"]])[0]
        payload = {
            "match": {
                "id": f"live-{case['platform']}",
                "company": case["company"],
                "role": case["role"],
                "url": page_url,
                "platform": case["platform"],
                "resume_variant_id": "live-test",
                "answers": _approved_answers(),
            }
        }
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    ctx.route(f"{BACKEND_URL}/tracker/match**", tracker_match)
    ctx.route(
        f"{BACKEND_URL}/autofill/learned**",
        lambda route: route.fulfill(status=200, content_type="application/json", body="{}"),
    )
    ctx.route(
        f"{BACKEND_URL}/resume/versions/live-test/pdf",
        lambda route: route.fulfill(status=200, content_type="application/pdf", body=PDF_BYTES),
    )


def _field_state(page) -> dict:
    return page.evaluate(
        """() => {
          const docs = [document];
          for (const frame of document.querySelectorAll('iframe')) {
            try {
              if (frame.contentDocument && frame.contentDocument.body) docs.push(frame.contentDocument);
            } catch (e) {}
          }
          const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
          const fields = docs.flatMap(doc => Array.from(doc.querySelectorAll(
            'input:not([type=hidden]):not([type=submit]):not([type=button]), textarea, select, [contenteditable="true"]'
          ))).filter(visible);
          const filledFields = fields.filter(el => {
            if (el.matches('input[type=file]')) return el.files && el.files.length > 0;
            if (el.matches('input[type=checkbox], input[type=radio]')) return el.checked;
            if (el.matches('[contenteditable="true"]')) return (el.innerText || '').trim().length > 0;
            if (el.tagName === 'SELECT') return !!el.value && el.selectedIndex > 0;
            return !!(el.value || '').trim();
          });
          const unfilled = fields.filter(el => !filledFields.includes(el)).slice(0, 15).map(el => ({
            tag: el.tagName.toLowerCase(),
            type: el.type || '',
            name: el.name || '',
            id: el.id || '',
            label: (el.getAttribute('aria-label') || el.placeholder || '').slice(0, 80),
          }));
          return { filled: filledFields.length, total: fields.length, unfilled };
        }"""
    )


def _click_fill(page):
    page.wait_for_selector("#localhire-floating-panel", timeout=30000)
    pill = page.locator("#localhire-floating-panel .lh-pill").first
    if pill.count() > 0:
        pill.click(timeout=5000)
    page.locator("#lh-fill").click(timeout=10000)


@pytest.mark.parametrize("case", _load_live_cases(), ids=lambda c: f"{c['platform']}-{c['company']}")
def test_live_ats_fill_smoke(browser_ctx, case):
    _install_backend_routes(browser_ctx, case)
    page = browser_ctx.new_page()
    try:
        try:
            page.goto(case["url"], wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            if "ERR_SOCKET_NOT_CONNECTED" in str(e):
                pytest.skip(f"Browser could not reach live ATS host: {e}")
            raise
        try:
            page.wait_for_function(
                """() => {
                  const docs = [document];
                  for (const frame of document.querySelectorAll('iframe')) {
                    try {
                      if (frame.contentDocument && frame.contentDocument.body) docs.push(frame.contentDocument);
                    } catch (e) {}
                  }
                  const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                  return docs.flatMap(doc => Array.from(doc.querySelectorAll(
                    'input:not([type=hidden]):not([type=submit]):not([type=button]), textarea, select, [contenteditable="true"]'
                  ))).filter(visible).length > 0;
                }""",
                timeout=10000,
            )
        except Exception as e:
            if "Timeout" in str(e):
                state = _field_state(page)
                pytest.skip(f"No public application fields visible; fixture may be closed or gated. state={state}")
            raise
        _click_fill(page)
        page.wait_for_function(
            """min => {
              const docs = [document];
              for (const frame of document.querySelectorAll('iframe')) {
                try {
                  if (frame.contentDocument && frame.contentDocument.body) docs.push(frame.contentDocument);
                } catch (e) {}
              }
              const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
              return docs.flatMap(doc => Array.from(doc.querySelectorAll('input:not([type=hidden]):not([type=submit]):not([type=button]), textarea, select, [contenteditable="true"]')))
                .filter(visible)
                .filter(el => {
                  if (el.matches('input[type=file]')) return el.files.length > 0;
                  if (el.matches('input[type=checkbox], input[type=radio]')) return el.checked;
                  return ((el.value || el.innerText || '').trim().length > 0);
                }).length >= min;
            }""",
            arg=case["min_filled"],
            timeout=60000,
        )
        state = _field_state(page)
        assert state["filled"] >= case["min_filled"], (
            f"{case['platform']} filled {state['filled']}/{state['total']} fields; "
            f"unfilled={state['unfilled']}"
        )
    finally:
        page.close()
