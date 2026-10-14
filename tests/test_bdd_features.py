"""BDD Feature Verification Suite.

Directly tests and maps all scenarios defined across specs/features/*.feature.
"""

import asyncio
import os
import pymupdf as fitz
import pytest
from unittest.mock import AsyncMock, patch
from PIL import Image

from docforge.exceptions import (
    CorruptedPdfException,
    InvalidPageRangeException,
    LlmInferenceException,
    MissingApiKeyException,
)
from docforge.models.ast import (
    BlockType,
    CodeBlock,
    DocumentAST,
    DocumentChunkAST,
    HeadingBlock,
    ImageBlock,
    MathBlock,
    MathMode,
    ParagraphBlock,
    TableBlock,
)
from docforge.models.cli_options import CliConfig, LlmConfig, OutputFormat, PageMarkerMode
from docforge.models.pipeline import ChunkDefinition, DocumentClassification, PageImageRecord, PageTextRecord
from docforge.models.toc import OutlineNode
from docforge.stages.classifier import build_bypass_chunk_ast, classify_chunk
from docforge.stages.extract import (
    compute_file_sha256,
    extract_and_dedupe_assets,
    extract_raw_page_text,
    open_pdf_document,
    render_page_image,
)
from docforge.stages.llm_worker import (
    parse_and_validate_response,
    process_chunk_with_llm,
    validate_api_key_for_model,
)
from docforge.stages.partition import (
    calculate_adaptive_chunk_size,
    parse_page_selection,
    partition_into_chunks,
)
from docforge.stages.stitcher import stitch_chunks
from docforge.stages.synthesis import emit_markdown, format_page_marker
from docforge.stages.toc import generate_interactive_toc, transcribe_printed_toc


# -----------------------------------------------------------------------------
# 1. Page Selection & Chunk Partitioning (specs/features/page_selection_and_chunking.feature)
# -----------------------------------------------------------------------------

def test_feature_page_selection_and_chunking():
    # Scenario: Default full document chunking with 4 pages per chunk
    pages = parse_page_selection(None, 500)
    assert len(pages) == 500
    chunks = partition_into_chunks(pages, max_chunk_size=4)
    assert len(chunks) == 125
    for c in chunks:
        assert len(c.page_numbers) == 4

    # Scenario: Print-style page range parsing
    assert parse_page_selection("1-10", 500) == list(range(1, 11))
    assert parse_page_selection("5", 500) == [5]
    assert parse_page_selection("1, 3, 5-7", 500) == [1, 3, 5, 6, 7]
    assert len(parse_page_selection("1-200, 401-500", 500)) == 300
    assert parse_page_selection("10-15, 12-18", 500) == list(range(10, 19))

    # Scenario: Python slice notation parsing
    assert parse_page_selection("[0:10]", 500) == list(range(1, 11))
    assert parse_page_selection("[:5]", 500) == list(range(1, 6))
    assert parse_page_selection("[-10:]", 500) == list(range(491, 501))
    assert parse_page_selection("[0:5] + [9:12]", 500) == [1, 2, 3, 4, 5, 10, 11, 12]
    assert parse_page_selection("[0:10:2]", 500) == [1, 3, 5, 7, 9]

    # Scenario: Disjoint ranges produce separated chunks
    disjoint_pages = parse_page_selection("1-5, 20-25", 500)
    chunks_disjoint = partition_into_chunks(disjoint_pages, max_chunk_size=8)
    assert len(chunks_disjoint) == 2
    assert chunks_disjoint[0].page_numbers == [1, 2, 3, 4, 5]
    assert chunks_disjoint[1].page_numbers == [20, 21, 22, 23, 24, 25]

    # Scenario: Out of bounds page specification raises error
    with pytest.raises(InvalidPageRangeException):
        parse_page_selection("600-700", 500)


# -----------------------------------------------------------------------------
# 2. Deterministic Local Extraction (specs/features/deterministic_extraction.feature)
# -----------------------------------------------------------------------------

def test_feature_deterministic_extraction(tmp_path):
    pdf_path = str(tmp_path / "extraction_test.pdf")
    doc = fitz.open()

    img = Image.new("RGB", (100, 100), color="green")
    img_path = str(tmp_path / "img.png")
    img.save(img_path)

    # 12x12 icon
    icon = Image.new("RGB", (12, 12), color="red")
    icon_path = str(tmp_path / "icon.png")
    icon.save(icon_path)

    p1 = doc.new_page(width=595, height=842)
    p1.insert_text((50, 50), "Deterministic text", fontsize=14)

    p2 = doc.new_page(width=595, height=842)
    p2.insert_image(fitz.Rect(50, 50, 150, 150), filename=img_path)
    p2.insert_image(fitz.Rect(50, 200, 62, 212), filename=icon_path)

    p3 = doc.new_page(width=595, height=842)
    p3.insert_image(fitz.Rect(50, 50, 150, 150), filename=img_path)

    doc.save(pdf_path)
    doc.close()

    doc_opened = open_pdf_document(pdf_path)

    # Scenario: High-resolution page image rendering
    out_img = str(tmp_path / "p1.png")
    rec = render_page_image(doc_opened, 1, dpi=150, output_path=out_img)
    assert abs(rec.width_px - int(595 * 150 / 72)) <= 2

    # Scenario: Raw text span extraction without OCR
    txt_rec = extract_raw_page_text(doc_opened, 1)
    assert "Deterministic text" in txt_rec.full_text

    # Scenario: Embedded image stream extraction and deduplication
    assets_dir = str(tmp_path / "assets_bdd")
    dedup = {}
    a2 = extract_and_dedupe_assets(doc_opened, 2, assets_dir, dedup)
    # The 12x12 icon is filtered out
    assert len(a2) == 1
    assert a2[0].is_deduplicated is False

    a3 = extract_and_dedupe_assets(doc_opened, 3, assets_dir, dedup)
    assert len(a3) == 1
    assert a3[0].is_deduplicated is True

    doc_opened.close()


# -----------------------------------------------------------------------------
# 3. Fast-Path Hybrid Bypass Classifier (specs/features/fast_path_bypass.feature)
# -----------------------------------------------------------------------------

def test_feature_fast_path_bypass(tmp_path):
    pdf_path = str(tmp_path / "bypass_test.pdf")
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    p.insert_text((50, 50), "Single column plain story paragraph.", fontsize=12)
    doc.save(pdf_path)
    doc.close()

    doc_opened = fitz.open(pdf_path)
    chunk = ChunkDefinition(chunk_index=0, start_page=1, end_page=1, page_numbers=[1], is_contiguous=True)

    # Scenario: Trivial prose page bypasses LLM
    cl_bypass = classify_chunk(doc_opened, chunk, auto_bypass_enabled=True)
    assert cl_bypass == DocumentClassification.TRIVIAL_PROSE
    ast = build_bypass_chunk_ast(doc_opened, chunk)
    assert ast.bypass_used is True

    # Scenario: Auto-bypass disabled by default
    cl_default = classify_chunk(doc_opened, chunk, auto_bypass_enabled=False)
    assert cl_default == DocumentClassification.COMPLEX

    doc_opened.close()


# -----------------------------------------------------------------------------
# 4. Multimodal LLM Structural Parsing (specs/features/multimodal_llm_parsing.feature)
# -----------------------------------------------------------------------------

def test_feature_multimodal_llm_parsing():
    # Scenario: Structured JSON Output conforming to schema & block types
    sample_json = """
    {
      "blocks": [
        {"type": "table", "page": 1, "raw_html": "<table><tr><th colspan=2>Header</th></tr></table>"},
        {"type": "math", "page": 1, "content": "E = mc^2", "math_mode": "inline"},
        {"type": "math", "page": 1, "content": "f(x) = \\\\int_0^1 x dx", "math_mode": "block"},
        {"type": "code", "page": 1, "language": "python", "content": "def foo(): pass"},
        {"type": "image", "page": 2, "asset_path": "assets/asset_p2_01.png", "caption": "Figure 1: Architecture", "alt_text": "Architecture diagram"}
      ]
    }
    """
    chunk_ast = parse_and_validate_response(sample_json, chunk_index=0, page_numbers=[1, 2], model_name="gpt-4o")
    assert len(chunk_ast.blocks) == 5
    assert chunk_ast.blocks[0].type == BlockType.TABLE
    assert chunk_ast.blocks[0].raw_html == "<table><tr><th colspan=2>Header</th></tr></table>"
    assert chunk_ast.blocks[1].type == BlockType.MATH
    assert chunk_ast.blocks[1].math_mode == MathMode.INLINE
    assert chunk_ast.blocks[2].type == BlockType.MATH
    assert chunk_ast.blocks[2].math_mode == MathMode.BLOCK
    assert chunk_ast.blocks[3].type == BlockType.CODE
    assert chunk_ast.blocks[3].language == "python"
    assert chunk_ast.blocks[4].type == BlockType.IMAGE
    assert chunk_ast.blocks[4].asset_path == "assets/asset_p2_01.png"


# -----------------------------------------------------------------------------
# 5. Cross-Chunk AST Stitching (specs/features/cross_chunk_stitching.feature)
# -----------------------------------------------------------------------------

def test_feature_cross_chunk_stitching():
    # Scenario: Healing split paragraph across chunk boundary
    c0 = DocumentChunkAST(
        chunk_index=0,
        page_range=(1, 8),
        created_at="2026-10-12T00:00:00Z",
        blocks=[ParagraphBlock(page=8, content="in modern distributed")],
    )
    c1 = DocumentChunkAST(
        chunk_index=1,
        page_range=(9, 16),
        created_at="2026-10-12T00:00:00Z",
        blocks=[ParagraphBlock(page=9, content="architectures, reliability is essential.")],
    )
    res = stitch_chunks([c0, c1], "hash", 16)
    assert len(res.blocks) == 1
    assert res.blocks[0].content == "in modern distributed architectures, reliability is essential."

    # Scenario: Preserving separated paragraphs when terminal punctuation exists
    c0_term = DocumentChunkAST(
        chunk_index=0,
        page_range=(1, 8),
        created_at="2026-10-12T00:00:00Z",
        blocks=[ParagraphBlock(page=8, content="the experiment was successful.")],
    )
    c1_term = DocumentChunkAST(
        chunk_index=1,
        page_range=(9, 16),
        created_at="2026-10-12T00:00:00Z",
        blocks=[ParagraphBlock(page=9, content="However, further testing is needed.")],
    )
    res_term = stitch_chunks([c0_term, c1_term], "hash", 16)
    assert len(res_term.blocks) == 2

    # Scenario: Hyphenated word rejoin at page boundary
    c0_hyphen = DocumentChunkAST(
        chunk_index=0,
        page_range=(1, 8),
        created_at="2026-10-12T00:00:00Z",
        blocks=[ParagraphBlock(page=8, content="transfor-")],
    )
    c1_hyphen = DocumentChunkAST(
        chunk_index=1,
        page_range=(9, 16),
        created_at="2026-10-12T00:00:00Z",
        blocks=[ParagraphBlock(page=9, content="mation of raw inputs")],
    )
    res_hyphen = stitch_chunks([c0_hyphen, c1_hyphen], "hash", 16)
    assert res_hyphen.blocks[0].content == "transformation of raw inputs"

    # Scenario: Reconciling severed table across chunk boundary
    c0_tbl = DocumentChunkAST(
        chunk_index=0,
        page_range=(1, 8),
        created_at="2026-10-12T00:00:00Z",
        blocks=[TableBlock(page=8, header=["Name", "Value"], rows=[["A", "1"], ["B", "2"], ["C", "3"], ["D", "4"], ["E", "5"]])],
    )
    c1_tbl = DocumentChunkAST(
        chunk_index=1,
        page_range=(9, 16),
        created_at="2026-10-12T00:00:00Z",
        blocks=[TableBlock(page=9, header=["Name", "Value"], rows=[["F", "6"], ["G", "7"], ["H", "8"], ["I", "9"]])],
    )
    res_tbl = stitch_chunks([c0_tbl, c1_tbl], "hash", 16)
    assert len(res_tbl.blocks) == 1
    assert len(res_tbl.blocks[0].rows) == 9

    # Scenario: Disabling stitching with --no-stitch flag
    res_nostitch = stitch_chunks([c0, c1], "hash", 16, no_stitch=True)
    assert len(res_nostitch.blocks) == 2


# -----------------------------------------------------------------------------
# 6. Markdown Synthesis & Navigation (specs/features/markdown_synthesis_and_toc.feature)
# -----------------------------------------------------------------------------

def test_feature_markdown_synthesis_and_navigation():
    # Scenario Outline: Page boundary marker formatting
    assert format_page_marker(42, PageMarkerMode.ANCHOR) == '<a id="page-42"></a>\n<!-- [Page 42] -->\n\n'
    assert format_page_marker(42, PageMarkerMode.BADGE) == '<small class="pdf-page-indicator">📄 Page 42</small>\n---\n\n'
    assert format_page_marker(42, PageMarkerMode.COMMENT) == '<!-- [Page 42] -->\n\n'
    assert format_page_marker(42, PageMarkerMode.NONE) == ''

    # Scenario: Functional Table of Contents synthesized from PDF bookmarks
    outline = [
        OutlineNode(level=1, title="1. Introduction", page_number=3),
        OutlineNode(level=2, title="1.1 Problem Statement", page_number=4),
        OutlineNode(level=1, title="2. Methodology", page_number=12),
    ]
    headings = [
        HeadingBlock(page=3, level=1, content="1. Introduction", slug="1-introduction"),
        HeadingBlock(page=4, level=2, content="1.1 Problem Statement", slug="11-problem-statement"),
        HeadingBlock(page=12, level=1, content="2. Methodology", slug="2-methodology"),
    ]
    toc = generate_interactive_toc(outline, headings)
    doc_ast = DocumentAST(
        source_pdf_hash="hash",
        total_pages=50,
        selected_pages=[3, 4, 12],
        chunks_count=1,
        blocks=headings,
        stitched_at="2026-10-12T00:00:00Z",
    )
    md = emit_markdown(doc_ast, toc, marker_mode=PageMarkerMode.ANCHOR, include_toc=True)
    assert "## Table of Contents" in md
    assert "- [1. Introduction](#1-introduction) *(p. 3)*" in md
    assert "  - [1.1 Problem Statement](#11-problem-statement) *(p. 4)*" in md
    assert "- [2. Methodology](#2-methodology) *(p. 12)*" in md

    # Scenario: Fallback TOC link to page anchor when heading slug is missing
    outline_fallback = [OutlineNode(level=1, title="Appendix B", page_number=85)]
    toc_fallback = generate_interactive_toc(outline_fallback, [])
    assert toc_fallback[0].target_slug == "#page-85"

    # Scenario: Dotted leader line transcription
    raw_printed = "Chapter 1: Overview ..................... 5\nChapter 2: Deep Learning ................ 18"
    transcribed = transcribe_printed_toc(raw_printed)
    assert "- [Chapter 1: Overview](#chapter-1-overview) *(p. 5)*" in transcribed
    assert "- [Chapter 2: Deep Learning](#chapter-2-deep-learning) *(p. 18)*" in transcribed

    # Scenario: Disabling Table of Contents generation
    md_notoc = emit_markdown(doc_ast, toc, include_toc=False)
    assert "## Table of Contents" not in md_notoc


# -----------------------------------------------------------------------------
# 7. CLI & Error Handling (specs/features/cli_and_error_handling.feature)
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_feature_cli_error_handling(monkeypatch, tmp_path):
    # Scenario: Missing API key validation
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(MissingApiKeyException) as exc:
        validate_api_key_for_model(LlmConfig(model_name="gpt-4o"))
    assert "OPENAI_API_KEY is required" in str(exc.value)

    # Scenario: Corrupted PDF document error handling
    corrupt_file = str(tmp_path / "damaged.pdf")
    with open(corrupt_file, "wb") as f:
        f.write(b"not a pdf")
    with pytest.raises(CorruptedPdfException):
        open_pdf_document(corrupt_file)

    # Scenario: Multi-retry failure raises LlmInferenceException (no raw-text fallback)
    with patch("litellm.acompletion", side_effect=Exception("Repeated HTTP 500 errors")):
        with pytest.raises(LlmInferenceException):
            await process_chunk_with_llm(
                chunk_index=4,
                page_numbers=[4],
                page_images=[],
                raw_texts=[PageTextRecord(page_number=4, full_text="Raw page text.")],
                available_assets=[],
                config=LlmConfig(model_name="gpt-4o", max_retries=1, api_key="dummy"),
                semaphore=asyncio.Semaphore(1),
            )
