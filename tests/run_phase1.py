#!/usr/bin/env python3
"""
Phase 1 Pipeline Runner — run without pytest for quick debugging.
Usage: python3 tests/run_phase1.py [--url http://127.0.0.1:5001]
"""
import sys
import json
import time
import requests

BACKEND = "http://127.0.0.1:5001"
if "--url" in sys.argv:
    idx = sys.argv.index("--url")
    if idx + 1 < len(sys.argv):
        BACKEND = sys.argv[idx + 1]

SAMPLE_JD = """Senior AI/ML Engineer — Globalization Partners (G-P)
Houston, TX (Hybrid) | Full-Time

About the Role
G-P is looking for a Senior AI/ML Engineer to lead development of LLM-powered product features. You will build production-grade RAG pipelines, fine-tune language models, and deploy scalable ML APIs.

Responsibilities
- Design and deploy LLM-based solutions using OpenAI, Claude, and open-source models
- Build RAG pipelines with vector databases (Pinecone, Chroma, pgvector)
- Fine-tune language models for domain-specific tasks
- Build and maintain ML APIs using FastAPI or similar frameworks
- Collaborate with product teams to translate requirements into ML solutions
- Monitor model performance and implement feedback loops

Requirements (Must Have)
- 3+ years of hands-on experience with Python and ML frameworks (PyTorch, TensorFlow)
- Experience with LLMs: prompt engineering, RAG, fine-tuning
- Experience with vector databases and embedding models
- Strong understanding of MLOps: CI/CD for ML, model versioning, monitoring
- AWS or Azure cloud deployment experience
- MS or PhD in CS, Data Science, or related field preferred

Nice to Have
- LangChain, LlamaIndex, or similar orchestration frameworks
- Kubernetes, Docker for containerized ML deployments
- Experience with agentic AI systems (multi-agent, tool use)"""


PASS_COUNT = 0
FAIL_COUNT = 0
FAILURES = []


def run_step(name, fn):
    global PASS_COUNT, FAIL_COUNT
    print(f"\n{'='*60}")
    print(f"STEP: {name}")
    print("=" * 60)
    t0 = time.time()
    try:
        result = fn()
        elapsed = time.time() - t0
        print(f"PASS ({elapsed:.1f}s)")
        PASS_COUNT += 1
        return True, result
    except Exception as e:
        elapsed = time.time() - t0
        print(f"FAIL ({elapsed:.1f}s): {e}")
        FAIL_COUNT += 1
        FAILURES.append((name, str(e)))
        return False, None


# ── Step helpers ──────────────────────────────────────────────────

def step_health():
    r = requests.get(f"{BACKEND}/health", timeout=5)
    r.raise_for_status()
    print(f"  Health: {r.json()}")
    return r.json()


def step_post_pending_jd():
    r = requests.post(
        f"{BACKEND}/pending-jd",
        json={"jd": SAMPLE_JD, "role": "Senior AI/ML Engineer", "company": "Globalization Partners"},
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    assert data.get("ok") is True, f"Expected ok=true, got: {data}"
    token = data.get("token")
    assert token and len(token) > 8, f"Token missing or too short: {token!r}"
    print(f"  Token: {token}")
    return token


def step_get_pending_jd(token):
    r = requests.get(f"{BACKEND}/pending-jd", params={"token": token}, timeout=10)
    r.raise_for_status()
    got = r.json()
    assert got["jd"] == SAMPLE_JD, (
        f"JD mismatch. Got {len(got.get('jd',''))} chars, expected {len(SAMPLE_JD)}"
    )
    assert got["role"] == "Senior AI/ML Engineer", f"Role mismatch: {got.get('role')!r}"
    assert got["company"] == "Globalization Partners", f"Company mismatch: {got.get('company')!r}"
    print(f"  JD fetched: {len(got['jd'])} chars, role={got['role']!r}, company={got['company']!r}")
    return got


def step_token_consumed(token):
    """First read already consumed token; second read must return empty JD."""
    r = requests.get(f"{BACKEND}/pending-jd", params={"token": token}, timeout=10)
    r.raise_for_status()
    got = r.json()
    assert got.get("jd", "") == "", (
        f"Expected empty JD on second read (token should be consumed), got {len(got.get('jd',''))} chars"
    )
    print("  Token correctly consumed (second read returned empty)")


def step_analyze_deep():
    print("  Calling /analyze-deep (may take up to 120s)...")
    r = requests.post(
        f"{BACKEND}/analyze-deep",
        json={
            "jd_text": SAMPLE_JD,
            "company": "Globalization Partners",
            "role": "Senior AI/ML Engineer",
            "llm": "ollama",
        },
        timeout=120,
    )
    r.raise_for_status()
    data = r.json()
    assert "match_score" in data, f"Response missing match_score: {list(data.keys())}"
    score = data["match_score"]
    assert 0 <= score <= 100, f"match_score out of range: {score}"
    skills = data.get("must_have_skills", [])
    assert len(skills) > 0, "No must_have_skills returned"
    print(f"  match_score={score}  must_have_skills={len(skills)}  gaps={len(data.get('gaps',[]))}")

    # Validate skills appear in JD
    jd_lower = SAMPLE_JD.lower()
    fabricated = []
    for s in skills:
        skill = s.get("skill", "").lower()
        if not any(tok in jd_lower for tok in skill.split() if len(tok) >= 3):
            fabricated.append(skill)
    if fabricated:
        print(f"  WARNING: {len(fabricated)} skill(s) may be fabricated (not found in JD): {fabricated[:5]}")
    else:
        print(f"  All {len(skills)} skills verified against JD text")

    return data


def step_tailor_resume():
    print("  Calling /tailor-resume (may take up to 300s)...")
    r = requests.post(
        f"{BACKEND}/tailor-resume",
        json={
            "jd_text": SAMPLE_JD,
            "role": "Senior AI/ML Engineer",
            "company": "Globalization Partners",
            "selected_skills": ["Python", "LLM", "RAG", "FastAPI", "MLOps"],
            "llm": "ollama",
        },
        timeout=300,
    )
    r.raise_for_status()
    data = r.json()
    assert "experience" in data, f"Response missing 'experience': {list(data.keys())}"
    assert len(data["experience"]) > 0, "experience array is empty"

    all_bullets = [b for e in data["experience"] for b in (e.get("bullets") or [])]
    changed = [b for b in all_bullets if b.get("status") in ("edited", "added")]
    unchanged = [b for b in all_bullets if b.get("status") == "unchanged"]

    print(f"  Bullets: total={len(all_bullets)}  changed={len(changed)}  unchanged={len(unchanged)}")

    assert len(all_bullets) > 0, "No bullets in tailored resume"
    assert len(changed) > 0, (
        f"REGRESSION: ALL {len(all_bullets)} bullets are 'unchanged' — tailoring produced no edits! "
        f"Sample: {all_bullets[0] if all_bullets else 'none'}"
    )

    summary = data.get("tailored_summary", "")
    if summary:
        print(f"  Summary: {summary[:120]}...")
    else:
        print("  WARNING: tailored_summary is empty")

    return data


def step_generate_pdf(tailored_data):
    print("  Calling /generate-pdf (may take up to 180s)...")
    payload = dict(tailored_data)
    payload["_company"] = "Globalization Partners"
    payload["_role"] = "Senior AI/ML Engineer"
    payload["_jd"] = SAMPLE_JD

    r = requests.post(f"{BACKEND}/generate-pdf", json=payload, timeout=180)
    r.raise_for_status()

    ct = r.headers.get("content-type", "")
    assert ct.startswith("application/pdf"), f"Expected application/pdf content-type, got: {ct!r}"

    pdf_bytes = r.content
    assert pdf_bytes[:4] == b"%PDF", (
        f"Response does not start with %PDF. First 20 bytes: {pdf_bytes[:20]!r}"
    )
    assert len(pdf_bytes) > 10000, (
        f"PDF too small ({len(pdf_bytes)} bytes) — likely empty or corrupt"
    )
    print(f"  PDF generated: {len(pdf_bytes):,} bytes — valid PDF")
    return pdf_bytes


# ── Main ─────────────────────────────────────────────────────────

def main():
    print(f"\nSmartApplyAI Phase 1 Pipeline Runner")
    print(f"Backend: {BACKEND}")
    print(f"JD length: {len(SAMPLE_JD)} chars")

    # Step 1: health
    ok, _ = run_step("Backend health check", step_health)
    if not ok:
        print("\nBackend is not running. Start it with:")
        print("  cd backend && uvicorn main:app --port 5001 --reload")
        sys.exit(1)

    # Step 2: pending-jd roundtrip
    ok, token = run_step("POST /pending-jd (store JD)", step_post_pending_jd)
    if ok:
        ok, jd_data = run_step("GET /pending-jd (fetch with token)", lambda: step_get_pending_jd(token))
        if ok:
            # Re-post a fresh token to test consumption (the first token was already consumed by GET)
            ok2, token2 = run_step(
                "POST /pending-jd (fresh token for consumption test)",
                step_post_pending_jd,
            )
            if ok2:
                # Read it once (consumes it)
                requests.get(f"{BACKEND}/pending-jd", params={"token": token2}, timeout=10)
                run_step("Token consumed on second read", lambda: step_token_consumed(token2))

    # Step 3: analyze-deep
    ok, analysis = run_step("POST /analyze-deep", step_analyze_deep)

    # Step 4: tailor-resume
    ok, tailored = run_step("POST /tailor-resume (check for actual edits)", step_tailor_resume)

    # Step 5: generate-pdf (only if tailor succeeded)
    if ok and tailored is not None:
        run_step("POST /generate-pdf (validate PDF bytes)", lambda: step_generate_pdf(tailored))
    else:
        print(f"\n{'='*60}")
        print("STEP: POST /generate-pdf (validate PDF bytes)")
        print("=" * 60)
        print("SKIPPED — tailor-resume failed, cannot generate PDF")
        FAILURES.append(("POST /generate-pdf", "Skipped (tailor-resume failed)"))

    # Summary
    total = PASS_COUNT + FAIL_COUNT
    print(f"\n{'='*60}")
    print(f"SUMMARY: {PASS_COUNT}/{total} steps passed")
    print("=" * 60)
    if FAILURES:
        print("FAILED STEPS:")
        for name, err in FAILURES:
            print(f"  - {name}: {err}")
        sys.exit(1)
    else:
        print("All steps passed!")
        sys.exit(0)


if __name__ == "__main__":
    main()
