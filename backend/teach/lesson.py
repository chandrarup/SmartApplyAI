"""Skill lesson generation with KB-aware proficiency gating."""

from __future__ import annotations

import os
from datetime import date
from typing import Callable

from knowledge import rating

DEFAULT_GAPS_FILE = os.path.join(os.path.dirname(__file__), "gaps.yaml")


def load_gap_skills(path: str = DEFAULT_GAPS_FILE) -> list[str]:
    """Read simple YAML list under `skills:` without external deps."""
    skills: list[str] = []
    in_skills_block = False
    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line == "skills:":
                in_skills_block = True
                continue
            if in_skills_block and line.startswith("- "):
                val = line[2:].strip().strip('"').strip("'")
                if val:
                    skills.append(val)
            elif in_skills_block and not line.startswith("- "):
                break
    return skills


def _interview_line_only(skill: str, llm_callable: Callable | None, llm_prefer: str) -> str:
    if not llm_callable:
        return f"I can explain {skill} clearly and apply it in production systems."
    prompt = (
        "Write one interview-ready sentence (max 25 words) about this skill, "
        "first person, concrete, no fluff.\n"
        f"Skill: {skill}"
    )
    return llm_callable([{"role": "user", "content": prompt}], temperature=0.2, prefer=llm_prefer).strip()


def lesson(
    skill: str,
    pid: str = "default",
    llm_callable: Callable | None = None,
    llm_prefer: str = "ollama",
) -> dict:
    """
    Generate a lesson block with fixed section order.

    If skill proficiency is already >= 4, returns only interview line output.
    """
    clean_skill = (skill or "").strip()
    if not clean_skill:
        raise ValueError("skill is required")

    proficiency = rating.get_proficiency(pid, clean_skill)
    if proficiency is not None and proficiency >= 4:
        interview_line = _interview_line_only(clean_skill, llm_callable, llm_prefer)
        return {
            "skill": clean_skill,
            "proficiency": proficiency,
            "short_circuit": True,
            "lesson": f"You know this already. Interview line only:\n{interview_line}",
        }

    if not llm_callable:
        # Deterministic fallback if LLM is unavailable.
        fallback = (
            "1. intuition\n"
            f"{clean_skill} helps you model complex behavior with a structured abstraction.\n\n"
            "2. everyday analogy\n"
            f"Think of {clean_skill} like organizing a messy toolbox into labeled drawers.\n\n"
            "3. biomedical/spatial-omics analogy\n"
            f"In spatial-omics, {clean_skill} is like mapping cell neighborhoods over time.\n\n"
            "4. depth\n"
            "Each component exists to reduce ambiguity, improve retrieval quality, and make decisions "
            "more robust under noisy real-world inputs.\n\n"
            "5. one interview-ready sentence\n"
            f"I use {clean_skill} to turn unstructured context into reliable, explainable decisions."
        )
        return {
            "skill": clean_skill,
            "proficiency": proficiency,
            "short_circuit": False,
            "lesson": fallback,
        }

    today = date.today().isoformat()
    prompt = f"""
Create a teaching block for this skill: {clean_skill}
Audience: AI/ML engineer preparing for interviews.
Date: {today}

Return plain text with EXACTLY these 5 sections in this order:
1. intuition
2. everyday analogy
3. biomedical/spatial-omics analogy
4. depth (why each component exists, not just what)
5. one interview-ready sentence

Requirements:
- Clear, concise, practical.
- Do not include markdown bullets or extra sections.
- Keep total length under 350 words.
""".strip()

    try:
        text = llm_callable(
            [{"role": "user", "content": prompt}],
            temperature=0.25,
            prefer=llm_prefer,
        ).strip()
    except Exception:
        text = (
            "1. intuition\n"
            f"{clean_skill} models how information should be represented and reused over time so your system can reason instead of keyword-match.\n\n"
            "2. everyday analogy\n"
            f"Think of {clean_skill} like a smart notebook where each note links to older notes and updates itself when facts change.\n\n"
            "3. biomedical/spatial-omics analogy\n"
            f"In spatial-omics, {clean_skill} is like connecting each cell-state snapshot to prior tissue context so trajectory and neighborhood signals stay queryable.\n\n"
            "4. depth (why each component exists, not just what)\n"
            "Schema/typing prevents ambiguous entities, temporal edges preserve evolution, and retrieval/ranking components ensure you fetch the right prior fact at decision time.\n\n"
            "5. one interview-ready sentence\n"
            f"I use {clean_skill} to retain evolving context with traceable links, so downstream decisions stay accurate as new evidence arrives."
        )

    return {
        "skill": clean_skill,
        "proficiency": proficiency,
        "short_circuit": False,
        "lesson": text,
    }
