"""Stage 3: Bounded Multimodal LLM Structural Parsing & Worker Pool.

Conforms to specs/contracts/llm_provider.contract, specs/algorithms/multimodal_worker_pool.pseudo,
and specs/contracts/material_json.contract.
"""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
import json
import os
import random
import re
import time
from typing import Any, Callable, Dict, List, Optional
import litellm
litellm.telemetry = False

from pdf2md.exceptions import LlmInferenceException, LlmTruncationException, MissingApiKeyException
from pdf2md.logging import get_logger
from pdf2md.models.ast import (
    Block,
    CalloutBlock,
    CodeBlock,
    DocumentChunkAST,
    HeadingBlock,
    ImageBlock,
    ListBlock,
    ListItem,
    MathBlock,
    ParagraphBlock,
    TableBlock,
    ThematicBreakBlock,
)
from pdf2md.models.cli_options import LlmConfig
from pdf2md.models.pipeline import AssetRecord, PageImageRecord, PageTextRecord

logger = get_logger("llm_worker")


def normalize_model_name(model_name: str) -> str:
    """Ensure Ollama models have proper provider prefix for LiteLLM."""
    if not model_name:
        return "ollama/gemma4:cloud"
    known_prefixes = (
        "ollama/", "ollama_chat/", "openai/", "anthropic/", "gemini/",
        "groq/", "mistral/", "bedrock/", "azure/", "openrouter/"
    )
    if any(model_name.startswith(p) for p in known_prefixes):
        return model_name

    # If model is gemma, llama, or contains colon tag like gemma4:cloud -> route to ollama
    if ":" in model_name or any(k in model_name.lower() for k in ["gemma", "llama", "qwen", "mistral", "deepseek", "phi"]):
        return f"ollama/{model_name}"

    return model_name


def validate_api_key_for_model(config: LlmConfig) -> None:
    """Validate required API key presence before executing pipeline."""
    model = config.model_name.lower()
    if config.api_key:
        return

    # Ollama models do not require an API key by default
    if model.startswith("ollama") or "gemma" in model or "llama" in model:
        return

    # Check common provider environment variables
    if "gpt" in model or "o1" in model or "o3" in model or "openai" in model:
        if not os.environ.get("OPENAI_API_KEY"):
            raise MissingApiKeyException(f"Error: OPENAI_API_KEY is required for model '{config.model_name}'")
    elif "claude" in model or "anthropic" in model:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise MissingApiKeyException(f"Error: ANTHROPIC_API_KEY is required for model '{config.model_name}'")
    elif "gemini" in model:
        if not os.environ.get("GEMINI_API_KEY") and not os.environ.get("GOOGLE_API_KEY"):
            raise MissingApiKeyException(f"Error: GEMINI_API_KEY is required for model '{config.model_name}'")


def build_system_prompt() -> str:
    """Construct system prompt enforcing structured JSON and CommonMark rules."""
    return """You are a high-fidelity document structure parser.
Analyze the provided PDF page images and native ground truth text.
Output a single JSON object conforming to this schema:
{
  "blocks": [
    {
      "type": "heading" | "paragraph" | "list" | "table" | "code" | "math" | "image" | "callout" | "thematic_break",
      "page": <integer 1-based>,
      "id": "<optional slug>",
      ...block specific fields
    }
  ]
}

Block-specific field definitions:
- "heading": "level" (1-6), "content" (string), "slug" (kebab-case anchor)
- "paragraph": "content" (CommonMark text)
- "list": "ordered" (true/false), "items": [{"content": string}]
- "table": "header": [string], "rows": [[string]], "caption": optional string, "raw_html": optional html table
- "code": "content" (string), "language" (string), "caption": optional string
- "math": "content" (LaTeX syntax without $), "math_mode": "inline" | "block"
- "image": "asset_path" (must match available assets), "alt_text" (string), "caption": optional string
- "callout": "callout_type" ("NOTE" | "TIP" | "IMPORTANT" | "WARNING" | "CAUTION"), "content" (string)
- "thematic_break": (no extra fields)

IMPORTANT:
1. Formulas must be transcribed into clean standard LaTeX.
2. Code blocks must infer correct language tag and preserve indentation.
3. Tables must transcribe cells faithfully. If cells span multiple columns or rows, provide clean HTML table in raw_html.
4. Images must reference matching paths from the provided asset manifest.
5. Return ONLY raw JSON without markdown code fences.
"""


def assemble_prompt_payload(
    page_numbers: List[int],
    page_images: List[PageImageRecord],
    raw_texts: List[PageTextRecord],
    available_assets: List[AssetRecord],
) -> List[Dict[str, Any]]:
    """Assemble multimodal messages list containing images and ground-truth text."""
    system_prompt = build_system_prompt()

    user_content: List[Dict[str, Any]] = []

    # Add images
    for img_rec in page_images:
        if os.path.exists(img_rec.image_path):
            with open(img_rec.image_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
            user_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            })

    # Add ground-truth text & asset manifests
    text_parts: List[str] = []
    for txt_rec in raw_texts:
        text_parts.append(f"<page_text page=\"{txt_rec.page_number}\">\n{txt_rec.full_text}\n</page_text>")

    for p in page_numbers:
        p_assets = [a for a in available_assets if a.page_number == p]
        if p_assets:
            manifest_json = json.dumps([{"path": a.file_path, "width": a.width_px, "height": a.height_px} for a in p_assets])
            text_parts.append(f"<available_assets page=\"{p}\">\n{manifest_json}\n</available_assets>")

    user_content.append({
        "type": "text",
        "text": "\n\n".join(text_parts),
    })

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]


def sanitize_json_text(raw_text: str) -> str:
    """Sanitize and repair common LLM JSON syntax issues (unescaped LaTeX backslashes, trailing commas)."""
    # 1. Escape known LaTeX commands starting with b, f, t that would otherwise be interpreted as control chars
    latex_cmd_pattern = r"\\(?=(?:frac|flat|forall|beta|bar|binom|begin|bf|times|theta|tau|tan|tanh|text|textbf|textit|tilde|top|to)\b)"
    s = re.sub(latex_cmd_pattern, r"\\\\", raw_text)

    # 2. Escape any backslash not followed by valid JSON escape sequence (\, ", /, b, f, n, r, t, uXXXX)
    s = re.sub(r"\\(?![/\"\\bfnrt]|u[0-9a-fA-F]{4})", r"\\\\", s)

    # 3. Strip trailing commas before object or array closures
    s = re.sub(r",\s*([\]}])", r"\1", s)
    return s


def repair_json_structure(cleaned: str) -> Optional[dict]:
    """Attempt structural repairs on malformed JSON like unclosed arrays or objects."""
    stripped = cleaned.rstrip()
    for suffix in [
        "}",
        "]}",
        "\"}]}",
        "\"}}",
        "}]}",
        "\"]}]}",
    ]:
        try:
            return json.loads(sanitize_json_text(stripped + suffix))
        except json.JSONDecodeError:
            continue
    return None


def parse_and_validate_response(
    raw_response: str,
    chunk_index: int,
    page_numbers: List[int],
    model_name: str,
) -> DocumentChunkAST:
    """Parse JSON string and construct validated DocumentChunkAST."""
    cleaned = raw_response.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    # Attempt regex cleanup of invalid LaTeX escapes and trailing commas if json fails
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        cleaned_repaired = sanitize_json_text(cleaned)
        try:
            data = json.loads(cleaned_repaired)
        except json.JSONDecodeError:
            data = repair_json_structure(cleaned)
            if data is None:
                raise

    raw_blocks = data.get("blocks", [])
    blocks: List[Block] = []

    for i, b in enumerate(raw_blocks):
        b_type = b.get("type")
        page = b.get("page", page_numbers[0])
        b_id = b.get("id") or f"blk_c{chunk_index}_p{page}_{i}"

        if b_type == "heading":
            slug = b.get("slug") or re.sub(r"[^\w\s-]", "", b.get("content", "")).lower().replace(" ", "-")
            blocks.append(
                HeadingBlock(
                    id=b_id,
                    page=page,
                    level=max(1, min(6, int(b.get("level", 1)))),
                    content=str(b.get("content", "")),
                    slug=slug or f"heading-{i}",
                )
            )
        elif b_type == "paragraph":
            blocks.append(
                ParagraphBlock(
                    id=b_id,
                    page=page,
                    content=str(b.get("content", "")),
                    is_continuation=bool(b.get("is_continuation", False)),
                )
            )
        elif b_type == "list":
            items = [ListItem(content=str(it.get("content", ""))) if isinstance(it, dict) else ListItem(content=str(it)) for it in b.get("items", [])]
            if not items:
                items = [ListItem(content="")]
            blocks.append(
                ListBlock(
                    id=b_id,
                    page=page,
                    ordered=bool(b.get("ordered", False)),
                    start_index=b.get("start_index", 1),
                    items=items,
                )
            )
        elif b_type == "table":
            blocks.append(
                TableBlock(
                    id=b_id,
                    page=page,
                    header=b.get("header", []),
                    rows=b.get("rows", []),
                    caption=b.get("caption"),
                    raw_html=b.get("raw_html"),
                )
            )
        elif b_type == "code":
            blocks.append(
                CodeBlock(
                    id=b_id,
                    page=page,
                    content=str(b.get("content", "")),
                    language=str(b.get("language", "")),
                    caption=b.get("caption"),
                )
            )
        elif b_type == "math":
            mmode = b.get("math_mode", "block")
            if mmode == "display" or mmode not in ("inline", "block"):
                mmode = "block"
            blocks.append(
                MathBlock(
                    id=b_id,
                    page=page,
                    content=str(b.get("content", "")),
                    math_mode=mmode,
                    equation_number=b.get("equation_number"),
                )
            )
        elif b_type == "image":
            blocks.append(
                ImageBlock(
                    id=b_id,
                    page=page,
                    asset_path=str(b.get("asset_path", "assets/image.png")),
                    alt_text=str(b.get("alt_text", "")),
                    caption=b.get("caption"),
                    width_px=b.get("width_px"),
                    height_px=b.get("height_px"),
                )
            )
        elif b_type == "callout":
            blocks.append(
                CalloutBlock(
                    id=b_id,
                    page=page,
                    callout_type=b.get("callout_type", "NOTE"),
                    title=b.get("title"),
                    content=str(b.get("content", "")),
                )
            )
        elif b_type == "thematic_break":
            blocks.append(ThematicBreakBlock(id=b_id, page=page))

    return DocumentChunkAST(
        chunk_index=chunk_index,
        page_range=(min(page_numbers), max(page_numbers)),
        bypass_used=False,
        fallback_used=False,
        llm_model=model_name,
        blocks=blocks,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


async def process_chunk_with_llm(
    chunk_index: int,
    page_numbers: List[int],
    page_images: List[PageImageRecord],
    raw_texts: List[PageTextRecord],
    available_assets: List[AssetRecord],
    config: LlmConfig,
    semaphore: asyncio.Semaphore,
    status_callback: Optional[Callable[[int, str], None]] = None,
) -> DocumentChunkAST:
    """Submit chunk to multimodal LLM pool bounded by Semaphore(3) with retry and fallback."""
    async with semaphore:
        logger.debug(f"[LLM POOL] Acquired worker semaphore for chunk {chunk_index} (pages {min(page_numbers)}-{max(page_numbers)})")
        if status_callback:
            status_callback(chunk_index, "LLM_INFERENCE")

        messages = assemble_prompt_payload(page_numbers, page_images, raw_texts, available_assets)

        attempt = 0
        last_error = ""

        normalized_model = normalize_model_name(config.model_name)
        while attempt < config.max_retries:
            attempt += 1
            call_start = time.monotonic()
            try:
                # Call litellm async completion
                kwargs: Dict[str, Any] = {
                    "model": normalized_model,
                    "messages": messages,
                    "temperature": config.temperature,
                    "max_tokens": config.max_tokens,
                    "timeout": config.request_timeout_seconds,
                    "response_format": {"type": "json_object"},
                }
                if config.context_window is not None:
                    if "extra_body" not in kwargs:
                        kwargs["extra_body"] = {}
                    kwargs["extra_body"]["num_ctx"] = config.context_window
                api_base = config.api_base or os.environ.get("PDF2MD_API_BASE") or (
                    os.environ.get("OLLAMA_API_BASE") if "ollama" in normalized_model else None
                )
                if api_base:
                    kwargs["api_base"] = api_base

                api_key = config.api_key or os.environ.get("PDF2MD_API_KEY") or (
                    os.environ.get("OLLAMA_API_KEY") if "ollama" in normalized_model else None
                )
                if api_key:
                    kwargs["api_key"] = api_key

                response = await litellm.acompletion(**kwargs)
                elapsed = time.monotonic() - call_start
                raw_text = response.choices[0].message.content or "{}"

                usage = getattr(response, "usage", None)
                p_tok = getattr(usage, "prompt_tokens", 0) or 0
                c_tok = getattr(usage, "completion_tokens", 0) or 0
                t_tok = getattr(usage, "total_tokens", 0) or 0

                finish_reason = response.choices[0].finish_reason if response.choices else None

                logger.info(
                    f"[LLM] Chunk {chunk_index}: {normalized_model} responded in {elapsed:.2f}s "
                    f"({len(raw_text)} bytes, {t_tok} tokens: {p_tok} prompt / {c_tok} completion)"
                )

                if finish_reason == "length":
                    logger.warning(
                        f"[LLM TRUNCATED] Chunk {chunk_index}: response truncated by token limit "
                        f"({c_tok}/{config.max_tokens} tokens, finish_reason='{finish_reason}')"
                    )
                    raise LlmTruncationException(
                        f"Chunk {chunk_index} (pages {min(page_numbers)}-{max(page_numbers)}) truncated "
                        f"by token limit ({c_tok}/{config.max_tokens}, finish_reason='{finish_reason}')"
                    )

                try:
                    return parse_and_validate_response(raw_text, chunk_index, page_numbers, normalized_model)
                except Exception as parse_err:
                    if c_tok > 0 and c_tok >= config.max_tokens:
                        logger.warning(
                            f"[LLM TRUNCATED] Chunk {chunk_index}: response truncated by token limit "
                            f"({c_tok}/{config.max_tokens} tokens)"
                        )
                        raise LlmTruncationException(
                            f"Chunk {chunk_index} (pages {min(page_numbers)}-{max(page_numbers)}) truncated "
                            f"by token limit ({c_tok}/{config.max_tokens}): {parse_err}"
                        ) from parse_err
                    raise parse_err

            except LlmTruncationException:
                # Fast fail so pipeline can dynamically subdivide chunk instead of wasting retries
                raise

            except Exception as e:
                elapsed = time.monotonic() - call_start
                last_error = str(e)
                if status_callback:
                    status_callback(chunk_index, f"RETRY_BACKOFF ({attempt}/{config.max_retries})")

                # Exponential backoff with jitter
                backoff_time = min(10.0, (1.5 ** attempt) + random.uniform(0.1, 0.5))
                logger.warning(
                    f"[LLM RETRY] Chunk {chunk_index}: Attempt {attempt}/{config.max_retries} failed after {elapsed:.2f}s "
                    f"({e}). Retrying in {backoff_time:.2f}s..."
                )
                await asyncio.sleep(backoff_time)

        # No fallback: fail explicitly when LLM retries are exhausted
        logger.error(
            f"[LLM FAILED] Chunk {chunk_index}: Retries exhausted for {normalized_model} "
            f"after {config.max_retries} attempts: {last_error}"
        )
        raise LlmInferenceException(
            f"Failed to process chunk {chunk_index} (pages {min(page_numbers)}-{max(page_numbers)}) "
            f"with model '{normalized_model}' after {config.max_retries} attempts: {last_error}"
        )
