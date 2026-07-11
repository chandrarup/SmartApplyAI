# Phase-1 Sourcing v3 — Design Contract

Status: implementation contract for the sourcing upgrades on `feature/hybrid-filtering`.
Built by parallel agents; do not deviate from signatures/schemas without updating this doc.
Companion: docs/PHASE2_FILTERING_DESIGN.md (filtering stream, same branch).

## 1. Evaluation summary (spec: ~/Downloads/job-scraper-spec.md)

Gap analysis (file:line verified) found the current scraper already implements the
spec's backbone: 8 ATS providers via public JSON APIs (Greenhouse, Lever, Ashby,
Workday, SmartRecruiters, Workable, Teamtailor, Personio) + a GitHub tracker
provider, registry auto-detection, upsert with first_seen/last_seen,
expire-via-absence, scheduled (APScheduler) + on-demand (POST /pipeline/scrape, CLI)
modes, per-company failure isolation, and politeness pacing.

**Genuinely missing (this contract implements):**
1. Persisted per-source health (success/error/latency/cooldown) — today counters are
   in-memory per run and discarded.
2. Persisted `runs` history + coverage-drop detection.
3. Retry-with-backoff + 429/Retry-After handling in the HTTP layer.
4. Cross-source dedupe (tracker vs ATS can list the same role twice → double-tailoring risk).
5. Freshness-windowed "latest jobs" query + API endpoint.

**Excluded / deferred (with reasons):**
- LinkedIn/Indeed/Google Jobs connectors — violates CLAUDE.md rule 4. Never.
- Apify Multi-ATS — this is the intentional `tierb.py` slot ("Tier B aggregator later");
  paid, deferred until ATS coverage is insufficient.
- Email connector — user's own inbox so rules-compatible, but OAuth plumbing for
  marginal value; deferred.
- GenericCareerConnector (HTML heuristics/headless) — brittle, heavy; deferred.
- LLM "orchestrator agent" — the deterministic planner (health-aware skip/cooldown)
  achieves the spec's adaptive behavior without nondeterminism.
- Schema fields employment_type/board_url/location_normalized/description_html —
  low downstream value now (html survives in raw_json); revisit on demand.

**DO-NOT-REGRESS list** (current behaviors the spec doesn't know about):
liveness classifier (liveness.py + jobs.liveness columns), targeting flags
(is_internship/location_match/sponsorship_knockout), matched_searches stamping +
boosts, expire-via-absence (store.py:206-230), tracker schema-guard (fails loud),
bootstrap_registry.py, MAX_CONCURRENCY=4 + SLEEP_BETWEEN_CALLS_SECONDS=0.35 pacing,
and the matcher contract: `status='active'` + flag columns + matched_searches are
read by matcher/prefilter.py — never change their semantics. jobs.db has ~5.5k real
rows: ALL schema changes must be additive (CREATE TABLE IF NOT EXISTS /
PRAGMA-guarded ALTER TABLE ADD COLUMN). Tests: tmp_path DBs only, no network.

## 2. Module contracts

### 2.1 Agent S1 — store layer v3 (`backend/scraper/store.py` + `backend/scraper/test_store_v3.py`)

Owns ALL store.py changes. Additive schema, applied inside the existing
`ensure_schema`/connection path:

```sql
-- new column on jobs (PRAGMA-guarded ALTER TABLE)
ALTER TABLE jobs ADD COLUMN dedupe_hash TEXT;            -- + index idx_jobs_dedupe
-- new tables
CREATE TABLE IF NOT EXISTS source_health (
  source_ats TEXT PRIMARY KEY,
  last_success_at TEXT, last_error_at TEXT,
  consecutive_errors INTEGER NOT NULL DEFAULT 0,
  avg_latency_ms REAL, cooldown_until TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL, finished_at TEXT,
  mode TEXT NOT NULL DEFAULT 'on_demand',                -- scheduled|on_demand|cli
  jobs_fetched INTEGER DEFAULT 0, jobs_new INTEGER DEFAULT 0,
  jobs_updated INTEGER DEFAULT 0, jobs_expired INTEGER DEFAULT 0,
  provider_stats_json TEXT, anomalies_json TEXT
);
CREATE TABLE IF NOT EXISTS job_sources (
  canonical_source_ats TEXT NOT NULL, canonical_external_id TEXT NOT NULL,
  alt_source_ats TEXT NOT NULL, alt_external_id TEXT NOT NULL,
  alt_apply_url TEXT, first_seen TEXT NOT NULL,
  PRIMARY KEY (alt_source_ats, alt_external_id)
);
```

API additions (existing functions keep exact signatures/behavior):
```python
def compute_dedupe_hash(company: str, title: str, location: str, remote_flag: int = 0) -> str
    # sha256 of "norm(company)|norm(title)|loc_bucket"; norm = lowercase, strip
    # punctuation, collapse whitespace; loc_bucket = "remote" if remote_flag else
    # first comma-segment of location lowercased ("" if empty).

# upsert_company_jobs: stamp dedupe_hash on insert/update, THEN cross-source policy:
#  - inserting a 'tracker' job whose hash matches an ACTIVE non-tracker job →
#    suppress insert, record in job_sources (canonical = the ATS row).
#  - inserting an ATS job whose hash matches an ACTIVE 'tracker' job → insert ATS
#    row, flip the tracker row to status='expired', record link in job_sources.
#  - ATS-vs-ATS hash collisions: leave both (companies live on one ATS; do not guess).
#  Return dict gains keys: "suppressed": int, "superseded": int.
#  NOTE: superseded tracker rows use status='expired' (NOT a new 'superseded'
#  value) because the live jobs table has CHECK(status IN ('active','expired'))
#  and altering a CHECK on 5.5k live rows needs a full table rebuild — too risky.
#  Functionally identical: the matcher only reads status='active'. The real
#  reason lives in job_sources. The "superseded" return count still reports the
#  action for observability.

def latest_jobs(db_path, *, hours_first_seen: int = 72, days_posted: int | None = None,
                keywords: list[str] | None = None, companies: list[str] | None = None,
                limit: int = 100) -> list[dict]
    # active rows; first_seen >= now-hours; optional posted_at window (rows with
    # NULL posted_at are kept — many ATS omit it); keywords = case-insensitive
    # LIKE over title+description_text (any); ordered first_seen DESC.

def record_run_start(db_path, mode: str) -> int          # returns run id
def record_run_end(db_path, run_id: int, totals: dict, provider_stats: dict,
                   anomalies: list[dict]) -> None
def recent_runs(db_path, limit: int = 5) -> list[dict]
def update_source_health(db_path, source_ats: str, *, ok: bool,
                         latency_ms: float | None = None,
                         error: str | None = None,
                         cooldown_hours: float = 6.0,
                         cooldown_after: int = 3) -> None
    # ok=True: last_success_at=now, consecutive_errors=0, cooldown_until=NULL,
    #   avg_latency_ms = EMA(0.3*new + 0.7*old, or new if none).
    # ok=False: last_error_at=now, consecutive_errors+=1; if >= cooldown_after →
    #   cooldown_until = now + cooldown_hours.
def sources_in_cooldown(db_path) -> dict[str, str]        # {source_ats: cooldown_until} where cooldown_until > now
```
Tests: schema migration on a pre-v3 db file (create old schema, reopen, assert new
tables/columns and data intact); dedupe hash normalization; all three cross-source
policies; latest_jobs windows/keywords/NULL posted_at; health EMA + cooldown
enter/exit; run record round-trip. Existing tests must keep passing.

### 2.2 Agent S2 — HTTP resilience (`backend/scraper/providers/base.py` + `backend/scraper/test_http_resilience.py`)

Owns base.py only. Upgrade `http_get_json` / `http_post_json` (keep signatures;
add keyword-only `retries: int = 3`):
- Retry on: requests exceptions (timeout/connection), HTTP 429, HTTP 5xx.
  No retry on other 4xx (config errors — fail immediately).
- Backoff: `min(1.0 * 2**attempt + random.uniform(0, 0.5), 30)` seconds; honor a
  numeric `Retry-After` header when present (cap 60s).
- After final attempt: re-raise the last exception (callers' error isolation in
  run.py already handles it).
- Add module-level `LAST_CALL_LATENCY_MS: float | None` is NOT acceptable (global
  state + concurrency). Instead add:
```python
def timed_get_json(url, *, retries=3, **kw) -> tuple[Any, float]   # (payload, latency_ms of the successful attempt)
def timed_post_json(url, json_body, *, retries=3, **kw) -> tuple[Any, float]
```
  `http_get_json`/`http_post_json` become thin wrappers discarding latency, so all
  existing providers get retries with zero changes.
- Keep USER_AGENT, pacing constants untouched.
Tests: monkeypatched `requests` session/transport — success after N failures, 429
with Retry-After honored, immediate fail on 404, latency returned, no real network.
Use a fake clock/sleep (monkeypatch time.sleep) so tests are instant.

### 2.3 Agent S3 — orchestration wiring (after S1+S2): `backend/scraper/run.py`, `backend/main.py`, `backend/scraper/test_run_v3.py`

- `execute_run(mode: str = "on_demand", ...)` (new optional param, default keeps CLI/API behavior):
  1. `run_id = record_run_start(db, mode)`.
  2. Before fetching: `skip = sources_in_cooldown(db)`; providers in cooldown are
     skipped with loud log `[health] skipping {source} until {ts}`; skipped
     companies counted in provider_stats as `skipped_cooldown`.
  3. Per company fetch: wrap timing around provider calls (use `time.monotonic()`
     at the run level — provider internals unchanged); on success/failure call
     `update_source_health(...)` per source_ats (aggregate per provider per run:
     one health update per provider using any-error status and mean latency).
  4. Coverage-drop detection: after totals, compare per-provider `fetched` to the
     mean of the last 5 runs' provider_stats (from `recent_runs`); if mean >= 20
     and current < 0.3 * mean → anomaly `{"source": s, "type": "coverage_drop",
     "fetched": n, "baseline": mean}`, printed as WARNING.
  5. `record_run_end(...)` in a finally block (crash-safe).
- `backend/main.py`: add `GET /jobs/latest` (query params: hours=72, days=None,
  q=None comma-split keywords, company=None comma-split, limit=100) → `store.latest_jobs`;
  add `GET /jobs/runs` (last 10 runs). Follow the existing endpoint style near
  `GET /jobs/stats` (main.py ~line 1549). Do not touch other endpoints.
- schedule.py: pass `mode="scheduled"`.
- Tests: fake providers (registry monkeypatch) driving cooldown skip, health
  updates, run rows, coverage anomaly; tmp_path db; no network. Existing scraper
  tests keep passing.

## 3. Ground rules (all agents)

Same as the filtering contract: venv at .venv/bin/python; pytest from repo root
with literal output shown; no new pip deps (requests/APScheduler already present);
additive migrations only; one bad company/provider never kills a run; do not
touch matcher/, knowledge/, tailoring code; do not git commit.
