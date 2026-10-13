"""Tests for DocumentAST partitioning and multi-file export."""

from datetime import datetime
from pathlib import Path
from unittest.mock import patch
import pytest

from pdf2md.models.ast import (
    DocumentAST,
    DocumentChunkAST,
    HeadingBlock,
    ParagraphBlock,
)
from pdf2md.models.cli_options import CliConfig, LlmConfig
from pdf2md.pipeline import PipelineRunner
from pdf2md.stages.partition_export import (
    generate_partition_index,
    partition_ast,
)


@pytest.fixture
def multi_chapter_ast():
    return DocumentAST(
        source_pdf_hash="hash123",
        total_pages=4,
        selected_pages=[1, 2, 3, 4],
        chunks_count=2,
        stitched_at=datetime.now().isoformat(),
        blocks=[
            HeadingBlock(page=1, level=1, content="Introduction", slug="intro"),
            ParagraphBlock(page=1, content="Introductory content."),
            HeadingBlock(page=2, level=2, content="Background", slug="background"),
            ParagraphBlock(page=2, content="Background details."),
            HeadingBlock(page=3, level=1, content="Architecture", slug="architecture"),
            ParagraphBlock(page=3, content="Architecture description."),
            ParagraphBlock(page=4, content="Page 4 continuation."),
        ],
    )


def test_partition_by_page(multi_chapter_ast):
    parts = partition_ast(multi_chapter_ast, split_by="page")
    assert len(parts) == 4
    assert str(parts[0][0]) == "page-001"
    assert str(parts[1][0]) == "page-002"
    assert str(parts[2][0]) == "page-003"
    assert str(parts[3][0]) == "page-004"
    assert parts[0][1].selected_pages == [1]


def test_partition_by_chapter(multi_chapter_ast):
    parts = partition_ast(multi_chapter_ast, split_by="chapter")
    assert len(parts) == 2
    assert "intro" in str(parts[0][0])
    assert "architecture" in str(parts[1][0])
    assert len(parts[0][1].blocks) == 4  # H1, Para, H2, Para
    assert len(parts[1][1].blocks) == 3  # H1, Para, Para


def test_partition_by_section(multi_chapter_ast):
    parts = partition_ast(multi_chapter_ast, split_by="section")
    assert len(parts) == 3
    assert "intro" in str(parts[0][0])
    assert "background" in str(parts[1][0])
    assert "architecture" in str(parts[2][0])


def test_partition_hierarchical(multi_chapter_ast):
    parts = partition_ast(multi_chapter_ast, split_by="chapter/page")
    # Chapter 1 spans pages 1 & 2 -> 2 parts
    # Chapter 2 spans pages 3 & 4 -> 2 parts
    assert len(parts) == 4
    rel_paths = [str(p[0]) for p in parts]
    assert any("page-001" in p for p in rel_paths)
    assert any("page-003" in p for p in rel_paths)


def test_invalid_split_strategy(multi_chapter_ast):
    with pytest.raises(ValueError, match="Unknown split strategy"):
        partition_ast(multi_chapter_ast, split_by="invalid_strategy")


def test_generate_partition_index(multi_chapter_ast):
    parts = partition_ast(multi_chapter_ast, split_by="chapter")
    index_md = generate_partition_index(parts, file_extension=".md")
    assert "# Table of Contents" in index_md
    assert "[Introduction](01-intro.md)" in index_md
    assert "[Architecture](02-architecture.md)" in index_md


@pytest.mark.asyncio
async def test_pipeline_partitioned_export(tmp_path):
    import pymupdf as fitz

    pdf_file = tmp_path / "test.pdf"
    doc = fitz.open()
    p1 = doc.new_page()
    p1.insert_text((50, 50), "Page 1")
    p2 = doc.new_page()
    p2.insert_text((50, 50), "Page 2")
    doc.save(str(pdf_file))
    doc.close()

    out_dir = tmp_path / "output_dir"
    config = CliConfig(
        input_pdf_path=str(pdf_file),
        output_path=str(out_dir),
        output_formats=["md", "json", "html"],
        split_by="page",
        llm=LlmConfig(model_name="ollama/mock"),
    )
    runner = PipelineRunner(config)

    async def mock_worker(*args, **kwargs):
        page_nums = kwargs.get("page_numbers", [1, 2])
        return DocumentChunkAST(
            source_pdf_hash="dummy",
            chunk_index=kwargs.get("chunk_index", 0),
            page_range=page_nums,
            total_pages=2,
            created_at=datetime.now().isoformat(),
            blocks=[ParagraphBlock(page=p, content=f"Text on page {p}") for p in page_nums],
        )

    with patch("pdf2md.pipeline.process_chunk_with_llm", side_effect=mock_worker):
        await runner.run()

    assert out_dir.is_dir()
    files = sorted([f.name for f in out_dir.iterdir()])
    assert "page-001.md" in files
    assert "page-001.json" in files
    assert "page-001.html" in files
    assert "page-002.md" in files
    assert "index.md" in files
    assert "index.html" in files
    assert "SUMMARY.md" in files
