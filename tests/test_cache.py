import os
import pytest
from pdf2md.cache.manager import CacheManager
from pdf2md.models.ast import DocumentChunkAST, ParagraphBlock
from pdf2md.models.cli_options import CliConfig, LlmConfig


@pytest.fixture
def dummy_config(tmp_path):
    return CliConfig(
        input_pdf_path=str(tmp_path / "test.pdf"),
        output_path=str(tmp_path / "out.md"),
        cache_dir_path=str(tmp_path / ".cache"),
        llm=LlmConfig(model_name="gpt-4o"),
    )


def test_save_and_load_chunk_cache(dummy_config):
    cache_mgr = CacheManager(
        pdf_path=dummy_config.input_pdf_path,
        pdf_sha256="1234567890abcdef" * 4,
        total_pages=10,
        config=dummy_config,
    )
    chunk = DocumentChunkAST(
        chunk_index=0,
        page_range=(1, 4),
        created_at="2026-10-12T00:00:00Z",
        blocks=[ParagraphBlock(page=1, content="Hello cached world")],
    )
    assert not cache_mgr.is_chunk_cached(0)
    cache_mgr.save_chunk_ast(chunk)

    assert cache_mgr.is_chunk_cached(0)
    loaded = cache_mgr.load_chunk_ast(0)
    assert loaded is not None
    assert loaded.chunk_index == 0
    assert loaded.blocks[0].content == "Hello cached world"


def test_invalidation_on_model_change(dummy_config):
    pdf_hash = "abcdef1234567890" * 4
    cache_mgr1 = CacheManager(
        pdf_path=dummy_config.input_pdf_path,
        pdf_sha256=pdf_hash,
        total_pages=10,
        config=dummy_config,
    )
    chunk = DocumentChunkAST(
        chunk_index=0,
        page_range=(1, 4),
        created_at="2026-10-12T00:00:00Z",
        blocks=[],
    )
    cache_mgr1.save_chunk_ast(chunk)
    assert cache_mgr1.is_chunk_cached(0)

    # Change model in config
    cfg_new_model = dummy_config.model_copy(deep=True)
    cfg_new_model.llm.model_name = "claude-3-5-sonnet"

    cache_mgr2 = CacheManager(
        pdf_path=dummy_config.input_pdf_path,
        pdf_sha256=pdf_hash,
        total_pages=10,
        config=cfg_new_model,
    )
    # Cache should be invalidated
    assert not cache_mgr2.is_chunk_cached(0)


def test_no_cache_flag_bypasses(dummy_config):
    pdf_hash = "9876543210abcdef" * 4
    cache_mgr = CacheManager(
        pdf_path=dummy_config.input_pdf_path,
        pdf_sha256=pdf_hash,
        total_pages=10,
        config=dummy_config,
    )
    chunk = DocumentChunkAST(
        chunk_index=0,
        page_range=(1, 4),
        created_at="2026-10-12T00:00:00Z",
        blocks=[],
    )
    cache_mgr.save_chunk_ast(chunk)
    assert cache_mgr.is_chunk_cached(0)

    cfg_no_cache = dummy_config.model_copy(deep=True)
    cfg_no_cache.no_cache = True

    cache_mgr_no_cache = CacheManager(
        pdf_path=dummy_config.input_pdf_path,
        pdf_sha256=pdf_hash,
        total_pages=10,
        config=cfg_no_cache,
    )
    assert not cache_mgr_no_cache.is_chunk_cached(0)


def test_purge_chunk_renders(dummy_config):
    cache_mgr = CacheManager(
        pdf_path=dummy_config.input_pdf_path,
        pdf_sha256="1111222233334444" * 4,
        total_pages=10,
        config=dummy_config,
    )
    render_dir = cache_mgr.get_chunk_render_dir(1)
    test_img = os.path.join(render_dir, "page_0001.png")
    with open(test_img, "wb") as f:
        f.write(b"PNG_DATA")

    assert os.path.exists(test_img)
    cache_mgr.purge_chunk_renders(1)
    assert not os.path.exists(test_img)


def test_page_range_cache_isolation_on_subset_runs(dummy_config):
    pdf_hash = "abcdef0123456789" * 4
    cache_mgr = CacheManager(
        pdf_path=dummy_config.input_pdf_path,
        pdf_sha256=pdf_hash,
        total_pages=1600,
        config=dummy_config,
    )

    # Run 1: Full or wider run, chunk 0 corresponds to pages 1-4
    chunk_run1 = DocumentChunkAST(
        chunk_index=0,
        page_range=(1, 4),
        created_at="2026-10-12T00:00:00Z",
        blocks=[ParagraphBlock(page=1, content="Content from pages 1-4")],
    )
    cache_mgr.save_chunk_ast(chunk_run1)

    # Verify run 1 file naming
    file_run1 = os.path.join(cache_mgr.chunks_dir, "chunk_p0001_p0004.json")
    assert os.path.exists(file_run1)
    assert cache_mgr.is_chunk_cached((1, 4))

    # Run 2: Subset run -p "22-23", relative chunk index is 0 but page range is (22, 23)
    # Must NOT collide with chunk index 0 from pages 1-4
    assert not cache_mgr.is_chunk_cached((22, 23))

    chunk_run2 = DocumentChunkAST(
        chunk_index=0,
        page_range=(22, 23),
        created_at="2026-10-12T00:00:00Z",
        blocks=[ParagraphBlock(page=22, content="Content from pages 22-23")],
    )
    cache_mgr.save_chunk_ast(chunk_run2)

    # Verify run 2 file naming
    file_run2 = os.path.join(cache_mgr.chunks_dir, "chunk_p0022_p0023.json")
    assert os.path.exists(file_run2)

    # Both chunks must coexist without collision
    assert cache_mgr.is_chunk_cached((1, 4))
    assert cache_mgr.is_chunk_cached((22, 23))

    loaded_run1 = cache_mgr.load_chunk_ast((1, 4))
    assert loaded_run1 is not None
    assert loaded_run1.blocks[0].content == "Content from pages 1-4"

    loaded_run2 = cache_mgr.load_chunk_ast((22, 23))
    assert loaded_run2 is not None
    assert loaded_run2.blocks[0].content == "Content from pages 22-23"


def test_purge_chunk_renders_with_page_range(dummy_config):
    cache_mgr = CacheManager(
        pdf_path=dummy_config.input_pdf_path,
        pdf_sha256="4444333322221111" * 4,
        total_pages=100,
        config=dummy_config,
    )
    render_dir = cache_mgr.get_chunk_render_dir((22, 23))
    assert os.path.basename(render_dir) == "chunk_p0022_p0023"
    test_img = os.path.join(render_dir, "page_0022.png")
    with open(test_img, "wb") as f:
        f.write(b"PNG_DATA")

    assert os.path.exists(test_img)
    cache_mgr.purge_chunk_renders((22, 23))
    assert not os.path.exists(test_img)

