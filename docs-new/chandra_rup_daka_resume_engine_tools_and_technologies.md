# Complete Toolchain & Technologies Specification for the Antigravity Custom Resume Engine

This companion specification documents every CLI tool, system package, Python library, LaTeX package, and verification command used to inspect, synthesize, compile, and visually verify single-page tailored resumes for **Chandra Rup Daka** ([GitHub](https://github.com/chandrarup), [Career Repository](https://www.google.com/search?q=https://github.com/chandrarup/career), [Portfolio](https://chandrarup.github.io/Portfolio/)).

---

## 1. End-to-End Tool Inventory & Exact Commands Executed

| Stage | Tool / Binary / Library | System / Python Package | Exact CLI Command or API Invoked | Why It Is Used in the Pipeline |
| :--- | :--- | :--- | :--- | :--- |
| **1. Template Reverse Engineering** | `pdfinfo` & `pdffonts` | `poppler-utils` (Linux apt) | `pdfinfo current_resume.pdf && pdffonts current_resume.pdf` | Extracts exact page dimensions (`612 x 792 pts` US Letter) and embedded Type 1 font names (`CMCSC10`, `CMBX12`, `CMBX9`, `CMTI9`, `CMR9`, `CMR8`) from [chandra_rup_daka_Resume_UH.pdf](https://drive.google.com/file/d/1S7voPE4orpTXnTbV6oNoho1V5DL-8pVd/view?usp=drivesdk). |
| **2. Live JD & Profile Scraping** | Headless Chromium / Playwright / Selenium | `selenium` / `playwright` + `chromium` | Headless browser DOM extraction on SPA job portals (Workday, Greenhouse, Lever, Ashby, Tsenta) | Extracts full job descriptions and knockout questions from JavaScript-rendered Single-Page Applications where static HTTP requests fail. |
| **3. Structured JD & AST Synthesis** | Pydantic + LLM Structured Outputs | `pydantic>=2.0`, `openai` / `anthropic` / `google-genai` / `boto3` | `client.beta.chat.completions.parse(..., response_format=TailoredResumeAST)` | Enforces strict JSON schema validation for JD deconstruction (`DeconstructedJD`) and LaTeX bullet synthesis (`TailoredResumeAST`). |
| **4. Fast Vector PDF Compilation** | `pdflatex` (`pdfTeX`) | `texlive-latex-base`, `texlive-fonts-recommended`, `texlive-latex-extra` | `pdflatex -interaction=nonstopmode -halt-on-error resume.tex` (executed twice inside `/tmp`) | Compiles `.tex` source to vector `.pdf` in $\sim 0.15\text{s}$ using native Type 1 Computer Modern fonts and resolves `hyperref` clickable links on Pass 2. |
| **5. Horizontal Margin Audit** | Python `re` Log Parser | Python Standard Library (`re`) | Parses `/tmp/resume.log` for `r"Overfull \\hbox \(([0-9.]+)pt too wide\)"` | Detects any project header or underlined hyperlink (`\ulhref`) that protrudes past the right margin before visual rendering. |
| **6. Deterministic Page-Count Check** | `pdfinfo` | `poppler-utils` (Linux apt) | `pdfinfo /tmp/resume.pdf \| grep "Pages:"` | Verifies that the compiled PDF is strictly **1 page** (`Pages: 1`). |
| **7. Spillover Line Extractor** | `pdftotext` | `poppler-utils` (Linux apt) | `pdftotext -f 2 -l 2 /tmp/resume.pdf -` | When `Pages > 1`, extracts the exact text that spilled onto Page 2 so the controller knows whether 1 word or 4 lines overflowed. |
| **8. High-DPI Page Rasterization** | `pdftoppm` (or `pypdfium2`) | `poppler-utils` / `pypdfium2` | `pdftoppm -png -r 150 /tmp/resume.pdf /tmp/resume_page` | Renders the compiled 1-page PDF into a 150-DPI PNG image (`1275 x 1650 px`) for pixel-level layout and density inspection. |
| **9. Pixel-Level Vertical Fill Scanner** | `Pillow (PIL)` | `Pillow` (`pip install Pillow`) | Scans RGB rows from bottom up to find lowest non-white pixel ($\min(R,G,B) < 245$) | Computes exact vertical page fill percentage ($\Phi_{\text{fill}} \in [92\%, 98\%]$) to guarantee zero awkward bottom whitespace gaps. |

---

## 2. Environment Setup Script for Antigravity (`setup_env.sh`)

Run this setup script inside your Antigravity workspace or Docker container to install every required binary and Python library:

```bash
#!/usr/bin/env bash
set -euo pipefail

# 1. Install TeX Live (pdflatex + Computer Modern Type 1 fonts) and Poppler PDF utilities
sudo apt-get update && sudo apt-get install -y --no-install-recommends \
    texlive-latex-base \
    texlive-latex-recommended \
    texlive-latex-extra \
    texlive-fonts-recommended \
    poppler-utils

# 2. Install Python dependencies for structured LLM synthesis, PDF rasterization, and pixel scanning
pip install --upgrade \
    pydantic>=2.7.0 \
    Pillow>=10.0.0 \
    pypdfium2>=4.30.0 \
    pypdf>=4.2.0 \
    pdfplumber>=0.11.0 \
    openai>=1.40.0 \
    anthropic>=0.34.0 \
    google-genai>=0.3.0
```

---

## 3. Exact LaTeX Packages & Macros Used (`uh_resume_template.sty`)

Every single-page resume generated for Chandra Rup Daka uses only standard, zero-external-dependency TeX Live packages so it compiles identically in local `pdflatex`, `lualatex`, Overleaf, and [SmartApplyAI](https://www.google.com/search?q=https://github.com/chandrarup/SmartApplyAI):

* `\documentclass[10pt,letterpaper]{article}` — Base 10pt article class on US Letter paper (`8.5in x 11in`).
* `\usepackage[left=0.35in,right=0.35in,top=0.22in,bottom=0.22in]{geometry}` — Precision margins controlled dynamically by the closed-loop layout tuner.
* `\usepackage{amsmath,amssymb}` — Provides math-mode symbols (`$\cdot$`, `$\times$`, `$\log_{10}$`, `$\mu$m`, `$\beta$`).
* `\usepackage{hyperref}` with `\hypersetup{colorlinks=false, pdfborder={0 0 0}}` — Embeds clickable PDF annotations without colored boxes, paired with `\newcommand{\ulhref}[2]{\href{#1}{\underline{#2}}}` to match the underlined links in [chandra_rup_daka_Resume_UH.pdf](https://drive.google.com/file/d/1S7voPE4orpTXnTbV6oNoho1V5DL-8pVd/view?usp=drivesdk).
* `\usepackage{xcolor}` — Standard color support.
* **Custom Layout Macros:**
  * `\headerblock`: Renders `\Huge\scshape Chandra Rup Daka` (`CMCSC10`) and the single-line `\footnotesize` (`CMR8`) contact bar.
  * `\ressection{#1}`: Renders `\large\textbf{#1}` (`CMBX12`) followed by `\hrule height 0.5pt`.
  * `\roleheader{#1}{#2}{#3}{#4}`: Renders the 2-line experience header (`\small\textbf{Role} \hfill \textbf{Dates}` above `\small\textit{Company} \hfill \textit{Location}`).
  * `\eduheader{#1}{#2}{#3}{#4}`: Compact 2-line education header with `1pt` top spacing so both degrees sit tightly grouped.
  * `\projheader{#1}{#2}`: Single-line project header (`\small\noindent#1 \hfill \textit{#2}`).
  * `\begin{reslist} ... \end{reslist}`: Custom en-dash (`--`) list environment in `\small` (`9pt` `CMR9`) with zero paragraph skip and tunable `\itemsep`.

---

## 4. Tool Definitions to Register in Your Antigravity Coding Agent

When configuring your Antigravity agent to autonomously generate and verify a resume from a job description, expose these four deterministic tool functions to the agent:

1. `load_candidate_knowledge_graph() -> dict`: Reads `data/career_knowledge.json` containing Chandra Rup Daka's verified roles, projects, publications, and locked scale metrics.
2. `validate_bullet_constraints(resume_ast_json: str) -> dict`: Programmatically verifies that:
   - Every skill in `skill_categories` that matches the JD also appears in at least one bullet (`bijective_grounding == True`).
   - Every framework in a bullet includes its base language (`Python`, `TypeScript`, `JavaScript`, `SQL`, `Java`, `R`).
   - Every bullet is between `165` and `230` plain-text characters (strictly 1.5–2 rendered lines) and uses zero banned AI-slop verbs.
   - Every project header (`title + tech_stack_short + year`) is $\le 92$ plain-text characters so the right-aligned year never wraps onto a second line.
3. `compile_and_verify_latex(tex_content: str, output_stem: str) -> dict`: Runs `pdflatex` (2 passes in `/tmp`), checks `pdfinfo` (`Pages == 1`), checks `.log` (`Overfull \hbox`), runs `pdftoppm -png -r 150`, and runs the `Pillow` pixel scanner to return `pages`, `spillover_text` (via `pdftotext -f 2 -l 2`), `vertical_fill_pct`, and `png_preview_path`.
4. `inspect_rendered_png(png_preview_path: str) -> dict`: Passes the 150-DPI rendered PNG to the multimodal vision model to confirm zero visual overlap, clean horizontal rule alignment, and balanced vertical distribution before saving the final `.tex` and `.pdf`.
