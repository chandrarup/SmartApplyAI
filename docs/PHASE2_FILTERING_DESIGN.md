# Phase-2 Hybrid Filtering — Design Contract

Status: implementation contract for `feature/hybrid-filtering`. Every module below is
built by a separate agent against THIS document. Do not deviate from the signatures,
file names, or data shapes without updating this file.

## 1. Why this exists (evaluation summary)

The spec in `~/Downloads/job-filter-spec.md` proposes deterministic hybrid
(skills + BM25 + embeddings + domain + level) scoring. Evaluation against the
current matcher found these real gaps:

1. **Coverage**: today only `top_recall=50 → top_fit=30` of ~5,500 active jobs ever
   get a real score. Everything else is invisible.
2. **Determinism**: the ranking layer (stage-1 top-1 evidence hit, stage-2
   cross-encoder) is noisy, and only 30 jobs get the canonical score per run.
3. **No lexical channel**: recall is pure bi-encoder semantic. Exact tech terms
   ("Cellpose", "LangChain", "FTS5") are where embeddings are weakest — BM25+dense
   hybrid is the production standard.
4. **Skill representation**: `EQUIVALENCES` in `backend/scoring.py` is a 10-entry
   hand map. An ontology with parents/synonyms gives partial credit systematically.
5. **No feature caching**: every nightly run recomputes from scratch.
6. **No calibration loop**: outcomes never feed back into weights.

Spec adaptations (project rules override the spec):
- **No ElasticSearch/OpenSearch** → SQLite **FTS5** with built-in `bm25()`
  (verified working: sqlite 3.51.3 in `.venv`, python 3.14.4). Zero new deps.
- **No full ESCO import** → curated YAML ontology (~150 skills, ESCO-style schema).
- **Tier thresholds stay per CLAUDE.md rule 10**: 70+ queue, Strong 85+,
  Stretch 70–84. The spec's 80/60 tiers are NOT used.
- **LLM five-dim fit stays** as the final gate (canonical `match_pct`, parity with
  Analyze/Tailor). The hybrid layer replaces the *ranking* stages (recall/rerank)
  and decides which jobs earn the LLM call.

## 2. Pipeline after this change

```
prefilter (unchanged)
  → ensure_job_features(survivors)          # incremental, features.db + FTS5
  → build_candidate_features(profile_id)    # cached by profile hash
  → score_jobs_hybrid(ALL survivors)        # deterministic, 0–100 + components
  → top_fit by hybrid total                 # replaces recall+rerank ranking
  → fit_candidates (LLM five-dim, UNCHANGED) → gate_and_store (70/85, UNCHANGED)
```

Config flag `use_hybrid_ranking: true`. If feature building or hybrid scoring
raises, log loudly and fall back to the legacy recall→rerank path (rule 7).

## 3. Module contracts

Import style everywhere (matches existing code):
```python
try:
    from backend.matcher import ontology
except ImportError:
    from matcher import ontology  # type: ignore
```
No new pip dependencies. numpy + pyyaml + stdlib sqlite3 only.
Embeddings ONLY via the existing adapter: `knowledge.embeddings.embed(texts) -> list[list[float]]`
(bge-small-en-v1.5, already L2-normalized). All new scoring code is LLM-free.

### 3.1 `backend/matcher/ontology.py` + `backend/matcher/skills_ontology.yaml` (Agent A)

YAML schema:
```yaml
version: 1
skills:
  - id: "skill:pytorch"
    name: "PyTorch"
    synonyms: ["pytorch", "torch"]          # matched as case-insensitive \b-bounded literals
    parents: ["skill:deep-learning"]
    related: ["skill:computer-vision"]
    weight: 1.0                              # importance in coverage calc
```
Seed coverage (~150 skills): AI/ML core, DL frameworks, LLM/GenAI stack (RAG,
agents, fine-tuning, vector DBs), classical ML, CV, NLP, biomedical imaging
(cellpose, stardist, spatial omics, microscopy), data engineering, MLOps, cloud
(AWS/GCP/Azure), languages, databases, web backends (FastAPI/Flask), tools.

API:
```python
@dataclass(slots=True)
class Skill:
    id: str; name: str
    synonyms: list[str]; parents: list[str]; related: list[str]
    weight: float = 1.0

def load_ontology(path: str | Path | None = None) -> dict[str, Skill]
    # default path: Path(__file__).with_name("skills_ontology.yaml"); cached with lru_cache on resolved path

def map_text_to_skills(text: str, ontology: dict[str, Skill] | None = None, *, title: str = "") -> dict[str, float]
    # deterministic: word-boundary regex over lowercased text for every synonym.
    # weight = skill.weight * (1 + log1p(term_frequency)); found in `title` → weight *= 2.
    # returns {skill_id: float_weight}, empty dict for empty text.

def match_strength(candidate_skills: dict[str, float], job_skill_id: str,
                   ontology: dict[str, Skill]) -> float
    # 1.0 exact id; 0.7 parent or child of a candidate skill; 0.5 related; else 0.0
```
Tests `backend/matcher/test_ontology.py`: yaml loads & every parent/related id
exists; synonym matching incl. word boundaries ("torch" must not match inside
"torchbearer" is NOT required — but "r" must not match inside "rust" style false
positives must be covered, e.g. short synonyms need `\b`); title boost;
determinism (same input → same output); match_strength ladder.

### 3.2 `backend/matcher/features.py` (Agent B) — job features + `features.db`

Module owns `backend/matcher/features.db` (rule 8). Default path constant
`FEATURES_DB = Path(__file__).with_name("features.db")`, every function takes
`db_path` override for tests.

Schema (created idempotently by `ensure_features_schema(conn)`):
```sql
CREATE TABLE IF NOT EXISTS job_features (
  job_key TEXT PRIMARY KEY,            -- f"{source_ats}:{external_id}"
  desc_hash TEXT NOT NULL,             -- sha256 of description_text; drives incremental rebuild
  required_skills TEXT NOT NULL,       -- json {skill_id: weight}
  preferred_skills TEXT NOT NULL,      -- json
  domain_tags TEXT NOT NULL,           -- json [str]
  level TEXT NOT NULL,                 -- intern|entry|mid|senior|unknown
  is_remote INTEGER NOT NULL DEFAULT 0,
  full_text TEXT NOT NULL,
  embedding_main BLOB NOT NULL,        -- np.float32 tobytes(), normalized
  embedding_requirements BLOB,         -- nullable
  updated_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS jobs_fts USING fts5(job_key UNINDEXED, full_text);
```

API:
```python
def build_job_features(job: dict, ontology: dict[str, Skill]) -> dict
    # pure, no I/O except nothing; segments description_text into sections by regex on
    # headers (requirements|qualifications|what you.ll need|responsibilities|
    # nice to have|preferred|bonus). required_skills from required-ish sections
    # (fallback: full text); preferred_skills from preferred-ish sections.
    # domain_tags from a small keyword table (ml, llm, cv, nlp, biomed, robotics,
    # data-eng, backend, frontend, security, fintech, healthcare).
    # level from title: intern/co-op→intern; junior|new grad|entry|associate→entry;
    # senior|staff|principal|lead|manager|director→senior; else unknown.
    # is_remote from title+location+text regex.
    # Does NOT embed (caller embeds in batch).

def ensure_job_features(jobs: list[dict], db_path=FEATURES_DB, *, ontology=None,
                        embed_fn=None) -> dict   # {"built": n, "reused": m, "failed": k}
    # incremental by desc_hash; batches embeddings via embed_fn (default
    # knowledge.embeddings.embed) for new/changed jobs only; per-job try/except:
    # one bad job is logged and skipped, never kills the run (rule 7).
    # Also refreshes the jobs_fts row on rebuild (delete+insert by job_key).

def get_features(db_path, job_keys: list[str]) -> dict[str, dict]
    # decodes json + np.frombuffer(dtype=np.float32) for embeddings

def bm25_scores(db_path, query_terms: list[str], job_keys: list[str] | None = None) -> dict[str, float]
    # FTS5: SELECT job_key, -bm25(jobs_fts) FROM jobs_fts WHERE jobs_fts MATCH ?
    # query = ' OR '.join of sanitized terms (strip fts5 operators, wrap each in double quotes).
    # Higher = better (bm25() returns negative-better, hence the minus).
    # Missing keys → 0.0. job_keys=None returns all matches.
```
Tests `backend/matcher/test_features.py`: schema idempotency; section split on a
realistic JD fixture; required vs preferred separation; level/remote extraction;
incremental skip on same hash + rebuild on changed hash; FTS5 bm25 ordering sanity
(job containing rare query term scores higher); per-job failure isolation
(monkeypatched embed_fn raising for one job). Use tmp_path db; use a fake
embed_fn (deterministic small vectors) — tests must not download models.

### 3.3 `backend/matcher/candidate_features.py` (Agent C)

```python
def build_candidate_features(profile_id: str = "default", *, ontology=None,
                             embed_fn=None, profile: dict | None = None,
                             target_level: str = "intern") -> dict
```
- Profile via `knowledge_store.get_profile(profile_id)` unless `profile` passed
  (tests pass a fixture; do NOT hit the real DB in tests).
- Returns:
```python
{
  "profile_hash": str,                  # sha256 of canonical-json of profile
  "skills": {skill_id: weight},         # mapper over: skills sections (facet weight 1.0),
                                        # experience details (0.8), projects (0.7),
                                        # summary (0.5); weights summed then max-normalized to 1.0
  "domains": [str],                     # same domain keyword table as features.py — import it
  "target_level": "intern",             # parameter; run.py passes cfg.role_mode mapping
                                        # internship→intern, fulltime→entry, both→intern
  "profile_embedding": np.ndarray,      # embed of summary + top skills paragraph
  "experience_embeddings": list[np.ndarray],  # one per experience/project description (cap 12)
  "query_terms": [str],                 # top 24 skill names+synonyms by weight, for BM25
}
```
- Cache: table `candidate_features(profile_id TEXT PRIMARY KEY, profile_hash TEXT,
  payload BLOB, updated_at TEXT)` in the SAME `features.db` (pass `db_path`;
  create table if missing — coordinate: use `CREATE TABLE IF NOT EXISTS` only,
  never touch job tables). Rebuild iff hash differs. numpy arrays serialized in
  payload as float32 bytes + shapes (json envelope with base64 or a pickle-free
  format of your choice — document it).
Tests `backend/matcher/test_candidate_features.py`: fixture profile → expected
skills present with facet weighting; cache hit (embed_fn called once across two
calls); cache invalidation on profile change; query_terms ordering deterministic.

### 3.4 `backend/matcher/hybrid.py` (Agent D — after A/B/C merge)

```python
SCORING_VERSION = "v1"
DEFAULT_WEIGHTS = {"skills": 0.40, "bm25": 0.20, "embedding": 0.20,
                   "domain": 0.10, "level": 0.10}   # must sum to 1.0

def score_jobs_hybrid(candidate: dict, jobs: list[dict], db_path=FEATURES_DB, *,
                      weights: dict | None = None, ontology=None) -> list[dict]
```
- Fetches features via `get_features`; jobs missing features are skipped with a
  loud log line (they'll be built next run).
- `score_skills`: spec §5.3 — coverage_required with `match_strength` partial
  credit, coverage_preferred scaled; `alpha=0.7, beta=0.3`;
  `(0.7*cov_req + 0.3*cov_pref)*100`; empty required_skills → fall back to
  coverage over all detected skills.
- `score_bm25`: one `bm25_scores(db_path, candidate["query_terms"])` call for the
  batch; min–max normalize to 0–100 **within the batch**; all-equal batch → 50.
- `score_embedding`: `sim = 0.6*cos(profile_emb, embedding_main) +
  0.4*max_i cos(experience_emb_i, embedding_requirements or embedding_main)`;
  vectors already normalized → dot product; map `max(0, sim)*100`.
- `score_domain`: any job domain_tag in candidate primary domains → 100;
  related pair (small hardcoded adjacency: ml↔cv↔nlp↔llm, biomed↔healthcare,
  data-eng↔backend) → 70; no tags on either side → 50 (unknown ≠ bad); else 30.
- `score_level` matrix from target_level=intern: intern→100, entry→85, mid→60,
  senior→25, unknown→70. (target entry: entry→100, intern→80, mid→70, senior→30.)
- Output per job, sorted desc by total:
```python
{"job_key": str, "job": dict, "hybrid_total": float,   # 0–100, round(2)
 "components": {"skills": f, "bm25": f, "embedding": f, "domain": f, "level": f},
 "explanation": {"matched_skills": [names], "gap_skills": [names],
                 "domain_tags": [...], "level": str},
 "scoring_version": SCORING_VERSION}
```
Tests `backend/matcher/test_hybrid.py`: weights sum guard; determinism (two runs
identical); component bounds 0–100; a hand-built candidate/job pair where every
component is hand-computable to exact values; batch bm25 normalization edges.

### 3.5 Integration (Agent D): `run.py`, `config.py`, `config.yaml`, `store.py`

- `MatcherConfig` additions: `use_hybrid_ranking: bool = True`,
  `hybrid_weights: dict = DEFAULT_WEIGHTS` (yaml-overridable),
  `features_db_path: str = "backend/matcher/features.db"`,
  `scoring_version: str = "v1"`.
- `run_pipeline`: when `use_hybrid_ranking`, replace recall+rerank with
  ensure_features → candidate features → hybrid; keep `search_boost` logic;
  select `top_fit` by `hybrid_total`; hand items to `fit_candidates` with
  `item["stage1_score"] = components["embedding"]/100` and
  `item["stage2_score"] = hybrid_total/100` (schema compatibility);
  attach `fit["hybrid"] = {total, components, explanation, scoring_version}`
  after fit so it lands in `fit_json` (dashboard + calibration read it there).
  Any exception in the hybrid path → print loud `[hybrid] failed: ... falling
  back to legacy recall/rerank` and run the legacy stages (rule 7).
- Thresholds/gating untouched.

### 3.6 `backend/matcher/calibrate.py` (Agent D or follow-up)

Offline CLI: reads `matches.db` (`review_status`, and `fit_json.hybrid.components`
when present) → labels (approved/applied=1, rejected=0); coarse grid search over
the weight simplex (step 0.05, weights ≥ 0.05) maximizing AUC (implement
rank-based AUC in numpy, no sklearn); prints best weights + AUC vs current;
`--write` bumps `scoring_version` and writes `hybrid_weights` into
`backend/matcher/config.yaml`. Never auto-applies without `--write`.

## 4. Ground rules for every agent

- venv: `/Users/chandrarupdaka/Documents/Personal/SmartApplyAI/.venv/bin/python`.
  Run tests as `.venv/bin/python -m pytest backend/matcher/test_<x>.py -v` from repo
  root and SHOW the literal output.
- Tests must run offline: no model downloads (fake `embed_fn`), no Ollama, no
  network, tmp_path databases only. Never write to the real `features.db`,
  `jobs.db`, or `matches.db` from tests.
- JD text is untrusted data (rule 6): it is only ever regex-matched or embedded,
  never templated into an LLM prompt in this layer.
- One bad job never kills a run (rule 7): per-job try/except + logged skip.
- No new pip dependencies.
