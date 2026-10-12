"""Processing Pipeline & Execution Models conforming to specs/models/pipeline.model."""

from __future__ import annotations

from enum import Enum
from typing import List, Optional, Tuple
from pydantic import BaseModel, Field, model_validator


class WorkerStatus(str, Enum):
    IDLE = "idle"
    RENDERING = "rendering"
    EXTRACTING = "extracting"
    LLM_INFERENCE = "llm_inference"
    RETRY_BACKOFF = "retry_backoff"
    COMPLETED = "completed"
    FAILED = "failed"


class DocumentClassification(str, Enum):
    COMPLEX = "complex"
    TRIVIAL_PROSE = "trivial_prose"


class PageInfo(BaseModel):
    page_number: int
    width_pts: float
    height_pts: float
    rotation: int = 0
    has_text: bool = True
    image_count: int = 0
    drawing_count: int = 0


class ChunkDefinition(BaseModel):
    chunk_index: int = Field(ge=0)
    start_page: int
    end_page: int
    page_numbers: List[int] = Field(min_length=1, max_length=16)
    is_contiguous: bool = True


class AssetRecord(BaseModel):
    asset_id: str
    page_number: int
    xref: int
    sha256: str
    extension: str
    width_px: int = Field(gt=0)
    height_px: int = Field(gt=0)
    color_space: str
    file_path: str
    byte_size: int = Field(gt=0)
    is_deduplicated: bool = False


class PageTextSpan(BaseModel):
    text: str
    bbox: Tuple[float, float, float, float]
    font_name: str = ""
    font_size: float = 0.0
    flags: int = 0
    is_bold: bool = False
    is_italic: bool = False
    is_monospace: bool = False


class PageTextRecord(BaseModel):
    page_number: int
    full_text: str
    spans: List[PageTextSpan] = Field(default_factory=list)
    word_count: int = 0
    line_count: int = 0


class PageImageRecord(BaseModel):
    page_number: int
    image_path: str
    dpi: int
    width_px: int
    height_px: int
    byte_size: int


class ExtractionResult(BaseModel):
    chunk_index: int
    page_numbers: List[int]
    rendered_images: List[PageImageRecord] = Field(default_factory=list)
    raw_texts: List[PageTextRecord] = Field(default_factory=list)
    extracted_assets: List[AssetRecord] = Field(default_factory=list)
    classification: DocumentClassification = DocumentClassification.COMPLEX


class WorkerState(BaseModel):
    worker_id: int
    current_chunk_index: Optional[int] = None
    status: WorkerStatus = WorkerStatus.IDLE
    current_attempt: int = 0
    last_error: Optional[str] = None
    started_at: Optional[float] = None


class PipelineMetrics(BaseModel):
    pdf_path: str
    pdf_sha256: str
    total_source_pages: int
    processed_pages: int = 0
    total_chunks: int = 0
    chunks_cached: int = 0
    chunks_bypassed: int = 0
    chunks_inferred_llm: int = 0
    chunks_fallback: int = 0
    total_assets_extracted: int = 0
    total_assets_deduplicated: int = 0
    start_time_epoch: float
    end_time_epoch: Optional[float] = None
    elapsed_seconds: Optional[float] = None
    peak_rss_mb: float = 0.0
