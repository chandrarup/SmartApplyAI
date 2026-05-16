from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
import uuid, hashlib
from datetime import date as _date
from pydantic import BaseModel
import requests as http_requests
import json
import os
import re
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

# Logging — must come after imports, before app
from logger import get_logger, log_event, is_logging_enabled, set_logging_enabled, set_log_level, get_config as get_log_config, LOGS_DIR
log = get_logger("api")

app = FastAPI()

# --- CONFIGURATION ---
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:7b")
OLLAMA_API_URL = os.getenv("OLLAMA_API_URL", "http://localhost:11434/v1/chat/completions")
OLLAMA_HEALTH_URL = os.getenv("OLLAMA_HEALTH_URL", "http://localhost:11434/api/tags")
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

def _enrich_profile_with_resume_sources(data: dict) -> dict:
    """Merge resume source files into the working profile without mutating disk state."""
    enriched = json.loads(json.dumps(data or {}))
    bundle = resume_source.build_resume_source_bundle()
    if bundle.get("base_summary") and not enriched.get("summary"):
        enriched["summary"] = bundle["base_summary"]
    enriched["project_library"] = resume_source.merge_project_libraries(
        enriched.get("projects", []),
        bundle.get("base_projects", []) + bundle.get("cv_projects", []),
    )
    enriched["_resume_source"] = {
        "base_resume_path": bundle.get("base_resume_path", ""),
        "cv_path": bundle.get("cv_path", ""),
        "editable_regions": bundle.get("editable_regions", []),
    }
    return enriched

def save_pdata(pid: str, data: dict):
    d = _profile_dir(pid)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "master_data.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

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

# Claude / Anthropic config — read from env at request time so hot-reload works
def get_anthropic_key():
    return os.environ.get("ANTHROPIC_API_KEY", "")

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

class CoverLetterRequest(BaseModel):
    company: str
    role: str
    jd_text: str = ""
    hiring_manager: str = ""
    llm: str = "ollama"

# ─────────────────────────────────────────────────────────────────
# LLM ABSTRACTION — try Ollama; fall back to Claude; raise if both fail
# ─────────────────────────────────────────────────────────────────
def call_ollama(messages: list, temperature: float = 0.3, timeout: int = 600) -> str:
    t0 = _time.time()
    log.debug(f"Calling Ollama — model={OLLAMA_MODEL} messages={len(messages)} temp={temperature}")
    data = {"model": OLLAMA_MODEL, "messages": messages, "stream": False, "temperature": temperature}
    response = http_requests.post(OLLAMA_API_URL, json=data, timeout=timeout)
    result = response.json()["choices"][0]["message"]["content"]
    log_event(log, "INFO", "llm_call", provider="ollama", model=OLLAMA_MODEL,
              latency_ms=int((_time.time()-t0)*1000), response_chars=len(result))
    return result

def call_claude(messages: list, temperature: float = 0.3, system: str = "") -> str:
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError(
            "Claude support is not installed. Run `python3 -m pip install -r backend/requirements-optional.txt`."
        ) from exc
    t0 = _time.time()
    api_key = get_anthropic_key()
    if not api_key:
        log.error("call_claude: ANTHROPIC_API_KEY is not set")
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    log.debug(f"Calling Claude — messages={len(messages)} temp={temperature}")
    client = anthropic.Anthropic(api_key=api_key)
    # Convert OpenAI-format messages; extract system if present
    claude_messages = []
    sys_content = system
    for m in messages:
        if m["role"] == "system":
            sys_content = (sys_content + "\n" + m["content"]).strip()
        else:
            claude_messages.append({"role": m["role"], "content": m["content"]})
    kwargs = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 2048,
        "messages": claude_messages,
    }
    if sys_content:
        kwargs["system"] = sys_content
    message = client.messages.create(**kwargs)
    result = message.content[0].text
    log_event(log, "INFO", "llm_call", provider="claude", model="claude-sonnet-4-6",
              latency_ms=int((_time.time()-t0)*1000), response_chars=len(result))
    return result

def call_llm(messages: list, temperature: float = 0.3, system: str = "",
             prefer: str = "ollama", timeout: int = 600) -> str:
    """Try preferred provider first, auto-fallback to the other."""
    providers = ["claude", "ollama"] if prefer == "claude" else ["ollama", "claude"]
    last_err = None
    for provider in providers:
        try:
            if provider == "ollama":
                return call_ollama(messages, temperature, timeout)
            else:
                return call_claude(messages, temperature, system)
        except Exception as e:
            last_err = e
            log.warning(f"LLM provider '{provider}' failed — {e}. Trying next...")
    log.error(f"All LLM providers failed. Last error: {last_err}")
    raise RuntimeError(f"All LLM providers failed. Last error: {last_err}")

def clean_json(raw: str) -> str:
    """Robustly extract the first valid JSON object or array from LLM output.
    Handles: fenced blocks (```json / ```JSON / ```), preamble text, postamble text,
    multiple code blocks, and truncated JSON gracefully.
    """
    if not raw:
        return raw
    # 1. Try fenced code blocks first (handles ```json, ```JSON, ```)
    fence_pattern = re.compile(r'```(?:json|JSON)?\s*\n?(.*?)```', re.DOTALL)
    for match in fence_pattern.finditer(raw):
        candidate = match.group(1).strip()
        try:
            json.loads(candidate)
            return candidate
        except (json.JSONDecodeError, ValueError):
            continue
    # 2. Depth-track to find first balanced { or [ — handles preamble/postamble text
    raw_s = raw.strip()
    for start_ch, end_ch in [('{', '}'), ('[', ']')]:
        start = raw_s.find(start_ch)
        if start == -1:
            continue
        depth = 0
        in_str = False
        esc = False
        for i, ch in enumerate(raw_s[start:], start):
            if esc:
                esc = False; continue
            if ch == '\\' and in_str:
                esc = True; continue
            if ch == '"' and not esc:
                in_str = not in_str; continue
            if in_str:
                continue
            if ch == start_ch: depth += 1
            elif ch == end_ch:
                depth -= 1
                if depth == 0:
                    candidate = raw_s[start:i+1]
                    try:
                        json.loads(candidate)
                        return candidate
                    except (json.JSONDecodeError, ValueError):
                        break
    return raw_s  # last resort — let caller handle json.loads error

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

def escape_latex_chars(text):
    if not isinstance(text, str):
        return text
    chars = {"&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}"}
    for char, replacement in chars.items():
        text = text.replace(char, replacement)
    return text

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
    """Render an ATS-formatted HTML resume merging tailored data into the profile."""
    master = _enrich_profile_with_resume_sources(load_pdata(get_pid(request)))
    # Merge tailored summary + tailored experience bullets if provided
    if data.get("tailored_summary"):
        master["summary"] = data["tailored_summary"]
    elif data.get("summary_diff", {}).get("tailored"):
        master["summary"] = data["summary_diff"]["tailored"]
    if data.get("experience"):
        # Replace bullets with tailored versions
        tailored_by_co = {(e.get("company","") + "::" + e.get("title","")): e for e in data["experience"]}
        for src in master.get("experience", []):
            key = src.get("company","") + "::" + src.get("title","")
            t = tailored_by_co.get(key)
            if t:
                src["bullets"] = [b.get("text","") for b in (t.get("bullets") or []) if b.get("text")]
    if data.get("selected_projects") and master.get("projects"):
        keep = {p.lower().strip() for p in data["selected_projects"]}
        master["projects"] = [p for p in master["projects"] if p.get("title","").lower().strip() in keep]
    html = render_resume_html(master)
    return HTMLResponse(content=html)

class LearnRequest(BaseModel):
    host: str
    label: str
    value: str

@app.post("/autofill/learn")
def autofill_learn(req: LearnRequest, request: Request):
    """Save a user correction so we use it next time on the same domain."""
    pid = get_pid(request)
    data = load_pdata(pid)
    learned = data.get("learned_answers", {})
    key = f"{req.host}::{req.label.strip().lower()}"
    learned[key] = req.value
    data["learned_answers"] = learned
    save_pdata(pid, data)
    return {"ok": True, "saved": key}

@app.get("/autofill/learned")
def autofill_learned(host: str, request: Request):
    pid = get_pid(request)
    data = load_pdata(pid)
    learned = data.get("learned_answers", {})
    return {k.split("::", 1)[1]: v for k, v in learned.items() if k.startswith(host + "::")}

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
    ollama_ok = False
    claude_ok = bool(get_anthropic_key())
    try:
        r = http_requests.get(OLLAMA_HEALTH_URL, timeout=2)
        ollama_ok = r.status_code == 200
    except Exception:
        pass
    return {
        "default_provider": "ollama",
        "ollama": ollama_ok,
        "claude": claude_ok,
        "claude_key_set": claude_ok,
        "pdf_toolchain": {
            "pdflatex": bool(compile_loop.PDFLATEX_BIN and os.path.exists(compile_loop.PDFLATEX_BIN)),
            "pdftotext": bool(compile_loop.PDFTOTEXT_BIN and os.path.exists(compile_loop.PDFTOTEXT_BIN)),
            "pdfinfo": bool(subprocess.which("pdfinfo")),
        },
    }

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
    save_pdata(pid, {"contact_info": {"name": name}, "autofill": {}, "experience": [],
                     "education": [], "skills": {}, "common_answers": {}, "summary": ""})
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

@app.put("/profile/contact")
def update_contact(request: Request, payload: dict):
    pid = get_pid(request)
    data = load_pdata(pid)
    if "contact_info" in payload:
        data["contact_info"] = {**data.get("contact_info", {}), **payload["contact_info"]}
    if "summary" in payload:
        data["summary"] = payload["summary"]
    save_pdata(pid, data)
    return {"ok": True}

@app.put("/profile/autofill")
def update_autofill(request: Request, payload: dict):
    pid = get_pid(request)
    data = load_pdata(pid)
    if "autofill" in payload:
        data["autofill"] = {**data.get("autofill", {}), **payload["autofill"]}
    save_pdata(pid, data)
    return {"ok": True}

@app.put("/profile/experience")
def update_experience(request: Request, payload: dict):
    pid = get_pid(request)
    data = load_pdata(pid)
    data["experience"] = payload.get("experience", data.get("experience", []))
    save_pdata(pid, data)
    return {"ok": True}

@app.put("/profile/education")
def update_education(request: Request, payload: dict):
    pid = get_pid(request)
    data = load_pdata(pid)
    data["education"] = payload.get("education", data.get("education", []))
    save_pdata(pid, data)
    return {"ok": True}

@app.put("/profile/skills")
def update_skills(request: Request, payload: dict):
    pid = get_pid(request)
    data = load_pdata(pid)
    if "skills" in payload:
        data["skills"] = {**data.get("skills", {}), **payload["skills"]}
    save_pdata(pid, data)
    return {"ok": True}

@app.put("/profile/answers")
def update_answers(request: Request, payload: dict):
    pid = get_pid(request)
    data = load_pdata(pid)
    if "common_answers" in payload:
        data["common_answers"] = {**data.get("common_answers", {}), **payload["common_answers"]}
    save_pdata(pid, data)
    return {"ok": True}

# ─────────────────────────────────────────────────────────────────
# APPLICATIONS CRUD (profile-aware)
# ─────────────────────────────────────────────────────────────────
@app.get("/applications")
def get_applications(request: Request):
    return load_papps(get_pid(request))

@app.post("/applications")
def add_application(request: Request, payload: dict):
    pid = get_pid(request)
    apps = load_papps(pid)
    entry = {
        "id": str(uuid.uuid4()),
        "company": payload.get("company", ""),
        "role": payload.get("role", ""),
        "platform": payload.get("platform", "Other"),
        "status": payload.get("status", "Applied"),
        "date_applied": payload.get("date_applied") or str(_date.today()),
        "salary": payload.get("salary", ""),
        "location": payload.get("location", ""),
        "url": payload.get("url", ""),
        "notes": payload.get("notes", ""),
    }
    apps.append(entry)
    save_papps(pid, apps)
    return entry

@app.patch("/applications/{app_id}")
def update_application(app_id: str, request: Request, payload: dict):
    pid = get_pid(request)
    apps = load_papps(pid)
    for app in apps:
        if app["id"] == app_id:
            app.update({k: v for k, v in payload.items() if k != "id"})
            save_papps(pid, apps)
            return app
    raise HTTPException(status_code=404, detail="Application not found")

@app.delete("/applications/{app_id}")
def delete_application(app_id: str, request: Request):
    pid = get_pid(request)
    apps = load_papps(pid)
    new_apps = [a for a in apps if a["id"] != app_id]
    if len(new_apps) == len(apps):
        raise HTTPException(status_code=404, detail="Application not found")
    save_papps(pid, new_apps)
    return {"ok": True}

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

    # Phase 2: find fields rule-based couldn't answer — send only those to LLM
    # Skip sensitive fields entirely (never ask the LLM about health/legal/political)
    custom_fields = [
        f for f in req.fields[:40]
        if (not base_answers.get(f.get("label", "")) or base_answers.get(f.get("label", "")) == "SKIP")
        and not f.get("sensitive")
    ]

    if not custom_fields:
        return base_answers

    field_list = json.dumps(custom_fields, indent=2)
    prompt = f"""You are an expert job application assistant filling out a form on behalf of a candidate. The form has fields whose labels you have NEVER seen before — your job is to answer EACH ONE using the candidate's profile data.

CANDIDATE PROFILE (full source of truth):
{json.dumps(user_data, indent=2)[:5500]}

AUTOFILL QUICK REFERENCE (canonical values):
{json.dumps(autofill, indent=2)}

JOB DESCRIPTION: {req.jd_text[:1200]}
COMPANY: {req.company}

UNANSWERED FORM FIELDS (standard fields already filled — only answer these):
{field_list}

ANSWERING STRATEGY (apply in order for each field):
1. If the label is a synonym/paraphrase of an autofill key (e.g. "Mailing Address" ≈ address_line1, "Cell" ≈ phone, "Earliest start" ≈ start_date) → return the matching autofill value verbatim.
2. If the label is a Yes/No or dropdown about availability/work-style/willingness → infer from profile (default to candidate-friendly answers).
3. If the label is a date field (anything matching "date") → return today's date in MM/DD/YYYY.
4. If the label is a percentage/numeric question (travel %, salary, years) → use a sensible value from profile or industry norm.
5. If it's an open-ended written question (e.g. "Why this role?", "Describe a challenge") → write 2-3 sentences in implied first person citing real candidate experience.
6. ONLY return "SKIP" if the question literally requires data the candidate cannot have (employee ID at this company, security clearance number, etc).

CRITICAL: Do NOT return "SKIP" for any field that can plausibly be answered from the profile. Be aggressive about inferring.

OUTPUT: JSON object where keys are EXACTLY the "label" values shown above. OUTPUT JSON ONLY."""

    try:
        content = call_llm([{"role": "user", "content": prompt}],
                           temperature=0.1, prefer=req.llm, timeout=600)
        llm_answers = json.loads(clean_json(content))
        log_event(log, "INFO", "autofill_ok", rule_fields=len(base_answers),
                  llm_fields=len(llm_answers))
        return {**base_answers, **llm_answers}
    except Exception as e:
        log.warning(f"Autofill LLM failed — using rule-based only. Error: {e}")
        return base_answers

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
            def is_matched(skill_name: str) -> bool:
                s = skill_name.lower().strip()
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


@app.post("/tailor-resume")
async def tailor_resume(req: TailorResumeRequest, request: Request):
    pid = get_pid(request)
    log_event(log, "INFO", "request", endpoint="POST /tailor-resume", pid=pid,
              jd_len=len(req.jd_text), role=req.role or "?", company=req.company or "?",
              selected_skills=len(req.selected_skills), llm=req.llm)
    async with processing_lock:
        user_data = _enrich_profile_with_resume_sources(load_pdata(get_pid(request)))
        style = _build_style_fingerprint(user_data)
        bundle = resume_source.build_resume_source_bundle()
        project_library = user_data.get("project_library", user_data.get("projects", []))

        # Build evidence text for fact-invention validation
        evidence_text = (user_data.get("summary", "") + " " + bundle.get("base_resume_plain", "") + " " +
            bundle.get("cv_plain", "") + " " + " ".join(
            b for e in user_data.get("experience", [])
            for b in (e.get("details") or e.get("bullets") or [])
        ))

        prompt = f"""You are an expert technical resume writer. Your job is to ACTIVELY REWRITE the candidate's experience bullets to maximize keyword match with the target JD.

WHAT "EDITING" MEANS (you MUST do this):
- Rephrase bullets to front-load JD-required skills (e.g., if JD wants "RAG pipelines", make sure bullets that mention RAG lead with that)
- Swap generic verbs for stronger JD-matching ones (e.g., "Built" → "Deployed", "Designed" → "Architected")
- Add JD keywords inline where they are genuinely supported by existing work (e.g., "...using LangChain and AutoGen (LLM orchestration frameworks)" → adds "LLM orchestration")
- Reorder clauses to put the JD-relevant achievement first
- Status "edited" means the text changed. Status "unchanged" means the text is IDENTICAL to the original.

HARD RULES:
1. NEVER modify company names, job titles, or dates — copy them EXACTLY from source.
2. NEVER invent metrics or achievements — but you MAY rephrase how existing metrics are presented.
3. NEVER add buzzwords (synergize, cutting-edge, world-class, paradigm shift, enterprise-grade solutions).
4. You MUST set status="edited" for bullets you changed, and include the original text in "original".
5. When the tailored_summary mentions the target company, use the FULL name "{req.company}" — never abbreviate.
6. MINIMUM: at least 3 bullets across all experience entries MUST have status="edited" or "added". Returning all "unchanged" is a FAILURE — the output will be rejected.

CANDIDATE STYLE FINGERPRINT (preserve this style):
- Median bullet length: {style['median_words']} words (max {style['max_words']})
- Verb-start ratio: {style['starts_with_verb_pct']}% (most bullets start with action verbs)
- Metric-led ratio: {style['metric_pct']}% (bullets reference numbers/percentages from real work)
- Example bullets: {json.dumps(style.get('sample_bullets', []))}

CANDIDATE PROFILE (source of truth):
{json.dumps(user_data, indent=2)[:6000]}

FULL CV EVIDENCE BANK (use to find additional evidence for rewrites — only real facts):
{bundle.get("cv_plain", "")[:3000]}

TARGET ROLE: {req.role}
TARGET COMPANY: {req.company}
SKILLS TO EMPHASIZE: {", ".join(req.selected_skills) if req.selected_skills else "(choose JD-relevant from existing skills)"}
PROJECTS TO PRIORITIZE: {", ".join(req.selected_projects) if req.selected_projects else "(choose best fit from project library)"}
OPTIONAL USER INSTRUCTION: {req.user_instruction or "(none)"}

PROJECT LIBRARY (choose from this set only):
{json.dumps(project_library, indent=2)[:4000]}

JOB DESCRIPTION:
\"\"\"{req.jd_text[:5000]}\"\"\"

OUTPUT JSON ONLY, exact shape:
{{
  "tailored_summary": "Rewritten 2-3 sentence summary in implied first person, no pronouns. Match candidate's writing style. Use FULL company name '{req.company}' if mentioned.",
  "summary_diff": {{"original": "...source summary...", "tailored": "...rewritten..."}},
  "skills_added":   ["skills newly surfaced for this JD"],
  "skills_removed": ["skills de-emphasized as irrelevant"],
  "tailored_skills": {{
    "languages": [],
    "frameworks": [],
    "tools": [],
    "databases": [],
    "domains": []
  }},
  "experience": [
    {{
      "company": "EXACT source company string",
      "title":   "EXACT source title/role string",
      "dates":   "EXACT source dates/duration string",
      "bullets": [
        {{"text":"...rewritten or original bullet (max {style['max_words']} words)...","status":"unchanged|edited|added","original":"...source bullet text, empty string only if status=added..."}}
      ]
    }}
  ],
  "selected_projects": ["3 most relevant project titles, EXACT match from candidate.projects"],
  "keywords_inserted": ["JD keywords woven into the rewrite"],
  "score_estimate": 0
}}

Rules:
- Include all original bullets — but ACTIVELY REWRITE those that can better match the JD (status: "edited").
- Only use "unchanged" when a bullet already perfectly matches the JD with zero changes needed.
- Add at most 1 NEW bullet per role only if backed by source evidence (status: "added", original: "").
- "edited" bullets MUST include the exact original source text in "original" field.
- Max {style['max_words']} words per bullet. Max 5 bullets per role.
- `tailored_skills` must use the same 5-category shape as the base resume.
- `selected_projects` must be chosen from PROJECT LIBRARY only (exact title match)."""

        try:
            content = call_llm([{"role": "user", "content": prompt}],
                               temperature=0.4, prefer=req.llm, timeout=600)
            result = json.loads(clean_json(content))
        except Exception as e:
            print(f"Tailor resume error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

        # ── ConstraintEngine validation ─────────────────────────────────
        validation = constraints_engine.validate_tailored_resume(
            user_data, result, evidence_text=evidence_text,
        )

        repair_actions = []
        if not validation.ok:
            print(f"[tailor-resume] {len(validation.fatal_violations)} fatal violations, attempting auto-repair")
            result, repair_actions = constraints_engine.auto_repair(result, validation, user_data)
            # Re-validate after repair
            validation_after = constraints_engine.validate_tailored_resume(
                user_data, result, evidence_text=evidence_text,
            )
        else:
            validation_after = validation

        # Attach validation diagnostics to response so UI can show them
        result["_validation"] = {
            "ok": validation_after.ok,
            "violations": [v.to_dict() for v in validation_after.violations],
            "repair_actions": repair_actions,
            "style_fingerprint": style,
            "editable_regions": bundle.get("editable_regions", []),
        }
        return result

# ─────────────────────────────────────────────────────────────────
# ENDPOINT 7: PDF GENERATION
# ─────────────────────────────────────────────────────────────────
def _merge_tailored_into_master(master: dict, data: dict) -> dict:
    """Apply tailor-resume output onto a copy of master profile data."""
    merged = json.loads(json.dumps(master))  # deep copy

    # 1. Tailored summary
    tailored_sum = data.get("tailored_summary") or (data.get("summary_diff") or {}).get("tailored")
    if tailored_sum:
        merged["summary"] = tailored_sum

    # 2. Tailored experience bullets
    if data.get("experience"):
        tailored_by_company = {
            (te.get("company","").lower().strip()[:30]): te for te in data["experience"]
        }
        for src_exp in merged.get("experience", []):
            key = src_exp.get("company","").lower().strip()[:30]
            te = tailored_by_company.get(key)
            if te and te.get("bullets"):
                new_bullets = [b.get("text","").strip() for b in te["bullets"] if (b.get("text") or "").strip()]
                if new_bullets:
                    src_exp["details"] = new_bullets
                    src_exp["bullets"] = new_bullets

    # 3. Project selection — explicit list, or default to top 3 (1-page constraint)
    project_library = merged.get("project_library") or merged.get("projects") or []
    if data.get("selected_projects"):
        target_titles = {t.lower().strip() for t in data["selected_projects"]}
        filtered = [p for p in project_library if p.get("title","").lower().strip() in target_titles]
        if filtered:
            merged["projects"] = filtered[:3]
    elif merged.get("projects") and len(merged["projects"]) > 3:
        # No selection provided → keep only top 3 to fit 1 page
        merged["projects"] = merged["projects"][:3]

    # 3b. Apply tailored skills if provided
    if data.get("tailored_skills"):
        merged["skills"] = {**merged.get("skills", {}), **data["tailored_skills"]}

    # 4. Dedup: don't repeat publications under projects
    if merged.get("publications") and merged.get("projects"):
        pub_titles = {p.get("title","").lower().strip() for p in merged["publications"]}
        merged["projects"] = [p for p in merged["projects"]
                              if p.get("title","").lower().strip() not in pub_titles]
    return merged


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
    return env.from_string(template_str).render(**master)


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
        profile_dir = _profile_dir(pid)
        master = _enrich_profile_with_resume_sources(load_pdata(pid))

        # 1. Merge
        merged = _merge_tailored_into_master(master, data)
        log.debug(f"[generate-pdf] merged data — projects={len(merged.get('projects',[]))}")

        # 2. Render LaTeX from template
        try:
            rendered_tex = _render_tex_from_master(merged)
            log.debug(f"[generate-pdf] LaTeX rendered — chars={len(rendered_tex)}")
        except Exception as e:
            log.error(f"[generate-pdf] Template render failed — pid={pid}: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Template render failed: {e}")

        # 3. Validate LaTeX balanced before compile
        ok, problems = latex_ast.validate_balanced(rendered_tex)
        if not ok:
            log.warning(f"[generate-pdf] LaTeX balance check warnings — {problems}")
            # don't reject — try to compile anyway (warnings are common)

        # 4. Compile with retry + repair
        backend_dir = os.path.dirname(__file__)
        ats_expected = {
            "name": master.get("contact_info", {}).get("name", ""),
            "email": master.get("contact_info", {}).get("email", ""),
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
                        "warnings": result.warnings,
                        "latex_balance_problems": problems,
                    },
                )
            except Exception as e:
                log.warning(f"[generate-pdf] Variant save failed (non-fatal) — pid={pid}: {e}")

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
