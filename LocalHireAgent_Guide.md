# LocalHire Agent 2.0 — Complete Setup, Testing & Interview Guide

> **Audience:** Beginners welcome. Every command is explained. No assumed knowledge beyond "I know how to open a terminal."

---

## Table of Contents

1. [What Is This Project?](#1-what-is-this-project)
2. [How It Works — Architecture Overview](#2-how-it-works--architecture-overview)
3. [Prerequisites — What You Need Installed](#3-prerequisites--what-you-need-installed)
4. [Local Setup — Step by Step](#4-local-setup--step-by-step)
5. [Running the Backend](#5-running-the-backend)
6. [Installing the Chrome Extension](#6-installing-the-chrome-extension)
7. [Testing Every Feature — Step by Step](#7-testing-every-feature--step-by-step)
8. [API Reference — Every Endpoint Explained](#8-api-reference--every-endpoint-explained)
9. [Data Model — How Your Data Is Stored](#9-data-model--how-your-data-is-stored)
10. [Deployment Guide](#10-deployment-guide)
11. [Interview Prep — Technical Deep Dive](#11-interview-prep--technical-deep-dive)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. What Is This Project?

**LocalHire Agent 2.0** is a two-part system:

| Part | What it is | What it does |
|---|---|---|
| **Backend** | FastAPI server (Python) | Stores your resume data, calls AI models, serves the dashboard |
| **Chrome Extension** | Browser extension (JavaScript) | Detects job application forms and auto-fills them using your data |

**The core loop:**
1. You save your resume data once in the dashboard
2. You visit a job posting (Workday, Greenhouse, LinkedIn, etc.)
3. The extension detects the application form
4. You click "AutoFill" in the popup — the backend uses AI to map your data to each field
5. The form fills instantly

**Key features:**
- Auto-fill on 8 ATS platforms (Workday, Greenhouse, Lever, Bamboo HR, iCIMS, LinkedIn, SmartRecruiters, Taleo)
- AI-powered answers to open-ended questions
- One-click cover letter generation
- Tailored PDF resume export
- Multi-profile system (up to 5 profiles, each with optional PIN)
- JD Analyzer page — paste any job description, get match score + gaps + tailored summary
- Runs 100% locally — your data never leaves your machine

---

## 2. How It Works — Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                YOUR COMPUTER                         │
│                                                      │
│  ┌──────────────────┐      ┌───────────────────────┐ │
│  │  Chrome Browser  │      │   FastAPI Backend     │ │
│  │                  │      │   localhost:5000       │ │
│  │  ┌────────────┐  │      │                       │ │
│  │  │  Extension │◄─┼──────┤  /autofill            │ │
│  │  │  (popup.js)│  │ HTTP │  /analyze             │ │
│  │  └────────────┘  │      │  /answer-question     │ │
│  │                  │      │  /cover-letter        │ │
│  │  ┌────────────┐  │      │  /generate-pdf        │ │
│  │  │ Dashboard  │◄─┼──────┤  /profile (CRUD)      │ │
│  │  │(dashboard  │  │      │  /applications (CRUD) │ │
│  │  │ .html)     │  │      │                       │ │
│  │  └────────────┘  │      │  profiles/            │ │
│  │                  │      │    default/           │ │
│  └──────────────────┘      │      master_data.json │ │
│                            │      applications.json│ │
│                            └───────────────────────┘ │
│                                      │                │
│                            ┌─────────┴──────────┐    │
│                            │   AI Provider      │    │
│                            │  (your choice)     │    │
│                            │  Ollama (local) OR │    │
│                            │  Claude API (cloud)│    │
│                            └────────────────────┘    │
└─────────────────────────────────────────────────────┘
```

**Request flow for AutoFill:**
1. Extension detects the ATS platform (e.g., Greenhouse)
2. Extension scrapes the field labels from the form DOM
3. Extension sends `POST /autofill` with `{fields: [...], llm: "ollama", platform: "greenhouse"}`
4. Backend loads your profile data, asks the AI: "here are the fields, here is the resume, map them"
5. AI returns `{"field_id": "value"}` mappings
6. Extension injects the values into the form fields

---

## 3. Prerequisites — What You Need Installed

### 3.1 Python 3.10 or higher

**Check if you have it:**
```bash
python3 --version
# Should print: Python 3.10.x or higher
```

**Install if missing:**
- macOS: `brew install python3` (needs Homebrew first: https://brew.sh)
- Windows: Download from https://python.org/downloads — check "Add to PATH" during install
- Linux: `sudo apt install python3 python3-pip`

### 3.2 pip (Python package manager)

Usually comes with Python. Check:
```bash
pip3 --version
```

### 3.3 Google Chrome

Download from: https://google.com/chrome

### 3.4 (Optional but recommended) Ollama — Local AI

Ollama lets the AI run entirely on your machine. No API key needed. No data sent to the cloud.

**Install:**
- macOS/Linux: `curl -fsSL https://ollama.com/install.sh | sh`
- Windows: Download installer from https://ollama.com

**Pull a model (do this once):**
```bash
ollama pull llama3.2
# This downloads ~2GB model — do it on WiFi
```

**Start Ollama:**
```bash
ollama serve
# Runs at http://localhost:11434 — keep this terminal open
```

> **Don't have Ollama?** You can use Claude instead. You'll need an Anthropic API key from https://console.anthropic.com

### 3.5 Git (to clone the project)

```bash
git --version
```

Install from: https://git-scm.com/downloads

---

## 4. Local Setup — Step by Step

### Step 1 — Clone the repository

```bash
git clone <your-repo-url> LocalHireAgent
cd LocalHireAgent
```

> If you downloaded a ZIP instead: unzip it and `cd` into the folder.

### Step 2 — Navigate to the backend folder

```bash
cd backend
```

Your folder structure looks like this:
```
LocalHireAgent/
├── backend/
│   ├── main.py              ← The entire backend API
│   ├── dashboard.html       ← The dashboard web app
│   ├── login.html           ← Profile selector page
│   ├── requirements.txt     ← Python packages needed
│   ├── master_data.json     ← Your legacy resume data
│   └── profiles/
│       └── default/
│           ├── master_data.json    ← Your profile data
│           └── applications.json  ← Your job applications
└── extension/
    ├── manifest.json        ← Extension config
    ├── popup.html           ← Extension UI
    ├── popup.js             ← Extension logic
    ├── content.js           ← Injected into job pages
    └── background.js        ← Service worker
```

### Step 3 — Install Python dependencies

```bash
pip3 install -r requirements.txt
```

This installs:
- `fastapi` — the web framework (like Flask but faster and with auto-docs)
- `uvicorn` — the web server that runs FastAPI
- `requests` — for making HTTP calls (to Ollama, Claude, etc.)
- `openai` — for calling the OpenAI-compatible Ollama API
- `jinja2` — for templating (used in PDF generation)

> **What is a dependency?** It's a library someone else wrote that your code uses. `requirements.txt` is a shopping list — `pip` goes and downloads everything on it.

### Step 4 — (Optional) Set your Claude API key

If you want to use Claude instead of (or in addition to) Ollama:

```bash
export ANTHROPIC_API_KEY="sk-ant-xxxxxxxxxxxxxxxx"
```

On Windows:
```cmd
set ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxx
```

> This sets an environment variable — a named value the program can read without you hardcoding it in the source code. It disappears when you close the terminal. To make it permanent, add it to `~/.bashrc` (Linux/macOS) or Windows System Environment Variables.

---

## 5. Running the Backend

### Start the server

From inside the `backend/` directory:

```bash
uvicorn main:app --host 0.0.0.0 --port 5000 --reload
```

**What each part means:**
- `uvicorn` — the web server program
- `main:app` — load the `app` object from `main.py`
- `--host 0.0.0.0` — listen on all network interfaces (not just localhost)
- `--port 5000` — listen on port 5000
- `--reload` — automatically restart when you save changes to the code

**Expected output:**
```
INFO:     Will watch for changes in these directories: ['/path/to/backend']
INFO:     Uvicorn running on http://0.0.0.0:5000 (Press CTRL+C to quit)
INFO:     Started reloader process [1234]
INFO:     Application startup complete.
```

### Verify it's running

Open a new terminal and run:
```bash
curl http://localhost:5000/health
```

Expected response:
```json
{"message": "Server is Online"}
```

Or just open your browser and go to: **http://localhost:5000**

You should see the login/profile selector page.

---

## 6. Installing the Chrome Extension

### Step 1 — Open Chrome Extensions page

In Chrome, go to: `chrome://extensions`

Or: Menu (⋮) → More tools → Extensions

### Step 2 — Enable Developer Mode

Top-right corner: toggle **Developer mode** ON.

> **Why Developer Mode?** Chrome only allows extensions from the Chrome Web Store by default. Developer Mode lets you load unpacked extensions from your local folder — essential for development and testing.

### Step 3 — Load the extension

Click **"Load unpacked"** → navigate to and select the `extension/` folder (the one containing `manifest.json`).

### Step 4 — Verify it loaded

You should see "LocalHire Agent" in your extensions list. Pin it to your toolbar by clicking the puzzle piece icon and pinning it.

### Step 5 — Configure the extension

Click the LocalHire Agent icon in your toolbar.

In the Settings tab, set:
- **Backend URL:** `http://localhost:5000`
- **Profile ID:** `default` (or leave blank)

---

## 7. Testing Every Feature — Step by Step

### Test 1 — Health Check

**URL:** http://localhost:5000/health

**Expected:**
```json
{"message": "Server is Online"}
```

---

### Test 2 — Login Page & Profile System

**URL:** http://localhost:5000 (or /login)

**What to test:**

1. **View profiles** — You should see a card for "Chandra Rup Daka" (or your name)
2. **Create a new profile:**
   - Click the "+" (New Profile) card
   - Enter a name (e.g., "Tech Resume")
   - Pick a color
   - Optionally enter a 4-digit PIN
   - Click Create
3. **PIN protection:**
   - Create a profile with PIN `1234`
   - Click that profile card
   - A modal appears asking for PIN
   - Type wrong PIN → should show error
   - Type `1234` → should enter dashboard
4. **Delete a profile:**
   - Long-press or right-click the card for the delete option
   - Confirm deletion
   - Should fail if it's the last profile remaining
5. **Max profiles:**
   - Try creating a 6th profile when 5 exist
   - Should be blocked with an error message

---

### Test 3 — Dashboard Navigation

**URL:** http://localhost:5000/dashboard (click your profile to get here)

The sidebar has these sections:

**Profile (your resume data):**
- Contact Info
- Work Experience
- Education
- Skills
- Common Answers

**Job Search:**
- Analyze JD ← new
- Applications

**System:**
- Settings

Test each nav item to confirm the page loads without errors.

---

### Test 4 — Updating Your Profile Data

1. Click **Contact Info** in the sidebar
2. Update your email or phone number
3. Click **Save**
4. Refresh the page (F5)
5. Your changes should still be there

Repeat for each section:

| Section | What to test saving |
|---|---|
| Contact Info | Name, email, phone, LinkedIn URL |
| Work Experience | Add a new job, edit duration, delete an entry |
| Education | Add a degree |
| Skills | Add "Docker" to tools |
| Common Answers | Write an answer to "Tell me about yourself" |

---

### Test 5 — Profile Data Isolation

1. Create a second profile called "Finance Resume"
2. Switch to it (go to /login, click it)
3. Update Contact Info to a different name
4. Switch back to your original profile
5. The original name should be unchanged

**This verifies that profiles are completely isolated — each one has its own separate data file.**

---

### Test 6 — Applications Tracker

1. Click **Applications** in the sidebar
2. Click **+ New Application**
3. Fill in: Company = "Google", Role = "SWE", Platform = "Other", Status = "Applied"
4. Click Save
5. The application appears in the list
6. Click the status badge → change it to "Interview"
7. Click the trash icon → delete it
8. Confirm deletion

---

### Test 7 — Analyze JD (Job Description Analyzer)

1. Click **Analyze JD** in the sidebar
2. Paste this sample JD:
   ```
   We are looking for a Machine Learning Engineer with 3+ years of experience.
   Requirements: Python, TensorFlow or PyTorch, MLOps, Docker, Kubernetes, SQL.
   Nice to have: LangChain, RAG pipelines, experience with LLMs.
   Responsibilities: Train and deploy ML models, design pipelines, collaborate with data scientists.
   ```
3. Enter Company: "OpenAI", Role: "ML Engineer"
4. Select your LLM (Ollama if running locally, Claude if you have an API key)
5. Click **Analyze Match**
6. Verify you see:
   - **Score ring** with a color-coded percentage
   - **Matched Skills** (green tags for skills you have)
   - **Gaps** (amber tags for skills you're missing)
   - **Tailored Summary** text
   - **3 interviewer questions**
7. Click **Copy** next to Tailored Summary — verify it copies to clipboard
8. Click **+ Log This Application** — should open the application modal pre-filled with "OpenAI" and "ML Engineer"

---

### Test 8 — AI AutoFill via Extension (ATS Test Pages)

The backend has built-in test pages for each platform. Test them at:

| Platform | Test URL |
|---|---|
| Greenhouse | http://localhost:5000/test/greenhouse |
| Workday | http://localhost:5000/test/workday |
| Lever | http://localhost:5000/test/lever |
| LinkedIn | http://localhost:5000/test/linkedin |
| iCIMS | http://localhost:5000/test/icims |
| BambooHR | http://localhost:5000/test/bamboohr |
| SmartRecruiters | http://localhost:5000/test/smartrecruiters |
| Taleo | http://localhost:5000/test/taleo |

**How to test:**
1. Open one of the URLs above in Chrome
2. Click the LocalHire Agent extension icon
3. In the popup, click **AutoFill**
4. Watch the form fields fill in automatically
5. Review the values — they should match your profile data

---

### Test 9 — AI Answer Generation

1. Go to a test ATS page (e.g., http://localhost:5000/test/greenhouse)
2. Look for an open-ended text area (e.g., "Why do you want to work here?")
3. In the extension popup, use the **Answer** tab
4. Type or paste the question
5. Click **Generate Answer**
6. The AI writes a personalized answer based on your profile data

---

### Test 10 — Cover Letter Generation

1. In the extension popup, go to **Cover Letter** tab
2. Enter:
   - Company: "Stripe"
   - Role: "Backend Engineer"
   - Paste the job description
3. Click **Generate**
4. A full cover letter appears, written in your voice using your real experience

---

### Test 11 — PDF Resume Generation

1. In the dashboard, go to **Settings** or use the **Generate PDF** button
2. Select a template
3. Click Generate
4. A PDF download should trigger

---

### Test 12 — LLM Status Check

```bash
curl http://localhost:5000/llm-status
```

Expected (with Ollama running):
```json
{
  "ollama": true,
  "claude": false,
  "claude_key_set": false
}
```

Expected (with Claude key set):
```json
{
  "ollama": false,
  "claude": true,
  "claude_key_set": true
}
```

---

## 8. API Reference — Every Endpoint Explained

### Pages (return HTML)

| Method | Path | Description |
|---|---|---|
| GET | `/` | Login / profile selector page |
| GET | `/login` | Same as `/` |
| GET | `/dashboard` | Main dashboard SPA |

### System

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Server health check |
| GET | `/llm-status` | Which AI providers are available |
| POST | `/set-claude-key` | Hot-load a Claude API key without restarting |

### Profiles

| Method | Path | Body | Description |
|---|---|---|---|
| GET | `/profiles` | — | List all profiles (id, name, color, has_pin) |
| POST | `/profiles` | `{name, color, pin?}` | Create a new profile (max 5) |
| DELETE | `/profiles/{id}` | — | Delete a profile (blocked if last one) |
| PUT | `/profiles/{id}/name` | `{name}` | Rename a profile |
| POST | `/profiles/{id}/verify-pin` | `{pin}` | Verify PIN — 200 if correct, 401 if wrong |

### Profile Data (all require `X-Profile-ID` header)

| Method | Path | Body | Description |
|---|---|---|---|
| GET | `/profile` | — | Get all resume data for active profile |
| PUT | `/profile/contact` | `{contact_info, summary}` | Update contact details |
| PUT | `/profile/autofill` | `{autofill}` | Update autofill field mappings |
| PUT | `/profile/experience` | `{experience: [...]}` | Replace work experience list |
| PUT | `/profile/education` | `{education: [...]}` | Replace education list |
| PUT | `/profile/skills` | `{skills}` | Update skills (merged, not replaced) |
| PUT | `/profile/answers` | `{common_answers}` | Update common Q&A answers |

### Applications (all require `X-Profile-ID` header)

| Method | Path | Body | Description |
|---|---|---|---|
| GET | `/applications` | — | List all applications for active profile |
| POST | `/applications` | `{company, role, platform, status, ...}` | Add a new application |
| PATCH | `/applications/{id}` | `{status?, notes?, ...}` | Update specific fields |
| DELETE | `/applications/{id}` | — | Delete an application |

### AI / LLM Endpoints (all require `X-Profile-ID` header)

| Method | Path | Body | Description |
|---|---|---|---|
| POST | `/analyze` | `{jd_text, llm}` | Analyze JD vs profile → score, skills, gaps, summary |
| POST | `/suggest-questions` | `{jd_text, llm}` | Generate 3 interviewer questions from JD |
| POST | `/autofill` | `{fields, platform, llm}` | Map form fields to your profile data |
| POST | `/answer-question` | `{question, context, llm}` | Generate answer to a specific question |
| POST | `/cover-letter` | `{company, role, jd_text, llm}` | Generate a full cover letter |
| POST | `/generate-pdf` | `{...profile data...}` | Generate a tailored PDF resume |

### Test Pages

| Method | Path |
|---|---|
| GET | `/test/greenhouse` |
| GET | `/test/workday` |
| GET | `/test/lever` |
| GET | `/test/linkedin` |
| GET | `/test/icims` |
| GET | `/test/bamboohr` |
| GET | `/test/smartrecruiters` |
| GET | `/test/taleo` |

---

## 9. Data Model — How Your Data Is Stored

### File structure on disk

```
backend/
├── master_data.json          ← Legacy file (kept as backup)
├── applications.json         ← Legacy file (kept as backup)
└── profiles/
    ├── meta.json             ← List of all profile metadata
    └── default/              ← One folder per profile
        ├── master_data.json  ← Resume data for this profile
        └── applications.json ← Job applications for this profile
```

### profiles/meta.json

```json
[
  {
    "id": "default",
    "name": "Chandra Rup Daka",
    "color": "#F97316",
    "created_at": "2025-05-01",
    "pin_hash": ""
  }
]
```

> **PINs are never stored as plain text.** They are hashed using SHA-256 before saving. Even if someone reads the file, they cannot recover the PIN.

### profiles/default/master_data.json

```json
{
  "contact_info": {
    "name": "Chandra Rup Daka",
    "email": "chandrarupdaka@gmail.com",
    "phone": "+1 253-632-3181",
    "linkedin": "...",
    "github": "...",
    "location": "Houston, TX"
  },
  "summary": "AI/ML Engineer with ...",
  "experience": [
    {
      "role": "Advanced App Engineering Analyst",
      "company": "Accenture",
      "duration": "Aug 2023 - Aug 2025",
      "location": "India",
      "details": ["Built LLM solutions...", "..."]
    }
  ],
  "education": [
    {
      "degree": "Master of Science",
      "university": "University of Houston",
      "graduation_year": "2027"
    }
  ],
  "skills": {
    "languages": ["Python", "JavaScript", "..."],
    "frameworks": ["FastAPI", "LangChain", "..."],
    "tools": ["Docker", "Git", "..."],
    "databases": ["PostgreSQL", "..."],
    "domains": ["Machine Learning", "..."]
  },
  "autofill": {
    "first_name": "Chandra Rup",
    "last_name": "Daka",
    "city": "Houston",
    "state": "TX",
    "visa_status": "F-1 OPT",
    "salary_expectation": "130000",
    "...": "..."
  },
  "common_answers": {
    "why_this_company": "...",
    "strengths": "...",
    "weaknesses": "..."
  }
}
```

---

## 10. Deployment Guide

### Option A — Keep it local (recommended for privacy)

This is the default. Run `uvicorn` on your machine, load the extension in Chrome, and use it. Your data never leaves your computer.

**To run it automatically on startup (macOS/Linux):**

Create a startup script `~/start_localhire.sh`:
```bash
#!/bin/bash
cd /path/to/LocalHireAgent/backend
ollama serve &
sleep 3
uvicorn main:app --host 0.0.0.0 --port 5000 --reload
```

Make it executable:
```bash
chmod +x ~/start_localhire.sh
```

### Option B — Deploy to a server (for access from multiple machines)

1. Spin up a VPS (DigitalOcean, AWS EC2, etc.)
2. Install Python and dependencies
3. Use a process manager like `systemd` or `supervisor` to keep it running
4. Use `nginx` as a reverse proxy for HTTPS
5. Set `ANTHROPIC_API_KEY` as a system environment variable
6. Update the Chrome extension `manifest.json` host_permissions to include your server URL
7. Change the extension's backend URL setting to `https://your-server.com`

> **Security note:** If you deploy publicly, add authentication to the API. Currently it uses the `X-Profile-ID` header with PIN verification, which is sufficient for local use but not hardened for public internet exposure.

### Option C — Deploy on Replit (current setup)

The project runs on Replit with the workflow:
```
cd backend && uvicorn main:app --host 0.0.0.0 --port 5000 --reload
```

The dashboard is accessible at your Replit project URL. The Chrome extension needs the Replit URL as its backend URL (update the Settings tab in the extension popup).

---

## 11. Interview Prep — Technical Deep Dive

### Question 1: "Tell me about this project."

**Answer framework:**

> "LocalHire Agent is a full-stack AI-powered job application assistant I built from scratch. It has two main components: a FastAPI backend that stores my resume data and calls AI models, and a Chrome extension that auto-detects job application forms and fills them using AI-generated mappings from my resume.
>
> The core technical challenge was the autofill logic — different ATS platforms (Workday, Greenhouse, LinkedIn, etc.) each have unique DOM structures, field naming conventions, and even React-based dynamic forms. I solved this by having the extension scrape the field labels, send them to the backend, and let the LLM intelligently map them to my profile data rather than using brittle hardcoded selectors.
>
> I then built a multi-profile system so I can maintain separate resumes for different job types, each with optional PIN protection and full data isolation — each profile has its own files on disk."

---

### Question 2: "Why FastAPI over Flask or Django?"

**Answer:**

> "I chose FastAPI for three reasons. First, it has native async support, which matters when making LLM API calls that might take 5-10 seconds — I don't want to block the server during that time. Second, it auto-generates OpenAPI documentation at `/docs`, which helped me test endpoints during development without writing a separate test client. Third, its Pydantic model integration gives me free request validation — if someone sends a POST without a required field, FastAPI returns a clear 422 error automatically."

**Key points to know:**
- FastAPI is built on Starlette (async web framework) and Pydantic (data validation)
- `async def` endpoints can handle concurrent requests without blocking
- `uvicorn` is the ASGI server (Asynchronous Server Gateway Interface) — the equivalent of gunicorn for WSGI

---

### Question 3: "Explain the multi-profile data isolation architecture."

**Answer:**

> "Each profile gets its own subdirectory under `profiles/`. A central `meta.json` file stores the profile list with metadata (id, name, color, PIN hash). Every read and write operation extracts the profile ID from an `X-Profile-ID` HTTP header and routes to that profile's files. This is similar to multi-tenancy in SaaS — just at the filesystem level instead of a database.
>
> I deliberately chose flat JSON files over SQLite or PostgreSQL because the data is simple, the volume is tiny (one user's resume), and it avoids a database dependency. The tradeoff is no transactions or relational queries, which is fine for this use case."

**Architecture terms to know:**
- **Multi-tenancy:** One system serving multiple isolated users/accounts
- **Data isolation:** Guaranteeing that Profile A cannot see or modify Profile B's data
- **Idempotent migration:** A setup function that can run multiple times safely without duplicating or corrupting data

---

### Question 4: "How does the AI autofill work under the hood?"

**Answer:**

> "The extension first identifies which ATS platform the user is on by checking the URL pattern. It then walks the DOM to find form fields — looking for `input`, `select`, and `textarea` elements, capturing their labels, placeholder text, and `name`/`id` attributes.
>
> It sends this list of field descriptors along with the platform name to `POST /autofill` on the backend. The backend loads the user's full profile data and constructs a prompt like: 'Here is the user's resume data as JSON. Here are the form fields. Return a JSON object mapping each field to the correct value.'
>
> The LLM — either Ollama running locally or Claude via API — returns structured mappings. The extension receives them and uses `element.value = value` plus dispatching synthetic input events (to trigger React's onChange handlers) to fill each field."

**Key concepts:**
- **DOM scraping:** Reading the HTML structure to find form elements
- **Prompt engineering:** Crafting the instruction to the LLM to get structured JSON output
- **Synthetic events:** Dispatching `new Event('input', {bubbles: true})` so that JavaScript frameworks (React, Angular, Vue) detect the value change

---

### Question 5: "What security considerations did you make?"

**Answer:**

> "A few. First, PINs are never stored in plaintext — I hash them with SHA-256 before writing to disk. Even if someone reads the meta.json file, they can't recover the PIN.
>
> Second, the profile ID is sanitized before it's used as a filesystem path (`re.sub(r'[^a-zA-Z0-9\-]', '', pid)`) to prevent path traversal attacks — where an attacker might pass `../../etc/passwd` as a profile ID to read system files.
>
> Third, CORS is configured to allow the Chrome extension's `chrome-extension://` origin, which is needed since extensions operate in a different security context than web pages.
>
> Fourth, the Anthropic API key is read from an environment variable at request time, not hardcoded in source code, so it doesn't end up in version control."

---

### Question 6: "Explain async/await in the context of this project."

**Answer:**

> "Calling an LLM API takes 5-10 seconds. If I used a synchronous function, the entire server would block and couldn't handle any other requests during that time. By marking the endpoint `async def` and using `await` on the LLM call, the server can release the thread while waiting for the response and handle other requests concurrently.
>
> FastAPI uses asyncio under the hood, which is Python's event loop for cooperative multitasking. I also used an `asyncio.Lock()` — a 'traffic light' — to prevent two users from hammering the LLM simultaneously, which could cause rate limit errors or Ollama crashes on low-memory machines."

---

### Question 7: "How would you scale this beyond a single user?"

**Answer:**

> "Currently it's a single-user tool — the profiles directory is on the local filesystem. To scale it:
>
> 1. Replace flat JSON files with a real database (PostgreSQL with one row per profile per data type, or MongoDB for the document-style storage that maps naturally to the current JSON structure)
> 2. Add proper authentication (JWT tokens instead of the simple X-Profile-ID header)
> 3. Move the LLM calls to a task queue (Celery + Redis) so long-running AI jobs don't tie up the web workers
> 4. Store files (PDFs, uploads) in object storage like S3 instead of the local filesystem
> 5. Horizontally scale the FastAPI app behind a load balancer — the stateless endpoints would work fine; only the LLM traffic light would need to move to Redis-based locking"

---

### Question 8: "What was the hardest bug you fixed?"

**Answer:**

> "The sneakiest bug was a duplicate route registration. I had two `@app.get('/profile')` decorators in main.py — an old legacy one that read from a single JSON file, and the new profile-aware one that used the `X-Profile-ID` header. FastAPI silently uses the first matching route, so the profile-aware version was never called. Every profile was reading the same data — the bug was invisible until I systematically tested data isolation.
>
> I found it by running an end-to-end test script that created a new profile, wrote data to it, and verified the GET returned that specific data — not someone else's. The test caught the symptom, and a `grep` for `@app.get('/profile')` revealed the duplicate route immediately."

---

### Key Technical Terms to Know Cold

| Term | What it means in this project |
|---|---|
| **FastAPI** | Python web framework with automatic docs and async support |
| **Uvicorn** | ASGI server that runs the FastAPI app |
| **ASGI** | Asynchronous Server Gateway Interface — the Python async web standard |
| **Pydantic** | Python library for data validation using type annotations |
| **CORS** | Cross-Origin Resource Sharing — browser security policy; configured to allow the extension |
| **ATS** | Applicant Tracking System — job application platforms (Workday, Greenhouse, etc.) |
| **DOM** | Document Object Model — the browser's representation of an HTML page |
| **LLM** | Large Language Model — the AI (Ollama/Claude) that powers the smart features |
| **RAG** | Retrieval-Augmented Generation — giving the AI your specific data to ground its answers |
| **SHA-256** | Cryptographic hash function — converts PIN to a fixed-length string that can't be reversed |
| **Path traversal** | Security attack where `../` in a path escapes the intended directory |
| **Idempotent** | An operation that can run multiple times with the same result |
| **Multi-tenancy** | Architecture where one system serves multiple isolated accounts |
| **asyncio.Lock** | Python's async mutex — ensures only one coroutine executes a critical section at a time |
| **Service worker** | Background script for Chrome extensions — handles events when the popup isn't open |
| **Manifest V3** | Current Chrome extension specification (stricter security vs older Manifest V2) |
| **X-Profile-ID** | Custom HTTP request header used to identify which profile's data to use |

---

## 12. Troubleshooting

### "Server is not responding" / curl fails

1. Make sure uvicorn is still running in your terminal
2. Check the port: `curl http://localhost:5000/health`
3. If you get "connection refused", uvicorn crashed — check the terminal output for error messages

### "Ollama not available"

1. Start Ollama: `ollama serve`
2. Verify: `curl http://localhost:11434/health`
3. Make sure you pulled a model: `ollama list`
4. If empty: `ollama pull llama3.2`

### Extension shows "Cannot connect to backend"

1. Make sure the backend is running at port 5000
2. Open extension popup → Settings → Backend URL should be `http://localhost:5000`
3. Try navigating to `http://localhost:5000/health` in Chrome — if that works, the connection is fine

### Extension doesn't auto-detect the form

1. Make sure the `content.js` script is loading — check the Chrome DevTools console on the job page
2. Some ATS platforms load forms dynamically — wait for the page to fully load
3. Try refreshing the page after the form is visible

### "Profile data not saving"

1. Check that you're clicking Save after editing
2. Open DevTools (F12) → Network tab → look for the PUT /profile/... request
3. If it shows a 422 error, the JSON you're sending is malformed
4. If it shows a 404, you may have an invalid profile ID in localStorage

### Clearing a corrupted state

```bash
# Back up first!
cp -r backend/profiles backend/profiles_backup

# Check what's in meta.json
cat backend/profiles/meta.json

# If totally broken, delete and let the server recreate it
rm -rf backend/profiles/
# Restart uvicorn — the migration will recreate the default profile from master_data.json
```

---

*Document generated for LocalHire Agent v2.0 — May 2026*
