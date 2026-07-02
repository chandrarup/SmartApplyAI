# JD Extraction Findings

Generated: 2026-07-02T13:26:04.559Z

**Summary:** 22 passed, 0 failed (22 total)

## lazy_loaded.html timing

- Immediate length: 10
- After 1200ms wait length: 56
- Result changed with wait: **true**

| Fixture | Pass | Platform | Len | Notes |
|---------|------|----------|-----|-------|
| happy/test_bamboohr.html | PASS | bamboohr | 358 |  |
| happy/test_generic.html | PASS | generic | 384 |  |
| happy/test_greenhouse.html | PASS | greenhouse | 400 |  |
| happy/test_greenhouse_real.html | PASS | greenhouse | 362 |  |
| happy/test_icims.html | PASS | icims | 383 |  |
| happy/test_lever.html | PASS | lever | 395 |  |
| happy/test_linkedin.html | PASS | linkedin | 86 |  |
| happy/test_smartrecruiters.html | PASS | smartrecruiters | 357 |  |
| happy/test_taleo.html | PASS | taleo | 374 |  |
| happy/test_workday.html | PASS | workday | 390 |  |
| adversarial/entities_unicode.html | PASS | generic | 70 |  |
| adversarial/huge_jd.html | PASS | generic | 39993 |  |
| adversarial/jd_collapsed_readmore.html | PASS | generic | 115 |  |
| adversarial/jd_in_iframe.html | PASS | generic | 100 |  |
| adversarial/jd_in_iframe_crossorigin.html | PASS | generic | 153 |  |
| adversarial/jd_in_shadow_dom.html | PASS | generic | 75 |  |
| adversarial/junk_heavy.html | PASS | generic | 161 |  |
| adversarial/lazy_loaded.html | PASS | generic | 56 |  |
| adversarial/login_wall.html | PASS | generic | 62 |  |
| adversarial/multi_posting.html | PASS | generic | 97 |  |
| adversarial/no_semantic_main.html | PASS | generic | 71 |  |
| adversarial/pdf_embedded.html | PASS | generic | 96 |  |

## Failure root causes & proposed fixes

