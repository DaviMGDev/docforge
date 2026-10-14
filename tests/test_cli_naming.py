"""Unit tests for CLI output naming, page range suffixing, and collision protection.

Covers Issue #17.
"""

from __future__ import annotations

import os
from pathlib import Path
import pytest
from typer.testing import CliRunner

from docforge.cli import app, resolve_output_path
from docforge.exceptions import DiskWriteException

runner = CliRunner()


def test_default_naming_without_pages():
    out_md = resolve_output_path("manual.pdf", format_ext="md")
    assert out_md == "manual.md"

    out_json = resolve_output_path("manual.pdf", format_ext="json")
    assert out_json == "manual.json"


def test_page_range_suffixing():
    # Hyphenated range
    assert resolve_output_path("manual.pdf", pages="5-10", format_ext="md") == "manual_p5-10.md"
    assert resolve_output_path("physics.pdf", pages="1-491", format_ext="typ") == "physics_p1-491.typ"

    # Single page
    assert resolve_output_path("manual.pdf", pages="3", format_ext="html") == "manual_p3.html"

    # Bracketed slice notation [0:10]
    assert resolve_output_path("manual.pdf", pages="[0:10]", format_ext="md") == "manual_p0-10.md"


def test_configurable_output_template():
    # Template with {stem}, {pages}, {format}
    p1 = resolve_output_path(
        "report.pdf",
        pages="2-4",
        format_ext="md",
        output_template="{stem}_{pages}.{format}",
    )
    assert p1 == "report_p2-4.md"

    # Template with custom directory and no pages
    p2 = resolve_output_path(
        "report.pdf",
        pages=None,
        format_ext="typ",
        output_template="dist/{stem}.clean.{ext}",
    )
    assert p2 == "dist/report.clean.typ"

    # Template with {page_suffix}
    p3 = resolve_output_path(
        "thesis.pdf",
        pages="10-20",
        format_ext="tex",
        output_template="{stem}{page_suffix}.custom.{format}",
    )
    assert p3 == "thesis_p10-20.custom.tex"


def test_collision_avoidance_for_compilable_formats():
    # Formats that compile back to PDF (.typ, .tex, .html) default to _forge when full document
    # to avoid overwriting <stem>.pdf when typst/pdflatex/weasyprint compiles the output
    assert resolve_output_path("book.pdf", format_ext="typ") == "book_forge.typ"
    assert resolve_output_path("book.pdf", format_ext="tex") == "book_forge.tex"
    assert resolve_output_path("book.pdf", format_ext="html") == "book_forge.html"

    # With a page range, the page indicator already prevents collision with book.pdf
    assert resolve_output_path("book.pdf", pages="1-5", format_ext="typ") == "book_p1-5.typ"


def test_explicit_output_collision_raises_disk_write_exception(tmp_path: Path):
    source_pdf = tmp_path / "document.pdf"
    source_pdf.write_bytes(b"%PDF-1.4 mock")

    # Explicitly trying to overwrite the input source PDF raises DiskWriteException
    with pytest.raises(DiskWriteException) as excinfo:
        resolve_output_path(str(source_pdf), output=str(source_pdf))
    assert "cannot overwrite input source PDF" in str(excinfo.value)


def test_cli_help_displays_output_template():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "--output-template" in result.output
