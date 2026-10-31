"""
FastAPI Server for the Antigravity Autonomous Resume Engine.
Provides REST API endpoints for real-time JD ingestion, ATS deconstruction,
LaTeX compilation, high-DPI rasterization, and PDF distribution.
"""

import os
from typing import Optional
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse

from src.schemas import (
    GenerateResumeRequest,
    GenerateResumeResponse,
    TailoredResumeAST,
    CompilationResult,
    FitAuditReport,
)
from src.pipeline import generate_tailored_resume
from src.latex_compiler import compile_and_verify_single_page
from src.knowledge_graph import load_candidate_knowledge_graph

app = FastAPI(
    title="Antigravity Autonomous Resume Engine API",
    description="Truth-Grounded Qualification Synthesis & Closed-Loop LaTeX Compiler for Chandra Rup Daka",
    version="2.0.0",
)

# Enable CORS for Chrome Extension and Web Dashboard
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

OUTPUT_DIR = os.path.abspath("output")
os.makedirs(OUTPUT_DIR, exist_ok=True)


TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


@app.get("/")
def root(request: Request):
    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        dashboard_path = os.path.join(TEMPLATES_DIR, "dashboard.html")
        if os.path.exists(dashboard_path):
            with open(dashboard_path, "r", encoding="utf-8") as f:
                return HTMLResponse(content=f.read())

    return {
        "service": "Antigravity Autonomous Resume Engine",
        "candidate": "Chandra Rup Daka",
        "status": "online",
        "version": "2.0.0",
        "endpoints": [
            "GET / (Browser Dashboard / API Metadata)",
            "GET /dashboard",
            "POST /api/generate-resume",
            "GET /api/health",
            "GET /api/candidate-knowledge",
            "POST /api/verify-latex",
            "GET /api/download/{filename}",
        ],
    }


@app.get("/dashboard")
def get_dashboard():
    dashboard_path = os.path.join(TEMPLATES_DIR, "dashboard.html")
    if not os.path.exists(dashboard_path):
        raise HTTPException(status_code=404, detail="Dashboard template not found.")
    with open(dashboard_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/api/health")
def health_check():
    import shutil
    has_pdflatex = shutil.which("pdflatex") is not None
    has_pdfinfo = shutil.which("pdfinfo") is not None
    has_pdftoppm = shutil.which("pdftoppm") is not None
    has_gemini = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))

    return {
        "status": "healthy" if (has_pdflatex and has_pdfinfo) else "degraded",
        "binaries": {
            "pdflatex": has_pdflatex,
            "pdfinfo": has_pdfinfo,
            "pdftoppm": has_pdftoppm,
        },
        "gemini_api_configured": has_gemini,
    }


@app.get("/api/candidate-knowledge")
def get_candidate_knowledge():
    try:
        kg = load_candidate_knowledge_graph()
        return kg
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/generate-resume", response_model=GenerateResumeResponse)
def api_generate_resume(req: GenerateResumeRequest):
    """
    Accepts raw job description text and parameters, runs the full synthesis pipeline,
    and returns verified compilation results, fit audit, and output paths.
    """
    if not req.raw_jd.strip():
        raise HTTPException(status_code=400, detail="Job description text cannot be empty.")

    resp = generate_tailored_resume(
        raw_jd=req.raw_jd,
        company_name=req.company_name,
        role_title=req.role_title,
        track_override=req.track_override,
        output_dir=OUTPUT_DIR,
        output_stem=req.output_stem,
    )

    if not resp.success:
        return JSONResponse(
            status_code=422,
            content=resp.model_dump(),
        )

    return resp


@app.post("/api/verify-latex")
def api_verify_latex(ast: TailoredResumeAST):
    """Compiles a candidate AST and returns single-page verification metrics."""
    res = compile_and_verify_single_page(ast, output_dir=OUTPUT_DIR)
    return res.model_dump()


@app.get("/api/download/{filename}")
def download_artifact(filename: str):
    """Serves compiled PDF, LaTeX, PNG, or Markdown artifacts."""
    file_path = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail=f"File '{filename}' not found in output directory.")

    media_type = "application/octet-stream"
    if filename.endswith(".pdf"):
        media_type = "application/pdf"
    elif filename.endswith(".png"):
        media_type = "image/png"
    elif filename.endswith(".tex"):
        media_type = "text/plain"
    elif filename.endswith(".md"):
        media_type = "text/markdown"

    return FileResponse(file_path, media_type=media_type, filename=filename)


@app.post("/api/tailor-v2")
def api_tailor_v2(payload: dict):
    """
    Adapter endpoint providing backwards-compatible integration with SmartApplyAI Chrome Extension.
    Accepts {'jd': str, 'company': str, 'title': str} and returns generated resume data.
    """
    raw_jd = payload.get("jd") or payload.get("job_description") or ""
    company = payload.get("company")
    title = payload.get("title")

    if not raw_jd:
        raise HTTPException(status_code=400, detail="Missing required 'jd' in payload.")

    resp = generate_tailored_resume(
        raw_jd=raw_jd,
        company_name=company,
        role_title=title,
        output_dir=OUTPUT_DIR,
    )
    return resp.model_dump()
