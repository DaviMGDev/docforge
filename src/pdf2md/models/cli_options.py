"""CLI Options & Configuration Models conforming to specs/models/cli_options.model."""

from __future__ import annotations

import os
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field, model_validator


class OutputFormat(str, Enum):
    MD = "md"
    JSON = "json"
    BOTH = "both"


class PageMarkerMode(str, Enum):
    ANCHOR = "anchor"
    BADGE = "badge"
    COMMENT = "comment"
    NONE = "none"


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"


class RangeSpec(BaseModel):
    raw_input: str
    normalized_pages: List[int] = Field(default_factory=list)


class ConcurrencyConfig(BaseModel):
    cpu_workers: int = Field(default_factory=lambda: min(os.cpu_count() or 1, 4), ge=1)
    llm_concurrency: int = Field(default=3, ge=1, le=10)
    sliding_window_size: int = Field(default=6, ge=1)

    @model_validator(mode="after")
    def validate_invariants(self) -> ConcurrencyConfig:
        if self.sliding_window_size < self.llm_concurrency:
            self.sliding_window_size = self.llm_concurrency * 2
        return self


class LlmConfig(BaseModel):
    model_name: str = "gpt-4o"
    api_key: Optional[str] = None
    api_base: Optional[str] = None
    temperature: float = 0.0
    max_tokens: int = 4096
    request_timeout_seconds: float = 120.0
    max_retries: int = 3


class CliConfig(BaseModel):
    input_pdf_path: str
    output_path: str
    output_format: OutputFormat = OutputFormat.MD
    assets_dir_path: str = "assets"
    cache_dir_path: str = ".cache"
    pages_spec: Optional[RangeSpec] = None
    page_marker_mode: PageMarkerMode = PageMarkerMode.ANCHOR
    generate_toc: bool = True
    chunk_size: int = Field(default=4, ge=1, le=16)
    adaptive_chunking: bool = True
    no_stitch: bool = False
    dpi: int = Field(default=150, ge=72, le=400)
    auto_bypass: bool = False
    no_cache: bool = False
    verbose: bool = False
    concurrency: ConcurrencyConfig = Field(default_factory=ConcurrencyConfig)
    llm: LlmConfig = Field(default_factory=LlmConfig)
