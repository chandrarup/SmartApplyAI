from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
import uuid, hashlib
import threading as _threading
from datetime import date as _date
from datetime import datetime, timezone
from pydantic import BaseModel, Field
import requests as http_requests
import json
import os
import re
import sqlite3
import shutil
import subprocess
import asyncio
import time as _time
from jinja2 import Environment, BaseLoader

# Production-grade resume pipeline modules
import compile_loop
import constraints as constraints_engine
import resume_versions
import latex_ast
import resume_source

from knowledge import client as knowledge_client
from knowledge import capture as knowledge_capture
from knowledge import rating as knowledge_rating
from knowledge import rating as knowledge_rating
from knowledge import semantic as knowledge_semantic
from teach import fsrs as teach_fsrs
from teach import lesson as teach_lesson
from teach import store as teach_store
import tailor_edits

# M6 review queue + application tracker
from matcher import store as matcher_store
from tracker import store as tracker_store
from tracker import dedupe as tracker_dedupe
from tracker import pacing as tracker_pacing
from tracker import match as tracker_match
from tracker.config import STATUS_READY as _STATUS_READY

# Logging — must come after imports, before app
from logger import get_logger, log_event, is_logging_enabled, set_logging_enabled, set_log_level, get_config as get_log_config, LOGS_DIR
log = get_logger("api")

app = FastAPI()

# --- CONFIGURATION ---
# LLM provider seam lives in llm_provider.py (shared with matcher/teach — CLAUDE.md rule 9)
from llm_provider import (
    OLLAMA_MODEL, OLLAMA_API_URL, OLLAMA_HEALTH_URL, get_anthropic_key,
    call_ollama, call_claude, call_llm, clean_json, ollama_reachable, normalize_llm_prefer,
)
PDF_OUTPUT_DIR = os.path.join(os.getcwd(), "generated_resumes")
os.makedirs(PDF_OUTPUT_DIR, exist_ok=True)

# ─────────────────────────────────────────────────────────────────
# MULTI-PROFILE MANAGEMENT
# ─────────────────────────────────────────────────────────────────
PROFILES_DIR = os.path.join(os.path.dirname(__file__), "profiles")
os.makedirs(PROFILES_DIR, exist_ok=True)
PROFILE_COLORS = ["#F97316","#0D9488","#7C3AED","#E11D48","#4F46E5","#059669"]
MAX_PROFILES = 5

def _safe_pid(pid: str) -> str:
    return re.sub(r'[^a-zA-Z0-9\-]', '', str(pid)) or "default"

def _profile_dir(pid: str) -> str:
    return os.path.join(PROFILES_DIR, _safe_pid(pid))

def load_profiles_meta() -> list:
    try:
        with open(os.path.join(PROFILES_DIR, "meta.json")) as f:
            return json.load(f)
    except:
        return []

def save_profiles_meta(profiles: list):
    with open(os.path.join(PROFILES_DIR, "meta.json"), "w") as f:
        json.dump(profiles, f, indent=2)

def _pin_hash(pin: str) -> str:
    return hashlib.sha256(pin.encode()).hexdigest() if pin else ""

def load_pdata(pid: str) -> dict:
    pid = _safe_pid(pid)
    data = knowledge_client.get_profile(pid)
    if data:
        return data
    return _load_pdata_json_only(pid)

def _load_pdata_json_only(pid: str) -> dict:
    path = os.path.join(_profile_dir(pid), "master_data.json")
    try:
        with open(path) as f:
            return json.load(f)
    except:
        try:
            with open(os.path.join(os.path.dirname(__file__), "master_data.json")) as f:
                return json.load(f)
        except:
            return {}

def _ensure_profile_in_store(pid: str):
    """Bootstrap SQLite from JSON mirror on first partial write (pre-migrate safety)."""
    pid = _safe_pid(pid)
    if knowledge_client.get_profile(pid):
        return
    data = _load_pdata_json_only(pid)
    if data:
        knowledge_client.save_profile(pid, data)

def _mirror_pdata_json(pid: str):
    """Keep master_data.json in sync with the SQLite store (rollback / direct readers)."""
    pid = _safe_pid(pid)
    d = _profile_dir(pid)
    os.makedirs(d, exist_ok=True)
    data = knowledge_client.get_profile(pid)
    with open(os.path.join(d, "master_data.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

_RESUME_CONFIG_DEFAULTS = {
    "preferred_model": "ollama",
    "resume": {
        "max_bullets_per_role": 6,
        "max_words_per_bullet": 35,
        "tone": "technical",
        "project_priority_keywords": [],
    },
    "skills_display_categories": {},
    "skills_jd_additions": {},
}

def load_profile_config(pid: str) -> dict:
    """Load per-profile resume config. Falls back to defaults if not present."""
    import copy
    path = os.path.join(_profile_dir(pid), "resume_config.json")
    try:
        with open(path) as f:
            cfg = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        cfg = {}
    # Deep-merge with defaults so missing keys always have a value
    merged = copy.deepcopy(_RESUME_CONFIG_DEFAULTS)
    for key, val in cfg.items():
        if isinstance(val, dict) and isinstance(merged.get(key), dict):
            merged[key].update(val)
        else:
            merged[key] = val
    return merged


def _enrich_profile_with_resume_sources(data: dict) -> dict:
    """Merge resume source files into the working profile without mutating disk state."""
    enriched = json.loads(json.dumps(data or {}))
    bundle = resume_source.build_resume_source_bundle()
    if bundle.get("base_summary") and not enriched.get("summary"):
        enriched["summary"] = bundle["base_summary"]
    enriched["project_library"] = _ensure_project_bullets(
        resume_source.merge_project_libraries(
            enriched.get("projects", []),
            bundle.get("base_projects", []) + bundle.get("cv_projects", []),
        )
    )
    enriched["_resume_source"] = {
        "base_resume_path": bundle.get("base_resume_path", ""),
        "cv_path": bundle.get("cv_path", ""),
        "editable_regions": bundle.get("editable_regions", []),
    }
    return enriched

def save_pdata(pid: str, data: dict):
    pid = _safe_pid(pid)
    knowledge_client.save_profile(pid, data)
    _mirror_pdata_json(pid)

def load_papps(pid: str) -> list:
    path = os.path.join(_profile_dir(pid), "applications.json")
    try:
        with open(path) as f:
            return json.load(f)
    except:
        return []

def save_papps(pid: str, apps: list):
    d = _profile_dir(pid)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "applications.json"), "w", encoding="utf-8") as f:
        json.dump(apps, f, indent=2)

def get_pid(request: Request) -> str:
    return _safe_pid(request.headers.get("X-Profile-ID", "default"))

def _deep_merge_dict(base: dict, override: dict) -> dict:
    merged = json.loads(json.dumps(base or {}))
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = _deep_merge_dict(merged[k], v)
        else:
            merged[k] = v
    return merged

def _coerce_json(value):
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        txt = value.strip()
        if not txt:
            return None
        try:
            return json.loads(txt)
        except Exception:
            return None
    return None

def _matcher_db_path() -> str:
    env_path = (os.getenv("MATCHER_DB_PATH") or "").strip()
    if env_path:
        return env_path
    return os.path.join(os.path.dirname(__file__), "matcher", "matches.db")

def _matcher_table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    return {str(r[1]).lower() for r in rows}

def _matcher_pick_table(conn: sqlite3.Connection) -> tuple[str | None, set[str]]:
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()]
    for table in tables:
        cols = _matcher_table_columns(conn, table)
        if ("id" in cols or "match_id" in cols) and (
            "tailored_data" in cols or "profile_override" in cols or "company" in cols
        ):
            return table, cols
    for table in tables:
        cols = _matcher_table_columns(conn, table)
        if "company" in cols and ("status" in cols or "approved" in cols):
            return table, cols
    return (tables[0], _matcher_table_columns(conn, tables[0])) if tables else (None, set())

def _matcher_row_to_item(row: dict, cols: set[str]) -> dict:
    rid = row.get("id", row.get("match_id", row.get("queue_id")))
    status = str(row.get("status", row.get("match_status", row.get("state", ""))) or "").lower()
    is_approved = row.get("approved")
    approved_flag = None
    if isinstance(is_approved, (int, float)):
        approved_flag = int(is_approved) == 1
    elif isinstance(is_approved, str):
        approved_flag = is_approved.strip().lower() in {"1", "true", "yes", "approved"}

    tailored = (
        _coerce_json(row.get("tailored_data"))
        or _coerce_json(row.get("profile_override"))
        or _coerce_json(row.get("tailored"))
        or {}
    )
    has_approval_markers = ("approved" in cols) or ("status" in cols) or ("match_status" in cols) or ("state" in cols)
    computed_approved = approved_flag if approved_flag is not None else ("approved" in status)
    if not has_approval_markers:
        computed_approved = True
    return {
        "id": rid,
        "company": row.get("company", row.get("company_name", "")),
        "role": row.get("role", row.get("title", row.get("job_title", ""))),
        "apply_url": row.get("apply_url", row.get("job_url", row.get("url", ""))),
        "status": row.get("status", row.get("match_status", row.get("state", ""))),
        "approved": computed_approved,
        "tailored_data": tailored if isinstance(tailored, dict) else {},
        "_raw": row,
    }

def _matcher_fetch_items(limit: int = 100) -> list[dict]:
    path = _matcher_db_path()
    if not os.path.exists(path):
        return []
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        table, cols = _matcher_pick_table(conn)
        if not table:
            return []
        rows = conn.execute(f'SELECT * FROM "{table}" LIMIT ?', (int(max(1, min(limit, 500))),)).fetchall()
        return [_matcher_row_to_item(dict(r), cols) for r in rows]

def migrate_to_profiles():
    """Idempotent migration: ensure a 'default' profile exists with master_data.json."""
    BASE = os.path.dirname(__file__)
    legacy_src = os.path.join(BASE, "master_data.json")
    legacy_apps = os.path.join(BASE, "applications.json")

    meta = load_profiles_meta()

    # Ensure there is at least one profile and it includes 'default'
    default_entry = next((p for p in meta if p["id"] == "default"), None)

    if not default_entry:
        # Load legacy data to get the user's real name
        data = {}
        if os.path.exists(legacy_src):
            try:
                with open(legacy_src) as f:
                    data = json.load(f)
            except Exception:
                pass
        name = data.get("contact_info", {}).get("name", "My Profile") or "My Profile"
        default_entry = {"id": "default", "name": name, "color": PROFILE_COLORS[0],
                         "created_at": str(_date.today()), "pin_hash": ""}
        # Prepend default so it's first in the list
        meta = [default_entry] + [p for p in meta if p["id"] != "default"]
        save_profiles_meta(meta)

    # Always ensure profiles/default/ directory exists
    d = _profile_dir("default")
    os.makedirs(d, exist_ok=True)

    # Ensure master_data.json exists in the default profile directory
    dst = os.path.join(d, "master_data.json")
    if not os.path.exists(dst):
        data = {}
        if os.path.exists(legacy_src):
            try:
                with open(legacy_src) as f:
                    data = json.load(f)
            except Exception:
                pass
        with open(dst, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)

    # Ensure applications.json exists in the default profile directory
    adst = os.path.join(d, "applications.json")
    if not os.path.exists(adst):
        apps = []
        if os.path.exists(legacy_apps):
            try:
                with open(legacy_apps) as f:
                    apps = json.load(f)
            except Exception:
                pass
        with open(adst, "w", encoding="utf-8") as f:
            json.dump(apps, f, indent=2)

migrate_to_profiles()

log.info(f"SmartApplyAI backend initialized — OLLAMA_MODEL={OLLAMA_MODEL}")
log.info(f"PDF output dir: {PDF_OUTPUT_DIR}")
log.info(f"Profiles dir:   {PROFILES_DIR}")
log_event(log, "INFO", "startup", logs_dir=LOGS_DIR, log_enabled=is_logging_enabled())

# GLOBAL LOCK (The "Traffic Light")
processing_lock = asyncio.Lock()

# Security
origins = ["chrome-extension://*", "http://localhost", "http://127.0.0.1", "*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- DATA MODELS ---
class JobRequest(BaseModel):
    jd_text: str
    llm: str = "ollama"  # "ollama" | "claude"

class ChatRequest(BaseModel):
    context: str
    question: str
    history: list = []
    llm: str = "ollama"

class AutofillRequest(BaseModel):
    fields: list
    jd_text: str = ""
    company: str = ""
    llm: str = "ollama"

class QuestionRequest(BaseModel):
    question: str
    jd_text: str = ""
    company: str = ""
    word_limit: int = 150
    llm: str = "ollama"


class CaptureProposeRequest(BaseModel):
    raw_text: str
    source: str = "dashboard"


class CaptureCommitRequest(BaseModel):
    event_id: int
    edited_delta: dict = {}


class SkillRatingRequest(BaseModel):
    proficiency: int
    evidence: str | None = None

class CoverLetterRequest(BaseModel):
    company: str
    role: str
    jd_text: str = ""
    hiring_manager: str = ""
    llm: str = "ollama"


class TeachReviewRequest(BaseModel):
    skill: str
    grade: str  # again | hard | good | easy


class TeachLearnedRequest(BaseModel):
    skill: str
    proficiency: int | None = None
    evidence: str | None = None
    category: str = "domains"

# ─────────────────────────────────────────────────────────────────
# RULE-BASED AUTOFILL FALLBACK (no LLM needed)
# ─────────────────────────────────────────────────────────────────
def _today_us():
    from datetime import date as _d
    return _d.today().strftime("%m/%d/%Y")

def build_rule_based_answers(fields, autofill, user_data):
    answers = {}
    contact = user_data.get("contact_info", {})
    name = contact.get("name", "")
    today = _today_us()

    # Use autofill section's first/last name directly (avoids middle-name split)
    first_name = autofill.get("first_name") or (name.split()[0] if name else "")
    last_name = autofill.get("last_name") or (name.split()[-1] if name else "")

    # Order matters — more specific patterns FIRST to prevent false matches
    RULES = [
        # ── Identity ────────────────────────────────────────────────────────
        (r"first\s*name|given\s*name|forename|^first$", first_name),
        (r"last\s*name|family\s*name|surname|^last$", last_name),
        (r"preferred\s*(first\s*)?name|nickname", first_name),
        (r"^(full\s*)?name$|^your\s*name$|^name\b|applicant\s*name", name),

        # ── Contact ─────────────────────────────────────────────────────────
        (r"e[\s-]?mail", contact.get("email", "")),
        # Phone-related — specific patterns FIRST so phone doesn't grab them
        (r"phone\s*ext|extension", ""),  # leave extension blank
        (r"country\s*phone\s*code|phone\s*code|country\s*code", "United States of America (+1)"),
        (r"phone\s*device|device\s*type", "Mobile"),
        (r"^phone$|phone\s*number|^mobile(\s*phone|\s*number)?$|^cell(\s*number)?$|^telephone$",
         contact.get("phone", "")),
        (r"linkedin", contact.get("linkedin", "")),
        (r"github", contact.get("github", "")),
        (r"website|portfolio|personal\s*(url|site)", contact.get("website", contact.get("github", ""))),

        # ── Work authorization BEFORE location to prevent "United States" false match ──
        (r"currently\s*eligible\s*to\s*work|eligible\s*to\s*work.*without.*sponsor|"
         r"authorized.*without.*visa|work\s*auth|legally\s*(authorized|eligible)|"
         r"authorized\s*to\s*work",
         autofill.get("work_authorization", "Yes")),

        # Sponsorship — multiple real phrasings
        (r"require.*sponsor.*now\s*or.*future|now\s*or.*future.*require.*sponsor|"
         r"will\s*you.*require.*sponsor|require.*employer.*sponsor|"
         r"do\s*you.*require.*visa|do\s*you\s*now|future.*require.*sponsor|"
         r"require.*sponsor|need.*sponsor|sponsor.*required|visa\s*sponsor",
         autofill.get("requires_sponsorship", "No")),

        # ── Location ────────────────────────────────────────────────────────
        # candidate-location / Location (City) — Greenhouse specific
        (r"location.*city|city.*state.*zip|candidate.?location|^location$",
         autofill.get("city", "Houston")),
        (r"\bcity\b|\blocality\b|\btown\b", autofill.get("city", "Houston")),
        (r"^state$|^province$|\bstate\s*/\s*province\b|\bstate\b.*\bprovince\b",
         autofill.get("state", "TX")),
        (r"\bzip\b|\bpostal\b", autofill.get("zip", "77001")),
        (r"\bcountry\b", autofill.get("country", "United States")),
        (r"address\s*line\s*1|street\s*address|mailing\s*address",
         autofill.get("address", "123 Main St")),

        # ── Compensation & logistics ─────────────────────────────────────────
        (r"salary|compensation|desired\s*pay|expected\s*salary|pay\s*expect|wage",
         autofill.get("salary_expectation", "120000")),
        (r"years.*(of\s*)?experience|experience.*years|years\s*relevant",
         autofill.get("years_of_experience", "2")),
        (r"start\s*date|when.*available|available.*start|earliest\s*start",
         autofill.get("start_date", "Immediately")),
        (r"relocat|willing\s*to\s*move", autofill.get("willing_to_relocate", "Yes")),
        (r"remote|hybrid|work.*arrangement", "Open to remote or hybrid"),
        (r"notice\s*period|weeks?\s*notice", autofill.get("notice_period", "2 weeks")),

        # ── Current employment ───────────────────────────────────────────────
        (r"current\s*(company|employer|organization)|present\s*employer",
         autofill.get("current_company", "Accenture")),
        (r"current\s*(job\s*)?(title|position|role)|present\s*title|job\s*title",
         autofill.get("current_title", "Advanced App Engineering Analyst")),

        # ── Greenhouse-specific custom question labels ───────────────────────
        (r"have you been referred|referred by.*employee", "No"),
        (r"have you worked at", "No"),
        (r"what year will you graduate|graduation year", "2025"),

        # ── EEO & demographic ────────────────────────────────────────────────
        # "I identify my gender as:" — real Greenhouse/general phrasing
        (r"i\s*identify\s*my\s*gender|gender\s*identity|\bgender\b|\bsex\b",
         autofill.get("gender", "Male")),
        (r"i\s*identify\s*my\s*ethnicity|ethnic|race\b|racial",
         autofill.get("ethnicity", "Asian")),
        (r"veteran|military\s*status", autofill.get("veteran_status", "I am not a protected veteran")),
        (r"disability", autofill.get("disability_status", "I don't wish to answer")),

        # ── Open-ended ────────────────────────────────────────────────────────
        (r"summary|tell\s*us\s*about|about\s*yourself|introduce\s*yourself|background",
         user_data.get("summary", "")),
        (r"cover\s*letter|cover letter", user_data.get("summary", "")),
        (r"please\s*introduce\s*yourself|why.*great\s*fit|explain.*why.*fit",
         user_data.get("summary", "")),

        # ── Source / referral ────────────────────────────────────────────────
        (r"referral|how\s*did\s*you\s*(hear|find|learn|know)|referred\s*by|"
         r"source\s*of\s*hire|where\s*did\s*you|how\s*did\s*(techflow|acme|corp|company)",
         "LinkedIn"),

        # ── Dates ────────────────────────────────────────────────────────────
        (r"^date$|^\d+\.?\s*date:?$|today.?s?\s*date|signature\s*date", today),
        (r"how\s*much\s*%|travel\s*percent|%.*travel", "25"),

        # ── Misc ─────────────────────────────────────────────────────────────
        (r"pronouns", "He/Him"),
        (r"additional\s*information|anything\s*else|additional\s*comments",
         user_data.get("summary", "")),
    ]

    for field in fields:
        label = field.get("label", "")
        if not label:
            continue
        # Hard-skip any field flagged as sensitive (health/legal/political/etc.)
        if field.get("sensitive"):
            answers[label] = "SKIP"
            continue
        for pattern, value in RULES:
            if re.search(pattern, label, re.I) and value:
                answers[label] = str(value)
                break

    return answers

# ─────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────
def load_master_data():
    try:
        with open("master_data.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

# LaTeX special characters → their escaped, literal-rendering forms. Backslash and
# the two math accents must become \textbackslash/\textasciitilde/\textasciicircum so
# that hostile input like \write18{...} or \input{/etc/passwd} renders as visible text
# instead of executing. Applied in a single pass (see escape_latex_chars) so the {}
# introduced by these replacements are never themselves re-escaped.
_LATEX_ESCAPE_MAP = {
    "\\": r"\textbackslash{}",  # must map backslash to a form that has no live command
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "^": r"\textasciicircum{}",
    "~": r"\textasciitilde{}",
}
_LATEX_ESCAPE_RE = re.compile("|".join(re.escape(c) for c in _LATEX_ESCAPE_MAP))

def escape_latex_chars(text):
    """Escape every TeX special so untrusted text renders as literal characters.

    Single-pass regex: each special is replaced exactly once, so the braces in
    replacements like ``\\textbackslash{}`` are never re-processed. There is
    deliberately NO 'already escaped' shortcut — that heuristic is itself an injection
    bypass (hostile ``\\&\\textbf{x}`` would skip escaping entirely).
    """
    if not isinstance(text, str):
        return text
    return _LATEX_ESCAPE_RE.sub(lambda m: _LATEX_ESCAPE_MAP[m.group(0)], text)


# Schemes permitted inside \href — anything else (javascript:, data:, a raw \command,
# a bare word) is treated as hostile and the link is dropped.
_SAFE_URL_SCHEME_RE = re.compile(r"^(?:https?://|mailto:)", re.IGNORECASE)
# Chars that must be escaped inside \href's first (URL) argument so a crafted URL
# cannot break out of the braces or start a command.
_HREF_ESCAPE_MAP = {
    "\\": r"\textbackslash{}",
    "%": r"\%",
    "#": r"\#",
    "{": r"\{",
    "}": r"\}",
    "^": r"\textasciicircum{}",
    "~": r"\textasciitilde{}",
    "&": r"\&",
}
_HREF_ESCAPE_RE = re.compile("|".join(re.escape(c) for c in _HREF_ESCAPE_MAP))

def _safe_href_url(url):
    """Return a \\href-safe URL, or "" if the URL is missing or not a trusted scheme.

    Only http(s):// and mailto: survive; the surviving URL is escaped for the \\href
    argument. Callers/template render the project title as plain text when this is "".
    """
    if not isinstance(url, str):
        return ""
    u = url.strip()
    if not u or not _SAFE_URL_SCHEME_RE.match(u):
        return ""
    return _HREF_ESCAPE_RE.sub(lambda m: _HREF_ESCAPE_MAP[m.group(0)], u)


# Control characters (C0 minus tab/newline, plus DEL and C1) that should never reach
# the template — they can corrupt the .tex stream or hide content.
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

def sanitize_untrusted_text(value, max_len=20000):
    """Normalize untrusted free-text before it enters the tailoring/PDF path.

    Strips control chars/NULs, collapses whitespace runs, and caps length. This is
    defense-in-depth ahead of escape_latex_chars (the hard guarantee) — it keeps raw
    control bytes and pathological lengths out of stored/merged profile data.
    Recurses into lists/dicts so callers can hand it a whole payload subtree.
    """
    if isinstance(value, str):
        s = _CONTROL_CHAR_RE.sub("", value)
        s = re.sub(r"[ \t]+", " ", s)
        s = re.sub(r"\n{3,}", "\n\n", s)
        s = s.strip()
        if max_len is not None and len(s) > max_len:
            s = s[:max_len]
        return s
    if isinstance(value, list):
        return [sanitize_untrusted_text(v, max_len) for v in value]
    if isinstance(value, dict):
        return {k: sanitize_untrusted_text(v, max_len) for k, v in value.items()}
    return value

# ─────────────────────────────────────────────────────────────────
# STATIC ROUTES
# ─────────────────────────────────────────────────────────────────
@app.get("/health")
def health_check():
    return {"message": "Server is Online"}

@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)

# ── Telemetry sink (JSONL append to ./telemetry.log) ───────────────────────
_TELEMETRY_PATH = os.path.join(os.path.dirname(__file__), "telemetry.log")

@app.post("/telemetry/events")
async def telemetry_events(payload: dict):
    """Append-only event log. Rolled up later by analytics jobs."""
    events = payload.get("events", [])
    if not events: return {"received": 0}
    try:
        with open(_TELEMETRY_PATH, "a") as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")
        return {"received": len(events)}
    except Exception as e:
        return {"received": 0, "error": str(e)}

@app.get("/telemetry/summary")
def telemetry_summary():
    """Quick rollup: events by name, adapter success rate."""
    if not os.path.exists(_TELEMETRY_PATH):
        return {"total": 0, "events": {}}
    by_name, by_adapter = {}, {}
    total = 0
    try:
        with open(_TELEMETRY_PATH) as f:
            for line in f:
                try: e = json.loads(line)
                except: continue
                total += 1
                by_name[e.get("name", "?")] = by_name.get(e.get("name", "?"), 0) + 1
                if e.get("adapter"):
                    by_adapter.setdefault(e["adapter"], {"total": 0, "by_method": {}})
                    by_adapter[e["adapter"]]["total"] += 1
                    m = e.get("method", "?")
                    by_adapter[e["adapter"]]["by_method"][m] = by_adapter[e["adapter"]]["by_method"].get(m, 0) + 1
    except Exception: pass
    return {"total": total, "events": by_name, "adapters": by_adapter}

# ── Pending JD store (one per profile, in-memory, cleared on read) ──────────
_pending_jd: dict = {}  # token → {jd, role, company, ts, pid}

@app.post("/pending-jd")
def set_pending_jd(payload: dict, request: Request):
    pid = get_pid(request)
    token = uuid.uuid4().hex
    _pending_jd[token] = {
        "jd": payload.get("jd", ""),
        "role": payload.get("role", ""),
        "company": payload.get("company", ""),
        "jd_quality": payload.get("jd_quality"),
        "pid": pid,
        "ts": __import__("time").time(),
    }
    return {"ok": True, "token": token}

@app.get("/pending-jd")
def get_pending_jd(request: Request, token: str = ""):
    pid = get_pid(request)
    data = None
    if token:
        token = re.sub(r"[^a-zA-Z0-9]", "", token)
        data = _pending_jd.pop(token, None)
    if not data:
        # Legacy fallback for older clients
        for key, item in list(_pending_jd.items()):
            if item.get("pid") == pid:
                data = _pending_jd.pop(key, None)
                if data:
                    break
    if data and (__import__("time").time() - data.get("ts", 0)) < 300:  # 5-min TTL
        return data
    return {"jd": "", "role": "", "company": ""}

@app.get("/resume/versions")
def list_resume_versions(request: Request):
    """List all saved tailored resume variants for the active profile, newest first."""
    pid = get_pid(request)
    return {"variants": resume_versions.list_variants(_profile_dir(pid))}


@app.get("/resume/versions/{variant_id}/pdf")
def get_variant_pdf(variant_id: str, request: Request):
    """Download a specific variant's PDF."""
    pid = get_pid(request)
    path = resume_versions.get_variant_path(_profile_dir(pid), variant_id, "tailored.pdf")
    if not path:
        raise HTTPException(status_code=404, detail="Variant or PDF not found")
    return FileResponse(path, media_type="application/pdf", filename=f"{variant_id}.pdf")


@app.get("/resume/versions/{variant_id}/meta")
def get_variant_meta(variant_id: str, request: Request):
    """Full metadata of a variant including validation results."""
    pid = get_pid(request)
    pdir = _profile_dir(pid)
    out = {}
    for fname in ["meta.json", "score.json", "validation.json", "analysis.json"]:
        path = resume_versions.get_variant_path(pdir, variant_id, fname)
        if path:
            try:
                with open(path) as f:
                    out[fname.replace(".json", "")] = json.load(f)
            except Exception:
                pass
    if not out:
        raise HTTPException(status_code=404, detail="Variant not found")
    return out


@app.delete("/resume/versions/{variant_id}")
def delete_variant(variant_id: str, request: Request):
    pid = get_pid(request)
    var_dir = os.path.join(_profile_dir(pid), "resumes", "variants", variant_id)
    if not os.path.exists(var_dir) or not re.match(r"^[\w.-]+$", variant_id):
        raise HTTPException(status_code=404, detail="Variant not found")
    import shutil
    shutil.rmtree(var_dir)
    return {"deleted": variant_id}


@app.get("/last-resume")
def last_resume():
    """Return the most recently generated resume PDF for upload by the extension."""
    path = os.path.join(os.path.dirname(__file__), "tailored_resume.pdf")
    if os.path.exists(path):
        return FileResponse(path, media_type="application/pdf", filename="resume.pdf")
    raise HTTPException(status_code=404, detail="No resume generated yet")

def render_resume_html(master: dict) -> str:
    """ATS-friendly HTML resume — printable to PDF with Cmd+P."""
    contact = master.get("contact_info", {})
    name = contact.get("name", "")
    parts = [contact.get(k, "") for k in ("phone", "email", "linkedin", "github", "location") if contact.get(k)]
    summary = master.get("summary", "")
    edu = master.get("education", []) or []
    exp = master.get("experience", []) or []
    projects = master.get("projects", []) or []
    skills = master.get("skills", {}) or {}

    def esc(s):
        return (str(s) if s is not None else "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
    def bullets_html(items):
        return "<ul>" + "".join(f"<li>{esc(b)}</li>" for b in (items or [])) + "</ul>" if items else ""

    css = """
    @page { margin: 0.5in; }
    body { font-family: 'Times New Roman', Georgia, serif; max-width: 8in; margin: 0.4in auto; color: #000; line-height: 1.35; font-size: 11pt; }
    h1 { font-size: 18pt; text-align: center; margin: 0 0 4px 0; letter-spacing: 1px; }
    .contact { text-align: center; font-size: 10pt; margin-bottom: 12px; color: #333; }
    h2 { font-size: 12pt; text-transform: uppercase; border-bottom: 1px solid #000; margin: 14px 0 6px 0; padding-bottom: 2px; letter-spacing: 0.8px; }
    h3 { font-size: 11pt; margin: 6px 0 2px 0; }
    .row { display: flex; justify-content: space-between; align-items: baseline; margin-top: 6px; }
    .role { font-style: italic; font-size: 10.5pt; }
    .dates { font-size: 10pt; color: #444; }
    ul { margin: 4px 0 4px 22px; padding: 0; }
    li { margin: 2px 0; font-size: 10.5pt; }
    .skills-line { font-size: 10.5pt; margin: 2px 0; }
    .skills-cat { font-weight: bold; }
    .summary { font-size: 10.5pt; margin: 4px 0; }
    @media print { body { margin: 0; } .noprint { display: none; } }
    """

    exp_html = ""
    for e in exp:
        # Profile uses: role, company, duration, location, details
        # Tailor-resume sets: title, start_date/end_date, bullets — support both
        bul = e.get("bullets") or e.get("details") or []
        if not bul and e.get("description"):
            bul = e["description"] if isinstance(e["description"], list) else [e["description"]]
        title = e.get("title") or e.get("role") or ""
        dates = e.get("duration") or f"{e.get('start_date','')} – {e.get('end_date','Present')}"
        exp_html += f"""
        <div>
          <div class="row"><h3>{esc(e.get('company',''))}</h3><span class="dates">{esc(dates)}</span></div>
          <div class="row"><span class="role">{esc(title)}</span><span class="dates">{esc(e.get('location',''))}</span></div>
          {bullets_html(bul)}
        </div>"""

    edu_html = ""
    for ed in edu:
        edu_html += f"""
        <div>
          <div class="row"><h3>{esc(ed.get('university',''))}</h3><span class="dates">{esc(ed.get('start','') or ed.get('start_date',''))} – {esc(ed.get('end','') or ed.get('graduation','') or '')}</span></div>
          <div class="row"><span class="role">{esc(ed.get('degree',''))}{', ' + esc(ed.get('field','')) if ed.get('field') else ''}</span>
            <span class="dates">{('GPA: ' + esc(ed.get('gpa',''))) if ed.get('gpa') else ''}</span></div>
        </div>"""

    proj_html = ""
    for p in projects[:4]:
        bul = p.get("bullets") or ([p.get("description")] if p.get("description") else [])
        proj_html += f"""
        <div>
          <h3>{esc(p.get('title',''))}</h3>
          {bullets_html(bul)}
        </div>"""

    skills_html = ""
    for cat, items in skills.items():
        if isinstance(items, list) and items:
            label = cat.replace("_", " ").title()
            skills_html += f'<div class="skills-line"><span class="skills-cat">{esc(label)}:</span> {esc(", ".join(items))}</div>'

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{esc(name)} — Resume</title><style>{css}</style></head>
<body>
  <h1>{esc(name)}</h1>
  <div class="contact">{esc(' | '.join([p for p in parts if p]))}</div>
  {f'<h2>Summary</h2><p class="summary">{esc(summary)}</p>' if summary else ''}
  {f'<h2>Education</h2>{edu_html}' if edu_html else ''}
  {f'<h2>Experience</h2>{exp_html}' if exp_html else ''}
  {f'<h2>Selected Projects</h2>{proj_html}' if proj_html else ''}
  {f'<h2>Skills</h2>{skills_html}' if skills_html else ''}
  <div class="noprint" style="text-align:center;margin-top:30px;padding:14px;background:#f3f4f6;border-radius:8px;font-family:Arial">
    <strong>Press Cmd+P (or Ctrl+P) → Save as PDF</strong> to download an ATS-compatible PDF.
  </div>
</body></html>"""

@app.post("/resume-html")
async def resume_html(data: dict, request: Request):
    """Render HTML from the same merged resume state used by PDF export."""
    master = _enrich_profile_with_resume_sources(load_pdata(get_pid(request)))
    html = render_resume_html(_merge_tailored_into_master(master, data))
    return HTMLResponse(content=html)

class LearnRequest(BaseModel):
    host: str
    label: str
    value: str


class KnowledgeSearchRequest(BaseModel):
    query_text: str
    k: int = 10
    kind_filter: str | None = None


@app.post("/autofill/learn")
def autofill_learn(req: LearnRequest, request: Request):
    """Save a user correction so we use it next time on the same domain."""
    pid = get_pid(request)
    _ensure_profile_in_store(pid)
    saved = knowledge_client.set_learned_answer(pid, req.host, req.label, req.value)
    _mirror_pdata_json(pid)
    return {"ok": True, "saved": saved}

@app.get("/autofill/learned")
def autofill_learned(host: str, request: Request):
    pid = get_pid(request)
    return knowledge_client.get_learned_answers(pid, host)


@app.get("/autofill/learned/all")
def autofill_learned_all(request: Request):
    """Every answer the user has taught the autofiller, grouped by host for the UI."""
    pid = get_pid(request)
    raw = knowledge_client.list_all_learned_answers(pid)
    items = []
    for key, value in (raw or {}).items():
        host, _, label = str(key).partition("::")
        items.append({"key": key, "host": host, "label": label, "value": value})
    items.sort(key=lambda x: (x["host"], x["label"]))
    return {"items": items}


@app.delete("/autofill/learned")
def autofill_learned_delete(key: str, request: Request):
    pid = get_pid(request)
    deleted = knowledge_client.delete_learned_answer(pid, key)
    if deleted:
        _mirror_pdata_json(pid)
    return {"deleted": deleted}


@app.post("/knowledge/search")
def knowledge_search(req: KnowledgeSearchRequest, request: Request):
    pid = get_pid(request)
    try:
        hits = knowledge_client.search(
            pid=pid,
            query_text=req.query_text,
            k=req.k,
            kind_filter=req.kind_filter,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"results": hits}


class ClaudeKeyRequest(BaseModel):
    key: str

@app.post("/set-claude-key")
def set_claude_key(req: ClaudeKeyRequest):
    """Allow the extension to push a Claude API key at runtime (stored in env)."""
    if not req.key or not req.key.startswith("sk-"):
        raise HTTPException(status_code=400, detail="Invalid API key format. Must start with 'sk-'.")
    os.environ["ANTHROPIC_API_KEY"] = req.key
    return {"message": "Claude API key set. Claude is now active as a fallback."}

@app.get("/llm-status")
def llm_status():
    """Return provider availability plus local PDF toolchain status."""
    ollama_ok = ollama_reachable(timeout=1.5)
    claude_ok = bool(get_anthropic_key())
    return {
        "default_provider": "ollama",
        "ollama": ollama_ok,
        "ollama_model": OLLAMA_MODEL,
        "ollama_api_url": OLLAMA_API_URL,
        "claude": claude_ok,
        "claude_key_set": claude_ok,
        "pdf_toolchain": {
            "pdflatex": bool(compile_loop.PDFLATEX_BIN and os.path.exists(compile_loop.PDFLATEX_BIN)),
            "pdftotext": bool(compile_loop.PDFTOTEXT_BIN and os.path.exists(compile_loop.PDFTOTEXT_BIN)),
            "pdfinfo": bool(shutil.which("pdfinfo")),
        },
    }


@app.get("/models")
def list_models(request: Request):
    """Return all models available for use, grouped by provider.

    Ollama models come from the Ollama /api/tags endpoint.
    Claude is added as a provider if an API key is configured.
    Each model entry: { id, label, provider, default, size_gb, note }
    """
    # Models that are NOT for text generation — exclude from this list
    _EXCLUDE_PATTERNS = ["embed", "ocr", "clip", "vision-only", "whisper"]

    # Recommended notes for known models
    _MODEL_NOTES = {
        "qwen2.5-coder:7b":      "Best for structured JSON + code output (recommended)",
        "deepseek-r1:7b":        "Strong reasoning — good for nuanced bullet rewriting",
        "qwen3:32b":             "Highest quality — slower, needs more RAM",
        "qwen3-coder-next:latest": "Latest Qwen3 coder — highest quality, very large (51GB)",
        "llama3.2:3b":           "Fast and lightweight — lower output quality",
        "qwen2.5:3b":            "Fast and lightweight — lower output quality",
        "gemma4:e4b":            "Google Gemma 4 — good general-purpose model",
    }

    # Preferred display order
    _ORDER = [
        "qwen2.5-coder:7b",
        "deepseek-r1:7b",
        "qwen3:32b",
        "gemma4:e4b",
        "qwen2.5:3b",
        "llama3.2:3b",
        "qwen3-coder-next:latest",
    ]

    models = []
    # Ollama models
    try:
        r = http_requests.get(OLLAMA_HEALTH_URL, timeout=1.5)
        if r.status_code == 200:
            raw = r.json().get("models", [])
            # Filter out non-text-gen models
            filtered = [
                m for m in raw
                if not any(pat in m.get("name", "").lower() for pat in _EXCLUDE_PATTERNS)
            ]
            # Sort by preferred order; unknowns go to end
            def _sort_key(m):
                name = m.get("name", "")
                try:
                    return _ORDER.index(name)
                except ValueError:
                    return len(_ORDER)
            filtered.sort(key=_sort_key)
            installed_names = [m.get("name", "") for m in filtered]
            default_name = OLLAMA_MODEL if OLLAMA_MODEL in installed_names else (
                next((n for n in _ORDER if n in installed_names), installed_names[0] if installed_names else OLLAMA_MODEL)
            )
            for m in filtered:
                name = m.get("name", "")
                is_default = name == default_name
                note = _MODEL_NOTES.get(name, "")
                if name == OLLAMA_MODEL and OLLAMA_MODEL not in installed_names:
                    note = (note + " (configured default — not installed; run `ollama pull " + OLLAMA_MODEL + "`)").strip()
                models.append({
                    "id": f"ollama/{name}",
                    "label": name,
                    "provider": "ollama",
                    "default": is_default,
                    "size_gb": round(m.get("size", 0) / 1e9, 1),
                    "reachable": True,
                    "note": note,
                })
            # Configured default missing from disk — still list it so UI can show pull hint
            if OLLAMA_MODEL not in installed_names:
                models.insert(0, {
                    "id": f"ollama/{OLLAMA_MODEL}",
                    "label": OLLAMA_MODEL,
                    "provider": "ollama",
                    "default": False,
                    "size_gb": None,
                    "reachable": True,
                    "installed": False,
                    "note": "Configured default — run `ollama pull " + OLLAMA_MODEL + "`",
                })
    except Exception:
        # Ollama not reachable — still expose the configured default with canonical id
        models.append({
            "id": f"ollama/{OLLAMA_MODEL}", "label": OLLAMA_MODEL, "provider": "ollama",
            "default": True, "size_gb": None, "reachable": False,
            "note": f"{_MODEL_NOTES.get(OLLAMA_MODEL, '')} (Ollama offline — start Ollama to use)".strip(),
        })

    # Claude (cloud — always available if key set)
    if get_anthropic_key():
        for claude_model, note in [
            ("claude-sonnet-4-6", "Latest Claude Sonnet — highest quality cloud model"),
            ("claude-haiku-4-5-20251001", "Claude Haiku — fast and cost-efficient cloud model"),
        ]:
            models.append({
                "id": f"claude/{claude_model}",
                "label": claude_model,
                "provider": "claude",
                "default": False,
                "size_gb": None,
                "note": note,
            })

    # Profile default model from config
    pid = get_pid(request)
    cfg = load_profile_config(pid)
    profile_default = cfg.get("preferred_model", "ollama")

    return {"models": models, "profile_default": profile_default, "system_default": OLLAMA_MODEL,
            "ollama_reachable": ollama_reachable(timeout=1.5)}

# ─────────────────────────────────────────────────────────────────
# PAGES
# ─────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
@app.get("/login", response_class=HTMLResponse)
def login_page():
    with open(os.path.join(os.path.dirname(__file__), "login.html"), "r") as f:
        return f.read()

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    with open(os.path.join(os.path.dirname(__file__), "dashboard.html"), "r") as f:
        return f.read()


@app.get("/teach/review", response_class=HTMLResponse)
def teach_review_page():
    return FileResponse(os.path.join(os.path.dirname(__file__), "teach", "review.html"))


@app.get("/teach/due")
def teach_due(request: Request):
    pid = get_pid(request)
    due = teach_store.due_today(pid)
    return {"due": due, "count": len(due)}


@app.get("/teach/lesson/{skill}")
def teach_lesson_route(skill: str, request: Request, llm: str = "ollama"):
    pid = get_pid(request)
    return teach_lesson.lesson(
        skill=skill,
        pid=pid,
        llm_callable=call_llm,
        llm_prefer=llm,
    )


@app.post("/teach/review")
def teach_review(payload: TeachReviewRequest, request: Request):
    if payload.grade not in teach_fsrs.VALID_GRADES:
        raise HTTPException(status_code=400, detail="grade must be one of again|hard|good|easy")
    pid = get_pid(request)
    base = teach_store.ensure_state(pid, payload.skill)
    updated = teach_fsrs.apply_review(base, payload.grade)
    saved = teach_store.save_state(pid, payload.skill, updated)
    return {"ok": True, "review": saved}


@app.post("/teach/learned")
def teach_learned(payload: TeachLearnedRequest, request: Request):
    pid = get_pid(request)
    skill = (payload.skill or "").strip()
    if not skill:
        raise HTTPException(status_code=400, detail="skill is required")

    before = knowledge_rating.get_proficiency(pid, skill)
    target = payload.proficiency
    if target is None:
        target = min(5, (before or 2) + 1)
    evidence = payload.evidence or f"self-study {_date.today().isoformat()}"

    updated = knowledge_rating.set_rating_by_name(
        pid=pid,
        skill_name=skill,
        proficiency=target,
        evidence=evidence,
        source="teach.learned",
        category=payload.category or "domains",
    )
    _mirror_pdata_json(pid)
    after = knowledge_rating.get_proficiency(pid, skill)
    return {
        "ok": True,
        "skill": skill,
        "before": before,
        "after": after,
        "evidence": updated.get("evidence", evidence),
    }

# ─────────────────────────────────────────────────────────────────
# PROFILES API
# ─────────────────────────────────────────────────────────────────
@app.get("/profiles")
def list_profiles():
    meta = load_profiles_meta()
    return [{"id": p["id"], "name": p["name"], "color": p["color"],
             "created_at": p.get("created_at",""), "has_pin": bool(p.get("pin_hash",""))} for p in meta]

@app.post("/profiles")
def create_profile(payload: dict):
    meta = load_profiles_meta()
    if len(meta) >= MAX_PROFILES:
        raise HTTPException(status_code=400, detail=f"Maximum {MAX_PROFILES} profiles reached")
    name = payload.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    pid = re.sub(r'[^a-z0-9]', '-', name.lower())[:20] + "-" + str(uuid.uuid4())[:6]
    color = payload.get("color", PROFILE_COLORS[len(meta) % len(PROFILE_COLORS)])
    pin = payload.get("pin", "")
    new_profile = {"id": pid, "name": name, "color": color,
                   "created_at": str(_date.today()), "pin_hash": _pin_hash(pin)}
    os.makedirs(_profile_dir(pid), exist_ok=True)
    # Start with empty profile data
    knowledge_client.create_stub(pid, name)
    _mirror_pdata_json(pid)
    save_papps(pid, [])
    meta.append(new_profile)
    save_profiles_meta(meta)
    return {"id": pid, "name": name, "color": color, "has_pin": bool(pin)}

@app.post("/profiles/{pid}/verify-pin")
def verify_pin(pid: str, payload: dict):
    pid = _safe_pid(pid)
    meta = load_profiles_meta()
    profile = next((p for p in meta if p["id"] == pid), None)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    stored = profile.get("pin_hash", "")
    if not stored:
        return {"ok": True}  # no PIN set
    submitted = _pin_hash(payload.get("pin", ""))
    if submitted != stored:
        raise HTTPException(status_code=401, detail="Incorrect PIN")
    return {"ok": True}

@app.delete("/profiles/{pid}")
def delete_profile(pid: str):
    pid = _safe_pid(pid)
    meta = load_profiles_meta()
    if len(meta) <= 1:
        raise HTTPException(status_code=400, detail="Cannot delete the only profile")
    new_meta = [p for p in meta if p["id"] != pid]
    if len(new_meta) == len(meta):
        raise HTTPException(status_code=404, detail="Profile not found")
    save_profiles_meta(new_meta)
    import shutil
    d = _profile_dir(pid)
    if os.path.exists(d):
        shutil.rmtree(d)
    return {"ok": True}

@app.put("/profiles/{pid}/name")
def rename_profile(pid: str, payload: dict):
    pid = _safe_pid(pid)
    meta = load_profiles_meta()
    for p in meta:
        if p["id"] == pid:
            p["name"] = payload.get("name", p["name"]).strip() or p["name"]
            save_profiles_meta(meta)
            return {"ok": True}
    raise HTTPException(status_code=404, detail="Profile not found")

# ─────────────────────────────────────────────────────────────────
# PROFILE DATA CRUD (profile-aware)
# ─────────────────────────────────────────────────────────────────
@app.get("/profile")
def get_profile(request: Request):
    return _enrich_profile_with_resume_sources(load_pdata(get_pid(request)))


@app.post("/knowledge/capture/propose")
def knowledge_capture_propose(request: Request, payload: CaptureProposeRequest):
    pid = get_pid(request)
    _ensure_profile_in_store(pid)
    return knowledge_capture.propose(pid, payload.raw_text, payload.source)


@app.post("/knowledge/capture/commit")
def knowledge_capture_commit(request: Request, payload: CaptureCommitRequest):
    pid = get_pid(request)
    _ensure_profile_in_store(pid)
    try:
        result = knowledge_capture.commit(pid, payload.event_id, payload.edited_delta)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _mirror_pdata_json(pid)
    return result


@app.get("/knowledge/skills/unrated")
def knowledge_list_unrated_skills(request: Request):
    pid = get_pid(request)
    _ensure_profile_in_store(pid)
    return {"skills": knowledge_rating.list_unrated(pid)}


@app.post("/knowledge/skills/{skill_id}/rate")
def knowledge_rate_skill(skill_id: int, request: Request, payload: SkillRatingRequest):
    pid = get_pid(request)
    _ensure_profile_in_store(pid)
    try:
        return knowledge_rating.set_rating(pid, skill_id, payload.proficiency, payload.evidence)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ── Knowledge memory browsing (dashboard Knowledge & Memory page) ────────────

@app.get("/knowledge/events")
def knowledge_list_events(request: Request, limit: int = 100):
    pid = get_pid(request)
    _ensure_profile_in_store(pid)
    return {"events": knowledge_client.list_events(pid, limit)}


@app.delete("/knowledge/events/{event_id}")
def knowledge_delete_event(event_id: int, request: Request):
    pid = get_pid(request)
    if not knowledge_client.delete_event(pid, event_id):
        raise HTTPException(status_code=404, detail=f"event {event_id} not found")
    return {"deleted": True, "event_id": event_id}


@app.get("/knowledge/skills")
def knowledge_list_skills(request: Request):
    pid = get_pid(request)
    _ensure_profile_in_store(pid)
    return {"skills": knowledge_client.list_all_skills(pid)}


@app.post("/knowledge/embed")
def knowledge_embed(request: Request):
    pid = get_pid(request)
    _ensure_profile_in_store(pid)
    return {"embedded": knowledge_client.embed_profile(pid)}


# ── Pipeline control (dashboard Job Sourcing page) ───────────────────────────
# Scrape/match run in daemon threads; one at a time per kind (rule 7: a failed
# run is recorded and surfaced, never silently swallowed).

_PIPELINE_LOCK = _threading.Lock()
_PIPELINE_STATE: dict = {
    "scrape": {"state": "idle", "started_at": None, "finished_at": None, "result": None, "error": None},
    "match": {"state": "idle", "started_at": None, "finished_at": None, "result": None, "error": None},
}


def _start_pipeline_job(kind: str, target) -> bool:
    with _PIPELINE_LOCK:
        if _PIPELINE_STATE[kind]["state"] == "running":
            return False
        _PIPELINE_STATE[kind] = {
            "state": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "result": None,
            "error": None,
        }

    def _worker():
        try:
            result = target()
            _PIPELINE_STATE[kind].update(
                state="done",
                finished_at=datetime.now(timezone.utc).isoformat(),
                result=result,
            )
        except Exception as exc:  # noqa: BLE001
            log.error(f"pipeline {kind} failed: {exc}")
            _PIPELINE_STATE[kind].update(
                state="error",
                finished_at=datetime.now(timezone.utc).isoformat(),
                error=str(exc),
            )

    _threading.Thread(target=_worker, daemon=True, name=f"pipeline-{kind}").start()
    return True


@app.post("/pipeline/scrape")
def pipeline_scrape():
    try:
        from backend.scraper.run import execute_run
    except ImportError:
        from scraper.run import execute_run  # type: ignore
    if not _start_pipeline_job("scrape", execute_run):
        raise HTTPException(status_code=409, detail="A scrape run is already in progress")
    return {"started": True, "kind": "scrape"}


@app.post("/pipeline/match")
def pipeline_match(request: Request):
    pid = get_pid(request)
    try:
        from backend.matcher.run import run_pipeline
    except ImportError:
        from matcher.run import run_pipeline  # type: ignore

    def _match_then_tailor():
        # Match, then tailor the pending queue so every item reaches review already
        # tailored and can be approved without a separate step (mirrors the nightly flow).
        matched = run_pipeline(profile_id=pid)
        try:
            tailored = asyncio.run(tailor_pending_queue(pid))
        except Exception as exc:  # noqa: BLE001 — tailoring failure must not lose match results
            log.error(f"pipeline match: tailoring pass failed: {exc}")
            tailored = {"error": str(exc)}
        return {"match": matched, "tailor": tailored}

    if not _start_pipeline_job("match", _match_then_tailor):
        raise HTTPException(status_code=409, detail="A matcher run is already in progress")
    return {"started": True, "kind": "match", "profile_id": pid}


@app.get("/pipeline/status")
def pipeline_status():
    return _PIPELINE_STATE


@app.get("/jobs/stats")
def jobs_stats():
    try:
        from backend.scraper.store import stats as jobs_store_stats
    except ImportError:
        from scraper.store import stats as jobs_store_stats  # type: ignore
    return jobs_store_stats()


@app.put("/profile/contact")
def update_contact(request: Request, payload: dict):
    pid = get_pid(request)
    _ensure_profile_in_store(pid)
    if "contact_info" in payload:
        knowledge_client.merge_section(pid, "contact_info", payload["contact_info"])
    if "summary" in payload:
        knowledge_client.replace_section(pid, "summary", payload["summary"])
    _mirror_pdata_json(pid)
    return {"ok": True}

@app.put("/profile/autofill")
def update_autofill(request: Request, payload: dict):
    pid = get_pid(request)
    _ensure_profile_in_store(pid)
    if "autofill" in payload:
        knowledge_client.merge_section(pid, "autofill", payload["autofill"])
    _mirror_pdata_json(pid)
    return {"ok": True}

@app.put("/profile/experience")
def update_experience(request: Request, payload: dict):
    pid = get_pid(request)
    _ensure_profile_in_store(pid)
    data = load_pdata(pid)
    knowledge_client.replace_section(
        pid, "experience", payload.get("experience", data.get("experience", []))
    )
    _mirror_pdata_json(pid)
    return {"ok": True}

@app.put("/profile/education")
def update_education(request: Request, payload: dict):
    pid = get_pid(request)
    _ensure_profile_in_store(pid)
    data = load_pdata(pid)
    knowledge_client.replace_section(
        pid, "education", payload.get("education", data.get("education", []))
    )
    _mirror_pdata_json(pid)
    return {"ok": True}

@app.put("/profile/skills")
def update_skills(request: Request, payload: dict):
    pid = get_pid(request)
    _ensure_profile_in_store(pid)
    if "skills" in payload:
        knowledge_client.merge_section(pid, "skills", payload["skills"])
    _mirror_pdata_json(pid)
    return {"ok": True}

@app.put("/profile/answers")
def update_answers(request: Request, payload: dict):
    pid = get_pid(request)
    _ensure_profile_in_store(pid)
    if "common_answers" in payload:
        knowledge_client.merge_section(pid, "common_answers", payload["common_answers"])
    _mirror_pdata_json(pid)
    return {"ok": True}

# ─────────────────────────────────────────────────────────────────
# APPLICATIONS CRUD  (M6 tracker.db — profile-aware, own SQLite store)
# ─────────────────────────────────────────────────────────────────
def _ensure_tracker_migrated(pid: str) -> None:
    """One-time import of legacy applications.json into tracker.db (idempotent).

    migrate_from_json skips when the tracker already holds rows for the profile, so
    this is cheap to call on every request. The JSON file is left intact as a backup.
    """
    try:
        legacy = load_papps(pid)
        if legacy:
            tracker_store.migrate_from_json(pid, legacy)
    except Exception as e:  # never let migration break a read (rule 7)
        log.warning(f"[tracker] migration skipped for pid={pid}: {e}")

@app.get("/applications")
def get_applications(request: Request):
    pid = get_pid(request)
    _ensure_tracker_migrated(pid)
    return tracker_store.list_applications(pid)

@app.post("/applications")
def add_application(request: Request, payload: dict):
    pid = get_pid(request)
    _ensure_tracker_migrated(pid)
    entry = tracker_store.create_application(pid, {
        "company": payload.get("company", ""),
        "role": payload.get("role", ""),
        "platform": payload.get("platform", "Other"),
        "status": payload.get("status") or tracker_store.STATUS_APPLIED,
        "date_applied": payload.get("date_applied") or str(_date.today()),
        "salary": payload.get("salary", ""),
        "location": payload.get("location", ""),
        "url": payload.get("url", ""),
        "notes": payload.get("notes", ""),
        "band": payload.get("band"),
        "match_pct": payload.get("match_pct"),
        "resume_variant_id": payload.get("resume_variant_id"),
    })
    return entry

@app.patch("/applications/{app_id}")
def update_application(app_id: str, request: Request, payload: dict):
    pid = get_pid(request)
    try:
        updated = tracker_store.update_application(pid, app_id, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not updated:
        raise HTTPException(status_code=404, detail="Application not found")
    return updated

@app.delete("/applications/{app_id}")
def delete_application(app_id: str, request: Request):
    pid = get_pid(request)
    if not tracker_store.delete_application(pid, app_id):
        raise HTTPException(status_code=404, detail="Application not found")
    return {"ok": True}

# ─────────────────────────────────────────────────────────────────
# TRACKER — pacing release + outcome analytics (M6)
# ─────────────────────────────────────────────────────────────────
@app.post("/tracker/release")
def tracker_release(request: Request):
    """Pacing gate: promote approved rows to 'ready_to_apply' within human-scale caps
    (rule 11). Approved ≠ released; the human still clicks submit (rule 1)."""
    pid = get_pid(request)
    return tracker_pacing.release_ready(pid)

@app.get("/tracker/analytics")
def tracker_analytics(request: Request):
    pid = get_pid(request)
    _ensure_tracker_migrated(pid)
    return tracker_store.analytics(pid)

@app.get("/tracker/match")
def tracker_match_page(request: Request, host: str = "", url: str = "", company: str = ""):
    """Find the package that should drive autofill on the current page.

    Checks customized queue items first, then legacy ready_to_apply tracker rows.
    """
    pid = get_pid(request)
    _ensure_tracker_migrated(pid)

    # Customized queue items (primary path after M6 workflow fix).
    customized = matcher_store.list_customized(_matcher_db_path(), pid)
    queue_candidates = [
        {
            "id": f"queue:{it.get('id')}",
            "queue_match_id": it.get("id"),
            "company": it.get("company", ""),
            "role": it.get("title", ""),
            "url": it.get("apply_url", ""),
            "platform": it.get("source_ats", "") or "Other",
            "resume_variant_id": it.get("resume_variant_id"),
            "answers": {},
            "source": "queue",
        }
        for it in customized
    ]
    item = tracker_match.best_match(queue_candidates, host=host, url=url, company=company)
    if item:
        return {"match": item}

    ready = tracker_store.list_applications(pid, status=_STATUS_READY)
    item = tracker_match.best_match(ready, host=host, url=url, company=company)
    if not item:
        return {"match": None}
    return {"match": {
        "id": item.get("id"),
        "company": item.get("company", ""),
        "role": item.get("role", ""),
        "url": item.get("url", ""),
        "platform": item.get("platform", ""),
        "resume_variant_id": item.get("resume_variant_id"),
        "answers": item.get("answers") or {},
        "source": "tracker",
    }}

# ─────────────────────────────────────────────────────────────────
# REVIEW QUEUE (M6) — banded matches, tailored overnight, approved by human
# ─────────────────────────────────────────────────────────────────
def _queue_item_view(item: dict) -> dict:
    """Shape a matches.db row for the review UI (fit rationale + tailored edits)."""
    fit = item.get("fit") or {}
    tailored = item.get("tailored")
    return {
        "id": item.get("id"),
        "company": item.get("company", ""),
        "title": item.get("title", ""),
        "apply_url": item.get("apply_url", ""),
        "match_pct": item.get("match_pct"),
        "band": item.get("band"),
        "rationale": fit.get("rationale", ""),
        "matched_skills": fit.get("matched_skills", []),
        "missing_skills": fit.get("missing_skills", []),
        "best_projects": fit.get("best_projects", []),
        "tailor_status": item.get("tailor_status"),
        "tailor_error": item.get("tailor_error"),
        "review_status": item.get("review_status"),
        "resume_variant_id": item.get("resume_variant_id"),
        "edits": (tailored or {}).get("_edits", []) if isinstance(tailored, dict) else [],
        "summary_diff": (tailored or {}).get("summary_diff") if isinstance(tailored, dict) else None,
        "tailor_sections": (tailored or {}).get("_sections") if isinstance(tailored, dict) else None,
        "has_tailoring": isinstance(tailored, dict),
        # Hard, visible page-fit flag (FINDINGS_tailoring §5) — no longer a silent PDF warning.
        "page_fit": constraints_engine.page_fit_summary(
            (tailored or {}).get("_preflight") if isinstance(tailored, dict) else None
        ),
    }

@app.get("/queue")
def get_queue(request: Request, band: str = "", review_status: str = ""):
    """Banded review queue, Strong first (rule 10)."""
    pid = get_pid(request)
    items = matcher_store.list_queue(
        _matcher_db_path(), pid, band=band or None, review_status=review_status or None,
    )
    return {"items": [_queue_item_view(i) for i in items], "source": _matcher_db_path()}

@app.get("/queue/{match_id}")
def get_queue_item_detail(match_id: int, request: Request):
    """Full queue item including the tailored payload (for the diff editor)."""
    pid = get_pid(request)
    item = matcher_store.get_queue_item(_matcher_db_path(), pid, match_id)
    if not item:
        raise HTTPException(status_code=404, detail="Queue item not found")
    view = _queue_item_view(item)
    view["tailored"] = item.get("tailored")
    view["jd_text"] = item.get("jd_text", "")
    return view

async def _tailor_one_item(
    pid: str, item: dict, *, sequential_llm: bool = True, db_path: str | None = None
) -> dict:
    """Tailor a single queue item; raises on failure."""
    db = db_path or _matcher_db_path()
    result = await run_tailoring(
        pid, item.get("jd_text", "") or "",
        role=item.get("title", ""), company=item.get("company", ""),
        sequential_llm=sequential_llm,
    )
    matcher_store.set_tailoring(db, item["id"], status="tailored", tailored=result)
    return result


async def tailor_pending_queue(pid: str, db_path: str | None = None) -> dict:
    """Tailor every pending queue item for a profile. Per-item failure isolation:
    one bad item is recorded as tailor_status='failed' and never kills the run (rule 7).
    Shared by POST /queue/tailor and the nightly orchestrator."""
    db = db_path or _matcher_db_path()
    pending = matcher_store.list_pending_tailoring(db, pid)
    tailored = failed = 0
    for item in pending:
        try:
            await _tailor_one_item(pid, item, sequential_llm=True, db_path=db)
            tailored += 1
        except Exception as e:  # one bad item never kills the run
            log.error(f"[queue] tailoring failed for match {item.get('id')}: {e}", exc_info=True)
            matcher_store.set_tailoring(db, item["id"], status="failed", error=str(e))
            failed += 1
    return {"pending": len(pending), "tailored": tailored, "failed": failed}

@app.post("/queue/tailor")
async def tailor_queue(request: Request):
    """Tailor every pending queue item for this profile (manual trigger of the nightly
    step). Per-item failure isolation (rule 7)."""
    return await tailor_pending_queue(get_pid(request))


@app.post("/queue/{match_id}/tailor")
async def tailor_queue_item(match_id: int, request: Request):
    """Tailor a single queue item (used by per-card actions and approve fallback)."""
    pid = get_pid(request)
    db = _matcher_db_path()
    item = matcher_store.get_queue_item(db, pid, match_id)
    if not item:
        raise HTTPException(status_code=404, detail="Queue item not found")
    try:
        result = await _tailor_one_item(pid, item, sequential_llm=True, db_path=db)
        return {"ok": True, "match_id": match_id, "tailor_status": "tailored", "edits": result.get("_edits", [])}
    except Exception as e:
        log.error(f"[queue] single-item tailoring failed for match {match_id}: {e}", exc_info=True)
        matcher_store.set_tailoring(db, match_id, status="failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/queue/{match_id}/skip")
def skip_queue_item(match_id: int, request: Request):
    pid = get_pid(request)
    item = matcher_store.get_queue_item(_matcher_db_path(), pid, match_id)
    if not item:
        raise HTTPException(status_code=404, detail="Queue item not found")
    matcher_store.set_review_status(_matcher_db_path(), match_id, "skipped")
    return {"ok": True, "review_status": "skipped"}

@app.post("/queue/{match_id}/approve")
async def approve_queue_item(match_id: int, request: Request, payload: dict = None):
    """Approve reviewed edits: compile PDF, mark queue item customized.

    Stays in Review Queue until the human submits the application form (mark-applied).
    Does NOT create a tracker application row yet.
    """
    payload = payload or {}
    pid = get_pid(request)
    db = _matcher_db_path()
    item = matcher_store.get_queue_item(db, pid, match_id)
    if not item:
        raise HTTPException(status_code=404, detail="Queue item not found")

    if item.get("tailor_status") != "tailored":
        raise HTTPException(
            status_code=400,
            detail="Item has no tailoring to approve; run /queue/{id}/tailor first.",
        )

    company = item.get("company", "")
    title = item.get("title", "")

    # Dedupe / rejection-history guard (block unless force).
    verdict = tracker_dedupe.check(pid, company, title)
    if verdict["blocked"] and not payload.get("force"):
        raise HTTPException(status_code=409, detail={"error": "dedupe_block", "dedupe": verdict})

    variant_id = payload.get("variant_id")
    if not variant_id:
        tailored = payload.get("tailored") or item.get("tailored") or {}
        if not tailored:
            raise HTTPException(status_code=400, detail="Item has no tailoring to approve; run /queue/tailor first.")
        if payload.get("edits") is not None:
            tailored = {**tailored, "_edits": payload["edits"]}
        pdf_data = {
            **tailored,
            "_company": company,
            "_role": title,
            "_jd": item.get("jd_text", ""),
        }
        async with processing_lock:
            ctx = _render_compile_version(pid, pdf_data)
        result = ctx["result"]
        if not (result.success and ctx["variant_meta"]):
            raise HTTPException(status_code=500, detail={
                "error": "PDF generation failed during approval",
                "compile_result": result.to_dict(),
            })
        variant_id = ctx["variant_meta"]["id"]

    matcher_store.set_customized(db, match_id, resume_variant_id=variant_id)
    return {
        "ok": True,
        "variant_id": variant_id,
        "review_status": "customized",
        "dedupe": verdict,
    }


@app.post("/queue/{match_id}/mark-applied")
async def mark_queue_item_applied(match_id: int, request: Request, payload: dict = None):
    """After extension autofill + human submit: create tracker row as applied."""
    payload = payload or {}
    pid = get_pid(request)
    db = _matcher_db_path()
    item = matcher_store.get_queue_item(db, pid, match_id)
    if not item:
        raise HTTPException(status_code=404, detail="Queue item not found")
    if item.get("review_status") != "customized":
        raise HTTPException(status_code=400, detail="Item must be customized before marking applied.")

    company = item.get("company", "")
    title = item.get("title", "")
    variant_id = item.get("resume_variant_id") or payload.get("variant_id")
    if not variant_id:
        raise HTTPException(status_code=400, detail="No resume variant on this queue item.")

    verdict = tracker_dedupe.check(pid, company, title)
    if verdict["blocked"] and not payload.get("force"):
        raise HTTPException(status_code=409, detail={"error": "dedupe_block", "dedupe": verdict})

    application = tracker_store.create_application(pid, {
        "company": company,
        "role": title,
        "band": item.get("band"),
        "match_pct": item.get("match_pct"),
        "status": tracker_store.STATUS_APPLIED,
        "resume_variant_id": variant_id,
        "jd_text": item.get("jd_text", ""),
        "answers": payload.get("answers"),
        "match_ref": f"{item.get('source_ats','')}:{item.get('external_id','')}",
        "url": payload.get("url") or item.get("apply_url", ""),
        "platform": item.get("source_ats", "") or payload.get("platform") or "Other",
        "notes": payload.get("notes", ""),
    })
    matcher_store.set_review_status(db, match_id, "applied")
    return {"ok": True, "application": application, "variant_id": variant_id}

# ─────────────────────────────────────────────────────────────────
# MATCHER QUEUE (approved items for extension fill override)
# ─────────────────────────────────────────────────────────────────
@app.get("/matches/approved")
def get_approved_matches(limit: int = 50):
    items = _matcher_fetch_items(limit=max(10, limit * 3))
    approved = [i for i in items if i.get("approved")]
    approved.sort(key=lambda x: str(x.get("id", "")), reverse=True)
    out = []
    for item in approved[: max(1, min(limit, 200))]:
        out.append({
            "id": item.get("id"),
            "company": item.get("company", ""),
            "role": item.get("role", ""),
            "apply_url": item.get("apply_url", ""),
            "status": item.get("status", "approved"),
            "tailored_data": item.get("tailored_data", {}),
        })
    return {"items": out, "source": _matcher_db_path()}

@app.get("/matches/{match_id}")
def get_match_item(match_id: str):
    for item in _matcher_fetch_items(limit=500):
        if str(item.get("id")) == str(match_id):
            return {
                "id": item.get("id"),
                "company": item.get("company", ""),
                "role": item.get("role", ""),
                "apply_url": item.get("apply_url", ""),
                "status": item.get("status", ""),
                "approved": bool(item.get("approved")),
                "tailored_data": item.get("tailored_data", {}),
            }
    raise HTTPException(status_code=404, detail="Match not found")

@app.get("/test/greenhouse", response_class=HTMLResponse)
def test_greenhouse():
    with open(os.path.join(os.path.dirname(__file__), "test_greenhouse.html"), "r") as f:
        return f.read()

@app.get("/test/workday", response_class=HTMLResponse)
def test_workday():
    with open(os.path.join(os.path.dirname(__file__), "test_workday.html"), "r") as f:
        return f.read()

@app.get("/test/generic", response_class=HTMLResponse)
def test_generic():
    with open(os.path.join(os.path.dirname(__file__), "test_generic.html"), "r") as f:
        return f.read()

@app.get("/test/greenhouse-real", response_class=HTMLResponse)
def test_greenhouse_real():
    with open(os.path.join(os.path.dirname(__file__), "test_greenhouse_real.html"), "r") as f:
        return f.read()

@app.get("/test/lever", response_class=HTMLResponse)
def test_lever():
    with open(os.path.join(os.path.dirname(__file__), "test_lever.html"), "r") as f:
        return f.read()

@app.get("/test/bamboohr", response_class=HTMLResponse)
def test_bamboohr():
    with open(os.path.join(os.path.dirname(__file__), "test_bamboohr.html"), "r") as f:
        return f.read()

@app.get("/test/icims", response_class=HTMLResponse)
def test_icims():
    with open(os.path.join(os.path.dirname(__file__), "test_icims.html"), "r") as f:
        return f.read()

@app.get("/test/smartrecruiters", response_class=HTMLResponse)
def test_smartrecruiters():
    with open(os.path.join(os.path.dirname(__file__), "test_smartrecruiters.html"), "r") as f:
        return f.read()

@app.get("/test/linkedin", response_class=HTMLResponse)
def test_linkedin():
    with open(os.path.join(os.path.dirname(__file__), "test_linkedin.html"), "r") as f:
        return f.read()

@app.get("/test/taleo", response_class=HTMLResponse)
def test_taleo():
    with open(os.path.join(os.path.dirname(__file__), "test_taleo.html"), "r") as f:
        return f.read()

# ─────────────────────────────────────────────────────────────────
# ENDPOINT 1: ANALYZE
# ─────────────────────────────────────────────────────────────────
@app.post("/analyze")
async def analyze_job(req: JobRequest, request: Request):
    pid = get_pid(request)
    log_event(log, "INFO", "request", endpoint="POST /analyze", pid=pid,
              jd_len=len(req.jd_text), llm=req.llm)
    async with processing_lock:
        user_data = load_pdata(pid)
        prompt = f"""You are a Career Strategist.
CANDIDATE PROFILE: {json.dumps(user_data)}
JOB DESCRIPTION: "{req.jd_text[:7000]}"

TASK:
1. Identify the Job Role.
2. List at least 3 key skills from the JD that the candidate matches.
3. List at least 1 missing skill (gap).
4. Calculate a Match Score (0-100%).
5. Write a tailored summary (2-3 sentences) in IMPLIED FIRST PERSON (no pronouns, no name).
6. Select the 3 most relevant projects from the candidate's list (exact titles).

OUTPUT JSON ONLY:
{{"role":"...","skills_matched":[],"missing_skill":"...","score":"...","tailored_summary":"...","selected_projects":[]}}"""
        try:
            content = call_llm([{"role": "user", "content": prompt}],
                               temperature=0.2, prefer=req.llm)
            result = json.loads(clean_json(content))
            log_event(log, "INFO", "analyze_ok", pid=pid, score=result.get("score"),
                      role=result.get("role","")[:40])
            return result
        except Exception as e:
            log.error(f"POST /analyze failed — pid={pid}: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

# ─────────────────────────────────────────────────────────────────
# ENDPOINT 2: SUGGEST QUESTIONS
# ─────────────────────────────────────────────────────────────────
@app.post("/suggest-questions")
async def suggest_questions(req: JobRequest, request: Request):
    log_event(log, "INFO", "request", endpoint="POST /suggest-questions",
              pid=get_pid(request), jd_len=len(req.jd_text), llm=req.llm)
    async with processing_lock:
        prompt = f"""Analyze this job posting:
"{req.jd_text[:7000]}"

Generate 3 short, specific questions a candidate should ask about this role.
OUTPUT JSON LIST ONLY: ["Question 1", "Question 2", "Question 3"]"""
        try:
            content = call_llm([{"role": "user", "content": prompt}],
                               temperature=0.4, prefer=req.llm)
            return json.loads(clean_json(content))
        except Exception:
            return ["What is the expected tech stack?", "Is sponsorship available?", "What is the salary range?"]

# ─────────────────────────────────────────────────────────────────
# ENDPOINT 3: CHAT
# ─────────────────────────────────────────────────────────────────
@app.post("/chat")
async def chat_with_page(request: ChatRequest):
    log_event(log, "INFO", "request", endpoint="POST /chat",
              context_len=len(request.context), history_turns=len(request.history), llm=request.llm)
    async with processing_lock:
        system = f"""You are a helpful assistant. Answer the user's question based ONLY on the following webpage context. If the answer isn't in the context, say "I couldn't find that info on this page."

WEBPAGE CONTEXT:
{request.context[:3000]}"""
        messages = []
        if request.history:
            messages.extend(request.history)
        messages.append({"role": "user", "content": request.question})
        try:
            answer = call_llm(messages, temperature=0.3, system=system, prefer=request.llm)
            return {"answer": answer}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

# ─────────────────────────────────────────────────────────────────
# ENDPOINT 4: AUTOFILL
# ─────────────────────────────────────────────────────────────────
class AutofillRequest2(BaseModel):
    fields: list
    jd_text: str = ""
    company: str = ""
    host: str = ""
    llm: str = "ollama"
    profile_override: dict = Field(default_factory=dict)

def _build_compact_profile(user_data: dict) -> dict:
    """Compact profile for LLM — avoids truncation losing skills/education."""
    contact = user_data.get("contact_info", {})
    autofill = user_data.get("autofill", {})
    all_skills = []
    for items in (user_data.get("skills") or {}).values():
        if isinstance(items, list): all_skills.extend(items)
    return {
        "name": contact.get("name",""), "title": autofill.get("current_title",""),
        "location": contact.get("location",""), "email": contact.get("email",""),
        "phone": contact.get("phone",""), "linkedin": contact.get("linkedin",""),
        "summary": user_data.get("summary","")[:400],
        "experience": [{"company":e.get("company",""),"title":e.get("title",""),
            "dates":f"{e.get('start_date','')}–{e.get('end_date','Present')}",
            "highlights":(e.get("bullets") or [])[:3]}
            for e in (user_data.get("experience") or [])[:3]],
        "education": [{"school":e.get("university",e.get("school","")),"degree":e.get("degree",""),
            "field":e.get("field",e.get("major","")),"gpa":e.get("gpa",""),
            "year":e.get("graduation",e.get("end_date",""))}
            for e in (user_data.get("education") or [])[:2]],
        "skills": all_skills[:60],
        "autofill": autofill,
    }

@app.post("/autofill")
async def autofill_fields(req: AutofillRequest2, request: Request):
    pid = get_pid(request)
    log_event(log, "INFO", "request", endpoint="POST /autofill", pid=pid,
              fields=len(req.fields), host=req.host or "?", llm=req.llm)
    # Phase 0 + 1 need no lock — pure Python, no I/O bottleneck
    user_data = load_pdata(get_pid(request))
    if isinstance(req.profile_override, dict) and req.profile_override:
        user_data = _deep_merge_dict(user_data, req.profile_override)
    autofill = user_data.get("autofill", {})
    learned = user_data.get("learned_answers", {})

    # Phase 0: per-host learned answers (highest priority)
    host_answers = {}
    if req.host:
        prefix = req.host + "::"
        for k, v in learned.items():
            if k.startswith(prefix):
                label = k[len(prefix):]
                host_answers[label] = v

    # Phase 1: instant rule-based pass (no LLM)
    base_answers = build_rule_based_answers(req.fields, autofill, user_data)
    # Merge: rules first, learned overrides (because user explicitly corrected)
    for f in req.fields:
        label = f.get("label", "").strip().lower()
        if label in host_answers:
            base_answers[f.get("label", "")] = host_answers[label]

    # Answers we're confident about = rule-based/learned hits that aren't blank or "SKIP".
    def _grounded(v):
        return bool(v) and str(v).strip().upper() != "SKIP"

    answers = {label: val for label, val in base_answers.items() if _grounded(val)}

    # Phase 2: fields rule-based couldn't ground — try the LLM, but ONLY let it answer
    # when it can cite the candidate's own data. Anything left goes back to the user.
    # Skip sensitive fields entirely (never ask the LLM about health/legal/political).
    custom_fields = [
        f for f in req.fields[:40]
        if not _grounded(base_answers.get(f.get("label", "")))
        and not f.get("sensitive")
    ]

    def _unanswered_payload(fields):
        out = []
        for f in fields:
            out.append({
                "label": f.get("label", ""),
                "type": f.get("type", "text"),
                "options": f.get("options", []),
            })
        return out

    def _hybrid(answers_map, unanswered_list):
        # Nested keys for the new extension, flat {label: value} echoed at the top
        # level so an older content.js (pre-reload) keeps filling instead of
        # silently matching nothing.
        return {**answers_map, "answers": answers_map, "unanswered": unanswered_list}

    if not custom_fields:
        return _hybrid(answers, [])

    field_list = json.dumps(custom_fields, indent=2)
    prompt = f"""You are an expert job application assistant filling out a form on behalf of a candidate. Answer each field ONLY when the answer is grounded in the candidate's own profile data below — never invent facts the candidate did not provide.

CANDIDATE PROFILE (full source of truth):
{json.dumps(user_data, indent=2)[:5500]}

AUTOFILL QUICK REFERENCE (canonical values):
{json.dumps(autofill, indent=2)}

JOB DESCRIPTION: {req.jd_text[:1200]}
COMPANY: {req.company}

FORM FIELDS TO ANSWER:
{field_list}

ANSWERING STRATEGY (apply in order for each field):
1. If the label is a synonym/paraphrase of an autofill key (e.g. "Mailing Address" ≈ address_line1, "Cell" ≈ phone, "Earliest start" ≈ start_date) → return the matching autofill value verbatim.
2. If the label is a date field → return today's date in MM/DD/YYYY.
3. If it's a percentage/numeric question (travel %, salary, years) and the profile supports a value → use it.
4. If it's an open-ended written question (e.g. "Why this role?") and the profile has relevant experience → write 2-3 sentences in implied first person citing that real experience.
5. Return "SKIP" whenever the field asks for information the candidate did NOT provide (a specific preference, an opinion, an employee ID, a number the profile doesn't contain, a free-text answer with no supporting experience). Do NOT guess. A field the candidate must decide belongs to them, not you.

OUTPUT: JSON object where keys are EXACTLY the "label" values shown above and values are the grounded answer or "SKIP". OUTPUT JSON ONLY."""

    try:
        content = call_llm([{"role": "user", "content": prompt}],
                           temperature=0.1, prefer=req.llm, timeout=600)
        llm_answers = json.loads(clean_json(content))
    except Exception as e:
        log.warning(f"Autofill LLM failed — returning rule-based answers only. Error: {e}")
        # Everything the rules couldn't ground goes back to the user.
        return _hybrid(answers, _unanswered_payload(custom_fields))

    # Fold in only the grounded LLM answers; whatever it declined (SKIP/blank/missing)
    # becomes an explicit "your call" question for the user (rule 2: no silent invention).
    unanswered = []
    for f in custom_fields:
        label = f.get("label", "")
        val = llm_answers.get(label)
        if _grounded(val):
            answers[label] = str(val)
        else:
            unanswered.append({
                "label": label,
                "type": f.get("type", "text"),
                "options": f.get("options", []),
            })
    log_event(log, "INFO", "autofill_ok", answered=len(answers), unanswered=len(unanswered))
    return _hybrid(answers, unanswered)

# ─────────────────────────────────────────────────────────────────
# ENDPOINT 5: ANSWER SINGLE QUESTION
# ─────────────────────────────────────────────────────────────────
@app.post("/answer-question")
async def answer_question(req: QuestionRequest, request: Request):
    log_event(log, "INFO", "request", endpoint="POST /answer-question",
              pid=get_pid(request), company=req.company or "?", llm=req.llm)
    async with processing_lock:
        user_data = _enrich_profile_with_resume_sources(load_pdata(get_pid(request)))
        prompt = f"""You are an expert job application assistant answering a question on behalf of the candidate.

CANDIDATE PROFILE:
{json.dumps(user_data, indent=2)[:5000]}

JOB DESCRIPTION: {req.jd_text[:2000]}
COMPANY: {req.company}

QUESTION: "{req.question}"

INSTRUCTIONS:
- Write in implied first person (e.g. "Experienced in...", "With 2 years of...")
- DO NOT use pronouns (I, He, She) — implied first person only
- Reference real candidate details
- Approximately {req.word_limit} words
- Output ONLY the answer text, no preamble."""

        try:
            content = call_llm([{"role": "user", "content": prompt}],
                               temperature=0.3, prefer=req.llm)
            return {"answer": content.strip()}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

# ─────────────────────────────────────────────────────────────────
# ENDPOINT 6: COVER LETTER
# ─────────────────────────────────────────────────────────────────
@app.post("/cover-letter")
async def generate_cover_letter(req: CoverLetterRequest, request: Request):
    log_event(log, "INFO", "request", endpoint="POST /cover-letter",
              pid=get_pid(request), company=req.company or "?", role=req.role or "?", llm=req.llm)
    async with processing_lock:
        user_data = load_pdata(get_pid(request))
        contact = user_data.get("contact_info", {})
        today = __import__("datetime").date.today().strftime("%B %d, %Y")

        prompt = f"""You are an expert career coach writing a compelling cover letter.

CANDIDATE PROFILE:
{json.dumps(user_data, indent=2)[:5000]}

TARGET COMPANY: {req.company}
TARGET ROLE: {req.role}
HIRING MANAGER: {req.hiring_manager or "Hiring Manager"}
JOB DESCRIPTION:
{req.jd_text[:3000]}

INSTRUCTIONS:
- Write 3-4 paragraphs, 280-350 words total
- Opening: Engaging first line (no "I am writing to express...")
- Body 1: 2-3 most relevant technical skills/experiences
- Body 2: Specific project or measurable achievement
- Closing: Enthusiasm + call to action
- Tone: Professional but personable
- Reference what makes {req.company} specifically interesting
- Use the candidate's REAL name, contact info, and experiences
- Output ONLY the cover letter text, no explanation."""

        try:
            letter = call_llm([{"role": "user", "content": prompt}],
                              temperature=0.5, prefer=req.llm, timeout=600)
            return {
                "cover_letter": letter.strip(),
                "metadata": {
                    "company": req.company,
                    "role": req.role,
                    "candidate": contact.get("name", ""),
                    "generated_date": today
                }
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

# ─────────────────────────────────────────────────────────────────
# ENDPOINT 6b: DEEP ANALYZE — categorized skills, summary, role
# ─────────────────────────────────────────────────────────────────
class DeepAnalyzeRequest(BaseModel):
    jd_text: str
    company: str = ""
    role: str = ""
    llm: str = "ollama"

@app.post("/analyze-deep")
async def analyze_deep(req: DeepAnalyzeRequest, request: Request):
    pid = get_pid(request)
    log_event(log, "INFO", "request", endpoint="POST /analyze-deep", pid=pid,
              jd_len=len(req.jd_text), company=req.company or "?", llm=req.llm)
    async with processing_lock:
        user_data = load_pdata(get_pid(request))
        # Pull all candidate skills (flat list)
        all_skills = []
        for cat, items in (user_data.get("skills") or {}).items():
            if isinstance(items, list):
                all_skills.extend(items)

        candidate_skills_text = ", ".join(all_skills[:120]).lower()
        # Pre-extract company from JD text with regex before asking LLM
        def _extract_company(text: str, hint: str = "") -> str:
            if hint and hint.strip() and len(hint.strip()) < 80:
                return hint.strip()
            # Common patterns: "at CompanyName", "Company: X", first capitalised org
            m = (re.search(r'\bat\s+([A-Z][A-Za-z0-9\s&,\.]+?)(?:\.|,|\n|$)', text[:600]) or
                 re.search(r'Company[:\s]+([A-Z][A-Za-z0-9\s&,\.]+?)(?:\.|,|\n|$)', text[:600]))
            return m.group(1).strip()[:60] if m else ""
        inferred_company = _extract_company(req.jd_text, req.company)

        prompt = f"""You are a senior technical recruiter parsing a SPECIFIC job description.

YOUR ONLY SOURCES OF TRUTH:
1. THE JOB DESCRIPTION below — this is where ALL skills you list must come from.
2. THE CANDIDATE PROFILE below — used ONLY to compute the "matched" boolean for each JD skill.

DO NOT invent skills. DO NOT pad lists. DO NOT list the candidate's full skill catalog. Only return skills that are LITERALLY mentioned (or clearly implied) in the JD text.

═══ JOB DESCRIPTION ═══
{req.jd_text[:6000]}
═══════════════════════

INFERRED COMPANY (pre-extracted, use this if JD doesn't clearly state one): {inferred_company}
CANDIDATE TITLE: {user_data.get('autofill',{}).get('current_title','')}
CANDIDATE SUMMARY: {user_data.get('summary','')[:400]}
CANDIDATE SKILLS LIST (just for the "matched" flag): {candidate_skills_text[:1500]}

OUTPUT EXACTLY THIS JSON SHAPE — no extra keys, no commentary:
{{
  "role": "Exact job title from JD header",
  "company": "Company name from JD — use INFERRED COMPANY above if the JD body doesn't explicitly state the company name",
  "level": "Intern|Entry|Mid|Senior|Staff|Manager",
  "summary": "Plain 2-3 sentence summary of what the role does, in your own words",
  "responsibilities": ["3-5 concise bullets, each <=15 words, taken from the JD"],
  "must_have_skills":   [array of 4-8 items],
  "nice_to_have_skills":[array of 0-5 items],
  "keywords": [array of 8-12 ATS keywords lifted from the JD],
  "match_score": <integer 0-100>,
  "gaps": [array of 0-4 short phrases of what the candidate lacks],
  "recommendations": [array of 0-3 short phrases of skills candidate likely has but should add]
}}

Each skill object: {{"skill":"<short name>", "matched":<true if the skill word/phrase appears anywhere in CANDIDATE SKILLS LIST or CANDIDATE TITLE/SUMMARY, case-insensitive — else false>}}

HARD RULES:
- must_have_skills: ONLY skills that the JD lists as REQUIRED. Cap at 8.
- nice_to_have_skills: ONLY skills the JD calls "plus", "preferred", "nice to have", "bonus". Cap at 5.
- Do NOT return more than 8 must-haves under any circumstance.
- Each skill name must be 1-4 words max. e.g. "AWS" not "Familiarity with AWS/Azure cloud platforms".
- If the JD doesn't mention a category, return an empty list — do NOT pad with generic skills.
- match_score = round(100 * matched_must_have_count / total_must_have_count)."""
        try:
            content = call_llm([{"role": "user", "content": prompt}],
                               temperature=0.1, prefer=req.llm)
            result = json.loads(clean_json(content))

            # Post-validation: drop any skill not literally found in the JD text.
            # Small LLMs frequently fabricate "Python", "SQL", etc. as nice-to-haves.
            jd_lower = req.jd_text.lower()
            def skill_in_jd(skill_obj):
                s = (skill_obj.get("skill") or "").strip()
                if not s or len(s) > 60: return False
                # Match if any whole word/phrase of the skill appears in the JD
                tokens = re.split(r"[/,\s]+", s.lower())
                for t in tokens:
                    if len(t) >= 3 and t in jd_lower:
                        return True
                return s.lower() in jd_lower
            # Deterministic matching: synonym-aware lookup against candidate's profile text
            cand_haystack = (
                candidate_skills_text + " " +
                (user_data.get("autofill",{}).get("current_title","") or "") + " " +
                (user_data.get("summary","") or "")
            ).lower()
            SYNONYMS = {
                "ml/dl": ["machine learning", "deep learning", "ml", "dl", "neural"],
                "llm/rag/fine-tuning": ["llm", "rag", "fine-tuning", "fine tuning", "language model"],
                "aws/azure cloud": ["aws", "azure", "cloud", "ec2", "s3"],
                "large data sets": ["big data", "data sets", "dataset", "data pipeline", "etl"],
                "vector embeddings/databases": ["vector", "embedding", "pinecone", "chroma", "weaviate", "pgvector", "qdrant"],
                "ml pipelines": ["pipeline", "mlops", "airflow", "kubeflow"],
                "advanced degree": ["m.s", "master", "ms ", "phd", "ph.d", "doctorate"],
            }
            def is_matched(skill_name) -> bool:
                if isinstance(skill_name, dict):
                    skill_name = skill_name.get("skill", "")
                s = str(skill_name or "").lower().strip()
                if not s: return False
                # direct token check
                for tok in re.split(r"[/,\s]+", s):
                    if len(tok) >= 3 and tok in cand_haystack:
                        return True
                # synonym fallback
                for syns in SYNONYMS.values():
                    if s in syns or any(syn in s for syn in syns):
                        if any(syn in cand_haystack for syn in syns):
                            return True
                return False
            for s in result.get("must_have_skills", []):
                s["matched"] = is_matched(s.get("skill",""))
            for s in result.get("nice_to_have_skills", []):
                s["matched"] = is_matched(s.get("skill",""))
            result["must_have_skills"] = [s for s in result.get("must_have_skills", []) if skill_in_jd(s)][:8]
            result["nice_to_have_skills"] = [s for s in result.get("nice_to_have_skills", []) if skill_in_jd(s)][:5]
            # Recompute match_score from validated must-haves
            mh = result["must_have_skills"]
            if mh:
                result["match_score"] = round(100 * sum(1 for s in mh if s.get("matched")) / len(mh))
            # Fallback: if LLM returned empty/generic company, use inferred
            if not result.get("company") or len(result.get("company","")) < 3:
                result["company"] = inferred_company or req.company or ""
            result["jd_extracted"] = req.jd_text[:4000]

            # Split keywords into covered (already in profile) vs missing (gaps to fill)
            keywords = result.get("keywords") or []
            result["keywords_covered"] = [kw for kw in keywords if is_matched(kw)]
            result["keywords_missing"] = [kw for kw in keywords if not is_matched(kw)]
            return result
        except Exception as e:
            log.error(f"POST /analyze-deep failed — pid={pid}: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

# ─────────────────────────────────────────────────────────────────
# ENDPOINT 6c: TAILOR RESUME — per-section diff
# ─────────────────────────────────────────────────────────────────
class TailorResumeRequest(BaseModel):
    jd_text: str
    role: str = ""
    company: str = ""
    selected_skills: list = []   # skills the user opted to emphasize
    selected_projects: list = []
    user_instruction: str = ""
    llm: str = "ollama"

def _build_style_fingerprint(user_data: dict) -> dict:
    """Compute style metrics from the candidate's actual resume bullets — feeds into prompts as constraint."""
    bullets = []
    for e in user_data.get("experience", []):
        bullets.extend(e.get("details") or e.get("bullets") or [])
    if not bullets:
        return {"median_words": 15, "max_words": 25, "starts_with_verb_pct": 90, "metric_pct": 50}

    word_counts = [len(re.findall(r"\b[\w'-]+\b", b)) for b in bullets if b]
    verb_starts = sum(1 for b in bullets if re.match(r"^[A-Z][a-z]+(ed|ing)?\b", b or ""))
    has_metric = sum(1 for b in bullets if re.search(r"\b\d{1,3}(\.\d+)?%?\b", b or ""))
    return {
        "median_words": sorted(word_counts)[len(word_counts)//2] if word_counts else 15,
        "max_words": max(word_counts) if word_counts else 25,
        "starts_with_verb_pct": round(100 * verb_starts / len(bullets)),
        "metric_pct": round(100 * has_metric / len(bullets)),
        "sample_bullets": bullets[:2],
    }


def _skill_base(s: str) -> str:
    """Normalize a skill name for dedup/comparison.

    'Python (Expert)' → 'python'
    'PostgreSQL (pgvector)' → 'postgresql'
    'Machine Learning' → 'machine learning'
    """
    return re.sub(r'\s*\(.*?\)', '', s).lower().strip()


def _rank_projects_for_jd(project_library: list, jd_text: str,
                          hint_titles: list = None, n: int = 3,
                          priority_keywords: list = None) -> list:
    """Score every project against the JD via keyword overlap and return top-n.

    Generic — no profile-specific hardcoding.
    priority_keywords: loaded from resume_config.json, empty list by default.
      If a project's corpus contains any of these, it gets +3 per match.
    hint_titles: explicit user selections → always included first.
    """
    jd_lower = jd_text.lower()
    hint_titles = [t.strip() for t in (hint_titles or []) if (t or "").strip()]
    priority_kw = [kw.lower() for kw in (priority_keywords or [])]
    if hint_titles:
        matched = []
        used = set()
        for hint in hint_titles:
            hint_lower = hint.lower().strip()
            for proj in project_library:
                title_lower = (proj.get("title") or "").lower().strip()
                if not title_lower or title_lower in used:
                    continue
                if hint_lower == title_lower or hint_lower in title_lower or title_lower in hint_lower:
                    matched.append(proj)
                    used.add(title_lower)
                    break
        if matched:
            return matched[:n]
    scored = []
    for proj in project_library:
        title_lower = (proj.get("title") or "").lower().strip()
        tech_text = " ".join(proj.get("tech_stack") or [])
        url_text  = (proj.get("url") or "").lower()
        corpus = title_lower + " " + tech_text.lower() + " " + (proj.get("description") or "").lower() + " " + url_text
        tokens = re.findall(r"\b[a-z][a-z0-9_-]{2,}\b", corpus)
        # JD keyword overlap (primary score)
        score = sum(1 for t in tokens if t in jd_lower)
        # Multi-word JD phrases (stronger signal than single tokens)
        for a, b in re.findall(r"\b([a-z]{3,})\s+([a-z]{3,})\b", jd_lower):
            phrase = f"{a} {b}"
            if phrase in corpus:
                score += 3
        # Profile-defined priority keywords (loaded from resume_config.json)
        for pkw in priority_kw:
            if pkw in corpus:
                score += 3
        scored.append((score, proj))
    scored.sort(key=lambda x: -x[0])
    return [p for _, p in scored[:n]]


def _ensure_project_bullets(projects: list) -> list:
    """Ensure each project has up to 2 bullets (from LaTeX source or --- split description)."""
    for p in projects or []:
        if p.get("bullets"):
            p["bullets"] = [b for b in p["bullets"] if (b or "").strip()][:2]
            continue
        desc = (p.get("description") or "").strip()
        if " --- " in desc:
            p["bullets"] = [x.strip() for x in desc.split(" --- ") if x.strip()][:2]
        elif desc:
            p["bullets"] = [desc]
        else:
            p["bullets"] = []
    return projects


def _trim_skills_lists(
    skills: dict,
    jd_text: str,
    selected_skill_names: list | None = None,
    max_per_category: int = 11,
) -> tuple[dict, list]:
    """Trim skill lists for one-page PDF; drop lowest-priority items. Returns (trimmed, removed)."""
    jd_lower = (jd_text or "").lower()
    selected_set = {_skill_base(s) for s in (selected_skill_names or [])}
    removed: list[str] = []
    out: dict = {}

    for key, items in (skills or {}).items():
        if not isinstance(items, list):
            out[key] = items
            continue
        scored: list[tuple[int, int, str]] = []
        for idx, s in enumerate(items):
            if not (s or "").strip():
                continue
            base = _skill_base(s)
            score = 0
            if base in selected_set:
                score += 12
            if base in jd_lower:
                score += 8
            for token in re.split(r"[/,\s]+", s.lower()):
                if len(token) >= 3 and token in jd_lower:
                    score += 4
                    break
            scored.append((score, idx, str(s).strip()))
        scored.sort(key=lambda x: (-x[0], x[1]))
        kept = [s for _, _, s in scored[:max_per_category]]
        for _, _, s in scored[max_per_category:]:
            removed.append(s)
        out[key] = kept
    return out, removed


def _assemble_tailored_skills(master_skills: dict, jd_text: str,
                              selected_skill_names: list = None,
                              jd_additions_map: dict = None) -> dict:
    """Build the skills block for the resume (generic — works with any profile).

    Strategy:
      - Detect skill categories from whatever keys exist in master_skills.
      - Promote selected_skills to front of the appropriate category.
      - Surface JD-relevant keywords not already present (from jd_additions_map
        loaded from resume_config.json, NOT hardcoded here).
    Returns a dict matching master_skills category keys.
    """
    import copy
    skills = copy.deepcopy(master_skills)
    jd_lower = jd_text.lower()

    # Derive display keys from master profile (generic — any set of categories)
    display_keys = [k for k in skills if isinstance(skills[k], list)]

    # Promote selected_skills to front using normalized comparison (_skill_base strips parentheticals)
    selected_set = {_skill_base(s) for s in (selected_skill_names or [])}
    for key in display_keys:
        cat = skills.get(key) or []
        front = [s for s in cat if _skill_base(s) in selected_set]
        rest  = [s for s in cat if _skill_base(s) not in selected_set]
        skills[key] = front + rest

    # Surface JD-relevant extras using profile-defined keyword map
    if jd_additions_map:
        all_existing = {_skill_base(s) for key in display_keys for s in (skills.get(key) or [])}
        for key, candidates in jd_additions_map.items():
            if key not in display_keys:
                continue
            for kw in candidates:
                if kw.lower() in jd_lower and _skill_base(kw) not in all_existing:
                    display_label = kw.capitalize() if key == "languages" else kw
                    skills.setdefault(key, []).append(display_label)
                    all_existing.add(_skill_base(kw))

    # Add selected skills that are genuinely NEW (not in any category after normalization)
    all_existing_norm = {_skill_base(s) for key in display_keys for s in (skills.get(key) or [])}
    for skill_name in (selected_skill_names or []):
        base = _skill_base(skill_name)
        if base in all_existing_norm:
            continue  # already present (after normalising away parentheticals)
        # Try to place in the most fitting category via jd_additions_map
        placed = False
        if jd_additions_map:
            for key, candidates in jd_additions_map.items():
                if key not in display_keys:
                    continue
                if any(_skill_base(c) == base or base in c.lower() or c.lower() in base for c in candidates):
                    skills.setdefault(key, []).append(skill_name)
                    all_existing_norm.add(base)
                    placed = True
                    break
        if not placed and display_keys:
            # Default: first category (typically domains/LLMs)
            skills.setdefault(display_keys[0], []).append(skill_name)
            all_existing_norm.add(base)

    return {k: skills.get(k) or [] for k in display_keys}


def _build_grounded_edits(
    *,
    pid: str,
    jd_text: str,
    master: dict,
    tailored: dict,
) -> list[dict]:
    """Create additive _edits with evidence grounding and review statuses."""
    edits: list[dict] = []

    # Summary edit
    before_summary = (master.get("summary") or "").strip()
    after_summary = (
        tailored.get("tailored_summary")
        or (tailored.get("summary_diff") or {}).get("tailored")
        or ""
    ).strip()
    if after_summary and after_summary != before_summary:
        edits.append(
            {
                "section": "summary",
                "field": "summary",
                "before": before_summary,
                "after": after_summary,
                "reason": "Summary aligned to JD terms",
                "evidence_ref": None,
                "confidence": 0.8,
                "status": "accepted",
            }
        )

    # Experience bullet edits
    master_exp = master.get("experience") or []
    for e_idx, exp in enumerate(tailored.get("experience") or []):
        old_bullets = []
        if e_idx < len(master_exp):
            old_bullets = (master_exp[e_idx].get("details") or master_exp[e_idx].get("bullets") or [])
        for b_idx, bullet in enumerate(exp.get("bullets") or []):
            new_text = (bullet.get("text") or "").strip()
            old_text = (bullet.get("original") or (old_bullets[b_idx] if b_idx < len(old_bullets) else "")).strip()
            if not new_text:
                continue
            if new_text == old_text:
                continue
            edits.append(
                {
                    "section": "experience",
                    "field": f"experience.{e_idx}.bullets.{b_idx}",
                    "before": old_text,
                    "after": new_text,
                    "reason": "Bullet rewritten for JD relevance",
                    "evidence_ref": None,
                    "confidence": 0.78,
                    "status": "accepted",
                }
            )

    # Skills category-level edits
    master_skills = master.get("skills") or {}
    tailored_skills = tailored.get("tailored_skills") or {}
    for key, new_items in tailored_skills.items():
        if not isinstance(new_items, list):
            continue
        old_items = master_skills.get(key) or []
        if old_items == new_items:
            continue
        edits.append(
            {
                "section": "skills",
                "field": f"tailored_skills.{key}",
                "before": ", ".join(old_items),
                "after": ", ".join(new_items),
                "reason": "Skills reordered/expanded for JD",
                "evidence_ref": None,
                "confidence": 0.74,
                "status": "accepted",
            }
        )

    grounded: list[dict] = []
    for e in edits:
        grounded.append(
            tailor_edits.ground_edit(
                e,
                pid=pid,
                jd_text=jd_text,
                knowledge_search=knowledge_semantic.search,
            )
        )
    return tailor_edits.validate_edits(grounded)


@app.post("/tailor-resume")
async def tailor_resume(req: TailorResumeRequest, request: Request):
    pid = get_pid(request)
    log_event(log, "INFO", "request", endpoint="POST /tailor-resume", pid=pid,
              jd_len=len(req.jd_text), role=req.role or "?", company=req.company or "?",
              selected_skills=len(req.selected_skills), llm=req.llm)
    return await run_tailoring(
        pid, req.jd_text, role=req.role, company=req.company,
        selected_skills=req.selected_skills, selected_projects=req.selected_projects,
        user_instruction=req.user_instruction, llm=req.llm,
    )


async def run_tailoring(
    pid: str,
    jd_text: str,
    role: str = "",
    company: str = "",
    selected_skills: list | None = None,
    selected_projects: list | None = None,
    user_instruction: str = "",
    llm: str = "",
    sequential_llm: bool = False,
) -> dict:
    """Core resume-tailoring pipeline shared by POST /tailor-resume and the nightly
    queue run (M6). Emits grounded `_edits` (evidence rule 2) and passes untrusted LLM
    output through sanitize_untrusted_text (rule 6). LLM access stays via call_llm (rule 9).
    """
    from types import SimpleNamespace
    req = SimpleNamespace(
        jd_text=jd_text, role=role or "", company=company or "",
        selected_skills=selected_skills or [], selected_projects=selected_projects or [],
        user_instruction=user_instruction or "", llm=llm or "",
    )
    async with processing_lock:
        user_data = _enrich_profile_with_resume_sources(load_pdata(pid))
        style = _build_style_fingerprint(user_data)
        bundle = resume_source.build_resume_source_bundle()
        project_library = user_data.get("project_library", user_data.get("projects", []))
        master_skills   = user_data.get("skills", {})

        # ── STEP 1: Load profile config, then deterministic project + skills ──
        cfg = load_profile_config(pid)
        resume_cfg = cfg.get("resume", {})
        active_model = req.llm or cfg.get("preferred_model", "ollama")

        # Exclude publications from project ranking — they appear in the frozen Research section
        pub_titles_excl = {p.get("title","").lower().strip() for p in user_data.get("publications", [])}
        project_pool = [p for p in project_library if p.get("title","").lower().strip() not in pub_titles_excl]

        selected_projects = _rank_projects_for_jd(
            project_pool, req.jd_text,
            hint_titles=req.selected_projects, n=3,
            priority_keywords=resume_cfg.get("project_priority_keywords", []),
        )
        tailored_skills = _assemble_tailored_skills(
            master_skills, req.jd_text,
            selected_skill_names=req.selected_skills,
            jd_additions_map=cfg.get("skills_jd_additions", {}),
        )
        tailored_skills, skills_removed = _trim_skills_lists(
            tailored_skills, req.jd_text, req.selected_skills,
        )
        selected_projects = _ensure_project_bullets(selected_projects)
        print(f"[tailor-resume] Selected projects: {[p.get('title','') for p in selected_projects]}")

        # ── STEP 2: Build evidence for constraint validation ─────────────
        evidence_text = (user_data.get("summary", "") + " " + bundle.get("base_resume_plain", "") + " " +
            bundle.get("cv_plain", "") + " " + " ".join(
            b for e in user_data.get("experience", [])
            for b in (e.get("details") or e.get("bullets") or [])
        ))

        # ── STEP 3: Section-specific LLM calls ─────────────────────────────
        # Keep high-risk sections isolated: deterministic project/skills selection,
        # then separate prompts for summary and experience bullets.
        exp0 = user_data.get("experience", [{}])[0]
        exp0_bullets = exp0.get("details") or exp0.get("bullets") or []
        bullets_json = json.dumps(
            [{"text": b, "original": b, "status": "unchanged"} for b in exp0_bullets],
            indent=2,
        )
        emphasis = ", ".join(req.selected_skills) if req.selected_skills else "choose from JD"

        style_block = (
            f"Candidate voice (match this): median bullet ~{style.get('median_words', 15)} words, "
            f"max ~{style.get('max_words', 25)}; "
            f"{style.get('starts_with_verb_pct', 90)}% of bullets start with strong verbs; "
            f"metrics appear in ~{style.get('metric_pct', 50)}% of bullets."
        )
        humanity_rules = """
HUMAN / NATURAL WRITING (critical):
- Sound like a strong engineer wrote this, not a cover-letter bot.
- Prefer small phrase swaps over full-sentence rewrites.
- Keep original sentence rhythm, em-dashes, and numbers exactly when possible.
- NEVER use: cutting-edge, world-class, synergize, leverage, passionate, results-driven,
  proven track record, spearheaded transformative, holistic, paradigm, thrilled, excited to.
- No filler openers ("Highly motivated", "Proven ability to").
- Do not stack more than 2 JD keywords in one sentence."""

        summary_prompt = f"""You are a technical resume editor rewriting ONLY the summary section.

GOAL:
- Improve ATS alignment for the target JD without changing the candidate's facts.
- Keep the same direct tone and similar density as the source summary.
- This should feel like a bounded replacement, not a new bio.

RULES:
- 2-3 sentences, 45-80 words.
- Implied first person, no pronouns.
- Keep wording grounded in the candidate evidence below.
- If mentioning target company use the FULL name "{req.company}".
- Do NOT use placeholders or commentary.
{humanity_rules}
{style_block}

JOB DESCRIPTION:
\"\"\"{req.jd_text[:3500]}\"\"\"

TARGET ROLE: {req.role}
TARGET COMPANY: {req.company}
SKILLS TO EMPHASIZE: {emphasis}
OPTIONAL INSTRUCTION: {req.user_instruction or "(none)"}

SOURCE SUMMARY:
{user_data.get("summary", "")}

EVIDENCE SNIPPET:
{evidence_text[:1800]}

OUTPUT JSON ONLY:
{{
  "tailored_summary": "<actual rewritten summary>",
  "summary_diff": {{"original": "{user_data.get("summary", "")[:200]}", "tailored": "<same as tailored_summary>"}},
  "keywords_inserted": ["keywords added in the summary"],
  "score_estimate": 0
}}"""

        experience_prompt = f"""You are a technical resume editor rewriting ONLY one experience section.

GOAL:
- Improve ATS alignment for this JD using only the existing facts from the bullets below.
- Treat each bullet as a bounded rewrite. Replace phrases; do not invent scope.

RULES:
- Keep company, title, and dates unchanged.
- Status "edited" = text changed. "unchanged" = identical to original. "added" = new bullet backed by candidate evidence.
- Preserve the original sentence skeleton and technical facts wherever possible.
- Length budget: each edited bullet should stay within roughly the same footprint as the source bullet (target <= 1.15x original words).
- Only insert keywords directly supported by the original bullet or the candidate evidence below.
- Prefer 2-4 phrase-level edits per bullet; mark "unchanged" if the bullet already fits the JD.
- If a bullet already mentions a JD skill, leave it unchanged rather than rephrase for style.
{humanity_rules}
{style_block}

JOB DESCRIPTION:
\"\"\"{req.jd_text[:3500]}\"\"\"

TARGET ROLE: {req.role}
TARGET COMPANY: {req.company}
SKILLS TO EMPHASIZE: {emphasis}
OPTIONAL INSTRUCTION: {req.user_instruction or "(none)"}

EXPERIENCE ENTRY TO EDIT:
Company: {exp0.get("company", "")}
Title: {exp0.get("role") or exp0.get("title", "")}
Dates: {exp0.get("duration", "")}
Bullets:
{bullets_json}

CANDIDATE EVIDENCE:
{evidence_text[:2600]}

OUTPUT JSON ONLY:
{{
  "experience": [
    {{
      "company": "{exp0.get("company", "")}",
      "title": "{exp0.get("role") or exp0.get("title", "")}",
      "dates": "{exp0.get("duration", "")}",
      "bullets": [
        {{"text": "<rewritten or original text>", "status": "edited|unchanged|added", "original": "<exact source text, empty string if added>"}}
      ]
    }}
  ],
  "keywords_inserted": ["keywords added in experience bullets"]
}}"""

        async def _llm_json(prompt_text: str, temperature: float = 0.35):
            provider_key, _ = normalize_llm_prefer(active_model or "ollama")
            prefer = active_model if active_model else "ollama"
            if not prefer or prefer in {"ollama", "claude"}:
                prefer = provider_key
            fallback_model = None
            if active_model and active_model.startswith("ollama/"):
                fallback_model = active_model.split("/", 1)[1]
            elif active_model and active_model not in {"ollama", "claude"} and "/" not in active_model:
                fallback_model = active_model
            content = await asyncio.to_thread(
                call_llm,
                [{"role": "user", "content": prompt_text}],
                temperature,
                "",
                prefer,
                600,
                fallback_model,
            )
            return sanitize_untrusted_text(json.loads(clean_json(content)))

        if sequential_llm:
            summary_raw = await _llm_json(summary_prompt, 0.3)
            experience_raw = await _llm_json(experience_prompt, 0.4)
        else:
            summary_task = asyncio.create_task(_llm_json(summary_prompt, 0.3))
            experience_task = asyncio.create_task(_llm_json(experience_prompt, 0.4))
            summary_raw, experience_raw = await asyncio.gather(
                summary_task, experience_task, return_exceptions=True
            )

        if isinstance(summary_raw, Exception) and isinstance(experience_raw, Exception):
            print(f"Tailor resume error: summary={summary_raw} experience={experience_raw}")
            raise HTTPException(status_code=500, detail="Both summary and experience tailoring failed.")

        result = {
            "tailored_summary": user_data.get("summary", ""),
            "summary_diff": {
                "original": user_data.get("summary", ""),
                "tailored": user_data.get("summary", ""),
            },
            "experience": [],
            "keywords_inserted": [],
            "_sections": {},
        }

        if isinstance(summary_raw, Exception):
            print(f"[tailor-resume] Summary call failed, keeping master summary: {summary_raw}")
            result["_sections"]["summary"] = {"ok": False, "fallback": True}
        else:
            result["tailored_summary"] = summary_raw.get("tailored_summary", result["tailored_summary"])
            result["summary_diff"] = summary_raw.get("summary_diff", result["summary_diff"])
            result["keywords_inserted"].extend(summary_raw.get("keywords_inserted", []))
            if summary_raw.get("score_estimate") is not None:
                result["score_estimate"] = summary_raw.get("score_estimate")
            result["_sections"]["summary"] = {"ok": True, "fallback": False}

        if isinstance(experience_raw, Exception):
            print(f"[tailor-resume] Experience call failed, keeping existing bullets: {experience_raw}")
            result["experience"] = [{
                "company": exp0.get("company", ""),
                "title": exp0.get("role") or exp0.get("title", ""),
                "dates": exp0.get("duration", ""),
                "bullets": [{"text": b, "status": "unchanged", "original": b} for b in exp0_bullets],
            }]
            result["_sections"]["experience"] = {"ok": False, "fallback": True}
        else:
            result["experience"] = experience_raw.get("experience", [])
            result["keywords_inserted"].extend(experience_raw.get("keywords_inserted", []))
            result["_sections"]["experience"] = {"ok": True, "fallback": False}

        # De-duplicate inserted keywords while preserving order.
        seen_keywords = set()
        deduped_keywords = []
        for kw in result.get("keywords_inserted", []):
            norm = str(kw).strip().lower()
            if not norm or norm in seen_keywords:
                continue
            seen_keywords.add(norm)
            deduped_keywords.append(str(kw).strip())
        result["keywords_inserted"] = deduped_keywords

        # ── STEP 4: Post-process — de-AI, validate summary, attach deterministic fields ──
        result = constraints_engine.humanize_tailored_output(result)

        # Reject placeholder summaries
        summary = result.get("tailored_summary", "")
        placeholder_patterns = ["...rewritten...", "rewritten for this role", "<actual", "2-3 sentence summary"]
        if not summary or any(p.lower() in summary.lower() for p in placeholder_patterns) or len(summary) < 40:
            print(f"[tailor-resume] Summary placeholder detected, falling back to master: {summary!r}")
            result["tailored_summary"] = user_data.get("summary", "")
            result.setdefault("summary_diff", {})["tailored"] = result["tailored_summary"]
        else:
            company_lower = (req.company or "").strip().lower()
            if company_lower:
                initials = "".join(part[0] for part in re.findall(r"[A-Za-z]+", req.company)).lower()
                collapsed_summary = re.sub(r"[^a-z]", "", result["tailored_summary"].lower())
                if initials and len(initials) <= 4 and initials in collapsed_summary and company_lower not in result["tailored_summary"].lower():
                    result["tailored_summary"] = f"{result['tailored_summary']} Targeted for {req.company}."
                    result.setdefault("summary_diff", {})["tailored"] = result["tailored_summary"]

        # Attach deterministic projects + skills (override LLM guesses)
        result["selected_projects"] = [p.get("title", "") for p in selected_projects]
        result["tailored_skills"] = tailored_skills

        # Compute accurate skills_added: items in tailored_skills NOT in master (normalized)
        master_flat = {_skill_base(s) for cat, lst in master_skills.items() if isinstance(lst, list) for s in lst}
        actually_added = []
        for lst in tailored_skills.values():
            for s in (lst or []):
                if _skill_base(s) not in master_flat and s not in actually_added:
                    actually_added.append(s)
        result["skills_added"] = actually_added
        result["skills_removed"] = skills_removed

        # ── STEP 5: Constraint validation ────────────────────────────────
        validation = constraints_engine.validate_tailored_resume(
            user_data, result, evidence_text=evidence_text,
        )

        repair_actions = []
        if not validation.ok:
            print(f"[tailor-resume] {len(validation.fatal_violations)} fatal violations, attempting auto-repair")
            result, repair_actions = constraints_engine.auto_repair(result, validation, user_data)
            validation_after = constraints_engine.validate_tailored_resume(
                user_data, result, evidence_text=evidence_text,
            )
        else:
            validation_after = validation

        result["_validation"] = {
            "ok": validation_after.ok,
            "violations": [v.to_dict() for v in validation_after.violations],
            "repair_actions": repair_actions,
            "style_fingerprint": style,
            "editable_regions": bundle.get("editable_regions", []),
        }
        result["_preflight"] = constraints_engine.preflight_tailored_resume(user_data, result)
        result["_edits"] = _build_grounded_edits(
            pid=pid,
            jd_text=req.jd_text,
            master=user_data,
            tailored=result,
        )
        result["_meta"] = {
            "model_used": active_model,
            "profile": pid,
            "edit_preference": "claude_with_ollama_fallback",
        }
        return result


@app.post("/preflight-check")
async def preflight_check(data: dict, request: Request):
    """Re-run pre-PDF checks after dashboard edits (no LLM)."""
    pid = get_pid(request)
    master = load_pdata(pid)
    data = constraints_engine.humanize_tailored_output(data)
    return constraints_engine.preflight_tailored_resume(master, data)

# ─────────────────────────────────────────────────────────────────
# ENDPOINT 7: PDF GENERATION
# ─────────────────────────────────────────────────────────────────
def _merge_tailored_into_master(master: dict, data: dict) -> dict:
    """Apply tailor-resume output onto a copy of master profile data."""
    merged = json.loads(json.dumps(master))  # deep copy
    accepted_edit_map = {
        e["field"]: e
        for e in tailor_edits.validate_edits(data.get("_edits"))
        if e.get("status") == "accepted"
    }
    enforce_accept_only = bool(data.get("_edits"))

    # 1. Tailored summary
    tailored_sum = data.get("tailored_summary") or (data.get("summary_diff") or {}).get("tailored")
    if enforce_accept_only:
        accepted_summary = accepted_edit_map.get("summary")
        if accepted_summary and accepted_summary.get("after"):
            merged["summary"] = accepted_summary["after"]
    elif tailored_sum:
        merged["summary"] = tailored_sum

    # 2. Tailored experience bullets
    if data.get("experience"):
        tailored_by_company = {
            (te.get("company","").lower().strip()[:30]): te for te in data["experience"]
        }
        for exp_idx, src_exp in enumerate(merged.get("experience", [])):
            key = src_exp.get("company","").lower().strip()[:30]
            te = tailored_by_company.get(key)
            if te and te.get("bullets"):
                if enforce_accept_only:
                    original_bullets = list(src_exp.get("details") or src_exp.get("bullets") or [])
                    accepted_bullets: list[str] = []
                    for b_idx, old_text in enumerate(original_bullets):
                        field = f"experience.{exp_idx}.bullets.{b_idx}"
                        accepted = accepted_edit_map.get(field)
                        if accepted and (accepted.get("after") or "").strip():
                            accepted_bullets.append(accepted["after"].strip())
                        else:
                            accepted_bullets.append(old_text)
                    # accepted "added" bullets (index beyond original list)
                    for field, edit in accepted_edit_map.items():
                        m = re.match(rf"^experience\.{exp_idx}\.bullets\.(\d+)$", field)
                        if not m:
                            continue
                        b_idx = int(m.group(1))
                        if b_idx < len(original_bullets):
                            continue
                        text = (edit.get("after") or "").strip()
                        if text:
                            accepted_bullets.append(text)
                    if accepted_bullets:
                        src_exp["details"] = accepted_bullets
                        src_exp["bullets"] = accepted_bullets
                else:
                    new_bullets = [b.get("text","").strip() for b in te["bullets"] if (b.get("text") or "").strip()]
                    if new_bullets:
                        src_exp["details"] = new_bullets
                        src_exp["bullets"] = new_bullets

    # 3. Project selection — use pre-ranked titles from _rank_projects_for_jd
    project_library = merged.get("project_library") or merged.get("projects") or []
    if data.get("selected_projects"):
        target_titles = {t.lower().strip() for t in data["selected_projects"]}
        # Match by substring so minor title differences don't break lookup
        filtered = [p for p in project_library
                    if any(t in p.get("title","").lower() or p.get("title","").lower() in t
                           for t in target_titles)]
        if filtered:
            merged["projects"] = filtered[:3]
    elif merged.get("projects") and len(merged["projects"]) > 3:
        merged["projects"] = merged["projects"][:3]

    # 3b. Apply tailored skills — use the full assembled version from _assemble_tailored_skills.
    # The tailored_skills dict already contains the complete master list with JD additions.
    # Only override if it has non-empty values for at least 3 categories (guards against LLM truncation).
    if data.get("tailored_skills"):
        ts = data["tailored_skills"]
        if enforce_accept_only:
            for key in ["domains", "frameworks", "tools", "databases", "languages"]:
                field = f"tailored_skills.{key}"
                accepted = accepted_edit_map.get(field)
                if not accepted:
                    continue
                after = (accepted.get("after") or "").strip()
                merged.setdefault("skills", {})[key] = (
                    [s.strip() for s in after.split(",") if s.strip()] if after else []
                )
        else:
            non_empty = sum(1 for v in ts.values() if isinstance(v, list) and len(v) > 1)
            if non_empty >= 3:
                # Merge: for each category keep master entries + any new items from tailored (no truncation)
                for key in ["domains", "frameworks", "tools", "databases", "languages"]:
                    tailored_list = ts.get(key) or []
                    # Union preserving order: tailored first (may reorder for emphasis), then any master extras.
                    # Use _skill_base normalization to avoid duplicates like "Python" + "Python (Expert)".
                    merged.setdefault("skills", {})[key] = list(tailored_list)
            else:
                print(f"[merge] tailored_skills too sparse ({non_empty} non-empty categories) — keeping master skills")

    # 4. Dedup: don't repeat publications under projects
    pub_titles: set = set()
    if merged.get("publications"):
        pub_titles = {p.get("title","").lower().strip() for p in merged["publications"]}
    if merged.get("projects"):
        merged["projects"] = _ensure_project_bullets(merged["projects"])
        merged["projects"] = [p for p in merged["projects"]
                              if p.get("title","").lower().strip() not in pub_titles]
        # Pad back to 3 if publication dedup removed some entries
        if len(merged["projects"]) < 3:
            used = {p.get("title","").lower().strip() for p in merged["projects"]}
            for p in project_library:
                if len(merged["projects"]) >= 3:
                    break
                t = p.get("title","").lower().strip()
                if t not in used and t not in pub_titles:
                    merged["projects"].append(p)
                    used.add(t)
    return merged


def _filter_payload_to_accepted(master: dict, data: dict) -> dict:
    """Project payload fields to accepted edits only (additive-safe fallback)."""
    payload = json.loads(json.dumps(data or {}))
    accepted = {
        e["field"]: e
        for e in tailor_edits.validate_edits(payload.get("_edits"))
        if e.get("status") == "accepted"
    }
    if not payload.get("_edits"):
        return payload

    # Summary
    s = accepted.get("summary")
    summary_value = (s.get("after") if s else master.get("summary", "")) or ""
    payload["tailored_summary"] = summary_value
    payload.setdefault("summary_diff", {})["tailored"] = summary_value

    # Experience bullets
    for e_idx, exp in enumerate(payload.get("experience") or []):
        orig = []
        if e_idx < len(master.get("experience") or []):
            orig = (master["experience"][e_idx].get("details") or master["experience"][e_idx].get("bullets") or [])
        for b_idx, bullet in enumerate(exp.get("bullets") or []):
            field = f"experience.{e_idx}.bullets.{b_idx}"
            if field in accepted:
                bullet["text"] = accepted[field].get("after") or bullet.get("text") or ""
            elif b_idx < len(orig):
                bullet["text"] = orig[b_idx]
                bullet["status"] = "unchanged"

    # Skills
    ts = payload.get("tailored_skills") or {}
    ms = master.get("skills") or {}
    for key in list(ts.keys()):
        field = f"tailored_skills.{key}"
        if field in accepted:
            after = accepted[field].get("after") or ""
            ts[key] = [x.strip() for x in after.split(",") if x.strip()] if after else []
        else:
            ts[key] = list(ms.get(key) or [])
    payload["tailored_skills"] = ts
    return payload


def _render_tex_from_master(master: dict) -> str:
    """Render resume_template.tex with master data via Jinja."""
    tpl_path = os.path.join(os.path.dirname(__file__), "resume_template.tex")
    with open(tpl_path) as f:
        template_str = f.read()
    env = Environment(
        block_start_string='\\BLOCK{', block_end_string='}',
        variable_start_string='\\VAR{', variable_end_string='}',
        comment_start_string='\\#{', comment_end_string='}',
        loader=BaseLoader(),
    )
    env.filters['latex'] = escape_latex_chars
    env.filters['url'] = _safe_href_url
    return env.from_string(template_str).render(**master)


def _sanitize_pdf_meta_value(value: str) -> str:
    return re.sub(r"[{}\\\n\r]+", " ", str(value or "")).strip()


def _inject_pdf_metadata(tex: str, *, author: str, title: str) -> str:
    """Attach clean PDF metadata fields.

    NOTE: These properties are hygiene-only and are not ATS scoring inputs.
    """
    safe_author = _sanitize_pdf_meta_value(author)
    safe_title = _sanitize_pdf_meta_value(title)
    if not safe_author:
        return tex
    metadata_block = (
        "\n% Metadata hygiene only (not ATS scoring fields)\n"
        "\\hypersetup{\n"
        f"  pdfauthor={{{safe_author}}},\n"
        f"  pdftitle={{{safe_title or (safe_author + ' - Resume')}}},\n"
        "  pdfcreator={},\n"
        "  pdfproducer={}\n"
        "}\n"
    )
    if "\\begin{document}" in tex:
        return tex.replace("\\begin{document}", metadata_block + "\\begin{document}", 1)
    return metadata_block + tex


def _render_compile_version(pid: str, data: dict) -> dict:
    """Merge tailored data → render → compile → persist an exact resume variant.

    Shared by POST /generate-pdf and the M6 queue approve path so both link the
    precise PDF artifact. Callers must hold `processing_lock`. Raises HTTPException on
    preflight/hygiene/render failure; on compile failure returns result.success=False.
    """
    profile_dir = _profile_dir(pid)
    master = _enrich_profile_with_resume_sources(load_pdata(pid))
    # Untrusted inbound payload (JD-derived edits, user text): strip control
    # chars and cap lengths before it is merged/rendered. Escaping at render time
    # (escape_latex_chars) remains the hard guarantee; this bounds blast radius.
    data = sanitize_untrusted_text(data)
    effective_data = _filter_payload_to_accepted(master, data)

    # 0. Pre-PDF preflight (after user edits in dashboard)
    preflight = constraints_engine.preflight_tailored_resume(master, effective_data)
    if not preflight.get("ok"):
        fatal = [i for i in preflight.get("issues", []) if i.get("severity") == "fatal"]
        log.warning(f"[generate-pdf] preflight blocked — {fatal}")
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Resume failed pre-PDF checks. Fix issues in the preview, then try again.",
                "preflight": preflight,
            },
        )

    # 1. Merge
    merged = _merge_tailored_into_master(master, effective_data)
    log.debug(f"[generate-pdf] merged data — projects={len(merged.get('projects',[]))}")

    # 2. Render LaTeX from template
    try:
        rendered_tex = _render_tex_from_master(merged)
        contact = merged.get("contact_info", {}) or {}
        author_name = contact.get("name", "") or master.get("contact_info", {}).get("name", "")
        rendered_tex = _inject_pdf_metadata(
            rendered_tex,
            author=author_name,
            title=f"{author_name} - Resume" if author_name else "Resume",
        )
        log.debug(f"[generate-pdf] LaTeX rendered — chars={len(rendered_tex)}")
    except Exception as e:
        log.error(f"[generate-pdf] Template render failed — pid={pid}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Template render failed: {e}")

    # 3. Validate LaTeX balanced before compile
    ok, problems = latex_ast.validate_balanced(rendered_tex)
    if not ok:
        log.warning(f"[generate-pdf] LaTeX balance check warnings — {problems}")
        # don't reject — try to compile anyway (warnings are common)

    hygiene = compile_loop.inspect_tex_hygiene(rendered_tex)
    if not hygiene.get("ok"):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Resume failed PDF hygiene checks.",
                "hygiene": hygiene,
            },
        )

    # 4. Compile with retry + repair
    backend_dir = os.path.dirname(__file__)
    ats_expected = {
        "name": merged.get("contact_info", {}).get("name", ""),
        "email": merged.get("contact_info", {}).get("email", ""),
    }
    result = compile_loop.compile_with_retry(
        rendered_tex,
        work_dir=backend_dir,
        name="tailored_resume",
        max_attempts=12,  # 1-page enforcement may need many trim passes
        target_max_pages=1,
        ats_expected=ats_expected,
    )

    log_event(log, "INFO" if result.success else "ERROR", "compile_result",
              pid=pid, success=result.success, attempts=result.attempts,
              pages=result.page_count, latency_ms=result.latency_ms,
              repairs=len(result.repair_actions), ats_ok=result.ats_validation.get("overall_ok"))

    # 5. Persist variant
    variant_meta = None
    if result.success and result.pdf_bytes:
        try:
            variant_meta = resume_versions.create_variant(
                profile_dir,
                company=data.get("_company") or data.get("company") or "unknown",
                role=data.get("_role") or data.get("role") or "",
                jd_text=data.get("_jd") or "",
                tailored_tex=rendered_tex,
                tailored_pdf_bytes=result.pdf_bytes,
                analysis=data.get("_analysis"),
                score={
                    "estimate": data.get("score_estimate"),
                    "before": (data.get("_analysis") or {}).get("match_score"),
                    "after": data.get("score_estimate"),
                    "delta": (
                        data.get("score_estimate", 0) - ((data.get("_analysis") or {}).get("match_score") or 0)
                        if data.get("score_estimate") is not None and (data.get("_analysis") or {}).get("match_score") is not None
                        else None
                    ),
                },
                validation={
                    "compile_attempts": result.attempts,
                    "repair_actions": result.repair_actions,
                    "page_count": result.page_count,
                    "ats_validation": result.ats_validation,
                    "hygiene": hygiene,
                    "warnings": result.warnings,
                    "latex_balance_problems": problems,
                },
            )
        except Exception as e:
            log.warning(f"[generate-pdf] Variant save failed (non-fatal) — pid={pid}: {e}")

    return {"result": result, "variant_meta": variant_meta, "problems": problems}


@app.post("/generate-pdf")
async def generate_pdf(data: dict, request: Request):
    """Production-grade PDF generation:
       1. Merge tailored data into master
       2. Render LaTeX
       3. Validate LaTeX balanced
       4. Compile with retry + auto-repair
       5. Validate PDF + ATS extractability
       6. Save as versioned variant
       7. Return PDF (never silent HTML fallback)
    """
    pid = get_pid(request)
    _pdf_t0 = _time.time()
    log_event(log, "INFO", "request", endpoint="POST /generate-pdf", pid=pid,
              role=data.get("_role","?"), company=data.get("_company","?"))
    async with processing_lock:
        ctx = _render_compile_version(pid, data)
        result = ctx["result"]
        variant_meta = ctx["variant_meta"]
        problems = ctx["problems"]

        # 6. Return PDF if success
        total_ms = int((_time.time() - _pdf_t0) * 1000)
        if result.success and result.pdf_path:
            log_event(log, "INFO", "pdf_ok", pid=pid, total_ms=total_ms,
                      variant=variant_meta["id"] if variant_meta else "unsaved")
            headers = {
                "X-PDF-Attempts": str(result.attempts),
                "X-PDF-Pages": str(result.page_count or "?"),
                "X-PDF-ATS-OK": str(result.ats_validation.get("overall_ok", False)).lower(),
                "X-PDF-Latency-Ms": str(result.latency_ms),
            }
            if variant_meta:
                headers["X-PDF-Variant-Id"] = variant_meta["id"]
            return FileResponse(
                result.pdf_path,
                media_type="application/pdf",
                filename="tailored_resume.pdf",
                headers=headers,
            )

        # 7. Hard failure — return detailed error so user knows what happened
        missing_tools = [e for e in result.errors if e.get("type") == "missing_binary"]
        if missing_tools:
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "Local PDF toolchain is missing.",
                    "missing_tools": [
                        "pdflatex" if "pdflatex" in (e.get("message") or "").lower() else "unknown"
                        for e in missing_tools
                    ],
                    "hint": "Install a TeX distribution that provides `pdflatex`. For ATS validation, also install `pdftotext` and `pdfinfo`.",
                    "compile_result": result.to_dict(),
                },
            )
        log.error(f"[generate-pdf] FAILED after {result.attempts} attempts — pid={pid} "
                  f"errors={[e.get('type') for e in result.errors]}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "PDF generation failed after retries",
                "compile_result": result.to_dict(),
                "latex_balance_problems": problems,
                "hint": "Check that LaTeX template + master_data are consistent.",
            },
        )


# ─────────────────────────────────────────────────────────────────
# LOG MANAGEMENT API  (/lh/logs/*)
# ─────────────────────────────────────────────────────────────────

@app.get("/lh/logs")
def list_log_files():
    """List all log files in the logs/ directory, newest first."""
    if not os.path.isdir(LOGS_DIR):
        return {"files": [], "logs_dir": LOGS_DIR}
    files = []
    for fname in sorted(os.listdir(LOGS_DIR), reverse=True):
        if not fname.endswith(".log"):
            continue
        fpath = os.path.join(LOGS_DIR, fname)
        stat = os.stat(fpath)
        files.append({
            "name": fname,
            "size_kb": round(stat.st_size / 1024, 1),
            "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        })
    return {"files": files, "logs_dir": LOGS_DIR, "config": get_log_config()}


@app.get("/lh/logs/config")
def log_config():
    """Return current logging configuration."""
    return get_log_config()


@app.post("/lh/logs/config")
def update_log_config(payload: dict):
    """
    Toggle or change log level at runtime without restarting the server.
    Body: { "enabled": true|false, "level": "DEBUG"|"INFO"|"WARNING"|"ERROR" }
    """
    changed = {}
    if "enabled" in payload:
        set_logging_enabled(bool(payload["enabled"]))
        changed["enabled"] = bool(payload["enabled"])
        log.info(f"Log config updated — enabled={payload['enabled']}")
    if "level" in payload:
        ok = set_log_level(str(payload["level"]))
        if ok:
            changed["level"] = str(payload["level"]).upper()
    return {"ok": True, "changed": changed, "config": get_log_config()}


@app.get("/lh/logs/{filename}")
def read_log_file(filename: str, tail: int = 200):
    """
    Return the last `tail` lines of a log file.
    Filename must end in .log and contain no path separators.
    """
    if "/" in filename or "\\" in filename or not filename.endswith(".log"):
        raise HTTPException(status_code=400, detail="Invalid filename")
    fpath = os.path.join(LOGS_DIR, filename)
    if not os.path.isfile(fpath):
        raise HTTPException(status_code=404, detail="Log file not found")
    try:
        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return {
            "filename": filename,
            "total_lines": len(lines),
            "lines": [l.rstrip("\n") for l in lines[-tail:]],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/lh/ext-logs")
async def receive_ext_logs(payload: dict):
    """
    Receive batched log entries from the Chrome extension.
    Writes them to a separate ext-logs file inside the logs/ directory.
    """
    entries = payload.get("logs", [])
    if not entries:
        return {"received": 0}
    os.makedirs(LOGS_DIR, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    ext_log_path = os.path.join(LOGS_DIR, f"extension_{today}.log")
    written = 0
    try:
        with open(ext_log_path, "a", encoding="utf-8") as f:
            for entry in entries:
                ts    = entry.get("ts", "")
                level = entry.get("level", "INFO").upper()
                mod   = entry.get("module", "ext")
                msg   = entry.get("msg", "")
                data  = entry.get("data")
                line  = f"[{ts}] [{level:<8}] [{mod}]  {msg}"
                if data:
                    line += f"  | {json.dumps(data)}"
                f.write(line + "\n")
                written += 1
    except Exception as e:
        return {"received": 0, "error": str(e)}
    return {"received": written}


from datetime import datetime  # ensure imported for log endpoints
