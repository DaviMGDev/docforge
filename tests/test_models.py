import pytest
from pydantic import ValidationError
from pdf2md.models import (
    BlockType,
    BoundingBox,
    HeadingBlock,
    ParagraphBlock,
    TableBlock,
    DocumentChunkAST,
    DocumentAST,
    RangeSpec,
    ConcurrencyConfig,
    CliConfig,
    CacheManifest,
    ChunkCacheEntry,
    CalloutBlock,
    CalloutType,
)


def test_bounding_box_validation():
    bbox = BoundingBox(x0=10.0, y0=20.0, x1=50.0, y1=60.0)
    assert bbox.x0 == 10.0
    assert bbox.x1 == 50.0

    with pytest.raises(ValidationError):
        BoundingBox(x0=50.0, y0=20.0, x1=10.0, y1=60.0)

    with pytest.raises(ValidationError):
        BoundingBox(x0=-1.0, y0=0.0, x1=10.0, y1=10.0)


def test_heading_block():
    hb = HeadingBlock(page=1, level=2, content="Introduction", slug="introduction")
    assert hb.type == BlockType.HEADING
    assert hb.level == 2

    with pytest.raises(ValidationError):
        HeadingBlock(page=1, level=7, content="Invalid", slug="invalid")


def test_document_chunk_ast():
    chunk = DocumentChunkAST(
        chunk_index=0,
        page_range=(1, 4),
        created_at="2026-10-12T00:00:00Z",
        blocks=[
            HeadingBlock(page=1, level=1, content="Title", slug="title"),
            ParagraphBlock(page=1, content="Hello world"),
        ],
    )
    assert chunk.chunk_index == 0
    assert len(chunk.blocks) == 2

    with pytest.raises(ValidationError):
        DocumentChunkAST(chunk_index=-1, page_range=(1, 4), created_at="2026-10-12T00:00:00Z")

    with pytest.raises(ValidationError):
        DocumentChunkAST(chunk_index=0, page_range=(5, 2), created_at="2026-10-12T00:00:00Z")


def test_concurrency_config_invariants():
    cfg = ConcurrencyConfig(cpu_workers=4, llm_concurrency=3, sliding_window_size=2)
    assert cfg.sliding_window_size == 6  # auto-adjusted to llm_concurrency * 2


def test_callout_block_normalization_and_fallback():
    # Direct enum assignment
    b = CalloutBlock(page=1, callout_type=CalloutType.TIP, content="Use units")
    assert b.callout_type == CalloutType.TIP

    # Exact uppercase strings
    assert CalloutBlock(page=1, callout_type="NOTE", content="c").callout_type == CalloutType.NOTE
    assert CalloutBlock(page=1, callout_type="IMPORTANT", content="c").callout_type == CalloutType.IMPORTANT
    assert CalloutBlock(page=1, callout_type="WARNING", content="c").callout_type == CalloutType.WARNING
    assert CalloutBlock(page=1, callout_type="CAUTION", content="c").callout_type == CalloutType.CAUTION

    # Lowercase and whitespace stripping
    assert CalloutBlock(page=1, callout_type="note", content="c").callout_type == CalloutType.NOTE
    assert CalloutBlock(page=1, callout_type=" tip  ", content="c").callout_type == CalloutType.TIP
    assert CalloutBlock(page=1, callout_type="warning", content="c").callout_type == CalloutType.WARNING

    # Synonym mapping
    assert CalloutBlock(page=1, callout_type="info", content="c").callout_type == CalloutType.NOTE
    assert CalloutBlock(page=1, callout_type="INFORMATION", content="c").callout_type == CalloutType.NOTE
    assert CalloutBlock(page=1, callout_type="summary", content="c").callout_type == CalloutType.NOTE
    assert CalloutBlock(page=1, callout_type="hint", content="c").callout_type == CalloutType.TIP
    assert CalloutBlock(page=1, callout_type="notice", content="c").callout_type == CalloutType.IMPORTANT
    assert CalloutBlock(page=1, callout_type="alert", content="c").callout_type == CalloutType.WARNING
    assert CalloutBlock(page=1, callout_type="danger", content="c").callout_type == CalloutType.WARNING
    assert CalloutBlock(page=1, callout_type="warn", content="c").callout_type == CalloutType.WARNING

    # Arbitrary domain-specific strings fallback gracefully to NOTE without raising ValidationError
    assert CalloutBlock(page=1, callout_type="strategy", content="c").callout_type == CalloutType.NOTE
    assert CalloutBlock(page=1, callout_type="key concept", content="c").callout_type == CalloutType.NOTE
    assert CalloutBlock(page=1, callout_type="bio application", content="c").callout_type == CalloutType.NOTE
    assert CalloutBlock(page=1, callout_type="", content="c").callout_type == CalloutType.NOTE
    assert CalloutBlock(page=1, callout_type=None, content="c").callout_type == CalloutType.NOTE
    assert CalloutBlock(page=1, content="c").callout_type == CalloutType.NOTE

