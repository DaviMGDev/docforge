"""Document AST Models conforming to specs/models/ast.model."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, List, Literal, Optional, Tuple, Union
from pydantic import BaseModel, Field, model_validator


class BlockType(str, Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    TABLE = "table"
    CODE = "code"
    MATH = "math"
    IMAGE = "image"
    CALLOUT = "callout"
    THEMATIC_BREAK = "thematic_break"
    RAW_FALLBACK = "raw_fallback"


class MathMode(str, Enum):
    INLINE = "inline"
    BLOCK = "block"


class CalloutType(str, Enum):
    NOTE = "NOTE"
    TIP = "TIP"
    IMPORTANT = "IMPORTANT"
    WARNING = "WARNING"
    CAUTION = "CAUTION"


class BoundingBox(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float

    @model_validator(mode="after")
    def validate_invariants(self) -> BoundingBox:
        if self.x0 < 0 or self.y0 < 0:
            raise ValueError("Bounding box coordinates must be non-negative")
        if self.x0 > self.x1 or self.y0 > self.y1:
            raise ValueError("Invalid bounding box: x0 <= x1 and y0 <= y1 required")
        return self


class BaseBlock(BaseModel):
    id: Optional[str] = None
    type: BlockType
    page: int
    bbox: Optional[BoundingBox] = None
    raw_source: Optional[str] = None


class HeadingBlock(BaseBlock):
    type: Literal[BlockType.HEADING] = BlockType.HEADING
    level: int = Field(ge=1, le=6)
    content: str = Field(min_length=1)
    slug: str


class ParagraphBlock(BaseBlock):
    type: Literal[BlockType.PARAGRAPH] = BlockType.PARAGRAPH
    content: str
    is_continuation: bool = False


class ListItem(BaseModel):
    content: str
    sub_items: List[ListItem] = Field(default_factory=list)


class ListBlock(BaseBlock):
    type: Literal[BlockType.LIST] = BlockType.LIST
    ordered: bool = False
    start_index: Optional[int] = 1
    items: List[ListItem] = Field(min_length=1)


class TableCell(BaseModel):
    content: str
    colspan: int = 1
    rowspan: int = 1
    align: Optional[str] = "left"


class TableBlock(BaseBlock):
    type: Literal[BlockType.TABLE] = BlockType.TABLE
    header: List[str] = Field(default_factory=list)
    rows: List[List[str]] = Field(default_factory=list)
    complex_cells: Optional[List[List[TableCell]]] = None
    caption: Optional[str] = None
    raw_html: Optional[str] = None

    @model_validator(mode="after")
    def validate_content(self) -> TableBlock:
        if not self.header and not (self.complex_cells and len(self.complex_cells) > 0) and not self.rows and not self.raw_html:
            raise ValueError("TableBlock must have either header, rows, complex_cells, or raw_html")
        return self


class CodeBlock(BaseBlock):
    type: Literal[BlockType.CODE] = BlockType.CODE
    content: str
    language: Optional[str] = ""
    caption: Optional[str] = None


class MathBlock(BaseBlock):
    type: Literal[BlockType.MATH] = BlockType.MATH
    content: str
    math_mode: MathMode = MathMode.BLOCK
    equation_number: Optional[str] = None


class ImageBlock(BaseBlock):
    type: Literal[BlockType.IMAGE] = BlockType.IMAGE
    asset_path: str = Field(min_length=1)
    alt_text: str = ""
    caption: Optional[str] = None
    width_px: Optional[int] = None
    height_px: Optional[int] = None
    sha256: Optional[str] = None


class CalloutBlock(BaseBlock):
    type: Literal[BlockType.CALLOUT] = BlockType.CALLOUT
    callout_type: CalloutType = CalloutType.NOTE
    title: Optional[str] = None
    content: str


class ThematicBreakBlock(BaseBlock):
    type: Literal[BlockType.THEMATIC_BREAK] = BlockType.THEMATIC_BREAK


class RawFallbackBlock(BaseBlock):
    type: Literal[BlockType.RAW_FALLBACK] = BlockType.RAW_FALLBACK
    content: str
    warning_message: str = "Page rendered via deterministic raw-text fallback"


Block = Annotated[
    Union[
        HeadingBlock,
        ParagraphBlock,
        ListBlock,
        TableBlock,
        CodeBlock,
        MathBlock,
        ImageBlock,
        CalloutBlock,
        ThematicBreakBlock,
        RawFallbackBlock,
    ],
    Field(discriminator="type"),
]


class DocumentChunkAST(BaseModel):
    chunk_index: int = Field(ge=0)
    page_range: Tuple[int, int]
    bypass_used: bool = False
    fallback_used: bool = False
    llm_model: Optional[str] = None
    blocks: List[Block] = Field(default_factory=list)
    created_at: str

    @model_validator(mode="after")
    def validate_page_range(self) -> DocumentChunkAST:
        if self.page_range[0] < 1 or self.page_range[1] < self.page_range[0]:
            raise ValueError(f"Invalid page_range: {self.page_range}. start >= 1 and end >= start required")
        return self


class DocumentAST(BaseModel):
    source_pdf_hash: str
    total_pages: int
    selected_pages: List[int] = Field(min_length=1)
    chunks_count: int = Field(ge=1)
    blocks: List[Block] = Field(default_factory=list)
    toc_items: List[Any] = Field(default_factory=list)
    stitched_at: str
