# Tailoring / PDF Pipeline Findings

**Date:** 2026-06-29  
**Scope:** TEST ONLY — failure-class characterization for `/analyze`, `/tailor-resume`, `/generate-pdf`, `clean_json()`, `escape_latex_chars()`.  
**Harness:** `tests/unit/test_tailoring_failures.py` (35 tests, all passing).  
**No production fixes applied.**

---

## Summary

| # | Failure class | Severity | Status |
|---|---------------|----------|--------|
| 1 | LaTeX injection / compile breakage | **Critical** | Compiles with command injection; partial escaper |
| 2 | Malformed LLM JSON (`clean_json`) | **Medium** | Fails loudly on bad JSON; root-array quirk |
| 3 | Evidence rule (H3) | **Low** (mitigated) | `needs_your_call` blocks auto-accept |
| 4 | Project selection | **Low** | ≤3, no duplicates; zero-match still returns top-3 |
| 5 | Length / overflow | **Medium** | Preflight + trim exist; PDF may still warn on balance |
| 6 | Diff integrity (accept/reject) | **Low** (mitigated) | Accepted-only merge works; underscore escaped in TeX |
| 7 | Garbage-in JD | **Low** | No crashes on empty / non-English / privacy-policy JD |

---

## 1. LaTeX injection / compile breakage — **Critical**

### What we tested
Injected every common LaTeX metacharacter plus unicode, emoji, em-dash, and `https://github.com/foo/bar_baz` into summary, experience bullets, project titles, and project URLs. Ran `_render_tex_from_master()` and `compile_loop.compile_with_retry()` (pdflatex present). Exercised `POST /generate-pdf` with hostile `tailored_summary`.

### Findings

| Input | `escape_latex_chars` | Compile result |
|-------|----------------------|----------------|
| `% $ & # _ { }` | Escaped to `\%`, `\$`, `\&`, `\#`, `\_`, `\{`, `\}` | OK |
| `\textbf{INJECT}` | **Not escaped** — passes through as live LaTeX | OK (renders bold injection) |
| `^` `~` | **Not escaped** | OK (caret can trigger `extra_brace` warnings) |
| Emoji / em-dash | Passed through | OK |
| `proj.url` with underscores | **URL not piped through `\|latex` filter** in `resume_template.tex` (`\href{\VAR{proj.url}}`) | OK in tests; fragile for `_` |

### Silent / dangerous behaviors
- **`escape_latex_chars()` is incomplete** — only 7 characters; no `\`, `^`, `~`, nor `\href` argument escaping.
- **LaTeX command injection succeeds**: `\textbf{INJECT}` in summary compiles and renders.
- **`/generate-pdf` logs brace imbalance** (`{=196 vs }=193`) when hostile `{`/`}` present, but **still compiles** (HTTP 200, 1 page, ATS check passed).
- Hygiene check (`inspect_tex_hygiene`) does **not** catch injection or imbalance.

### Severity rationale
User-facing PDF output can be structurally altered or broken without a hard failure. This is the #1 silent PDF failure class called out in the test plan — partially mitigated for `%$&#_{}` but not for backslash commands.

---

## 2. Malformed LLM JSON (`clean_json`) — **Medium**

### Recovers correctly
- ` ```json\n{...}\n``` ` fenced blocks
- Prose before/after JSON
- Trailing prose after valid object (`{"a":1} garbage`)
- Nested objects inside fences

### Fails loudly (caller `json.loads` raises)
- Trailing commas: `{"a":1,}`
- Truncated JSON: `{"a":`
- Single-quoted keys: `{'a':1}`
- Pure prose / empty string

No half-parsed dict was observed for truncated input.

### Quirk — root JSON arrays
- Input `[{"x":1}]` → `clean_json` returns `{"x":1}` (inner object), not the array.
- **Cause:** depth scanner prefers first `{` over `[`.
- **Impact:** Endpoints expecting a JSON **array** (e.g. `/suggest-questions`) may mis-parse list responses wrapped as `[{...}]` if that shape were ever returned.

### Severity rationale
Bad LLM output generally surfaces as HTTP 500 on `json.loads` — acceptable. The array/object ambiguity is a latent parser bug for list-shaped responses.

---

## 3. Evidence rule — **Low (mitigated)**

### Test
JD demands **Kubernetes** and **CobaltDB** (absent from `master_data.json`). Mock LLM injects both into summary and experience bullets. `knowledge_semantic.search` mocked to return no evidence.

### Result
- All edits with fabricated JD terms → `status: needs_your_call`
- **Zero** edits with `status: accepted` containing `kubernetes` or `cobaltdb`
- `tailor_edits.ground_edit()` enforces H3 as designed

### Residual risk
Fabricated text still appears in `_edits[].after` until user rejects — not silently merged into accepted PDF path.

---

## 4. Project selection — **Low**

### Tests
- **Zero JD overlap** (`quantum florbnicate…`) → still returns up to 3 projects (highest-scoring by token overlap, score may be 0).
- **High overlap** (all tech tokens in JD) → ≤3 projects, **no duplicate titles**.
- **`/tailor-resume`** `selected_projects` length ≤3, unique (case-insensitive).

### Finding
When JD matches nothing, system does **not** return 0 projects — it fills slots from library rank order. This is deterministic but may surface irrelevant projects for garbage/wrong-page JDs (ties to §7).

---

## 5. Length / overflow — **Medium**

### Controls present
- `preflight_tailored_resume()` → **fatal** when summary >85 words (~120-word test flagged).
- `_trim_skills_lists()` drops lowest-priority skills above `max_per_category` (test: 8 cap, extras in `removed`).
- `compile_loop._trim_to_fit_one_page()` runs during PDF compile (up to 12 attempts).

### Gaps
- Preflight is **warn-only** for 76–85 words; PDF compile proceeds.
- Hostile metacharacters can cause **brace imbalance warnings** without blocking compile.
- No automated test here proved 2-page PDF for extreme bullet growth (compile loop reported 1 page in injection runs).

---

## 6. Diff integrity — **Low (mitigated)**

### Tests
- Summary: one `accepted` + one `rejected` edit for same field → merged summary = accepted text only; rejected absent from rendered TeX.
- Experience bullet: `rejected` edit → original bullet preserved in merge.

### `_filter_payload_to_accepted` + `_merge_tailored_into_master`
Work correctly when `_edits` present and `enforce_accept_only` is true.

### Note for reviewers
Rendered TeX escapes underscores (`ACCEPTED_SUMMARY_MARKER` → `ACCEPTED\_SUMMARY\_MARKER`). Assertions must account for LaTeX escaping when grepping `.tex` output.

### Latent risk
Duplicate `field` keys in `_edits` with **multiple `accepted`** rows — dict comprehension keeps last wins; UI should enforce one edit per field.

---

## 7. Garbage-in JD — **Low**

| JD type | `/analyze` | `/tailor-resume` |
|---------|------------|------------------|
| Empty | 200 | 200 |
| Whitespace | 200 | 200 |
| Non-English (中文 JD) | 200 | 200 |
| Privacy policy text | 200 | 200 |

No HTTP 500. Mocked LLM returns fallback shapes. Real LLM could still produce nonsense scores or irrelevant project picks (see §4).

---

## Utility reference

### `escape_latex_chars` (`main.py:678`)
```python
chars = {"&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}"}
```
Missing: `\`, `^`, `~`, and URL-safe escaping for `\href{...}`.

### `clean_json` (`main.py:492`)
Fence → balanced-brace scan → return raw string (caller must `json.loads`).

### JD slice caps (related)
`/analyze` 7000 · `/analyze-deep` 6000 · `/tailor-resume` 3500 · `/cover-letter` 3000 · `/answer-question` 2000 · `/autofill` 1200.

---

## How to run

```bash
cd /Users/chandrarupdaka/Documents/Personal/SmartApplyAI
backend/.venv/bin/python -m pytest tests/unit/test_tailoring_failures.py -v
```

PDF compile tests require `pdflatex` on PATH (TeX Live 2025 verified locally).

---

## Files added

| File | Purpose |
|------|---------|
| `tests/unit/test_tailoring_failures.py` | Seven failure-class pytest suite |
| `FINDINGS_tailoring.md` | This report |
