import asyncio
import os
from unittest.mock import AsyncMock, patch
import pytest
from pdf2md.exceptions import (
    LlmInferenceException,
    LlmTruncationException,
    MissingApiKeyException,
)
from pdf2md.models.ast import BlockType, MathMode
from pdf2md.models.cli_options import LlmConfig
from pdf2md.models.pipeline import PageImageRecord, PageTextRecord
from pdf2md.stages.llm_worker import (
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


def test_ollama_model_normalization_and_validation(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from pdf2md.stages.llm_worker import normalize_model_name

    assert normalize_model_name("gemma4:cloud") == "ollama/gemma4:cloud"
    assert normalize_model_name("ollama/gemma4:cloud") == "ollama/gemma4:cloud"
    assert normalize_model_name("gpt-4o") == "gpt-4o"

    # Ollama models do not require an API key
    cfg = LlmConfig(model_name="gemma4:cloud", api_key=None)
    validate_api_key_for_model(cfg)  # Should not raise


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


def test_parse_callout_blocks_with_non_standard_types_and_synonyms():
    mock_json = """
    {
      "blocks": [
        {"type": "callout", "page": 1, "callout_type": "note", "content": "Lowercase note"},
        {"type": "callout", "page": 1, "callout_type": "info", "content": "Info synonym"},
        {"type": "callout", "page": 1, "callout_type": "DANGER", "content": "Danger alert"},
        {"type": "callout", "page": 1, "callout_type": "strategy", "content": "Think step-by-step"},
        {"type": "callout", "page": 1, "callout_type": "Key Concept", "title": "Energy Conservation", "content": "Energy is conserved"},
        {"type": "callout", "page": 1, "content": "Missing callout_type default"}
      ]
    }
    """
    ast = parse_and_validate_response(mock_json, chunk_index=0, page_numbers=[1], model_name="test-model")
    assert len(ast.blocks) == 6

    # 0: lowercase "note" -> NOTE
    assert ast.blocks[0].type == BlockType.CALLOUT
    assert ast.blocks[0].callout_type.value == "NOTE"
    assert ast.blocks[0].title is None

    # 1: synonym "info" -> NOTE
    assert ast.blocks[1].callout_type.value == "NOTE"
    assert ast.blocks[1].title is None

    # 2: synonym "DANGER" -> WARNING
    assert ast.blocks[2].callout_type.value == "WARNING"

    # 3: domain-specific "strategy" without title -> NOTE with title="strategy"
    assert ast.blocks[3].callout_type.value == "NOTE"
    assert ast.blocks[3].title == "strategy"

    # 4: domain-specific "Key Concept" with explicit title -> NOTE with title="Energy Conservation"
    assert ast.blocks[4].callout_type.value == "NOTE"
    assert ast.blocks[4].title == "Energy Conservation"

    # 5: omitted callout_type -> NOTE
    assert ast.blocks[5].callout_type.value == "NOTE"



@pytest.mark.asyncio
async def test_llm_failure_raises_exception():
    cfg = LlmConfig(model_name="gpt-4o", max_retries=2, api_key="dummy")
    semaphore = asyncio.Semaphore(3)

    raw_texts = [PageTextRecord(page_number=1, full_text="Text from page 1")]

    with patch("litellm.acompletion", side_effect=Exception("HTTP 500 Server Error")):
        with pytest.raises(LlmInferenceException) as exc_info:
            await process_chunk_with_llm(
                chunk_index=0,
                page_numbers=[1],
                page_images=[],
                raw_texts=raw_texts,
                available_assets=[],
                config=cfg,
                semaphore=semaphore,
            )

    assert "Failed to process chunk 0" in str(exc_info.value)
    assert "HTTP 500 Server Error" in str(exc_info.value)


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


def test_parse_latex_formulas_with_unescaped_backslashes():
    raw_latex_json = r"""
    {
      "blocks": [
        {
          "type": "paragraph",
          "page": 1,
          "content": "Velocity is \vec{v} = \frac{dx}{dt} where \alpha and \theta are angles."
        },
        {
          "type": "math",
          "page": 1,
          "content": "\lim_{\Delta t \to 0} \frac{\Delta \vec{r}}{\Delta t}",
          "math_mode": "display"
        }
      ]
    }
    """
    ast = parse_and_validate_response(
        raw_latex_json, chunk_index=0, page_numbers=[1], model_name="ollama/gemma4:cloud"
    )
    assert len(ast.blocks) == 2
    assert r"\vec{v}" in ast.blocks[0].content
    assert r"\frac{dx}{dt}" in ast.blocks[0].content
    assert r"\lim_{\Delta t \to 0}" in ast.blocks[1].content


def test_parse_latex_formulas_mixed_and_double_escaped_backslashes():
    # Mixed JSON with valid double backslashes and invalid single backslashes
    mixed_json = """
    {
      "blocks": [
        {
          "type": "paragraph",
          "page": 1,
          "content": "Double escaped \\\\vec{v} and \\\\sigma alongside single \\vec{a} and \\rho."
        },
        {
          "type": "math",
          "page": 1,
          "content": "\\\\nabla \\\\times \\\\vec{B} = \\mu_0 \\vec{J} + \\epsilon_0 \\frac{\\\\partial \\vec{E}}{\\\\partial t}",
          "math_mode": "block"
        }
      ]
    }
    """
    ast = parse_and_validate_response(
        mixed_json, chunk_index=0, page_numbers=[1], model_name="ollama/gemma4:cloud"
    )
    assert len(ast.blocks) == 2
    assert r"\vec{v}" in ast.blocks[0].content
    assert r"\sigma" in ast.blocks[0].content
    assert r"\vec{a}" in ast.blocks[0].content
    assert r"\rho" in ast.blocks[0].content
    assert r"\nabla" in ast.blocks[1].content
    assert r"\mu_0" in ast.blocks[1].content



@pytest.mark.asyncio
async def test_llm_truncation_raises_truncation_exception():
    cfg = LlmConfig(model_name="ollama/gemma4:cloud", max_retries=1, max_tokens=100)
    semaphore = asyncio.Semaphore(1)

    mock_resp = AsyncMock()
    mock_resp.choices = [
        AsyncMock(
            message=AsyncMock(content='{"blocks": [{"type": "paragraph", "content": "truncated text'),
            finish_reason="length",
        )
    ]
    mock_resp.usage = AsyncMock(prompt_tokens=50, completion_tokens=100, total_tokens=150)

    with patch("litellm.acompletion", return_value=mock_resp):
        with pytest.raises(LlmTruncationException) as exc_info:
            await process_chunk_with_llm(
                chunk_index=0,
                page_numbers=[1, 2],
                page_images=[],
                raw_texts=[PageTextRecord(page_number=1, full_text="")],
                available_assets=[],
                config=cfg,
                semaphore=semaphore,
            )
        assert "truncated" in str(exc_info.value).lower()
