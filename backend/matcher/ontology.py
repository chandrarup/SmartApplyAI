"""Curated skills ontology + deterministic text→skills mapping (Phase-2 §3.1).

No LLM, no network. Pure regex over a hand-curated YAML taxonomy.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml


@dataclass(slots=True)
class Skill:
    id: str
    name: str
    synonyms: list[str] = field(default_factory=list)
    parents: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)
    weight: float = 1.0


# ── synonym → regex ──────────────────────────────────────────────────
# The contract prose says "\b-bounded literals", but a literal \b breaks on
# symbol synonyms ("c++", "c#", ".net": no word boundary after '+'/'#'). We use
# lookarounds instead (sanctioned: "use \b plus lookarounds"): identifier chars
# may not abut the match. Single-letter/collision-prone tokens get stricter
# context whitelists so "r" never fires inside "rust" / "R&D".
_AMBIGUOUS = {"r", "c", "go"}          # english-word / single-letter collisions
_MEASURE = {"ml", "cv"}                # collide with units ("100 ml") / "your CV"


def _ambiguous_pattern(tok: str) -> str:
    esc = re.escape(tok)
    tail = r"(?![A-Za-z0-9+#&])"       # not part of a larger token (blocks c++, rust, R&D)
    delim = rf"\b{esc}{tail}(?=\s*[,/;)|])"                         # "R," "C/" "Go)"
    desc = (rf"\b{esc}{tail}(?=\s+(?:programming|programmer|language|lang|"
            rf"developer|dev|coding|script))")                     # "R programming"
    pre = (r"(?:(?<=\bin )|(?<=\bwith )|(?<=\bvia )|(?<= and )|(?<= or )|"
           r"(?<=using )|(?<= both )|(?<=learn )|(?<= know )|(?<= knew ))")
    ctx = rf"{pre}{esc}{tail}\b"                                    # "in R" "using Go"
    return rf"(?:{delim}|{desc}|{ctx})"


def _synonym_pattern(syn: str) -> str | None:
    s = syn.strip().lower()
    if not s:
        return None
    if s in _AMBIGUOUS or len(s) == 1:
        return _ambiguous_pattern(s)
    lead = r"(?<![A-Za-z0-9+#.])"
    if s in _MEASURE:
        lead += r"(?<!\d )"            # skip "100 ml" but keep "ML/AI"
    return lead + re.escape(s) + r"(?![A-Za-z0-9+#])"


@lru_cache(maxsize=4096)
def _compile(synonyms: tuple[str, ...]) -> re.Pattern[str]:
    parts = [p for p in (_synonym_pattern(s) for s in synonyms) if p]
    if not parts:
        return re.compile(r"(?!x)x")   # matches nothing
    return re.compile("|".join(parts), re.IGNORECASE)


# ── loading ──────────────────────────────────────────────────────────
def _resolve(path: str | Path | None) -> str:
    if path is None:
        path = Path(__file__).with_name("skills_ontology.yaml")
    return str(Path(path).resolve())


@lru_cache(maxsize=None)
def _load_cached(resolved: str) -> dict[str, Skill]:
    data = yaml.safe_load(Path(resolved).read_text(encoding="utf-8")) or {}
    out: dict[str, Skill] = {}
    for raw in data.get("skills", []):
        sid = raw["id"]
        skill = Skill(
            id=sid,
            name=raw.get("name", sid),
            synonyms=list(raw.get("synonyms", []) or []),
            parents=list(raw.get("parents", []) or []),
            related=list(raw.get("related", []) or []),
            weight=float(raw.get("weight", 1.0)),
        )
        out[sid] = skill
        _compile(tuple(skill.synonyms))   # precompile one alternation per skill
    return out


def load_ontology(path: str | Path | None = None) -> dict[str, Skill]:
    return _load_cached(_resolve(path))


# ── text → skills ────────────────────────────────────────────────────
def map_text_to_skills(
    text: str,
    ontology: dict[str, Skill] | None = None,
    *,
    title: str = "",
) -> dict[str, float]:
    """{skill_id: weight} for every skill whose synonyms appear in `text`.

    weight = skill.weight * (1 + log1p(term_frequency)); ×2 if the skill also
    appears in `title`. `title` never introduces a skill on its own — a skill
    is included only when found in `text` (empty text → empty dict).
    """
    body = (text or "").lower()
    if not body:
        return {}
    head = (title or "").lower()
    onto = ontology if ontology is not None else load_ontology()

    out: dict[str, float] = {}
    for sid, skill in onto.items():
        pat = _compile(tuple(skill.synonyms))
        tf = sum(1 for _ in pat.finditer(body))
        if tf == 0:
            continue
        w = skill.weight * (1.0 + math.log1p(tf))
        if head and pat.search(head):
            w *= 2.0
        out[sid] = w
    return out


def match_strength(
    candidate_skills: dict[str, float],
    job_skill_id: str,
    ontology: dict[str, Skill],
) -> float:
    """1.0 exact · 0.7 parent-or-child of a candidate · 0.5 related · else 0.0."""
    if job_skill_id in candidate_skills:
        return 1.0
    job = ontology.get(job_skill_id)
    best = 0.0
    for cid in candidate_skills:
        cand = ontology.get(cid)
        if cand is None:
            continue
        if job_skill_id in cand.parents or (job is not None and cid in job.parents):
            best = max(best, 0.7)
        elif job_skill_id in cand.related or (job is not None and cid in job.related):
            best = max(best, 0.5)
    return best
