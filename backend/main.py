from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import json
#from openai import OpenAI
app = FastAPI()

# --- CONFIGURATION ---
# Replace this string with the EXACT name from 'ollama list'
# Examples: "qwen2.5-coder", "qwen2.5-coder:7b", "qwen2.5:14b"
OLLAMA_MODEL = "ai/qwen3-coder" 

# If you are running Docker, ensure port 11434 is mapped (-p 11434:11434)
OLLAMA_API_URL = "http://localhost:12434/engines/llama.cpp/v1/chat/completions"
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

# 2. Data Model
class JobRequest(BaseModel):
    jd_text: str

@app.get("/health")
def health_check():
    return {"message": f"Server is Online. Using model: {OLLAMA_MODEL}"}

@app.post("/analyze")
def analyze_job(request: JobRequest):
    print(f"Analyzing with {OLLAMA_MODEL}...") 

    # 3. The Prompt (Optimized for Qwen Coder)
    # Qwen Coder is excellent at following code/JSON structures.
    prompt = f"""
    You are a Career Assistant. Analyze this Job Description:
    "{request.jd_text[:3000]}..." 
    
    Task:
    1. Identify the Job Role.
    2. List top 3 technical skills required.
    3. Give a 'Match Score' (0-100%) assuming the candidate is a Data Science Student.
    
    Return ONLY a valid JSON object. Do not write markdown blocks (```json). 
    Format:
    {{ "role": "...", "skills": ["..."], "score": "..." }}
    """
    data = {
    "model": "ai/qwen3-coder",
    "messages": [
        {
            "role": "system",
            "content": "You are a helpful assistant."
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
            raise HTTPException(status_code=500, detail=f"Ollama Error: {response.text}")

        ai_data = response.json()
        print(response.json()["choices"][0]["message"]["content"])
        
        # Sometimes Qwen is so polite it adds text before the JSON. 
        # This parsing ensures we just get the data.
        return json.loads(response.json()["choices"][0]["message"]["content"])

    except Exception as e:
        print(f"Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))