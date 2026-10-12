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
