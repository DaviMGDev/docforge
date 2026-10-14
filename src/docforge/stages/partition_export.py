"""DocumentAST Partitioning for Multi-File Export.

Conforms to Issue #14 (support multi-file output partitioned by page, chapter, or section).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

from docforge.models.ast import (
    Block,
    BlockType,
    DocumentAST,
    HeadingBlock,
)


def slugify(text: str) -> str:
    """Convert arbitrary title text into filesystem-safe kebab-case slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text.strip("-") or "section"


def create_sub_ast(parent: DocumentAST, blocks: List[Block]) -> DocumentAST:
    """Create a child DocumentAST retaining parent provenance metadata."""
    pages = sorted(list({b.page for b in blocks})) if blocks else parent.selected_pages
    return DocumentAST(
        source_pdf_hash=parent.source_pdf_hash,
        total_pages=parent.total_pages,
        selected_pages=pages or [1],
        chunks_count=parent.chunks_count,
        stitched_at=parent.stitched_at,
        blocks=blocks,
        toc_items=[t for t in (parent.toc_items or []) if t.target_page in pages],
    )


def partition_by_page(
    doc_ast: DocumentAST,
    pattern: Optional[str] = None,
) -> List[Tuple[Path, DocumentAST]]:
    """Partition DocumentAST into one sub-document per page."""
    # Find all distinct pages
    pages = sorted(list({b.page for b in doc_ast.blocks}))
    if not pages:
        pages = doc_ast.selected_pages or [1]

    partitions: List[Tuple[Path, DocumentAST]] = []
    for p in pages:
        page_blocks = [b for b in doc_ast.blocks if b.page == p]
        if pattern:
            filename = pattern.format(page=p, page_num=f"{p:03d}")
        else:
            filename = f"page-{p:03d}"
        sub_ast = create_sub_ast(doc_ast, page_blocks)
        partitions.append((Path(filename), sub_ast))

    return partitions


def partition_by_headings(
    doc_ast: DocumentAST,
    max_level: int = 1,
    pattern: Optional[str] = None,
) -> List[Tuple[Path, DocumentAST]]:
    """Partition DocumentAST on heading boundaries (level <= max_level)."""
    partitions: List[Tuple[Path, DocumentAST]] = []
    current_blocks: List[Block] = []
    current_title = "preamble"
    current_slug = "00-preamble"
    index = 0

    for block in doc_ast.blocks:
        if block.type == BlockType.HEADING and getattr(block, "level", 99) <= max_level:
            if current_blocks:
                if pattern:
                    fname = pattern.format(
                        idx=index,
                        index=f"{index:02d}",
                        slug=current_slug,
                        title=current_title,
                    )
                else:
                    fname = f"{index:02d}-{current_slug}" if index > 0 else current_slug

                partitions.append((Path(fname), create_sub_ast(doc_ast, current_blocks)))
                current_blocks = []

            index += 1
            current_title = getattr(block, "content", f"section-{index}")
            current_slug = getattr(block, "slug", "") or slugify(current_title)

        current_blocks.append(block)

    if current_blocks:
        if pattern:
            fname = pattern.format(
                idx=index,
                index=f"{index:02d}",
                slug=current_slug,
                title=current_title,
            )
        else:
            fname = f"{index:02d}-{current_slug}" if index > 0 else current_slug

        partitions.append((Path(fname), create_sub_ast(doc_ast, current_blocks)))

    return partitions


def partition_by_chapter_and_page(
    doc_ast: DocumentAST,
) -> List[Tuple[Path, DocumentAST]]:
    """Partition hierarchical chapter/page structure: chapter-XX/page-YYY."""
    chapter_partitions = partition_by_headings(doc_ast, max_level=1)
    results: List[Tuple[Path, DocumentAST]] = []

    for chap_idx, (chap_path, chap_ast) in enumerate(chapter_partitions, start=1):
        chap_slug = chap_path.name
        page_partitions = partition_by_page(chap_ast)
        for page_path, page_ast in page_partitions:
            combined_path = Path(chap_slug) / page_path.name
            results.append((combined_path, page_ast))

    return results


def partition_ast(
    doc_ast: DocumentAST,
    split_by: str,
    pattern: Optional[str] = None,
) -> List[Tuple[Path, DocumentAST]]:
    """Partition DocumentAST into (relative_path, sub_ast) pairs based on strategy.

    Supported strategies:
    - 'page': One document per page (page-001, page-002, ...)
    - 'chapter': One document per top-level heading (H1)
    - 'section': One document per section heading (H1 or H2)
    - 'chapter/page': Directory per chapter containing per-page documents
    """
    strategy = split_by.strip().lower()

    if strategy in ("page", "pages"):
        return partition_by_page(doc_ast, pattern)
    elif strategy in ("chapter", "chapters", "heading-1", "h1"):
        return partition_by_headings(doc_ast, max_level=1, pattern=pattern)
    elif strategy in ("section", "sections", "heading-2", "h2"):
        return partition_by_headings(doc_ast, max_level=2, pattern=pattern)
    elif strategy in ("chapter/page", "chapter-page", "hierarchical"):
        return partition_by_chapter_and_page(doc_ast)
    else:
        raise ValueError(
            f"Unknown split strategy '{split_by}'. Supported: 'page', 'chapter', 'section', 'chapter/page'"
        )


def generate_partition_index(
    partitions: List[Tuple[Path, DocumentAST]],
    file_extension: str,
    title: str = "Table of Contents",
) -> str:
    """Generate Markdown index / SUMMARY.md with relative links to all partitions."""
    lines: List[str] = [f"# {title}\n"]

    for rel_path, sub_ast in partitions:
        ext = file_extension if file_extension.startswith(".") else f".{file_extension}"
        link_target = str(rel_path.with_suffix(ext))

        # Determine a human-readable title for the partition
        label = None
        for b in sub_ast.blocks:
            if b.type == BlockType.HEADING:
                label = getattr(b, "content", None)
                break

        if not label:
            if sub_ast.selected_pages:
                if len(sub_ast.selected_pages) == 1:
                    label = f"Page {sub_ast.selected_pages[0]}"
                else:
                    label = f"Pages {min(sub_ast.selected_pages)}-{max(sub_ast.selected_pages)}"
            else:
                label = rel_path.name.replace("-", " ").title()

        lines.append(f"- [{label}]({link_target})")

    return "\n".join(lines) + "\n"

