"""Table of Contents & Navigation Models conforming to specs/models/toc.model."""

from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional, Set
from pydantic import BaseModel, Field


class TocSource(str, Enum):
    PDF_BOOKMARK = "pdf_bookmark"
    AST_HEADING = "ast_heading"
    SYNTHESIZED = "synthesized"
    PRINTED_TOC = "printed_toc"


class OutlineNode(BaseModel):
    level: int = Field(ge=1)
    title: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    dest_y: Optional[float] = None


class ASTHeadingNode(BaseModel):
    level: int = Field(ge=1, le=6)
    content: str
    page_number: int = Field(ge=1)
    slug: str
    block_id: Optional[str] = None


class TocItem(BaseModel):
    id: str
    level: int = Field(ge=1)
    title: str
    target_slug: str = Field(min_length=1)
    target_page: int = Field(ge=1)
    source: TocSource
    children: List[TocItem] = Field(default_factory=list)


class TocTree(BaseModel):
    items: List[TocItem] = Field(default_factory=list)
    total_entries: int = 0
    has_outline_source: bool = False
    has_ast_source: bool = False


class SlugMap(BaseModel):
    slug_to_block_id: Dict[str, str] = Field(default_factory=dict)
    slug_to_page: Dict[str, int] = Field(default_factory=dict)
    allocated_slugs: Set[str] = Field(default_factory=set)


class PrintedTocLine(BaseModel):
    raw_text: str
    section_title: str
    target_page: int
    matched_slug: Optional[str] = None
