"""Unit and integration tests for custom system prompts and transcription instructions (Issue #1)."""

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from docforge.cache.manager import CacheManager
from docforge.cli import app
from docforge.models.ast import DocumentChunkAST, ParagraphBlock
from docforge.models.cache import InvalidationReason
from docforge.models.cli_options import CliConfig, LlmConfig
from docforge.stages.llm_worker import assemble_prompt_payload, build_system_prompt


@pytest.fixture
def dummy_pdf(tmp_path: Path) -> Path:
    pdf_file = tmp_path / "test.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 sample")
    return pdf_file


def test_build_system_prompt_without_instructions():
    prompt = build_system_prompt()
    assert "You are a high-fidelity document structure parser." in prompt
    assert "Return ONLY raw JSON" in prompt
    assert "ADDITIONAL USER TRANSCRIPTION INSTRUCTIONS:" not in prompt


def test_build_system_prompt_with_custom_instructions():
    instructions = "Ignore all marginalia, stamps, and handwritten pen annotations."
    prompt = build_system_prompt(custom_instructions=instructions)
    assert "You are a high-fidelity document structure parser." in prompt
    assert "ADDITIONAL USER TRANSCRIPTION INSTRUCTIONS:" in prompt
    assert instructions in prompt
    assert "Return ONLY raw JSON" in prompt


def test_assemble_prompt_payload_includes_instructions():
    payload = assemble_prompt_payload(
        page_numbers=[1],
        page_images=[],
        raw_texts=[],
        available_assets=[],
        custom_instructions="Do not transcribe watermarks.",
    )
    system_msg = payload[0]
    assert system_msg["role"] == "system"
    assert "Do not transcribe watermarks." in system_msg["content"]


def test_cli_inline_instructions_flag(dummy_pdf: Path):
    runner = CliRunner()
    mock_doc = MagicMock()
    mock_doc.__len__.return_value = 1
    mock_ast = MagicMock()
    mock_ast.blocks = []

    with patch("docforge.cli.PipelineRunner") as mock_pipeline:
        mock_pipeline.return_value.run = AsyncMock(return_value=(mock_ast, "out.md", None))
        with patch("docforge.cli.open_pdf_document", return_value=mock_doc):
            result = runner.invoke(app, [str(dummy_pdf), "--instructions", "Custom inline rule"])
            assert result.exit_code == 0
            config = mock_pipeline.call_args[0][0]
            assert config.instructions == "Custom inline rule"
            assert config.llm.instructions == "Custom inline rule"


def test_cli_prompt_alias_flag(dummy_pdf: Path):
    runner = CliRunner()
    mock_doc = MagicMock()
    mock_doc.__len__.return_value = 1
    mock_ast = MagicMock()
    mock_ast.blocks = []

    with patch("docforge.cli.PipelineRunner") as mock_pipeline:
        mock_pipeline.return_value.run = AsyncMock(return_value=(mock_ast, "out.md", None))
        with patch("docforge.cli.open_pdf_document", return_value=mock_doc):
            result = runner.invoke(app, [str(dummy_pdf), "--prompt", "Prompt alias rule"])
            assert result.exit_code == 0
            config = mock_pipeline.call_args[0][0]
            assert config.instructions == "Prompt alias rule"


def test_cli_prompt_file_flag(dummy_pdf: Path, tmp_path: Path):
    prompt_file = tmp_path / "instructions.txt"
    prompt_file.write_text("Ignore handwritten scribbles and draft watermarks.")

    runner = CliRunner()
    mock_doc = MagicMock()
    mock_doc.__len__.return_value = 1
    mock_ast = MagicMock()
    mock_ast.blocks = []

    with patch("docforge.cli.PipelineRunner") as mock_pipeline:
        mock_pipeline.return_value.run = AsyncMock(return_value=(mock_ast, "out.md", None))
        with patch("docforge.cli.open_pdf_document", return_value=mock_doc):
            result = runner.invoke(app, [str(dummy_pdf), "--prompt-file", str(prompt_file)])
            assert result.exit_code == 0
            config = mock_pipeline.call_args[0][0]
            assert "Ignore handwritten scribbles and draft watermarks." in config.instructions


def test_cli_missing_prompt_file_exits_with_error(dummy_pdf: Path):
    runner = CliRunner()
    mock_doc = MagicMock()
    mock_doc.__len__.return_value = 1

    with patch("docforge.cli.open_pdf_document", return_value=mock_doc):
        result = runner.invoke(app, [str(dummy_pdf), "--prompt-file", "/nonexistent/path/rules.md"])
        assert result.exit_code == 2
        assert "Prompt file not found" in result.output


def test_env_instructions_loading(dummy_pdf: Path):
    runner = CliRunner()
    mock_doc = MagicMock()
    mock_doc.__len__.return_value = 1
    mock_ast = MagicMock()
    mock_ast.blocks = []

    # DOCFORGE_INSTRUCTIONS
    with patch.dict(os.environ, {"DOCFORGE_INSTRUCTIONS": "Docforge env rule"}, clear=True):
        with patch("docforge.cli.PipelineRunner") as mock_pipeline:
            mock_pipeline.return_value.run = AsyncMock(return_value=(mock_ast, "out.md", None))
            with patch("docforge.cli.open_pdf_document", return_value=mock_doc):
                result = runner.invoke(app, [str(dummy_pdf)])
                assert result.exit_code == 0
                config = mock_pipeline.call_args[0][0]
                assert config.instructions == "Docforge env rule"

    # PDF2MD_INSTRUCTIONS fallback
    with patch.dict(os.environ, {"PDF2MD_INSTRUCTIONS": "Legacy env rule"}, clear=True):
        with patch("docforge.cli.PipelineRunner") as mock_pipeline:
            mock_pipeline.return_value.run = AsyncMock(return_value=(mock_ast, "out.md", None))
            with patch("docforge.cli.open_pdf_document", return_value=mock_doc):
                result = runner.invoke(app, [str(dummy_pdf)])
                assert result.exit_code == 0
                config = mock_pipeline.call_args[0][0]
                assert config.instructions == "Legacy env rule"


def test_cache_invalidation_on_instructions_change(tmp_path: Path, dummy_pdf: Path):
    cache_dir = tmp_path / "cache"

    config1 = CliConfig(
        input_pdf_path=str(dummy_pdf),
        output_path=str(tmp_path / "out.md"),
        cache_dir_path=str(cache_dir),
        instructions="Initial instruction set A",
        llm=LlmConfig(model_name="gpt-4o", instructions="Initial instruction set A"),
    )
    cm1 = CacheManager(str(dummy_pdf), "b" * 64, 1, config1)
    v1 = cm1.manifest.prompt_version
    assert v1.startswith("1.0-")

    chunk = DocumentChunkAST(
        chunk_index=0,
        page_range=[1, 1],
        created_at="2026-10-13T00:00:00Z",
        blocks=[ParagraphBlock(page=1, content="Extracted text")],
    )
    cm1.save_chunk_ast(chunk)
    assert cm1.is_chunk_cached((1, 1))

    # Second run with different instructions invalidates cache
    config2 = CliConfig(
        input_pdf_path=str(dummy_pdf),
        output_path=str(tmp_path / "out.md"),
        cache_dir_path=str(cache_dir),
        instructions="Modified instruction set B",
        llm=LlmConfig(model_name="gpt-4o", instructions="Modified instruction set B"),
    )
    cm2 = CacheManager(str(dummy_pdf), "b" * 64, 1, config2)
    v2 = cm2.manifest.prompt_version
    assert v2 != v1
    assert not cm2.is_chunk_cached((1, 1))
