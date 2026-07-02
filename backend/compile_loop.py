"""
Compile Loop — robust LaTeX → PDF compilation with retry, auto-repair, and validation.

Pipeline:
  1. Compile attempt
  2. Detect error class (missing brace, undefined command, page overflow, etc.)
  3. Apply targeted repair OR rollback to last known good
  4. Re-compile
  5. Validate PDF: exists, has pages, ATS-extractable, not >2 pages
  6. Return result with diagnostics

Never returns silently — always reports what happened.
"""
import os
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any

from logger import get_logger
_clog = get_logger("compile_loop")

PDFLATEX_BIN = "/Library/TeX/texbin/pdflatex"
PDFTOTEXT_BIN = "/opt/homebrew/bin/pdftotext"  # falls back to which()


def _find_bin(name: str, fallback: str) -> str:
    if fallback and os.path.exists(fallback):
        return fallback
    r = shutil.which(name)
    return r or fallback


PDFLATEX_BIN = _find_bin("pdflatex", PDFLATEX_BIN)
PDFTOTEXT_BIN = _find_bin("pdftotext", PDFTOTEXT_BIN)


class CompileResult:
    def __init__(self):
        self.pdf_path: str | None = None
        self.pdf_bytes: bytes | None = None
        self.success: bool = False
        self.attempts: int = 0
        self.errors: list[dict] = []  # [{type, line, message}]
        self.warnings: list[str] = []
        self.page_count: int | None = None
        self.extracted_text: str = ""
        self.ats_validation: dict[str, Any] = {}
        self.repair_actions: list[str] = []
        self.latency_ms: int = 0

    def to_dict(self):
        return {
            "success": self.success,
            "attempts": self.attempts,
            "page_count": self.page_count,
            "errors": self.errors,
            "warnings": self.warnings,
            "repair_actions": self.repair_actions,
            "ats_validation": self.ats_validation,
            "latency_ms": self.latency_ms,
            "extracted_text_len": len(self.extracted_text),
        }


def inspect_tex_hygiene(tex_content: str) -> dict[str, Any]:
    """Static checks for ATS-safe TeX hygiene (pre-compile)."""
    issues: list[str] = []
    tex_lower = (tex_content or "").lower()

    hidden_text_patterns = [
        r"\\phantom\{",
        r"\\hphantom\{",
        r"\\vphantom\{",
        r"\\textcolor\{white\}",
        r"\\color\{white\}",
        r"\\transparent\{0",
        r"\\opacity\{0",
        r"\\fontsize\{0",
    ]
    for pattern in hidden_text_patterns:
        if re.search(pattern, tex_lower):
            issues.append(f"Hidden/zero-visibility text pattern found: {pattern}")

    single_column_patterns = [
        r"\\twocolumn",
        r"\\begin\{multicols\}",
        r"\\begin\{paracol\}",
    ]
    for pattern in single_column_patterns:
        if re.search(pattern, tex_lower):
            issues.append(f"Multi-column layout pattern found: {pattern}")

    return {
        "ok": len(issues) == 0,
        "issues": issues,
        "single_column_ok": not any("Multi-column" in i for i in issues),
        "hidden_text_ok": not any("Hidden/zero-visibility" in i for i in issues),
    }


# ── LaTeX error classifier ─────────────────────────────────────────────────
ERROR_PATTERNS = [
    (r"Undefined control sequence", "undefined_command"),
    (r"Missing \\\$", "missing_math"),
    (r"Missing \}", "unbalanced_brace"),
    (r"Extra \}", "extra_brace"),
    (r"File [`']?(\S+?)[`']? not found", "missing_file"),
    (r"Paragraph ended before .* was complete", "incomplete_macro"),
    (r"Overfull \\hbox", "overflow_h"),
    (r"Overfull \\vbox", "overflow_v"),
    (r"Underfull \\hbox", "underflow"),
    (r"Argument of .* has an extra \}", "extra_brace"),
    (r"Runaway argument", "runaway"),
    (r"Emergency stop", "fatal_stop"),
]


def parse_log_errors(log_text: str) -> list[dict]:
    """Extract structured errors from pdflatex .log file."""
    errors = []
    for pattern, etype in ERROR_PATTERNS:
        for m in re.finditer(pattern, log_text):
            # Try to find the line number
            line_match = re.search(r"l\.(\d+)", log_text[max(0, m.start()-200):m.end()+200])
            line_no = int(line_match.group(1)) if line_match else None
            errors.append({
                "type": etype,
                "line": line_no,
                "message": m.group(0)[:100],
            })
    return errors


def pdftotext(pdf_path: str) -> str:
    """Extract text from PDF using poppler. Returns empty string on failure."""
    if not os.path.exists(pdf_path) or not PDFTOTEXT_BIN:
        return ""
    try:
        result = subprocess.run(
            [PDFTOTEXT_BIN, "-layout", pdf_path, "-"],
            capture_output=True, timeout=30,
        )
        if result.returncode == 0:
            return result.stdout.decode("utf-8", errors="replace")
    except Exception:
        pass
    return ""


def count_pages(pdf_path: str) -> int | None:
    """Get PDF page count via pdftotext or fallback."""
    if not os.path.exists(pdf_path):
        return None
    try:
        # pdftotext -layout doesn't print page count; use pdfinfo if available
        pdfinfo = shutil.which("pdfinfo")
        if pdfinfo:
            r = subprocess.run([pdfinfo, pdf_path], capture_output=True, timeout=10)
            if r.returncode == 0:
                m = re.search(r"Pages:\s+(\d+)", r.stdout.decode())
                if m: return int(m.group(1))
        # Fallback: count \f form-feeds in pdftotext output
        text = pdftotext(pdf_path)
        return text.count("\f") + 1 if text else None
    except Exception:
        return None


def validate_ats_extractability(pdf_path: str, expected_contains: dict[str, str]) -> dict[str, Any]:
    """Verify PDF text is extractable and contains required identity fields.

    expected_contains: dict like {"name": "Chandra Rup Daka", "email": "chandrarupdaka@gmail.com"}
    Returns dict with per-check booleans + extracted_text_len.
    """
    text = pdftotext(pdf_path)
    text_lower = text.lower()
    result = {
        "extracted_text_len": len(text),
        "extraction_ok": len(text) > 800,  # any real resume produces 800+ chars
        "checks": {},
    }
    for key, expected in expected_contains.items():
        if not expected:
            continue
        # Look for at least the unique part (last word for names, before @ for email)
        haystack_terms = [expected.lower()]
        if "@" in expected:
            haystack_terms.append(expected.split("@")[0].lower())
        if " " in expected:
            haystack_terms.append(expected.split()[-1].lower())
        result["checks"][key] = any(t in text_lower for t in haystack_terms if t)

    # Check standard section headings are present
    sections_found = [s for s in ("summary", "education", "experience", "skills", "projects")
                      if s in text_lower]
    result["sections_found"] = sections_found
    result["sections_ok"] = len(sections_found) >= 3
    result["overall_ok"] = (result["extraction_ok"]
                            and result["sections_ok"]
                            and all(result["checks"].values()))
    return result


# ── Targeted repair functions ───────────────────────────────────────────────
def repair_undefined_command(tex: str, error: dict) -> tuple[str, str]:
    """If an undefined command appears, strip the macro call. Returns (new_tex, action)."""
    # Find the offending line (if known)
    if not error.get("line"):
        return tex, ""
    lines = tex.split("\n")
    idx = error["line"] - 1
    if idx < 0 or idx >= len(lines):
        return tex, ""
    # Strip any \unknown{...} → just the {...}
    new_line = re.sub(r"\\([A-Za-z]+)\{([^}]*)\}", r"\2", lines[idx])
    if new_line != lines[idx]:
        lines[idx] = new_line
        return "\n".join(lines), f"Stripped undefined command on line {error['line']}"
    return tex, ""


def repair_unbalanced_braces(tex: str) -> tuple[str, str]:
    """Add missing braces at end of file if obvious imbalance."""
    # Count braces ignoring comments and escaped
    stripped = re.sub(r"%[^\n]*", "", tex)
    stripped = re.sub(r"\\.", "", stripped)
    open_n = stripped.count("{")
    close_n = stripped.count("}")
    if open_n > close_n:
        # Add closing braces before \end{document}
        diff = open_n - close_n
        if "\\end{document}" in tex:
            tex = tex.replace("\\end{document}", "}" * diff + "\n\\end{document}")
            return tex, f"Added {diff} closing braces before \\end{{document}}"
    return tex, ""


REPAIR_HANDLERS = {
    "undefined_command": repair_undefined_command,
    "unbalanced_brace": lambda tex, err: repair_unbalanced_braces(tex),
    "extra_brace": lambda tex, err: ("", ""),  # no auto-fix yet
    "incomplete_macro": lambda tex, err: ("", ""),
}


def compile_pdf(tex_content: str, work_dir: str, name: str = "tailored_resume") -> CompileResult:
    """Single pdflatex compile attempt. Doesn't retry — that's the caller's job."""
    result = CompileResult()
    t0 = time.time()
    tex_path = os.path.join(work_dir, f"{name}.tex")
    pdf_path = os.path.join(work_dir, f"{name}.pdf")
    log_path = os.path.join(work_dir, f"{name}.log")

    # Delete stale PDF so we know if compile produced fresh one
    for f in [pdf_path, log_path]:
        if os.path.exists(f):
            try: os.remove(f)
            except: pass

    with open(tex_path, "w") as f:
        f.write(tex_content)

    try:
        proc = subprocess.run(
            [PDFLATEX_BIN, "-interaction=nonstopmode", "-halt-on-error=false", tex_path],
            capture_output=True, cwd=work_dir, timeout=120,
        )
    except subprocess.TimeoutExpired:
        result.errors.append({"type": "timeout", "line": None, "message": "pdflatex timed out after 120s"})
        result.latency_ms = int((time.time() - t0) * 1000)
        return result
    except FileNotFoundError as e:
        result.errors.append({"type": "missing_binary", "line": None, "message": str(e)})
        result.latency_ms = int((time.time() - t0) * 1000)
        return result

    log_text = ""
    if os.path.exists(log_path):
        try:
            with open(log_path, errors="replace") as f:
                log_text = f.read()
        except Exception:
            pass

    result.errors = parse_log_errors(log_text)

    if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 1024:
        result.success = True
        result.pdf_path = pdf_path
        result.page_count = count_pages(pdf_path)
        with open(pdf_path, "rb") as f:
            result.pdf_bytes = f.read()

    result.latency_ms = int((time.time() - t0) * 1000)
    return result


def _find_section(tex: str, name: str) -> tuple[int, int] | None:
    """Find a \\section{name} ... up to next \\section{ or \\end{document}.
    Returns (start, end) char offsets, or None.
    """
    m = re.search(r"\\section\{" + re.escape(name) + r"\}", tex)
    if not m:
        return None
    start = m.start()
    # Find next \section or \end{document}
    next_section = re.search(r"\\section\{|\\end\{document\}", tex[m.end():])
    if next_section:
        return (start, m.end() + next_section.start())
    return (start, len(tex))


def _count_blocks(text: str, anchor: str) -> int:
    """Count occurrences of a starting anchor in text."""
    return text.count(anchor)


def _remove_last_block(text: str, anchor: str) -> tuple[str, bool]:
    """Find the LAST occurrence of anchor and remove from there until the next
    anchor OR end of section. Returns (new_text, removed)."""
    positions = []
    start = 0
    while True:
        idx = text.find(anchor, start)
        if idx == -1: break
        positions.append(idx)
        start = idx + len(anchor)
    if len(positions) < 2:
        return text, False
    last_start = positions[-1]
    # End of last block = next \resumeProjectHeading occurrence after last_start (none, so end-of-section)
    # OR \resumeSubHeadingListEnd (the section terminator)
    end_anchor = text.find("\\resumeSubHeadingListEnd", last_start)
    if end_anchor == -1:
        return text, False
    # Walk back to start of the current item line
    return text[:last_start] + text[end_anchor:], True


def _trim_to_fit_one_page(tex: str) -> tuple[str, str]:
    """Trim content progressively to fit one page. Each call removes ONE thing.
    Strategy priority designed to preserve most-recent + most-relevant content.
    """
    # Strategy 1: Remove last project entry
    proj = _find_section(tex, "Projects")
    if proj:
        sect = tex[proj[0]:proj[1]]
        count = _count_blocks(sect, "\\resumeProjectHeading")
        if count > 1:
            new_sect, removed = _remove_last_block(sect, "\\resumeProjectHeading")
            if removed:
                return tex[:proj[0]] + new_sect + tex[proj[1]:], f"Trimmed 1 project ({count-1} remaining)"

    # Strategy 2: Remove last certification
    cert = _find_section(tex, "Certifications")
    if cert:
        sect = tex[cert[0]:cert[1]]
        count = _count_blocks(sect, "\\resumeProjectHeading")
        if count > 1:
            new_sect, removed = _remove_last_block(sect, "\\resumeProjectHeading")
            if removed:
                return tex[:cert[0]] + new_sect + tex[cert[1]:], f"Trimmed 1 certification ({count-1} remaining)"

    # Strategy 3: Remove last Research Publication entry
    pubs = _find_section(tex, "Research Publications")
    if pubs:
        sect = tex[pubs[0]:pubs[1]]
        count = _count_blocks(sect, "\\resumeProjectHeading")
        if count > 1:
            new_sect, removed = _remove_last_block(sect, "\\resumeProjectHeading")
            if removed:
                return tex[:pubs[0]] + new_sect + tex[pubs[1]:], f"Trimmed 1 publication ({count-1} remaining)"

    # Strategy 4: Drop longest experience bullet
    exp = _find_section(tex, "Experience")
    if exp:
        exp_text = tex[exp[0]:exp[1]]
        bullets = list(re.finditer(r"\\resumeItem\{([^}]*)\}", exp_text))
        if len(bullets) > 4:
            longest = max(bullets, key=lambda m: len(m.group(0)))
            new_exp = exp_text[:longest.start()] + exp_text[longest.end():]
            return tex[:exp[0]] + new_exp + tex[exp[1]:], f"Trimmed longest experience bullet"

    # Strategy 5: Remove entire Research Publications section
    if pubs:
        return tex[:pubs[0]] + tex[pubs[1]:], "Removed entire Research Publications section"

    # Strategy 6: Remove entire Certifications section
    if cert:
        return tex[:cert[0]] + tex[cert[1]:], "Removed entire Certifications section"

    # Strategy 7: Remove entire Projects section
    if proj:
        return tex[:proj[0]] + tex[proj[1]:], "Removed entire Projects section"

    # Strategy 8: Last resort — drop last experience entry
    if exp:
        exp_text = tex[exp[0]:exp[1]]
        subheadings = list(re.finditer(r"\\resumeSubheading", exp_text))
        if len(subheadings) > 1:
            last_start = subheadings[-1].start()
            end_anchor = exp_text.find("\\resumeSubHeadingListEnd", last_start)
            if end_anchor > last_start:
                new_exp = exp_text[:last_start] + exp_text[end_anchor:]
                return tex[:exp[0]] + new_exp + tex[exp[1]:], "Removed last experience entry"

    return tex, ""  # nothing more to trim


def compile_with_retry(
    tex_content: str,
    work_dir: str,
    *,
    name: str = "tailored_resume",
    max_attempts: int = 5,
    target_max_pages: int = 1,
    ats_expected: dict | None = None,
) -> CompileResult:
    """Compile with auto-repair retry + page-trimming. Returns final CompileResult.

    target_max_pages: desired max pages. If exceeded, trim and recompile.
    """
    current_tex = tex_content
    final_result = None

    _clog.info(f"compile_with_retry start — max_attempts={max_attempts} "
               f"target_pages={target_max_pages} tex_chars={len(tex_content)}")

    for attempt in range(1, max_attempts + 1):
        _clog.debug(f"Attempt {attempt}/{max_attempts} — tex_chars={len(current_tex)}")
        result = compile_pdf(current_tex, work_dir, name=name)
        result.attempts = attempt
        final_result = result

        if result.success:
            _clog.info(f"Attempt {attempt}: COMPILED — pages={result.page_count} "
                       f"latency={result.latency_ms}ms target={target_max_pages}")
            if result.page_count and result.page_count > target_max_pages:
                if attempt < max_attempts:
                    trimmed_tex, action = _trim_to_fit_one_page(current_tex)
                    if trimmed_tex != current_tex:
                        current_tex = trimmed_tex
                        result.repair_actions.append(action + f" (was {result.page_count} pages)")
                        _clog.info(f"Trim action: {action} — new tex_chars={len(current_tex)}")
                        continue  # recompile
                    else:
                        _clog.warning(f"Trim returned no change — accepting {result.page_count} pages")
                # Still over budget — accept with warning
                result.warnings.append(f"PDF has {result.page_count} pages (target ≤ {target_max_pages}) — could not trim further")
            # Validate ATS extractability
            if ats_expected:
                result.ats_validation = validate_ats_extractability(result.pdf_path, ats_expected)
                result.extracted_text = pdftotext(result.pdf_path)
                if not result.ats_validation.get("overall_ok"):
                    result.warnings.append("ATS extractability check failed: " + json.dumps(result.ats_validation))
                    _clog.warning(f"ATS check failed — {result.ats_validation}")
                else:
                    _clog.info(f"ATS check passed — extracted_len={result.ats_validation.get('extracted_text_len')}")
            _clog.info(f"compile_with_retry DONE — success=True attempts={attempt} "
                       f"pages={result.page_count} repairs={result.repair_actions}")
            return result

        # Compile failed — log errors and try LaTeX repair
        error_types = [e.get("type") for e in result.errors]
        _clog.warning(f"Attempt {attempt}: FAILED — errors={error_types} latency={result.latency_ms}ms")
        repaired = False
        for err in result.errors:
            handler = REPAIR_HANDLERS.get(err.get("type"))
            if handler:
                new_tex, action = handler(current_tex, err)
                if new_tex and new_tex != current_tex:
                    current_tex = new_tex
                    result.repair_actions.append(action)
                    repaired = True
                    _clog.info(f"Repair applied: {action} (error type: {err.get('type')})")
                    break
        if not repaired:
            _clog.error(f"No repair handler could fix errors {error_types} — stopping after attempt {attempt}")
            break  # nothing more to try

    _clog.error(f"compile_with_retry EXHAUSTED — success=False after {max_attempts} attempts "
                f"repairs={final_result.repair_actions if final_result else []}")
    return final_result


import json  # used in warnings
