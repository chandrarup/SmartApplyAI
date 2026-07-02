# Pipeline Edge-Case Findings

**Date:** 2026-06-29  
**Scope:** TEST ONLY — scraper, knowledge store, matcher, scheduler  
**Harness:** `tests/unit/test_pipeline_edges.py` (14 tests, all passing)  
**No fixes applied.**

---

## Summary

| Subsystem | Tests | Critical | High | Medium | Low |
|-----------|-------|----------|------|--------|-----|
| Scraper | 5 | 0 | 0 | 0 | 5 pass |
| Knowledge store | 4 | **1** | 0 | 0 | 3 pass |
| Matcher | 3 | 0 | 0 | 0 | 3 pass |
| Scheduler | 2 | 0 | **1** | **1** | 0 pass |

---

## Scraper — **Low severity (behaviors OK)**

### 404 / empty board → skip + continue — **PASS**
- `execute_run()` catches per-company `HTTPError` (404) and empty lists.
- Logs `DROPPED reason=404` / `zero_jobs`; other tokens still processed.
- Run totals continue; no global abort.

### Dedup across runs — **PASS**
- `upsert_company_jobs` uses `PRIMARY KEY (source_ats, external_id)`.
- Second upsert of same job → **1 row**, status `active`.

### Disappeared job → expired, not deleted — **PASS**
- Jobs not in latest fetch → `status='expired'`.
- Row remains in DB (audit/history preserved).

### Bad JSON shape — **PASS (loud failure)**
- `fetch_lever`: non-list payload → `ValueError("Expected list payload…")`.
- `fetch_ashby`: unexpected dict → `ValueError("Unexpected Ashby payload…")`.
- Does **not** silently return `[]`.

### Rate limit / throttle — **PASS**
- `_fetch_all` sleeps `SLEEP_BETWEEN_CALLS_SECONDS` (0.35s) between thread submissions.
- `MAX_CONCURRENCY = 4` bounds parallel fetches.

---

## Knowledge store

### Mirror divergence (SQLite ↔ JSON) — **PASS (Low)**
- After `save_pdata()`, `profiles/{pid}/master_data.json` **equals** `get_profile(pid)`.
- Phase 1 rollback mirror contract holds for normal writes.

### Concurrent writes — **PASS (Low)**
- Two threads calling `save_profile` on same pid → last-write-wins on `summary`.
- No SQLite corruption; profile remains readable.

### `X-Profile-ID` absent → `"default"` — **PASS (Low)**
- `get_pid(Request)` returns `"default"` when header missing.
- Matches `KNOWLEDGE_SERVICE_MAP` contract.

### Skill rating + re-migrate — **FAIL (Critical)**

**Observed:** `set_rating(proficiency=4)` → `save_profile()` (as `migrate.py` does) → proficiency reset to **NULL**.

**Root cause:** `save_profile` → `_sync_skills_table` **DELETE** all skill rows and re-INSERT with `proficiency=NULL`. Ratings are not merged back from JSON (proficiency lives only in `skills` table, not in section JSON).

**Impact:** Re-running migration or full profile save **wipes manual skill ratings**.

**Proposed fix (not applied):** Preserve `proficiency`/`evidence` on skill re-sync keyed by `(category, name)`; or store ratings in section JSON mirror.

---

## Matcher — **Low severity (behaviors OK)**

### Malformed stage-3 JSON for one job — **PASS**
- `fit_candidates` catches parse errors per job → `_fallback_fit()` (`match_pct=0`).
- Batch continues; second job still scored (90 in test).

**Note:** Failed job is **not skipped** from the list — it remains with zero score. `gate_and_store` excludes it below threshold.

### Empty candidate set — **PASS**
- Empty `jobs.db` / no survivors after prefilter → `[]`, no exception.
- `matcher/run.py` prints `[done] no survivors` and exits 0.

### Threshold boundary at 85 — **PASS**
- `gate_and_store(..., match_threshold=85)` uses `>=`.
- `match_pct=85` **stored**; `84` **excluded** (consistent).

---

## Scheduler

### Missed nightly run (laptop asleep at 02:00) — **High (gap documented)**

**Observed:** `schedule.py` only registers:

```python
scheduler.add_job(execute_run, "cron", hour=2, minute=0)
```

- No `misfire_grace_time`, no catch-up interval, no “run on wake” job.
- **APScheduler default:** missed cron fire while scheduler was stopped is **not** replayed.

**Impact:** If the laptop sleeps at 02:00, **morning job boards may be stale** until next manual run or next night.

**Proposed fix (not applied):** Add startup `execute_run` + `misfire_grace_time` or interval catch-up job.

### Overlapping runs — **Medium (gap documented)**

**Observed:** `execute_run()` has **no mutex/lock**. Two overlapping invocations (manual + scheduled) can process concurrently.

**Impact:** Duplicate fetches, possible SQLite write contention (WAL usually tolerates), confusing logs.

**Proposed fix (not applied):** Module-level `threading.Lock` or job `max_instances=1` in APScheduler.

---

## How to run

```bash
backend/.venv/bin/python -m pytest tests/unit/test_pipeline_edges.py -v
```

---

## Files added

| File | Purpose |
|------|---------|
| `tests/unit/test_pipeline_edges.py` | Pipeline edge pytest suite |
| `FINDINGS_pipeline.md` | This document |
