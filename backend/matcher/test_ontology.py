"""Tests for the Phase-2 skills ontology (contract §3.1)."""

from __future__ import annotations

import pytest

try:
    from backend.matcher import ontology
    from backend.matcher.ontology import Skill, load_ontology, map_text_to_skills, match_strength
except ImportError:  # pragma: no cover - path fallback matches sibling modules
    from matcher import ontology  # type: ignore
    from matcher.ontology import Skill, load_ontology, map_text_to_skills, match_strength  # type: ignore


# ── tiny inline ontology (do NOT mutate the lru_cached real one) ──────
def _mini() -> dict[str, Skill]:
    return {
        "skill:ai": Skill("skill:ai", "AI", ["artificial intelligence"], [], ["skill:ml"]),
        "skill:ml": Skill("skill:ml", "ML", ["machine learning", "ml"], ["skill:ai"],
                          ["skill:dl", "skill:python"]),
        "skill:dl": Skill("skill:dl", "DL", ["deep learning", "dl"], ["skill:ml"],
                          ["skill:pytorch"]),
        "skill:pytorch": Skill("skill:pytorch", "PyTorch", ["pytorch", "torch"], ["skill:dl"], []),
        "skill:python": Skill("skill:python", "Python", ["python"], [], []),
        "skill:r": Skill("skill:r", "R", ["r"], [], []),
    }


# ── YAML load + referential integrity ─────────────────────────────────
def test_yaml_loads_and_is_nonempty():
    onto = load_ontology()
    assert len(onto) >= 120
    assert all(isinstance(s, Skill) for s in onto.values())
    assert "skill:pytorch" in onto


def test_every_parent_and_related_id_exists():
    onto = load_ontology()
    ids = set(onto)
    for sid, skill in onto.items():
        assert sid.startswith("skill:")
        for ref in (*skill.parents, *skill.related):
            assert ref in ids, f"{sid} references undefined id {ref!r}"
        assert sid not in skill.parents, f"{sid} lists itself as parent"
        assert sid not in skill.related, f"{sid} lists itself as related"


def test_ids_are_unique_in_file():
    onto = load_ontology()
    # load_ontology dedupes by dict; assert the file itself has no dup ids
    import yaml
    from pathlib import Path
    raw = yaml.safe_load(Path(ontology.__file__).with_name("skills_ontology.yaml").read_text())
    ids = [s["id"] for s in raw["skills"]]
    assert len(ids) == len(set(ids))


def test_load_ontology_is_cached_by_path():
    assert load_ontology() is load_ontology()


# ── synonym matching + word boundaries ────────────────────────────────
def test_basic_synonym_match():
    onto = _mini()
    got = map_text_to_skills("We build with PyTorch and Python.", onto)
    assert "skill:pytorch" in got
    assert "skill:python" in got


def test_word_boundary_no_substring_false_positive():
    onto = _mini()
    # "torch" must not fire inside "pytorch"; "python" not inside "pythonic-ish" word
    got = map_text_to_skills("torchbearer led the pythonista parade", onto)
    assert got == {}


def test_single_letter_r_not_matched_inside_rust():
    onto = _mini()
    assert map_text_to_skills("experience with Rust and Go", onto) == {}
    assert map_text_to_skills("our R&D lab", onto) == {}


def test_single_letter_r_matched_in_language_context():
    onto = _mini()
    assert "skill:r" in map_text_to_skills("proficient in R, Python and SQL", onto)
    assert "skill:r" in map_text_to_skills("R/Python scientific stack", onto)


def test_empty_text_returns_empty_dict():
    assert map_text_to_skills("", _mini()) == {}
    assert map_text_to_skills("   ", _mini()) == {}


# ── weighting: term frequency + title boost ───────────────────────────
def test_term_frequency_increases_weight():
    onto = _mini()
    once = map_text_to_skills("python", onto)["skill:python"]
    twice = map_text_to_skills("python and more python", onto)["skill:python"]
    assert twice > once


def test_title_boost_doubles_weight():
    onto = _mini()
    base = map_text_to_skills("we use python daily", onto)["skill:python"]
    boosted = map_text_to_skills("we use python daily", onto, title="Python Engineer")["skill:python"]
    assert boosted == pytest.approx(base * 2.0)


def test_title_alone_never_introduces_a_skill():
    onto = _mini()
    # skill only in title, absent from body → not included (empty-text contract clause)
    assert map_text_to_skills("we build backend services", onto, title="PyTorch Engineer") == {}


# ── determinism ────────────────────────────────────────────────────────
def test_determinism_same_input_same_output():
    onto = _mini()
    text = "Machine learning with PyTorch, Python and deep learning."
    assert map_text_to_skills(text, onto) == map_text_to_skills(text, onto)


# ── match_strength ladder ──────────────────────────────────────────────
def test_match_strength_exact():
    onto = _mini()
    assert match_strength({"skill:pytorch": 1.0}, "skill:pytorch", onto) == 1.0


def test_match_strength_parent_direction():
    onto = _mini()
    # candidate has DL; job wants ML (a parent of DL) → 0.7
    assert match_strength({"skill:dl": 1.0}, "skill:ml", onto) == 0.7


def test_match_strength_child_direction():
    onto = _mini()
    # candidate has ML; job wants DL (a child of ML) → 0.7
    assert match_strength({"skill:ml": 1.0}, "skill:dl", onto) == 0.7


def test_match_strength_related():
    onto = _mini()
    # candidate has ML; job wants Python (related to ML, not parent/child) → 0.5
    assert match_strength({"skill:ml": 1.0}, "skill:python", onto) == 0.5


def test_match_strength_none():
    onto = _mini()
    assert match_strength({"skill:python": 1.0}, "skill:r", onto) == 0.0


def test_match_strength_prefers_parent_over_related():
    onto = _mini()
    # candidate has both ml (parent of dl) and pytorch (related of dl) for job dl
    strength = match_strength({"skill:ml": 1.0, "skill:pytorch": 1.0}, "skill:dl", onto)
    assert strength == 0.7


# ── real ontology behaves on a realistic blurb ─────────────────────────
def test_real_ontology_maps_realistic_jd():
    got = map_text_to_skills(
        "Build LLM and RAG pipelines with LangChain and PyTorch; deploy on AWS with Docker.",
        title="Machine Learning Engineer, LLM",
    )
    for sid in ("skill:rag", "skill:langchain", "skill:pytorch", "skill:aws",
                "skill:docker", "skill:llm"):
        assert sid in got
