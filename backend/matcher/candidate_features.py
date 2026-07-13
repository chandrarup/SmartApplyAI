"""Candidate-side features for hybrid job scoring (Phase-2 design contract §3.3).

Builds a deterministic feature bundle for one candidate profile:

- ``skills``: {skill_id: weight} mapped from profile facets with facet weights
  (skills sections 1.0, experience details 0.8, projects 0.7, summary 0.5),
  summed per skill then max-normalized so the top skill weight is 1.0.
- ``domains``: domain tags detected with the same keyword table as
  ``backend.matcher.features`` (imported lazily; local fallback copy below).
- ``profile_embedding`` / ``experience_embeddings``: via the existing
  ``knowledge.embeddings.embed`` adapter (injectable ``embed_fn`` for tests).
- ``query_terms``: top skill display names + synonyms (max 24) for BM25.

Caching: table ``candidate_features(profile_id TEXT PRIMARY KEY, profile_hash
TEXT, payload BLOB, updated_at TEXT)`` inside the shared ``features.db``.
This module only ever issues ``CREATE TABLE IF NOT EXISTS`` for that one table
and never touches the job tables owned by ``backend.matcher.features``.
Rebuild happens iff the profile hash (sha256 of canonical JSON) differs.

Payload serialization (pickle-free): the feature dict is stored as UTF-8 JSON;
each numpy array is replaced by a JSON envelope::

    {"shape": [...], "dtype": "float32", "data": "<base64 of tobytes()>"}

PARITY CONTRACT (CLAUDE.md rule 3): this module only READS the profile via
``knowledge.store.get_profile`` — it never writes to knowledge.db and never
alters the profile dict shape.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np

__all__ = ["build_candidate_features", "profile_hash", "CANDIDATE_TABLE_SQL"]

# Same value as features.FEATURES_DB (Agent B); used only if that module is
# not importable yet. _resolve_db_path prefers the features.py constant.
_DEFAULT_FEATURES_DB = Path(__file__).with_name("features.db")

_FACET_WEIGHT_SKILLS = 1.0
_FACET_WEIGHT_EXPERIENCE = 0.8
_FACET_WEIGHT_PROJECTS = 0.7
_FACET_WEIGHT_SUMMARY = 0.5

_PROFILE_EMBED_TOP_SKILLS = 12   # skills named in the profile-embedding paragraph
_EXPERIENCE_EMBED_CAP = 12       # max experience/project descriptions embedded
_QUERY_TERM_CAP = 24             # max BM25 query terms

CANDIDATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS candidate_features (
  profile_id TEXT PRIMARY KEY,
  profile_hash TEXT,
  payload BLOB,
  updated_at TEXT
)
"""

# Fallback copy of the features.py domain keyword table (same tags per design
# contract §3.2) so this module works standalone while modules are built in
# parallel. _resolve_domain_keywords prefers features.DOMAIN_KEYWORDS.
_FALLBACK_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "ml": ["machine learning", "deep learning", "pytorch", "tensorflow",
           "scikit-learn", "sklearn", "neural network", "model training",
           "xgboost", "mlops"],
    "llm": ["llm", "large language model", "generative ai", "genai", "rag",
            "retrieval augmented", "prompt engineering", "fine-tuning",
            "finetuning", "langchain", "vector database", "gpt", "claude"],
    "cv": ["computer vision", "image segmentation", "object detection",
           "opencv", "image classification", "cellpose", "stardist"],
    "nlp": ["nlp", "natural language processing", "text classification",
            "named entity recognition", "transformers", "tokenization"],
    "biomed": ["biomedical", "bioinformatics", "spatial omics", "single-cell",
               "genomics", "microscopy", "histology", "pathology"],
    "robotics": ["robotics", "robot", "ros", "slam", "autonomous systems"],
    "data-eng": ["etl", "data pipeline", "airflow", "spark", "kafka", "dbt",
                 "data warehouse", "snowflake", "data engineering"],
    "backend": ["backend", "fastapi", "flask", "django", "microservices",
                "rest api", "api", "postgresql", "sql", "sqlite"],
    "frontend": ["frontend", "react", "typescript", "javascript", "css",
                 "next.js"],
    "security": ["security", "vulnerability", "cryptography", "appsec",
                 "penetration testing"],
    "fintech": ["fintech", "trading", "payments", "banking", "financial services"],
    "healthcare": ["healthcare", "health care", "clinical", "medical",
                   "patient", "ehr", "hipaa"],
}


# ── lazy dependency resolution (parallel-build safe) ──────────────────

def _resolve_db_path(db_path: str | Path | None) -> Path:
    if db_path is not None:
        return Path(db_path)
    try:
        from backend.matcher.features import FEATURES_DB
        return Path(FEATURES_DB)
    except ImportError:
        try:
            from matcher.features import FEATURES_DB  # type: ignore
            return Path(FEATURES_DB)
        except ImportError:
            return _DEFAULT_FEATURES_DB


def _resolve_domain_keywords(
    domain_keywords: dict[str, list[str]] | None,
) -> dict[str, list[str]]:
    if domain_keywords is not None:
        return domain_keywords
    try:
        from backend.matcher.features import DOMAIN_KEYWORDS
        return DOMAIN_KEYWORDS
    except ImportError:
        try:
            from matcher.features import DOMAIN_KEYWORDS  # type: ignore
            return DOMAIN_KEYWORDS
        except ImportError:
            return _FALLBACK_DOMAIN_KEYWORDS


def _resolve_mapper(
    ontology: dict | None,
    mapper: Callable[[str, str], dict[str, float]] | None,
) -> tuple[Callable[[str], dict[str, float]], dict | None]:
    """Return (text -> {skill_id: weight} callable, ontology dict or None).

    Prefers an injected ``mapper``; otherwise uses
    ``backend.matcher.ontology.map_text_to_skills`` (Agent A). Fails loud if
    neither is available (caller falls back to legacy ranking, rule 7).
    """
    if mapper is not None:
        return (lambda text: dict(mapper(text, "") or {})), ontology

    onto_mod = None
    try:
        from backend.matcher import ontology as onto_mod  # type: ignore
    except ImportError:
        try:
            from matcher import ontology as onto_mod  # type: ignore
        except ImportError:
            onto_mod = None
    if onto_mod is None:
        raise RuntimeError(
            "candidate_features: no skill mapper available — "
            "backend.matcher.ontology is not importable and no mapper was injected"
        )
    onto = ontology if ontology is not None else onto_mod.load_ontology()
    return (
        lambda text: dict(onto_mod.map_text_to_skills(text, onto, title="") or {})
    ), onto


def _default_embed() -> Callable[[list[str]], list[list[float]]]:
    try:
        from backend.knowledge.embeddings import embed
    except ImportError:
        from knowledge.embeddings import embed  # type: ignore
    return embed


def _get_profile(profile_id: str) -> dict[str, Any]:
    """READ-ONLY profile fetch (parity contract, CLAUDE.md rule 3)."""
    try:
        from backend.knowledge import store as knowledge_store
    except ImportError:
        from knowledge import store as knowledge_store  # type: ignore
    return knowledge_store.get_profile(profile_id) or {}


# ── profile hashing ───────────────────────────────────────────────────

def profile_hash(profile: dict[str, Any]) -> str:
    """sha256 of the canonical (sorted-keys, compact) JSON of the profile."""
    canon = json.dumps(
        profile, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), default=str,
    )
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


# ── facet extraction (profile shape per KNOWLEDGE_SERVICE_MAP §5) ─────

def _facet_texts(profile: dict[str, Any]) -> list[tuple[str, float]]:
    """(text, facet_weight) pairs. Malformed entries are skipped, not fatal."""
    facets: list[tuple[str, float]] = []

    skills = profile.get("skills") or {}
    if isinstance(skills, dict):
        for section in sorted(skills):  # deterministic section order
            items = skills.get(section)
            if isinstance(items, list) and items:
                facets.append(
                    (", ".join(str(i) for i in items), _FACET_WEIGHT_SKILLS)
                )

    for exp in profile.get("experience") or []:
        if not isinstance(exp, dict):
            continue
        details = exp.get("details") or exp.get("bullets") or []
        if not isinstance(details, list):
            details = [str(details)]
        text = " ".join(
            [str(exp.get("role") or exp.get("title") or "")]
            + [str(d) for d in details]
        ).strip()
        if text:
            facets.append((text, _FACET_WEIGHT_EXPERIENCE))

    for proj in profile.get("projects") or []:
        if not isinstance(proj, dict):
            continue
        stack = proj.get("tech_stack") or []
        stack_txt = (
            ", ".join(str(s) for s in stack) if isinstance(stack, list) else str(stack)
        )
        text = " ".join(
            part for part in (
                str(proj.get("title") or ""),
                stack_txt,
                str(proj.get("description") or ""),
            ) if part
        ).strip()
        if text:
            facets.append((text, _FACET_WEIGHT_PROJECTS))

    summary = str(profile.get("summary") or "").strip()
    if summary:
        facets.append((summary, _FACET_WEIGHT_SUMMARY))

    return facets


def _experience_texts(profile: dict[str, Any]) -> list[str]:
    """One text per experience/project description, capped at 12."""
    texts: list[str] = []
    for exp in profile.get("experience") or []:
        if not isinstance(exp, dict):
            continue
        details = exp.get("details") or exp.get("bullets") or []
        if not isinstance(details, list):
            details = [str(details)]
        role = str(exp.get("role") or exp.get("title") or "").strip()
        company = str(exp.get("company") or "").strip()
        header = " @ ".join(p for p in (role, company) if p)
        body = " ".join(str(d) for d in details).strip()
        text = " | ".join(p for p in (header, body) if p)
        if text:
            texts.append(text)
    for proj in profile.get("projects") or []:
        if not isinstance(proj, dict):
            continue
        stack = proj.get("tech_stack") or []
        stack_txt = (
            ", ".join(str(s) for s in stack) if isinstance(stack, list) else str(stack)
        )
        text = " | ".join(
            p for p in (
                str(proj.get("title") or "").strip(),
                stack_txt.strip(),
                str(proj.get("description") or "").strip(),
            ) if p
        )
        if text:
            texts.append(text)
    return texts[:_EXPERIENCE_EMBED_CAP]


# ── skill naming / query terms ────────────────────────────────────────

def _skill_field(skill_obj: Any, field: str) -> Any:
    if isinstance(skill_obj, dict):
        return skill_obj.get(field)
    return getattr(skill_obj, field, None)


def _human_id(skill_id: str) -> str:
    """'skill:deep-learning' -> 'deep learning' (fallback display name)."""
    return str(skill_id).split(":", 1)[-1].replace("-", " ").replace("_", " ").strip()


def _display_name(skill_id: str, ontology: dict | None) -> str:
    sk = (ontology or {}).get(skill_id)
    name = _skill_field(sk, "name") if sk is not None else None
    return str(name) if name else _human_id(skill_id)


def _ordered_skills(skill_weights: dict[str, float]) -> list[tuple[str, float]]:
    """Deterministic: weight desc, then skill id alphabetical."""
    return sorted(skill_weights.items(), key=lambda kv: (-kv[1], kv[0]))


def _query_terms(
    skill_weights: dict[str, float],
    ontology: dict | None,
    cap: int = _QUERY_TERM_CAP,
) -> list[str]:
    """Top skill display names + synonyms by weight, deduped, max ``cap``."""
    terms: list[str] = []
    seen: set[str] = set()
    for sid, _weight in _ordered_skills(skill_weights):
        names: list[str] = []
        sk = (ontology or {}).get(sid)
        if sk is not None:
            name = _skill_field(sk, "name")
            if name:
                names.append(str(name))
            names.extend(str(s) for s in (_skill_field(sk, "synonyms") or []))
        if not names:
            names.append(_human_id(sid))
        for name in names:
            key = name.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            terms.append(name.strip())
            if len(terms) >= cap:
                return terms
    return terms


# ── domain detection ──────────────────────────────────────────────────

def _detect_domains(text: str, keyword_table: dict[str, list[str]]) -> list[str]:
    """Domain tags with >=1 keyword hit, ordered by hits desc then tag name."""
    text_lower = str(text or "").lower()
    counts: dict[str, int] = {}
    for tag in sorted(keyword_table):
        hits = 0
        for kw in keyword_table[tag]:
            kw = str(kw).lower().strip()
            if not kw:
                continue
            if re.search(r"(?<!\w)" + re.escape(kw) + r"(?!\w)", text_lower):
                hits += 1
        if hits:
            counts[tag] = hits
    return [tag for tag, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


# ── numpy array (de)serialization — pickle-free JSON envelope ─────────

def _encode_array(arr: np.ndarray) -> dict[str, Any]:
    a = np.asarray(arr, dtype=np.float32)
    return {
        "shape": list(a.shape),
        "dtype": "float32",
        "data": base64.b64encode(a.tobytes()).decode("ascii"),
    }


def _decode_array(envelope: dict[str, Any]) -> np.ndarray:
    raw = base64.b64decode(envelope["data"])
    arr = np.frombuffer(raw, dtype=np.dtype(str(envelope.get("dtype", "float32"))))
    return arr.reshape([int(d) for d in envelope["shape"]]).copy()


def _features_to_payload(features: dict[str, Any]) -> bytes:
    doc = dict(features)
    doc["profile_embedding"] = _encode_array(features["profile_embedding"])
    doc["experience_embeddings"] = [
        _encode_array(a) for a in features["experience_embeddings"]
    ]
    return json.dumps(doc, ensure_ascii=False, sort_keys=True).encode("utf-8")


def _payload_to_features(blob: bytes) -> dict[str, Any]:
    doc = json.loads(bytes(blob).decode("utf-8"))
    doc["profile_embedding"] = _decode_array(doc["profile_embedding"])
    doc["experience_embeddings"] = [
        _decode_array(e) for e in doc["experience_embeddings"]
    ]
    return doc


# ── core build ────────────────────────────────────────────────────────

def _build_features(
    profile: dict[str, Any],
    phash: str,
    target_level: str,
    *,
    ontology: dict | None,
    embed_fn: Callable[[list[str]], list[list[float]]] | None,
    mapper: Callable[[str, str], dict[str, float]] | None,
    domain_keywords: dict[str, list[str]] | None,
) -> dict[str, Any]:
    map_fn, onto = _resolve_mapper(ontology, mapper)
    facets = _facet_texts(profile)

    # Facet-weighted skill aggregation, then max-normalize to top weight 1.0.
    skill_weights: dict[str, float] = {}
    for text, facet_weight in facets:
        for sid, weight in (map_fn(text) or {}).items():
            try:
                w = float(weight)
            except (TypeError, ValueError):
                continue
            sid = str(sid)
            skill_weights[sid] = skill_weights.get(sid, 0.0) + facet_weight * w
    if skill_weights:
        top = max(skill_weights.values())
        if top > 0:
            skill_weights = {
                sid: round(w / top, 6) for sid, w in skill_weights.items()
            }

    ordered = _ordered_skills(skill_weights)
    top_names = [
        _display_name(sid, onto) for sid, _ in ordered[:_PROFILE_EMBED_TOP_SKILLS]
    ]
    summary = str(profile.get("summary") or "").strip()
    profile_text_parts = [summary] if summary else []
    if top_names:
        profile_text_parts.append("Key skills: " + ", ".join(top_names))
    profile_text = "\n\n".join(profile_text_parts) or " "

    experience_texts = _experience_texts(profile)

    embed = embed_fn if embed_fn is not None else _default_embed()
    vectors = embed([profile_text] + experience_texts) or []
    expected = 1 + len(experience_texts)
    if len(vectors) != expected:
        raise RuntimeError(
            f"candidate_features: embed_fn returned {len(vectors)} vectors "
            f"for {expected} texts"
        )
    profile_embedding = np.asarray(vectors[0], dtype=np.float32)
    experience_embeddings = [np.asarray(v, dtype=np.float32) for v in vectors[1:]]

    domain_text = " ".join(text for text, _ in facets)
    domains = _detect_domains(domain_text, _resolve_domain_keywords(domain_keywords))

    return {
        "profile_hash": phash,
        "skills": skill_weights,
        "domains": domains,
        "target_level": str(target_level),
        "profile_embedding": profile_embedding,
        "experience_embeddings": experience_embeddings,
        "query_terms": _query_terms(skill_weights, onto),
    }


# ── public API ────────────────────────────────────────────────────────

def build_candidate_features(
    profile_id: str = "default",
    *,
    ontology: dict | None = None,
    embed_fn: Callable[[list[str]], list[list[float]]] | None = None,
    profile: dict | None = None,
    target_level: str = "intern",
    mapper: Callable[[str, str], dict[str, float]] | None = None,
    db_path: str | Path | None = None,
    domain_keywords: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Build (or load from cache) candidate features per design contract §3.3.

    Extra keyword-only parameters beyond the contract signature (all optional,
    for dependency injection / tests):
      mapper           callable (text, title) -> {skill_id: weight}; overrides
                       the ontology-module mapper (parallel-build safe).
      db_path          features.db override (tests use tmp_path). Defaults to
                       features.FEATURES_DB when importable, else the local
                       backend/matcher/features.db constant.
      domain_keywords  domain keyword table override; defaults to
                       features.DOMAIN_KEYWORDS with a local fallback copy.

    Cache: rebuilds iff the profile hash differs; ``target_level`` is not part
    of the hash and is stamped onto the returned dict on cache hits too.
    """
    if profile is None:
        profile = _get_profile(profile_id)
    if not profile:
        raise ValueError(
            f"candidate_features: profile '{profile_id}' is empty or missing"
        )

    phash = profile_hash(profile)
    path = _resolve_db_path(db_path)

    conn = sqlite3.connect(str(path))
    try:
        conn.execute(CANDIDATE_TABLE_SQL)
        row = conn.execute(
            "SELECT profile_hash, payload FROM candidate_features "
            "WHERE profile_id = ?",
            (profile_id,),
        ).fetchone()
        if row and row[0] == phash and row[1]:
            try:
                features = _payload_to_features(row[1])
                features["target_level"] = str(target_level)
                print(
                    f"[candidate-features] cache hit profile={profile_id} "
                    f"hash={phash[:12]}"
                )
                return features
            except Exception as exc:  # corrupt cache row → rebuild (rule 7)
                print(
                    f"[candidate-features] cache decode failed for "
                    f"profile={profile_id} ({exc}); rebuilding"
                )

        features = _build_features(
            profile, phash, target_level,
            ontology=ontology, embed_fn=embed_fn, mapper=mapper,
            domain_keywords=domain_keywords,
        )
        conn.execute(
            "INSERT OR REPLACE INTO candidate_features "
            "(profile_id, profile_hash, payload, updated_at) VALUES (?, ?, ?, ?)",
            (
                profile_id,
                phash,
                _features_to_payload(features),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    print(
        f"[candidate-features] built profile={profile_id} "
        f"skills={len(features['skills'])} domains={len(features['domains'])} "
        f"exp_embeddings={len(features['experience_embeddings'])} "
        f"query_terms={len(features['query_terms'])} hash={phash[:12]}"
    )
    return features
