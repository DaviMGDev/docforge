import asyncio
import os
from unittest.mock import AsyncMock, patch
import pytest
from pdf2md.exceptions import MissingApiKeyException
from pdf2md.models.ast import BlockType, MathMode
from pdf2md.models.cli_options import LlmConfig
from pdf2md.models.pipeline import PageImageRecord, PageTextRecord
from pdf2md.stages.llm_worker import (
    build_raw_fallback_ast,
    parse_and_validate_response,
    process_chunk_with_llm,
    validate_api_key_for_model,
)


def test_missing_api_key_validation(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = LlmConfig(model_name="gpt-4o", api_key=None)
    with pytest.raises(MissingApiKeyException) as exc:
        validate_api_key_for_model(cfg)
    assert "OPENAI_API_KEY is required" in str(exc.value)


def test_parse_and_validate_response():
    mock_json = """
    {
      "blocks": [
        {"type": "heading", "page": 1, "level": 1, "content": "Introduction", "slug": "intro"},
        {"type": "paragraph", "page": 1, "content": "This is a paragraph."},
        {"type": "code", "page": 1, "content": "print('hello')", "language": "python"},
        {"type": "math", "page": 1, "content": "E=mc^2", "math_mode": "inline"},
        {"type": "table", "page": 1, "header": ["A", "B"], "rows": [["1", "2"]]}
      ]
    }
    """
    ast = parse_and_validate_response(mock_json, chunk_index=0, page_numbers=[1], model_name="gpt-4o")
    assert ast.chunk_index == 0
    assert len(ast.blocks) == 5
    assert ast.blocks[0].type == BlockType.HEADING
    assert ast.blocks[2].type == BlockType.CODE
    assert ast.blocks[2].language == "python"
    assert ast.blocks[3].type == BlockType.MATH
    assert ast.blocks[3].math_mode == MathMode.INLINE


@pytest.mark.asyncio
async def test_deterministic_fallback_on_llm_failure():
    cfg = LlmConfig(model_name="gpt-4o", max_retries=2, api_key="dummy")
    semaphore = asyncio.Semaphore(3)

    raw_texts = [PageTextRecord(page_number=1, full_text="Raw fallback text from page 1")]

    with patch("litellm.acompletion", side_effect=Exception("HTTP 500 Server Error")):
        result = await process_chunk_with_llm(
            chunk_index=0,
            page_numbers=[1],
            page_images=[],
            raw_texts=raw_texts,
            available_assets=[],
            config=cfg,
            semaphore=semaphore,
        )

    assert result.fallback_used is True
    assert len(result.blocks) == 1
    assert result.blocks[0].type == BlockType.RAW_FALLBACK
    assert "Raw fallback text from page 1" in result.blocks[0].content


@pytest.mark.asyncio
async def test_concurrency_bounding():
    semaphore = asyncio.Semaphore(3)
    active_concurrent = 0
    max_observed_concurrent = 0

    async def mock_acompletion(**kwargs):
        nonlocal active_concurrent, max_observed_concurrent
        active_concurrent += 1
        max_observed_concurrent = max(max_observed_concurrent, active_concurrent)
        await asyncio.sleep(0.05)
        active_concurrent -= 1
        mock_resp = AsyncMock()
        mock_resp.choices = [AsyncMock(message=AsyncMock(content='{"blocks": []}'))]
        return mock_resp

    cfg = LlmConfig(model_name="gpt-4o", api_key="dummy")
    with patch("litellm.acompletion", side_effect=mock_acompletion):
        tasks = [
            process_chunk_with_llm(
                chunk_index=i,
                page_numbers=[i + 1],
                page_images=[],
                raw_texts=[PageTextRecord(page_number=i + 1, full_text="")],
                available_assets=[],
                config=cfg,
                semaphore=semaphore,
            )
            for i in range(10)
        ]
        results = await asyncio.gather(*tasks)

    assert len(results) == 10
    assert max_observed_concurrent <= 3
