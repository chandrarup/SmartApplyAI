from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
import uuid, hashlib
from datetime import date as _date
from pydantic import BaseModel
import requests as http_requests
import json
import os
import re
import subprocess
import asyncio
from jinja2 import Environment, BaseLoader

app = FastAPI()

# --- CONFIGURATION ---
OLLAMA_MODEL = "ai/qwen3-coder"
OLLAMA_API_URL = "http://localhost:12434/engines/llama.cpp/v1/chat/completions"
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
    """One-time migration of existing master_data.json → profiles/default/"""
    if load_profiles_meta():
        return
    pid = "default"
    d = _profile_dir(pid)
    os.makedirs(d, exist_ok=True)
    src = os.path.join(os.path.dirname(__file__), "master_data.json")
    dst = os.path.join(d, "master_data.json")
    data = {}
    if os.path.exists(src):
        with open(src) as f:
            data = json.load(f)
        if not os.path.exists(dst):
            with open(dst, "w") as f:
                json.dump(data, f, indent=4)
    asrc = os.path.join(os.path.dirname(__file__), "applications.json")
    adst = os.path.join(d, "applications.json")
    if os.path.exists(asrc) and not os.path.exists(adst):
        with open(asrc) as f:
            apps = json.load(f)
        with open(adst, "w") as f:
            json.dump(apps, f, indent=2)
    name = data.get("contact_info", {}).get("name", "My Profile") or "My Profile"
    save_profiles_meta([{"id": pid, "name": name, "color": PROFILE_COLORS[0],
                         "created_at": str(_date.today()), "pin_hash": ""}])

migrate_to_profiles()

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
def call_ollama(messages: list, temperature: float = 0.3, timeout: int = 60) -> str:
    data = {"model": OLLAMA_MODEL, "messages": messages, "stream": False, "temperature": temperature}
    response = http_requests.post(OLLAMA_API_URL, json=data, timeout=timeout)
    return response.json()["choices"][0]["message"]["content"]

def call_claude(messages: list, temperature: float = 0.3, system: str = "") -> str:
    import anthropic
    api_key = get_anthropic_key()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
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
    return message.content[0].text

def call_llm(messages: list, temperature: float = 0.3, system: str = "",
             prefer: str = "ollama", timeout: int = 60) -> str:
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
            print(f"[LLM] {provider} failed: {e}. Trying next...")
    raise RuntimeError(f"All LLM providers failed. Last error: {last_err}")

def clean_json(raw: str) -> str:
    """Strip markdown fences from LLM JSON output."""
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0]
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0]
    return raw.strip()

# ─────────────────────────────────────────────────────────────────
# RULE-BASED AUTOFILL FALLBACK (no LLM needed)
# ─────────────────────────────────────────────────────────────────
def build_rule_based_answers(fields, autofill, user_data):
    answers = {}
    contact = user_data.get("contact_info", {})
    name = contact.get("name", "")

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
        (r"phone|mobile|cell|telephone", contact.get("phone", "")),
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

        # ── Misc ─────────────────────────────────────────────────────────────
        (r"pronouns", "He/Him"),
        (r"additional\s*information|anything\s*else|additional\s*comments",
         user_data.get("summary", "")),
    ]

    for field in fields:
        label = field.get("label", "")
        if not label:
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

@app.get("/profile")
def get_profile():
    return load_master_data()

def save_master_data(data: dict):
    with open("master_data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

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
    """Return which LLM providers are available."""
    ollama_ok = False
    claude_ok = bool(get_anthropic_key())
    try:
        r = http_requests.get("http://localhost:12434/health", timeout=2)
        ollama_ok = r.status_code == 200
    except Exception:
        pass
    return {"ollama": ollama_ok, "claude": claude_ok, "claude_key_set": claude_ok}

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
    return load_pdata(get_pid(request))

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
    print("Request Received: Analyze Job")
    async with processing_lock:
        user_data = load_pdata(get_pid(request))
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
            return json.loads(clean_json(content))
        except Exception as e:
            print(f"Analyze error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

# ─────────────────────────────────────────────────────────────────
# ENDPOINT 2: SUGGEST QUESTIONS
# ─────────────────────────────────────────────────────────────────
@app.post("/suggest-questions")
async def suggest_questions(req: JobRequest, request: Request):
    print("Request Received: Suggest Questions")
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
    print("Request Received: Chat")
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
@app.post("/autofill")
async def autofill_fields(req: AutofillRequest, request: Request):
    print("Request Received: Autofill")
    async with processing_lock:
        user_data = load_pdata(get_pid(request))
        autofill = user_data.get("autofill", {})

        field_list = json.dumps(req.fields[:40], indent=2)

        prompt = f"""You are an expert job application assistant filling out a form on behalf of a candidate.

CANDIDATE PROFILE:
{json.dumps(user_data, indent=2)[:6000]}

AUTOFILL QUICK REFERENCE:
{json.dumps(autofill, indent=2)}

JOB DESCRIPTION: {req.jd_text[:2000]}
COMPANY: {req.company}

FORM FIELDS TO FILL:
{field_list}

TASK: For EACH field, provide the best answer.
- Name fields: use exact values from profile.
- Dropdowns (options list present): pick ONLY from the provided options.
- Work authorization: "Yes". Sponsorship required: "Yes".
- Years of experience: {autofill.get("years_of_experience", "2")}
- Salary: {autofill.get("salary_expectation", "120000")}
- Open-ended questions: 2-3 concise sentences using real candidate experience.
- EEO fields: use autofill values.
- If unknown: "SKIP".

OUTPUT: JSON object where keys are EXACTLY the "label" values from above.
Example: {{"First Name": "{autofill.get("first_name","Chandra Rup")}", "Email": "{user_data.get("contact_info",{}).get("email","")}"}}

OUTPUT JSON ONLY."""

        try:
            content = call_llm([{"role": "user", "content": prompt}],
                               temperature=0.1, prefer=req.llm, timeout=90)
            return json.loads(clean_json(content))
        except Exception as e:
            print(f"Autofill LLM error: {e}. Falling back to rule-based.")
            return build_rule_based_answers(req.fields, autofill, user_data)

# ─────────────────────────────────────────────────────────────────
# ENDPOINT 5: ANSWER SINGLE QUESTION
# ─────────────────────────────────────────────────────────────────
@app.post("/answer-question")
async def answer_question(req: QuestionRequest, request: Request):
    print("Request Received: Answer Question")
    async with processing_lock:
        user_data = load_pdata(get_pid(request))
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
    print("Request Received: Cover Letter")
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
                              temperature=0.5, prefer=req.llm, timeout=90)
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
# ENDPOINT 7: PDF GENERATION
# ─────────────────────────────────────────────────────────────────
@app.post("/generate-pdf")
async def generate_pdf(data: dict, request: Request):
    print("Request Received: PDF Generation")
    async with processing_lock:
        master = load_pdata(get_pid(request))
        master["summary"] = data.get("tailored_summary", master.get("summary", ""))

        if "selected_projects" in data and data["selected_projects"]:
            target_titles = [t.lower().strip() for t in data["selected_projects"]]
            if "projects" in master:
                filtered = [p for p in master["projects"]
                            if p.get("title", "").lower().strip() in target_titles]
                if filtered:
                    master["projects"] = filtered

        if "publications" in master and "projects" in master:
            pub_titles = {pub["title"].lower().strip() for pub in master["publications"]}
            master["projects"] = [p for p in master["projects"]
                                  if p["title"].lower().strip() not in pub_titles]
        try:
            with open("resume_template.tex", "r") as f:
                template_str = f.read()
            env = Environment(
                block_start_string='\\BLOCK{', block_end_string='}',
                variable_start_string='\\VAR{', variable_end_string='}',
                comment_start_string='\\#{', comment_end_string='}',
                loader=BaseLoader()
            )
            env.filters['latex'] = escape_latex_chars
            template = env.from_string(template_str)
            rendered_tex = template.render(**master)

            with open("tailored_resume.tex", "w", encoding="utf-8") as f:
                f.write(rendered_tex)

            cwd = os.getcwd()
            cmd = ["docker", "run", "--rm", "-v", f"{cwd}:/data", "-w", "/data",
                   "texlive/texlive:latest", "pdflatex", "-interaction=nonstopmode",
                   "tailored_resume.tex"]
            subprocess.run(cmd, check=True)
            return FileResponse("tailored_resume.pdf",
                                media_type="application/pdf",
                                filename="tailored_resume.pdf")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"PDF Failed: {str(e)}")
