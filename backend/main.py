from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import requests
import json
import os
import subprocess
from jinja2 import Environment, BaseLoader
import asyncio
#from openai import OpenAI
app = FastAPI()

processing_lock = asyncio.Lock()
# --- CONFIGURATION ---
# Replace this string with the EXACT name from 'ollama list'
# Examples: "qwen2.5-coder", "qwen2.5-coder:7b", "qwen2.5:14b"
OLLAMA_MODEL = "ai/qwen3-coder" 

# If you are running Docker, ensure port 11434 is mapped (-p 11434:11434)
OLLAMA_API_URL = "http://localhost:12434/engines/llama.cpp/v1/chat/completions"

PDF_OUTPUT_DIR = os.path.join(os.getcwd(), "generated_resumes")
os.makedirs(PDF_OUTPUT_DIR, exist_ok=True)


# ---------------------

# 1. Security: Allow Chrome Extension to talk to us
origins = ["chrome-extension://*", "http://localhost", "*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
class JobRequest(BaseModel):
    jd_text: str

def escape_latex_chars(text):
    """
    Escapes reserved LaTeX characters like &, %, $, #, _
    Example: "C++ & Python" -> "C++ \& Python"
    """
    if not isinstance(text, str):
        return text
    
    chars = {
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\^{}"
    }
    # Replace characters
    for char, replacement in chars.items():
        text = text.replace(char, replacement)
    return text


def load_master_data():
    """Safely load the JSON with error checking"""
    try:
        # Force UTF-8 to avoid Windows encoding issues
        with open("master_data.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print("ERROR: master_data.json not found in backend folder.")
        return "{}"
    except json.JSONDecodeError as e:
        print(f"ERROR: Your master_data.json has invalid syntax! {e}")
        return "{}"

@app.get("/health")
def health_check():
    return {"message": f"Server is Online. Using model: {OLLAMA_MODEL}"}

@app.post("/analyze")
async def analyze_job(request: JobRequest):
    print("Request Received. waiting for processing lock...")
    async with processing_lock:
        
        print(f"Analyzing with {OLLAMA_MODEL}...") 

        user_data = load_master_data()
        if user_data == "{}":
            return {"role": "Error", "score": "0%", "skills_matched": [], "missing_skill": "Check Server Logs for JSON Error"}
        # 3. The Prompt (Optimized for Qwen Coder)
        # Qwen Coder is excellent at following code/JSON structures.
        prompt = f"""
        You are a Career Strategist. 
        
        CANDIDATE PROFILE (JSON):
        {json.dumps(user_data)}

        JOB DESCRIPTION:
        "{request.jd_text[:3000]}..." 
        
        TASK:
        1. Identify the Job Role.
        2. List 3 key skills from the JD that the candidate MATCHES.
        3. List at least 1 missing skill (gap).
        4. Calculate a 'Match Score' (0-100%).
        5. Write a 'Tailored Summary' (2-3 sentences) for the resume that emphasizes skills from the JD that the candidate has.
        6. Select the 3 most relevant projects from the candidate's list.Return their exact "title" from the profile.
        
        OUTPUT FORMAT (JSON ONLY):
        {{ 
            "role": "Job Title", 
            "skills_matched": ["Skill A", "Skill B"], 
            "missing_skill": "Skill C",
            "score": "85%" 
            "tailored_summary": "Tailored Summary text ..",
            "selectedprojects": ["Project title 1", "Project title 2", "Project title 3"]
        }}
        """
        data = {
        "model": "ai/qwen3-coder",
        "messages": [
            {
                "role": "system","content": "You are a helpful JSON-speaking assistant."
            },
            {
                "role": "user",
                "content": prompt
            }
        ]
    }
        # 4. Call Ollama (Docker/Local)
        try:
            response = requests.post(
                OLLAMA_API_URL,
                json=data
            )
            
            
            # 5. Process & Return the Answer
            if response.status_code != 200:
                print(f"Ollama Error: {response.text}") 
                raise HTTPException(status_code=500, detail=f"Ollama Error: {response.text}")

            content = response.json()["choices"][0]["message"]["content"]
            print(content)
            
            # Clean Markdown wrapper if present
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].strip()
            # Sometimes Qwen is so polite it adds text before the JSON. 
            # This parsing ensures we just get the data.
            return json.loads(content)

        except Exception as e:
            print(f"Error: {e}")
            raise HTTPException(status_code=500, detail=str(e))


@app.post("/generate-pdf")
async def generate_pdf(data: dict):

    print("Request Received. waiting for processing lock...")
    async with processing_lock:
        print("Generating PDF...")
    
        master = load_master_data()
        
        # Update Summary
        master["summary"] = data.get("tailored_summary", master.get("summary", ""))
        
        # Filter Projects
        if "selected_projects" in data and data["selected_projects"]:
            target_titles = [t.lower().strip() for t in data["selected_projects"]]
            if "projects" in master:
                filtered_projects = [
                    p for p in master["projects"] 
                    if p.get("title", "").lower().strip() in target_titles
                ]
                if filtered_projects:
                    master["projects"] = filtered_projects

        # --- FIX: USE ENVIRONMENT FOR CUSTOM DELIMITERS ---
        try:
            with open("resume_template.tex", "r") as f:
                template_str = f.read()
            
            # We configure Jinja2 to use LaTeX-friendly delimiters
            # Logic (Loops): \BLOCK{ for x in y }
            # Variables (Print): \VAR{ variable_name }
            env = Environment(
                block_start_string='\\BLOCK{',
                block_end_string='}',
                variable_start_string='\\VAR{',   # <--- CHANGED to \VAR{
                variable_end_string='}',
                comment_start_string='\\#{',
                comment_end_string='}',
                loader=BaseLoader()
            )
            
            env.filters['latex'] = escape_latex_chars
            template = env.from_string(template_str)
            rendered_tex = template.render(**master)
            
            tex_filename = "tailored_resume.tex"
            with open(tex_filename, "w", encoding="utf-8") as f:
                f.write(rendered_tex)

            # Compile using Docker
            cwd = os.getcwd()
            cmd = [
                "docker", "run", "--rm",
                "-v", f"{cwd}:/data",
                "-w", "/data",
            "texlive/texlive:latest",
                "pdflatex",
                "-interaction=nonstopmode",
                tex_filename
            ]
            subprocess.run(cmd, check=True)
            
            return FileResponse("tailored_resume.pdf", media_type="application/pdf", filename="tailored_resume.pdf")

        except Exception as e:
            print(f"PDF Generation Failed: {e}")
            raise HTTPException(status_code=500, detail=f"PDF Compilation Failed: {str(e)}")