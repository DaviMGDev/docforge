"""Stage 1: Page Selection & Chunk Partitioning.

Conforms to specs/algorithms/page_range_parser.pseudo and specs/contracts/extractor.contract.
"""

from __future__ import annotations

import re
from typing import Any, List, Optional
import pymupdf as fitz

from pdf2md.exceptions import InvalidPageRangeException
from pdf2md.models.pipeline import ChunkDefinition


def parse_page_selection(range_str: Optional[str], total_pages: int) -> List[int]:
    """Parse print-range or Python slice notation into a deduplicated, sorted list of 1-based page numbers."""
    if total_pages <= 0:
        raise InvalidPageRangeException(f"Total pages must be positive, got {total_pages}")

    if range_str is None or range_str.strip() == "" or range_str.strip().lower() == "all":
        return list(range(1, total_pages + 1))

    selected_pages: set[int] = set()

    # Split combined expressions (handles comma separation and '+' slice joins)
    cleaned_str = range_str.replace("+", ",")
    tokens = [t.strip() for t in cleaned_str.split(",") if t.strip()]

    for token in tokens:
        # Case 1: Python slice notation e.g., "[0:10]", "[:5]", "[10:]", "[::2]", "[-10:]"
        if token.startswith("[") and token.endswith("]"):
            slice_content = token[1:-1].strip()
            parts = slice_content.split(":")

            try:
                if len(parts) == 1:
                    idx = int(parts[0])
                    resolved_idx = idx if idx >= 0 else total_pages + idx
                    if 0 <= resolved_idx < total_pages:
                        selected_pages.add(resolved_idx + 1)
                    else:
                        raise InvalidPageRangeException(f"Index {idx} out of document range (total {total_pages})")
                    continue
                elif len(parts) == 2:
                    start_val = int(parts[0]) if parts[0].strip() else 0
                    stop_val = int(parts[1]) if parts[1].strip() else total_pages
                    step_val = 1
                elif len(parts) == 3:
                    start_val = int(parts[0]) if parts[0].strip() else 0
                    stop_val = int(parts[1]) if parts[1].strip() else total_pages
                    step_val = int(parts[2]) if parts[2].strip() else 1
                else:
                    raise InvalidPageRangeException(f"Invalid slice syntax: {token}")

                # Resolve negative bounds relative to total_pages
                if start_val < 0:
                    start_val = max(0, total_pages + start_val)
                if stop_val < 0:
                    stop_val = max(0, total_pages + stop_val)

                start_clamped = min(max(start_val, 0), total_pages)
                stop_clamped = min(max(stop_val, 0), total_pages)

                for i in range(start_clamped, stop_clamped, step_val):
                    if 0 <= i < total_pages:
                        selected_pages.add(i + 1)
            except ValueError as e:
                if isinstance(e, InvalidPageRangeException):
                    raise
                raise InvalidPageRangeException(f"Malformed slice notation '{token}': {e}") from e

        # Case 2: Print range notation e.g., "1-200" or "42"
        elif "-" in token:
            bounds = token.split("-")
            if len(bounds) != 2:
                raise InvalidPageRangeException(f"Invalid range token: {token}")
            try:
                start_p = int(bounds[0].strip())
                end_p = int(bounds[1].strip())
            except ValueError as e:
                raise InvalidPageRangeException(f"Invalid numeric value in range: {token}") from e

            if start_p > end_p:
                raise InvalidPageRangeException(f"Start page cannot exceed end page: {token}")
            if start_p < 1 or end_p > total_pages:
                if start_p == 0:
                    raise InvalidPageRangeException(
                        f"Page range '{token}' starts at 0. Page numbers in print range notation are 1-based (e.g. '1-{end_p}'). "
                        f"For 0-based indexing, use Python slice notation (e.g. '[:{end_p}]' or '[0:{end_p}]')."
                    )
                raise InvalidPageRangeException(
                    f"Page range {token} out of document bounds (1-{total_pages})"
                )

            for p in range(start_p, end_p + 1):
                selected_pages.add(p)
        else:
            try:
                p = int(token)
            except ValueError as e:
                raise InvalidPageRangeException(f"Invalid page token: {token}") from e

            if p < 1 or p > total_pages:
                if p == 0:
                    raise InvalidPageRangeException(
                        "Page number 0 is invalid. Page numbers in print notation are 1-based (e.g. '1'). "
                        "For 0-based indexing, use Python slice notation (e.g. '[:1]')."
                    )
                raise InvalidPageRangeException(
                    f"Page {p} out of document bounds (1-{total_pages})"
                )
            selected_pages.add(p)

    if not selected_pages:
        raise InvalidPageRangeException(
            f"No valid pages selected within document range (1 to {total_pages})"
        )

    return sorted(selected_pages)


def calculate_adaptive_chunk_size(
    doc: fitz.Document,
    page_numbers: List[int],
    base_chunk_size: int = 4,
) -> int:
    """Dynamically adjust chunk size based on page density."""
    sample_pages = page_numbers[: min(5, len(page_numbers))]
    total_words = 0
    total_images = 0

    for p_num in sample_pages:
        try:
            page = doc.load_page(p_num - 1)
            total_words += len(page.get_text("words"))
            total_images += len(page.get_images())
        except Exception:
            continue

    avg_words = total_words / max(1, len(sample_pages))
    avg_images = total_images / max(1, len(sample_pages))

    # Dense pages heuristic from specs
    if avg_words > 1200 or avg_images > 4:
        return 1
    elif avg_words > 700 or avg_images > 2:
        return min(2, base_chunk_size)
    else:
        return base_chunk_size


def partition_into_chunks(pages: List[int], max_chunk_size: int = 4) -> List[ChunkDefinition]:
    """Partition normalized pages into chunks of size <= max_chunk_size.

    Severes chunks at non-contiguous page jumps.
    """
    if not pages:
        return []

    chunks: List[ChunkDefinition] = []
    current_pages: List[int] = []
    chunk_index = 0

    for page in pages:
        if current_pages:
            prev_page = current_pages[-1]
            is_contiguous = (page == prev_page + 1)
            is_full = (len(current_pages) >= max_chunk_size)

            if is_full or not is_contiguous:
                chunks.append(
                    ChunkDefinition(
                        chunk_index=chunk_index,
                        start_page=current_pages[0],
                        end_page=current_pages[-1],
                        page_numbers=list(current_pages),
                        is_contiguous=(current_pages[-1] - current_pages[0] + 1 == len(current_pages)),
                    )
                )
                chunk_index += 1
                current_pages = []

        current_pages.append(page)

    if current_pages:
        chunks.append(
            ChunkDefinition(
                chunk_index=chunk_index,
                start_page=current_pages[0],
                end_page=current_pages[-1],
                page_numbers=list(current_pages),
                is_contiguous=(current_pages[-1] - current_pages[0] + 1 == len(current_pages)),
            )
        )

    return chunks
