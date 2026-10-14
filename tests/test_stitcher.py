import pytest
from docforge.models.ast import (
    BlockType,
    DocumentChunkAST,
    HeadingBlock,
    ParagraphBlock,
    TableBlock,
)
from docforge.stages.stitcher import stitch_chunks


def test_heal_split_paragraph_across_chunk_boundary():
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
    doc_ast = stitch_chunks([c0, c1], source_pdf_hash="abc", total_pages=16)
    assert len(doc_ast.blocks) == 1
    assert doc_ast.blocks[0].content == "in modern distributed architectures, reliability is essential."
    assert doc_ast.blocks[0].is_continuation is True


def test_preserve_separated_paragraphs_with_terminal_punctuation():
    c0 = DocumentChunkAST(
        chunk_index=0,
        page_range=(1, 8),
        created_at="2026-10-12T00:00:00Z",
        blocks=[ParagraphBlock(page=8, content="the experiment was successful.")],
    )
    c1 = DocumentChunkAST(
        chunk_index=1,
        page_range=(9, 16),
        created_at="2026-10-12T00:00:00Z",
        blocks=[ParagraphBlock(page=9, content="However, further testing is needed.")],
    )
    doc_ast = stitch_chunks([c0, c1], source_pdf_hash="abc", total_pages=16)
    assert len(doc_ast.blocks) == 2
    assert doc_ast.blocks[0].content == "the experiment was successful."
    assert doc_ast.blocks[1].content == "However, further testing is needed."


def test_hyphenated_word_rejoin():
    c0 = DocumentChunkAST(
        chunk_index=0,
        page_range=(1, 8),
        created_at="2026-10-12T00:00:00Z",
        blocks=[ParagraphBlock(page=8, content="transfor-")],
    )
    c1 = DocumentChunkAST(
        chunk_index=1,
        page_range=(9, 16),
        created_at="2026-10-12T00:00:00Z",
        blocks=[ParagraphBlock(page=9, content="mation of raw inputs")],
    )
    doc_ast = stitch_chunks([c0, c1], source_pdf_hash="abc", total_pages=16)
    assert len(doc_ast.blocks) == 1
    assert doc_ast.blocks[0].content == "transformation of raw inputs"


def test_reconcile_severed_table():
    c0 = DocumentChunkAST(
        chunk_index=0,
        page_range=(1, 8),
        created_at="2026-10-12T00:00:00Z",
        blocks=[TableBlock(page=8, header=["Name", "Value"], rows=[[f"Row{i}", str(i)] for i in range(5)])],
    )
    c1 = DocumentChunkAST(
        chunk_index=1,
        page_range=(9, 16),
        created_at="2026-10-12T00:00:00Z",
        blocks=[TableBlock(page=9, header=["Name", "Value"], rows=[[f"Row{i}", str(i)] for i in range(5, 9)])],
    )
    doc_ast = stitch_chunks([c0, c1], source_pdf_hash="abc", total_pages=16)
    assert len(doc_ast.blocks) == 1
    tbl = doc_ast.blocks[0]
    assert tbl.type == BlockType.TABLE
    assert tbl.header == ["Name", "Value"]
    assert len(tbl.rows) == 9


def test_suppressing_running_headers_and_footers():
    # ACM banner appears on pages 2, 3, 4, 5
    blocks = []
    for p in range(2, 6):
        blocks.append(ParagraphBlock(page=p, content="ACM Transactions on Graphics | Vol. 42"))
        blocks.append(HeadingBlock(page=p, level=2, content=f"Section on page {p}", slug=f"sec-{p}"))
        blocks.append(ParagraphBlock(page=p, content=f"Content on page {p}"))

    c0 = DocumentChunkAST(chunk_index=0, page_range=(2, 5), created_at="2026-10-12T00:00:00Z", blocks=blocks)
    doc_ast = stitch_chunks([c0], source_pdf_hash="abc", total_pages=5)

    # All 4 occurrences of ACM banner should be pruned
    assert not any("ACM Transactions" in b.content for b in doc_ast.blocks if hasattr(b, "content"))
    # The headings and normal paragraphs remain
    assert len([b for b in doc_ast.blocks if b.type == BlockType.HEADING]) == 4


def test_no_stitch_flag():
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
    doc_ast = stitch_chunks([c0, c1], source_pdf_hash="abc", total_pages=16, no_stitch=True)
    assert len(doc_ast.blocks) == 2
