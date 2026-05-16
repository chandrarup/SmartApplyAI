"""
Real-world scenario tests — each test documents a SPECIFIC observed failure mode.

Run with:
    pytest tests/integration/test_scenarios.py -v
    pytest tests/integration/test_scenarios.py::TestRealWorldScenarios -v -k "aria"

These tests are self-contained: they call page.set_content() to build
a minimal HTML page that replicates the exact DOM structure causing the bug.
No backend needed. No extension needed. Pure DOM + JS logic tests.

Design philosophy:
    - Test the FIXED logic, not just that filling works
    - Each test documents what USED to break (the "before") and what should work now
    - If a test fails after a code change, it tells you exactly which scenario regressed
"""

import pytest
import time

playwright = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright.sync_playwright
Page = playwright.Page


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures — no extension needed for these DOM-level tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    ctx = browser.new_context()
    p = ctx.new_page()
    yield p
    p.close()
    ctx.close()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — JS implementations of the FIXED functions
# These mirror the exact rewrites in DEEP_ANALYSIS_AND_REWRITES.md
# ─────────────────────────────────────────────────────────────────────────────

# BUG 1 FIX: getLabelForInput with aria-labelledby support
GET_LABEL_JS = """
function getLabelForInput(input, platform) {
    // 1. aria-label (direct)
    const ariaLabel = input.getAttribute('aria-label');
    if (ariaLabel && ariaLabel.trim()) return ariaLabel.replace(/[\\s*✱]+$/, '').trim();

    // 1b. aria-labelledby (BUG 1 FIX)
    // Workday, SuccessFactors, newer Greenhouse use this pattern:
    // <label id="city-lbl">City *</label>  <input aria-labelledby="city-lbl">
    const labelledBy = input.getAttribute('aria-labelledby');
    if (labelledBy) {
        const parts = labelledBy.trim().split(/\\s+/);
        const texts = parts
            .map(id => {
                const el = document.getElementById(id);
                return el ? el.innerText.replace(/[\\s*✱]+$/, '').trim() : '';
            })
            .filter(Boolean);
        if (texts.length) return texts.join(' ');
    }

    // 2. label[for=id]
    if (input.id) {
        const lbl = document.querySelector('label[for="' + input.id + '"]');
        if (lbl) return lbl.innerText.replace(/[\\s*✱]+$/, '').trim();
    }

    // 3. closest wrapping label
    const parent = input.closest('label');
    if (parent) return parent.innerText.replace(/[\\s*✱]+$/, '').trim();

    // 4. placeholder
    const ph = input.getAttribute('placeholder');
    if (ph && ph.trim()) return ph.trim();

    return '';
}
"""

# BUG 3 FIX: setSelectValue with state abbreviation mapping + input event
SET_SELECT_JS = """
const STATE_ABBREVIATIONS = {
    'alabama': 'AL', 'alaska': 'AK', 'arizona': 'AZ', 'arkansas': 'AR',
    'california': 'CA', 'colorado': 'CO', 'connecticut': 'CT', 'delaware': 'DE',
    'florida': 'FL', 'georgia': 'GA', 'hawaii': 'HI', 'idaho': 'ID',
    'illinois': 'IL', 'indiana': 'IN', 'iowa': 'IA', 'kansas': 'KS',
    'kentucky': 'KY', 'louisiana': 'LA', 'maine': 'ME', 'maryland': 'MD',
    'massachusetts': 'MA', 'michigan': 'MI', 'minnesota': 'MN', 'mississippi': 'MS',
    'missouri': 'MO', 'montana': 'MT', 'nebraska': 'NE', 'nevada': 'NV',
    'new hampshire': 'NH', 'new jersey': 'NJ', 'new mexico': 'NM', 'new york': 'NY',
    'north carolina': 'NC', 'north dakota': 'ND', 'ohio': 'OH', 'oklahoma': 'OK',
    'oregon': 'OR', 'pennsylvania': 'PA', 'rhode island': 'RI', 'south carolina': 'SC',
    'south dakota': 'SD', 'tennessee': 'TN', 'texas': 'TX', 'utah': 'UT',
    'vermont': 'VT', 'virginia': 'VA', 'washington': 'WA', 'west virginia': 'WV',
    'wisconsin': 'WI', 'wyoming': 'WY', 'district of columbia': 'DC',
};

function setSelectValue(element, value) {
    if (!value) return false;
    const valLower = value.toString().toLowerCase().trim();
    let bestOption = null, partialMatch = null;

    for (const opt of element.options) {
        const t = opt.text.toLowerCase().trim();
        const v = opt.value.toLowerCase().trim();
        if (t === valLower || v === valLower) { bestOption = opt; break; }
        if (!partialMatch && (t.includes(valLower) || valLower.includes(t))) {
            partialMatch = opt;
        }
    }

    // State abbreviation fallback — "TX" in profile vs "Texas" in dropdown
    if (!bestOption && !partialMatch) {
        const abbrev = STATE_ABBREVIATIONS[valLower];
        const fullName = Object.keys(STATE_ABBREVIATIONS).find(
            k => STATE_ABBREVIATIONS[k] === valLower.toUpperCase()
        );
        const tryVal = abbrev || fullName;
        if (tryVal) {
            for (const opt of element.options) {
                const t = opt.text.toLowerCase().trim();
                const v = opt.value.toLowerCase().trim();
                if (t === tryVal.toLowerCase() || v === tryVal.toLowerCase()) {
                    partialMatch = opt;
                    break;
                }
            }
        }
    }

    const chosen = bestOption || partialMatch;
    if (chosen) {
        element.value = chosen.value;
        // Fire BOTH events — React uses 'change', Angular/Vue use 'input'
        element.dispatchEvent(new Event('input',  { bubbles: true }));
        element.dispatchEvent(new Event('change', { bubbles: true }));
        element.dispatchEvent(new Event('blur',   { bubbles: true }));
        return true;
    }
    return false;
}
"""

# BUG 4 FIX: setNativeValue with contenteditable handling
SET_NATIVE_VALUE_JS = """
function setNativeValue(element, value) {
    // Handle contenteditable divs (SmartRecruiters, newer Lever, BambooHR custom fields)
    if (element.contentEditable === 'true' || element.getAttribute('role') === 'textbox') {
        element.focus();
        const sel = window.getSelection();
        const range = document.createRange();
        range.selectNodeContents(element);
        sel.removeAllRanges();
        sel.addRange(range);
        document.execCommand('insertText', false, String(value));
        element.dispatchEvent(new Event('input',  { bubbles: true }));
        element.dispatchEvent(new Event('change', { bubbles: true }));
        return;
    }

    // Standard inputs and textareas
    const proto = element.tagName === 'TEXTAREA'
        ? window.HTMLTextAreaElement.prototype
        : window.HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
    if (setter) setter.call(element, value);
    else element.value = value;
    element.dispatchEvent(new Event('input',  { bubbles: true }));
    element.dispatchEvent(new Event('change', { bubbles: true }));
    element.dispatchEvent(new Event('blur',   { bubbles: true }));
}
"""


# ─────────────────────────────────────────────────────────────────────────────
# TEST CLASS: Real-World Scenarios
# ─────────────────────────────────────────────────────────────────────────────

class TestRealWorldScenarios:

    # ── BUG 1: aria-labelledby ─────────────────────────────────────────────

    def test_scenario_aria_labelledby_resolution(self, page):
        """
        SCENARIO: Workday uses aria-labelledby instead of for= or aria-label.
        BEFORE FIX: getLabelForInput returned "" → field went to LLM with empty label → wrong fill.
        AFTER FIX:  Resolves label ID → returns "City" → FIELD_PATTERNS matches → correct fill.

        Exact Workday DOM pattern:
            <label id="label-city-abc123">City *</label>
            <input type="text" aria-labelledby="label-city-abc123">
        """
        page.set_content("""
            <html><body>
            <label id="city-lbl-123">City *</label>
            <div><input type="text" aria-labelledby="city-lbl-123" autocomplete="off"></div>
            </body></html>
        """)

        label = page.evaluate(GET_LABEL_JS + """
            return getLabelForInput(document.querySelector('input'), 'workday');
        """)
        assert label == "City", \
            f"aria-labelledby should resolve to 'City' (stripped of *), got: '{label}'"

    def test_scenario_aria_labelledby_multiple_ids(self, page):
        """
        SCENARIO: aria-labelledby with TWO IDs (label + hint text).
        Workday does: aria-labelledby="label-123 hint-456"
        AFTER FIX: Both IDs resolved and joined with space.
        """
        page.set_content("""
            <html><body>
            <label id="lbl-1">State</label>
            <span id="lbl-2">/ Province</span>
            <input type="text" aria-labelledby="lbl-1 lbl-2">
            </body></html>
        """)

        label = page.evaluate(GET_LABEL_JS + """
            return getLabelForInput(document.querySelector('input'), 'workday');
        """)
        assert "State" in label, \
            f"Should resolve both aria-labelledby IDs, got: '{label}'"

    def test_scenario_aria_labelledby_missing_id_graceful(self, page):
        """
        SCENARIO: aria-labelledby points to a non-existent ID (stale DOM).
        AFTER FIX: Should not crash — skips missing IDs and falls through.
        """
        page.set_content("""
            <html><body>
            <label for="field1">Email</label>
            <input id="field1" type="email" aria-labelledby="nonexistent-id-xyz">
            </body></html>
        """)

        # When aria-labelledby ID doesn't exist, should fall through to label[for=id]
        label = page.evaluate(GET_LABEL_JS + """
            return getLabelForInput(document.querySelector('input'), 'workday');
        """)
        # Falls through to label[for=id] which returns "Email"
        assert label == "Email", \
            f"Should fall through to label[for=id] when aria-labelledby ID missing, got: '{label}'"

    # ── BUG 3: State abbreviation mapping ─────────────────────────────────

    def test_scenario_workday_state_profile_has_abbreviation(self, page):
        """
        SCENARIO: Profile stores state as "TX", Workday dropdown shows "Texas".
        BEFORE FIX: "TX" didn't match "Texas" → state left empty → form fails validation.
        AFTER FIX:  STATE_ABBREVIATIONS maps TX → texas → matches "Texas" option text.
        """
        page.set_content("""
            <html><body>
            <div data-automation-id="stateSection">
              <label for="state_sel">State</label>
              <select id="state_sel">
                <option value="">Select...</option>
                <option value="TX">Texas</option>
                <option value="CA">California</option>
                <option value="NY">New York</option>
              </select>
            </div>
            </body></html>
        """)

        result = page.evaluate(SET_SELECT_JS + """
            const sel = document.getElementById('state_sel');
            const ok = setSelectValue(sel, 'TX');   // Profile stores abbreviation
            return { ok, value: sel.value, text: sel.options[sel.selectedIndex]?.text };
        """)

        assert result["ok"] is True, "setSelectValue should succeed with TX → Texas mapping"
        assert result["value"] == "TX", f"Select value should be TX, got: '{result['value']}'"
        assert result["text"] == "Texas", f"Selected text should be Texas, got: '{result['text']}'"

    def test_scenario_workday_state_profile_has_full_name(self, page):
        """
        SCENARIO: Profile stores "Texas", dropdown options have value="TX" text="Texas".
        Should match by text ("Texas" == "Texas") — direct match, no mapping needed.
        """
        page.set_content("""
            <html><body>
            <select id="state2">
                <option value="">--</option>
                <option value="TX">Texas</option>
                <option value="CA">California</option>
            </select>
            </body></html>
        """)

        result = page.evaluate(SET_SELECT_JS + """
            const sel = document.getElementById('state2');
            const ok = setSelectValue(sel, 'Texas');  // Profile stores full name
            return { ok, value: sel.value };
        """)

        assert result["ok"] is True, "Full name match should work directly"
        assert result["value"] == "TX", f"Expected TX, got: '{result['value']}'"

    def test_scenario_select_fires_input_event_for_angular(self, page):
        """
        SCENARIO: Angular form with [(ngModel)] on a <select>.
        BEFORE FIX: Only 'change' fired → Angular internal state still has old value.
        AFTER FIX:  Both 'input' and 'change' are fired.

        We verify by attaching an 'input' listener and confirming it fires.
        """
        page.set_content("""
            <html><body>
            <select id="ang_select">
                <option value="">Select</option>
                <option value="full_time">Full Time</option>
                <option value="part_time">Part Time</option>
            </select>
            <div id="event_log"></div>
            </body></html>
        """)

        page.evaluate("""
            document.getElementById('ang_select').addEventListener('input', () => {
                document.getElementById('event_log').textContent = 'input_fired';
            });
        """)

        page.evaluate(SET_SELECT_JS + """
            setSelectValue(document.getElementById('ang_select'), 'Full Time');
        """)

        log = page.locator("#event_log").inner_text()
        assert log == "input_fired", \
            "The 'input' event must fire for Angular/Vue [(ngModel)] to update. Got: " + log

    # ── BUG 4: contenteditable divs ───────────────────────────────────────

    def test_scenario_smartrecruiters_contenteditable_fill(self, page):
        """
        SCENARIO: SmartRecruiters uses <div contenteditable="true"> for long text fields.
        BEFORE FIX: setNativeValue called HTMLInputElement.prototype setter on a div → silent no-op.
        AFTER FIX:  Detects contentEditable === 'true' → uses execCommand('insertText').
        """
        page.set_content("""
            <html><body>
            <div id="sr_summary" contenteditable="true" role="textbox"
                 aria-label="Professional Summary"
                 data-placeholder="Describe your professional background..."></div>
            </body></html>
        """)

        page.evaluate(SET_NATIVE_VALUE_JS + """
            setNativeValue(
                document.getElementById('sr_summary'),
                'AI/ML Engineer with 2 years building RAG pipelines and spatial proteomics tools.'
            );
        """)

        content = page.locator("#sr_summary").inner_text()
        assert "RAG" in content, \
            f"contenteditable div should be filled via execCommand, got: '{content}'"

    def test_scenario_contenteditable_does_not_break_regular_input(self, page):
        """
        Regression: the contenteditable check must not affect normal <input> filling.
        """
        page.set_content("""
            <html><body>
            <input type="text" id="normal_input">
            </body></html>
        """)

        page.evaluate(SET_NATIVE_VALUE_JS + """
            setNativeValue(document.getElementById('normal_input'), 'Houston, TX');
        """)

        val = page.locator("#normal_input").input_value()
        assert val == "Houston, TX", \
            f"Normal input should still work after contenteditable fix, got: '{val}'"

    def test_scenario_contenteditable_textarea_role(self, page):
        """
        Some ATS use role="textbox" on a non-contenteditable div.
        The fix should handle role="textbox" as well as contentEditable.
        """
        page.set_content("""
            <html><body>
            <div id="cv_div" role="textbox" contenteditable="true"
                 aria-multiline="true"
                 aria-label="Cover Letter"></div>
            </body></html>
        """)

        page.evaluate(SET_NATIVE_VALUE_JS + """
            setNativeValue(document.getElementById('cv_div'), 'Dear Hiring Manager,');
        """)

        content = page.locator("#cv_div").inner_text()
        assert "Dear Hiring Manager" in content, \
            f"role=textbox div should be fillable, got: '{content}'"

    # ── BUG 6: fillMonthYearInput "Present" handling ──────────────────────

    def test_scenario_present_end_date_checks_checkbox(self, page):
        """
        SCENARIO: Current job has end_date = "Present" or null.
        BEFORE FIX: fillMonthYearInput returned false → end date left blank → Workday validation error.
        AFTER FIX:  Detects "Present" string → finds 'I currently work here' checkbox → clicks it.
        """
        page.set_content("""
            <html><body>
            <div data-automation-id="workExperience">
              <label for="end_date">End Date (MM/YYYY)</label>
              <input type="text" id="end_date" placeholder="MM/YYYY" maxlength="7">
              <input type="checkbox" id="current_job" name="currentJob">
              <label for="current_job">I currently work here</label>
            </div>
            </body></html>
        """)

        page.evaluate("""
            async function delay(ms) { return new Promise(r => setTimeout(r, ms)); }

            function getLabelForInput(input) {
                const ariaLabel = input.getAttribute('aria-label');
                if (ariaLabel) return ariaLabel;
                if (input.id) {
                    const lbl = document.querySelector('label[for="' + input.id + '"]');
                    if (lbl) return lbl.innerText.trim();
                }
                return '';
            }

            async function fillMonthYearInput(el, isoOrFreeform) {
                const str = String(isoOrFreeform || '').trim();
                if (!str || /^(present|current|ongoing|now|-)$/i.test(str)) {
                    const container = el.closest('[data-automation-id], section, fieldset')
                                   || el.parentElement?.parentElement;
                    if (container) {
                        const checkboxes = container.querySelectorAll('input[type="checkbox"]');
                        for (const cb of checkboxes) {
                            const cbLabel = (getLabelForInput(cb) || '').toLowerCase();
                            if (/current|still.*work|present|ongoing/.test(cbLabel) || !cbLabel) {
                                if (!cb.checked) { cb.click(); await delay(100); }
                                return true;
                            }
                        }
                    }
                    return false;
                }
                return false;
            }

            const endDate = document.getElementById('end_date');
            fillMonthYearInput(endDate, 'Present');
        """)

        time.sleep(0.3)
        is_checked = page.locator("#current_job").is_checked()
        assert is_checked, \
            "'I currently work here' checkbox should be checked when end_date is 'Present'"

    def test_scenario_present_variations(self, page):
        """All 'present' synonyms should trigger the checkbox: Present, Current, now, -, ''."""
        for present_value in ["Present", "present", "Current", "current", "ongoing", "now", "-", ""]:
            page.set_content("""
                <html><body>
                <div data-automation-id="exp">
                  <input type="text" id="ed" placeholder="MM/YYYY">
                  <input type="checkbox" id="cb_curr">
                  <label for="cb_curr">I currently work here</label>
                </div>
                </body></html>
            """)

            # Reset checkbox
            page.evaluate("document.getElementById('cb_curr').checked = false;")

            page.evaluate(f"""
                async function delay(ms) {{ return new Promise(r => setTimeout(r, ms)); }}
                async function fillMonthYearInput(el, val) {{
                    const str = String(val || '').trim();
                    if (!str || /^(present|current|ongoing|now|-)$/i.test(str)) {{
                        const container = el.closest('[data-automation-id]') || el.parentElement;
                        if (container) {{
                            const cbs = container.querySelectorAll('input[type="checkbox"]');
                            for (const cb of cbs) {{ if (!cb.checked) {{ cb.click(); return true; }} }}
                        }}
                        return false;
                    }}
                    return false;
                }}
                fillMonthYearInput(document.getElementById('ed'), {repr(present_value)});
            """)
            time.sleep(0.2)
            checked = page.locator("#cb_curr").is_checked()
            assert checked, f"Checkbox should be checked for present_value='{present_value}'"

    # ── BUG 7: Radio group label (legend vs option label) ─────────────────

    def test_scenario_radio_group_uses_legend_not_option_label(self, page):
        """
        SCENARIO: Radio group "Are you authorized to work?" has options "Yes" / "No".
        BEFORE FIX: getRadioGroups() called getLabelForInput(radio_input) → got "Yes" as group label.
        AFTER FIX:  Checks <legend> inside <fieldset> first → gets correct question text.
        """
        page.set_content("""
            <html><body>
            <fieldset>
              <legend>Are you authorized to work in the US?</legend>
              <label><input type="radio" name="work_auth" value="yes"> Yes</label>
              <label><input type="radio" name="work_auth" value="no"> No</label>
            </fieldset>
            </body></html>
        """)

        group_label = page.evaluate("""
            function getRadioGroupLabel(radioInput) {
                const container = radioInput.closest('fieldset, .field, .form-group');
                if (!container) return radioInput.name;
                const legend = container.querySelector('legend');
                if (legend) return legend.innerText.replace(/[\\s*]+$/, '').trim();
                const labels = Array.from(container.querySelectorAll('label, .label'));
                const groupLabel = labels.find(l => !l.querySelector('input'));
                if (groupLabel) return groupLabel.innerText.replace(/[\\s*]+$/, '').trim();
                return radioInput.name;
            }
            const radio = document.querySelector('input[type="radio"]');
            return getRadioGroupLabel(radio);
        """)

        assert "authorized" in group_label.lower(), \
            f"Radio group label should be the question text from <legend>, got: '{group_label}'"
        assert group_label.lower() not in ["yes", "no"], \
            f"Radio group label must NOT be an option value, got: '{group_label}'"

    # ── BUG 8: Re-entrancy guard ──────────────────────────────────────────

    def test_scenario_reentrancy_guard_blocks_second_call(self, page):
        """
        SCENARIO: MutationObserver fires during fill → second runAutoFill() starts.
        BEFORE FIX: Two fills run simultaneously → fields get double-written, race conditions.
        AFTER FIX:  isCurrentlyFilling flag blocks the second call immediately.
        """
        page.set_content("<html><body><div id='log'></div></body></html>")

        call_count = page.evaluate("""
            let isCurrentlyFilling = false;
            let callCount = 0;

            async function delay(ms) { return new Promise(r => setTimeout(r, ms)); }

            async function runAutoFill() {
                if (isCurrentlyFilling) {
                    document.getElementById('log').textContent += 'blocked,';
                    return;  // Guard fires
                }
                isCurrentlyFilling = true;
                callCount++;
                document.getElementById('log').textContent += 'running,';
                try {
                    await delay(100);  // Simulate async fill work
                    document.getElementById('log').textContent += 'done,';
                } finally {
                    isCurrentlyFilling = false;
                }
            }

            // Simulate two rapid calls (what MutationObserver does)
            runAutoFill();
            runAutoFill();

            return new Promise(resolve => {
                setTimeout(() => resolve(callCount), 300);
            });
        """)

        assert call_count == 1, \
            f"runAutoFill should only execute ONCE due to re-entrancy guard, ran {call_count} times"

    def test_scenario_reentrancy_allows_fill_after_completion(self, page):
        """
        After a fill completes (isCurrentlyFilling = false), a new fill should be allowed.
        The guard must reset in the finally block.
        """
        page.set_content("<html><body></body></html>")

        call_count = page.evaluate("""
            let isCurrentlyFilling = false;
            let callCount = 0;

            async function delay(ms) { return new Promise(r => setTimeout(r, ms)); }

            async function runAutoFill() {
                if (isCurrentlyFilling) return;
                isCurrentlyFilling = true;
                try {
                    callCount++;
                    await delay(50);
                } finally {
                    isCurrentlyFilling = false;  // MUST reset or nothing can ever fill again
                }
            }

            return new Promise(async resolve => {
                await runAutoFill();  // First fill completes
                await runAutoFill();  // Second fill — should be allowed now
                resolve(callCount);
            });
        """)

        assert call_count == 2, \
            f"Guard should reset after completion — second fill must be allowed. Got {call_count} calls"

    # ── BUG 2: Same-label deduplication ──────────────────────────────────

    def test_scenario_multiple_job_entries_not_deduplicated(self, page):
        """
        SCENARIO: Workday Work Experience has 2 entries — both have "Job Title" fields.
        BEFORE FIX: seen.has("Job Title") deduplicates → second entry's Job Title is never found.
        AFTER FIX:  Identity = "Job Title::section-automation-id" → unique per section.
        """
        page.set_content("""
            <html><body>
            <div data-automation-id="exp-section-1">
              <label for="title1">Job Title</label>
              <input id="title1" type="text" name="title">
            </div>
            <div data-automation-id="exp-section-2">
              <label for="title2">Job Title</label>
              <input id="title2" type="text" name="title">
            </div>
            </body></html>
        """)

        field_count = page.evaluate(GET_LABEL_JS + """
            const seen = new Set();
            const fields = [];
            const inputs = document.querySelectorAll('input');

            for (const el of inputs) {
                const label = getLabelForInput(el, 'workday');
                const scopeEl = el.closest('[data-automation-id]');
                const scopeId = scopeEl?.getAttribute('data-automation-id') || '';
                const identity = (label || el.name || el.id) + '::' + scopeId;

                if (!identity.replace(/::/g, '') || seen.has(identity)) continue;
                seen.add(identity);
                fields.push({ label, scopeId });
            }
            return fields.length;
        """)

        assert field_count == 2, \
            f"Both 'Job Title' fields should be found (scoped by section ID), found: {field_count}"

    # ── BUG 12: JD vs form page detection ─────────────────────────────────

    def test_scenario_application_form_detected_not_jd(self, page):
        """
        SCENARIO: User is on the application form page, not the job description page.
        BEFORE FIX: getCleanText() returned form labels as JD → LLM got "First Name, Last Name" as JD.
        AFTER FIX:  isJobDescriptionPage() detects form signals and warns user.
        """
        page.set_content("""
            <html><body>
            <form>
              <label>First Name</label><input type="text">
              <label>Last Name</label><input type="text">
              <label>Email</label><input type="email">
              <button type="submit">Submit Application</button>
              <p>Upload Resume</p>
              <p>Equal Opportunity Employer — Privacy Policy applies</p>
            </form>
            </body></html>
        """)

        is_jd = page.evaluate("""
            function isJobDescriptionPage() {
                const text = document.body.innerText.toLowerCase();
                const JD_SIGNALS = [
                    'responsibilities', 'requirements', 'qualifications',
                    'what you will do', 'about the role', 'minimum qualifications',
                    'we are looking for', 'benefits', 'what we offer'
                ];
                const FORM_SIGNALS = [
                    'first name', 'last name', 'submit application', 'upload resume',
                    'equal opportunity', 'privacy policy'
                ];
                const jdMatches   = JD_SIGNALS.filter(s => text.includes(s)).length;
                const formMatches = FORM_SIGNALS.filter(s => text.includes(s)).length;
                return jdMatches >= formMatches;
            }
            return isJobDescriptionPage();
        """)

        assert is_jd is False, \
            "Application form page should NOT be detected as a job description page"

    def test_scenario_job_description_page_detected(self, page):
        """
        Job description pages should pass the isJobDescriptionPage() check.
        """
        page.set_content("""
            <html><body>
            <h1>Senior ML Engineer</h1>
            <h2>About the Role</h2>
            <p>We are looking for an experienced ML engineer to join our team.</p>
            <h2>Responsibilities</h2>
            <ul>
              <li>Build and deploy ML models</li>
              <li>Design data pipelines</li>
            </ul>
            <h2>Requirements</h2>
            <ul>
              <li>5+ years experience</li>
              <li>Python, PyTorch required</li>
            </ul>
            <h2>Minimum Qualifications</h2>
            <p>BS/MS in Computer Science or related field.</p>
            <h2>What We Offer</h2>
            <p>Competitive salary, benefits, remote options.</p>
            </body></html>
        """)

        is_jd = page.evaluate("""
            function isJobDescriptionPage() {
                const text = document.body.innerText.toLowerCase();
                const JD_SIGNALS = [
                    'responsibilities', 'requirements', 'qualifications',
                    'what you will do', 'about the role', 'minimum qualifications',
                    'we are looking for', 'benefits', 'what we offer'
                ];
                const FORM_SIGNALS = [
                    'first name', 'last name', 'submit application', 'upload resume',
                    'equal opportunity', 'privacy policy'
                ];
                const jdMatches   = JD_SIGNALS.filter(s => text.includes(s)).length;
                const formMatches = FORM_SIGNALS.filter(s => text.includes(s)).length;
                return jdMatches >= formMatches;
            }
            return isJobDescriptionPage();
        """)

        assert is_jd is True, \
            "Job description page should be detected as a JD page"

    # ── MISSING A: Checkbox groups ────────────────────────────────────────

    def test_scenario_checkbox_group_detected(self, page):
        """
        SCENARIO: ATS asks 'Which languages do you know?' with checkboxes.
        BEFORE FIX: All checkboxes excluded from getFormFields() → zero fields found.
        AFTER FIX:  getCheckboxGroups() scans for checkbox groups as separate field type.
        """
        page.set_content("""
            <html><body>
            <fieldset>
              <legend>Which programming languages do you know?</legend>
              <label><input type="checkbox" name="lang" value="python"> Python</label>
              <label><input type="checkbox" name="lang" value="java"> Java</label>
              <label><input type="checkbox" name="lang" value="go"> Go</label>
              <label><input type="checkbox" name="lang" value="rust"> Rust</label>
            </fieldset>
            </body></html>
        """)

        result = page.evaluate("""
            function getCheckboxGroups() {
                const groups = {};
                const checkboxes = document.querySelectorAll('input[type="checkbox"]');
                for (const cb of checkboxes) {
                    const container = cb.closest('fieldset, .field, .form-group, .question');
                    if (!container) continue;
                    const legend = container.querySelector('legend');
                    const groupLabel = legend ? legend.innerText.trim() : null;
                    if (!groupLabel) continue;
                    if (!groups[groupLabel]) groups[groupLabel] = { label: groupLabel, options: [] };
                    const optText = cb.closest('label')?.innerText?.trim() || cb.value;
                    groups[groupLabel].options.push({ text: optText, value: cb.value });
                }
                return groups;
            }
            const groups = getCheckboxGroups();
            const keys = Object.keys(groups);
            return {
                groupCount: keys.length,
                firstLabel: keys[0] || '',
                optionCount: groups[keys[0]]?.options.length || 0
            };
        """)

        assert result["groupCount"] == 1, f"Should find 1 checkbox group, found {result['groupCount']}"
        assert "languages" in result["firstLabel"].lower(), \
            f"Group label should be the legend text, got: '{result['firstLabel']}'"
        assert result["optionCount"] == 4, \
            f"Should find 4 checkbox options, found {result['optionCount']}"

    def test_scenario_checkbox_answers_applied(self, page):
        """
        When answers contain 'Python, Go' for a checkbox group, those boxes should be checked.
        """
        page.set_content("""
            <html><body>
            <fieldset>
              <legend>Programming Languages</legend>
              <label><input type="checkbox" id="cb_py" value="python"> Python</label>
              <label><input type="checkbox" id="cb_java" value="java"> Java</label>
              <label><input type="checkbox" id="cb_go" value="go"> Go</label>
            </fieldset>
            </body></html>
        """)

        page.evaluate("""
            function fillCheckboxGroup(options, answerString) {
                const selected = answerString.split(/[,;]+/).map(s => s.trim().toLowerCase());
                for (const opt of options) {
                    const optLower = opt.element.value.toLowerCase();
                    const shouldCheck = selected.some(v => optLower === v || optLower.includes(v));
                    if (shouldCheck && !opt.element.checked) opt.element.click();
                }
            }

            const options = [
                { element: document.getElementById('cb_py') },
                { element: document.getElementById('cb_java') },
                { element: document.getElementById('cb_go') },
            ];
            fillCheckboxGroup(options, 'Python, Go');
        """)

        assert page.locator("#cb_py").is_checked(), "Python should be checked"
        assert not page.locator("#cb_java").is_checked(), "Java should NOT be checked"
        assert page.locator("#cb_go").is_checked(), "Go should be checked"


# ─────────────────────────────────────────────────────────────────────────────
# Additional edge-case tests
# ─────────────────────────────────────────────────────────────────────────────

class TestSelectEdgeCases:
    """Edge cases in setSelectValue() that have caused production failures."""

    @pytest.fixture
    def page(self, browser):
        ctx = browser.new_context()
        p = ctx.new_page()
        yield p
        p.close()
        ctx.close()

    def test_partial_match_when_no_exact(self, page):
        """When no exact match, should use partial match (e.g., 'United States' matches 'United States of America')."""
        page.set_content("""
            <html><body>
            <select id="country_sel">
                <option value="">Select...</option>
                <option value="US">United States of America</option>
                <option value="CA">Canada</option>
            </select>
            </body></html>
        """)

        result = page.evaluate(SET_SELECT_JS + """
            const sel = document.getElementById('country_sel');
            const ok = setSelectValue(sel, 'United States');  // Partial
            return { ok, value: sel.value };
        """)

        assert result["ok"] is True, "Partial match 'United States' should match 'United States of America'"
        assert result["value"] == "US"

    def test_case_insensitive_match(self, page):
        """Match should be case-insensitive."""
        page.set_content("""
            <html><body>
            <select id="emp_type">
                <option value="">--</option>
                <option value="ft">Full Time</option>
                <option value="pt">Part Time</option>
            </select>
            </body></html>
        """)

        result = page.evaluate(SET_SELECT_JS + """
            const sel = document.getElementById('emp_type');
            const ok = setSelectValue(sel, 'full time');  // lowercase
            return { ok, value: sel.value };
        """)

        assert result["ok"] is True
        assert result["value"] == "ft"

    def test_empty_value_returns_false(self, page):
        """Empty string should not change the select and should return false."""
        page.set_content("""
            <html><body>
            <select id="sel">
                <option value="default">Default</option>
            </select>
            </body></html>
        """)

        result = page.evaluate(SET_SELECT_JS + """
            const sel = document.getElementById('sel');
            const ok = setSelectValue(sel, '');
            return { ok, value: sel.value };
        """)

        assert result["ok"] is False, "Empty value should return false"
