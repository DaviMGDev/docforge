import asyncio
import os
import pymupdf as fitz
import pytest
from unittest.mock import patch, AsyncMock

from docforge.exceptions import LlmTruncationException, LlmInferenceException
from docforge.models.ast import DocumentChunkAST, ParagraphBlock
from docforge.models.cli_options import CliConfig, ConcurrencyConfig, LlmConfig, OutputFormat
from docforge.pipeline import PipelineRunner


@pytest.fixture
def multi_page_pdf(tmp_path):
    pdf_path = str(tmp_path / "test_4pages.pdf")
    doc = fitz.open()
    for i in range(1, 5):
        p = doc.new_page(width=595, height=842)
        p.insert_text((50, 50), f"Page {i} content text line.", fontsize=12)
    doc.save(pdf_path)
    doc.close()
    return pdf_path


@pytest.mark.asyncio
async def test_adaptive_chunk_subdivision_on_truncation(multi_page_pdf, tmp_path):
    out_md = str(tmp_path / "out.md")
    cache_dir = str(tmp_path / "cache")
    config = CliConfig(
        input_pdf_path=multi_page_pdf,
        output_path=out_md,
        cache_dir_path=cache_dir,
        chunk_size=4,
        adaptive_chunking=False,
        concurrency=ConcurrencyConfig(llm_concurrency=2, sliding_window_size=2),
        llm=LlmConfig(model_name="gemma4:cloud", context_window=16384, request_timeout_seconds=90.0),
    )

    runner = PipelineRunner(config)

    async def mock_llm_worker(chunk_index, page_numbers, page_images, raw_texts, available_assets, config, semaphore, status_callback=None):
        # If 4 pages, simulate token truncation
        if len(page_numbers) == 4:
            raise LlmTruncationException(f"Chunk with pages {page_numbers} hit token limit")

        sample_texts = {
            1: "Introduction to classical mechanics and Newtonian principles of particle motion in three dimensions.",
            2: "Analysis of gravitational forces and potential energy fields across variable reference frames.",
            3: "Kinematics of projectile trajectories under uniform acceleration in the presence of atmospheric drag.",
            4: "Thermodynamic laws and conservation of mechanical work during inelastic collisions of solid bodies.",
        }
        # For subdivided 2-page chunks, succeed
        return DocumentChunkAST(
            chunk_index=chunk_index,
            page_range=(min(page_numbers), max(page_numbers)),
            blocks=[
                ParagraphBlock(
                    id=f"p_{p}",
                    page=p,
                    content=sample_texts[p],
                )
                for p in page_numbers
            ],
            confidence_score=0.95,
            created_at="2026-10-12T00:00:00Z",
        )

    with patch("docforge.pipeline.process_chunk_with_llm", side_effect=mock_llm_worker):
        doc_ast, res_md, _ = await runner.run()

    assert doc_ast is not None
    assert len(doc_ast.blocks) == 4
    pages_in_blocks = [b.page for b in doc_ast.blocks]
    assert pages_in_blocks == [1, 2, 3, 4]
    assert os.path.exists(out_md)


@pytest.mark.asyncio
async def test_single_page_truncation_fails_with_actionable_error(multi_page_pdf, tmp_path):
    out_md = str(tmp_path / "out.md")
    config = CliConfig(
        input_pdf_path=multi_page_pdf,
        output_path=out_md,
        chunk_size=1,
        adaptive_chunking=False,
        concurrency=ConcurrencyConfig(llm_concurrency=1, sliding_window_size=1),
        llm=LlmConfig(model_name="gemma4:cloud", max_tokens=100),
    )
    runner = PipelineRunner(config)

    async def mock_single_page_truncation(*args, **kwargs):
        raise LlmTruncationException("Single page hit token limit")

    with patch("docforge.pipeline.process_chunk_with_llm", side_effect=mock_single_page_truncation):
        with pytest.raises(LlmInferenceException) as exc_info:
            await runner.run()
        assert "exceeded model maximum token capacity" in str(exc_info.value)
        assert "--max-tokens" in str(exc_info.value)
