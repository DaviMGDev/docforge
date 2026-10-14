"""Markdown exporter conforming to BaseExporter contract."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from docforge.exceptions import DiskWriteException
from docforge.exporters.base import BaseExporter, ExportOptions, register_exporter
from docforge.models.ast import DocumentAST
from docforge.models.cli_options import PageMarkerMode
from docforge.stages.synthesis import emit_markdown


class MarkdownExporter(BaseExporter):
    """Exports DocumentAST to CommonMark / GFM Markdown."""

    format_name = "md"
    file_extension = ".md"

    def render(
        self,
        doc_ast: DocumentAST,
        options: Optional[ExportOptions] = None,
    ) -> str:
        opts = options or ExportOptions()
        return emit_markdown(
            doc_ast=doc_ast,
            toc_items=doc_ast.toc_items or [],
            marker_mode=opts.page_marker_mode,
            include_toc=opts.generate_toc,
        )

    def export(
        self,
        doc_ast: DocumentAST,
        output_path: Path,
        options: Optional[ExportOptions] = None,
    ) -> None:
        content = self.render(doc_ast, options)
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(content, encoding="utf-8")
        except Exception as e:
            raise DiskWriteException(f"Failed to write markdown output to {output_path}: {e}") from e


# Register default instance
register_exporter(MarkdownExporter())
