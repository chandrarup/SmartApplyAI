"""
Tailoring / PDF pipeline failure-class tests (TEST ONLY — no production fixes).

Targets: /analyze, /tailor-resume, /generate-pdf, clean_json(), escape_latex_chars().
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

import compile_loop  # noqa: E402
import main  # noqa: E402
import tailor_edits  # noqa: E402


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def master():
    return main._enrich_profile_with_resume_sources(main.load_pdata("default"))


@pytest.fixture
def has_pdflatex():
    return shutil.which("pdflatex") is not None


LATEX_METACHAR_BLOB = (
    r"100% $5 & C# dev _underscore_ {braces} \textbf{INJECT} ^caret ~tilde "
    "emoji 🚀 em—dash URL https://github.com/foo/bar_baz"
)


# ── 1. LaTeX injection / compile breakage ─────────────────────────────────────

@pytest.mark.parametrize(
    "field,value",
    [
        ("summary", LATEX_METACHAR_BLOB),
        ("bullet", LATEX_METACHAR_BLOB),
        ("project_title", LATEX_METACHAR_BLOB),
        ("project_url", "https://github.com/foo/bar_baz_repo"),
    ],
)
def test_latex_metacharacters_render_or_compile(master, has_pdflatex, field, value):
    """Inject metacharacters; assert template renders and PDF compiles when toolchain present."""
    m = json.loads(json.dumps(master))
    if field == "summary":
        m["summary"] = value
    elif field == "bullet" and m.get("experience"):
        m["experience"][0]["details"] = [value]
        m["experience"][0]["bullets"] = [value]
    elif field == "project_title" and m.get("projects"):
        m["projects"][0]["title"] = value
    elif field == "project_url" and m.get("projects"):
        m["projects"][0]["url"] = value
        m["projects"][0]["title"] = "Safe Title"

    tex = main._render_tex_from_master(m)
    hygiene = compile_loop.inspect_tex_hygiene(tex)
    assert hygiene["ok"], hygiene

    # Escaper now neutralizes the full TeX special set (see fix/latex-injection).
    if field == "summary":
        escaped = main.escape_latex_chars(value)
        assert r"\%" in escaped
        assert r"\$" in escaped
        assert r"\#" in escaped
        assert r"\_" in escaped
        # Backslash commands are now neutralized — no live \textbf survives.
        if r"\textbf" in value:
            assert r"\textbf{INJECT}" not in tex

    if not has_pdflatex:
        pytest.skip("pdflatex not installed")
    result = compile_loop.compile_with_retry(
        tex, work_dir=os.path.dirname(main.__file__), name=f"latex_{field}", max_attempts=4, target_max_pages=1,
    )
    assert result.success, result.errors
    assert (result.page_count or 0) <= 1


def test_escape_latex_chars_covers_documented_set():
    raw = "% $ & # _ { }"
    out = main.escape_latex_chars(raw)
    assert out == r"\% \$ \& \# \_ \{ \}"


def test_escape_latex_chars_escapes_backslash_caret_tilde():
    """Escaper is complete — backslash/caret/tilde are neutralized."""
    raw = r"\textbf{x} ^ ~"
    out = main.escape_latex_chars(raw)
    assert r"\textbackslash{}" in out
    assert r"\textasciicircum{}" in out
    assert r"\textasciitilde{}" in out
    assert r"\textbf{x}" not in out  # no live command remains


def test_project_url_is_filtered_in_template(master):
    """proj.url now flows through the url filter — underscores kept, no break."""
    m = json.loads(json.dumps(master))
    m["projects"][0]["url"] = "https://example.com/my_under_score"
    tex = main._render_tex_from_master(m)
    # Underscore is valid inside \href's argument, so it stays literal (not \_).
    assert "https://example.com/my_under_score" in tex
    assert r"\href{https://example.com/my_under_score}" in tex


@pytest.mark.skipif(not shutil.which("pdflatex"), reason="pdflatex required")
def test_generate_pdf_endpoint_with_hostile_summary(client, master):
    payload = {
        "tailored_summary": LATEX_METACHAR_BLOB,
        "tailored_skills": master.get("skills"),
        "selected_projects": [p.get("title") for p in (master.get("projects") or [])[:3]],
        "_role": "Test",
        "_company": "TestCo",
        "_edits": [],
    }
    r = client.post("/generate-pdf", json=payload, headers={"X-Profile-ID": "default"})
    # Hostile summary may trip preflight (400) or compile (500) — must not fail opaquely
    assert r.status_code in (200, 400, 500)
    if r.status_code == 500:
        assert "detail" in r.json()


# ── 2. Malformed LLM JSON (clean_json) ────────────────────────────────────────

CLEAN_JSON_GOOD = [
    ('```json\n{"a": 1}\n```', {"a": 1}),
    ('Here you go:\n{"a": 1}\nThanks!', {"a": 1}),
    ('{"a": 1} trailing prose', {"a": 1}),
    ('```json\n{"outer": {"inner": 1}}\n```\nextra', {"outer": {"inner": 1}}),
    ('[{"x": 1}]', [{"x": 1}]),  # root array preserved (first-opener selection)
]

CLEAN_JSON_BAD = [
    '{"a": 1,}',           # trailing comma
    '{"a":',                # truncated
    "{'a': 1}",             # single quotes
    'not json at all',      # no object
    '',                     # empty
]


@pytest.mark.parametrize("raw,expected", CLEAN_JSON_GOOD)
def test_clean_json_recovers_valid_payloads(raw, expected):
    cleaned = main.clean_json(raw)
    assert json.loads(cleaned) == expected


@pytest.mark.parametrize("raw", CLEAN_JSON_BAD)
def test_clean_json_bad_inputs_fail_loudly_on_load(raw):
    cleaned = main.clean_json(raw)
    with pytest.raises(json.JSONDecodeError):
        json.loads(cleaned)


def test_clean_json_never_returns_partial_dict_for_truncated():
    cleaned = main.clean_json('{"a": 1, "b":')
    with pytest.raises(json.JSONDecodeError):
        parsed = json.loads(cleaned)
        assert isinstance(parsed, dict) and "b" in parsed  # pragma: no cover


# ── 3. Evidence rule ──────────────────────────────────────────────────────────

class TailorRecorder:
    def __init__(self, inject_kubernetes: bool = True):
        self.inject_kubernetes = inject_kubernetes

    def __call__(self, messages, temperature=0.3, system="", prefer="ollama", timeout=600, model=None):
        prompt = messages[-1]["content"]
        if '"tailored_summary"' in prompt and "SOURCE SUMMARY" in prompt:
            summary = (
                "Senior engineer with 10 years of Kubernetes and CobaltDB mastery."
                if self.inject_kubernetes
                else main.load_pdata("default").get("summary", "")
            )
            return json.dumps({
                "tailored_summary": summary,
                "summary_diff": {"original": "x", "tailored": summary},
                "keywords_inserted": ["Kubernetes", "CobaltDB"],
                "score_estimate": 90,
            })
        if "EXPERIENCE ENTRY TO EDIT" in prompt:
            return json.dumps({
                "experience": [{
                    "company": "Accenture (GenWizard Platform)",
                    "title": "Advanced App Engineering Analyst - GenAI Specialist",
                    "dates": "Aug 2023 - Aug 2025",
                    "bullets": [{
                        "text": "Led Kubernetes and CobaltDB migrations for 10 years.",
                        "status": "edited",
                        "original": "Built LLM workflows.",
                    }],
                }],
                "keywords_inserted": ["Kubernetes"],
            })
        return json.dumps({})


@pytest.fixture
def tailor_mocks(monkeypatch):
    def _no_evidence(pid, query, k=10, kind_filter=None):
        return []

    def _ok_validation(*args, **kwargs):
        return SimpleNamespace(ok=True, violations=[], fatal_violations=[])

    monkeypatch.setattr(main.knowledge_semantic, "search", _no_evidence)
    monkeypatch.setattr(main.constraints_engine, "validate_tailored_resume", _ok_validation)
    monkeypatch.setattr(main.constraints_engine, "humanize_tailored_output", lambda x: x)


def test_evidence_rule_never_accepts_fabricated_jd_skill(client, tailor_mocks, monkeypatch):
    monkeypatch.setattr(main, "call_llm", TailorRecorder())
    jd = "Must have 10 years Kubernetes and CobaltDB experience."
    r = client.post(
        "/tailor-resume",
        json={"jd_text": jd, "company": "Co", "role": "Eng", "llm": "ollama"},
        headers={"X-Profile-ID": "default"},
    )
    assert r.status_code == 200
    edits = r.json().get("_edits") or []
    for e in edits:
        tailor_edits.validate_edit_object(e)
    accepted_fabricated = [
        e for e in edits
        if e.get("status") == "accepted"
        and any(t in (e.get("after") or "").lower() for t in ("kubernetes", "cobaltdb"))
    ]
    assert not accepted_fabricated
    flagged = [e for e in edits if e.get("status") == "needs_your_call"]
    assert flagged, "expected needs_your_call for ungrounded JD terms"


# ── 4. Project selection ──────────────────────────────────────────────────────

def test_project_selection_zero_jd_overlap(master):
    library = master.get("project_library") or master.get("projects") or []
    jd = "quantum florbnicate zylophage chromodynamics " * 20
    picked = main._rank_projects_for_jd(library, jd, n=3)
    assert len(picked) <= 3
    titles = [p.get("title") for p in picked]
    assert len(titles) == len(set(titles)), "duplicate project titles selected"


def test_project_selection_high_overlap_no_duplicates(master):
    library = master.get("project_library") or master.get("projects") or []
    corpus = []
    for p in library:
        corpus.extend(p.get("tech_stack") or [])
        corpus.append(p.get("title") or "")
    jd = " ".join(corpus) + " machine learning python deep learning LLM"
    picked = main._rank_projects_for_jd(library, jd, n=3)
    assert 1 <= len(picked) <= 3
    titles = [p.get("title") for p in picked]
    assert len(titles) == len(set(titles))


def test_tailor_resume_selected_projects_count(client, tailor_mocks, monkeypatch):
    monkeypatch.setattr(main, "call_llm", TailorRecorder(inject_kubernetes=False))
    r = client.post(
        "/tailor-resume",
        json={
            "jd_text": "Python machine learning LLM engineer",
            "company": "Co",
            "role": "ML",
            "llm": "ollama",
        },
        headers={"X-Profile-ID": "default"},
    )
    assert r.status_code == 200
    titles = r.json().get("selected_projects") or []
    assert len(titles) <= 3
    assert len(titles) == len(set(t.lower() for t in titles))


# ── 5. Length / overflow ──────────────────────────────────────────────────────

def test_preflight_flags_overlong_summary(master):
    long_summary = "word " * 120  # ~120 words
    payload = {
        "tailored_summary": long_summary,
        "experience": [],
        "tailored_skills": master.get("skills"),
    }
    pf = main.constraints_engine.preflight_tailored_resume(master, payload)
    fatal = [i for i in pf.get("issues", []) if i.get("severity") == "fatal"]
    assert any(i.get("kind") == "summary_length" for i in fatal)


def test_trim_skills_lists_drops_overflow(master):
    skills = json.loads(json.dumps(master.get("skills") or {}))
    for key in skills:
        if isinstance(skills[key], list):
            skills[key] = skills[key] + [f"ExtraSkill{i}" for i in range(20)]
    trimmed, removed = main._trim_skills_lists(skills, "python ml", max_per_category=8)
    for key, items in trimmed.items():
        if isinstance(items, list):
            assert len(items) <= 8
    assert removed


# ── 6. Diff integrity (accepted vs rejected edits) ───────────────────────────

def test_merge_respects_accepted_edits_only(master):
    orig_summary = master.get("summary", "")
    accepted_text = "ACCEPTED_SUMMARY_MARKER"
    rejected_text = "REJECTED_SUMMARY_MARKER"

    payload = {
        "tailored_summary": rejected_text,
        "summary_diff": {"tailored": rejected_text},
        "_edits": [
            {
                "section": "summary",
                "field": "summary",
                "before": orig_summary,
                "after": accepted_text,
                "reason": "test accept",
                "status": "accepted",
                "confidence": 0.9,
            },
            {
                "section": "summary",
                "field": "summary",
                "before": orig_summary,
                "after": rejected_text,
                "reason": "test reject",
                "status": "rejected",
                "confidence": 0.9,
            },
        ],
        "experience": [],
        "tailored_skills": master.get("skills"),
        "selected_projects": [],
    }
    filtered = main._filter_payload_to_accepted(master, payload)
    assert accepted_text in (filtered.get("tailored_summary") or "")
    assert rejected_text not in (filtered.get("tailored_summary") or "")

    merged = main._merge_tailored_into_master(master, filtered)
    tex = main._render_tex_from_master(merged)
    # Underscores are LaTeX-escaped in rendered output
    assert "ACCEPTED" in tex and "SUMMARY" in tex and "MARKER" in tex
    assert "REJECTED_SUMMARY_MARKER" not in tex
    assert merged.get("summary") == accepted_text


def test_experience_bullet_reject_keeps_original(master):
    exp0 = (master.get("experience") or [{}])[0]
    orig_bullet = (exp0.get("details") or exp0.get("bullets") or ["orig"])[0]
    payload = {
        "_edits": [
            {
                "section": "experience",
                "field": "experience.0.bullets.0",
                "before": orig_bullet,
                "after": "REJECTED_BULLET_MARKER",
                "reason": "test",
                "status": "rejected",
                "confidence": 0.8,
            },
        ],
        "experience": [{
            "company": exp0.get("company"),
            "bullets": [{"text": "REJECTED_BULLET_MARKER", "status": "edited", "original": orig_bullet}],
        }],
        "tailored_skills": master.get("skills"),
    }
    filtered = main._filter_payload_to_accepted(master, payload)
    merged = main._merge_tailored_into_master(master, filtered)
    bullets = (merged["experience"][0].get("details") or merged["experience"][0].get("bullets") or [])
    assert "REJECTED_BULLET_MARKER" not in bullets[0]
    assert orig_bullet[:40] in bullets[0] or bullets[0] == orig_bullet


# ── 7. Garbage in ─────────────────────────────────────────────────────────────

GARBAGE_JDS = {
    "empty": "",
    "whitespace": "   \n\t  ",
    "non_english": "机器学习工程师 自然语言处理 深度学习 北京 薪资面议",
    "privacy_policy": (
        "Privacy Policy. We collect personal information including name, email, and browsing data. "
        "By using this site you consent to cookies and third-party sharing. "
        "Contact privacy@example.com for GDPR requests."
    ),
}


@pytest.fixture
def analyze_recorder(monkeypatch):
    rec = []

    def _fake(messages, temperature=0.3, system="", prefer="ollama", timeout=600, model=None):
        rec.append(messages[-1]["content"])
        return json.dumps({
            "role": "Unknown",
            "skills_matched": [],
            "missing_skill": "n/a",
            "score": "0",
            "tailored_summary": "Fallback summary.",
            "selected_projects": [],
        })

    monkeypatch.setattr(main, "call_llm", _fake)
    return rec


@pytest.mark.parametrize("key", list(GARBAGE_JDS.keys()))
def test_analyze_garbage_jd_non_crashing(client, analyze_recorder, key):
    r = client.post(
        "/analyze",
        json={"jd_text": GARBAGE_JDS[key], "llm": "ollama"},
        headers={"X-Profile-ID": "default"},
    )
    assert r.status_code == 200
    data = r.json()
    assert "role" in data


@pytest.mark.parametrize("key", list(GARBAGE_JDS.keys()))
def test_tailor_resume_garbage_jd_non_crashing(client, tailor_mocks, monkeypatch, key):
    monkeypatch.setattr(main, "call_llm", TailorRecorder(inject_kubernetes=False))
    r = client.post(
        "/tailor-resume",
        json={"jd_text": GARBAGE_JDS[key], "company": "Co", "role": "R", "llm": "ollama"},
        headers={"X-Profile-ID": "default"},
    )
    assert r.status_code == 200
    assert "_edits" in r.json() or "tailored_summary" in r.json()
