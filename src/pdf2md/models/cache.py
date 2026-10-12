"""Cache & Checkpoint Models conforming to specs/models/cache.model."""

from __future__ import annotations

from enum import Enum
from typing import List, Tuple
from pydantic import BaseModel, Field, model_validator


class CacheStatus(str, Enum):
    HIT = "hit"
    MISS = "miss"
    INVALIDATED = "invalidated"
    CORRUPTED = "corrupted"


class InvalidationReason(str, Enum):
    NONE = "none"
    FORCE_FLAG = "force_flag"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    MODEL_MISMATCH = "model_mismatch"
    PROMPT_VERSION_MISMATCH = "prompt_version_mismatch"
    DPI_MISMATCH = "dpi_mismatch"
    CHUNK_SIZE_MISMATCH = "chunk_size_mismatch"


class CacheConfig(BaseModel):
    cache_dir: str = ".cache"
    pdf_sha256: str
    cache_root_path: str
    enabled: bool = True


class ChunkCacheEntry(BaseModel):
    chunk_index: int = Field(ge=0)
    page_range: Tuple[int, int]
    ast_json_path: str
    sha256_ast: str
    bypass_used: bool = False
    fallback_used: bool = False
    timestamp_utc: str
    status: CacheStatus = CacheStatus.HIT


class CacheManifest(BaseModel):
    version: str = "1.0.0"
    pdf_name: str
    pdf_sha256: str = Field(min_length=64, max_length=64)
    pdf_size_bytes: int
    total_pages: int = Field(ge=1)
    chunk_size: int = Field(ge=1)
    dpi: int
    model_name: str
    prompt_version: str
    created_at: str
    last_updated_at: str
    is_complete: bool = False
    completed_chunks: List[ChunkCacheEntry] = Field(default_factory=list)


class ResumeCheckpoint(BaseModel):
    can_resume: bool = False
    invalidation_reason: InvalidationReason = InvalidationReason.NONE
    cached_chunk_indices: List[int] = Field(default_factory=list)
    pending_chunk_indices: List[int] = Field(default_factory=list)
    reusable_ast_entries: List[ChunkCacheEntry] = Field(default_factory=list)
