from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
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
        # Identity — must come before generic "name" match
        (r"first\s*name|given\s*name|forename", first_name),
        (r"last\s*name|family\s*name|surname", last_name),
        (r"^(full\s*)?name$|^your\s*name$|^name\b", name),
        # Contact
        (r"e[\s-]?mail", contact.get("email", "")),
        (r"phone|mobile|cell|telephone", contact.get("phone", "")),
        (r"linkedin", contact.get("linkedin", "")),
        (r"github", contact.get("github", "")),
        (r"website|portfolio", contact.get("github", "")),
        # Work authorization — MUST be before state/country patterns
        (r"work\s*auth|legally\s*(authorized|eligible)|authorized\s*to\s*work|authorized.*work",
         autofill.get("work_authorization", "Yes")),
        (r"require.*sponsor|need.*sponsor|sponsor.*required|visa\s*sponsor",
         autofill.get("requires_sponsorship", "Yes")),
        (r"\bsponsor\b", autofill.get("requires_sponsorship", "Yes")),
        # Location — use word boundaries to avoid matching "United States"
        (r"\bcity\b|\blocality\b", autofill.get("city", "Houston")),
        (r"^state$|^province$|\bstate\s*/\s*province\b|\bstate\b.*\bprovince\b",
         autofill.get("state", "TX")),
        (r"\bzip\b|\bpostal\b", autofill.get("zip", "77001")),
        (r"\bcountry\b", autofill.get("country", "United States")),
        # Compensation & logistics
        (r"salary|compensation|pay\b|desired\s*pay", autofill.get("salary_expectation", "120000")),
        (r"years.*(of\s*)?experience|experience.*years", autofill.get("years_of_experience", "2")),
        (r"start\s*date|when.*available|available.*start", autofill.get("start_date", "Immediately")),
        (r"relocat", autofill.get("willing_to_relocate", "Yes")),
        (r"remote|hybrid|work.*arrangement", "Open to remote or hybrid"),
        (r"notice\s*period|notice\b", autofill.get("notice_period", "2 weeks")),
        # Current employment
        (r"current\s*(company|employer|organization)", autofill.get("current_company", "Accenture")),
        (r"current\s*(job\s*)?(title|position|role)|job\s*title|position\s*title",
         autofill.get("current_title", "Advanced App Engineering Analyst")),
        # EEO & demographic
        (r"\bgender\b|\bsex\b", autofill.get("gender", "Male")),
        (r"veteran|military\s*status", autofill.get("veteran_status", "I am not a protected veteran")),
        (r"disability", autofill.get("disability_status", "I don't wish to answer")),
        (r"ethnic|race\b|racial", autofill.get("ethnicity", "Asian")),
        # Open-ended
        (r"summary|tell us about|about yourself|introduce yourself|background",
         user_data.get("summary", "")),
        (r"cover\s*letter", user_data.get("summary", "")),
        # Source / referral
        (r"referral|how did you hear|source|where did you|how did you find|how did you learn|how did you know", "LinkedIn"),
        (r"pronouns", "He/Him"),
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
    data = load_master_data()
    return {
        "contact_info": data.get("contact_info", {}),
        "skills_count": len(data.get("skills", [])),
        "projects_count": len(data.get("projects", [])),
    }

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

@app.get("/", response_class=HTMLResponse)
def dashboard():
    with open(os.path.join(os.path.dirname(__file__), "dashboard.html"), "r") as f:
        return f.read()

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

# ─────────────────────────────────────────────────────────────────
# ENDPOINT 1: ANALYZE
# ─────────────────────────────────────────────────────────────────
@app.post("/analyze")
async def analyze_job(request: JobRequest):
    print("Request Received: Analyze Job")
    async with processing_lock:
        user_data = load_master_data()
        prompt = f"""You are a Career Strategist.
CANDIDATE PROFILE: {json.dumps(user_data)}
JOB DESCRIPTION: "{request.jd_text[:7000]}"

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
                               temperature=0.2, prefer=request.llm)
            return json.loads(clean_json(content))
        except Exception as e:
            print(f"Analyze error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

# ─────────────────────────────────────────────────────────────────
# ENDPOINT 2: SUGGEST QUESTIONS
# ─────────────────────────────────────────────────────────────────
@app.post("/suggest-questions")
async def suggest_questions(request: JobRequest):
    print("Request Received: Suggest Questions")
    async with processing_lock:
        prompt = f"""Analyze this job posting:
"{request.jd_text[:7000]}"

Generate 3 short, specific questions a candidate should ask about this role.
OUTPUT JSON LIST ONLY: ["Question 1", "Question 2", "Question 3"]"""
        try:
            content = call_llm([{"role": "user", "content": prompt}],
                               temperature=0.4, prefer=request.llm)
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
async def autofill_fields(request: AutofillRequest):
    print("Request Received: Autofill")
    async with processing_lock:
        user_data = load_master_data()
        autofill = user_data.get("autofill", {})

        field_list = json.dumps(request.fields[:40], indent=2)

        prompt = f"""You are an expert job application assistant filling out a form on behalf of a candidate.

CANDIDATE PROFILE:
{json.dumps(user_data, indent=2)[:6000]}

AUTOFILL QUICK REFERENCE:
{json.dumps(autofill, indent=2)}

JOB DESCRIPTION: {request.jd_text[:2000]}
COMPANY: {request.company}

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
                               temperature=0.1, prefer=request.llm, timeout=90)
            return json.loads(clean_json(content))
        except Exception as e:
            print(f"Autofill LLM error: {e}. Falling back to rule-based.")
            return build_rule_based_answers(request.fields, autofill, user_data)

# ─────────────────────────────────────────────────────────────────
# ENDPOINT 5: ANSWER SINGLE QUESTION
# ─────────────────────────────────────────────────────────────────
@app.post("/answer-question")
async def answer_question(request: QuestionRequest):
    print("Request Received: Answer Question")
    async with processing_lock:
        user_data = load_master_data()
        prompt = f"""You are an expert job application assistant answering a question on behalf of the candidate.

CANDIDATE PROFILE:
{json.dumps(user_data, indent=2)[:5000]}

JOB DESCRIPTION: {request.jd_text[:2000]}
COMPANY: {request.company}

QUESTION: "{request.question}"

INSTRUCTIONS:
- Write in implied first person (e.g. "Experienced in...", "With 2 years of...")
- DO NOT use pronouns (I, He, She) — implied first person only
- Reference real candidate details
- Approximately {request.word_limit} words
- Output ONLY the answer text, no preamble."""

        try:
            content = call_llm([{"role": "user", "content": prompt}],
                               temperature=0.3, prefer=request.llm)
            return {"answer": content.strip()}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

# ─────────────────────────────────────────────────────────────────
# ENDPOINT 6: COVER LETTER
# ─────────────────────────────────────────────────────────────────
@app.post("/cover-letter")
async def generate_cover_letter(request: CoverLetterRequest):
    print("Request Received: Cover Letter")
    async with processing_lock:
        user_data = load_master_data()
        contact = user_data.get("contact_info", {})
        today = __import__("datetime").date.today().strftime("%B %d, %Y")

        prompt = f"""You are an expert career coach writing a compelling cover letter.

CANDIDATE PROFILE:
{json.dumps(user_data, indent=2)[:5000]}

TARGET COMPANY: {request.company}
TARGET ROLE: {request.role}
HIRING MANAGER: {request.hiring_manager or "Hiring Manager"}
JOB DESCRIPTION:
{request.jd_text[:3000]}

INSTRUCTIONS:
- Write 3-4 paragraphs, 280-350 words total
- Opening: Engaging first line (no "I am writing to express...")
- Body 1: 2-3 most relevant technical skills/experiences
- Body 2: Specific project or measurable achievement
- Closing: Enthusiasm + call to action
- Tone: Professional but personable
- Reference what makes {request.company} specifically interesting
- Use the candidate's REAL name, contact info, and experiences
- Output ONLY the cover letter text, no explanation."""

        try:
            letter = call_llm([{"role": "user", "content": prompt}],
                              temperature=0.5, prefer=request.llm, timeout=90)
            return {
                "cover_letter": letter.strip(),
                "metadata": {
                    "company": request.company,
                    "role": request.role,
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
async def generate_pdf(data: dict):
    print("Request Received: PDF Generation")
    async with processing_lock:
        master = load_master_data()
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
