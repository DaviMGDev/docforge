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
    MissingApiKeyException,
)
from pdf2md.logging import get_logger
from pdf2md.models.ast import DocumentAST, DocumentChunkAST, HeadingBlock
from pdf2md.models.cli_options import CliConfig, OutputFormat
from pdf2md.models.pipeline import (
    AssetRecord,
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
            progress_callback("partition", len(selected_pages), total_source_pages)

        # Prepare worker concurrency
        semaphore = asyncio.Semaphore(self.config.concurrency.llm_concurrency)
        dedup_table: Dict[str, AssetRecord] = {}
        chunk_results: List[Optional[DocumentChunkAST]] = [None] * total_chunks

        # Process chunks with sliding window
        tasks_in_flight: Dict[int, asyncio.Task] = {}
        processed_count = 0

        for idx, chunk in enumerate(chunks):
            if self.interrupted:
                break

            # 1. Check disk cache
            pages_desc = f"pages {min(chunk.page_numbers)}-{max(chunk.page_numbers)}"
            if not self.config.no_cache and cache_mgr.is_chunk_cached(chunk.chunk_index):
                cached_ast = cache_mgr.load_chunk_ast(chunk.chunk_index)
                if cached_ast:
                    logger.info(f"[CACHE HIT] chunk {chunk.chunk_index} ({pages_desc})")
                    chunk_results[idx] = cached_ast
                    processed_count += 1
                    if progress_callback:
                        progress_callback("chunks", processed_count, total_chunks)
                    continue
            elif not self.config.no_cache:
                logger.info(f"[CACHE MISS] chunk {chunk.chunk_index} ({pages_desc})")

            # 2. Sliding window bound
            while len(tasks_in_flight) >= self.config.concurrency.sliding_window_size:
                done, _ = await asyncio.wait(
                    tasks_in_flight.values(), return_when=asyncio.FIRST_COMPLETED
                )
                for t in done:
                    # Find task chunk index
                    for c_idx, task_obj in list(tasks_in_flight.items()):
                        if task_obj == t:
                            res_ast = t.result()
                            chunk_results[c_idx] = res_ast
                            cache_mgr.save_chunk_ast(res_ast)
                            logger.debug(f"[CACHE SAVE] chunk {res_ast.chunk_index} saved to cache")
                            cache_mgr.purge_chunk_renders(chunks[c_idx].chunk_index)
                            del tasks_in_flight[c_idx]
                            processed_count += 1
                            if progress_callback:
                                progress_callback("chunks", processed_count, total_chunks)

            # 3. Deterministic Extraction
            chunk_render_dir = cache_mgr.get_chunk_render_dir(chunk.chunk_index)
            extraction_res = extract_chunk(
                doc=doc,
                chunk_index=chunk.chunk_index,
                page_numbers=chunk.page_numbers,
                cache_chunk_dir=chunk_render_dir,
                assets_dir=self.config.assets_dir_path,
                dedup_table=dedup_table,
                dpi=self.config.dpi,
            )

            # Stage 2.5 Fast-Path Auto Bypass is deprecated (Issue #6).
            # 100% of chunks route directly to Stage 3 multimodal LLM parsing.

            # 5. Multimodal LLM worker task
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
            tasks_in_flight[idx] = task

        # Await remaining tasks
        if tasks_in_flight and not self.interrupted:
            done, _ = await asyncio.wait(tasks_in_flight.values())
            for c_idx, task_obj in tasks_in_flight.items():
                res_ast = task_obj.result()
                chunk_results[c_idx] = res_ast
                cache_mgr.save_chunk_ast(res_ast)
                logger.debug(f"[CACHE SAVE] chunk {res_ast.chunk_index} saved to cache")
                cache_mgr.purge_chunk_renders(chunks[c_idx].chunk_index)
                processed_count += 1
                if progress_callback:
                    progress_callback("chunks", processed_count, total_chunks)

        if self.interrupted:
            raise KeyboardInterrupt()

        # Step 4: Stitch Chunks
        valid_chunks = [c for c in chunk_results if c is not None]
        stitched_ast = stitch_chunks(
            chunks=valid_chunks,
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
