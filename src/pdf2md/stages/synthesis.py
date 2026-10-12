"""Stage 5: CommonMark / GFM Markdown Serializer.

Conforms to specs/contracts/renderer.contract and specs/algorithms/markdown_emitter.pseudo.
"""

from __future__ import annotations

import os
from typing import List, Optional
from pdf2md.exceptions import DiskWriteException
from pdf2md.models.ast import (
    Block,
    BlockType,
    DocumentAST,
    HeadingBlock,
    MathMode,
)
from pdf2md.models.cli_options import PageMarkerMode
from pdf2md.models.toc import TocItem


def format_page_marker(page_num: int, mode: PageMarkerMode) -> str:
    """Format page boundary navigation marker according to configured mode."""
    if mode == PageMarkerMode.ANCHOR:
        return f'<a id="page-{page_num}"></a>\n<!-- [Page {page_num}] -->\n\n'
    elif mode == PageMarkerMode.BADGE:
        return f'<small class="pdf-page-indicator">📄 Page {page_num}</small>\n---\n\n'
    elif mode == PageMarkerMode.COMMENT:
        return f"<!-- [Page {page_num}] -->\n\n"
    else:  # NONE
        return ""


def render_gfm_table(header: List[str], rows: List[List[str]], caption: Optional[str] = None) -> str:
    """Render standard GFM pipe table with aligned separators."""
    lines: List[str] = []
    if caption:
        lines.append(f"*{caption}*\n")

    if not header and rows:
        header = [f"Col {i + 1}" for i in range(len(rows[0]))]

    col_count = max(len(header), max((len(r) for r in rows), default=0))
    if col_count == 0:
        return ""

    padded_header = list(header) + [""] * (col_count - len(header))
    clean_header = [cell.replace("\n", " ").strip() for cell in padded_header]
    lines.append("| " + " | ".join(clean_header) + " |")
    lines.append("| " + " | ".join(["---"] * col_count) + " |")

    for r in rows:
        padded_row = list(r) + [""] * (col_count - len(r))
        clean_row = [cell.replace("\n", "<br>").strip() for cell in padded_row]
        lines.append("| " + " | ".join(clean_row) + " |")

    return "\n".join(lines) + "\n"


def render_single_block(block: Block) -> str:
    """Serialize individual AST block into Markdown representation."""
    if block.type == BlockType.HEADING:
        prefix = "#" * block.level
        # Emit standard markdown heading with anchor id tag if custom
        return f"{prefix} {block.content}\n"

    elif block.type == BlockType.PARAGRAPH:
        return f"{block.content}\n"

    elif block.type == BlockType.LIST:
        lines: List[str] = []
        for i, item in enumerate(block.items):
            pfx = f"{i + (block.start_index or 1)}. " if block.ordered else "- "
            lines.append(f"{pfx}{item.content}")
            for sub in item.sub_items:
                lines.append(f"  - {sub.content}")
        return "\n".join(lines) + "\n"

    elif block.type == BlockType.CODE:
        lang = block.language or ""
        header = f"```{lang}\n"
        footer = "\n```\n"
        caption = f"\n*{block.caption}*\n\n" if block.caption else ""
        return header + block.content.rstrip() + footer + caption

    elif block.type == BlockType.MATH:
        if block.math_mode == MathMode.BLOCK:
            eq_num = f" \\tag{{{block.equation_number}}}" if block.equation_number else ""
            return f"$$\n{block.content.strip()}{eq_num}\n$$\n"
        else:
            return f"${block.content.strip()}$"

    elif block.type == BlockType.TABLE:
        if block.raw_html and block.raw_html.strip():
            caption = f"*{block.caption}*\n\n" if block.caption else ""
            return caption + block.raw_html.strip() + "\n"
        else:
            return render_gfm_table(block.header, block.rows, block.caption)

    elif block.type == BlockType.IMAGE:
        caption_str = f"\n*{block.caption}*\n" if block.caption else ""
        return f"![{block.alt_text}]({block.asset_path}){caption_str}"

    elif block.type == BlockType.CALLOUT:
        quoted = "\n".join(f"> {line}" for line in block.content.splitlines())
        title_hdr = f" **{block.title}**\n" if block.title else "\n"
        return f"> [!{block.callout_type}]{title_hdr}{quoted}\n"

    elif block.type == BlockType.THEMATIC_BREAK:
        return "---\n"

    elif block.type == BlockType.RAW_FALLBACK:
        warning_banner = f"> [!WARNING] Page rendered via deterministic raw-text fallback.\n\n"
        return warning_banner + block.content + "\n"

    return ""


def emit_markdown(
    doc_ast: DocumentAST,
    toc_items: List[TocItem],
    marker_mode: PageMarkerMode = PageMarkerMode.ANCHOR,
    include_toc: bool = True,
) -> str:
    """Serialize DocumentAST into complete CommonMark/GFM Markdown document."""
    parts: List[str] = []

    # 1. Interactive Table of Contents
    if include_toc and toc_items:
        parts.append("## Table of Contents\n")
        for item in toc_items:
            indent = "  " * (item.level - 1)
            parts.append(f"{indent}- [{item.title}]({item.target_slug}) *(p. {item.target_page})*")
        parts.append("\n---\n\n")

    # 2. Blocks with page markers
    current_page = 0
    for block in doc_ast.blocks:
        if block.page != current_page:
            current_page = block.page
            marker = format_page_marker(current_page, marker_mode)
            if marker:
                parts.append(marker)

        rendered = render_single_block(block)
        if rendered:
            parts.append(rendered)
            parts.append("\n")

    return "".join(parts).strip() + "\n"


def write_output_document(
    content: str,
    output_path: str,
) -> None:
    """Persist generated document to output path with parent dir creation."""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        raise DiskWriteException(f"Failed to write output to {output_path}: {e}") from e
