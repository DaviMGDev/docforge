"""HTML5 Exporter conforming to BaseExporter contract and Issue #2."""

from __future__ import annotations

import base64
import html
import mimetypes
import os
from pathlib import Path
from typing import List, Optional

from pdf2md.exceptions import DiskWriteException
from pdf2md.exporters.base import BaseExporter, ExportOptions, register_exporter
from pdf2md.models.ast import (
    Block,
    BlockType,
    DocumentAST,
    HeadingBlock,
    ImageBlock,
    ListBlock,
    MathBlock,
    MathMode,
    ParagraphBlock,
    TableBlock,
    CalloutBlock,
)
from pdf2md.models.cli_options import PageMarkerMode
from pdf2md.models.toc import TocItem


DEFAULT_CSS = """
:root {
  --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  --font-mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  --bg-color: #ffffff;
  --text-color: #24292f;
  --border-color: #d0d7de;
  --link-color: #0969da;
  --code-bg: #f6f8fa;
  --callout-bg: #f8fafc;
  --callout-note: #0969da;
  --callout-tip: #1a7f37;
  --callout-warning: #9a6700;
  --callout-caution: #d1242f;
  --callout-important: #8250df;
}

@media (prefers-color-scheme: dark) {
  :root {
    --bg-color: #0d1117;
    --text-color: #c9d1d9;
    --border-color: #30363d;
    --link-color: #58a6ff;
    --code-bg: #161b22;
    --callout-bg: #161b22;
  }
}

body {
  font-family: var(--font-sans);
  line-height: 1.6;
  color: var(--text-color);
  background-color: var(--bg-color);
  margin: 0;
  padding: 2rem 1rem;
}

.container {
  max-width: 900px;
  margin: 0 auto;
  padding: 0 1rem;
}

a {
  color: var(--link-color);
  text-decoration: none;
}
a:hover {
  text-decoration: underline;
}

h1, h2, h3, h4, h5, h6 {
  margin-top: 1.5em;
  margin-bottom: 0.5em;
  font-weight: 600;
  line-height: 1.25;
}

h1 { font-size: 2em; border-bottom: 1px solid var(--border-color); padding-bottom: 0.3em; }
h2 { font-size: 1.5em; border-bottom: 1px solid var(--border-color); padding-bottom: 0.3em; }
h3 { font-size: 1.25em; }

p {
  margin-top: 0;
  margin-bottom: 1em;
}

pre {
  background-color: var(--code-bg);
  border-radius: 6px;
  padding: 16px;
  overflow: auto;
  font-family: var(--font-mono);
  font-size: 85%;
  line-height: 1.45;
  border: 1px solid var(--border-color);
}

code {
  font-family: var(--font-mono);
  font-size: 85%;
  background-color: var(--code-bg);
  padding: 0.2em 0.4em;
  border-radius: 3px;
}

pre code {
  background-color: transparent;
  padding: 0;
}

table {
  border-collapse: collapse;
  width: 100%;
  margin: 1em 0;
  display: block;
  overflow-x: auto;
}

th, td {
  border: 1px solid var(--border-color);
  padding: 6px 13px;
}

th {
  background-color: var(--code-bg);
  font-weight: 600;
}

tr:nth-child(2n) {
  background-color: rgba(127, 127, 127, 0.05);
}

blockquote, .callout {
  padding: 0.5em 1em;
  margin: 1em 0;
  border-left: 0.25em solid var(--border-color);
  background-color: var(--callout-bg);
  border-radius: 0 6px 6px 0;
}

.callout-note { border-left-color: var(--callout-note); }
.callout-tip { border-left-color: var(--callout-tip); }
.callout-warning { border-left-color: var(--callout-warning); }
.callout-caution { border-left-color: var(--callout-caution); }
.callout-important { border-left-color: var(--callout-important); }

.callout-title {
  font-weight: 600;
  margin-bottom: 0.3em;
  display: flex;
  align-items: center;
  gap: 0.5em;
}

.page-badge {
  text-align: right;
  margin: 1.5rem 0 0.5rem 0;
}

.page-badge .badge {
  font-size: 0.75rem;
  color: #6e7781;
  background-color: var(--code-bg);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  padding: 2px 8px;
}

.toc {
  background-color: var(--code-bg);
  border: 1px solid var(--border-color);
  border-radius: 6px;
  padding: 1rem 1.5rem;
  margin-bottom: 2rem;
}

.toc h2 {
  margin-top: 0;
  font-size: 1.25rem;
  border-bottom: none;
}

.toc ul {
  padding-left: 1.5rem;
  margin-bottom: 0;
}

.math.display {
  overflow-x: auto;
  text-align: center;
  margin: 1em 0;
}
"""


def render_html_page_marker(page_num: int, mode: PageMarkerMode) -> str:
    """Format HTML page marker based on configured mode."""
    if mode == PageMarkerMode.ANCHOR:
        return f'<span id="page-{page_num}" class="page-marker" data-page="{page_num}"></span>\n<!-- [Page {page_num}] -->\n'
    elif mode == PageMarkerMode.BADGE:
        return f'<div class="page-badge"><span class="badge">📄 Page {page_num}</span></div>\n'
    elif mode == PageMarkerMode.COMMENT:
        return f'<!-- [Page {page_num}] -->\n'
    return ""


def encode_image_base64(image_path: str, assets_dir: Optional[Path] = None) -> Optional[str]:
    """Encode image file to base64 data URI."""
    cand = Path(image_path)
    if not cand.exists() and assets_dir:
        cand = assets_dir / image_path

    if cand.is_file():
        mime_type, _ = mimetypes.guess_type(cand.name)
        mime_type = mime_type or "image/png"
        try:
            encoded = base64.b64encode(cand.read_bytes()).decode("ascii")
            return f"data:{mime_type};base64,{encoded}"
        except Exception:
            return None
    return None


class HtmlExporter(BaseExporter):
    """Exports DocumentAST to semantic HTML5 documents or fragments."""

    format_name = "html"
    file_extension = ".html"

    def render_block(
        self,
        block: Block,
        embed_assets: bool = False,
        assets_dir: Optional[Path] = None,
    ) -> str:
        """Render single AST block to HTML element."""
        if block.type == BlockType.HEADING:
            tag = f"h{block.level}"
            slug_attr = f' id="{html.escape(block.slug)}"' if block.slug else ""
            return f'<{tag}{slug_attr}>{html.escape(block.content)}</{tag}>\n'

        elif block.type == BlockType.PARAGRAPH:
            return f'<p>{html.escape(block.content)}</p>\n'

        elif block.type == BlockType.LIST:
            tag = "ol" if block.ordered else "ul"
            start_attr = f' start="{block.start_index}"' if block.ordered and block.start_index else ""
            items_html: List[str] = []
            for item in block.items:
                sub_html = ""
                if item.sub_items:
                    sub_tag = "ul"
                    sub_lis = "".join(f"<li>{html.escape(s.content)}</li>" for s in item.sub_items)
                    sub_html = f"<{sub_tag}>{sub_lis}</{sub_tag}>"
                items_html.append(f"<li>{html.escape(item.content)}{sub_html}</li>")
            return f'<{tag}{start_attr}>\n' + "\n".join(items_html) + f'\n</{tag}>\n'

        elif block.type == BlockType.CODE:
            lang = html.escape(block.language or "")
            class_attr = f' class="language-{lang}"' if lang else ""
            escaped_code = html.escape(block.content.rstrip())
            caption = f'<figcaption>{html.escape(block.caption)}</figcaption>\n' if block.caption else ""
            code_el = f'<pre><code{class_attr}>{escaped_code}</code></pre>\n'
            if caption:
                return f'<figure>{code_el}{caption}</figure>\n'
            return code_el

        elif block.type == BlockType.MATH:
            content = block.content.strip()
            if block.math_mode == MathMode.BLOCK:
                tag_str = f' \\tag{{{block.equation_number}}}' if block.equation_number else ""
                return f'<div class="math display">\\[\n{html.escape(content + tag_str)}\n\\]</div>\n'
            else:
                return f'<span class="math inline">\\({html.escape(content)}\\)</span>'

        elif block.type == BlockType.TABLE:
            if block.raw_html and block.raw_html.strip():
                return block.raw_html.strip() + "\n"

            lines = ['<table>']
            if block.caption:
                lines.append(f'  <caption>{html.escape(block.caption)}</caption>')

            if block.header:
                lines.append('  <thead><tr>')
                for h in block.header:
                    lines.append(f'    <th>{html.escape(h.strip())}</th>')
                lines.append('  </tr></thead>')

            if block.rows:
                lines.append('  <tbody>')
                for row in block.rows:
                    lines.append('    <tr>')
                    for cell in row:
                        lines.append(f'      <td>{html.escape(cell.strip())}</td>')
                    lines.append('    </tr>')
                lines.append('  </tbody>')

            lines.append('</table>\n')
            return "\n".join(lines)

        elif block.type == BlockType.IMAGE:
            src = block.asset_path
            if embed_assets:
                data_uri = encode_image_base64(block.asset_path, assets_dir)
                if data_uri:
                    src = data_uri

            alt = html.escape(block.alt_text or "")
            src_attr = html.escape(src)
            img_html = f'<img src="{src_attr}" alt="{alt}" />'
            if block.caption:
                return f'<figure>{img_html}<figcaption>{html.escape(block.caption)}</figcaption></figure>\n'
            return f'<figure>{img_html}</figure>\n'

        elif block.type == BlockType.CALLOUT:
            c_type = block.callout_type.value if hasattr(block.callout_type, "value") else str(block.callout_type)
            c_class = f"callout callout-{c_type.lower()}"
            title_html = f'<div class="callout-title"><strong>{html.escape(block.title or c_type.upper())}</strong></div>'
            body_html = f'<div class="callout-body">{html.escape(block.content)}</div>'
            return f'<aside class="{c_class}">{title_html}{body_html}</aside>\n'

        elif block.type == BlockType.THEMATIC_BREAK:
            return '<hr />\n'

        return ""

    def render_toc(self, toc_items: List[TocItem]) -> str:
        """Render interactive HTML Table of Contents navigation list."""
        if not toc_items:
            return ""
        lines = ['<nav class="toc">', '  <h2>Table of Contents</h2>', '  <ul>']
        for item in toc_items:
            slug = html.escape(item.target_slug)
            title = html.escape(item.title)
            lines.append(
                f'    <li style="margin-left: {(item.level - 1) * 1.25}rem;">'
                f'<a href="#{slug}">{title}</a> <span class="toc-page">(p. {item.target_page})</span></li>'
            )
        lines.append('  </ul>')
        lines.append('</nav>\n')
        return "\n".join(lines)

    def render(
        self,
        doc_ast: DocumentAST,
        options: Optional[ExportOptions] = None,
    ) -> str:
        opts = options or ExportOptions()
        body_parts: List[str] = []

        # 1. TOC
        if opts.generate_toc and doc_ast.toc_items:
            body_parts.append(self.render_toc(doc_ast.toc_items))

        # 2. Blocks with page markers
        current_page = 0
        for block in doc_ast.blocks:
            if block.page != current_page:
                current_page = block.page
                marker = render_html_page_marker(current_page, opts.page_marker_mode)
                if marker:
                    body_parts.append(marker)

            rendered = self.render_block(
                block,
                embed_assets=opts.embed_assets,
                assets_dir=opts.assets_dir,
            )
            if rendered:
                body_parts.append(rendered)

        fragment = "\n".join(body_parts)

        if not opts.standalone:
            return fragment.strip() + "\n"

        # Standalone HTML5 document wrap
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Document</title>
  <style>
{DEFAULT_CSS.strip()}
  </style>
  <script>
    MathJax = {{
      tex: {{
        inlineMath: [['$', '$'], ['\\\\(', '\\\\)']],
        displayMath: [['$$', '$$'], ['\\\\[', '\\\\]']]
      }}
    }};
  </script>
  <script id="MathJax-script" async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
</head>
<body>
  <div class="container">
{fragment}
  </div>
</body>
</html>
"""

    def export(
        self,
        doc_ast: DocumentAST,
        output_path: Path,
        options: Optional[ExportOptions] = None,
    ) -> None:
        opts = options or ExportOptions()
        if opts.assets_dir is None:
            opts.assets_dir = output_path.parent / "assets"
        content = self.render(doc_ast, opts)
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(content, encoding="utf-8")
        except Exception as e:
            raise DiskWriteException(f"Failed to write HTML output to {output_path}: {e}") from e


# Register default instance
register_exporter(HtmlExporter())
