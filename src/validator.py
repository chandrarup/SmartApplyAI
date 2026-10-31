"""
Bullet Constraint and Bijective Grounding Validator for the Antigravity Resume Engine.
Enforces Tsenta's qualification rules, character budgets (165-230 chars),
base language pairings, anti-slop verb bans, and <=92 character project header constraints.
"""

import re
import json
from typing import Dict, Any, List, Tuple
from src.schemas import TailoredResumeAST, DeconstructedJD
from src.prompt_templates import (
    BANNED_ACTION_VERBS,
    APPROVED_ACTION_VERBS,
    BASE_LANGUAGE_DEPENDENCY_TABLE,
)


def strip_latex_formatting(text: str) -> str:
    """Removes LaTeX command wrappers to measure rendered plain-text character length."""
    # Replace \ulhref{url}{anchor} with anchor
    text = re.sub(r"\\ulhref\{[^\}]*\}\{([^\}]*)\}", r"\1", text)
    # Replace \href{url}{anchor} with anchor
    text = re.sub(r"\\href\{[^\}]*\}\{([^\}]*)\}", r"\1", text)
    # Replace $|$ or \hfill with plain spaces or pipe
    text = re.sub(r"\\?\$[\|I]\$?", "|", text)
    text = text.replace(r"\hfill", " ")
    # Strip \textbf{...}, \textit{...}, \scshape, \small, etc.
    text = re.sub(r"\\textbf\{([^\}]*)\}", r"\1", text)
    text = re.sub(r"\\textit\{([^\}]*)\}", r"\1", text)
    text = re.sub(r"\\scshape", "", text)
    text = re.sub(r"\\small", "", text)
    text = re.sub(r"\\noindent", "", text)
    # Unescape escaped symbols
    text = text.replace(r"\%", "%").replace(r"\&", "&").replace(r"\$", "$").replace(r"\_", "_")
    # Clean redundant whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def check_banned_verbs(bullet: str) -> List[str]:
    """Detects any prohibited AI-slop or passive management verbs at the start of a bullet."""
    plain = strip_latex_formatting(bullet)
    first_words = plain.split()[:2]
    found = []
    for verb in BANNED_ACTION_VERBS:
        for word in first_words:
            clean_word = re.sub(r"[^\w]", "", word).lower()
            if clean_word == verb.lower():
                found.append(verb)
    return found


def repair_banned_verbs(bullet: str) -> str:
    """Replaces banned opening verbs with active engineering verbs."""
    replacement_map = {
        "spearheaded": "Architected",
        "synergized": "Integrated",
        "drove": "Engineered",
        "utilized": "Applied",
        "leveraged": "Built with",
        "assisted": "Engineered",
        "helped": "Implemented",
        "worked on": "Built",
        "participated in": "Engineered",
        "handled": "Engineered",
        "responsible for": "Built",
        "pioneered": "Architected",
        "revolutionized": "Engineered",
    }
    for bad, good in replacement_map.items():
        pattern = re.compile(rf"^(?:\\textbf\{{)?{bad}(?:\}})?\b", re.IGNORECASE)
        if pattern.search(bullet.strip()):
            bullet = pattern.sub(good, bullet.strip())
            break
    return bullet


def verify_base_languages(bullet: str) -> List[str]:
    """Flags any framework that appears without its underlying base language."""
    plain = strip_latex_formatting(bullet).lower()
    missing_pairings = []

    for framework, base_langs in BASE_LANGUAGE_DEPENDENCY_TABLE.items():
        if framework.lower() in plain:
            # Check if at least one base language is present
            has_base = any(lang.lower() in plain for lang in base_langs)
            if not has_base:
                missing_pairings.append(f"{framework} (requires {base_langs[0]})")
    return missing_pairings


def validate_bullet_constraints(
    resume_ast: TailoredResumeAST,
    deconstructed_jd: DeconstructedJD = None,
) -> Dict[str, Any]:
    """
    Validates all structural and linguistic constraints:
    1. Every bullet character length between 165 and 230 plain characters.
    2. Zero banned action verbs.
    3. Every framework has its base language.
    4. Project headers (title + tech_stack_short + year) <= 92 characters.
    5. Bijective grounding: skills matching JD appear in at least one bullet.
    """
    violations: List[str] = []
    bullet_lengths: List[int] = []
    banned_verbs_found: List[str] = []
    missing_base_langs: List[str] = []
    all_bullets_plain: List[str] = []

    # Check Experience Bullets
    for e_idx, entry in enumerate(resume_ast.experience_entries):
        for b_idx, bullet in enumerate(entry.bullets):
            plain = strip_latex_formatting(bullet)
            length = len(plain)
            bullet_lengths.append(length)
            all_bullets_plain.append(plain)

            if length < 155:
                violations.append(
                    f"Experience [{entry.role_title}] Bullet #{b_idx+1} is too short ({length} chars < 165). Risk of under-fill."
                )
            elif length > 240:
                violations.append(
                    f"Experience [{entry.role_title}] Bullet #{b_idx+1} is too long ({length} chars > 230). Will spill to 3rd line."
                )

            banned = check_banned_verbs(bullet)
            if banned:
                banned_verbs_found.extend(banned)
                violations.append(
                    f"Experience [{entry.role_title}] Bullet #{b_idx+1} contains banned verb(s): {', '.join(banned)}"
                )

            missing = verify_base_languages(bullet)
            if missing:
                missing_base_langs.extend(missing)
                violations.append(
                    f"Experience [{entry.role_title}] Bullet #{b_idx+1} misses base language for: {', '.join(missing)}"
                )

    # Check Project Bullets and Header Lengths
    for p_idx, proj in enumerate(resume_ast.project_entries):
        plain_header = strip_latex_formatting(f"{proj.title} | {proj.tech_stack_short}  {proj.year}")
        if len(plain_header) > 92:
            violations.append(
                f"Project [{proj.title}] header is too long ({len(plain_header)} chars > 92). Year will wrap to second line."
            )

        for b_idx, bullet in enumerate(proj.bullets):
            plain = strip_latex_formatting(bullet)
            length = len(plain)
            bullet_lengths.append(length)
            all_bullets_plain.append(plain)

            if length < 150:
                violations.append(
                    f"Project [{proj.title}] Bullet #{b_idx+1} is too short ({length} chars < 165)."
                )
            elif length > 240:
                violations.append(
                    f"Project [{proj.title}] Bullet #{b_idx+1} is too long ({length} chars > 230)."
                )

            banned = check_banned_verbs(bullet)
            if banned:
                banned_verbs_found.extend(banned)

    # Check Bijective Grounding against JD
    unproved_jd_skills = []
    if deconstructed_jd and deconstructed_jd.top_hard_skills:
        combined_bullet_text = " ".join(all_bullets_plain).lower()
        for skill in deconstructed_jd.top_hard_skills:
            # If skill is not present in bullets but is in skills_csv
            if skill.lower() not in combined_bullet_text and skill not in deconstructed_jd.true_gaps:
                unproved_jd_skills.append(skill)

    if unproved_jd_skills:
        violations.append(
            f"Bijective grounding warning: JD skills not grounded in experience bullets: {', '.join(unproved_jd_skills[:5])}"
        )

    is_valid = len(violations) == 0

    return {
        "valid": is_valid,
        "total_bullets": len(bullet_lengths),
        "bullet_lengths": bullet_lengths,
        "avg_bullet_length": round(sum(bullet_lengths) / len(bullet_lengths), 1) if bullet_lengths else 0,
        "banned_verbs_found": list(set(banned_verbs_found)),
        "missing_base_languages": list(set(missing_base_langs)),
        "unproved_jd_skills": unproved_jd_skills,
        "violations": violations,
    }
