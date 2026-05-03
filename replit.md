# LocalHire Agent

An AI-powered resume tailoring tool consisting of a FastAPI backend and a Chrome Extension.

## Architecture

- **Backend** (`backend/`): FastAPI server running on port 5000
- **Extension** (`extension/`): Chrome Extension (Manifest V3) that scrapes job postings and calls the backend

## How It Works

1. The Chrome Extension scrapes job description text from the active browser tab
2. It sends the text to the FastAPI backend
3. The backend uses Ollama (LLM) to analyze the job description against the candidate's profile in `master_data.json`
4. Results include a match score, matched/missing skills, and a tailored resume summary
5. PDF generation uses LaTeX via Docker (`texlive/texlive:latest`)

## Running the Project

The workflow starts the FastAPI server:
```
cd backend && uvicorn main:app --host 0.0.0.0 --port 5000 --reload
```

The dashboard is accessible at the root URL and shows backend status, API endpoints, and candidate profile info.

## External Dependencies

- **Ollama**: Must be running locally at `localhost:12434` with model `ai/qwen3-coder`
- **Docker**: Required for PDF generation using `texlive/texlive:latest`

## Key Files

- `backend/main.py` — FastAPI app with all API endpoints
- `backend/master_data.json` — Candidate profile data (skills, projects, experience)
- `backend/resume_template.tex` — Jinja2 LaTeX template for resume generation
- `backend/dashboard.html` — Web dashboard served at `/`
- `extension/manifest.json` — Chrome Extension manifest
- `extension/popup.html` / `popup.js` — Extension UI and logic
- `extension/content.js` — Content script for scraping page text

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/profile` | Candidate profile summary |
| POST | `/analyze` | Analyze job description, return match score |
| POST | `/suggest-questions` | Generate FAQ questions for a job posting |
| POST | `/chat` | Chat with AI about a job page |
| POST | `/generate-pdf` | Generate tailored resume PDF via LaTeX |

## Deployment

Configured for autoscale deployment using Gunicorn + Uvicorn workers:
```
gunicorn --bind=0.0.0.0:5000 --reuse-port --chdir=backend main:app -k uvicorn.workers.UvicornWorker
```
