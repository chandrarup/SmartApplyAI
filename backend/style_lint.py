"""Rule-based writing-style lint (config-driven) + targeted rewrite loop.

Detection uses style_lint.yaml — no LLM. Violations get one targeted LLM rewrite
per flagged sentence; re-lint; still failing → lint_flags on the edit/card.
"""

from __future__ import annotations

import copy
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import yaml

STYLE_LINT_PATH = Path(__file__).resolve().parent / "style_lint.yaml"

LlmCall = Callable[..., str]


@lru_cache(maxsize=2)
def load_style_lint(path: str | None = None) -> dict[str, Any]:
    p = Path(path) if path else STYLE_LINT_PATH
    if not p.is_file():
        return {"banned_phrases": [], "company_claim_patterns": []}
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {"banned_phrases": [], "company_claim_patterns": []}


def _compile_banned(cfg: dict[str, Any] | None = None) -> list[tuple[re.Pattern[str], str]]:
    cfg = cfg or load_style_lint()
    out: list[tuple[re.Pattern[str], str]] = []
    for entry in cfg.get("banned_phrases") or []:
        if isinstance(entry, str):
            pat, label = entry, entry
        elif isinstance(entry, dict):
            pat = str(entry.get("pattern") or "")
            label = str(entry.get("label") or pat)
        else:
            continue
        if not pat:
            continue
        try:
            # Plain literals (em-dash) vs regex
            if pat.startswith("(?") or any(c in pat for c in r".*+?[](){}^$|\\"):
                out.append((re.compile(pat), label))
            else:
                out.append((re.compile(re.escape(pat)), label))
        except re.error:
            out.append((re.compile(re.escape(pat)), label))
    return out


def find_style_violations(text: str, *, cfg: dict[str, Any] | None = None) -> list[dict[str, str]]:
    """Return list of {label, match, sentence} for banned patterns in text."""
    if not text:
        return []
    violations: list[dict[str, str]] = []
    for pattern, label in _compile_banned(cfg):
        for m in pattern.finditer(text):
            # Sentence containing the match
            start = text.rfind(".", 0, m.start()) + 1
            end = text.find(".", m.end())
            if end < 0:
                end = len(text)
            sentence = text[start : end + 1].strip() or m.group(0)
            violations.append({
                "label": label,
                "match": m.group(0),
                "sentence": sentence[:300],
            })
    return violations


def _rewrite_sentence(
    sentence: str,
    labels: list[str],
    *,
    llm_call: LlmCall,
    llm: str = "ollama",
) -> str:
    prompt = (
        "Rewrite the sentence below to remove banned resume clichés while keeping "
        "the same facts and meaning. Do not add new claims. Output ONLY the rewritten "
        f"sentence.\n\nBanned: {', '.join(labels)}\n\nSentence:\n{sentence}"
    )
    try:
        raw = llm_call([{"role": "user", "content": prompt}], temperature=0.2, prefer=llm)
        line = (raw or "").strip().splitlines()[0].strip().strip('"')
        return line or sentence
    except Exception:
        return sentence


def lint_and_rewrite_text(
    text: str,
    *,
    llm_call: LlmCall | None = None,
    llm: str = "ollama",
    max_rewrites: int = 3,
) -> tuple[str, list[dict[str, str]]]:
    """Detect → rewrite flagged sentences once each → re-lint.

    Returns (cleaned_text, remaining_flags). Remaining flags should surface on UI.
    """
    if not text:
        return text, []
    cfg = load_style_lint()
    violations = find_style_violations(text, cfg=cfg)
    if not violations or not llm_call:
        return text, [{"label": v["label"], "match": v["match"], "sentence": v["sentence"]} for v in violations]

    out = text
    rewritten = 0
    # Group by sentence to avoid double-rewriting
    by_sentence: dict[str, list[str]] = {}
    for v in violations:
        by_sentence.setdefault(v["sentence"], []).append(v["label"])

    for sentence, labels in list(by_sentence.items())[:max_rewrites]:
        if sentence not in out:
            continue
        new_sent = _rewrite_sentence(sentence, labels, llm_call=llm_call, llm=llm)
        if new_sent and new_sent != sentence:
            out = out.replace(sentence, new_sent, 1)
            rewritten += 1

    remaining = find_style_violations(out, cfg=cfg)
    flags = [{"label": v["label"], "match": v["match"], "sentence": v["sentence"]} for v in remaining]
    return out, flags


def lint_tailored_payload(
    tailored: dict[str, Any],
    *,
    llm_call: LlmCall | None = None,
    llm: str = "ollama",
) -> dict[str, Any]:
    """Lint summary + bullets in place; attach lint_flags on the payload."""
    out = copy.deepcopy(tailored)
    all_flags: list[dict[str, str]] = []

    summary = out.get("tailored_summary") or ""
    if summary:
        cleaned, flags = lint_and_rewrite_text(summary, llm_call=llm_call, llm=llm)
        out["tailored_summary"] = cleaned
        if isinstance(out.get("summary_diff"), dict):
            out["summary_diff"]["tailored"] = cleaned
        for f in flags:
            all_flags.append({**f, "field": "summary"})

    for e_idx, exp in enumerate(out.get("experience") or []):
        if not isinstance(exp, dict):
            continue
        for b_idx, bullet in enumerate(exp.get("bullets") or []):
            if not isinstance(bullet, dict):
                continue
            text = bullet.get("text") or ""
            cleaned, flags = lint_and_rewrite_text(text, llm_call=llm_call, llm=llm)
            bullet["text"] = cleaned
            for f in flags:
                all_flags.append({**f, "field": f"experience.{e_idx}.bullets.{b_idx}"})

    out["lint_flags"] = all_flags
    return out


def flag_cover_letter_company_claims(
    letter: str,
    company: str,
    *,
    cfg: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Flag specific company claims that should be phrased generically or verified."""
    if not letter or not (company or "").strip():
        return []
    cfg = cfg or load_style_lint()
    flags: list[dict[str, str]] = []
    # Always run banned-phrase lint on cover letters
    for v in find_style_violations(letter, cfg=cfg):
        flags.append({**v, "kind": "style"})
    company_esc = re.escape(company.strip())
    for raw in cfg.get("company_claim_patterns") or []:
        try:
            pat = re.compile(str(raw).replace("{company}", company_esc))
        except re.error:
            continue
        if pat.search(letter):
            flags.append({
                "kind": "company_claim",
                "label": "unverified company claim",
                "match": company,
                "sentence": "Phrase generically or verify — specific company claim without a source.",
            })
    return flags
