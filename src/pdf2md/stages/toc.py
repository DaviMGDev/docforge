"""Stage 5: Dual-Source TOC Generator & Deep Linker.

Conforms to specs/algorithms/toc_generator_and_linker.pseudo and specs/models/toc.model.
"""

from __future__ import annotations

import difflib
import re
import uuid
from typing import Dict, List, Optional, Set
from pdf2md.models.ast import HeadingBlock
from pdf2md.models.toc import OutlineNode, SlugMap, TocItem, TocSource


def normalize_for_matching(text: str) -> str:
    """Normalize text for fuzzy title matching."""
    norm = re.sub(r"[^\w\s]", "", text.lower()).strip()
    return re.sub(r"\s+", " ", norm)


def string_similarity(s1: str, s2: str) -> float:
    """Compute gestalt pattern matching similarity between two strings (0.0 to 1.0)."""
    return difflib.SequenceMatcher(None, s1, s2).ratio()


def resolve_target_slug(
    bookmark_title: str,
    target_page: int,
    ast_headings: List[HeadingBlock],
    allocated_slugs: Optional[Set[str]] = None,
) -> str:
    """Find closest heading slug matching bookmark title on page, or fallback to #page-N."""
    norm_title = normalize_for_matching(bookmark_title)

    # 1. Look for candidate headings on target page (or +/- 1 page tolerance)
    candidates = [h for h in ast_headings if abs(h.page - target_page) <= 1]
    best_heading: Optional[HeadingBlock] = None
    best_sim = 0.0

    for h in candidates:
        norm_h = normalize_for_matching(h.content)
        sim = string_similarity(norm_title, norm_h)
        if sim > 0.7 and sim > best_sim:
            best_sim = sim
            best_heading = h

    if best_heading:
        return f"#{best_heading.slug}"

    # 2. Search globally across all headings
    for h in ast_headings:
        norm_h = normalize_for_matching(h.content)
        if norm_h == norm_title:
            return f"#{h.slug}"

    # 3. Direct match if title itself matches an allocated slug
    clean_slug = re.sub(r"[^\w\s-]", "", bookmark_title.lower()).strip().replace(" ", "-")
    if allocated_slugs and clean_slug in allocated_slugs:
        return f"#{clean_slug}"

    # 4. Fallback: Page anchor
    return f"#page-{target_page}"


def generate_interactive_toc(
    native_outline: List[OutlineNode],
    ast_headings: List[HeadingBlock],
) -> List[TocItem]:
    """Construct unified interactive TOC linking to heading slugs or page anchors."""
    toc_items: List[TocItem] = []
    allocated_slugs = {h.slug for h in ast_headings}

    # Case 1: PDF native bookmarks exist
    if native_outline:
        for node in native_outline:
            target_slug = resolve_target_slug(node.title, node.page_number, ast_headings, allocated_slugs)
            toc_items.append(
                TocItem(
                    id=str(uuid.uuid4()),
                    level=node.level,
                    title=node.title,
                    target_slug=target_slug,
                    target_page=node.page_number,
                    source=TocSource.SYNTHESIZED,
                )
            )
        return toc_items

    # Case 2: Build TOC purely from AST headings
    for heading in ast_headings:
        if heading.level <= 3:
            toc_items.append(
                TocItem(
                    id=str(uuid.uuid4()),
                    level=heading.level,
                    title=heading.content,
                    target_slug=f"#{heading.slug}",
                    target_page=heading.page,
                    source=TocSource.AST_HEADING,
                )
            )

    return toc_items


def transcribe_printed_toc(toc_text: str, allocated_slugs: Optional[Set[str]] = None) -> str:
    """Transform printed TOC dotted leader lines into markdown links."""
    lines = toc_text.splitlines()
    transformed: List[str] = []

    for line in lines:
        match = re.match(r"^(.*?)\s*[\.·\-_]{3,}\s*(\d+)\s*$", line)
        if match:
            title = match.group(1).strip()
            page_num = int(match.group(2).strip())
            target_slug = resolve_target_slug(title, page_num, [], allocated_slugs)
            # If slug is just #page-N, check if we can slugify title
            if target_slug == f"#page-{page_num}":
                slug_cand = re.sub(r"[^\w\s-]", "", title.lower()).strip().replace(" ", "-")
                target_slug = f"#{slug_cand}"
            transformed.append(f"- [{title}]({target_slug}) *(p. {page_num})*")
        else:
            transformed.append(line)

    return "\n".join(transformed)
