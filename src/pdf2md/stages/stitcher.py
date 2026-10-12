"""Stage 4: Cross-Chunk AST Stitching & Chrome Suppression.

Conforms to specs/contracts/stitcher.contract and specs/algorithms/cross_chunk_stitcher.pseudo.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Dict, List, Set
from pdf2md.models.ast import (
    Block,
    BlockType,
    DocumentAST,
    DocumentChunkAST,
    HeadingBlock,
    ParagraphBlock,
    TableBlock,
)


def generate_unique_slug(text: str, allocated_slugs: Set[str]) -> str:
    """Generate unique URL-safe anchor slug with collision resolution."""
    clean = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    base_slug = re.sub(r"[-\s]+", "-", clean) or "heading"

    candidate = base_slug
    seq = 1
    while candidate in allocated_slugs:
        candidate = f"{base_slug}-{seq}"
        seq += 1

    allocated_slugs.add(candidate)
    return candidate


def check_split_paragraph(trailing: Block, leading: Block) -> tuple[bool, bool]:
    """Check if paragraphs are split, returning (is_split, is_hyphenated)."""
    if trailing.type != BlockType.PARAGRAPH or leading.type != BlockType.PARAGRAPH:
        return False, False

    trailing_p = trailing.content.rstrip()
    leading_p = leading.content.lstrip()

    if not trailing_p or not leading_p:
        return False, False

    # Check hyphenated word wrap
    if trailing_p.endswith("-") and len(trailing_p) > 1 and trailing_p[-2].isalpha():
        trailing.content = trailing_p[:-1]
        return True, True

    terminal_punctuation = (".", "!", "?", "...", ":", '"', "”", "'")
    if not any(trailing_p.endswith(p) for p in terminal_punctuation):
        first_char = leading_p[0]
        if first_char.islower() or first_char in (",", ";", ")", "]", "}") or first_char.isdigit():
            return True, False

    return False, False


def is_split_paragraph(trailing: Block, leading: Block) -> bool:
    """Return True if trailing and leading blocks represent a continuous split paragraph."""
    split, _ = check_split_paragraph(trailing, leading)
    return split


def is_split_table(trailing: Block, leading: Block) -> bool:
    """Return True if trailing and leading blocks represent severed TableBlocks."""
    if trailing.type != BlockType.TABLE or leading.type != BlockType.TABLE:
        return False

    # Leading table must either have identical header or an empty header
    if trailing.header == leading.header or not leading.header:
        return bool(trailing.header or trailing.rows)

    return False


def merge_table_blocks(t1: TableBlock, t2: TableBlock) -> TableBlock:
    """Fuse two split TableBlocks into a single TableBlock."""
    merged_rows = list(t1.rows) + list(t2.rows)
    return TableBlock(
        id=t1.id,
        page=t1.page,
        header=t1.header,
        rows=merged_rows,
        caption=t1.caption or t2.caption,
    )


def normalize_chrome_text(text: str) -> str:
    """Normalize running header/footer string for comparison (ignore digits/spaces)."""
    norm = text.strip().lower()
    # Replace variable numbers (e.g. page numbers) with '#'
    return re.sub(r"\d+", "#", norm)


def suppress_running_chrome(blocks: List[Block]) -> List[Block]:
    """Detect and prune headers/footers repeated across >= 3 pages."""
    page_to_blocks: Dict[int, List[Block]] = {}
    for b in blocks:
        page_to_blocks.setdefault(b.page, []).append(b)

    candidate_occurrences: Dict[str, Set[int]] = {}

    for page_num, p_blocks in page_to_blocks.items():
        if not p_blocks:
            continue
        first_b = p_blocks[0]
        last_b = p_blocks[-1]

        # Top margin candidate
        if first_b.type == BlockType.PARAGRAPH and len(first_b.content) < 100:
            norm = normalize_chrome_text(first_b.content)
            candidate_occurrences.setdefault(norm, set()).add(page_num)

        # Bottom margin candidate
        if last_b.type == BlockType.PARAGRAPH and len(last_b.content) < 60:
            norm = normalize_chrome_text(last_b.content)
            candidate_occurrences.setdefault(norm, set()).add(page_num)

    chrome_signatures = {sig for sig, pages in candidate_occurrences.items() if len(pages) >= 3}

    if not chrome_signatures:
        return blocks

    filtered: List[Block] = []
    for b in blocks:
        if b.type == BlockType.PARAGRAPH:
            norm = normalize_chrome_text(b.content)
            if norm in chrome_signatures:
                continue
        filtered.append(b)

    return filtered


def normalize_heading_hierarchy(blocks: List[Block]) -> List[Block]:
    """Ensure heading levels do not jump by more than 1 (e.g. H1 directly to H3)."""
    current_level = 0
    for b in blocks:
        if b.type == BlockType.HEADING:
            if current_level == 0:
                current_level = b.level
            elif b.level > current_level + 1:
                b.level = current_level + 1
                current_level = b.level
            else:
                current_level = b.level
    return blocks


def stitch_chunks(
    chunks: List[DocumentChunkAST],
    source_pdf_hash: str,
    total_pages: int,
    no_stitch: bool = False,
) -> DocumentAST:
    """Combine sequential chunk ASTs into a cohesive single DocumentAST."""
    now_utc = datetime.now(timezone.utc).isoformat()
    if not chunks:
        return DocumentAST(
            source_pdf_hash=source_pdf_hash,
            total_pages=total_pages,
            selected_pages=[1],
            chunks_count=1,
            blocks=[],
            toc_items=[],
            stitched_at=now_utc,
        )

    # Sort chunks by chunk_index
    sorted_chunks = sorted(chunks, key=lambda c: c.chunk_index)

    all_selected_pages: Set[int] = set()
    for chunk in sorted_chunks:
        for p in range(chunk.page_range[0], chunk.page_range[1] + 1):
            all_selected_pages.add(p)

    selected_pages_list = sorted(all_selected_pages)

    if no_stitch:
        concatenated: List[Block] = []
        for chunk in sorted_chunks:
            concatenated.extend(chunk.blocks)
        return DocumentAST(
            source_pdf_hash=source_pdf_hash,
            total_pages=total_pages,
            selected_pages=selected_pages_list,
            chunks_count=len(sorted_chunks),
            blocks=concatenated,
            toc_items=[],
            stitched_at=now_utc,
        )

    stitched_blocks: List[Block] = []

    for chunk in sorted_chunks:
        chunk_blocks = list(chunk.blocks)
        if not chunk_blocks:
            continue

        if stitched_blocks:
            trailing = stitched_blocks[-1]
            leading = chunk_blocks[0]

            # Paragraph heal
            is_split, is_hyphen = check_split_paragraph(trailing, leading)
            if is_split:
                if is_hyphen:
                    trailing.content = trailing.content + leading.content
                else:
                    trailing.content = trailing.content + " " + leading.content
                trailing.is_continuation = True
                chunk_blocks = chunk_blocks[1:]
            # Table heal
            elif is_split_table(trailing, leading):
                stitched_blocks[-1] = merge_table_blocks(trailing, leading)
                chunk_blocks = chunk_blocks[1:]

        stitched_blocks.extend(chunk_blocks)

    # Suppress chrome
    filtered_blocks = suppress_running_chrome(stitched_blocks)

    # Normalize headings
    normalized_blocks = normalize_heading_hierarchy(filtered_blocks)

    # Allocate slugs
    allocated_slugs: Set[str] = set()
    for b in normalized_blocks:
        if b.type == BlockType.HEADING:
            b.slug = generate_unique_slug(b.content, allocated_slugs)

    return DocumentAST(
        source_pdf_hash=source_pdf_hash,
        total_pages=total_pages,
        selected_pages=selected_pages_list,
        chunks_count=len(sorted_chunks),
        blocks=normalized_blocks,
        toc_items=[],
        stitched_at=now_utc,
    )
