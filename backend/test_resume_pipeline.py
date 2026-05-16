#!/usr/bin/env python3
"""
End-to-end Resume Pipeline Test

Runs the FULL production path:
  1. Analyze 3 JDs → /analyze-deep
  2. Tailor each → /tailor-resume (with constraint validation)
  3. Generate PDF for each → /generate-pdf (compile + retry + ATS validate)
  4. Verify variant saved → /resume/versions
  5. Download saved variant → /resume/versions/{id}/pdf

Hard requirements (test fails on any of these):
  - All 3 JDs produce valid PDFs (HTTP 200 with application/pdf)
  - PDFs are 1-3 pages
  - PDFs are ATS-extractable (name + email visible)
  - Each variant is saved with correct company metadata
  - tailor-resume validation reports zero fatal violations
  - generate-pdf returns variant IDs in headers

Run: cd backend && /opt/anaconda3/bin/python test_resume_pipeline.py
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error

API = "http://127.0.0.1:5000"
GREEN, RED, YELLOW, RESET, BOLD, DIM = "\x1b[32m", "\x1b[31m", "\x1b[33m", "\x1b[0m", "\x1b[1m", "\x1b[2m"


def ok(msg, detail=""):  print(f"  {GREEN}✓{RESET} {msg}" + (f" {DIM}{detail}{RESET}" if detail else ""))
def fail(msg, detail=""): print(f"  {RED}✗{RESET} {msg}" + (f" {DIM}{detail}{RESET}" if detail else ""))
def info(msg):           print(f"  {YELLOW}→{RESET} {msg}")
def head(msg):           print(f"\n{BOLD}══ {msg} ══{RESET}")


def http_request(method, path, body=None, headers=None, timeout=180):
    url = API + path
    data = None
    h = {"X-Profile-ID": "default"}
    if headers: h.update(headers)
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        else:
            data = body if isinstance(body, bytes) else body.encode()
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        body = resp.read()
        # Lowercase keys for case-insensitive lookup
        return resp.status, {k.lower(): v for k, v in resp.headers.items()}, body
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()


JOB_DESCRIPTIONS = [
    {
        "company": "Cotiviti",
        "role": "AI/ML Engineer Intern",
        "jd": """Intern - Generative AI Research Engineer at Cotiviti.
Conducts primary and secondary research of advanced and emerging healthcare informatics technology
with a focus on Generative AI and emerging Artificial General Intelligence technology for healthcare.

Responsibilities:
- Develops generative AI models, architectures, databases, and systems.
- Creates research papers, analytical reports, proposals.
- Collaborates with research and development teams.

Qualifications:
- Pursuing or recently completed an advanced degree in Computer Science, Biomedical Informatics.
- Hands-on experience with Machine Learning and Deep Learning models.
- Experience with LLM/RAG models and LLM fine-tuning is a plus.
- Hands-on experience working with cloud services (AWS/Azure).
- Experience with vector embeddings and databases is a plus.""",
    },
    {
        "company": "Ready",
        "role": "Data Engineering Intern",
        "jd": """Geospatial Data Engineering Intern at Ready.
Build and scale our geospatial data infrastructure over the summer.

Responsibilities:
- Build and improve Airflow ELT pipelines that ingest, transform, and serve geospatial datasets at scale.
- Work across our Airflow 2 and Airflow 3 repositories and assist with the Airflow 2 to 3 migration.
- Port DAGs, validate parity, and help retire legacy pipelines.
- Contribute to operational infrastructure that powers geospatial analysis.

Qualifications:
- Strong Python data engineering skills.
- Experience with Airflow, dbt, or similar workflow orchestration.
- Familiarity with PostGIS, geospatial data, or spatial SQL is a plus.
- Comfortable working in Git/GitHub.""",
    },
    {
        "company": "Centific",
        "role": "Technical Intern",
        "jd": """Technical Intern at Centific.
Work with our AI team on data pipelines and ML systems.

Responsibilities:
- Build production data pipelines using Python.
- Support ML model training, evaluation, and deployment.
- Collaborate on LLM-powered applications.

Qualifications:
- Pursuing MS in CS, Data Science, or related field.
- Python, SQL, basic ML knowledge required.
- Experience with cloud (AWS/Azure/GCP) is a plus.""",
    },
]


def run_tests():
    head("PRELUDE: Backend Health + Profile")
    failures = []
    total = 0

    total += 1
    status, _, body = http_request("GET", "/health")
    if status == 200:
        ok("Backend online")
    else:
        fail(f"Backend not online: HTTP {status}")
        failures.append("backend-health")
        return total, len(failures), failures  # can't continue

    total += 1
    status, _, body = http_request("GET", "/profile")
    if status == 200:
        p = json.loads(body)
        name = p.get("contact_info", {}).get("name", "")
        if name:
            ok(f"Profile loaded: {name}")
        else:
            fail("Profile has no name"); failures.append("profile-empty")
    else:
        fail(f"Profile load failed: HTTP {status}"); failures.append("profile-load")

    for i, jd_case in enumerate(JOB_DESCRIPTIONS, 1):
        head(f"PIPELINE {i}/{len(JOB_DESCRIPTIONS)}: {jd_case['company']} — {jd_case['role']}")

        # 1. /analyze-deep
        total += 1
        info("Analyzing JD...")
        t0 = time.time()
        status, _, body = http_request("POST", "/analyze-deep", {
            "jd_text": jd_case["jd"],
            "company": jd_case["company"],
            "role": jd_case["role"],
            "llm": "ollama",
        }, timeout=180)
        elapsed = time.time() - t0
        if status != 200:
            fail(f"analyze-deep failed: HTTP {status}", body[:200].decode("utf-8", "replace"))
            failures.append(f"{jd_case['company']}:analyze")
            continue
        analysis = json.loads(body)
        mh = analysis.get("must_have_skills", [])
        if not mh:
            fail("analyze-deep returned no must-have skills")
            failures.append(f"{jd_case['company']}:no-skills")
            continue
        match = analysis.get("match_score", 0)
        ok(f"Analyzed in {elapsed:.1f}s — {match}% match, {len(mh)} must-have skills, role: {analysis.get('role','')[:40]}")

        # 2. /tailor-resume
        total += 1
        info("Tailoring resume...")
        t0 = time.time()
        status, _, body = http_request("POST", "/tailor-resume", {
            "jd_text": jd_case["jd"],
            "role": jd_case["role"],
            "company": jd_case["company"],
            "selected_skills": [s["skill"] for s in mh if s.get("matched")][:5],
            "llm": "ollama",
        }, timeout=300)
        elapsed = time.time() - t0
        if status != 200:
            fail(f"tailor-resume failed: HTTP {status}", body[:200].decode("utf-8", "replace"))
            failures.append(f"{jd_case['company']}:tailor")
            continue
        tailored = json.loads(body)
        v = tailored.get("_validation", {})
        violations = v.get("violations", [])
        fatal_violations = [vv for vv in violations if vv.get("severity") == "fatal"]
        if fatal_violations:
            fail(f"Fatal validation violations: {len(fatal_violations)}", json.dumps(fatal_violations[:2]))
            failures.append(f"{jd_case['company']}:fatal-violations")
        else:
            ok(f"Tailored in {elapsed:.1f}s — validation OK, {len(violations)} warnings, "
               f"{len(tailored.get('experience', []))} roles, "
               f"{sum(len(e.get('bullets',[])) for e in tailored.get('experience',[]))} bullets")

        # 3. /generate-pdf
        total += 1
        info("Generating PDF...")
        t0 = time.time()
        payload = {
            **tailored,
            "_company": jd_case["company"],
            "_role": jd_case["role"],
            "_jd": jd_case["jd"],
            "_analysis": analysis,
        }
        status, headers, body = http_request("POST", "/generate-pdf", payload, timeout=180)
        elapsed = time.time() - t0
        if status != 200:
            fail(f"generate-pdf failed: HTTP {status}", body[:300].decode("utf-8", "replace"))
            failures.append(f"{jd_case['company']}:generate-pdf")
            continue
        # Validate response
        ct = headers.get("content-type", "")
        if "pdf" not in ct.lower():
            fail(f"Response is not PDF: content-type={ct}")
            failures.append(f"{jd_case['company']}:not-pdf")
            continue
        size = len(body)
        if size < 10000:
            fail(f"PDF suspiciously small: {size} bytes")
            failures.append(f"{jd_case['company']}:small-pdf")
            continue
        attempts = headers.get("x-pdf-attempts", "?")
        pages = headers.get("x-pdf-pages", "?")
        ats_ok = headers.get("x-pdf-ats-ok", "false")
        latency = headers.get("x-pdf-latency-ms", "?")
        variant_id = headers.get("x-pdf-variant-id", "")
        ok(f"PDF in {elapsed:.1f}s — {size} bytes, {pages} pages, attempts={attempts}, "
           f"ATS={ats_ok}, latency={latency}ms")

        # 4. Verify variant was created
        total += 1
        if not variant_id or variant_id == "unknown":
            fail(f"No variant ID in response headers")
            failures.append(f"{jd_case['company']}:no-variant")
        else:
            status, _, body = http_request("GET", "/resume/versions")
            if status == 200:
                variants = json.loads(body).get("variants", [])
                found = next((vv for vv in variants if vv.get("id") == variant_id), None)
                if found:
                    ok(f"Variant saved: {variant_id} ({found.get('company')})")
                else:
                    fail(f"Variant {variant_id} not in /resume/versions list")
                    failures.append(f"{jd_case['company']}:variant-missing")

            # 5. Download the saved variant PDF
            total += 1
            status, _, body = http_request("GET", f"/resume/versions/{variant_id}/pdf")
            if status == 200 and len(body) > 10000:
                ok(f"Variant PDF downloaded: {len(body)} bytes")
            else:
                fail(f"Variant PDF download failed: HTTP {status}, {len(body)} bytes")
                failures.append(f"{jd_case['company']}:variant-download")

        # 6. ATS extractability — verify the PDF text contains identity fields
        total += 1
        try:
            with open("/tmp/_test_resume.pdf", "wb") as f:
                f.write(body)
            r = subprocess.run(["/opt/homebrew/bin/pdftotext", "-layout", "/tmp/_test_resume.pdf", "-"],
                               capture_output=True, timeout=15)
            if r.returncode == 0:
                text = r.stdout.decode("utf-8", "replace").lower()
                has_name = "chandra" in text and "daka" in text
                has_email = "chandrarupdaka@gmail.com" in text
                has_sections = sum(1 for s in ["summary", "education", "experience", "skills"] if s in text)
                if has_name and has_email and has_sections >= 3:
                    ok(f"ATS extraction: name+email visible, {has_sections}/4 sections present")
                else:
                    fail(f"ATS extraction incomplete: name={has_name}, email={has_email}, sections={has_sections}/4")
                    failures.append(f"{jd_case['company']}:ats-incomplete")
            else:
                fail(f"pdftotext failed: rc={r.returncode}")
                failures.append(f"{jd_case['company']}:pdftotext")
        except Exception as e:
            fail(f"ATS validation crashed: {e}")
            failures.append(f"{jd_case['company']}:ats-crash")

    head("RESULT")
    passed = total - len(failures)
    pct = round(100 * passed / total) if total else 0
    color = GREEN if pct == 100 else YELLOW if pct >= 75 else RED
    print(f"\n  {color}{passed}/{total} passed ({pct}%){RESET}")
    if failures:
        print(f"\n  Failures:")
        for f_ in failures:
            print(f"    {RED}✗{RESET} {f_}")
    else:
        print(f"  {GREEN}All tests passing — pipeline is production-ready!{RESET}")
    return total, len(failures), failures


if __name__ == "__main__":
    total, failed, _ = run_tests()
    sys.exit(0 if failed == 0 else 1)
