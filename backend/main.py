from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
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
    history: list = [] # List of {"role": "user", "content": "..."}

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

# --- ENDPOINT 4: PDF GENERATION ---
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