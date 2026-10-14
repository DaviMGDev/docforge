import os
import pymupdf as fitz
import pytest
from typer.testing import CliRunner
from docforge.cli import app

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


def test_zero_page_range_fails_fast_with_actionable_error(dummy_pdf):
    result = runner.invoke(app, [dummy_pdf, "-p", "0-1"])
    assert result.exit_code == 4
    combined = (result.stderr or "") + (result.stdout or "") + result.output
    assert "starts at 0" in combined
    assert "1-based" in combined


def test_cli_flags_context_window_and_timeout(dummy_pdf, tmp_path):
    out_md = str(tmp_path / "out.md")
    result = runner.invoke(
        app,
        [
            dummy_pdf,
            "--auto-bypass",
            "--context-window",
            "16384",
            "--timeout",
            "90.0",
            "-o",
            out_md,
        ],
    )
    assert result.exit_code == 0
    assert os.path.exists(out_md)


def test_auto_bypass_run_output_markdown(dummy_pdf, tmp_path):
    out_md = str(tmp_path / "output.md")
    result = runner.invoke(app, [dummy_pdf, "--auto-bypass", "-o", out_md])
    assert result.exit_code == 0
    assert os.path.exists(out_md)
    # Verify deprecation warning is emitted to user
    combined = (result.stderr or "") + (result.stdout or "") + result.output
    assert "Warning: --auto-bypass is deprecated" in combined
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


@pytest.mark.asyncio
async def test_stage1_progress_tracks_selected_pages(tmp_path):
    pdf_path = str(tmp_path / "ten_pages.pdf")
    doc = fitz.open()
    for i in range(10):
        p = doc.new_page(width=595, height=842)
        p.insert_text((50, 50), f"Page {i + 1} content", fontsize=12)
    doc.save(pdf_path)
    doc.close()

    from unittest.mock import patch
    from docforge.models.ast import DocumentChunkAST, ParagraphBlock
    from docforge.models.cli_options import CliConfig, LlmConfig, RangeSpec
    from docforge.models.pipeline import ExtractionResult
    from docforge.pipeline import PipelineRunner

    cfg = CliConfig(
        input_pdf_path=pdf_path,
        output_path=str(tmp_path / "out.md"),
        pages_spec=RangeSpec(raw_input="2-3"),
        no_cache=True,
        llm=LlmConfig(model_name="ollama/llama3"),
    )
    pipeline = PipelineRunner(cfg)

    partition_events = []

    def progress_cb(stage: str, current: int, total: int):
        if stage == "partition":
            partition_events.append((current, total))

    mock_ast = DocumentChunkAST(
        chunk_index=0,
        page_range=(2, 3),
        created_at="2026-10-12T00:00:00Z",
        blocks=[ParagraphBlock(page=2, content="Mock text")],
    )

    with patch("docforge.pipeline.extract_chunk") as mock_extract, patch(
        "docforge.pipeline.process_chunk_with_llm", return_value=mock_ast
    ):
        mock_extract.return_value = ExtractionResult(
            chunk_index=0,
            page_numbers=[2, 3],
            rendered_images=[],
            raw_texts=[],
            extracted_assets=[],
        )
        await pipeline.run(progress_callback=progress_cb)

    assert len(partition_events) >= 1
    current, total = partition_events[-1]
    assert current == 2
    assert total == 2
    assert (current / total) * 100.0 == 100.0

