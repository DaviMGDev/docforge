"""Unit and integration tests for modular exporters (Markdown, JSON, HTML, Typst)."""

import json
from datetime import datetime
from pathlib import Path
import pytest

from pdf2md.exporters import (
    BaseExporter,
    ExportOptions,
    ExporterRegistry,
    HtmlExporter,
    JsonExporter,
    MarkdownExporter,
    TypstExporter,
    get_exporter,
    list_supported_formats,
    register_exporter,
)
from pdf2md.models.ast import (
    CalloutBlock,
    CalloutType,
    CodeBlock,
    DocumentAST,
    HeadingBlock,
    ImageBlock,
    ListBlock,
    ListItem,
    MathBlock,
    MathMode,
    ParagraphBlock,
    TableBlock,
)
from pdf2md.models.cli_options import PageMarkerMode
from pdf2md.models.toc import TocItem, TocSource


@pytest.fixture
def sample_ast():
    return DocumentAST(
        source_pdf_hash="sample_hash",
        total_pages=2,
        selected_pages=[1, 2],
        chunks_count=1,
        stitched_at=datetime.now().isoformat(),
        toc_items=[
            TocItem(
                id="toc-1",
                title="Chapter 1",
                level=1,
                target_page=1,
                target_slug="chap-1",
                source=TocSource.AST_HEADING,
            ),
        ],
        blocks=[
            HeadingBlock(page=1, level=1, content="Chapter 1", slug="chap-1"),
            ParagraphBlock(page=1, content="This is an introductory paragraph."),
            ListBlock(
                page=1,
                ordered=False,
                items=[ListItem(content="Point A"), ListItem(content="Point B")],
            ),
            CodeBlock(page=1, content="print('hello')", language="python", caption="Code 1"),
            TableBlock(page=2, header=["Header 1", "Header 2"], rows=[["Val 1", "Val 2"]], caption="Table 1"),
            MathBlock(page=2, content="E = mc^2", math_mode=MathMode.INLINE),
            MathBlock(page=2, content=r"\frac{a}{b} = c", math_mode=MathMode.BLOCK, equation_number="1"),
            CalloutBlock(page=2, callout_type=CalloutType.WARNING, content="Caution required", title="Warning Box"),
        ],
    )


def test_registry_lookups():
    assert "md" in list_supported_formats()
    assert "json" in list_supported_formats()
    assert "html" in list_supported_formats()
    assert "typ" in list_supported_formats()

    exp_md = get_exporter("md")
    assert isinstance(exp_md, MarkdownExporter)

    exp_json = get_exporter("json")
    assert isinstance(exp_json, JsonExporter)

    exp_html = get_exporter("html")
    assert isinstance(exp_html, HtmlExporter)

    exp_typ = get_exporter("typ")
    assert isinstance(exp_typ, TypstExporter)

    with pytest.raises(ValueError, match="Unsupported export format"):
        get_exporter("unknown_format_xyz")


def test_markdown_exporter(sample_ast, tmp_path):
    exporter = MarkdownExporter()
    out = exporter.render(sample_ast)
    assert "# Chapter 1" in out
    assert "This is an introductory paragraph." in out
    assert "- Point A" in out
    assert "```python\nprint('hello')" in out
    assert "| Header 1 | Header 2 |" in out
    assert "$E = mc^2$" in out
    assert "$$\n\\frac{a}{b} = c \\tag{1}\n$$" in out
    assert "> [!WARNING] **Warning Box**" in out

    out_file = tmp_path / "doc.md"
    exporter.export(sample_ast, out_file)
    assert out_file.exists()
    assert out_file.read_text(encoding="utf-8") == out


def test_json_exporter(sample_ast, tmp_path):
    exporter = JsonExporter()
    out = exporter.render(sample_ast)
    data = json.loads(out)
    assert data["source_pdf_hash"] == "sample_hash"
    assert len(data["blocks"]) == len(sample_ast.blocks)

    out_file = tmp_path / "doc.json"
    exporter.export(sample_ast, out_file)
    assert out_file.exists()
    assert json.loads(out_file.read_text(encoding="utf-8")) == data


def test_html_exporter_standalone(sample_ast, tmp_path):
    exporter = HtmlExporter()
    options = ExportOptions(standalone=True, page_marker_mode=PageMarkerMode.ANCHOR)
    out = exporter.render(sample_ast, options)

    assert "<!DOCTYPE html>" in out
    assert '<meta name="viewport"' in out
    assert "MathJax" in out
    assert '<h1 id="chap-1">Chapter 1</h1>' in out
    assert "<p>This is an introductory paragraph.</p>" in out
    assert "<ul>\n<li>Point A</li>\n<li>Point B</li>\n</ul>" in out
    assert '<code class="language-python">print(&#x27;hello&#x27;)</code>' in out
    assert "<table>" in out and "<th>Header 1</th>" in out and "<td>Val 1</td>" in out
    assert '<span class="math inline">\\(E = mc^2\\)</span>' in out
    assert 'class="callout callout-warning"' in out

    out_file = tmp_path / "doc.html"
    exporter.export(sample_ast, out_file, options)
    assert out_file.exists()
    assert out_file.read_text(encoding="utf-8") == out


def test_html_exporter_fragment(sample_ast):
    exporter = HtmlExporter()
    options = ExportOptions(standalone=False)
    out = exporter.render(sample_ast, options)

    assert "<!DOCTYPE html>" not in out
    assert "<html>" not in out
    assert '<h1 id="chap-1">Chapter 1</h1>' in out


def test_html_exporter_embed_assets(tmp_path):
    img_path = tmp_path / "sample.png"
    img_path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRtestimagecontent")

    ast = DocumentAST(
        source_pdf_hash="hash",
        total_pages=1,
        selected_pages=[1],
        chunks_count=1,
        stitched_at=datetime.now().isoformat(),
        blocks=[
            ImageBlock(page=1, asset_path=str(img_path), alt_text="Alt Text", caption="Caption Text")
        ],
    )
    exporter = HtmlExporter()
    out = exporter.render(ast, ExportOptions(embed_assets=True))
    assert 'data:image/png;base64,' in out
    assert 'alt="Alt Text"' in out
    assert "<figcaption>Caption Text</figcaption>" in out


def test_typst_exporter(sample_ast, tmp_path):
    exporter = TypstExporter()
    options = ExportOptions(standalone=True)
    out = exporter.render(sample_ast, options)

    assert '#set document(title: "Chapter 1")' in out
    assert "= Chapter 1 <chap-1>" in out
    assert "This is an introductory paragraph." in out
    assert "- Point A" in out
    assert "```python\nprint('hello')\n```" in out
    assert "table(" in out and "[* Header 1 *]" in out and "[Val 1]" in out
    assert "$E = m c^2$" in out
    assert "$ (a) / (b) = c $ // tag: 1" in out
    assert "#rect(" in out and "Warning Box" in out

    out_file = tmp_path / "doc.typ"
    exporter.export(sample_ast, out_file, options)
    assert out_file.exists()
    assert out_file.read_text(encoding="utf-8") == out

    import shutil
    import subprocess
    if shutil.which("typst"):
        proc = subprocess.run(["typst", "compile", str(out_file), str(tmp_path / "doc.pdf")], capture_output=True, text=True)
        assert proc.returncode == 0, f"Typst compile failed: {proc.stderr}"
