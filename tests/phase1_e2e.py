"""
Phase 1 End-to-End Pipeline Tests — SmartApplyAI
Tests the full JD -> Analyze -> Tailor -> PDF flow.

Run with:
    pytest tests/phase1_e2e.py -v --backend-url http://127.0.0.1:5001

These tests require:
    - Backend running: cd backend && uvicorn main:app --port 5001 --reload
    - Ollama running: ollama serve (with qwen2.5-coder:7b pulled)
    - playwright installed: pip install playwright && playwright install chromium
"""
import pytest
import requests
import json
import time

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


@pytest.fixture(scope="module")
def backend(backend_url):
    """Verify backend is reachable before running tests."""
    try:
        r = requests.get(f"{backend_url}/health", timeout=5)
        if not r.ok:
            pytest.skip(f"Backend returned {r.status_code}")
    except Exception as e:
        pytest.skip(f"Backend not reachable: {e}")
    return backend_url


class TestPendingJDRoundTrip:
    """Test the pending-jd store/fetch cycle that powers the extension->dashboard flow."""

    def test_post_and_get_with_token(self, backend):
        # POST
        r = requests.post(
            f"{backend}/pending-jd",
            json={
                "jd": SAMPLE_JD,
                "role": "Senior AI/ML Engineer",
                "company": "Globalization Partners",
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data.get("ok") is True
        token = data.get("token")
        assert token and len(token) > 8

        # GET with token
        r2 = requests.get(f"{backend}/pending-jd", params={"token": token})
        assert r2.status_code == 200
        got = r2.json()
        assert got["jd"] == SAMPLE_JD
        assert got["role"] == "Senior AI/ML Engineer"
        assert got["company"] == "Globalization Partners"

    def test_token_is_consumed_on_read(self, backend):
        """Tokens are one-time use — second read should return empty."""
        r = requests.post(
            f"{backend}/pending-jd",
            json={"jd": SAMPLE_JD, "role": "Test", "company": "Test"},
        )
        token = r.json()["token"]

        r1 = requests.get(f"{backend}/pending-jd", params={"token": token})
        assert r1.json()["jd"] == SAMPLE_JD  # first read works

        r2 = requests.get(f"{backend}/pending-jd", params={"token": token})
        assert r2.json()["jd"] == ""  # second read is empty


class TestAnalyzeDeep:
    """Test the JD analysis endpoint."""

    def test_returns_match_score(self, backend):
        r = requests.post(
            f"{backend}/analyze-deep",
            json={
                "jd_text": SAMPLE_JD,
                "company": "Globalization Partners",
                "role": "Senior AI/ML Engineer",
                "llm": "ollama",
            },
            timeout=120,
        )
        assert r.status_code == 200, f"analyze-deep failed: {r.text[:500]}"
        data = r.json()
        assert "match_score" in data, "Response must have match_score"
        assert 0 <= data["match_score"] <= 100
        assert "must_have_skills" in data
        assert len(data["must_have_skills"]) > 0, "Should find at least 1 must-have skill"

    def test_skills_are_real_jd_terms(self, backend):
        """Skills returned must actually appear in the JD text (post-validation filter)."""
        r = requests.post(
            f"{backend}/analyze-deep",
            json={
                "jd_text": SAMPLE_JD,
                "company": "Globalization Partners",
                "role": "Senior AI/ML Engineer",
                "llm": "ollama",
            },
            timeout=120,
        )
        data = r.json()
        jd_lower = SAMPLE_JD.lower()
        for skill_obj in data.get("must_have_skills", []):
            skill = skill_obj.get("skill", "").lower()
            # Each skill term should appear somewhere in the JD
            assert any(
                token in jd_lower for token in skill.split() if len(token) >= 3
            ), f"Skill '{skill}' not found in JD — was fabricated"


class TestTailorResume:
    """Test that resume tailoring actually produces changes."""

    def test_returns_tailored_content(self, backend):
        r = requests.post(
            f"{backend}/tailor-resume",
            json={
                "jd_text": SAMPLE_JD,
                "role": "Senior AI/ML Engineer",
                "company": "Globalization Partners",
                "selected_skills": ["Python", "LLM", "RAG", "FastAPI"],
                "llm": "ollama",
            },
            timeout=300,
        )
        assert r.status_code == 200, f"tailor-resume failed: {r.text[:500]}"
        data = r.json()
        assert "experience" in data, "Must return experience array"
        assert len(data["experience"]) > 0, "Must have at least 1 experience entry"

    def test_has_actual_changes(self, backend):
        """THE CRITICAL TEST: at least some bullets must be edited or added — not all unchanged."""
        r = requests.post(
            f"{backend}/tailor-resume",
            json={
                "jd_text": SAMPLE_JD,
                "role": "Senior AI/ML Engineer",
                "company": "Globalization Partners",
                "selected_skills": ["Python", "LLM", "RAG", "FastAPI", "MLOps"],
                "llm": "ollama",
            },
            timeout=300,
        )
        data = r.json()

        all_bullets = [
            b
            for e in data.get("experience", [])
            for b in (e.get("bullets") or [])
        ]
        changed = [b for b in all_bullets if b.get("status") in ("edited", "added")]

        assert len(all_bullets) > 0, "Should have at least 1 bullet"
        assert len(changed) > 0, (
            f"REGRESSION: ALL {len(all_bullets)} bullets are 'unchanged' — tailoring is not working! "
            f"Sample bullet: {all_bullets[0] if all_bullets else 'none'}"
        )

    def test_tailored_summary_uses_full_company_name(self, backend):
        """Company name must not be abbreviated in tailored_summary."""
        r = requests.post(
            f"{backend}/tailor-resume",
            json={
                "jd_text": SAMPLE_JD,
                "role": "Senior AI/ML Engineer",
                "company": "Globalization Partners",
                "selected_skills": ["Python", "LLM", "RAG"],
                "llm": "ollama",
            },
            timeout=300,
        )
        data = r.json()
        summary = data.get("tailored_summary", "")

        # If summary mentions the company at all, it should use full name
        if "g-p" in summary.lower() or "gp" in summary.lower().replace(" ", ""):
            # Check that full name is also there
            assert "globalization partners" in summary.lower(), (
                f"Summary uses abbreviated company name. Got: '{summary[:200]}'"
            )


class TestGeneratePDF:
    """Test that PDF generation produces a real PDF, not HTML fallback."""

    def test_pdf_is_valid(self, backend):
        # First get tailored data
        r = requests.post(
            f"{backend}/tailor-resume",
            json={
                "jd_text": SAMPLE_JD,
                "role": "Senior AI/ML Engineer",
                "company": "Globalization Partners",
                "selected_skills": ["Python", "LLM", "RAG"],
                "llm": "ollama",
            },
            timeout=300,
        )
        assert r.status_code == 200
        tailored = r.json()
        tailored["_company"] = "Globalization Partners"
        tailored["_role"] = "Senior AI/ML Engineer"
        tailored["_jd"] = SAMPLE_JD

        # Generate PDF
        r2 = requests.post(f"{backend}/generate-pdf", json=tailored, timeout=180)
        assert r2.status_code == 200, f"generate-pdf failed: {r2.text[:500]}"
        assert r2.headers.get("content-type", "").startswith("application/pdf"), (
            f"Response should be PDF, got: {r2.headers.get('content-type')}"
        )
        pdf_bytes = r2.content
        assert pdf_bytes[:4] == b"%PDF", (
            f"Response should be a PDF file (starts with %PDF), got: {pdf_bytes[:20]}"
        )
        assert len(pdf_bytes) > 10000, (
            f"PDF too small ({len(pdf_bytes)} bytes) — likely empty or corrupt"
        )


class TestDashboardAutoload:
    """Playwright test: dashboard switches to tailor tab when opened with from=extension."""

    @pytest.fixture(scope="class")
    def playwright_browser(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            pytest.skip(
                "playwright not installed — run: pip install playwright && playwright install chromium"
            )
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            yield browser
            browser.close()

    def test_dashboard_shows_tailor_tab_from_extension(self, backend, playwright_browser):
        """When dashboard opens with ?from=extension&token=..., the tailor tab must be active."""
        # First POST a JD
        r = requests.post(
            f"{backend}/pending-jd",
            json={
                "jd": SAMPLE_JD,
                "role": "Senior AI/ML Engineer",
                "company": "Globalization Partners",
            },
        )
        token = r.json()["token"]

        ctx = playwright_browser.new_context()
        ctx.add_init_script("localStorage.setItem('lh_profile_id', 'default');")
        page = ctx.new_page()

        # Navigate to dashboard with the token
        page.goto(f"{backend}/dashboard?from=extension&token={token}")

        # Wait for the page to load and switch tabs
        page.wait_for_timeout(3000)  # Give autoloadFromExtension time to run

        # pg-tailor should be the active page
        tailor_page = page.locator("#pg-tailor")
        assert tailor_page.is_visible(), (
            "pg-tailor must be visible when dashboard opens with ?from=extension — was: HOME PAGE"
        )

        # The JD textarea should have content
        jd_textarea = page.locator("#tlr-jd")
        jd_value = jd_textarea.input_value()
        assert len(jd_value) > 100, (
            f"JD textarea should be populated with the JD text. Got {len(jd_value)} chars"
        )

        page.close()
        ctx.close()

    def test_dashboard_shows_tailor_tab_even_with_empty_jd(self, backend, playwright_browser):
        """When from=extension but JD is empty, tailor tab should still be shown (not home page)."""
        # POST empty JD
        r = requests.post(
            f"{backend}/pending-jd", json={"jd": "", "role": "", "company": ""}
        )
        token = r.json()["token"]

        ctx = playwright_browser.new_context()
        ctx.add_init_script("localStorage.setItem('lh_profile_id', 'default');")
        page = ctx.new_page()
        page.goto(f"{backend}/dashboard?from=extension&token={token}")
        page.wait_for_timeout(2000)

        # Should still show tailor tab (user can paste JD manually)
        tailor_page = page.locator("#pg-tailor")
        assert tailor_page.is_visible(), (
            "Tailor tab should be shown even with empty JD (not home page)"
        )

        page.close()
        ctx.close()
