"""Per-job feature extraction and cache for hybrid filtering (Phase-2 §3.2).

Owns backend/matcher/features.db (rule 8): a job_features table plus an FTS5
index used for BM25 lexical scoring. JD text is untrusted input (rule 6): it is
only ever regex-matched or embedded here, never templated into an LLM prompt.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np

FEATURES_DB = Path(__file__).with_name("features.db")

# Domain tag -> keyword phrases (matched case-insensitively with word boundaries).
# Agent C's candidate_features.py imports this table — keep the name stable.
DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "ml": [
        "machine learning", "deep learning", "neural network", "neural networks",
        "pytorch", "tensorflow", "scikit-learn", "model training", "mlops",
        "ml engineer", "ml pipeline", "feature engineering",
    ],
    "llm": [
        "llm", "llms", "large language model", "large language models",
        "generative ai", "genai", "gpt", "rag", "retrieval augmented",
        "retrieval-augmented", "prompt engineering", "fine-tuning", "finetuning",
        "langchain", "foundation model", "foundation models", "ai agents",
        "transformer", "transformers",
    ],
    "cv": [
        "computer vision", "image segmentation", "object detection",
        "image classification", "opencv", "image processing", "image analysis",
        "video analytics", "pose estimation",
    ],
    "nlp": [
        "nlp", "natural language processing", "named entity recognition",
        "text classification", "sentiment analysis", "text mining",
        "speech recognition", "information extraction",
    ],
    "biomed": [
        "biomedical", "bioinformatics", "computational biology", "genomics",
        "microscopy", "cellpose", "stardist", "spatial omics", "spatial biology",
        "single-cell", "histology", "pathology", "proteomics", "drug discovery",
        "biotech", "life sciences",
    ],
    "robotics": [
        "robotics", "robot", "robots", "autonomous vehicles", "autonomous driving",
        "slam", "motion planning", "path planning", "ros", "control systems",
        "perception stack", "sensor fusion",
    ],
    "data-eng": [
        "data engineering", "data engineer", "etl", "data pipeline",
        "data pipelines", "airflow", "spark", "kafka", "data warehouse", "dbt",
        "snowflake", "databricks", "data lake",
    ],
    "backend": [
        "backend", "back-end", "rest api", "rest apis", "microservices",
        "fastapi", "django", "flask", "distributed systems", "grpc",
        "api development", "server-side",
    ],
    "frontend": [
        "frontend", "front-end", "react", "javascript", "typescript", "css",
        "user interface", "web application", "vue", "angular", "next.js",
    ],
    "security": [
        "cybersecurity", "security engineer", "application security", "appsec",
        "infosec", "penetration testing", "threat detection", "vulnerability",
        "vulnerabilities", "encryption", "zero trust", "incident response",
    ],
    "fintech": [
        "fintech", "payments", "trading", "banking", "financial services",
        "fraud detection", "risk modeling", "capital markets", "lending",
        "brokerage",
    ],
    "healthcare": [
        "healthcare", "clinical", "ehr", "emr", "medical devices", "patient",
        "patients", "hipaa", "health records", "hospital", "telehealth",
        "medical imaging",
    ],
}

# --- section / title / remote heuristics -----------------------------------

_PREFERRED_HEADER_RE = re.compile(
    r"nice[\s-]+to[\s-]+have|preferred|bonus", re.IGNORECASE
)
_REQUIRED_HEADER_RE = re.compile(
    r"requirements?|qualifications?|what\s+you.ll\s+need|responsibilities",
    re.IGNORECASE,
)
_MAX_HEADER_LEN = 60

_LEVEL_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("intern", re.compile(r"\bintern(?:ship)?s?\b|\bco[\s-]?op\b", re.IGNORECASE)),
    (
        "entry",
        re.compile(
            r"\bjunior\b|\bnew[\s-]grad(?:uate)?\b|\bentry(?:[\s-]level)?\b|\bassociate\b",
            re.IGNORECASE,
        ),
    ),
    (
        "senior",
        re.compile(
            r"\bsenior\b|\bsr\.?\b|\bstaff\b|\bprincipal\b|\blead\b|\bmanager\b|\bdirector\b",
            re.IGNORECASE,
        ),
    ),
]

_REMOTE_RE = re.compile(
    r"\bremote\b|\bwork[\s-]+from[\s-]+home\b|\bwfh\b|\bfully[\s-]+distributed\b",
    re.IGNORECASE,
)

_FTS_SANITIZE_RE = re.compile(r"[^A-Za-z0-9+#. -]")


def _job_key(job: dict[str, Any]) -> str:
    return f"{job.get('source_ats') or ''}:{job.get('external_id') or ''}"


def _sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _default_mapper() -> Callable[..., dict[str, float]]:
    try:
        from backend.matcher.ontology import map_text_to_skills
    except ImportError:
        from matcher.ontology import map_text_to_skills  # type: ignore
    return map_text_to_skills


def _default_embed() -> Callable[[list[str]], list[list[float]]]:
    try:
        from backend.knowledge.embeddings import embed
    except ImportError:
        from knowledge.embeddings import embed  # type: ignore
    return embed


def _split_sections(text: str) -> tuple[str, str]:
    """Split JD text into (required_text, preferred_text) by header lines.

    A header is a short line matching the required-ish or preferred-ish regex.
    Preferred wins on lines like "Preferred Qualifications". Text before the
    first header belongs to neither bucket (caller falls back to full text).
    """
    required_parts: list[str] = []
    preferred_parts: list[str] = []
    current: str | None = None
    for line in (text or "").splitlines():
        stripped = line.strip().strip(":#*-•·–— \t").strip()
        headerish = 0 < len(stripped) <= _MAX_HEADER_LEN
        if headerish and _PREFERRED_HEADER_RE.search(stripped):
            current = "preferred"
            continue
        if headerish and _REQUIRED_HEADER_RE.search(stripped):
            current = "required"
            continue
        if current == "required":
            required_parts.append(line)
        elif current == "preferred":
            preferred_parts.append(line)
    return "\n".join(required_parts).strip(), "\n".join(preferred_parts).strip()


def _detect_domains(text: str) -> list[str]:
    lowered = (text or "").lower()
    tags: list[str] = []
    for tag, phrases in DOMAIN_KEYWORDS.items():
        for phrase in phrases:
            if re.search(rf"\b{re.escape(phrase)}\b", lowered):
                tags.append(tag)
                break
    return tags


def _extract_level(title: str) -> str:
    for level, pattern in _LEVEL_RULES:
        if pattern.search(title or ""):
            return level
    return "unknown"


def ensure_features_schema(conn: sqlite3.Connection) -> None:
    """Create the job_features table and FTS5 index idempotently."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS job_features (
          job_key TEXT PRIMARY KEY,
          desc_hash TEXT NOT NULL,
          required_skills TEXT NOT NULL,
          preferred_skills TEXT NOT NULL,
          domain_tags TEXT NOT NULL,
          level TEXT NOT NULL,
          is_remote INTEGER NOT NULL DEFAULT 0,
          full_text TEXT NOT NULL,
          embedding_main BLOB NOT NULL,
          embedding_requirements BLOB,
          updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS jobs_fts "
        "USING fts5(job_key UNINDEXED, full_text)"
    )
    conn.commit()


def build_job_features(
    job: dict[str, Any],
    ontology: dict[str, Any] | None,
    *,
    mapper: Callable[..., dict[str, float]] | None = None,
) -> dict[str, Any]:
    """Extract deterministic features from one job dict. Pure — does NOT embed.

    ``mapper`` defaults to ontology.map_text_to_skills (lazily imported so this
    module loads without ontology.py); tests inject a fake mapper + ontology.
    """
    if mapper is None:
        mapper = _default_mapper()

    title = job.get("title") or ""
    company = job.get("company") or ""
    location = job.get("location") or ""
    desc = job.get("description_text") or ""

    required_text, preferred_text = _split_sections(desc)
    required_source = required_text if required_text else desc
    required_skills = (
        dict(mapper(required_source, ontology, title=title)) if required_source else {}
    )
    preferred_skills = (
        dict(mapper(preferred_text, ontology)) if preferred_text else {}
    )

    full_text = "\n".join(part for part in (title, company, location, desc) if part)
    is_remote = 1 if _REMOTE_RE.search(f"{title}\n{location}\n{desc}") else 0

    return {
        "job_key": _job_key(job),
        "desc_hash": _sha256(desc),
        "required_skills": required_skills,
        "preferred_skills": preferred_skills,
        "domain_tags": _detect_domains(f"{title}\n{desc}"),
        "level": _extract_level(title),
        "is_remote": is_remote,
        "full_text": full_text,
        # Not persisted as a column; used by ensure_job_features to embed the
        # requirements section separately (None -> embedding_requirements NULL).
        "requirements_text": required_text or None,
    }


def _embed_texts_for(feats: dict[str, Any]) -> list[str]:
    texts = [feats["full_text"]]
    if feats.get("requirements_text"):
        texts.append(feats["requirements_text"])
    return texts


def _write_features(
    conn: sqlite3.Connection, feats: dict[str, Any], vectors: list[Any], now: str
) -> None:
    embedding_main = np.asarray(vectors[0], dtype=np.float32).tobytes()
    embedding_requirements = (
        np.asarray(vectors[1], dtype=np.float32).tobytes() if len(vectors) > 1 else None
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO job_features (
          job_key, desc_hash, required_skills, preferred_skills, domain_tags,
          level, is_remote, full_text, embedding_main, embedding_requirements,
          updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            feats["job_key"],
            feats["desc_hash"],
            json.dumps(feats["required_skills"]),
            json.dumps(feats["preferred_skills"]),
            json.dumps(feats["domain_tags"]),
            feats["level"],
            int(feats["is_remote"]),
            feats["full_text"],
            embedding_main,
            embedding_requirements,
            now,
        ),
    )
    conn.execute("DELETE FROM jobs_fts WHERE job_key = ?", (feats["job_key"],))
    conn.execute(
        "INSERT INTO jobs_fts (job_key, full_text) VALUES (?, ?)",
        (feats["job_key"], feats["full_text"]),
    )


def ensure_job_features(
    jobs: list[dict[str, Any]],
    db_path: str | Path = FEATURES_DB,
    *,
    ontology: dict[str, Any] | None = None,
    embed_fn: Callable[[list[str]], list[list[float]]] | None = None,
    mapper: Callable[..., dict[str, float]] | None = None,
) -> dict[str, int]:
    """Incrementally build features for jobs (keyed by desc_hash).

    Batches embeddings for new/changed jobs in one embed_fn call; if the batch
    call fails, falls back to per-job embedding so one bad job is logged and
    skipped, never killing the run (rule 7).
    """
    built = reused = failed = 0
    conn = sqlite3.connect(str(db_path))
    try:
        ensure_features_schema(conn)

        pending: list[dict[str, Any]] = []
        for job in jobs:
            job_key = _job_key(job)
            try:
                desc_hash = _sha256(job.get("description_text") or "")
                row = conn.execute(
                    "SELECT desc_hash FROM job_features WHERE job_key = ?",
                    (job_key,),
                ).fetchone()
                if row is not None and row[0] == desc_hash:
                    reused += 1
                    continue
                pending.append(build_job_features(job, ontology, mapper=mapper))
            except Exception as exc:
                failed += 1
                print(f"[features] skip {job_key}: {exc}")

        embeddable: list[tuple[dict[str, Any], list[Any]]] = []
        if pending:
            if embed_fn is None:
                embed_fn = _default_embed()
            texts: list[str] = []
            spans: list[tuple[int, int]] = []
            for feats in pending:
                start = len(texts)
                texts.extend(_embed_texts_for(feats))
                spans.append((start, len(texts)))
            batch_vectors: Any = None
            try:
                batch_vectors = embed_fn(texts)
                if len(batch_vectors) != len(texts):
                    raise ValueError(
                        f"embed_fn returned {len(batch_vectors)} vectors "
                        f"for {len(texts)} texts"
                    )
            except Exception as exc:
                print(f"[features] batch embed failed ({exc}); retrying per job")
                batch_vectors = None
            if batch_vectors is not None:
                for feats, (start, end) in zip(pending, spans):
                    embeddable.append((feats, list(batch_vectors[start:end])))
            else:
                for feats in pending:
                    job_texts = _embed_texts_for(feats)
                    try:
                        vectors = embed_fn(job_texts)
                        if len(vectors) != len(job_texts):
                            raise ValueError(
                                f"embed_fn returned {len(vectors)} vectors "
                                f"for {len(job_texts)} texts"
                            )
                        embeddable.append((feats, list(vectors)))
                    except Exception as exc:
                        failed += 1
                        print(f"[features] skip {feats['job_key']}: {exc}")

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for feats, vectors in embeddable:
            try:
                _write_features(conn, feats, vectors, now)
                built += 1
            except Exception as exc:
                failed += 1
                print(f"[features] skip {feats['job_key']}: {exc}")
        conn.commit()
    finally:
        conn.close()
    print(f"[features] built={built} reused={reused} failed={failed}")
    return {"built": built, "reused": reused, "failed": failed}


def get_features(
    db_path: str | Path, job_keys: list[str]
) -> dict[str, dict[str, Any]]:
    """Fetch stored features for job_keys, decoding JSON columns and embeddings."""
    out: dict[str, dict[str, Any]] = {}
    if not job_keys:
        return out
    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        ensure_features_schema(conn)
        for i in range(0, len(job_keys), 500):
            chunk = list(job_keys[i : i + 500])
            placeholders = ", ".join("?" for _ in chunk)
            rows = conn.execute(
                f"SELECT * FROM job_features WHERE job_key IN ({placeholders})",
                chunk,
            ).fetchall()
            for row in rows:
                emb_req = row["embedding_requirements"]
                out[row["job_key"]] = {
                    "job_key": row["job_key"],
                    "desc_hash": row["desc_hash"],
                    "required_skills": json.loads(row["required_skills"]),
                    "preferred_skills": json.loads(row["preferred_skills"]),
                    "domain_tags": json.loads(row["domain_tags"]),
                    "level": row["level"],
                    "is_remote": bool(row["is_remote"]),
                    "full_text": row["full_text"],
                    "embedding_main": np.frombuffer(
                        row["embedding_main"], dtype=np.float32
                    ),
                    "embedding_requirements": (
                        np.frombuffer(emb_req, dtype=np.float32)
                        if emb_req is not None
                        else None
                    ),
                    "updated_at": row["updated_at"],
                }
    finally:
        conn.close()
    return out


def bm25_scores(
    db_path: str | Path,
    query_terms: list[str],
    job_keys: list[str] | None = None,
) -> dict[str, float]:
    """BM25 scores from the FTS5 index; higher = better (bm25() negated).

    Terms are sanitized (FTS5 operators stripped), quoted as phrases, and
    OR-joined. Missing keys map to 0.0. job_keys=None returns all matches.
    """
    sanitized: list[str] = []
    for term in query_terms or []:
        clean = _FTS_SANITIZE_RE.sub(" ", str(term or ""))
        clean = re.sub(r"\s+", " ", clean).strip()
        if clean and re.search(r"[A-Za-z0-9]", clean):
            sanitized.append(f'"{clean}"')
    if not sanitized:
        return {key: 0.0 for key in (job_keys or [])}

    query = " OR ".join(sanitized)
    conn = sqlite3.connect(str(db_path))
    try:
        ensure_features_schema(conn)
        rows = conn.execute(
            "SELECT job_key, -bm25(jobs_fts) AS score FROM jobs_fts "
            "WHERE jobs_fts MATCH ?",
            (query,),
        ).fetchall()
    finally:
        conn.close()

    matches = {str(key): float(score) for key, score in rows}
    if job_keys is None:
        return matches
    return {key: matches.get(key, 0.0) for key in job_keys}
