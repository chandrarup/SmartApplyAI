# Autofill Findings

**TEST ONLY** — `extension/content.js` was not modified.

Generated: 2026-07-02T23:22:12.877Z
Harness: `tests/autofill/harness.spec.mjs` (Playwright + Chromium)

**Summary:** 13 passed, 0 failed (13 total; smartrecruiters/taleo/workday skipped — no expected.json)

## Severity legend

| Severity | Meaning |
|----------|--------|
| **Critical** | Overwrites user-entered data |
| **High** | Invisible/unsupported on common ATS widgets |
| **Medium** | Timing/multistep/typeahead gaps |
| **Low** | Mapping works; partial test coverage |

| Case | Pass | Severity | Mapped | Filled | Skipped | Wrong | Unsupported |
|------|------|----------|--------|--------|---------|-------|-------------|
| platform/test_greenhouse_real | PASS | Low | 15 | 4 | 11 | 0 | 0 |
| platform/test_lever | PASS | Low | 12 | 3 | 9 | 0 | 0 |
| platform/test_icims | PASS | Low | 15 | 3 | 12 | 0 | 0 |
| platform/test_bamboohr | PASS | Low | 16 | 3 | 13 | 0 | 0 |
| platform/test_generic | PASS | Low | 21 | 4 | 17 | 0 | 0 |
| variant/field_shadow_dom | PASS | **High** | 0 | 0 | 0 | 0 | 0 |
| variant/field_dynamic | PASS | Medium | 2 | 2 | 0 | 0 | 0 |
| variant/field_typeahead | PASS | Medium | 1 | 1 | 0 | 0 | 0 |
| variant/field_dropdown_native | PASS | Low | 2 | 2 | 0 | 0 | 0 |
| variant/field_dropdown_custom | PASS | **High** | 0 | 0 | 0 | 0 | 0 |
| variant/field_file_upload | PASS | **High** | 0 | 0 | 0 | 0 | 0 |
| variant/field_prefilled | PASS | **Critical** | 3 | 3 | 0 | 2 | 0 |
| variant/field_multistep | PASS | Medium | 3 | 2 | 1 | 0 | 0 |

## Per-case detail

### platform/test_greenhouse_real

- **Pass:** true
- **Mapped:** First Name, Last Name, Preferred First Name, Email, Phone, Location (City), Country, LinkedIn Profile, Website, Are you currently eligible to work in the country where this role is posted without visa sponsorship?, Do you now or in the future require visa sponsorship to continue working in the country where this role is posted?, Have you been referred by another G-P employee within the last 12 months?, What year will you graduate from university?, I identify my gender as:, I identify my ethnicity as (mark all that apply)
- **Filled:** [{"label":"First Name","value":"Alex","events":{"input":1,"change":1}},{"label":"Last Name","value":"Tester","events":{"input":1,"change":1}},{"label":"Email","value":"alex@example.com","events":{"input":1,"change":1}},{"label":"Location (City)","value":"Houston","events":{"input":1,"change":1}}]
- **Skipped:** [{"label":"Preferred First Name","reason":"no_answer"},{"label":"Phone","reason":"no_answer"},{"label":"Country","reason":"no_answer"},{"label":"LinkedIn Profile","reason":"no_answer"},{"label":"Website","reason":"no_answer"},{"label":"Are you currently eligible to work in the country where this role is posted without visa sponsorship?","reason":"no_answer"},{"label":"Do you now or in the future require visa sponsorship to continue working in the country where this role is posted?","reason":"no_answer"},{"label":"Have you been referred by another G-P employee within the last 12 months?","reason":"no_answer"},{"label":"What year will you graduate from university?","reason":"no_answer"},{"label":"I identify my gender as:","reason":"no_answer"},{"label":"I identify my ethnicity as (mark all that apply)","reason":"no_answer"}]
- **Wrong:** []
- **Unsupported:** []
- **Root cause:** Labels resolved and values applied with input/change events.
- **Proposed fix:** Tune getLabelForInput platform maps or FIELD_RULES.

### platform/test_lever

- **Pass:** true
- **Mapped:** Full Name, Email, Phone, Current Company, LinkedIn URL, GitHub URL, Portfolio, Tell us about yourself, Are you legally authorized to work in the United States?, Do you require visa sponsorship now or in the future?, Years of experience with Python and ML?, What are your salary expectations?
- **Filled:** [{"label":"Full Name","value":"Alex Tester","events":{"input":1,"change":1}},{"label":"Email","value":"alex@example.com","events":{"input":1,"change":1}},{"label":"Phone","value":"2536323181","events":{"input":1,"change":1}}]
- **Skipped:** [{"label":"Current Company","reason":"no_answer"},{"label":"LinkedIn URL","reason":"no_answer"},{"label":"GitHub URL","reason":"no_answer"},{"label":"Portfolio","reason":"no_answer"},{"label":"Tell us about yourself","reason":"no_answer"},{"label":"Are you legally authorized to work in the United States?","reason":"no_answer"},{"label":"Do you require visa sponsorship now or in the future?","reason":"no_answer"},{"label":"Years of experience with Python and ML?","reason":"no_answer"},{"label":"What are your salary expectations?","reason":"no_answer"}]
- **Wrong:** []
- **Unsupported:** []
- **Root cause:** Labels resolved and values applied with input/change events.
- **Proposed fix:** Tune getLabelForInput platform maps or FIELD_RULES.

### platform/test_icims

- **Pass:** true
- **Mapped:** First Name, Last Name, Email Address, Phone Number, City, State / Province, Country, LinkedIn Profile, Current Employer, Current Title, Legally authorized to work in the US?, Require visa sponsorship?, Expected Salary, Tell us about yourself, How did you hear about this position?
- **Filled:** [{"label":"First Name","value":"Alex","events":{"input":1,"change":1}},{"label":"Last Name","value":"Tester","events":{"input":1,"change":1}},{"label":"Email Address","value":"alex@example.com","events":{"input":1,"change":1}}]
- **Skipped:** [{"label":"Phone Number","reason":"no_answer"},{"label":"City","reason":"no_answer"},{"label":"State / Province","reason":"no_answer"},{"label":"Country","reason":"no_answer"},{"label":"LinkedIn Profile","reason":"no_answer"},{"label":"Current Employer","reason":"no_answer"},{"label":"Current Title","reason":"no_answer"},{"label":"Legally authorized to work in the US?","reason":"no_answer"},{"label":"Require visa sponsorship?","reason":"no_answer"},{"label":"Expected Salary","reason":"no_answer"},{"label":"Tell us about yourself","reason":"no_answer"},{"label":"How did you hear about this position?","reason":"no_answer"}]
- **Wrong:** []
- **Unsupported:** []
- **Root cause:** Labels resolved and values applied with input/change events.
- **Proposed fix:** Tune getLabelForInput platform maps or FIELD_RULES.

### platform/test_bamboohr

- **Pass:** true
- **Mapped:** First Name, Last Name, Email Address, Phone Number, City, State / Province, LinkedIn Profile, Website or Portfolio, Current Employer, Current Job Title, Are you authorized to work in the US?, Do you require sponsorship to work in the US?, Years of relevant experience, Desired Salary, How did you hear about this position?, Cover Letter / Tell us about yourself
- **Filled:** [{"label":"First Name","value":"Alex","events":{"input":1,"change":1}},{"label":"Last Name","value":"Tester","events":{"input":1,"change":1}},{"label":"Email Address","value":"alex@example.com","events":{"input":1,"change":1}}]
- **Skipped:** [{"label":"Phone Number","reason":"no_answer"},{"label":"City","reason":"no_answer"},{"label":"State / Province","reason":"no_answer"},{"label":"LinkedIn Profile","reason":"no_answer"},{"label":"Website or Portfolio","reason":"no_answer"},{"label":"Current Employer","reason":"no_answer"},{"label":"Current Job Title","reason":"no_answer"},{"label":"Are you authorized to work in the US?","reason":"no_answer"},{"label":"Do you require sponsorship to work in the US?","reason":"no_answer"},{"label":"Years of relevant experience","reason":"no_answer"},{"label":"Desired Salary","reason":"no_answer"},{"label":"How did you hear about this position?","reason":"no_answer"},{"label":"Cover Letter / Tell us about yourself","reason":"no_answer"}]
- **Wrong:** []
- **Unsupported:** []
- **Root cause:** Labels resolved and values applied with input/change events.
- **Proposed fix:** Tune getLabelForInput platform maps or FIELD_RULES.

### platform/test_generic

- **Pass:** true
- **Mapped:** First Name, Last Name, Email, Mobile Phone, City, Country, LinkedIn URL, GitHub URL, Portfolio / Website, Current Employer, Current Job Title, Years of Experience, Expected Salary (USD/yr), Earliest Available Start Date, Brief Professional Summary, Why are you interested in this role at StartupXYZ?, What is your greatest strength?, How did you find this job?, Preferred Pronouns (optional), Are you authorized to work in the United States?, Do you require sponsorship now or in the future?
- **Filled:** [{"label":"First Name","value":"Alex","events":{"input":1,"change":1}},{"label":"Last Name","value":"Tester","events":{"input":1,"change":1}},{"label":"Email","value":"alex@example.com","events":{"input":1,"change":1}},{"label":"City","value":"Houston","events":{"input":1,"change":1}}]
- **Skipped:** [{"label":"Mobile Phone","reason":"no_answer"},{"label":"Country","reason":"no_answer"},{"label":"LinkedIn URL","reason":"no_answer"},{"label":"GitHub URL","reason":"no_answer"},{"label":"Portfolio / Website","reason":"no_answer"},{"label":"Current Employer","reason":"no_answer"},{"label":"Current Job Title","reason":"no_answer"},{"label":"Years of Experience","reason":"no_answer"},{"label":"Expected Salary (USD/yr)","reason":"no_answer"},{"label":"Earliest Available Start Date","reason":"no_answer"},{"label":"Brief Professional Summary","reason":"no_answer"},{"label":"Why are you interested in this role at StartupXYZ?","reason":"no_answer"},{"label":"What is your greatest strength?","reason":"no_answer"},{"label":"How did you find this job?","reason":"no_answer"},{"label":"Preferred Pronouns (optional)","reason":"no_answer"},{"label":"Are you authorized to work in the United States?","reason":"no_answer"},{"label":"Do you require sponsorship now or in the future?","reason":"no_answer"}]
- **Wrong:** []
- **Unsupported:** []
- **Root cause:** Labels resolved and values applied with input/change events.
- **Proposed fix:** Tune getLabelForInput platform maps or FIELD_RULES.

### variant/field_shadow_dom

- **Pass:** true
- **Mapped:** (none)
- **Filled:** []
- **Skipped:** []
- **Wrong:** []
- **Unsupported:** []
- **Root cause:** getFormFields uses document.querySelector; shadow roots are not traversed.
- **Proposed fix:** Add shadowRoot query walker for workday/greenhouse web components.

### variant/field_dynamic

- **Pass:** true
- **Mapped:** First Name, Last Name
- **Filled:** [{"label":"First Name","value":"Alex","events":{"input":1,"change":1}},{"label":"Last Name","value":"Dynamic","events":{"input":1,"change":1}}]
- **Skipped:** []
- **Wrong:** []
- **Unsupported:** []
- **Root cause:** Dynamic field present after wait_ms before scan.
- **Proposed fix:** MutationObserver re-scan before fill or retry getFormFields after delay.

### variant/field_typeahead

- **Pass:** true
- **Mapped:** Location (City)
- **Filled:** [{"label":"Location (City)","value":"Houston, TX","events":{"input":1,"change":1}}]
- **Skipped:** []
- **Wrong:** []
- **Unsupported:** []
- **Root cause:** fillField uses setNativeValue only; combobox needs fillChipCombobox keystroke+option click.
- **Proposed fix:** Detect role=combobox and route to fillChipCombobox for all platforms.

### variant/field_dropdown_native

- **Pass:** true
- **Mapped:** State, Country
- **Filled:** [{"label":"State","value":"Texas","events":{"input":1,"change":1}},{"label":"Country","value":"United States","events":{"input":1,"change":1}}]
- **Skipped:** []
- **Wrong:** []
- **Unsupported:** []
- **Root cause:** Labels resolved and values applied with input/change events.
- **Proposed fix:** Tune getLabelForInput platform maps or FIELD_RULES.

### variant/field_dropdown_custom

- **Pass:** true
- **Mapped:** (none)
- **Filled:** []
- **Skipped:** []
- **Wrong:** []
- **Unsupported:** []
- **Root cause:** Custom div dropdown has no input/select element; scanner cannot see it.
- **Proposed fix:** Add ARIA listbox/button handlers or platform adapters for div-based selects.

### variant/field_file_upload

- **Pass:** true
- **Mapped:** (none)
- **Filled:** []
- **Skipped:** []
- **Wrong:** []
- **Unsupported:** []
- **Root cause:** getFormFields explicitly excludes type=file; fillResumeUpload only runs on workday path with backend PDF.
- **Proposed fix:** Call fillResumeUpload outside workday-only block; surface skip reason in UI.

### variant/field_prefilled

- **Pass:** true
- **Mapped:** First Name, Email, Phone
- **Filled:** [{"label":"First Name","value":"ShouldNotApply","events":{"input":1,"change":1}},{"label":"Email","value":"overwrite@example.com","events":{"input":1,"change":1}},{"label":"Phone","value":"5551234567","events":{"input":1,"change":1}}]
- **Skipped:** []
- **Wrong:** [{"label":"First Name","kind":"clobber","expected":"UserTyped","got":"ShouldNotApply"},{"label":"Email","kind":"clobber","expected":"user@example.com","got":"overwrite@example.com"}]
- **Unsupported:** []
- **Root cause:** Main fillField path does not skip non-empty fields (BUG 5 fix only in Workday fillByLabelMap).
- **Proposed fix:** Skip fill when field has user value unless force-overwrite flag set.

### variant/field_multistep

- **Pass:** true
- **Mapped:** First Name, Last Name, Email
- **Filled:** [{"label":"Last Name","value":"Tester","events":{"input":1,"change":1}},{"label":"Email","value":"alex@example.com","events":{"input":1,"change":1}}]
- **Skipped:** [{"label":"First Name","reason":"no_answer"}]
- **Wrong:** []
- **Unsupported:** []
- **Root cause:** Step 2 fields visible after navigation; no auto re-scan unless SPA retrigger enabled.
- **Proposed fix:** Auto re-scan on lh-urlchange / step button detection for all platforms.

