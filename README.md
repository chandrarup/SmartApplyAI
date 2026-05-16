# SmartApplyAI

SmartApplyAI is currently focused on a clean **Phase 1 resume-tailoring workflow**:

1. Run the FastAPI backend locally.
2. Open the dashboard.
3. Paste a job description.
4. Analyze the match.
5. Tailor the resume.
6. Download the tailored PDF.

The Chrome extension and ATS autofill code are still in the repo, but they are treated as **Phase 2 / secondary** while the dashboard-first resume workflow is the supported path.

## Project structure

- `backend/` — FastAPI app, dashboard UI, resume tailoring pipeline, PDF generation
- `extension/` — Chrome extension for later ATS autofill work
- `tests/unit/` — lightweight Python and JS unit tests
- `tests/smoke/` — backend smoke tests for the Phase 1 flow
- `tests/integration/` — optional Playwright-heavy tests

## Canonical local defaults

- Backend URL: `http://127.0.0.1:5001`
- Primary AI provider: Ollama
- Default Ollama API URL: `http://localhost:11434/v1/chat/completions`
- Default Ollama health URL: `http://localhost:11434/api/tags`
- Default model: `qwen2.5-coder:7b`

Optional environment variables:

- `OLLAMA_API_URL`
- `OLLAMA_HEALTH_URL`
- `OLLAMA_MODEL`
- `ANTHROPIC_API_KEY`

## Requirements

### Required for Phase 1

- Python 3.10+
- Node.js 20+
- Ollama running locally
- A TeX installation that provides `pdflatex`

Recommended for better PDF validation:

- Poppler tools that provide `pdftotext` and `pdfinfo`

### Install dependencies

```bash
npm run setup:backend
```

Optional dependencies:

- Claude support:

```bash
npm run setup:optional
```

- Optional browser-based integration tests may also need Playwright browsers:

```bash
python3 -m playwright install chromium
```

## Run the backend

```bash
npm run dev:backend
```

Then open:

- Dashboard: [http://127.0.0.1:5001/dashboard](http://127.0.0.1:5001/dashboard)
- Health check: [http://127.0.0.1:5001/health](http://127.0.0.1:5001/health)

## Phase 1 workflow

1. Start Ollama with your preferred local model available.
2. Start the backend with `npm run dev:backend`.
3. Open the dashboard.
4. Save or review your profile data.
5. Go to `Analyze JD` or `Tailor Resume`.
6. Paste the target job description.
7. Run analysis and generate the tailored resume.
8. Download the PDF.

## Test commands

Run the standard local checks:

```bash
npm test
```

This runs:

- JS unit tests
- Python unit tests
- Phase 1 backend smoke tests

Optional tests:

```bash
npm run test:integration
npm run test:resume-pipeline
```

Notes:

- `test:integration` depends on Playwright for Python.
- `test:resume-pipeline` expects the backend to already be running and Ollama to be available.
- PDF generation tests are sensitive to your local TeX toolchain.

## PDF generation notes

The backend compiles resumes with a local LaTeX toolchain. The supported path is:

- `pdflatex` for PDF generation
- `pdftotext` and `pdfinfo` for ATS/page-count validation

If these tools are missing, the backend now returns an explicit error that tells you which tool is unavailable.

## Phase 2 extension work

The extension remains available in `extension/`, but it is not the primary supported product surface for this cleanup pass. See [docs/phase2-extension.md](/Users/chandrarupdaka/Documents/Personal/SmartApplyAI/docs/phase2-extension.md).
