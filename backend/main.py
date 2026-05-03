from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
import requests
import json
import os
import subprocess
import asyncio
from jinja2 import Environment, BaseLoader

app = FastAPI()

# --- CONFIGURATION ---
OLLAMA_MODEL = "ai/qwen3-coder" 
OLLAMA_API_URL = "http://localhost:12434/engines/llama.cpp/v1/chat/completions"
PDF_OUTPUT_DIR = os.path.join(os.getcwd(), "generated_resumes")
os.makedirs(PDF_OUTPUT_DIR, exist_ok=True)

# GLOBAL LOCK (The "Traffic Light")
processing_lock = asyncio.Lock()
# ---------------------

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

class ChatRequest(BaseModel):
    context: str
    question: str
    history: list = []

class AutofillRequest(BaseModel):
    fields: list
    jd_text: str = ""
    company: str = ""

class QuestionRequest(BaseModel):
    question: str
    jd_text: str = ""
    company: str = ""
    word_limit: int = 150

class CoverLetterRequest(BaseModel):
    company: str
    role: str
    jd_text: str = ""
    hiring_manager: str = ""

def load_master_data():
    try:
        with open("master_data.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def escape_latex_chars(text):
    if not isinstance(text, str): return text
    chars = {"&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}"}
    for char, replacement in chars.items():
        text = text.replace(char, replacement)
    return text

@app.get("/health")
def health_check():
    return {"message": "Server is Online"}

@app.get("/profile")
def get_profile():
    data = load_master_data()
    skills_count = len(data.get("skills", []))
    projects_count = len(data.get("projects", []))
    return {
        "contact_info": data.get("contact_info", {}),
        "skills_count": skills_count,
        "projects_count": projects_count,
    }

@app.get("/", response_class=HTMLResponse)
def dashboard():
    with open(os.path.join(os.path.dirname(__file__), "dashboard.html"), "r") as f:
        return f.read()

# --- ENDPOINT 1: ANALYZE (Resume Logic) ---
@app.post("/analyze")
async def analyze_job(request: JobRequest):
    print("Request Received: Analyze Job")
    async with processing_lock:
        print("Lock Acquired. Analyzing...")
        user_data = load_master_data()
        prompt = f"""
        You are a Career Strategist.
        CANDIDATE PROFILE: {json.dumps(user_data)}
        JOB DESCRIPTION: "{request.jd_text[:7000]}..." 
        
        TASK:
        1. Identify the Job Role.
        2. List at least 3 key skills from the JD that the candidate matches.
        3. List at least 1 missing skill (gap).
        4. Calculate a Match Score (0-100%).
        5. Write a tailored summary (2-3 sentences) for the resume that emphasizes skills from the JD that the candidate has.
            - CRITICAL: Write in IMPLIED FIRST PERSON (e.g., "Generative AI Engineer with 2+ years...").
            - DO NOT use the candidate's name.
            - DO NOT use pronouns like "He" or "She".
            - Focus on the skills relevant to the JD.   

        6. Select the 3 most relevant projects from the candidate's list. Return their exact "title" from the profile.
        
        OUTPUT JSON ONLY: {{ "role": "...", "skills_matched": [], "missing_skill": "...", "score": "...", "tailored_summary": "...", "selected_projects": [] }}
        """
        
        data = { "model": OLLAMA_MODEL, "messages": [{ "role": "user", "content": prompt }], "temperature": 0.2 }
        
        try:
            response = requests.post(OLLAMA_API_URL, json=data)
            content = response.json()["choices"][0]["message"]["content"]
            if "```json" in content: content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content: content = content.split("```")[1].strip()
            return json.loads(content)
        except Exception as e:
            print(f"Error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

# --- ENDPOINT 2: SUGGEST QUESTIONS (FAQ) ---
@app.post("/suggest-questions")
async def suggest_questions(request: JobRequest):
    print("Request Received: Suggest Questions")
    async with processing_lock:
        prompt = f"""
        Analyze this webpage text: "{request.jd_text[:7000]}..."
        
        Generate 3 short, specific questions a candidate should ask about this job/page.
        Examples: "What is the tech stack?", "Is visa sponsorship available?", "What is the salary range?"
        
        OUTPUT JSON LIST ONLY: ["Question 1", "Question 2", "Question 3"]
        """
        data = { "model": OLLAMA_MODEL, "messages": [{ "role": "user", "content": prompt }], "temperature": 0.4 }
        
        try:
            response = requests.post(OLLAMA_API_URL, json=data)
            content = response.json()["choices"][0]["message"]["content"]
            if "```json" in content: content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content: content = content.split("```")[1].strip()
            return json.loads(content)
        except Exception as e:
             # Fallback if AI fails
            return ["What are the key requirements?", "Is this a remote role?", "What is the company culture?"]

# --- ENDPOINT 3: CHAT ---
@app.post("/chat")
async def chat_with_page(request: ChatRequest):
    print("Request Received: Chat")
    async with processing_lock:
        # Build context-aware system prompt
        system_msg = f"""You are a helpful assistant. Answer the user's question based ONLY on the following webpage context. If the answer isn't in the context, say "I couldn't find that info on this page."
        
        WEBPAGE CONTEXT:
        {request.context[:3000]}
        """
        
        messages = [{"role": "system", "content": system_msg}]
        if request.history:
            messages.extend(request.history)
        messages.append({"role": "user", "content": request.question})
        
        data = { "model": OLLAMA_MODEL, "messages": messages, "stream": False }
        
        try:
            response = requests.post(OLLAMA_API_URL, json=data)
            return {"answer": response.json()["choices"][0]["message"]["content"]}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

# --- ENDPOINT 4: AUTOFILL ---
@app.post("/autofill")
async def autofill_fields(request: AutofillRequest):
    print("Request Received: Autofill")
    async with processing_lock:
        user_data = load_master_data()
        autofill = user_data.get("autofill", {})
        common_answers = user_data.get("common_answers", {})

        field_list = json.dumps(request.fields[:40], indent=2)

        prompt = f"""
You are an expert job application assistant filling out a form on behalf of a candidate.

CANDIDATE PROFILE:
{json.dumps(user_data, indent=2)[:6000]}

AUTOFILL QUICK REFERENCE:
{json.dumps(autofill, indent=2)}

JOB DESCRIPTION (for context):
{request.jd_text[:3000]}

COMPANY: {request.company}

FORM FIELDS TO FILL:
{field_list}

TASK:
For EACH field in the list above, provide the best answer based on the candidate's profile.
- For simple fields (name, email, phone, address, etc.): use exact values from the profile.
- For dropdowns with options: pick the best matching option from the "options" list.
- For "work authorization": answer "Yes" if authorized.
- For "sponsorship": answer "No".
- For "years of experience": answer based on profile (2 years).
- For "willing to relocate", "remote work": answer "Yes".
- For "salary": use the autofill salary_expectation.
- For open-ended questions (summary, cover letter, why this company, etc.): write a concise, compelling answer in 2-3 sentences using the candidate's actual experience.
- For EEO/demographic questions (gender, ethnicity, veteran, disability): use values from autofill.
- If you cannot determine a good answer, return "SKIP".

OUTPUT: A JSON object where keys are EXACTLY the "label" value from each field, and values are the answers.
Example: {{"First Name": "Chandra Rup", "Email": "chandrarupdaka@gmail.com", "Tell us about yourself": "Generative AI Engineer with..."}}

OUTPUT JSON ONLY, no explanation.
        """

        data = {"model": OLLAMA_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.1}
        try:
            response = requests.post(OLLAMA_API_URL, json=data, timeout=60)
            content = response.json()["choices"][0]["message"]["content"]
            if "```json" in content: content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content: content = content.split("```")[1].strip()
            return json.loads(content)
        except Exception as e:
            print(f"Autofill LLM error: {e}. Falling back to rule-based.")
            return build_rule_based_answers(request.fields, autofill, user_data)

def build_rule_based_answers(fields, autofill, user_data):
    import re
    answers = {}
    contact = user_data.get("contact_info", {})
    name = contact.get("name", "")
    parts = name.split(" ", 1)

    RULES = {
        r"first\s*name|given\s*name": parts[0] if parts else "",
        r"last\s*name|family\s*name|surname": parts[1] if len(parts) > 1 else "",
        r"^(full\s*)?name$|^your\s*name|^name\b": name,
        r"e[\s-]?mail": contact.get("email", ""),
        r"phone|mobile|cell": contact.get("phone", ""),
        r"linkedin": contact.get("linkedin", ""),
        r"github": contact.get("github", ""),
        r"website|portfolio": contact.get("github", ""),
        r"city": autofill.get("city", "Houston"),
        r"state|province": autofill.get("state", "TX"),
        r"zip|postal": autofill.get("zip", ""),
        r"country": autofill.get("country", "United States"),
        r"salary|compensation|pay": autofill.get("salary_expectation", "120000"),
        r"years.*(of\s*)?experience": autofill.get("years_of_experience", "2"),
        r"start\s*date|available": autofill.get("start_date", "Immediately"),
        r"work\s*auth|legally\s*(authorized|eligible)": autofill.get("work_authorization", "Yes"),
        r"sponsor": autofill.get("requires_sponsorship", "No"),
        r"relocat": autofill.get("willing_to_relocate", "Yes"),
        r"gender": autofill.get("gender", "Male"),
        r"veteran|military": autofill.get("veteran_status", "I am not a protected veteran"),
        r"disability": autofill.get("disability_status", "I don't wish to answer"),
        r"ethnic|race": autofill.get("ethnicity", "Asian"),
        r"summary|tell us about|about yourself|introduce": user_data.get("summary", ""),
        r"current\s*(company|employer)": autofill.get("current_company", "Accenture"),
        r"current\s*(title|position)": autofill.get("current_title", "Advanced App Engineering Analyst"),
        r"notice\s*period": autofill.get("notice_period", "2 weeks"),
        r"referral|how did you hear": "LinkedIn",
    }

    for field in fields:
        label = field.get("label", "")
        for pattern, value in RULES.items():
            if re.search(pattern, label, re.I) and value:
                answers[label] = value
                break

    return answers

# --- ENDPOINT 5: ANSWER SINGLE QUESTION ---
@app.post("/answer-question")
async def answer_question(request: QuestionRequest):
    print("Request Received: Answer Question")
    async with processing_lock:
        user_data = load_master_data()
        prompt = f"""
You are an expert job application assistant. Answer the following application question on behalf of the candidate.

CANDIDATE PROFILE:
{json.dumps(user_data, indent=2)[:5000]}

JOB DESCRIPTION CONTEXT:
{request.jd_text[:2000]}

COMPANY: {request.company}

QUESTION: "{request.question}"

INSTRUCTIONS:
- Write in implied first person (e.g., "Experienced in...", "Skilled at...", "With 2 years of...")
- Do NOT use pronouns like "He", "She", "I" — implied first person only
- Be specific and reference real details from the candidate's profile
- Keep the answer to approximately {request.word_limit} words
- Do NOT include any preamble — output ONLY the answer text

OUTPUT: Plain text answer only.
        """
        data = {"model": OLLAMA_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.3}
        try:
            response = requests.post(OLLAMA_API_URL, json=data, timeout=60)
            content = response.json()["choices"][0]["message"]["content"].strip()
            return {"answer": content}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

# --- ENDPOINT 6: COVER LETTER ---
@app.post("/cover-letter")
async def generate_cover_letter(request: CoverLetterRequest):
    print("Request Received: Cover Letter")
    async with processing_lock:
        user_data = load_master_data()
        contact = user_data.get("contact_info", {})
        today = __import__("datetime").date.today().strftime("%B %d, %Y")

        prompt = f"""
You are an expert career coach writing a compelling, personalized cover letter.

CANDIDATE PROFILE:
{json.dumps(user_data, indent=2)[:5000]}

TARGET COMPANY: {request.company}
TARGET ROLE: {request.role}
HIRING MANAGER: {request.hiring_manager or "Hiring Manager"}
JOB DESCRIPTION:
{request.jd_text[:3000]}

INSTRUCTIONS:
- Write a complete, professional cover letter (3-4 paragraphs)
- Opening: Express genuine interest in the specific role and company
- Body paragraph 1: Highlight 2-3 most relevant technical skills/experiences that match the JD
- Body paragraph 2: Mention a specific project or achievement that shows impact
- Closing: Express enthusiasm and call to action
- Tone: Professional but personable, confident without being arrogant
- Length: 280-350 words
- Use the candidate's REAL name, contact info, and experiences
- Make it feel personal to THIS company — reference what makes {request.company} interesting
- Do NOT use clichés like "I am writing to express my interest" or "To Whom It May Concern"
- Start directly with an engaging opening line

OUTPUT: The complete cover letter text only. No explanation, no markdown.
        """

        data = {"model": OLLAMA_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.5}
        try:
            response = requests.post(OLLAMA_API_URL, json=data, timeout=90)
            letter = response.json()["choices"][0]["message"]["content"].strip()
            return {
                "cover_letter": letter,
                "metadata": {
                    "company": request.company,
                    "role": request.role,
                    "candidate": contact.get("name", ""),
                    "generated_date": today
                }
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

# --- ENDPOINT 7: PDF GENERATION ---
@app.post("/generate-pdf")
async def generate_pdf(data: dict):
    print("Request Received: PDF Generation")
    async with processing_lock:
        # ... (PASTE YOUR EXISTING PDF LOGIC HERE - IT WAS CORRECT) ...
        # (I am omitting it to save space, but keep your existing logic!)
        # Ensure you use the 'env' with custom delimiters from the previous step.
        master = load_master_data()
        master["summary"] = data.get("tailored_summary", master.get("summary", ""))
        
        if "selected_projects" in data and data["selected_projects"]:
            target_titles = [t.lower().strip() for t in data["selected_projects"]]
            if "projects" in master:
                filtered = [p for p in master["projects"] if p.get("title", "").lower().strip() in target_titles]
                if filtered: master["projects"] = filtered
        # --- NEW: DEDUPLICATION LOGIC ---
        # Remove projects that are already listed in Publications to avoid double counting
        if "publications" in master and "projects" in master:
            pub_titles = {pub["title"].lower().strip() for pub in master["publications"]}
            # Keep project ONLY if its title is NOT in publications
            master["projects"] = [
                p for p in master["projects"] 
                if p["title"].lower().strip() not in pub_titles
            ]
        # --------------------------------
        try:
            with open("resume_template.tex", "r") as f: template_str = f.read()
            env = Environment(block_start_string='\\BLOCK{', block_end_string='}', variable_start_string='\\VAR{', variable_end_string='}', comment_start_string='\\#{', comment_end_string='}', loader=BaseLoader())
            env.filters['latex'] = escape_latex_chars
            
            template = env.from_string(template_str)
            rendered_tex = template.render(**master)
            
            with open("tailored_resume.tex", "w", encoding="utf-8") as f: f.write(rendered_tex)
            
            cwd = os.getcwd()
            cmd = ["docker", "run", "--rm", "-v", f"{cwd}:/data", "-w", "/data", "texlive/texlive:latest", "pdflatex", "-interaction=nonstopmode", "tailored_resume.tex"]
            subprocess.run(cmd, check=True)
            return FileResponse("tailored_resume.pdf", media_type="application/pdf", filename="tailored_resume.pdf")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"PDF Failed: {str(e)}")