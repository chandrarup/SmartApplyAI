"""
Per-company resume version storage with append-only history.

Layout on disk:
    profiles/{pid}/resumes/
      base.tex                          # canonical immutable template (copy of resume_template.tex)
      variants/
        {company_slug}_{jd_hash[:8]}_{ts}/
          tailored.tex                  # final compiled-from source
          tailored.pdf                  # final PDF
          analysis.json                 # /analyze-deep snapshot
          score.json                    # before/after scores
          edits.jsonl                   # append-only edit log
          validation.json               # constraint violations + ATS check
          meta.json                     # company, jd_hash, timestamps
"""
import json
import os
import re
import time
import hashlib
import shutil


def _safe_slug(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", (s or "").lower()).strip()
    s = re.sub(r"[\s_-]+", "-", s)
    return s[:40] or "unknown"


def _hash_jd(jd: str) -> str:
    return hashlib.sha1((jd or "").encode("utf-8")).hexdigest()[:12]


def get_versions_dir(profile_dir: str) -> str:
    d = os.path.join(profile_dir, "resumes", "variants")
    os.makedirs(d, exist_ok=True)
    return d


def create_variant(
    profile_dir: str,
    *,
    company: str,
    role: str,
    jd_text: str,
    tailored_tex: str | None = None,
    tailored_pdf_bytes: bytes | None = None,
    analysis: dict | None = None,
    score: dict | None = None,
    validation: dict | None = None,
) -> dict:
    """Create a new resume variant on disk. Returns metadata dict."""
    variants_dir = get_versions_dir(profile_dir)
    ts = int(time.time())
    slug = _safe_slug(company)
    jd_h = _hash_jd(jd_text)
    variant_id = f"{slug}_{jd_h}_{ts}"
    var_dir = os.path.join(variants_dir, variant_id)
    os.makedirs(var_dir, exist_ok=True)

    if tailored_tex:
        with open(os.path.join(var_dir, "tailored.tex"), "w") as f:
            f.write(tailored_tex)
    if tailored_pdf_bytes:
        with open(os.path.join(var_dir, "tailored.pdf"), "wb") as f:
            f.write(tailored_pdf_bytes)
    if analysis is not None:
        with open(os.path.join(var_dir, "analysis.json"), "w") as f:
            json.dump(analysis, f, indent=2)
    if score is not None:
        with open(os.path.join(var_dir, "score.json"), "w") as f:
            json.dump(score, f, indent=2)
    if validation is not None:
        with open(os.path.join(var_dir, "validation.json"), "w") as f:
            json.dump(validation, f, indent=2)

    meta = {
        "id": variant_id,
        "company": company,
        "role": role,
        "jd_hash": jd_h,
        "jd_preview": (jd_text or "")[:200],
        "created_at": ts,
        "created_at_iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)),
    }
    with open(os.path.join(var_dir, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)
    return meta


def list_variants(profile_dir: str) -> list[dict]:
    """Return metadata for all variants, newest first."""
    variants_dir = os.path.join(profile_dir, "resumes", "variants")
    if not os.path.exists(variants_dir):
        return []
    out = []
    for name in os.listdir(variants_dir):
        meta_path = os.path.join(variants_dir, name, "meta.json")
        if not os.path.exists(meta_path):
            continue
        try:
            with open(meta_path) as f:
                m = json.load(f)
            # Attach derived: pdf exists, score
            m["has_pdf"] = os.path.exists(os.path.join(variants_dir, name, "tailored.pdf"))
            score_path = os.path.join(variants_dir, name, "score.json")
            if os.path.exists(score_path):
                try:
                    with open(score_path) as f:
                        m["score"] = json.load(f)
                except Exception:
                    pass
            out.append(m)
        except Exception:
            continue
    out.sort(key=lambda x: x.get("created_at", 0), reverse=True)
    return out


def get_variant_path(profile_dir: str, variant_id: str, file_name: str) -> str | None:
    """Return absolute path to a variant file, or None if missing."""
    if not variant_id or not re.match(r"^[\w.-]+$", variant_id):
        return None
    path = os.path.join(profile_dir, "resumes", "variants", variant_id, file_name)
    return path if os.path.exists(path) else None


def append_edit(profile_dir: str, variant_id: str, edit: dict) -> bool:
    """Append an edit event to the variant's edits.jsonl. Returns success."""
    var_dir = os.path.join(profile_dir, "resumes", "variants", variant_id)
    if not os.path.exists(var_dir):
        return False
    edit["ts"] = int(time.time())
    try:
        with open(os.path.join(var_dir, "edits.jsonl"), "a") as f:
            f.write(json.dumps(edit) + "\n")
        return True
    except Exception:
        return False
