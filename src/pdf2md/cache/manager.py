"""Cache & Checkpoint Management.

Conforms to specs/models/cache.model and specs/features/caching_and_resume.feature.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import shutil
from typing import Dict, List, Optional, Tuple, Union
from pdf2md.models.ast import DocumentChunkAST
from pdf2md.models.cache import (
    CacheManifest,
    CacheStatus,
    ChunkCacheEntry,
    InvalidationReason,
    ResumeCheckpoint,
)
from pdf2md.models.cli_options import CliConfig


class CacheManager:
    """Manages content-addressed chunk AST caching and resumption."""

    def __init__(self, pdf_path: str, pdf_sha256: str, total_pages: int, config: CliConfig):
        self.pdf_path = pdf_path
        self.pdf_sha256 = pdf_sha256
        self.total_pages = total_pages
        self.config = config
        self.cache_root = os.path.join(config.cache_dir_path, pdf_sha256)
        self.chunks_dir = os.path.join(self.cache_root, "chunks")
        self.renders_dir = os.path.join(self.cache_root, "renders")
        self.manifest_path = os.path.join(self.cache_root, "manifest.json")
        self.manifest: Optional[CacheManifest] = None

        if config.no_cache:
            self._invalidate_all(InvalidationReason.FORCE_FLAG)
        else:
            self._load_or_init_manifest()

    def _invalidate_all(self, reason: InvalidationReason) -> None:
        """Clear cache directory on invalidation or force flag."""
        if os.path.exists(self.cache_root):
            try:
                shutil.rmtree(self.cache_root)
            except Exception:
                pass
        self._init_new_manifest()

    def _init_new_manifest(self) -> None:
        """Create fresh manifest."""
        os.makedirs(self.chunks_dir, exist_ok=True)
        os.makedirs(self.renders_dir, exist_ok=True)
        now_utc = datetime.now(timezone.utc).isoformat()
        file_size = os.path.getsize(self.pdf_path) if os.path.exists(self.pdf_path) else 0

        self.manifest = CacheManifest(
            version="1.0.0",
            pdf_name=os.path.basename(self.pdf_path),
            pdf_sha256=self.pdf_sha256,
            pdf_size_bytes=file_size,
            total_pages=self.total_pages,
            chunk_size=self.config.chunk_size,
            dpi=self.config.dpi,
            model_name=self.config.llm.model_name,
            prompt_version="1.0",
            created_at=now_utc,
            last_updated_at=now_utc,
            is_complete=False,
            completed_chunks=[],
        )
        self._persist_manifest()

    def _load_or_init_manifest(self) -> None:
        """Load manifest from disk or initialize if missing/invalidated."""
        if not os.path.exists(self.manifest_path):
            self._init_new_manifest()
            return

        try:
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            manifest = CacheManifest.model_validate(data)

            # Check configuration compatibility
            if manifest.model_name != self.config.llm.model_name:
                self._invalidate_all(InvalidationReason.MODEL_MISMATCH)
                return
            if manifest.dpi != self.config.dpi:
                self._invalidate_all(InvalidationReason.DPI_MISMATCH)
                return
            if manifest.chunk_size != self.config.chunk_size:
                self._invalidate_all(InvalidationReason.CHUNK_SIZE_MISMATCH)
                return

            self.manifest = manifest
            os.makedirs(self.chunks_dir, exist_ok=True)
            os.makedirs(self.renders_dir, exist_ok=True)
        except Exception:
            self._invalidate_all(InvalidationReason.CHECKSUM_MISMATCH)

    def _persist_manifest(self) -> None:
        """Save manifest to disk."""
        if self.manifest:
            self.manifest.last_updated_at = datetime.now(timezone.utc).isoformat()
            with open(self.manifest_path, "w", encoding="utf-8") as f:
                f.write(self.manifest.model_dump_json(indent=2))

    def is_chunk_cached(self, chunk_key: Union[int, Tuple[int, int]]) -> bool:
        """Return True if chunk exists and AST json file is valid."""
        if not self.manifest or self.config.no_cache:
            return False

        if isinstance(chunk_key, tuple):
            entry = next((c for c in self.manifest.completed_chunks if c.page_range == chunk_key), None)
            if not entry:
                return False
            chunk_file = os.path.join(self.cache_root, entry.ast_json_path)
            return os.path.exists(chunk_file)

        # Fallback / backward compatibility for int chunk_index
        entry = next((c for c in self.manifest.completed_chunks if c.chunk_index == chunk_key), None)
        if entry:
            chunk_file = os.path.join(self.cache_root, entry.ast_json_path)
            return os.path.exists(chunk_file)

        chunk_file = os.path.join(self.chunks_dir, f"chunk_{chunk_key:04d}.json")
        return os.path.exists(chunk_file) and any(c.chunk_index == chunk_key for c in self.manifest.completed_chunks)

    def load_chunk_ast(self, chunk_key: Union[int, Tuple[int, int]]) -> Optional[DocumentChunkAST]:
        """Load cached DocumentChunkAST from disk."""
        if not self.manifest:
            return None

        if isinstance(chunk_key, tuple):
            entry = next((c for c in self.manifest.completed_chunks if c.page_range == chunk_key), None)
            if entry:
                chunk_file = os.path.join(self.cache_root, entry.ast_json_path)
            else:
                chunk_file = os.path.join(self.chunks_dir, f"chunk_p{chunk_key[0]:04d}_p{chunk_key[1]:04d}.json")
        else:
            entry = next((c for c in self.manifest.completed_chunks if c.chunk_index == chunk_key), None)
            if entry:
                chunk_file = os.path.join(self.cache_root, entry.ast_json_path)
            else:
                chunk_file = os.path.join(self.chunks_dir, f"chunk_{chunk_key:04d}.json")

        if not os.path.exists(chunk_file):
            return None

        with open(chunk_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return DocumentChunkAST.model_validate(data)

    def save_chunk_ast(self, chunk_ast: DocumentChunkAST) -> None:
        """Persist completed DocumentChunkAST and update manifest."""
        start_p, end_p = chunk_ast.page_range
        chunk_file = os.path.join(self.chunks_dir, f"chunk_p{start_p:04d}_p{end_p:04d}.json")
        json_str = chunk_ast.model_dump_json(indent=2)
        with open(chunk_file, "w", encoding="utf-8") as f:
            f.write(json_str)

        sha256_ast = hashlib.sha256(json_str.encode("utf-8")).hexdigest()
        now_utc = datetime.now(timezone.utc).isoformat()

        entry = ChunkCacheEntry(
            chunk_index=chunk_ast.chunk_index,
            page_range=chunk_ast.page_range,
            ast_json_path=os.path.relpath(chunk_file, self.cache_root),
            sha256_ast=sha256_ast,
            bypass_used=chunk_ast.bypass_used,
            fallback_used=chunk_ast.fallback_used,
            timestamp_utc=now_utc,
            status=CacheStatus.HIT,
        )

        if self.manifest:
            # Replace existing or append by page_range
            self.manifest.completed_chunks = [
                c for c in self.manifest.completed_chunks if c.page_range != chunk_ast.page_range
            ] + [entry]
            self._persist_manifest()

    def get_chunk_render_dir(self, chunk_key: Union[int, Tuple[int, int]]) -> str:
        """Get path for temporary rendered images for chunk."""
        if isinstance(chunk_key, tuple):
            name = f"chunk_p{chunk_key[0]:04d}_p{chunk_key[1]:04d}"
        else:
            name = f"chunk_{chunk_key:04d}"
        chunk_render_dir = os.path.join(self.renders_dir, name)
        os.makedirs(chunk_render_dir, exist_ok=True)
        return chunk_render_dir

    def purge_chunk_renders(self, chunk_key: Union[int, Tuple[int, int]]) -> None:
        """Purge rendered page bitmaps for chunk to enforce sliding window memory bounds."""
        if isinstance(chunk_key, tuple):
            name = f"chunk_p{chunk_key[0]:04d}_p{chunk_key[1]:04d}"
        else:
            name = f"chunk_{chunk_key:04d}"
        chunk_render_dir = os.path.join(self.renders_dir, name)
        if os.path.exists(chunk_render_dir):
            try:
                shutil.rmtree(chunk_render_dir)
            except Exception:
                pass
