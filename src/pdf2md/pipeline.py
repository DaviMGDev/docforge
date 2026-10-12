"""End-to-end Pipeline Execution Engine.

Orchestrates all 5 stages, caching, concurrency, and metrics.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import time
from typing import Callable, Dict, List, Optional
import pymupdf as fitz

from pdf2md.cache.manager import CacheManager
from pdf2md.exceptions import (
    CorruptedPdfException,
    InvalidPageRangeException,
    LlmInferenceException,
    LlmTruncationException,
    MissingApiKeyException,
)
from pdf2md.logging import get_logger
from pdf2md.models.ast import DocumentAST, DocumentChunkAST, HeadingBlock
from pdf2md.models.cli_options import CliConfig, OutputFormat
from pdf2md.models.pipeline import (
    AssetRecord,
    ChunkDefinition,
    DocumentClassification,
    PipelineMetrics,
)
from pdf2md.stages.classifier import build_bypass_chunk_ast, classify_chunk
from pdf2md.stages.extract import (
    compute_file_sha256,
    extract_chunk,
    extract_native_outline,
    open_pdf_document,
)
from pdf2md.stages.llm_worker import (
    process_chunk_with_llm,
    validate_api_key_for_model,
)
from pdf2md.stages.partition import (
    calculate_adaptive_chunk_size,
    parse_page_selection,
    partition_into_chunks,
)
from pdf2md.stages.stitcher import stitch_chunks
from pdf2md.stages.synthesis import emit_markdown, write_output_document
from pdf2md.stages.toc import generate_interactive_toc


class PipelineRunner:
    """Executes the pdf2md conversion pipeline."""

    def __init__(self, config: CliConfig):
        self.config = config
        self.interrupted = False
        self._active_tasks: List[asyncio.Task] = []

    def handle_sigint(self) -> None:
        """Handle SIGINT cancellation gracefully."""
        self.interrupted = True
        for t in self._active_tasks:
            if not t.done():
                t.cancel()

    async def run(
        self,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
        worker_callback: Optional[Callable[[int, str], None]] = None,
    ) -> tuple[DocumentAST, Optional[str], Optional[str]]:
        """Run the complete pipeline asynchronously."""
        start_time = time.time()

        # Step 1: Open PDF & inspect document
        doc = open_pdf_document(self.config.input_pdf_path)
        total_source_pages = len(doc)
        pdf_sha256 = compute_file_sha256(self.config.input_pdf_path)
        native_outline = extract_native_outline(doc)

        # Step 2: Page selection & Partitioning
        range_str = self.config.pages_spec.raw_input if self.config.pages_spec else None
        selected_pages = parse_page_selection(range_str, total_source_pages)

        # Validate API key before rendering pages
        validate_api_key_for_model(self.config.llm)

        # Step 3: Initialize Cache Manager
        cache_mgr = CacheManager(
            pdf_path=self.config.input_pdf_path,
            pdf_sha256=pdf_sha256,
            total_pages=total_source_pages,
            config=self.config,
        )

        # Adaptive chunk sizing
        chunk_size = self.config.chunk_size
        if self.config.adaptive_chunking:
            chunk_size = calculate_adaptive_chunk_size(doc, selected_pages, self.config.chunk_size)

        chunks = partition_into_chunks(selected_pages, max_chunk_size=chunk_size)
        total_chunks = len(chunks)

        logger = get_logger("pipeline")
        logger.info(
            f"[Stage 1] Selected {len(selected_pages)} of {total_source_pages} pages; "
            f"partitioned into {total_chunks} chunks (chunk size {chunk_size})"
        )
        for ch in chunks:
            logger.debug(
                f"[Stage 1] Chunk {ch.chunk_index} boundary: pages {min(ch.page_numbers)}-{max(ch.page_numbers)} ({len(ch.page_numbers)} pages)"
            )

        if progress_callback:
            progress_callback("partition", len(selected_pages), len(selected_pages))

        # Prepare worker concurrency
        semaphore = asyncio.Semaphore(self.config.concurrency.llm_concurrency)
        dedup_table: Dict[str, AssetRecord] = {}
        completed_chunks: List[DocumentChunkAST] = []

        from collections import deque
        work_queue: deque[ChunkDefinition] = deque(chunks)
        total_work = len(chunks)
        next_chunk_index = max([ch.chunk_index for ch in chunks], default=0) + 1

        tasks_in_flight: Dict[asyncio.Task, ChunkDefinition] = {}
        processed_count = 0

        async def drain_completed(done_set: set[asyncio.Task]):
            nonlocal processed_count, total_work, next_chunk_index
            for t in done_set:
                chunk = tasks_in_flight.pop(t)
                if t in self._active_tasks:
                    self._active_tasks.remove(t)
                try:
                    res_ast = t.result()
                    completed_chunks.append(res_ast)
                    cache_mgr.save_chunk_ast(res_ast)
                    logger.debug(f"[CACHE SAVE] chunk {res_ast.chunk_index} saved to cache")
                    chunk_range = (min(chunk.page_numbers), max(chunk.page_numbers))
                    cache_mgr.purge_chunk_renders(chunk_range)
                    processed_count += 1
                    if progress_callback:
                        progress_callback("chunks", processed_count, total_work)
                except LlmTruncationException as trunc_err:
                    chunk_range = (min(chunk.page_numbers), max(chunk.page_numbers))
                    cache_mgr.purge_chunk_renders(chunk_range)
                    if len(chunk.page_numbers) > 1:
                        logger.warning(
                            f"[LLM ADAPTIVE SPLIT] Chunk {chunk.chunk_index} (pages {chunk_range[0]}-{chunk_range[1]}) "
                            f"exceeded token limit. Subdividing into smaller sub-chunks..."
                        )
                        pages = chunk.page_numbers
                        mid = len(pages) // 2
                        p1, p2 = pages[:mid], pages[mid:]
                        c1 = ChunkDefinition(
                            chunk_index=chunk.chunk_index,
                            start_page=p1[0],
                            end_page=p1[-1],
                            page_numbers=p1,
                            is_contiguous=(p1[-1] - p1[0] + 1 == len(p1)),
                        )
                        c2 = ChunkDefinition(
                            chunk_index=next_chunk_index,
                            start_page=p2[0],
                            end_page=p2[-1],
                            page_numbers=p2,
                            is_contiguous=(p2[-1] - p2[0] + 1 == len(p2)),
                        )
                        next_chunk_index += 1
                        total_work += 1
                        work_queue.appendleft(c2)
                        work_queue.appendleft(c1)
                    else:
                        logger.error(f"[LLM FAILED] Single-page chunk {chunk.page_numbers[0]} exceeded token limit: {trunc_err}")
                        raise LlmInferenceException(
                            f"Page {chunk.page_numbers[0]} exceeded model maximum token capacity. "
                            f"Please increase --max-tokens."
                        ) from trunc_err

        try:
            while (work_queue or tasks_in_flight) and not self.interrupted:
                # Dispatch tasks up to sliding window limit
                while work_queue and len(tasks_in_flight) < self.config.concurrency.sliding_window_size and not self.interrupted:
                    chunk = work_queue.popleft()
                    chunk_range = (min(chunk.page_numbers), max(chunk.page_numbers))
                    pages_desc = f"pages {chunk_range[0]}-{chunk_range[1]}"

                    # 1. Check disk cache
                    if not self.config.no_cache and cache_mgr.is_chunk_cached(chunk_range):
                        cached_ast = cache_mgr.load_chunk_ast(chunk_range)
                        if cached_ast:
                            logger.info(f"[CACHE HIT] chunk {chunk.chunk_index} ({pages_desc})")
                            cached_ast.chunk_index = chunk.chunk_index
                            completed_chunks.append(cached_ast)
                            processed_count += 1
                            if progress_callback:
                                progress_callback("chunks", processed_count, total_work)
                            continue
                    elif not self.config.no_cache:
                        logger.info(f"[CACHE MISS] chunk {chunk.chunk_index} ({pages_desc})")

                    # 2. Deterministic Extraction
                    chunk_render_dir = cache_mgr.get_chunk_render_dir(chunk_range)
                    extraction_res = extract_chunk(
                        doc=doc,
                        chunk_index=chunk.chunk_index,
                        page_numbers=chunk.page_numbers,
                        cache_chunk_dir=chunk_render_dir,
                        assets_dir=self.config.assets_dir_path,
                        dedup_table=dedup_table,
                        dpi=self.config.dpi,
                    )

                    # 3. Multimodal LLM worker task
                    validate_api_key_for_model(self.config.llm)
                    task = asyncio.create_task(
                        process_chunk_with_llm(
                            chunk_index=chunk.chunk_index,
                            page_numbers=chunk.page_numbers,
                            page_images=extraction_res.rendered_images,
                            raw_texts=extraction_res.raw_texts,
                            available_assets=extraction_res.extracted_assets,
                            config=self.config.llm,
                            semaphore=semaphore,
                            status_callback=worker_callback,
                        )
                    )
                    self._active_tasks.append(task)
                    tasks_in_flight[task] = chunk

                # Wait for running tasks
                if tasks_in_flight:
                    return_when = (
                        asyncio.FIRST_COMPLETED
                        if (work_queue or len(tasks_in_flight) >= self.config.concurrency.sliding_window_size)
                        else asyncio.ALL_COMPLETED
                    )
                    done, _ = await asyncio.wait(list(tasks_in_flight.keys()), return_when=return_when)
                    await drain_completed(done)

        except BaseException:
            # Cleanly cancel all in-flight and active tasks to prevent leaked task warnings
            all_pending = list(tasks_in_flight.keys()) + [t for t in self._active_tasks if not t.done()]
            for t in all_pending:
                t.cancel()
            if all_pending:
                await asyncio.gather(*all_pending, return_exceptions=True)
            raise

        if self.interrupted:
            raise KeyboardInterrupt()

        # Step 4: Stitch Chunks
        completed_chunks.sort(key=lambda c: min(c.page_range))
        for i, c in enumerate(completed_chunks):
            c.chunk_index = i

        stitched_ast = stitch_chunks(
            chunks=completed_chunks,
            source_pdf_hash=pdf_sha256,
            total_pages=total_source_pages,
            no_stitch=self.config.no_stitch,
        )

        # Step 5: Table of Contents & Markdown Synthesis
        headings = [b for b in stitched_ast.blocks if isinstance(b, HeadingBlock)]
        toc_items = generate_interactive_toc(native_outline, headings)
        stitched_ast.toc_items = toc_items

        output_md_path: Optional[str] = None
        output_json_path: Optional[str] = None

        base_out = self.config.output_path
        if base_out.endswith(".md"):
            stem = base_out[:-3]
        elif base_out.endswith(".json"):
            stem = base_out[:-5]
        else:
            stem = base_out

        if self.config.output_format in (OutputFormat.MD, OutputFormat.BOTH):
            output_md_path = f"{stem}.md" if not base_out.endswith(".md") else base_out
            md_content = emit_markdown(
                doc_ast=stitched_ast,
                toc_items=toc_items,
                marker_mode=self.config.page_marker_mode,
                include_toc=self.config.generate_toc,
            )
            write_output_document(md_content, output_md_path)

        if self.config.output_format in (OutputFormat.JSON, OutputFormat.BOTH):
            output_json_path = f"{stem}.json" if not base_out.endswith(".json") else base_out
            write_output_document(stitched_ast.model_dump_json(indent=2), output_json_path)

        doc.close()
        return stitched_ast, output_md_path, output_json_path
