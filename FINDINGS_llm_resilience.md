# LLM Provider Resilience Findings

**Date:** 2026-06-29  
**Scope:** TEST ONLY — `call_llm`, `call_ollama`, `call_claude` in `backend/main.py`  
**Harness:** `tests/unit/test_llm_resilience.py` (14 tests, all passing, providers mocked)  
**No fixes applied.**

---

## Executive summary

| Area | Verdict |
|------|---------|
| Ollama → Claude fallback | **Works** — `call_llm` tries next provider on any exception |
| 600s timeout forwarding | **Works** — `call_ollama` passes `timeout` to `requests.post` |
| Missing `ANTHROPIC_API_KEY` + `prefer="claude"` | **Works** — falls back to Ollama; raises only if both fail |
| Both providers down | **Partial** — clean `RuntimeError` message; `/analyze` still HTTP **500** |
| Empty LLM response | **Partial** — `/autofill` degrades; `/analyze` returns 500 JSON parse error |
| Autofill 5500-char budget | **Mostly OK** — contact + primary experience survive; tail projects/exp cut; `autofill` block is separate |
| Determinism | **Unstable** — same JD can yield different score/projects (informational) |

---

## Test results

```
14 passed in ~3s
backend/.venv/bin/python -m pytest tests/unit/test_llm_resilience.py -v
```

---

## 1. Ollama unreachable → Claude fallback — **PASS**

**Behavior:** `call_ollama` raises → `call_llm` logs warning → `call_claude` returns result.

**Verified:** Provider call order `["ollama", "claude"]` when `prefer="ollama"`.

---

## 2. Both providers down — **Medium severity gap**

**`call_llm`:** Raises `RuntimeError("All LLM providers failed. Last error: …")` — no stack in exception message itself.

| Endpoint | HTTP | User-visible detail |
|----------|------|---------------------|
| `POST /analyze` | **500** | `detail` string contains `"All LLM providers failed"` — **no Traceback** in JSON |
| `POST /autofill` | **200** | Returns rule-based `base_answers` only; LLM fields omitted |

**Gap:** `/analyze` is not a graceful 503/424 with structured `{error, hint}` — still a hard 500. `/autofill` is the better degradation pattern.

---

## 3. Ollama 600s timeout ceiling — **PASS**

**Behavior:** `call_ollama(..., timeout=600)` forwards `timeout=600` to `http_requests.post`. On `TimeoutError`, fallback to Claude fires.

**Note:** Ceiling is configurable per `call_llm(..., timeout=600)` call site; autofill and analyze both pass 600.

---

## 4. `ANTHROPIC_API_KEY` missing + `prefer="claude"` — **PASS**

**Behavior:**
1. `call_claude` → `RuntimeError("ANTHROPIC_API_KEY is not set")`
2. `call_llm` catches → tries Ollama
3. If Ollama also fails → `RuntimeError("All LLM providers failed…")`

No crash / uncaught exception in unit tests.

---

## 5. Empty string / `None` from provider — **Partial**

| Path | Result |
|------|--------|
| `clean_json("")` | Returns `""` |
| `json.loads(clean_json(""))` | **JSONDecodeError** |
| `/analyze` | HTTP 500, `detail` ≈ parse error string (no Traceback) |
| `/autofill` | Caught; returns rule-based answers; custom LLM fields skipped |

**Gap:** No explicit “empty model response” handling before `json.loads` on analyze/suggest-questions endpoints.

---

## 6. Autofill token budget (`[:5500]`) — **Low–Medium**

**Implementation** (`main.py` autofill LLM prompt):

```text
CANDIDATE PROFILE: {json.dumps(user_data, indent=2)[:5500]}
AUTOFILL QUICK REFERENCE: {json.dumps(autofill, indent=2)}   # NOT truncated
```

**Not used:** `_build_compact_profile()` exists but is **not** called on this path.

### Bloated profile experiment (80 padding projects + 15 filler experience entries)

| Field | In first 5500 chars? |
|-------|----------------------|
| `contact_info.email`, `phone` | **Yes** |
| Primary experience (Accenture) | **Yes** |
| Padding projects (e.g. `P79`) | **No** (tail cut) |
| Filler experience (`Filler Corp 14`) | **No** |
| `autofill.work_authorization` | **Yes** (separate `AUTOFILL QUICK REFERENCE` block) |

**Risk:** If JSON serialization order changes or contact block grows (very long summary + huge skills), `[:5500]` could cut **mid-JSON** → invalid fragment in prompt. `_build_compact_profile` would be safer but is unused here.

**Severity:** Low today for default profile; **Medium** for bloated profiles or future schema growth.

---

## 7. Determinism (`/analyze` twice) — **Informational**

With mocked drifting responses (`score` 75 → 88, different `selected_projects`):

- **Score delta:** 13 points on identical JD input
- **Projects:** changed between runs

Live Ollama (`qwen2.5-coder:7b`, `temperature=0.2`) will show similar variance. No server-side caching or seed pinning.

---

## Provider chain reference

```python
# prefer="ollama" (default)
providers = ["ollama", "claude"]

# prefer="claude"
providers = ["claude", "ollama"]
```

Failures on either provider are caught generically (`except Exception`); all error types trigger fallback.

---

## Proposed mitigations (not implemented)

1. **`/analyze` degradation** — Return 503 + `{error, hint, fallback_available}` instead of raw 500 when all providers fail.
2. **Empty response guard** — If `not content or not content.strip()` after `call_llm`, skip `json.loads` and return structured error.
3. **Use `_build_compact_profile`** in autofill LLM prompt instead of raw `user_data[:5500]` — guarantees contact + autofill essentials.
4. **Structured truncation log** — `profile_truncated=true, chars=5500, dropped_sections=[...]`.
5. **Determinism** — Document expected variance; optional `temperature=0` + response cache keyed by `(endpoint, jd_hash, profile_hash)`.

---

## Files added

| File | Purpose |
|------|---------|
| `tests/unit/test_llm_resilience.py` | Mocked provider resilience tests |
| `FINDINGS_llm_resilience.md` | This document |
