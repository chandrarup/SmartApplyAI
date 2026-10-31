"""
Closed-Loop Single-Page LaTeX Compiler for Chandra Rup Daka's Resumes.
Compiles AST to .tex, invokes pdflatex (2 passes), verifies Pages == 1 via pdfinfo,
parses Overfull \\hbox warnings, rasterizes to 150-DPI PNG via pdftoppm,
and computes pixel-level vertical page fill percentage via Pillow (PIL).
"""

import os
import re
import shutil
import subprocess
from typing import Dict, Any, Optional, List, Tuple
from PIL import Image

from src.schemas import TailoredResumeAST, CompilationResult

LAYOUT_LEVELS = [
    {
        "name": "Level 0 (Generous Breathing Room)",
        "margin_y": "0.28in",
        "margin_x": "0.36in",
        "linespread": "0.94",
        "sec_v": "3.4pt",
        "item_v": "0.5pt",
    },
    {
        "name": "Level 1 (Standard Layout)",
        "margin_y": "0.26in",
        "margin_x": "0.36in",
        "linespread": "0.92",
        "sec_v": "2.8pt",
        "item_v": "0.4pt",
    },
    {
        "name": "Level 2 (Slight Tightening)",
        "margin_y": "0.24in",
        "margin_x": "0.35in",
        "linespread": "0.90",
        "sec_v": "2.4pt",
        "item_v": "0.3pt",
    },
    {
        "name": "Level 3 (Dense Packing)",
        "margin_y": "0.22in",
        "margin_x": "0.35in",
        "linespread": "0.88",
        "sec_v": "2.0pt",
        "item_v": "0.25pt",
    },
    {
        "name": "Level 4 (Max Compression)",
        "margin_y": "0.20in",
        "margin_x": "0.34in",
        "linespread": "0.86",
        "sec_v": "1.6pt",
        "item_v": "0.2pt",
    },
]


def build_preamble(level: Dict[str, str]) -> str:
    """Builds LaTeX preamble replicating chandra_rup_daka_Resume_UH.pdf typography."""
    return rf"""\documentclass[10pt,letterpaper]{{article}}
\usepackage[left={level['margin_x']},right={level['margin_x']},top={level['margin_y']},bottom={level['margin_y']}]{{geometry}}
\usepackage{{amsmath,amssymb}}
\usepackage{{hyperref}}
\usepackage{{xcolor}}

\hypersetup{{colorlinks=false, pdfborder={{0 0 0}}}}
\pagestyle{{empty}}
\setlength{{\parindent}}{{0pt}}
\setlength{{\parskip}}{{0pt}}
\linespread{{{level['linespread']}}}

\newcommand{{\ulhref}}[2]{{\href{{#1}}{{\underline{{#2}}}}}}

\newcommand{{\ressection}}[1]{{%
  \vspace{{{level['sec_v']}}}%
  {{\large\textbf{{#1}}}}\par\vspace{{1.1pt}}%
  \hrule height 0.5pt%
  \vspace{{1.4pt}}%
}}

\newcommand{{\roleheader}}[4]{{%
  \vspace{{1.2pt}}%
  {{\small\noindent\textbf{{#1}} \hfill \textbf{{#2}}\par%
  \noindent\textit{{#3}} \hfill \textit{{#4}}\par}}%
  \vspace{{0.3pt}}%
}}

\newcommand{{\eduheader}}[4]{{%
  \vspace{{1.0pt}}%
  {{\small\noindent\textbf{{#1}} \hfill \textbf{{#2}}\par%
  \noindent\textit{{#3}} \hfill \textit{{#4}}\par}}%
}}

\newcommand{{\projheader}}[2]{{%
  \vspace{{1.2pt}}%
  {{\small\noindent#1 \hfill \textit{{#2}}\par}}%
  \vspace{{0.3pt}}%
}}

\newenvironment{{reslist}}{{%
  \begin{{list}}{{--}}{{%
    \setlength{{\leftmargin}}{{1.1em}}%
    \setlength{{\labelwidth}}{{0.75em}}%
    \setlength{{\labelsep}}{{0.35em}}%
    \setlength{{\itemsep}}{{{level['item_v']}}}%
    \setlength{{\parsep}}{{0pt}}%
    \setlength{{\topsep}}{{0.3pt}}%
    \setlength{{\partopsep}}{{0pt}}%
    \small
  }}%
}}{{%
  \end{{list}}%
  \vspace{{0.2pt}}%
}}

\newcommand{{\headerblock}}{{%
  \begin{{center}}
    {{\Huge\scshape Chandra Rup Daka}}\\[2.5pt]
    {{\footnotesize Houston, TX $\cdot$ \ulhref{{tel:12536323181}}{{+1 (253) 632-3181}} $\cdot$ \ulhref{{mailto:chandrarupdaka@gmail.com}}{{chandrarupdaka@gmail.com}} $\cdot$ \ulhref{{https://www.linkedin.com/in/chandra-rup-daka/}}{{linkedin.com/in/chandra-rup-daka}} $\cdot$ \ulhref{{https://github.com/chandrarup}}{{github.com/chandrarup}} $\cdot$ \ulhref{{https://chandrarup.github.io/Portfolio/}}{{chandrarup.github.io}}}}
  \end{{center}}
  \vspace{{-5pt}}
}}
"""


def escape_latex(text: str) -> str:
    """Escapes raw unescaped LaTeX special characters safely and idempotently."""
    if not text:
        return ""
    # Escape unescaped &, %, #
    text = re.sub(r"(?<!\\)&", r"\&", text)
    text = re.sub(r"(?<!\\)%", r"\%", text)
    text = re.sub(r"(?<!\\)#", r"\#", text)
    return text


def sanitize_project_header(title: str, url: str, tech_stack: str, year: str) -> Tuple[str, str]:
    """Ensures project header fits on a single line <= 92 plain characters."""
    title_esc = escape_latex(title)
    year_esc = escape_latex(year)
    tech_stack_clean = escape_latex(tech_stack)

    abbreviations = [
        ("Kubernetes (GKE)", "GKE"),
        ("Google Kubernetes Engine (GKE)", "GKE"),
        ("Hugging Face Transformers", "Hugging Face"),
        ("Model Context Protocol (MCP)", "MCP"),
        ("Sentence Transformers", "Sent. Transf."),
        ("PostgreSQL (pgvector)", "PostgreSQL, pgvector"),
        ("Apple Silicon mlx-whisper", "mlx-whisper"),
    ]
    curr_stack = tech_stack_clean
    combined_len = len(title) + len(curr_stack) + len(year) + 5
    if combined_len > 92:
        for orig, abbr in abbreviations:
            curr_stack = curr_stack.replace(orig, abbr)
            if len(title) + len(curr_stack) + len(year) + 5 <= 92:
                break

    header_content = rf"\textbf{{\ulhref{{{url}}}{{{title_esc}}}}} $|$ \textbf{{{curr_stack}}}"
    return header_content, curr_stack


def render_ast_to_latex(ast: TailoredResumeAST, level: Dict[str, str]) -> str:
    """Converts a TailoredResumeAST into complete, valid LaTeX document source code."""
    lines = []
    lines.append(build_preamble(level))
    lines.append(r"\begin{document}")
    lines.append(r"\headerblock")

    def clean(s: str) -> str:
        return escape_latex(re.sub(r"\s+", " ", s).strip())

    # Render sections according to ast.section_order
    for sec in ast.section_order:
        if sec == "Summary":
            lines.append(r"\ressection{Summary}")
            summary_clean = clean(ast.summary_latex)
            lines.append(rf"{{\small {summary_clean}}}\par")

        elif sec == "Education":
            lines.append(r"\ressection{Education}")
            # Degree 1: UH
            lines.append(
                r"\eduheader{University of Houston}{Aug 2025 -- May 2027}"
                r"{Master of Science in Engineering Data Science \& Artificial Intelligence (GPA: 3.9/4.0)}{Houston, TX}"
            )
            # Degree 2: Amrita
            lines.append(
                r"\eduheader{Amrita Vishwa Vidyapeetham}{Jun 2019 -- Jul 2023}"
                r"{Bachelor of Technology in Computer Science \& Engineering (Artificial Intelligence)}{Bangalore, India}"
            )
            if ast.include_certifications_in_edu:
                lines.append(
                    r"\vspace{0.8pt}"
                    r"{\small\noindent\textbf{Certifications:} Microsoft Certified: Azure AI Engineer Associate (AI-102), AWS Cloud Practitioner (CCP), Azure AI Fundamentals (AI-900)}\par"
                )

        elif sec == "Experience":
            exp_title = clean(ast.experience_section_title)
            lines.append(rf"\ressection{{{exp_title}}}")
            for entry in ast.experience_entries:
                role = clean(entry.role_title)
                dates = clean(entry.date_range)
                org = clean(entry.organization_line)
                loc = clean(entry.location)
                lines.append(rf"\roleheader{{{role}}}{{{dates}}}{{{org}}}{{{loc}}}")
                lines.append(r"\begin{reslist}")
                for b in entry.bullets:
                    b_esc = clean(b)
                    lines.append(rf"  \item {b_esc}")
                lines.append(r"\end{reslist}")

        elif sec == "Selected Projects":
            proj_title = clean(ast.projects_section_title)
            lines.append(rf"\ressection{{{proj_title}}}")
            for proj in ast.project_entries:
                header_tex, _ = sanitize_project_header(
                    proj.title, proj.url, proj.tech_stack_short, proj.year
                )
                lines.append(rf"\projheader{{{header_tex}}}{{{clean(proj.year)}}}")
                lines.append(r"\begin{reslist}")
                for b in proj.bullets:
                    b_esc = clean(b)
                    lines.append(rf"  \item {b_esc}")
                lines.append(r"\end{reslist}")

        elif sec == "Publications" and ast.publication_entries:
            lines.append(r"\ressection{Publications}")
            for pub in ast.publication_entries:
                header_tex, _ = sanitize_project_header(
                    pub.title, pub.url, pub.tech_stack_short, pub.year
                )
                lines.append(rf"\projheader{{{header_tex}}}{{{clean(pub.year)}}}")
                lines.append(r"\begin{reslist}")
                for b in pub.bullets:
                    b_esc = clean(b)
                    lines.append(rf"  \item {b_esc}")
                lines.append(r"\end{reslist}")

        elif sec == "Skills":
            lines.append(r"\ressection{Skills}")
            lines.append(r"{\small\noindent")
            for i, cat in enumerate(ast.skill_categories):
                suffix = r" \\[-1pt]" if i < len(ast.skill_categories) - 1 else ""
                cat_name = clean(cat.category_name)
                skills_val = clean(cat.skills_csv)
                lines.append(rf"\textbf{{{cat_name}:}} {skills_val}{suffix}")
            lines.append(r"}\par")

    lines.append(r"\end{document}")
    return "\n".join(lines)


def measure_vertical_fill(png_path: str) -> float:
    """
    Computes vertical page fill percentage Phi_fill in [0, 100%].
    Scans image rows from bottom up to find lowest non-white ink pixel (min(R,G,B) < 245).
    """
    if not os.path.exists(png_path):
        return 0.0

    img = Image.open(png_path).convert("RGB")
    w, h = img.size
    px = img.load()

    lowest_ink_y = 0
    # Step across columns between 5% and 95% of printable page width
    x_coords = list(range(int(w * 0.05), int(w * 0.95), 4))

    for y in range(h - 1, 0, -1):
        if any(min(px[x, y]) < 245 for x in x_coords):
            lowest_ink_y = y
            break

    fill_pct = round((lowest_ink_y / h) * 100.0, 1)
    return fill_pct


def run_pdflatex(tex_file: str, work_dir: str) -> subprocess.CompletedProcess:
    """Executes pdflatex safely with nonstopmode and halt-on-error."""
    cmd = [
        "pdflatex",
        "-interaction=nonstopmode",
        "-halt-on-error",
        os.path.basename(tex_file),
    ]
    return subprocess.run(cmd, cwd=work_dir, capture_output=True, text=True)


def parse_tex_log(log_path: str) -> Tuple[List[str], float]:
    """Extracts Overfull \\hbox warnings from pdflatex log."""
    if not os.path.exists(log_path):
        return [], 0.0

    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        log_text = f.read()

    overfull_matches = re.findall(r"Overfull \\hbox \(([0-9.]+)pt too wide\)", log_text)
    overfull_pts = [float(x) for x in overfull_matches]
    max_pt = max(overfull_pts) if overfull_pts else 0.0
    return overfull_matches, max_pt


def get_pdf_page_count(pdf_path: str) -> int:
    """Uses pdfinfo to obtain deterministic page count."""
    res = subprocess.run(["pdfinfo", pdf_path], capture_output=True, text=True)
    if res.returncode != 0:
        return 0
    for line in res.stdout.splitlines():
        if line.startswith("Pages:"):
            try:
                return int(line.split(":")[1].strip())
            except ValueError:
                return 0
    return 0


def get_spillover_text(pdf_path: str) -> str:
    """Extracts text from Page 2 when PDF spills over."""
    res = subprocess.run(["pdftotext", "-f", "2", "-l", "2", pdf_path, "-"], capture_output=True, text=True)
    return res.stdout.strip() if res.returncode == 0 else ""


def rasterize_pdf_page1(pdf_path: str, output_png_prefix: str) -> Optional[str]:
    """Rasterizes Page 1 of PDF to a 150-DPI PNG using pdftoppm."""
    res = subprocess.run(
        ["pdftoppm", "-png", "-r", "150", "-f", "1", "-l", "1", pdf_path, output_png_prefix],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        return None

    possible_names = [f"{output_png_prefix}-1.png", f"{output_png_prefix}-01.png", f"{output_png_prefix}.png"]
    for name in possible_names:
        if os.path.exists(name):
            return name
    return None


def compile_and_verify_single_page(
    ast: TailoredResumeAST,
    output_dir: str = "output",
    stem_override: Optional[str] = None,
) -> CompilationResult:
    """
    Executes the closed-loop compilation algorithm:
    1. Steps through LAYOUT_LEVELS (0 to 3) until Pages == 1.
    2. Runs pdflatex twice in /tmp to resolve hyperref links.
    3. Verifies Pages == 1 via pdfinfo.
    4. Measures vertical fill percentage (PIL) and checks Overfull \\hbox (log parser).
    5. Copies final .tex, .pdf, and .png to output_dir.
    """
    stem = stem_override or ast.target_filename_stem or "chandra_rup_daka_resume"
    os.makedirs(output_dir, exist_ok=True)
    tmp_dir = "/tmp"

    last_error = ""
    spillover_sample = ""
    warnings: List[str] = []

    for level_idx, level in enumerate(LAYOUT_LEVELS):
        full_tex = render_ast_to_latex(ast, level)

        tmp_tex = os.path.join(tmp_dir, f"{stem}.tex")
        tmp_pdf = os.path.join(tmp_dir, f"{stem}.pdf")
        tmp_log = os.path.join(tmp_dir, f"{stem}.log")

        with open(tmp_tex, "w", encoding="utf-8") as f:
            f.write(full_tex)

        # Pass 1
        res1 = run_pdflatex(tmp_tex, tmp_dir)
        if res1.returncode != 0:
            error_msg = res1.stdout[-1200:] if res1.stdout else res1.stderr
            last_error = f"LaTeX syntax error at level {level_idx}: {error_msg}"
            continue

        # Pass 2 for hyperref link stabilization
        run_pdflatex(tmp_tex, tmp_dir)

        if not os.path.exists(tmp_pdf):
            last_error = f"PDF was not generated at level {level_idx}"
            continue

        pages = get_pdf_page_count(tmp_pdf)
        _, max_overfull = parse_tex_log(tmp_log)

        if max_overfull > 2.0:
            warnings.append(f"Level {level_idx}: Max Overfull \\hbox is {max_overfull:.1f}pt too wide.")

        if pages == 1:
            # Render 150-DPI preview and measure vertical fill
            png_prefix = os.path.join(tmp_dir, f"{stem}_page")
            png_file = rasterize_pdf_page1(tmp_pdf, png_prefix)
            fill_pct = measure_vertical_fill(png_file) if png_file else 0.0

            # Destination paths
            dest_tex = os.path.join(output_dir, f"{stem}.tex")
            dest_pdf = os.path.join(output_dir, f"{stem}.pdf")
            dest_png = os.path.join(output_dir, f"{stem}.png")

            shutil.copyfile(tmp_tex, dest_tex)
            shutil.copyfile(tmp_pdf, dest_pdf)
            if png_file and os.path.exists(png_file):
                shutil.copyfile(png_file, dest_png)

            if fill_pct < 90.0:
                warnings.append(f"Vertical fill density {fill_pct}% is below optimal 90.0% threshold.")
            elif fill_pct > 98.5:
                warnings.append(f"Vertical fill density {fill_pct}% is near max boundary.")

            return CompilationResult(
                status="success",
                level_used=level_idx,
                pages=pages,
                vertical_fill_pct=fill_pct,
                max_overfull_pt=max_overfull,
                pdf_path=dest_pdf,
                tex_path=dest_tex,
                png_preview_path=dest_png if os.path.exists(dest_png) else None,
                warnings=warnings,
                errors=[],
            )
        else:
            spillover_sample = get_spillover_text(tmp_pdf)
            warnings.append(f"Level {level_idx} produced {pages} pages. Spillover: {spillover_sample[:100]}...")

    # If all 4 levels failed to keep it to 1 page
    return CompilationResult(
        status="spillover_error",
        level_used=3,
        pages=2,
        vertical_fill_pct=100.0,
        max_overfull_pt=0.0,
        pdf_path="",
        tex_path="",
        png_preview_path=None,
        spillover_text=spillover_sample,
        warnings=warnings,
        errors=[last_error or "Exceeded 1 page even at Level 3 max compression."],
    )
