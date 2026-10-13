"""Base Exporter interface and registry for pdf2md multi-format export.

Conforms to Issue #12 (modular multi-format export architecture).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Type

from pdf2md.models.ast import DocumentAST
from pdf2md.models.cli_options import PageMarkerMode


@dataclass
class ExportOptions:
    """Options parameterizing exporter output behavior."""
    page_marker_mode: PageMarkerMode = PageMarkerMode.ANCHOR
    generate_toc: bool = True
    standalone: bool = True
    embed_assets: bool = False
    assets_dir: Optional[Path] = None


class BaseExporter(ABC):
    """Abstract base class for all target format exporters."""

    format_name: str
    file_extension: str

    @abstractmethod
    def export(
        self,
        doc_ast: DocumentAST,
        output_path: Path,
        options: Optional[ExportOptions] = None,
    ) -> None:
        """Serialize DocumentAST and write output to destination path."""
        pass

    def render(
        self,
        doc_ast: DocumentAST,
        options: Optional[ExportOptions] = None,
    ) -> str:
        """Render DocumentAST to string representation where applicable."""
        raise NotImplementedError(f"{self.__class__.__name__} does not implement in-memory render()")


class ExporterRegistry:
    """Registry managing available document exporters."""

    def __init__(self) -> None:
        self._exporters: Dict[str, BaseExporter] = {}

    def register(self, exporter: BaseExporter) -> None:
        """Register an exporter instance under its format name and aliases."""
        self._exporters[exporter.format_name.lower()] = exporter

    def get(self, format_name: str) -> Optional[BaseExporter]:
        """Look up an exporter by format name or extension."""
        key = format_name.lower().lstrip(".")
        return self._exporters.get(key)

    def list_formats(self) -> List[str]:
        """Return list of supported format names."""
        return sorted(list(self._exporters.keys()))


# Global exporter registry
registry = ExporterRegistry()


def register_exporter(exporter: BaseExporter) -> None:
    """Register an exporter in the default global registry."""
    registry.register(exporter)


def get_exporter(format_name: str) -> BaseExporter:
    """Retrieve an exporter by format name from the default registry."""
    exp = registry.get(format_name)
    if exp is None:
        raise ValueError(
            f"Unsupported export format '{format_name}'. Supported formats: {', '.join(registry.list_formats())}"
        )
    return exp


def list_supported_formats() -> List[str]:
    """List all supported formats in the default registry."""
    return registry.list_formats()
