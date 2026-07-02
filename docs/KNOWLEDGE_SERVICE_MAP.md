# Knowledge Service Map — SmartApplyAI / LocalHire Agent 2.0

Read-only inventory of how profile (“knowledge”) data is shaped, loaded, mutated, and consumed. Source of truth at runtime is the **SQLite knowledge store (`backend/knowledge/knowledge.db`) accessed via `knowledge.client`**, with **`backend/profiles/{profile_id}/master_data.json`** as fallback and kept in sync as a JSON mirror on every write, and **`backend/master_data.json`** as legacy last-resort fallback.

---

## 1. PROFILE DATA MODEL

### Storage layout

| Path | Role |
|------|------|
| `backend/master_data.json` | Legacy single-user file; copied into `profiles/default/` on first migration if missing |
| `backend/profiles/default/master_data.json` | Active per-profile canonical store for profile `default` |
| `backend/profiles/meta.json` | Profile registry (id, name, color, pin_hash) — **not** part of `master_data` |
| `backend/profiles/default/resume_config.json` | Per-profile resume tuning (not in `master_data`; loaded by `load_profile_config()`) |

`load_pdata(pid)` asks `knowledge_client.get_profile(pid)` (in-process SQLite, or HTTP when `KNOWLEDGE_SERVICE_URL` is set); if the store has no profile it falls back to `profiles/{pid}/master_data.json`, then `backend/master_data.json`; on total failure returns `{}`. `save_pdata(pid, data)` writes to the store via `knowledge_client.save_profile` and then mirrors the result back to `profiles/{pid}/master_data.json` (`_mirror_pdata_json`).

### Top-level keys (both files share the same schema)

| Key | Type | Nested shape |
|-----|------|--------------|
| `contact_info` | `object` | `name`, `email`, `phone`, `linkedin`, `github`, `location` (all strings) |
| `summary` | `string` | Professional summary paragraph |
| `education` | `array[object]` | Each: `degree`, `university`, `graduation_date`, `details` (string); dashboard also supports optional `gpa`, `field`/`major` |
| `experience` | `array[object]` | Each: `role`, `company`, `duration`, `location`, `details` (string[]). Tailor/PDF paths also accept `title`, `bullets`, `start_date`, `end_date` as aliases |
| `projects` | `array[object]` | Each: `title`, `tech_stack` (string[]), `description` (string); per-profile copy often adds `url`, `date`/`year`, and runtime may add `bullets` (string[], up to 2) |
| `skills` | `object` | Category keys → string arrays. Canonical categories: `languages`, `frameworks`, `tools`, `databases`, `domains`. Legacy also has `apis`, `specialized_libraries` (still present in JSON; LaTeX template uses only the five main categories) |
| `publications` | `array[object]` | Each: `title`, `conference`, `date`, `description` |
| `certifications` | `array[object]` | Each: `name`, `issuer`, `date`; optional `expiry` |
| `awards` | `array[object]` | Each: `title`, `organization`, `description` |
| `leadership` | `array[object]` | Each: `role`, `organization`, `university`, `activities` |
| `research_interests` | `array[string]` | Free-text interest tags |
| `autofill` | `object` | Form-fill canonical values (see below) |
| `common_answers` | `object` | Pre-written Q&A snippets (see below) |
| `learned_answers` | `object` | Host-scoped corrections: keys `"<host>::<label_lowercase>"` → answer string (**per-profile copy only today**) |

### `skills` detail

```json
{
  "languages": ["Python", "SQL", "Java"],
  "frameworks": ["PyTorch", "TensorFlow", "..."],
  "tools": ["Docker", "Git", "..."],
  "databases": ["ChromaDB", "Pinecone", "..."],
  "domains": ["OpenAI", "LangChain", "RAG", "..."],
  "apis": ["OpenAI API", "..."],              // legacy file; optional
  "specialized_libraries": ["librosa", "..."] // legacy file; optional
}
```

LaTeX PDF template (`resume_template.tex`) renders **only** `skills.domains`, `skills.frameworks`, `skills.databases`, `skills.tools`, `skills.languages` under fixed category labels.

### `experience` detail

```json
{
  "role": "Advanced App Engineering Analyst - GenAI Specialist",
  "company": "Accenture (GenWizard Platform)",
  "duration": "Aug 2023 - Aug 2025",
  "location": "India",
  "details": ["bullet 1", "bullet 2", "..."]
}
```

`experience[0].details` is the **only** experience block LLM-tailored for PDF export (max 6 bullets per `resume_config.json`).

### `projects` detail

```json
{
  "title": "GPT-2 Style Small Language Model — Built from Scratch",
  "url": "https://github.com/chandrarup/Build-SLM-From-Scratch",
  "tech_stack": ["PyTorch", "Transformers", "BPE Tokenization"],
  "date": "2024",
  "description": "Single paragraph or 'part A --- part B' split into bullets at runtime"
}
```

Pool size: **13** projects (legacy), **14** (per-profile). Top 3 selected per JD for PDF.

### `autofill` detail

All string values unless noted:

| Key | Example / purpose |
|-----|-------------------|
| `first_name`, `last_name`, `full_name` | Identity |
| `email`, `phone` | Contact |
| `address_line1`, `address_line2`, `city`, `state`, `state_full`, `zip`, `country` | Address |
| `linkedin_url`, `github_url`, `website` | Links |
| `work_authorization`, `requires_sponsorship`, `visa_status` | Work eligibility |
| `salary_expectation`, `salary_min`, `salary_max` | Compensation |
| `notice_period`, `start_date`, `available_to_start` | Availability |
| `willing_to_relocate`, `willing_remote`, `willing_onsite`, `willing_hybrid` | Preferences |
| `veteran_status`, `disability_status`, `gender`, `ethnicity`, `pronouns` | EEO |
| `years_of_experience`, `desired_role`, `current_company`, `current_title` | Career |
| `highest_degree`, `university`, `graduation_year`, `gpa` | Education shortcuts |
| `referral_source` | How they heard about the job |
| `phone_country_code` | **per-profile only** — e.g. `"+1"` |
| `travel_pct` | **per-profile only** — e.g. `"10"` |
| `age_18_or_over`, `has_relatives_at_company`, `has_noncompete`, `currently_employed` | **per-profile only** — Yes/No screening |

### `common_answers` detail

Keys used by dashboard (`QMAP`):

`why_this_company`, `why_this_role`, `greatest_strength`, `greatest_weakness`, `five_year_plan`, `describe_yourself`, `biggest_achievement`, `team_or_solo`, `handling_pressure`, `remote_work_experience`

### Runtime-enriched fields (not persisted in `master_data.json`)

Returned by `GET /profile` via `_enrich_profile_with_resume_sources()`:

| Key | Type | Source |
|-----|------|--------|
| `project_library` | `array[object]` | Merge of `projects` + LaTeX-parsed projects from `resume_source.build_resume_source_bundle()` |
| `_resume_source` | `object` | `base_resume_path`, `cv_path`, `editable_regions` |

### Differences: legacy vs per-profile copy

| Aspect | `backend/master_data.json` | `backend/profiles/default/master_data.json` |
|--------|---------------------------|---------------------------------------------|
| Schema keys | Same top-level keys (no `learned_answers`) | Adds `learned_answers` |
| `experience[0].details` | 4 bullets (shorter) | 6 bullets (expanded, production wording) |
| `projects` | 13 entries; fewer have `url`/`date` | 14 entries; flagship projects (GPT-2 SLM, MARL, SER) at top with `url` + `date` |
| `skills` | Broader domain labels (e.g. “Large Language Models (LLMs)”); includes LangChain/AutoGen in `frameworks` | Restructured for resume PDF: `domains` = LLM/agentic keywords; `Python (Expert)`; APIs moved into `tools` |
| `autofill` | Base 38 keys | +7 keys: `phone_country_code`, `travel_pct`, `age_18_or_over`, `has_relatives_at_company`, `has_noncompete`, `currently_employed`, `available_to_start` |
| `common_answers` | Identical keys and text | Identical |
| `learned_answers` | Absent | Present (e.g. `test.workdayjobs.com::custom question`) |

At runtime **`load_pdata("default")` reads the per-profile file**, not the legacy root copy, unless the profile file is missing.

---

## 2. EVERY READ/WRITE PATH TO PROFILE DATA

### Core accessors (`backend/main.py`)

| Function | Read | Write | Notes |
|----------|------|-------|-------|
| `load_pdata(pid)` | `knowledge_client.get_profile` (SQLite/HTTP) → fallback `profiles/{pid}/master_data.json` → `backend/master_data.json` | — | Primary loader |
| `save_pdata(pid, data)` | — | `knowledge_client.save_profile` + JSON mirror `profiles/{pid}/master_data.json` | Full-document replace |
| `load_master_data()` | `backend/master_data.json` (cwd-relative) | — | **Defined but never called** in the codebase |
| `load_profile_config(pid)` | `profiles/{pid}/resume_config.json` | — | Adjacent config, not `master_data` |
| `_enrich_profile_with_resume_sources(data)` | In-memory merge with LaTeX sources | — | Adds `project_library`, `_resume_source`; may backfill `summary` |

Profile identity: HTTP header **`X-Profile-ID`** (default `"default"`), via `get_pid(request)`.

### All `load_pdata` / `save_pdata` call sites

| Location | Route / context | Read fields | Write fields |
|----------|-----------------|-------------|--------------|
| `resume_html` | `POST /resume-html` | Full profile → enriched | — |
| `autofill_learn` | `POST /autofill/learn` | `learned_answers` | `learned_answers` (key: `{host}::{label}`) |
| `autofill_learned` | `GET /autofill/learned?host=` | `learned_answers` | — |
| `create_profile` | `POST /profiles` | — | New stub: `contact_info.name`, empty `autofill`, `experience`, `education`, `skills`, `common_answers`, `summary` |
| `get_profile` | `GET /profile` | Full → enriched | — |
| `update_contact` | `PUT /profile/contact` | Full | `contact_info` (merge), `summary` |
| `update_autofill` | `PUT /profile/autofill` | Full | `autofill` (merge) |
| `update_experience` | `PUT /profile/experience` | Full | `experience` (replace array) |
| `update_education` | `PUT /profile/education` | Full | `education` (replace array) |
| `update_skills` | `PUT /profile/skills` | Full | `skills` (merge categories) |
| `update_answers` | `PUT /profile/answers` | Full | `common_answers` (merge) |
| `analyze_job` | `POST /analyze` | **Entire profile JSON** dumped to LLM prompt | — |
| `autofill_fields` | `POST /autofill` | `autofill`, `learned_answers`, full profile for LLM phase | — |
| `answer_question` | `POST /answer-question` | Full → enriched | — |
| `generate_cover_letter` | `POST /cover-letter` | **Entire profile JSON** | — |
| `analyze_deep` | `POST /analyze-deep` | `skills` (flat), `autofill.current_title`, `summary` | — |
| `tailor_resume` | `POST /tailor-resume` | Full → enriched: `summary`, `experience[0]`, `skills`, `projects`/`project_library`, `publications` | — |
| `preflight_check` | `POST /preflight-check` | Full (non-enriched) | — |
| `generate_pdf` | `POST /generate-pdf` | Full → enriched; uses `contact_info.name/email` for ATS check | — |

### `/profiles*` endpoints (registry — not `master_data` body)

| Route | Touches profile data? |
|-------|----------------------|
| `GET /profiles` | No — reads `profiles/meta.json` only |
| `POST /profiles` | Yes — creates empty `master_data.json` stub via `save_pdata` |
| `POST /profiles/{pid}/verify-pin` | No — PIN hash in meta |
| `DELETE /profiles/{pid}` | Yes — deletes entire `profiles/{pid}/` directory |
| `PUT /profiles/{pid}/name` | No — updates meta name only |

### Endpoint field consumption (requested routes)

#### `POST /analyze`
- **Consumes:** entire `master_data` document (all keys) serialized into LLM prompt.
- **Does not write** profile.

#### `POST /analyze-deep`
- **Consumes:** `skills` (all categories flattened), `autofill.current_title`, `summary` (first 400 chars).
- Post-processing also matches against flattened skills + title + summary.
- **Does not write** profile.

#### `POST /autofill`
- **Phase 0:** `learned_answers` filtered by `host`.
- **Phase 1 (rules):** `contact_info` (`name`, `email`, `phone`, `linkedin`, `github`), `autofill` (most keys), `summary`.
- **Phase 2 (LLM):** full profile + `autofill` (up to ~5500 chars JSON).
- **Does not write** profile (except via separate `/autofill/learn`).

#### `POST /answer-question`
- **Consumes:** full enriched profile (all keys including `project_library`) up to ~5000 chars in prompt.
- **Does not write** profile.

#### `POST /cover-letter`
- **Consumes:** full profile up to ~5000 chars; reads `contact_info.name` for response metadata.
- **Does not write** profile.

#### `POST /generate-pdf`
- **Consumes:** enriched master for merge + preflight:
  - `summary`, `experience[0].details|bullets`, `projects` (top 3), `skills` (5 categories), `contact_info.name/email`, `publications` (dedup filter), `project_library`.
- Request body carries tailor output (`tailored_summary`, `experience`, `selected_projects`, `tailored_skills`, etc.) — not persisted to `master_data`.
- **Does not write** `master_data` (writes PDF variant under `profiles/{pid}/resumes/`).

#### Related (not in user list but profile-backed)

| Route | Profile fields used |
|-------|---------------------|
| `POST /tailor-resume` | `summary`, `experience[0]`, `skills`, `projects`/`project_library`, `publications`; evidence from all experience bullets + LaTeX sources |
| `POST /preflight-check` | `summary`, `experience`, `skills` vs tailor payload |
| `POST /resume-html` | Same merge path as PDF (render only) |
| `GET /autofill/learned` | `learned_answers` by host (**no current extension caller**) |

---

## 3. CONSUMERS OUTSIDE THE BACKEND

### `backend/dashboard.html`

All profile API calls send `X-Profile-ID` from `localStorage` (`lh_profile_id`, default `default`).

| Endpoint | Direction | Fields |
|----------|-----------|--------|
| `GET /profile` | Read | Full enriched profile → populates all editor tabs |
| `PUT /profile/contact` | Write | `contact_info.{name,email,phone,location,linkedin,github}`, `summary` |
| `PUT /profile/autofill` | Write | 28 autofill keys (subset of schema; omits per-profile-only extras like `phone_country_code`) |
| `PUT /profile/experience` | Write | Full `experience[]` with `role,company,duration,location,details[]` |
| `PUT /profile/education` | Write | Full `education[]` with `degree,university,graduation_date,gpa,details` |
| `PUT /profile/skills` | Write | `skills.{languages,frameworks,tools,databases,domains}` |
| `PUT /profile/answers` | Write | All 10 `common_answers` keys |
| `POST /analyze` | Read (server-side) | Sends `jd_text` only; backend loads full profile |
| `POST /analyze-deep` | Read (server-side) | `jd_text`, `company`, `role` |
| `POST /tailor-resume` | Read (server-side) | JD + selected skills/projects |
| `POST /preflight-check` | Read (server-side) | Tailor payload; backend reloads master for validation |
| `POST /resume-html` | Read (server-side) | Tailor payload |
| `POST /generate-pdf` | Read (server-side) | Tailor payload + `_role`, `_company`, `_jd`, `_analysis` metadata |
| `GET /pending-jd` | — | JD cache only (not profile) |
| `GET /resume/versions` | — | Variant list (not profile) |

**Dashboard UI reads from loaded `profile` object:**

- Overview: `contact_info.name`, `autofill.current_title|desired_role`
- Tailor flow: `project_library|projects`, `publications`, `contact_info`, `education`, `skills`, `summary`
- Skills editor ignores `apis` and `specialized_libraries` categories

### `extension/content.js`

Extension calls **do not send `X-Profile-ID`** — backend always uses profile `default`.

| Endpoint | When | Profile-related payload / usage |
|----------|------|--------------------------------|
| `POST /autofill` | Auto-fill flow | Sends `{fields, jd_text, company, host, llm}`; backend reads full profile |
| `GET /profile` | Workday extras only | Reads `autofill.phone_country_code`, `education[0].field|major`, all `skills` categories, full structure for `fillExperienceFromProfile` / `fillEducationFromProfile` |
| `POST /autofill/learn` | User corrects a filled field | Writes `{host, label, value}` → `learned_answers` |
| `POST /cover-letter` | Panel Cover tab | `{company, role, jd_text, llm}`; backend reads full profile |
| `POST /answer-question` | Panel Ask tab | `{question, jd_text, company, word_limit, llm}`; backend reads enriched profile |
| `POST /analyze-deep` | Panel Resume tab | JD only in body; backend reads `skills`, `summary`, `autofill.current_title` |
| `POST /generate-pdf` | Panel Resume tab | Tailor state in body |
| `POST /pending-jd` | Customize → dashboard | JD only |
| `GET /last-resume` | Resume upload widget | PDF file, not profile JSON |

**Offline fallback:** `getLocalAnswers()` reads `chrome.storage.local.autofill_profile` (extension-local cache, **not** `master_data.json`) keyed by `matchFieldKey(label)`.

### `extension/popup.js`

| Endpoint | Profile usage |
|----------|---------------|
| `POST /analyze` | Backend loads full profile |
| `POST /generate-pdf` | Sends `analysisData` from `/analyze` (not raw profile) |
| `POST /cover-letter` | Backend loads full profile |
| `POST /suggest-questions` | No profile (JD only) |
| `POST /chat` | No profile (page context only) |
| `POST /pending-jd` | JD only |
| `POST /applications` | Application tracker only |
| `POST /set-claude-key` | LLM config only |

Popup does **not** call `GET /profile` directly.

---

## 4. LLM PROVIDER LAYER

Lives in **`backend/llm_provider.py`** (shared seam per CLAUDE.md rule 9); `main.py` and `matcher/llm.py` import from it, so `call_llm` behavior is identical everywhere.

### Environment variables & defaults

| Variable | Default | Purpose |
|----------|---------|---------|
| `OLLAMA_MODEL` | `qwen2.5-coder:7b` | Default Ollama model name |
| `OLLAMA_API_URL` | `http://localhost:11434/v1/chat/completions` | Ollama chat completions endpoint |
| `OLLAMA_HEALTH_URL` | `http://localhost:11434/api/tags` | Model listing / health |
| `ANTHROPIC_API_KEY` | `""` | Claude API key; also set at runtime via `POST /set-claude-key` |

Per-profile `resume_config.json` → `preferred_model` (`"ollama"` default) used by `/tailor-resume` and exposed via `GET /models`.

### `call_ollama(messages, temperature=0.3, timeout=600, model=None)`

- POSTs to `OLLAMA_API_URL` with `{model, messages, stream: false, temperature}`.
- Model: `model` arg or `OLLAMA_MODEL`.
- **Timeout:** `600` seconds (10 minutes) default; used as `requests.post(..., timeout=timeout)`.
- Returns `choices[0].message.content`.

### `call_claude(messages, temperature=0.3, system="")`

- Requires `anthropic` package + `ANTHROPIC_API_KEY`.
- Fixed model: **`claude-sonnet-4-6`**, `max_tokens: 2048`.
- Extracts `system` role messages into Claude `system` param.
- **No explicit timeout** (uses Anthropic SDK defaults).

### `call_llm(messages, temperature=0.3, system="", prefer="ollama", timeout=600, model=None)`

**Provider switch:**

1. Parse `prefer`:
   - `"claude"` → try Claude first, then Ollama.
   - `"ollama"` → try Ollama first, then Claude.
   - `"ollama/<model-name>"` → Ollama with that model, then Claude fallback.
2. On provider exception, log warning and try the other provider.
3. If both fail, raise `RuntimeError`.

**Call-site timeouts:**

| Endpoint | `timeout` passed to `call_llm` |
|----------|-------------------------------|
| `/autofill` | `600` |
| `/cover-letter` | `600` |
| `/tailor-resume` (via `asyncio.to_thread`) | `600` |
| All others | default `600` |

Request body field `llm` on API models maps to `prefer` (`"ollama"` | `"claude"` | `"ollama/model:tag"`).

---

## 5. INTEGRATION SURFACE

### What the Knowledge Service API must serve to avoid breaking anything

| Consumer | Required data (exact contract) |
|----------|-------------------------------|
| **Dashboard — Profile editor (`GET/PUT /profile*`)** | Full document: `contact_info`, `summary`, `autofill` (28 editable keys), `experience[]`, `education[]`, `skills.{languages,frameworks,tools,databases,domains}`, `common_answers` (10 keys). Writes are partial merges except `experience`/`education` (full replace). |
| **Dashboard — Tailor / PDF flow** | Enriched read: above plus `project_library`, `publications`, `_resume_source`. Tailor consumes `summary`, `experience[0].details`, all `skills`, project pool. PDF merge needs `contact_info.name/email`, top-3 `projects` with `title,url,tech_stack,date,bullets|description`, tailored `skills` 5 categories. |
| **`POST /analyze` (dashboard + popup)** | **Whole profile JSON** — LLM uses all sections to match JD, pick projects by title, write summary. |
| **`POST /analyze-deep` (dashboard + content panel)** | Flattened `skills.*`, `autofill.current_title`, `summary` (≤400 chars). Response is JD-derived; matching uses skills+title+summary only. |
| **`POST /tailor-resume`** | `summary`, `experience[0]` (`company,role|title,duration,details|bullets`), `skills` (all list categories), `projects` + merged `project_library`, `publications` (title dedup). External: `resume_config` priorities (`project_priority_keywords`, `skills_jd_additions`, `max_bullets_per_role`). |
| **`POST /preflight-check` + `POST /generate-pdf`** | Master: `summary`, `experience`, `skills`. Payload: `tailored_summary`, `experience[].bullets`, `selected_projects`, `tailored_skills`. ATS: `contact_info.name`, `contact_info.email`. |
| **`POST /resume-html`** | Same merged shape as PDF (HTML render). |
| **`POST /autofill` (content.js)** | `contact_info` (name, email, phone, linkedin, github), full `autofill` object, `summary`, `learned_answers` (host-filtered), and for LLM fallback entire profile. Rule engine also reads `autofill.address` key (not in schema — falls back to `"123 Main St"`). |
| **`POST /autofill/learn` (content.js)** | Write: `learned_answers["{host}::{label_lower}"] = value`. |
| **`GET /autofill/learned`** | Read: all `learned_answers` for host prefix (endpoint exists; no extension caller yet). |
| **`POST /answer-question` (content panel)** | Full enriched profile (~5 KB prompt budget). |
| **`POST /cover-letter` (popup + panel)** | Full profile (~5 KB); metadata needs `contact_info.name`. |
| **`GET /profile` (Workday path in content.js)** | `autofill.phone_country_code`, `education[0].field|major`, all `skills` list values, `experience[]`, `education[]` for structured Workday widgets. |
| **Extension offline fallback** | `chrome.storage.local.autofill_profile` — flat label→value map (separate from backend; populated outside this repo's `master_data`). |
| **`POST /profiles` (new profile)** | Initialize: `{contact_info:{name}, autofill:{}, experience:[], education:[], skills:{}, common_answers:{}, summary:""}`. |
| **Constraints / preflight engine** | `summary`, `experience[]` (company, bullets), for validation against tailor output. |
| **LaTeX `resume_template.tex`** | Dynamic: `summary`, `experience[0].details|bullets`, `projects[:3]` with `title,url,tech_stack,date,bullets|description`, `skills.{domains,frameworks,databases,tools,languages}`. Header/education/research are **hardcoded frozen** in template (not from JSON). |

### Minimum viable Knowledge Service API (preserve behavior)

To replace `load_pdata` / `save_pdata` without breaking consumers, the service must support:

1. **CRUD** on all top-level `master_data` keys listed in §1, plus `learned_answers`.
2. **Profile scoping** via `X-Profile-ID` (dashboard) and implicit `default` (extension).
3. **Enriched read** endpoint or equivalent: merged `project_library` + optional LaTeX source metadata.
4. **Partial updates** matching current PUT semantics (merge vs replace per section).
5. **Host-scoped learned answers** with `{host}::{label}` key format.
6. **Adjacent config** `resume_config.json` per profile (tailor ranking/trim behavior).

---

*Generated from codebase read-only analysis. No behavior was modified.*
