"""
Command-Line Interface (CLI) Entrypoint for Antigravity Resume Engine.
Accepts a raw job description file or pasted text and generates verified,
single-page .tex and .pdf resumes with high-DPI rasterization and fit audit reports.
"""

import sys
import os
import argparse
import subprocess
from typing import Optional

from src.pipeline import generate_tailored_resume
from src.schemas import ArchetypeTrack


def parse_args():
    parser = argparse.ArgumentParser(
        description="Autonomous Resume Synthesis Engine for Chandra Rup Daka.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python cli.py --jd-file tests/sample_jd.txt --company "Anthropic" --title "AI Systems Engineer"
  python cli.py --jd-text "We are hiring an Agentic AI Engineer with LangGraph and FastAPI..."
  python -m src.cli --jd-file sample_jd.txt --track agentic_swe --out-dir output/
        """,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--jd-file", "-f", type=str, help="Path to plain text file containing job description")
    group.add_argument("--jd-text", "-t", type=str, help="Raw pasted job description text")

    parser.add_argument("--company", "-c", type=str, default=None, help="Target company name")
    parser.add_argument("--title", "-r", type=str, default=None, help="Target role title")
    parser.add_argument(
        "--track",
        type=str,
        choices=["agentic_swe", "forward_deployed_engineer", "core_aiml", "bioinformatics_ai", "data_analyst_ds"],
        default=None,
        help="Manual override for candidate archetype track",
    )
    parser.add_argument("--out-dir", "-o", type=str, default="output", help="Directory where .tex, .pdf, and .png are saved")
    parser.add_argument("--stem", "-s", type=str, default=None, help="Stem name for output files")
    parser.add_argument("--open-pdf", action="store_true", help="Automatically open generated PDF upon completion (macOS/Linux)")
    return parser.parse_args()


def main():
    args = parse_args()

    # Load raw JD text
    if args.jd_file:
        if not os.path.exists(args.jd_file):
            print(f"Error: Job description file not found at '{args.jd_file}'", file=sys.stderr)
            sys.exit(1)
        with open(args.jd_file, "r", encoding="utf-8") as f:
            raw_jd = f.read()
    else:
        raw_jd = args.jd_text

    print("=" * 70)
    print(" ANTIGRAVITY RESUME ENGINE — AUTONOMOUS QUALIFICATION COMPILER")
    print(" Candidate: Chandra Rup Daka")
    print("=" * 70)
    print("[1/5] Ingesting Job Description & Running Requirements Deconstruction...")

    resp = generate_tailored_resume(
        raw_jd=raw_jd,
        company_name=args.company,
        role_title=args.title,
        track_override=args.track,
        output_dir=args.out_dir,
        output_stem=args.stem,
    )

    if not resp.success:
        print(f"\n❌ Pipeline failed: {resp.error}", file=sys.stderr)
        if resp.compilation_result:
            print(f"Compilation status: {resp.compilation_result.status}", file=sys.stderr)
            for err in resp.compilation_result.errors:
                print(f"  Error: {err}", file=sys.stderr)
        sys.exit(1)

    jd = resp.deconstructed_jd
    comp = resp.compilation_result
    audit = resp.fit_audit

    print(f"\n[2/5] Requirements Deconstructed:")
    print(f"  • Company:        {jd.company_name}")
    print(f"  • Target Title:   {jd.role_title}")
    print(f"  • Archetype:      {jd.selected_track.upper()} ({jd.archetype_justification})")
    print(f"  • Top Skills:     {', '.join(jd.top_hard_skills[:6])}...")
    print(f"  • Base Languages: {', '.join(jd.implied_base_languages)}")

    if jd.knockout_flags:
        print(f"  ⚠️ Knockout Warning: {', '.join(jd.knockout_flags)}")
    if jd.true_gaps:
        print(f"  ℹ️ Candidate Gaps:   {', '.join(jd.true_gaps)}")

    print(f"\n[3/5] Candidate Evidence Retrieved & Qualification AST Synthesized.")
    print(f"[4/5] Closed-Loop LaTeX Compiler Results:")
    print(f"  • Status:               {comp.status.upper()}")
    print(f"  • Deterministic Pages:  {comp.pages} page(s)")
    print(f"  • Compression Tier:     Level {comp.level_used}")
    print(f"  • Vertical Page Fill:   {comp.vertical_fill_pct}% (Optimal: 90%–98.5%)")
    print(f"  • Max Overfull \\hbox:   {comp.max_overfull_pt:.1f} pt")

    if comp.warnings:
        for w in comp.warnings:
            print(f"  ⚠️ {w}")

    print(f"\n[5/5] Compliance & Fit Audit Completed:")
    print(f"  • Overall Fit Score:    {audit.overall_fit_score}/100")
    print(f"  • Strong Matches:       {len(audit.strong_matches)} items")
    print(f"  • ATS Safety Check:     {'PASSED' if audit.ats_safety_check else 'FAILED'}")

    print("\n" + "=" * 70)
    print(" VERIFIED ARTIFACTS GENERATED SUCCESSFULLY:")
    print(f"  • PDF Resume:  {os.path.abspath(resp.pdf_path)}")
    print(f"  • LaTeX Source: {os.path.abspath(resp.tex_path)}")
    if resp.png_preview_path:
        print(f"  • PNG Preview:  {os.path.abspath(resp.png_preview_path)}")
    stem = args.stem or comp.tex_path.split("/")[-1].replace(".tex", "")
    audit_path = os.path.join(args.out_dir, f"{stem}_audit.md")
    if os.path.exists(audit_path):
        print(f"  • Audit Report: {os.path.abspath(audit_path)}")
    print("=" * 70)

    if args.open_pdf and sys.platform == "darwin":
        subprocess.run(["open", resp.pdf_path])


if __name__ == "__main__":
    main()
