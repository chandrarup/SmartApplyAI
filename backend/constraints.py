"""
Constraint Engine — validates LLM-generated resume edits before they touch the AST.

Catches:
  * Fact invention (claims not backed by candidate evidence)
  * Word/character budget violations
  * Protected field modifications
  * Excessive keyword density
  * Unnatural language patterns
  * Metric fabrication

Returns ValidationResult with .ok and per-violation explanations.
"""
import re
from collections import Counter
from typing import Any


class Violation:
    def __init__(self, kind: str, severity: str, message: str, location: str = ""):
        self.kind = kind          # 'fact_invention' | 'word_budget' | 'protected' | 'density' | 'authenticity'
        self.severity = severity  # 'fatal' (reject edit) | 'warn' (allow with flag)
        self.message = message
        self.location = location

    def __repr__(self):
        return f"Violation({self.severity}: {self.kind} @ {self.location}: {self.message})"

    def to_dict(self):
        return {"kind": self.kind, "severity": self.severity, "message": self.message, "location": self.location}


class ValidationResult:
    def __init__(self):
        self.violations: list[Violation] = []

    def add(self, v: Violation):
        self.violations.append(v)

    @property
    def ok(self) -> bool:
        return not any(v.severity == "fatal" for v in self.violations)

    @property
    def fatal_violations(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == "fatal"]

    def __repr__(self):
        return f"ValidationResult(ok={self.ok}, {len(self.violations)} violations)"


def count_words(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text or ""))


# ── Authenticity patterns ──────────────────────────────────────────────────
SUSPICIOUS_PHRASES = [
    r"\bsynerg(y|ize|ized|ies)\b",
    r"\bleverag(e|ed|ing)\b.*\bsynerg",
    r"\bcutting[- ]edge\b",
    r"\benterprise[- ]grade\b.*\bsolutions?\b",
    r"\bworld[- ]class\b",
    r"\bnext[- ]generation\b",
    r"\brevolutionary\b",
    r"\bdisruptive\b.*\binnovation\b",
    r"\bbest[- ]in[- ]class\b",
    r"\bmission[- ]critical\b.*\bstakeholder\b",
    r"\bsea[- ]change\b",
    r"\bparadigm[- ]shift\b",
    r"\bgranular\b.*\bvisibility\b",
    r"\bholistic\b.*\bapproach\b",
    r"\bend[- ]to[- ]end\b.*\bsolution\b",
    r"\bresults[- ]driven\b",
    r"\bpassionate\b",
    r"\bdynamic\b.*\bprofessional\b",
    r"\bproven track record\b",
    r"\bspearheaded\b.*\btransform",
    r"\bthought leader\b",
    r"\bvalue[- ]add(ed)?\b",
    r"\butiliz(e|ing)\b.*\bcutting",
    r"\bexcited to\b",
    r"\bthrilled to\b",
]

# Strip or soften common LLM resume clichés (keep sentence readable)
HUMANIZE_REPLACEMENTS = [
    (r"\bcutting[- ]edge\b", ""),
    (r"\bworld[- ]class\b", ""),
    (r"\benterprise[- ]grade\b", "production"),
    (r"\bsynerg(y|ize|ized|ies)\b", "collaboration"),
    (r"\bleverag(e|d|ing)\b", "used"),
    (r"\butiliz(e|d|ing)\b", "used"),
    (r"\bresults[- ]driven\b", ""),
    (r"\bpassionate\b", ""),
    (r"\bproven track record of\b", ""),
    (r"\bspearheaded\b", "Led"),
    (r"\s{2,}", " "),
]


def humanize_text(text: str) -> str:
    """Remove obvious AI-resume clichés while preserving facts."""
    if not text:
        return text
    out = text
    for pattern, repl in HUMANIZE_REPLACEMENTS:
        out = re.sub(pattern, repl, out, flags=re.IGNORECASE)
    return re.sub(r"\s+([,.;])", r"\1", out).strip()


def humanize_tailored_output(tailored: dict) -> dict:
    """Post-process LLM output for natural, human tone."""
    import copy
    out = copy.deepcopy(tailored)
    if out.get("tailored_summary"):
        out["tailored_summary"] = humanize_text(out["tailored_summary"])
        if out.get("summary_diff"):
            out["summary_diff"]["tailored"] = out["tailored_summary"]
    for exp in out.get("experience") or []:
        for b in exp.get("bullets") or []:
            if isinstance(b, dict) and b.get("text"):
                b["text"] = humanize_text(b["text"])
    return out


def preflight_tailored_resume(
    candidate_profile: dict,
    tailored_output: dict,
) -> dict:
    """Pre-PDF checks: overlap, length, formatting risks. Returns {ok, issues[]}."""
    issues: list[dict] = []

    summary = (
        tailored_output.get("tailored_summary")
        or (tailored_output.get("summary_diff") or {}).get("tailored")
        or ""
    )
    orig_summary = candidate_profile.get("summary", "")
    sw = count_words(summary)
    if sw > 85:
        issues.append({
            "severity": "fatal",
            "kind": "summary_length",
            "message": f"Summary is {sw} words (max ~80). Shorten before PDF.",
        })
    elif sw > 75:
        issues.append({
            "severity": "warn",
            "kind": "summary_length",
            "message": f"Summary is {sw} words — may crowd the page.",
        })

    for v in detect_authenticity_issues(summary):
        issues.append({
            "severity": v.severity,
            "kind": v.kind,
            "message": v.message,
            "location": "summary",
        })

    seen_bullets: set[str] = set()
    total_bullet_growth = 0
    for exp in tailored_output.get("experience") or []:
        for b in exp.get("bullets") or []:
            text = (b.get("text") if isinstance(b, dict) else str(b)) or ""
            if not text.strip():
                issues.append({
                    "severity": "fatal",
                    "kind": "empty_bullet",
                    "message": "An experience bullet is empty.",
                    "location": "experience",
                })
                continue
            norm = re.sub(r"\s+", " ", text.lower().strip())[:100]
            if norm in seen_bullets:
                issues.append({
                    "severity": "warn",
                    "kind": "duplicate_bullet",
                    "message": "Duplicate or near-duplicate bullet text.",
                    "location": "experience",
                })
            seen_bullets.add(norm)

            if isinstance(b, dict) and b.get("status") == "edited" and b.get("original"):
                ow = count_words(b["original"])
                nw = count_words(text)
                growth = nw - ow
                total_bullet_growth += max(0, growth)
                if growth > 12:
                    issues.append({
                        "severity": "warn",
                        "kind": "bullet_growth",
                        "message": f"Bullet grew by {growth} words — may cause page overflow.",
                        "location": "experience",
                    })
            for v in detect_authenticity_issues(text):
                issues.append({
                    "severity": v.severity,
                    "kind": v.kind,
                    "message": v.message,
                    "location": "experience",
                })

    if total_bullet_growth > 25:
        issues.append({
            "severity": "warn",
            "kind": "page_overflow",
            "message": f"Experience section grew ~{total_bullet_growth} words total — 1-page PDF at risk.",
        })

    # Skills line length (LaTeX single-line categories)
    for key, items in (tailored_output.get("tailored_skills") or {}).items():
        if not isinstance(items, list):
            continue
        line = ", ".join(str(s) for s in items)
        if len(line) > 130:
            issues.append({
                "severity": "warn",
                "kind": "skills_overflow",
                "message": f"Skills line '{key}' is very long ({len(line)} chars) — may wrap or overflow.",
                "location": f"skills.{key}",
            })

    # Summary ↔ bullet keyword overlap (stuffing)
    if summary and tailored_output.get("experience"):
        sum_tokens = set(re.findall(r"\b[a-z]{5,}\b", summary.lower()))
        for exp in tailored_output["experience"]:
            for b in exp.get("bullets") or []:
                bt = (b.get("text") if isinstance(b, dict) else str(b)) or ""
                overlap = sum(1 for t in sum_tokens if t in bt.lower())
                if overlap >= 6:
                    issues.append({
                        "severity": "warn",
                        "kind": "keyword_stuffing",
                        "message": "Summary and bullets repeat many of the same keywords.",
                        "location": "summary+experience",
                    })
                    break

    ok = not any(i["severity"] == "fatal" for i in issues)
    return {"ok": ok, "issues": issues}


# Preflight issue kinds that specifically threaten the one-page budget.
PAGE_FIT_KINDS = {"page_overflow", "bullet_growth", "skills_overflow", "summary_length"}


def page_fit_summary(preflight: dict | None) -> dict:
    """Collapse a preflight result into a single one-page-fit verdict for the queue UI.

    FINDINGS_tailoring §5: page overflow used to surface only as a silent PDF-compile
    warning. This lifts it to a hard, visible flag on the queue item so the reviewer
    sees "won't fit one page" before approving, not after the PDF is built.
    """
    issues = (preflight or {}).get("issues") or []
    reasons = [
        i.get("message", "")
        for i in issues
        if i.get("kind") in PAGE_FIT_KINDS
    ]
    return {"wont_fit_one_page": bool(reasons), "reasons": reasons}


def detect_authenticity_issues(text: str) -> list[Violation]:
    """Flag AI-prose tells: corporate buzzwords, padded language."""
    violations = []
    for pattern in SUSPICIOUS_PHRASES:
        if re.search(pattern, text, re.IGNORECASE):
            violations.append(Violation(
                "authenticity",
                "warn",
                f"AI-prose pattern detected: '{pattern}'",
                location=text[:60],
            ))
    return violations


def detect_metric_fabrication(text: str, evidence_text: str) -> list[Violation]:
    """Find numeric claims in text that don't appear in evidence (candidate's source data)."""
    violations = []
    # Find percentages and large numbers in the new text
    claims = re.findall(r"\b(\d{2,}(?:[\.,]\d+)?%?)\b", text)
    evidence_numbers = set(re.findall(r"\b(\d{2,}(?:[\.,]\d+)?%?)\b", evidence_text or ""))
    for claim in claims:
        # 1-2 digit numbers are common in dates/years, allow them
        try:
            n = float(claim.rstrip("%").replace(",", ""))
        except ValueError:
            continue
        if n < 10 or n > 9999:
            continue
        if claim not in evidence_numbers:
            violations.append(Violation(
                "fact_invention",
                "fatal",
                f"Numeric claim '{claim}' not found in candidate evidence",
                location=text[:60],
            ))
    return violations


def check_keyword_density(text: str, max_per_keyword: int = 5) -> list[Violation]:
    """Flag keyword stuffing — same noun repeated too often."""
    violations = []
    words = re.findall(r"\b[A-Za-z]{4,}\b", text)
    counter = Counter(w.lower() for w in words)
    for word, count in counter.items():
        # Skip common English words
        if word in {"with", "using", "from", "this", "that", "have", "will", "been",
                    "more", "into", "across", "through", "their", "than", "such"}:
            continue
        if count > max_per_keyword:
            violations.append(Violation(
                "density",
                "warn",
                f"Keyword stuffing risk: '{word}' appears {count} times",
                location="",
            ))
    return violations


def check_protected_fields(original: dict, modified: dict, protected_keys: list[str]) -> list[Violation]:
    """Compare nested keys in original vs modified. Fail if any protected key changed."""
    violations = []

    def get_path(d, path):
        for p in path.split("."):
            if isinstance(d, dict):
                d = d.get(p)
            elif isinstance(d, list) and p.isdigit():
                idx = int(p)
                d = d[idx] if idx < len(d) else None
            else:
                return None
        return d

    for key in protected_keys:
        a = get_path(original, key)
        b = get_path(modified, key)
        if a != b:
            violations.append(Violation(
                "protected",
                "fatal",
                f"Protected field '{key}' was modified: {repr(a)[:40]} -> {repr(b)[:40]}",
                location=key,
            ))
    return violations


def check_word_budget(original: str, modified: str, max_delta_pct: float = 1.40) -> list[Violation]:
    """Fail if modified is more than max_delta_pct times the original word count."""
    violations = []
    o_words = count_words(original)
    m_words = count_words(modified)
    if o_words == 0:
        return violations
    ratio = m_words / o_words
    if ratio > max_delta_pct:
        violations.append(Violation(
            "word_budget",
            "warn",
            f"Word count grew {ratio:.1%} (from {o_words} to {m_words}, max {max_delta_pct:.0%})",
            location=original[:60],
        ))
    return violations


def check_style_drift(original_bullets: list[str], modified_bullets: list[str]) -> list[Violation]:
    """Compare style metrics: median word count, verb-start ratio."""
    violations = []
    if not original_bullets or not modified_bullets:
        return violations

    def style_profile(bullets):
        counts = [count_words(b) for b in bullets if b]
        if not counts: return None
        verb_starts = sum(1 for b in bullets if re.match(r"^\s*[A-Z][a-z]+(ed|ing)?\b", b or ""))
        return {
            "median_words": sorted(counts)[len(counts)//2],
            "max_words": max(counts),
            "verb_start_pct": verb_starts / len(bullets),
        }

    orig = style_profile(original_bullets)
    mod = style_profile(modified_bullets)
    if not orig or not mod:
        return violations

    if mod["max_words"] > orig["max_words"] + 12:
        violations.append(Violation(
            "style_drift",
            "warn",
            f"Longest bullet grew from {orig['max_words']} to {mod['max_words']} words",
            location="bullets",
        ))
    if abs(mod["median_words"] - orig["median_words"]) > 8:
        violations.append(Violation(
            "style_drift",
            "warn",
            f"Median bullet length drifted from {orig['median_words']} to {mod['median_words']} words",
            location="bullets",
        ))
    return violations


# ── Main entry point ───────────────────────────────────────────────────────
def validate_tailored_resume(
    candidate_profile: dict,
    tailored_output: dict,
    evidence_text: str = "",
) -> ValidationResult:
    """Top-level validation. Catches all major problems.

    candidate_profile: the master_data.json
    tailored_output: the response from /tailor-resume (has experience, summary, etc.)
    evidence_text: concatenated source text from the candidate (bullets, summary, etc.)
    """
    result = ValidationResult()

    # 1. Protected fields — companies, titles, dates must NOT change
    if "experience" in tailored_output:
        for i, te in enumerate(tailored_output.get("experience", [])):
            # Find matching original by company name
            orig_match = None
            for oe in candidate_profile.get("experience", []):
                if oe.get("company", "").lower().strip() == te.get("company", "").lower().strip():
                    orig_match = oe
                    break
            if not orig_match:
                # Tailor introduced a company that doesn't exist in profile = fact invention
                result.add(Violation(
                    "fact_invention", "fatal",
                    f"Experience entry references unknown company: '{te.get('company','')}'",
                    location=f"experience[{i}]",
                ))
                continue

            # Tailored fields are: company, title, dates, bullets
            # Profile has: company, role, duration
            # The tailor-resume response uses different field names but same semantics
            t_title = te.get("title", "")
            o_title = orig_match.get("role") or orig_match.get("title", "")
            # Allow minor variations (the tailor may slightly tweak title casing)
            if t_title and o_title and t_title.lower().strip() != o_title.lower().strip():
                # Check if it's just casing/punctuation
                if re.sub(r"[^\w]", "", t_title.lower()) != re.sub(r"[^\w]", "", o_title.lower()):
                    result.add(Violation(
                        "protected", "fatal",
                        f"Job title changed: '{o_title}' → '{t_title}'",
                        location=f"experience[{i}].title",
                    ))

            t_dates = te.get("dates", "")
            o_dates = orig_match.get("duration") or f"{orig_match.get('start_date','')} - {orig_match.get('end_date','')}"
            if t_dates and o_dates and t_dates.strip() != o_dates.strip():
                # Allow trivial whitespace/dash variations
                if re.sub(r"[\s\-–—]+", "", t_dates) != re.sub(r"[\s\-–—]+", "", o_dates):
                    result.add(Violation(
                        "protected", "fatal",
                        f"Dates changed: '{o_dates}' → '{t_dates}'",
                        location=f"experience[{i}].dates",
                    ))

            # 2. Validate bullets — fact invention, style, word budget
            tailored_bullets_text = []
            for b in te.get("bullets", []):
                if isinstance(b, dict):
                    tailored_bullets_text.append(b.get("text", ""))
                else:
                    tailored_bullets_text.append(str(b))

            original_bullets = orig_match.get("details") or orig_match.get("bullets") or []
            evidence_for_role = " ".join(original_bullets) + " " + evidence_text

            # Per-bullet checks
            for j, b_text in enumerate(tailored_bullets_text):
                if not b_text: continue
                # Authenticity (warn only)
                for v in detect_authenticity_issues(b_text):
                    v.location = f"experience[{i}].bullets[{j}]"
                    result.add(v)
                # Metric fabrication (fatal)
                for v in detect_metric_fabrication(b_text, evidence_for_role):
                    v.location = f"experience[{i}].bullets[{j}]"
                    result.add(v)
                # Word budget per bullet
                if count_words(b_text) > 35:
                    result.add(Violation(
                        "word_budget", "warn",
                        f"Bullet has {count_words(b_text)} words (recommended max 30)",
                        location=f"experience[{i}].bullets[{j}]",
                    ))

            # Whole-role style drift
            for v in check_style_drift(original_bullets, tailored_bullets_text):
                v.location = f"experience[{i}]"
                result.add(v)

    # 3. Summary checks
    summary_text = tailored_output.get("tailored_summary") or tailored_output.get("summary_diff", {}).get("tailored", "")
    if summary_text:
        original_summary = candidate_profile.get("summary", "")
        # Authenticity
        for v in detect_authenticity_issues(summary_text):
            v.location = "summary"
            result.add(v)
        # Metric fabrication — summary should only reference candidate's actual numbers
        # Concatenate evidence: original summary + all bullets
        all_evidence = original_summary + " " + " ".join(
            (b for e in candidate_profile.get("experience", [])
             for b in (e.get("details") or e.get("bullets") or []))
        )
        for v in detect_metric_fabrication(summary_text, all_evidence):
            v.location = "summary"
            result.add(v)
        # Word budget
        for v in check_word_budget(original_summary, summary_text, max_delta_pct=1.6):
            v.location = "summary"
            result.add(v)
        # Keyword density
        for v in check_keyword_density(summary_text):
            v.location = "summary"
            result.add(v)

    return result


def auto_repair(tailored_output: dict, validation: ValidationResult,
                candidate_profile: dict) -> tuple[dict, list[str]]:
    """Attempt to fix fatal violations by reverting offending fields to originals.

    Returns (repaired_output, list_of_repair_actions).
    """
    actions = []
    repaired = json.loads(json.dumps(tailored_output))  # deep copy

    for v in validation.fatal_violations:
        if v.kind == "fact_invention" and v.location.startswith("experience["):
            # Strip the offending bullet
            try:
                m = re.match(r"experience\[(\d+)\]\.bullets\[(\d+)\]", v.location)
                if m:
                    i, j = int(m.group(1)), int(m.group(2))
                    # Replace with original bullet if we can find one
                    orig_exp = candidate_profile.get("experience", [])[i] if i < len(candidate_profile.get("experience", [])) else None
                    if orig_exp:
                        orig_bullets = orig_exp.get("details") or orig_exp.get("bullets") or []
                        if j < len(orig_bullets):
                            if i < len(repaired.get("experience", [])):
                                bullets = repaired["experience"][i].get("bullets", [])
                                if j < len(bullets):
                                    bullets[j] = {"text": orig_bullets[j], "status": "unchanged"}
                                    actions.append(f"Reverted experience[{i}].bullets[{j}] to original")
            except (ValueError, IndexError, KeyError):
                pass

        elif v.kind == "protected" and v.location.startswith("experience["):
            # Revert the protected field to its original
            try:
                m = re.match(r"experience\[(\d+)\]\.(\w+)", v.location)
                if m:
                    i, field = int(m.group(1)), m.group(2)
                    if i < len(candidate_profile.get("experience", [])):
                        orig = candidate_profile["experience"][i]
                        if i < len(repaired.get("experience", [])):
                            if field == "title":
                                repaired["experience"][i]["title"] = orig.get("role") or orig.get("title", "")
                            elif field == "dates":
                                repaired["experience"][i]["dates"] = orig.get("duration", "")
                            actions.append(f"Reverted experience[{i}].{field} to original")
            except (ValueError, IndexError, KeyError):
                pass

    return repaired, actions


import json  # at bottom to avoid shadowing earlier
