# LocalHire Agent 2.0

An AI-powered job application auto-filler and resume tailoring tool — similar to JobRight.ai. Consists of a FastAPI backend and a Chrome Extension.

## What It Does

- **Auto-fills** job applications on Workday, Greenhouse, Lever, BambooHR, iCIMS, SmartRecruiters, LinkedIn Easy Apply, and Taleo
- **Answers custom questions** using the candidate's real profile via AI
- **Generates cover letters** tailored to each specific company and role
- **Analyzes job descriptions** to compute a match score and select best-fit skills/projects
- **Generates tailored PDF resumes** via LaTeX, matching the JD

## Architecture

- **Backend** (`backend/`): FastAPI server running on port 5000 (dashboard + API)
- **Extension** (`extension/`): Chrome Extension Manifest V3 with 4 tabs: Match, Auto-Fill, Cover Letter, Chat

## Running the Project

```
cd backend && uvicorn main:app --host 0.0.0.0 --port 5000 --reload
```

## Chrome Extension Installation

1. Open Chrome → `chrome://extensions/` → Enable Developer Mode
2. Click "Load unpacked" → select the `extension/` folder
3. Set API URL in the Auto-Fill tab to point to this backend
4. Navigate to any job posting and use the 4-tab popup

## Supported Platforms (Phase 1)

Workday · Greenhouse · Lever · BambooHR · iCIMS · SmartRecruiters · LinkedIn Easy Apply · Taleo

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/profile` | Candidate profile summary |
| POST | `/analyze` | JD → match score, skills, tailored summary, selected projects |
| POST | `/autofill` | Field labels + JD → AI answers dict for every field |
| POST | `/answer-question` | Single question → profile-grounded AI answer |
| POST | `/cover-letter` | Company + role + JD → full personalized cover letter |
| POST | `/suggest-questions` | Generate FAQ questions for a job posting |
| POST | `/chat` | Chat with AI about a job page |
| POST | `/generate-pdf` | Render tailored LaTeX resume → PDF download |

## Key Files

- `backend/main.py` — FastAPI app with all API endpoints
- `backend/master_data.json` — Candidate profile (includes `autofill` and `common_answers` sections)
- `backend/resume_template.tex` — Jinja2 LaTeX template
- `backend/dashboard.html` — Web dashboard at `/`
- `extension/manifest.json` — Chrome Extension manifest v3 with all platform permissions
- `extension/background.js` — Service worker for API URL storage and state
- `extension/content.js` — Platform detection, field scanning, and auto-fill logic
- `extension/popup.html` — 4-tab popup UI (Match | Auto-Fill | Cover Letter | Chat)
- `extension/popup.js` — Popup logic

## External Dependencies

- **Ollama**: Must run locally at `localhost:12434` with model `ai/qwen3-coder`
- **Docker + texlive**: Required for PDF resume generation

## Deployment

Configured for autoscale via Gunicorn + Uvicorn workers on port 5000.
