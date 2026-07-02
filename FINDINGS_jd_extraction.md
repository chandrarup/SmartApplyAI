# JD Extraction Findings

Generated: 2026-06-30T04:19:04.179Z

**Summary:** 9 passed, 12 failed (21 total)

## lazy_loaded.html timing

- Immediate length: 10
- After 1200ms wait length: 56
- Result changed with wait: **true**

| Fixture | Pass | Platform | Len | Notes |
|---------|------|----------|-----|-------|
| happy/test_bamboohr.html | FAIL | bamboohr | 391 | happy/test_bamboohr.html: missing must_include "BambooHR" |
| happy/test_generic.html | FAIL | generic | 165 | happy/test_generic.html: missing must_include "Generic" |
| happy/test_greenhouse.html | FAIL | greenhouse | 386 | happy/test_greenhouse.html: missing must_include "Software Engineer" |
| happy/test_greenhouse_real.html | FAIL | greenhouse | 9 | happy/test_greenhouse_real.html: missing must_include "Software Engineer" |
| happy/test_icims.html | FAIL | icims | 146 | happy/test_icims.html: missing must_include "iCIMS" |
| happy/test_lever.html | FAIL | lever | 349 | happy/test_lever.html: missing must_include "Senior AI Engineer" |
| happy/test_linkedin.html | PASS | linkedin | 511 |  |
| happy/test_smartrecruiters.html | FAIL | smartrecruiters | 223 | happy/test_smartrecruiters.html: missing must_include "SmartRecruiters" |
| happy/test_taleo.html | FAIL | taleo | 320 | happy/test_taleo.html: missing must_include "Taleo" |
| happy/test_workday.html | PASS | workday | 1025 |  |
| adversarial/entities_unicode.html | PASS | generic | 70 |  |
| adversarial/huge_jd.html | FAIL | generic | 97523 | adversarial/huge_jd.html: length 97523 > max_len 40000 |
| adversarial/jd_collapsed_readmore.html | PASS | generic | 115 |  |
| adversarial/jd_in_iframe.html | FAIL | generic | 0 | adversarial/jd_in_iframe.html: missing must_include "PyTorch" |
| adversarial/jd_in_shadow_dom.html | FAIL | generic | 0 | adversarial/jd_in_shadow_dom.html: missing must_include "PyTorch" |
| adversarial/junk_heavy.html | PASS | generic | 161 |  |
| adversarial/lazy_loaded.html | PASS | generic | 56 |  |
| adversarial/login_wall.html | PASS | generic | 62 |  |
| adversarial/multi_posting.html | PASS | generic | 97 |  |
| adversarial/no_semantic_main.html | PASS | generic | 71 |  |
| adversarial/pdf_embedded.html | FAIL | generic | 11 | adversarial/pdf_embedded.html: missing must_include "PyTorch" |

## Failure root causes & proposed fixes

### happy/test_bamboohr.html

- **Errors:** happy/test_bamboohr.html: missing must_include "BambooHR"
- **Preview:** `Application Questions Are you authorized to work in the US? Select... Yes No Do you require sponsorship to work in the U`
- **Root cause:** Fixture is autofill-form-heavy with little JD body; getCleanText returns form labels/header, not skill-rich JD.
- **Proposed fix:** Add platform JD container sections to test fixtures or use real board HTML snapshots.

### happy/test_generic.html

- **Errors:** happy/test_generic.html: missing must_include "Generic"
- **Preview:** `How did you find this job? Select... LinkedIn Indeed Friend / Referral Company Website Other Preferred Pronouns (optiona`
- **Root cause:** Fixture is autofill-form-heavy with little JD body; getCleanText returns form labels/header, not skill-rich JD.
- **Proposed fix:** Add platform JD container sections to test fixtures or use real board HTML snapshots.

### happy/test_greenhouse.html

- **Errors:** happy/test_greenhouse.html: missing must_include "Software Engineer"; happy/test_greenhouse.html: missing must_include "AI Platform"
- **Preview:** `Application Questions Tell us about yourself Are you legally authorized to work in the United States? * -- Select -- Yes`
- **Root cause:** Fixture is autofill-form-heavy with little JD body; getCleanText returns form labels/header, not skill-rich JD.
- **Proposed fix:** Add platform JD container sections to test fixtures or use real board HTML snapshots.

### happy/test_greenhouse_real.html

- **Errors:** happy/test_greenhouse_real.html: missing must_include "Software Engineer"; happy/test_greenhouse_real.html: length 9 < min_len 80
- **Preview:** `not found`
- **Root cause:** Fixture is autofill-form-heavy with little JD body; getCleanText returns form labels/header, not skill-rich JD.
- **Proposed fix:** Add platform JD container sections to test fixtures or use real board HTML snapshots.

### happy/test_icims.html

- **Errors:** happy/test_icims.html: missing must_include "iCIMS"
- **Preview:** `Personal Information First Name * Last Name * Email Address * Phone Number City State / Province Country * -- Select -- `
- **Root cause:** Fixture is autofill-form-heavy with little JD body; getCleanText returns form labels/header, not skill-rich JD.
- **Proposed fix:** Add platform JD container sections to test fixtures or use real board HTML snapshots.

### happy/test_lever.html

- **Errors:** happy/test_lever.html: missing must_include "Senior AI Engineer"; happy/test_lever.html: missing must_include "Acme Corp"
- **Preview:** `Additional Information Additional Information / Cover Letter Are you legally authorized to work in the United States? * `
- **Root cause:** Fixture is autofill-form-heavy with little JD body; getCleanText returns form labels/header, not skill-rich JD.
- **Proposed fix:** Add platform JD container sections to test fixtures or use real board HTML snapshots.

### happy/test_smartrecruiters.html

- **Errors:** happy/test_smartrecruiters.html: missing must_include "SmartRecruiters"
- **Preview:** `Eligibility Are you legally authorized to work in the United States? * Select... Yes No Will you require employer sponso`
- **Root cause:** Fixture is autofill-form-heavy with little JD body; getCleanText returns form labels/header, not skill-rich JD.
- **Proposed fix:** Add platform JD container sections to test fixtures or use real board HTML snapshots.

### happy/test_taleo.html

- **Errors:** happy/test_taleo.html: missing must_include "Taleo"
- **Preview:** `Eligibility & EEO Authorized to work in US? * -- Select -- Yes No Require visa sponsorship? * -- Select -- Yes No Gender`
- **Root cause:** Fixture is autofill-form-heavy with little JD body; getCleanText returns form labels/header, not skill-rich JD.
- **Proposed fix:** Add platform JD container sections to test fixtures or use real board HTML snapshots.

### adversarial/huge_jd.html

- **Errors:** adversarial/huge_jd.html: length 97523 > max_len 40000
- **Preview:** `PyTorch retrieval-augmented generation PyTorch retrieval-augmented generation PyTorch retrieval-augmented generation PyT`
- **Root cause:** Length cap or noise filter may truncate very large JD text.
- **Proposed fix:** Tune JD_SELECTORS or add adapter for this page shape.

### adversarial/jd_in_iframe.html

- **Errors:** adversarial/jd_in_iframe.html: missing must_include "PyTorch"; adversarial/jd_in_iframe.html: missing must_include "retrieval-augmented generation"; adversarial/jd_in_iframe.html: length 0 < min_len 40
- **Preview:** ``
- **Root cause:** Cross-origin or stripped iframe: clone path removes iframe nodes before fallback; same-origin srcdoc may work but path-based test may still fail.
- **Proposed fix:** Keep same-origin iframe pass; for cross-origin, use adapter API fetch instead of DOM.

### adversarial/jd_in_shadow_dom.html

- **Errors:** adversarial/jd_in_shadow_dom.html: missing must_include "PyTorch"; adversarial/jd_in_shadow_dom.html: missing must_include "retrieval-augmented generation"; adversarial/jd_in_shadow_dom.html: length 0 < min_len 40
- **Preview:** ``
- **Root cause:** Shadow root content is not traversed by querySelector or cloneNode on host element.
- **Proposed fix:** Add shadow-root walker (element.shadowRoot) before selector pass.

### adversarial/pdf_embedded.html

- **Errors:** adversarial/pdf_embedded.html: missing must_include "PyTorch"
- **Preview:** `PDF only JD`
- **Root cause:** PDF embed/object content is not parsed; no text extraction from binary PDF.
- **Proposed fix:** Skip PDF embeds; link to download or OCR pipeline.

