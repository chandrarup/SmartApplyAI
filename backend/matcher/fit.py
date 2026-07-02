"""Stage 3 LLM fit scoring over reranked candidates."""

from __future__ import annotations

import json
from typing import Any

from backend.knowledge import store as knowledge_store

from .llm import call_llm, clean_json


def _profile_snapshot(profile: dict[str, Any]) -> str:
    parts: list[str] = []
    summary = profile.get("summary")
    if isinstance(summary, str):
        parts.append(f"Summary: {summary}")
    for exp in profile.get("experience", [])[:2]:
        if not isinstance(exp, dict):
            continue
        role = exp.get("role") or ""
        company = exp.get("company") or ""
        details = exp.get("details") or exp.get("bullets") or []
        details_txt = " ".join(details[:3]) if isinstance(details, list) else str(details)
        parts.append(f"Experience: {role} @ {company} | {details_txt}")
    for proj in profile.get("projects", [])[:4]:
        if not isinstance(proj, dict):
            continue
        parts.append(
            "Project: {title} | {stack} | {desc}".format(
                title=proj.get("title", ""),
                stack=", ".join(proj.get("tech_stack", [])[:6]) if isinstance(proj.get("tech_stack"), list) else "",
                desc=proj.get("description", ""),
            )
        )
    return "\n".join([p for p in parts if p])[:7000]


def _fit_prompt(job: dict[str, Any], profile_text: str) -> str:
    jd = (job.get("description_text") or "")[:10000]
    title = job.get("title") or ""
    company = job.get("company") or ""
    return (
        "You are ranking job fit for a candidate. Return strict JSON only.\n"
        "Schema:\n"
        "{"
        '"match_pct": number,'
        '"matched_skills":[{"skill":"...", "evidence_ref":"..."}],'
        '"missing_skills":[{"skill":"...", "evidence_ref":"..."}],'
        '"best_projects":[{"title":"...", "why":"..."}],'
        '"rationale":"one line"'
        "}\n"
        "Rules: match_pct must be 0-100 integer-like; keep arrays concise; no markdown.\n\n"
        f"Job Title: {title}\n"
        f"Company: {company}\n"
        f"Job Description:\n{jd}\n\n"
        f"Candidate Profile:\n{profile_text}\n"
    )


def _fallback_fit() -> dict[str, Any]:
    return {
        "match_pct": 0,
        "matched_skills": [],
        "missing_skills": [],
        "best_projects": [],
        "rationale": "Fit parsing failed",
    }


def fit_candidates(
    profile_id: str,
    reranked: list[dict[str, Any]],
    top_fit: int = 30,
    llm_prefer: str = "ollama",
) -> list[dict[str, Any]]:
    if not reranked:
        return []

    profile = knowledge_store.get_profile(profile_id)
    profile_text = _profile_snapshot(profile)
    selected = reranked[:top_fit]

    for item in selected:
        prompt = _fit_prompt(item["job"], profile_text)
        messages = [
            {"role": "user", "content": prompt},
        ]
        raw = call_llm(messages=messages, temperature=0.2, prefer=llm_prefer)
        try:
            fit_obj = json.loads(clean_json(raw))
            if not isinstance(fit_obj, dict):
                fit_obj = _fallback_fit()
        except Exception:  # noqa: BLE001
            fit_obj = _fallback_fit()
        item["fit"] = fit_obj
        item["match_pct"] = int(float(fit_obj.get("match_pct", 0) or 0))

    print(f"[stage3] fit_scored={len(selected)}")
    return selected

