"""Stage 2.5: Fast-Path Hybrid Bypass Classifier & Direct Layout Generator.

Conforms to specs/algorithms/auto_bypass_classifier.pseudo and specs/SPEC.md §3.3.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import List, Tuple
import pymupdf as fitz

from pdf2md.models.ast import (
    Block,
    DocumentChunkAST,
    HeadingBlock,
    ListBlock,
    ListItem,
    ParagraphBlock,
)
from pdf2md.models.pipeline import ChunkDefinition, DocumentClassification


MATH_SYMBOLS = ["∑", "∫", "∂", "√", "±", "≤", "≥", "≠", "≈", "∈", "∀", "∃", "∞", "×", "÷"]


def slugify(text: str) -> str:
    """Generate clean slug from heading text."""
    clean = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    return re.sub(r"[-\s]+", "-", clean) or "heading"


def classify_page(page: fitz.Page) -> DocumentClassification:
    """Evaluate whether a single PDF page is trivial single-column prose."""
    # Check 1: Embedded raster/vector images
    images = page.get_images(full=True)
    if len(images) > 0:
        return DocumentClassification.COMPLEX

    # Check 2: Vector drawing paths
    drawings = page.get_drawings()
    non_rule_drawings = 0
    for d in drawings:
        rect = d.get("rect")
        if not rect:
            non_rule_drawings += 1
            continue
        is_horizontal_rule = (rect.y1 - rect.y0 < 3.0) and (rect.x1 - rect.x0 > 100.0)
        if not is_horizontal_rule:
            non_rule_drawings += 1

    if non_rule_drawings > 2:
        return DocumentClassification.COMPLEX

    # Check 3: Multi-column layout detection via horizontal overlap analysis
    text_dict = page.get_text("dict")
    lines = []
    for b in text_dict.get("blocks", []):
        if b.get("type") == 0:
            for l in b.get("lines", []):
                if l.get("bbox"):
                    lines.append(l.get("bbox"))

    for i in range(len(lines)):
        for j in range(i + 1, min(i + 20, len(lines))):
            l1 = lines[i]
            l2 = lines[j]
            vertical_overlap = max(0.0, min(l1[3], l2[3]) - max(l1[1], l2[1]))
            if vertical_overlap > 8.0:
                horizontal_disjoint = (l1[2] < l2[0] - 15.0) or (l2[2] < l1[0] - 15.0)
                if horizontal_disjoint:
                    return DocumentClassification.COMPLEX

    text_blocks = page.get_text("blocks")
    if not text_blocks:
        return DocumentClassification.TRIVIAL_PROSE

    # Sort blocks by y0
    sorted_blocks = sorted(text_blocks, key=lambda b: b[1])
    for i in range(len(sorted_blocks) - 1):
        b1 = sorted_blocks[i]
        b2 = sorted_blocks[i + 1]
        vertical_overlap = max(0.0, min(b1[3], b2[3]) - max(b1[1], b2[1]))
        if vertical_overlap > 15.0:
            horizontal_disjoint = (b1[2] < b2[0] - 10.0) or (b2[2] < b1[0] - 10.0)
            if horizontal_disjoint:
                return DocumentClassification.COMPLEX

    # Check 4: Mathematical glyph heuristics
    full_text = page.get_text("text")
    for sym in MATH_SYMBOLS:
        if sym in full_text:
            return DocumentClassification.COMPLEX

    # Check 5: Tabular row/tab delimiters
    tabular_lines = 0
    for line in full_text.splitlines():
        if "\t" in line or "    " in line:
            tabular_lines += 1
    if tabular_lines > 3:
        return DocumentClassification.COMPLEX

    return DocumentClassification.TRIVIAL_PROSE


def classify_chunk(
    doc: fitz.Document,
    chunk: ChunkDefinition,
    auto_bypass_enabled: bool,
) -> DocumentClassification:
    """Classify chunk. If auto_bypass is disabled, always returns COMPLEX."""
    if not auto_bypass_enabled:
        return DocumentClassification.COMPLEX

    for p_num in chunk.page_numbers:
        page = doc.load_page(p_num - 1)
        if classify_page(page) == DocumentClassification.COMPLEX:
            return DocumentClassification.COMPLEX

    return DocumentClassification.TRIVIAL_PROSE


def build_bypass_chunk_ast(
    doc: fitz.Document,
    chunk: ChunkDefinition,
) -> DocumentChunkAST:
    """Build DocumentChunkAST directly using deterministic layout analysis (no LLM)."""
    blocks: List[Block] = []

    for page_num in chunk.page_numbers:
        page = doc.load_page(page_num - 1)
        page_dict = page.get_text("dict")

        # Gather font sizes to detect headings
        font_sizes: List[float] = []
        for b in page_dict.get("blocks", []):
            if b.get("type") == 0:
                for line in b.get("lines", []):
                    for s in line.get("spans", []):
                        if s.get("text", "").strip():
                            font_sizes.append(float(s.get("size", 10.0)))

        median_font_size = 10.0
        if font_sizes:
            font_sizes_sorted = sorted(font_sizes)
            median_font_size = font_sizes_sorted[len(font_sizes_sorted) // 2]

        for b in page_dict.get("blocks", []):
            if b.get("type") != 0:
                continue

            block_text = ""
            max_size = 0.0
            is_bold = False

            for line in b.get("lines", []):
                line_str = "".join(s.get("text", "") for s in line.get("spans", []))
                for s in line.get("spans", []):
                    size = float(s.get("size", 0.0))
                    if size > max_size:
                        max_size = size
                    flags = int(s.get("flags", 0))
                    if bool(flags & (1 << 4)) or "bold" in s.get("font", "").lower():
                        is_bold = True
                block_text += line_str + "\n"

            content = block_text.strip()
            if not content:
                continue

            # Heading detection
            if max_size >= median_font_size * 1.3 or (is_bold and len(content.splitlines()) <= 2 and len(content) < 120):
                level = 1 if max_size >= median_font_size * 1.6 else (2 if max_size >= median_font_size * 1.3 else 3)
                blocks.append(
                    HeadingBlock(
                        page=page_num,
                        level=level,
                        content=content.replace("\n", " "),
                        slug=slugify(content),
                    )
                )
            # List detection
            elif content.startswith(("- ", "* ", "• ")) or re.match(r"^\d+\.\s+", content):
                lines = content.splitlines()
                items = [ListItem(content=re.sub(r"^([-\*•]|\d+\.)\s+", "", line).strip()) for line in lines if line.strip()]
                is_ordered = bool(re.match(r"^\d+\.\s+", content))
                if items:
                    blocks.append(
                        ListBlock(
                            page=page_num,
                            ordered=is_ordered,
                            items=items,
                        )
                    )
                else:
                    blocks.append(ParagraphBlock(page=page_num, content=content))
            else:
                blocks.append(ParagraphBlock(page=page_num, content=content))

    start_page = chunk.page_numbers[0]
    end_page = chunk.page_numbers[-1]
    now_utc = datetime.now(timezone.utc).isoformat()

    return DocumentChunkAST(
        chunk_index=chunk.chunk_index,
        page_range=(start_page, end_page),
        bypass_used=True,
        fallback_used=False,
        llm_model="bypass",
        blocks=blocks,
        created_at=now_utc,
    )
