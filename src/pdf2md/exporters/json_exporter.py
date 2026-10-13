"""JSON exporter conforming to BaseExporter contract."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from pdf2md.exceptions import DiskWriteException
from pdf2md.exporters.base import BaseExporter, ExportOptions, register_exporter
from pdf2md.models.ast import DocumentAST


class JsonExporter(BaseExporter):
    """Exports DocumentAST to formatted JSON representation."""

    format_name = "json"
    file_extension = ".json"

    def render(
        self,
        doc_ast: DocumentAST,
        options: Optional[ExportOptions] = None,
    ) -> str:
        return doc_ast.model_dump_json(indent=2)

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
            raise DiskWriteException(f"Failed to write JSON output to {output_path}: {e}") from e


# Register default instance
register_exporter(JsonExporter())
