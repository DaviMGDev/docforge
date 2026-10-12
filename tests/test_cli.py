import os
import pymupdf as fitz
import pytest
from typer.testing import CliRunner
from pdf2md.cli import app

runner = CliRunner()


@pytest.fixture
def dummy_pdf(tmp_path):
    pdf_path = str(tmp_path / "doc.pdf")
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    p.insert_text((50, 50), "Sample heading", fontsize=16)
    p.insert_text((50, 100), "Body text line for testing.", fontsize=11)
    doc.save(pdf_path)
    doc.close()
    return pdf_path


def test_missing_api_key_exit_code(dummy_pdf, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = runner.invoke(app, [dummy_pdf, "--model", "gpt-4o"])
    assert result.exit_code == 5
    assert "OPENAI_API_KEY is required" in result.stderr or "OPENAI_API_KEY is required" in result.stdout


def test_corrupted_pdf_exit_code(tmp_path):
    corrupt_pdf = str(tmp_path / "damaged.pdf")
    with open(corrupt_pdf, "wb") as f:
        f.write(b"not a valid pdf file")
    result = runner.invoke(app, [corrupt_pdf])
    assert result.exit_code == 2
    assert "Corrupted PDF" in result.stderr or "Corrupted PDF" in result.stdout


def test_invalid_page_range_exit_code(dummy_pdf):
    result = runner.invoke(app, [dummy_pdf, "-p", "50-60"])
    assert result.exit_code == 4


def test_auto_bypass_run_output_markdown(dummy_pdf, tmp_path):
    out_md = str(tmp_path / "output.md")
    result = runner.invoke(app, [dummy_pdf, "--auto-bypass", "-o", out_md])
    assert result.exit_code == 0
    assert os.path.exists(out_md)
    with open(out_md, "r", encoding="utf-8") as f:
        content = f.read()
    assert "Sample heading" in content


def test_format_both(dummy_pdf, tmp_path):
    out_md = str(tmp_path / "doc.md")
    result = runner.invoke(app, [dummy_pdf, "--auto-bypass", "--format", "both", "-o", out_md])
    assert result.exit_code == 0
    out_json = str(tmp_path / "doc.json")
    assert os.path.exists(out_md)
    assert os.path.exists(out_json)


def test_format_json_only(dummy_pdf, tmp_path):
    out_json = str(tmp_path / "doc.json")
    result = runner.invoke(app, [dummy_pdf, "--auto-bypass", "--format", "json", "-o", out_json])
    assert result.exit_code == 0
    assert os.path.exists(out_json)
    out_md = str(tmp_path / "doc.md")
    assert not os.path.exists(out_md)
