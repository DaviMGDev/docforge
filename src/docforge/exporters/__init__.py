"""pdf2md exporters package: modular multi-format export architecture."""

from docforge.exporters.base import (
    BaseExporter,
    ExportOptions,
    ExporterRegistry,
    get_exporter,
    list_supported_formats,
    register_exporter,
    registry,
)
from docforge.exporters.markdown import MarkdownExporter
from docforge.exporters.json_exporter import JsonExporter
from docforge.exporters.html import HtmlExporter
from docforge.exporters.typst import TypstExporter

# Register typst alias
registry.register(registry.get("typ"))
if registry.get("typ"):
    registry._exporters["typst"] = registry.get("typ")

__all__ = [
    "BaseExporter",
    "ExportOptions",
    "ExporterRegistry",
    "HtmlExporter",
    "JsonExporter",
    "MarkdownExporter",
    "TypstExporter",
    "get_exporter",
    "list_supported_formats",
    "register_exporter",
    "registry",
]
