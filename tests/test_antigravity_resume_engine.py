"""
Unit and Integration Tests for Antigravity Resume Engine.
Verifies Knowledge Graph, Pydantic schemas, LaTeX compilation,
API endpoints, and CLI pipeline.
"""

import os
import json
import pytest
from fastapi.testclient import TestClient

from src.schemas import (
    DeconstructedJD,
    TailoredResumeAST,
    RoleEntryAST,
    ProjectEntryAST,
    SkillCategoryAST,
)
from src.knowledge_graph import load_candidate_knowledge_graph, select_evidence_bundle
from src.validator import validate_bullet_constraints, strip_latex_formatting
from src.latex_compiler import compile_and_verify_single_page
from src.pipeline import generate_tailored_resume
from src.api import app

client = TestClient(app)


def test_knowledge_graph_structure():
    kg = load_candidate_knowledge_graph()
    cand = kg["candidate"]
    assert cand["identity"]["name"] == "Chandra Rup Daka"
    assert "Houston, TX" in cand["identity"]["location"]
    assert len(cand["experience"]) >= 4
    assert len(cand["projects"]) >= 5
    assert len(cand["certifications"]) >= 4
    assert "agentic_swe" in cand["archetype_routing"]


def test_evidence_bundle_selection():
    kg = load_candidate_knowledge_graph()
    jd = DeconstructedJD(
        company_name="Anthropic",
        role_title="Agentic AI Engineer",
        selected_track="agentic_swe",
        top_hard_skills=["LangGraph", "FastAPI", "TypeScript", "MCP"],
    )
    bundle = select_evidence_bundle(jd, kg)
    assert bundle["section_order"][0] == "Summary"
    assert len(bundle["selected_experience"]) >= 3
    assert len(bundle["selected_projects"]) >= 2


def test_humana_company_override():
    kg = load_candidate_knowledge_graph()
    jd = DeconstructedJD(
        company_name="Humana Healthcare",
        role_title="AI Research Fellow",
        selected_track="agentic_swe",
        top_hard_skills=["Python", "Gemini", "Voice"],
    )
    bundle = select_evidence_bundle(jd, kg)
    # Humana Institute should be elevated to lead position
    assert bundle["selected_experience"][0]["id"] == "exp_humana"
    assert "Humana Institute" in bundle["summary_starter"]


def test_strip_latex_formatting():
    raw = r"\textbf{\ulhref{https://github.com}{SmartApplyAI}} $|$ \textbf{Python} \hfill \textit{2026}"
    plain = strip_latex_formatting(raw)
    assert "SmartApplyAI | Python 2026" in plain
    assert "\\" not in plain


def test_bullet_constraints_validator():
    ast = TailoredResumeAST(
        summary_latex="Test summary with sufficient characters to satisfy requirements for the candidate profile.",
        section_order=["Summary", "Experience", "Selected Projects", "Education", "Skills"],
        experience_entries=[
            RoleEntryAST(
                role_title="Generative AI Engineer",
                date_range="Aug 2023 -- Jul 2025",
                organization_line="Accenture",
                location="Chennai, India",
                bullets=[
                    # 195 characters
                    r"Built production \textbf{REST APIs} and \textbf{agent-to-agent (A2A)} workflows in \textbf{Python} using \textbf{FastAPI} and \textbf{LangGraph}, validating schemas and handling retry fallbacks across \textbf{30,000+ files}."
                ],
            )
        ],
        project_entries=[
            ProjectEntryAST(
                title="SmartApplyAI",
                url="https://github.com/chandrarup/SmartApplyAI",
                tech_stack_short="Python, Flask",
                year="2026",
                bullets=[
                    # 204 characters
                    r"Built local-first Chrome extension (\textbf{TypeScript}) with \textbf{Python (Flask, SQLite)} backend orchestrating \textbf{AWS Bedrock} and \textbf{Gemini} to parse ATS DOM trees and compile \textbf{LaTeX} resumes."
                ],
            )
        ],
        skill_categories=[
            SkillCategoryAST(category_name="Languages", skills_csv="Python, TypeScript, SQL")
        ],
    )
    report = validate_bullet_constraints(ast)
    assert report["valid"] is True
    assert report["avg_bullet_length"] >= 165
    assert len(report["banned_verbs_found"]) == 0


def test_latex_compiler_single_page():
    ast = TailoredResumeAST(
        target_filename_stem="test_unit_resume",
        summary_latex=r"AI Systems Engineer with 2+ years of production experience building enterprise agentic workflows, Model Context Protocol (MCP) tool integrations, and resilient REST microservices in \textbf{Python (FastAPI)} and \textbf{TypeScript (React, Next.js)}. Architected multi-agent orchestration for Fortune-scale platforms across \textbf{30,000+ files} and \textbf{15+ languages}. M.S. in Engineering Data Science \& AI candidate at University of Houston (GPA: 3.9/4.0).",
        section_order=["Summary", "Experience", "Selected Projects", "Education", "Skills"],
        experience_entries=[
            RoleEntryAST(
                role_title="Generative AI Engineer",
                date_range="Aug 2023 -- Jul 2025",
                organization_line="Accenture — GenWizard Platform",
                location="Chennai, India",
                bullets=[
                    r"Built production \textbf{REST APIs} and \textbf{agent-to-agent (A2A)} workflows in \textbf{Python} using \textbf{FastAPI} and \textbf{LangGraph}, validating schemas and handling retry fallbacks across \textbf{30,000+ files}.",
                    r"Architected \textbf{Model Context Protocol (MCP)} connectors and multi-agent coordination pipelines (LangChain, AutoGen) for automated code translation and test generation, achieving \textbf{89\% diagnostic coverage}.",
                    r"Implemented enterprise \textbf{RAG} with \textbf{PostgreSQL (pgvector)}, \textbf{Pinecone}, and \textbf{Neo4j} knowledge graphs using \textbf{BERTopic} and sentence transformers, cutting review cycles from weeks to hours.",
                    r"Mentored \textbf{30+ engineers} on \textbf{Python} agentic frameworks and \textbf{Docker} CI/CD standards, earning the \textbf{Wall of Fame (Q4 FY24)} and \textbf{Client Recognition Award} for enterprise delivery.",
                ],
            ),
            RoleEntryAST(
                role_title="AI & Data Science Researcher / Developer",
                date_range="Apr 2026 -- Sep 2026",
                organization_line="UH C.T. Bauer College of Business",
                location="Houston, TX",
                bullets=[
                    r"Architected \textbf{The Cost of Winning}, a real-time AI simulation in \textbf{TypeScript (React 19, Next.js)} and \textbf{PostgreSQL (Supabase)}, serving \textbf{60+ MBA students} across 13 branching decision scenes.",
                    r"Integrated multi-provider LLM routing (Claude, OpenAI, Gemini) using \textbf{Model Context Protocol (MCP)} tools and built an \textbf{LLM-as-judge} evaluation rubric for automated scoring.",
                    r"Engineered predictive classification pipelines in \textbf{Python (Scikit-learn, SQL)} on a \textbf{289,000-row} longitudinal employee panel, utilizing \textbf{SHAP} explainability to achieve \textbf{ROC-AUC 0.714}.",
                ],
            ),
            RoleEntryAST(
                role_title="Healthcare AI Fellow (SHERP 2026)",
                date_range="Jun 2026 -- Aug 2026",
                organization_line="Humana Institute",
                location="Houston, TX",
                bullets=[
                    r"Engineered \textbf{Companion Care}, a voice-first agentic web application using \textbf{TypeScript (TanStack Start)}, \textbf{PostgreSQL (Supabase)}, and \textbf{Google Gemini} for dementia patients.",
                    r"Integrated low-latency \textbf{ElevenLabs} voice synthesis and \textbf{Backboard} persistent memory, enforcing strict HIPAA-aligned OAuth and confirmation boundaries.",
                ],
            ),
        ],
        project_entries=[
            ProjectEntryAST(
                title="SmartApplyAI — Multi-LLM Agentic Copilot",
                url="https://github.com/chandrarup/SmartApplyAI",
                tech_stack_short="Python, TypeScript, Flask, SQLite, Gemini",
                year="2026",
                bullets=[
                    r"Built local-first Chrome extension (\textbf{TypeScript}) with \textbf{Python (Flask, SQLite)} backend orchestrating \textbf{AWS Bedrock} and \textbf{Gemini} to parse ATS DOM trees and compile \textbf{LaTeX} resumes."
                ],
            ),
            ProjectEntryAST(
                title="Hush — Cloud-Native Voice AI Assistant",
                url="https://github.com/chandrarup/Hush",
                tech_stack_short="Python, GKE, Docker, mlx-whisper, Gemini",
                year="2026",
                bullets=[
                    r"Engineered low-latency voice assistant in \textbf{Python} pairing Apple Silicon \textbf{mlx-whisper} with containerized \textbf{Google Kubernetes Engine (GKE)} and \textbf{Docker} microservices with cloud fallback."
                ],
            ),
        ],
        skill_categories=[
            SkillCategoryAST(category_name="Languages", skills_csv="Python (Expert), TypeScript, JavaScript, SQL, Java, R"),
            SkillCategoryAST(category_name="Agentic AI & ML", skills_csv="FastAPI, LangGraph, LangChain, MCP, PyTorch, RAG"),
            SkillCategoryAST(category_name="Cloud & Databases", skills_csv="Docker, GKE, PostgreSQL (pgvector), Supabase, Neo4j"),
        ],
    )
    res = compile_and_verify_single_page(ast, output_dir="output", stem_override="test_unit_resume")
    assert res.status == "success"
    assert res.pages == 1
    assert res.vertical_fill_pct >= 70.0
    assert os.path.exists(res.pdf_path)
    assert os.path.exists(res.tex_path)


def test_fastapi_endpoints():
    # Health check
    r_health = client.get("/api/health")
    assert r_health.status_code == 200
    assert r_health.json()["binaries"]["pdflatex"] is True

    # Candidate knowledge
    r_kg = client.get("/api/candidate-knowledge")
    assert r_kg.status_code == 200
    assert r_kg.json()["candidate"]["identity"]["name"] == "Chandra Rup Daka"

    # Generate resume
    req_payload = {
        "raw_jd": "Looking for an Agentic AI Engineer with Python, FastAPI, and LangGraph experience.",
        "company_name": "TestAI",
        "role_title": "AI Engineer",
        "track_override": "agentic_swe",
        "output_stem": "test_api_resume",
    }
    r_gen = client.post("/api/generate-resume", json=req_payload)
    assert r_gen.status_code == 200
    data = r_gen.json()
    assert data["success"] is True
    assert data["compilation_result"]["pages"] == 1
    assert os.path.exists(data["pdf_path"])

    # Download PDF
    r_dl = client.get("/api/download/test_api_resume.pdf")
    assert r_dl.status_code == 200
    assert r_dl.headers["content-type"] == "application/pdf"
