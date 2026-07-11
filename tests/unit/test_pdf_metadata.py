"""PDF metadata hygiene tests (rule 5): real author/title, neutral
creator/producer, no injection via metadata values.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

import main  # noqa: E402

TEX = "\\documentclass{article}\n\\begin{document}\nBody\n\\end{document}\n"


def test_metadata_injected_before_begin_document():
    out = main._inject_pdf_metadata(TEX, author="Jane Doe", title="Jane Doe - Resume")
    assert out.index("\\hypersetup{") < out.index("\\begin{document}")
    assert "pdfauthor={Jane Doe}" in out
    assert "pdftitle={Jane Doe - Resume}" in out
    # Neutral toolchain fields — no localhost, no bot-ish generator strings.
    assert "pdfcreator={}" in out
    assert "pdfproducer={}" in out


def test_empty_author_leaves_tex_untouched():
    assert main._inject_pdf_metadata(TEX, author="", title="X") == TEX


def test_missing_title_defaults_to_author_resume():
    out = main._inject_pdf_metadata(TEX, author="Jane Doe", title="")
    assert "pdftitle={Jane Doe - Resume}" in out


def test_metadata_values_are_sanitized_against_tex_injection():
    hostile = "Jane} \\input{/etc/passwd} {Doe\n\r"
    out = main._inject_pdf_metadata(TEX, author=hostile, title=hostile)
    block = out.split("\\begin{document}")[0]
    assert "\\input" not in block.replace("\\hypersetup", "")
    assert "{" not in block.split("pdfauthor={")[1].split("}")[0]


def test_no_document_marker_prepends_block():
    out = main._inject_pdf_metadata("plain tex", author="Jane Doe", title="T")
    assert out.startswith("\n% Metadata hygiene only")
    assert out.endswith("plain tex")
