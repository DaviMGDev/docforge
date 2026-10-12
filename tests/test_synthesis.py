import pytest
from pdf2md.models.ast import (
    BlockType,
    DocumentAST,
    HeadingBlock,
    ParagraphBlock,
    TableBlock,
    CalloutBlock,
    CalloutType,
)
from pdf2md.models.cli_options import PageMarkerMode
from pdf2md.models.toc import OutlineNode
from pdf2md.stages.synthesis import emit_markdown, format_page_marker
from pdf2md.stages.toc import generate_interactive_toc, transcribe_printed_toc


def test_page_marker_formatting():
    assert format_page_marker(42, PageMarkerMode.ANCHOR) == '<a id="page-42"></a>\n<!-- [Page 42] -->\n\n'
    assert format_page_marker(42, PageMarkerMode.BADGE) == '<small class="pdf-page-indicator">📄 Page 42</small>\n---\n\n'
    assert format_page_marker(42, PageMarkerMode.COMMENT) == '<!-- [Page 42] -->\n\n'
    assert format_page_marker(42, PageMarkerMode.NONE) == ''


def test_functional_toc_synthesized_from_bookmarks():
    outline = [
        OutlineNode(level=1, title="1. Introduction", page_number=3),
        OutlineNode(level=2, title="1.1 Problem Statement", page_number=4),
        OutlineNode(level=1, title="2. Methodology", page_number=12),
    ]
    ast_headings = [
        HeadingBlock(page=3, level=1, content="1. Introduction", slug="1-introduction"),
        HeadingBlock(page=4, level=2, content="1.1 Problem Statement", slug="11-problem-statement"),
        HeadingBlock(page=12, level=1, content="2. Methodology", slug="2-methodology"),
    ]
    toc_items = generate_interactive_toc(outline, ast_headings)
    assert len(toc_items) == 3
    assert toc_items[0].target_slug == "#1-introduction"
    assert toc_items[1].target_slug == "#11-problem-statement"
    assert toc_items[2].target_slug == "#2-methodology"

    doc_ast = DocumentAST(
        source_pdf_hash="abc",
        total_pages=20,
        selected_pages=[3, 4, 12],
        chunks_count=1,
        blocks=ast_headings,
        stitched_at="2026-10-12T00:00:00Z",
    )
    md = emit_markdown(doc_ast, toc_items, marker_mode=PageMarkerMode.ANCHOR, include_toc=True)
    assert "## Table of Contents\n\n" in md
    assert "- [1. Introduction](#1-introduction) *(p. 3)*\n" in md
    assert "  - [1.1 Problem Statement](#11-problem-statement) *(p. 4)*\n" in md
    assert "- [2. Methodology](#2-methodology) *(p. 12)*\n" in md


def test_deduplicate_repeated_ast_headings_in_toc():
    ast_headings = [
        HeadingBlock(page=1, level=1, content="Slide Topic", slug="slide-topic"),
        HeadingBlock(page=2, level=1, content="Slide Topic", slug="slide-topic-1"),
        HeadingBlock(page=3, level=2, content="Details", slug="details"),
    ]
    toc_items = generate_interactive_toc([], ast_headings)
    assert len(toc_items) == 2
    assert toc_items[0].title == "Slide Topic"
    assert toc_items[1].title == "Details"


def test_fallback_toc_link_to_page_anchor():
    outline = [OutlineNode(level=1, title="Appendix B", page_number=85)]
    ast_headings = []  # No heading on page 85
    toc_items = generate_interactive_toc(outline, ast_headings)
    assert len(toc_items) == 1
    assert toc_items[0].target_slug == "#page-85"


def test_printed_toc_transcription():
    raw_toc = (
        "Chapter 1: Overview ..................... 5\n"
        "Chapter 2: Deep Learning ................ 18"
    )
    transcribed = transcribe_printed_toc(raw_toc)
    assert "- [Chapter 1: Overview](#chapter-1-overview) *(p. 5)*" in transcribed
    assert "- [Chapter 2: Deep Learning](#chapter-2-deep-learning) *(p. 18)*" in transcribed


def test_disable_toc_flag():
    outline = [OutlineNode(level=1, title="Intro", page_number=1)]
    toc_items = generate_interactive_toc(outline, [])
    doc_ast = DocumentAST(
        source_pdf_hash="abc",
        total_pages=5,
        selected_pages=[1],
        chunks_count=1,
        blocks=[ParagraphBlock(page=1, content="Hello")],
        stitched_at="2026-10-12T00:00:00Z",
    )
    md = emit_markdown(doc_ast, toc_items, include_toc=False)
    assert "## Table of Contents" not in md


def test_callout_markdown_emission():
    blocks = [
        CalloutBlock(page=1, callout_type=CalloutType.NOTE, title="Reminder", content="Be careful with units."),
        CalloutBlock(page=1, callout_type=CalloutType.WARNING, content="High voltage danger!"),
    ]
    doc_ast = DocumentAST(
        source_pdf_hash="abc",
        total_pages=1,
        selected_pages=[1],
        chunks_count=1,
        blocks=blocks,
        stitched_at="2026-10-12T00:00:00Z",
    )
    md = emit_markdown(doc_ast, [], marker_mode=PageMarkerMode.NONE, include_toc=False)
    assert "> [!NOTE] **Reminder**\n> Be careful with units.\n" in md
    assert "> [!WARNING]\n> High voltage danger!\n" in md

