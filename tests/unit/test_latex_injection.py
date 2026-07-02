"""
LaTeX injection hardening regression suite (fix/latex-injection).

For each hostile payload we assert three things:
  1. NEUTRALIZED  — the raw command does not survive into the rendered .tex as a live
     control sequence (no live \\write18, \\input, \\textbf{...}, etc.).
  2. COMPILES     — the PDF still builds (1 page) when pdflatex is present.
  3. LITERAL      — the payload's visible text is present in the source as escaped chars.

Also covers _safe_href_url() scheme validation and the clean_json root-array fix.
"""

from __future__ import annotations

import json
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

import compile_loop  # noqa: E402
import llm_provider  # noqa: E402
import main  # noqa: E402

BACKEND_DIR = os.path.dirname(main.__file__)
HAS_PDFLATEX = shutil.which("pdflatex") is not None


@pytest.fixture
def master():
    return main._enrich_profile_with_resume_sources(main.load_pdata("default"))


# Payloads that must never execute or read files. Keyed by a short id.
HOSTILE_PAYLOADS = {
    "textbf": r"\textbf{INJECT}",
    "write18": r"\write18{rm -rf /}",
    "immediate_write18": r"\immediate\write18{touch /tmp/pwned}",
    "input": r"\input{/etc/passwd}",
    "include": r"\include{/etc/passwd}",
    "catcode": r"\catcode`\%=12 malicious",
    "openin": r"\openin0=/etc/passwd",
    "math_display": r"$\displaystyle \frac{1}{0}$",
    "brace_open_bomb": "{{{{{ unbalanced",
    "brace_close_bomb": "}}}}} unbalanced",
    "accents": r"^ ~ \ raw specials",
    "def_redefine": r"\def\x{boom}\x",
}

# Commands that never legitimately appear in the template — if any shows up, an
# injection survived. (The template's own \input{glyphtounicode} is fine and is NOT
# in this list; per-payload neutralization is checked separately via `value not in tex`.)
FORBIDDEN_ANYWHERE = [
    r"\write18",
    r"\immediate\write",
    r"\openin",
    r"\catcode",
    r"\textbf{INJECT}",
    r"\def\x",
    r"\input{/etc/passwd}",
    r"\include{/etc/passwd}",
]


def _render_with(master, field, value):
    """Render the resume template with `value` injected into one field."""
    m = json.loads(json.dumps(master))
    if field == "summary":
        m["summary"] = value
    elif field == "bullet":
        assert m.get("experience"), "fixture profile has no experience"
        m["experience"][0]["details"] = [value]
        m["experience"][0]["bullets"] = [value]
    elif field == "project_title":
        assert m.get("projects"), "fixture profile has no projects"
        m["projects"][0]["title"] = value
    elif field == "project_url":
        assert m.get("projects"), "fixture profile has no projects"
        m["projects"][0]["url"] = value
        m["projects"][0]["title"] = "Safe Project Title"
    else:
        raise ValueError(field)
    return main._render_tex_from_master(m)


@pytest.mark.parametrize("payload_id", list(HOSTILE_PAYLOADS))
@pytest.mark.parametrize("field", ["summary", "bullet", "project_title"])
def test_hostile_payload_neutralized_and_compiles(master, field, payload_id):
    value = HOSTILE_PAYLOADS[payload_id]
    tex = _render_with(master, field, value)

    # 1. NEUTRALIZED — the raw hostile string never appears verbatim (it was escaped),
    #    and no known-dangerous command shows up anywhere in the source.
    assert value not in tex, f"raw payload survived for {field}/{payload_id}"
    for marker in FORBIDDEN_ANYWHERE:
        assert marker not in tex, f"live command {marker!r} survived for {field}/{payload_id}"

    # Hygiene checks must still pass (no hidden text / multi-column smuggled).
    hygiene = compile_loop.inspect_tex_hygiene(tex)
    assert hygiene["ok"], hygiene

    # 2. COMPILES — the escaped payload builds a valid 1-page PDF.
    if not HAS_PDFLATEX:
        pytest.skip("pdflatex not installed")
    result = compile_loop.compile_with_retry(
        tex, work_dir=BACKEND_DIR, name=f"inj_{field}_{payload_id}",
        max_attempts=4, target_max_pages=1,
    )
    assert result.success, result.errors
    assert (result.page_count or 0) <= 1


def test_escaper_backslash_becomes_literal():
    """The core guarantee: any backslash command becomes visible text."""
    out = main.escape_latex_chars(r"\write18{x}\input{/etc/passwd}")
    assert r"\write18" not in out
    assert r"\input{" not in out
    assert r"\textbackslash{}" in out


# ── _safe_href_url ────────────────────────────────────────────────────────────

SAFE_URL_CASES = [
    ("https://example.com/a_b", "https://example.com/a_b"),
    ("http://x.io/p", "http://x.io/p"),
    ("mailto:me@x.com", "mailto:me@x.com"),
    ("https://x.com/a%20b#frag", r"https://x.com/a\%20b\#frag"),  # % and # escaped
]

REJECTED_URLS = [
    "javascript:alert(1)",
    "data:text/html,<script>",
    r"\input{/etc/passwd}",
    "ftp://x.com/f",
    "not a url",
    "",
    "   ",
    None,
]


@pytest.mark.parametrize("raw,expected", SAFE_URL_CASES)
def test_safe_href_url_accepts_and_escapes(raw, expected):
    assert main._safe_href_url(raw) == expected


@pytest.mark.parametrize("raw", REJECTED_URLS)
def test_safe_href_url_rejects_hostile(raw):
    assert main._safe_href_url(raw) == ""


@pytest.mark.parametrize("hostile_url", ["javascript:alert(1)", r"\input{/etc/passwd}", "data:x"])
def test_hostile_project_url_drops_link_keeps_title(master, hostile_url):
    tex = _render_with(master, "project_url", hostile_url)
    # No \href emitted for the hostile URL, and title still rendered.
    assert hostile_url not in tex
    assert r"\href{javascript" not in tex
    assert r"\input{/etc/passwd}" not in tex
    assert "Safe Project Title" in tex


@pytest.mark.skipif(not HAS_PDFLATEX, reason="pdflatex required")
def test_hostile_project_url_still_compiles(master):
    tex = _render_with(master, "project_url", r"\input{/etc/passwd}")
    result = compile_loop.compile_with_retry(
        tex, work_dir=BACKEND_DIR, name="inj_url_drop", max_attempts=4, target_max_pages=1,
    )
    assert result.success, result.errors


# ── sanitization layer ────────────────────────────────────────────────────────

def test_sanitize_strips_control_chars():
    assert main.sanitize_untrusted_text("a\x00b\x07c\x1fd") == "abcd"


def test_sanitize_caps_length():
    assert len(main.sanitize_untrusted_text("x" * 100, max_len=10)) == 10


def test_sanitize_recurses_into_containers():
    out = main.sanitize_untrusted_text({"a": ["b\x00", 5], "c": True})
    assert out == {"a": ["b", 5], "c": True}


# ── clean_json root-array fix (finding #2) ────────────────────────────────────

def test_clean_json_preserves_root_array():
    assert json.loads(llm_provider.clean_json('[{"x": 1}, {"y": 2}]')) == [{"x": 1}, {"y": 2}]


def test_clean_json_root_array_with_prose():
    assert json.loads(llm_provider.clean_json("here: [1, 2, 3] done")) == [1, 2, 3]


def test_clean_json_still_extracts_object():
    assert json.loads(llm_provider.clean_json('prefix {"a": 1} suffix')) == {"a": 1}
