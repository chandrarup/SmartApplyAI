"""
Unit tests for clean_json() in backend/main.py.

Run with:
    cd SmartApplyAI
    pytest tests/unit/test_clean_json.py -v

Why this matters:
    clean_json() is the most fragile function in the backend. LLM outputs vary
    wildly — some models wrap in ```json``` fences, some add preamble text,
    some return plain JSON. A single character difference in the LLM's output
    style breaks the whole autofill response. These tests cover every real-world
    LLM output format we've observed.

Teaching note (Bug 9):
    The original implementation used naive string splitting:
        raw.split("```json")[1].split("```")[0]
    This fails when:
    1. LLM adds text before the JSON ("Here is your JSON: {...")
    2. LLM uses uppercase "```JSON"
    3. JSON contains nested objects (depth-unaware split breaks)

    The fix uses a character-walk with depth tracking — O(n), exact, handles
    all edge cases. These tests verify that fix works for every observed failure.
"""

import pytest
import json
import sys
import os

# Add backend to path so we can import main.py directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from main import clean_json


class TestBasicFormats:
    """Tests for the most common LLM output formats."""

    def test_plain_json_object(self):
        """Standard well-formed JSON with no wrapping — should pass straight through."""
        raw = '{"name": "Chandra", "email": "c@gmail.com"}'
        result = clean_json(raw)
        data = json.loads(result)
        assert data == {"name": "Chandra", "email": "c@gmail.com"}

    def test_plain_json_with_whitespace(self):
        """Indented/pretty-printed JSON."""
        raw = '''
{
  "First Name": "Chandra",
  "Last Name": "Daka"
}
        '''
        result = clean_json(raw)
        data = json.loads(result)
        assert data["First Name"] == "Chandra"

    def test_fenced_block_with_json_label(self):
        """Ollama and Claude both commonly output: ```json\\n{...}\\n```"""
        raw = '```json\n{"name": "Chandra"}\n```'
        result = clean_json(raw)
        assert json.loads(result) == {"name": "Chandra"}

    def test_fenced_block_without_language_label(self):
        """Some models use ``` (no 'json' label after the fence)."""
        raw = '```\n{"name": "Chandra"}\n```'
        result = clean_json(raw)
        assert json.loads(result) == {"name": "Chandra"}

    def test_fenced_block_uppercase_json(self):
        """Some models output ```JSON (capital J)."""
        raw = '```JSON\n{"name": "Chandra"}\n```'
        result = clean_json(raw)
        assert json.loads(result) == {"name": "Chandra"}

    def test_json_array(self):
        """Arrays should also be extractable (used in /analyze-deep response)."""
        raw = '["Python", "SQL", "PyTorch"]'
        result = clean_json(raw)
        data = json.loads(result)
        assert "Python" in data

    def test_json_array_with_preamble(self):
        """Array with preamble text."""
        raw = 'Here are the missing skills:\n["Python", "SQL", "PyTorch"]'
        result = clean_json(raw)
        data = json.loads(result)
        assert "Python" in data


class TestPreambleAndPostamble:
    """
    Tests for LLM outputs that wrap JSON in explanation text.
    This is the MOST COMMON failure mode in production (Bug 9).
    """

    def test_preamble_text(self):
        """LLM adds explanation before JSON — very common with Llama/Ollama models."""
        raw = 'Here is the JSON response you requested:\n\n{"name": "Chandra", "score": 85}'
        result = clean_json(raw)
        data = json.loads(result)
        assert data == {"name": "Chandra", "score": 85}

    def test_postamble_text(self):
        """LLM adds a note after the JSON — also common."""
        raw = '{"name": "Chandra"}\n\nNote: I used the candidate\'s profile data above.'
        result = clean_json(raw)
        data = json.loads(result)
        assert data == {"name": "Chandra"}

    def test_preamble_and_postamble(self):
        """LLM wraps JSON in explanation on both sides."""
        raw = 'Based on the profile provided:\n{"name": "Chandra"}\nThis should answer your questions.'
        result = clean_json(raw)
        data = json.loads(result)
        assert data == {"name": "Chandra"}

    def test_long_preamble(self):
        """Very long preamble text (as produced by verbose models)."""
        preamble = "I've analyzed the form fields carefully and matched them to the candidate's profile. " * 5
        raw = preamble + '\n\n{"First Name": "Chandra", "Email": "c@gmail.com"}'
        result = clean_json(raw)
        data = json.loads(result)
        assert data["First Name"] == "Chandra"

    def test_fenced_block_with_preamble(self):
        """Fenced code block should take priority over any JSON-like text before it."""
        raw = 'The answer is NOT {"broken": json\n```json\n{"valid": true}\n```'
        result = clean_json(raw)
        data = json.loads(result)
        assert data == {"valid": True}


class TestNestedAndComplex:
    """Tests for complex, deeply nested JSON structures."""

    def test_nested_object(self):
        """Nested JSON (experience array inside autofill response)."""
        raw = '{"experience": [{"company": "Accenture", "title": "AI Engineer"}]}'
        result = clean_json(raw)
        data = json.loads(result)
        assert data["experience"][0]["company"] == "Accenture"

    def test_deeply_nested(self):
        """3-level nesting — depth tracker must handle this correctly."""
        raw = '{"a": {"b": {"c": "deep value"}}}'
        result = clean_json(raw)
        data = json.loads(result)
        assert data["a"]["b"]["c"] == "deep value"

    def test_escaped_quotes_in_strings(self):
        """Escaped quotes inside strings must not confuse the depth tracker."""
        raw = '{"answer": "He said \\"Yes\\" to the question"}'
        result = clean_json(raw)
        data = json.loads(result)
        assert 'Yes' in data["answer"]

    def test_escaped_backslash(self):
        """Double backslash (escaped backslash) should not break escape tracking."""
        raw = r'{"path": "C:\\Users\\Chandra"}'
        result = clean_json(raw)
        data = json.loads(result)
        assert "Chandra" in data["path"]

    def test_curly_brace_in_string(self):
        """Curly braces inside string values must not throw off depth counting."""
        raw = '{"template": "Hello {name}, your code is {code}"}'
        result = clean_json(raw)
        data = json.loads(result)
        assert "{name}" in data["template"]

    def test_unicode_characters(self):
        """Unicode in values shouldn't break parsing."""
        raw = '{"city": "São Paulo", "country": "Brasil"}'
        result = clean_json(raw)
        data = json.loads(result)
        assert data["city"] == "São Paulo"


class TestRealAutofillFormats:
    """
    Tests using the EXACT shapes that the autofill endpoint produces and consumes.
    These mirror real observed LLM outputs from Ollama (llama3, mistral) and OpenAI.
    """

    def test_autofill_response_shape(self):
        """Real autofill response — keys are form field labels as returned by LLM."""
        raw = '''```json
{
  "First Name": "Chandra",
  "Last Name": "Daka",
  "Email": "chandrarupdaka@gmail.com",
  "Phone": "+1-713-555-0000",
  "Are you authorized to work in the US?": "Yes",
  "Do you require visa sponsorship?": "No",
  "Describe your experience with machine learning": "Experienced ML engineer with 2+ years building RAG pipelines and cell segmentation models using CellPose and StarDist at Roysam Lab."
}
```'''
        result = clean_json(raw)
        data = json.loads(result)
        assert data["First Name"] == "Chandra"
        assert data["Are you authorized to work in the US?"] == "Yes"
        assert "CellPose" in data["Describe your experience with machine learning"]

    def test_analyze_deep_response_shape(self):
        """Response shape from /analyze-deep endpoint."""
        raw = '''```json
{
  "match_score": 78,
  "matched_skills": ["Python", "PyTorch", "SQL"],
  "missing_skills": ["Rust", "Go"],
  "summary": "Strong ML background with relevant spatial biology experience."
}
```'''
        result = clean_json(raw)
        data = json.loads(result)
        assert data["match_score"] == 78
        assert "Python" in data["matched_skills"]

    def test_tailor_resume_response_shape(self):
        """Response shape from /tailor-resume endpoint."""
        raw = '''Here is the rewritten resume data:

```json
{
  "before": "Built ML models using PyTorch",
  "after": "Engineered spatial proteomics segmentation models using PyTorch and CellPose, achieving 15% improvement in F1 over baseline",
  "explanation": "Added quantitative outcome and specific tool names from JD"
}
```
'''
        result = clean_json(raw)
        data = json.loads(result)
        assert "CellPose" in data["after"]

    def test_ollama_mistral_typical_output(self):
        """
        Mistral models via Ollama often add 'Of course!' or 'Sure!' before JSON.
        This is the #1 real-world failure mode we've seen in testing.
        """
        raw = "Of course! Here's the filled form data:\n\n{\"First Name\": \"Chandra\", \"City\": \"Houston\"}"
        result = clean_json(raw)
        data = json.loads(result)
        assert data["City"] == "Houston"

    def test_llama3_typical_output(self):
        """
        Llama 3 models often wrap in ```json and add trailing commentary.
        """
        raw = """Sure, I'll fill these fields based on the profile:

```json
{"First Name": "Chandra", "State": "TX"}
```

Note: I've used the candidate's profile data for these answers."""
        result = clean_json(raw)
        data = json.loads(result)
        assert data["State"] == "TX"


class TestEdgeCasesAndErrorHandling:
    """Tests for edge cases and graceful failure."""

    def test_empty_string(self):
        """Empty input should return empty string (caller handles the error)."""
        result = clean_json("")
        assert result == ""

    def test_none_is_not_passed(self):
        """Verify the function handles empty string gracefully (None would be a caller bug)."""
        result = clean_json("   ")
        assert isinstance(result, str)

    def test_multiple_json_objects_takes_first(self):
        """If LLM outputs multiple JSON objects, return the first valid one."""
        raw = '{"first": true}\n{"second": true}'
        result = clean_json(raw)
        data = json.loads(result)
        assert data == {"first": True}

    def test_invalid_json_returns_raw_for_caller(self):
        """If no valid JSON found, return raw string so caller gets useful json.loads error."""
        raw = "This is just plain text with no JSON at all"
        result = clean_json(raw)
        assert isinstance(result, str)
        with pytest.raises(json.JSONDecodeError):
            json.loads(result)

    def test_truncated_json_does_not_crash(self):
        """
        Truncated JSON (from a 5500-char context limit hitting mid-response) should
        not crash clean_json() — it should return what it can, letting json.loads
        raise the error with context.
        """
        raw = '{"First Name": "Chandra", "incomplete_field": '  # Truncated mid-value
        result = clean_json(raw)
        assert isinstance(result, str)
        # Caller will get JSONDecodeError, which is correct behavior
        with pytest.raises(json.JSONDecodeError):
            json.loads(result)

    def test_only_opening_brace(self):
        """Just '{' should not crash."""
        raw = "{"
        result = clean_json(raw)
        assert isinstance(result, str)

    def test_large_json(self):
        """Large JSON (as seen with full profile responses) should parse correctly."""
        large_dict = {f"field_{i}": f"value_{i}" for i in range(200)}
        raw = json.dumps(large_dict)
        result = clean_json(raw)
        data = json.loads(result)
        assert data["field_0"] == "value_0"
        assert data["field_199"] == "value_199"


if __name__ == "__main__":
    # Allow running directly: python tests/unit/test_clean_json.py
    import subprocess
    subprocess.run(["pytest", __file__, "-v"], check=False)
