"""Typst Exporter conforming to BaseExporter contract and Issue #13."""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

from docforge.exceptions import DiskWriteException
from docforge.exporters.base import BaseExporter, ExportOptions, register_exporter
from docforge.models.ast import (
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
from docforge.models.cli_options import PageMarkerMode


def escape_typst_text(text: str) -> str:
    """Escape special Typst markup characters in plain text."""
    # Special characters in Typst: #, $, [, ], *, _, `, <, >, @
    # For general text blocks, keep standard text readable while preventing unintended macro evaluation
    return text.replace("\\", "\\\\").replace("#", "\\#")


def escape_typst_string(text: str) -> str:
    """Escape string literals within Typst string quotes."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def format_typst_page_marker(page_num: int, mode: PageMarkerMode) -> str:
    """Format Typst page boundary marker."""
    if mode == PageMarkerMode.ANCHOR:
        return f"// [Page {page_num}] <page-{page_num}>\n"
    elif mode == PageMarkerMode.BADGE:
        return f"#align(right)[#text(size: 8pt, fill: gray.darken(20%))[📄 Page {page_num}]]\n"
    elif mode == PageMarkerMode.COMMENT:
        return f"// [Page {page_num}]\n"
    return ""


KNOWN_TYPST_MATH_WORDS = {
    "sin", "cos", "tan", "cot", "sec", "csc", "arcsin", "arccos", "arctan",
    "sinh", "cosh", "tanh", "ln", "log", "exp", "det", "gcd", "lcm", "min", "max",
    "lim", "mod", "deg", "dim", "ker", "hom", "arg", "Pr", "inf", "sup", "sqrt",
    "frac", "binom", "abs", "norm", "floor", "ceil", "round", "bold", "italic",
    "mono", "sans", "serif", "cal", "frak", "bb", "dot", "times", "plus", "minus",
    "infinity", "integral", "diff", "sum", "product", "forall", "exists", "nabla",
    "partial", "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta",
    "iota", "kappa", "lambda", "mu", "nu", "xi", "pi", "rho", "sigma", "tau",
    "upsilon", "phi", "chi", "psi", "omega", "Gamma", "Delta", "Theta", "Lambda",
    "Xi", "Pi", "Sigma", "Phi", "Psi", "Omega"
}


def convert_latex_to_typst_math(tex: str) -> str:
    """Basic normalization of common LaTeX math expressions for Typst."""
    s = tex.strip()
    # Typst handles raw math cleanly inside $ $, but replacing common LaTeX syntax produces idiomatic Typst
    # \frac{a}{b} -> (a)/(b) or frac(a, b)
    s = re.sub(r"\\frac\{([^{}]+)\}\{([^{}]+)\}", r"(\1) / (\2)", s)
    # \cdot -> dot
    s = s.replace(r"\cdot", "dot")
    # \times -> times
    s = s.replace(r"\times", "times")
    # \pm -> plus.minus
    s = s.replace(r"\pm", "plus.minus")
    # \infty -> infinity
    s = s.replace(r"\infty", "infinity")
    # \le, \leq -> <=
    s = re.sub(r"\\le(q)?\b", "<=", s)
    # \ge, \geq -> >=
    s = re.sub(r"\\ge(q)?\b", ">=", s)
    # \ne, \neq -> !=
    s = re.sub(r"\\ne(q)?\b", "!=", s)
    # \to -> ->
    s = s.replace(r"\to", "->")
    # \alpha, \beta, etc. strip backslash
    greek = [
        "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta",
        "iota", "kappa", "lambda", "mu", "nu", "xi", "pi", "rho", "sigma",
        "tau", "upsilon", "phi", "chi", "psi", "omega",
        "Gamma", "Delta", "Theta", "Lambda", "Xi", "Pi", "Sigma", "Phi", "Psi", "Omega"
    ]
    for g in greek:
        s = re.sub(rf"\\{g}\b", g, s)
    # \text{...} -> "..."
    s = re.sub(r"\\text\{([^{}]+)\}", r'"\1"', s)
    # \mathbf{...} -> bold(...)
    s = re.sub(r"\\mathbf\{([^{}]+)\}", r"bold(\1)", s)
    # \mathit{...} -> italic(...)
    s = re.sub(r"\\mathit\{([^{}]+)\}", r"italic(\1)", s)
    # \sqrt{...} -> sqrt(...)
    s = re.sub(r"\\sqrt\{([^{}]+)\}", r"sqrt(\1)", s)

    # In Typst, juxtaposed multi-letter variables like 'mc' are parsed as unknown symbols.
    # Separate adjacent latin variables not matching known functions/symbols:
    s = re.sub(
        r"\b[a-zA-Z]{2,}\b",
        lambda m: m.group(0) if m.group(0) in KNOWN_TYPST_MATH_WORDS else " ".join(list(m.group(0))),
        s,
    )
    return s


class TypstExporter(BaseExporter):
    """Exports DocumentAST to native Typst (.typ) source documents."""

    format_name = "typ"
    file_extension = ".typ"

    def render_block(self, block: Block) -> str:
        """Serialize individual AST block to Typst syntax."""
        if block.type == BlockType.HEADING:
            equals = "=" * min(block.level, 6)
            label = f" <{block.slug}>" if block.slug else ""
            return f"{equals} {block.content}{label}\n"

        elif block.type == BlockType.PARAGRAPH:
            return f"{block.content}\n"

        elif block.type == BlockType.LIST:
            lines: List[str] = []
            for i, item in enumerate(block.items):
                pfx = "+ " if block.ordered else "- "
                lines.append(f"{pfx}{item.content}")
                for sub in item.sub_items:
                    lines.append(f"  - {sub.content}")
            return "\n".join(lines) + "\n"

        elif block.type == BlockType.CODE:
            lang = block.language or ""
            fenced = f"```{lang}\n{block.content.rstrip()}\n```"
            if block.caption:
                cap_esc = escape_typst_string(block.caption)
                return f"#figure(\n  [{fenced}],\n  caption: [{cap_esc}]\n)\n"
            return f"{fenced}\n"

        elif block.type == BlockType.MATH:
            math_expr = convert_latex_to_typst_math(block.content)
            if block.math_mode == MathMode.BLOCK:
                tag_comment = f" // tag: {block.equation_number}" if block.equation_number else ""
                return f"$ {math_expr} ${tag_comment}\n"
            else:
                return f"${math_expr}$"

        elif block.type == BlockType.TABLE:
            col_count = len(block.header) if block.header else (len(block.rows[0]) if block.rows else 1)
            col_count = max(col_count, 1)

            cells: List[str] = []
            if block.header:
                for h in block.header:
                    cells.append(f"  [* {escape_typst_text(h.strip())} *],")
            if block.rows:
                for row in block.rows:
                    for cell in row:
                        cells.append(f"  [{escape_typst_text(cell.strip())}],")

            cells_joined = "\n".join(cells)
            if block.caption:
                cap_esc = escape_typst_string(block.caption)
                return (
                    f"#figure(\n"
                    f"  table(\n"
                    f"    columns: {col_count},\n"
                    f"{cells_joined}\n"
                    f"  ),\n"
                    f"  caption: [{cap_esc}]\n"
                    f")\n"
                )
            return f"#table(\n  columns: {col_count},\n{cells_joined}\n)\n"

        elif block.type == BlockType.IMAGE:
            img_path_obj = Path(block.asset_path)
            typst_supported_exts = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
            actual_path = block.asset_path
            if img_path_obj.suffix.lower() not in typst_supported_exts:
                png_path = img_path_obj.with_suffix(".png")
                if png_path.exists():
                    actual_path = str(png_path)
                elif img_path_obj.exists():
                    try:
                        import pymupdf as fitz
                        pix = fitz.Pixmap(str(img_path_obj))
                        pix.save(str(png_path))
                        actual_path = str(png_path)
                    except Exception:
                        pass

            path_esc = escape_typst_string(actual_path)
            if block.caption or block.alt_text:
                caption_text = block.caption or block.alt_text or ""
                cap_esc = escape_typst_string(caption_text)
                return f'#figure(\n  image("{path_esc}"),\n  caption: [{cap_esc}]\n)\n'
            return f'#image("{path_esc}")\n'

        elif block.type == BlockType.CALLOUT:
            c_type = block.callout_type.value if hasattr(block.callout_type, "value") else str(block.callout_type)
            color_map = {
                "NOTE": "blue",
                "TIP": "green",
                "WARNING": "orange",
                "CAUTION": "red",
                "IMPORTANT": "purple",
            }
            stroke_color = color_map.get(c_type.upper(), "blue")
            title = block.title or c_type.upper()
            title_line = f"*{escape_typst_text(title)}* \\ \n" if title else ""
            body_text = escape_typst_text(block.content)

            return (
                f"#rect(\n"
                f"  width: 100%,\n"
                f"  stroke: (left: 4pt + {stroke_color}),\n"
                f'  fill: rgb("f8fafc"),\n'
                f"  radius: (right: 4pt),\n"
                f"  inset: 10pt,\n"
                f")[\n"
                f"  {title_line}{body_text}\n"
                f"]\n"
            )

        elif block.type == BlockType.THEMATIC_BREAK:
            return "#line(length: 100%)\n"

        return ""

    def render(
        self,
        doc_ast: DocumentAST,
        options: Optional[ExportOptions] = None,
    ) -> str:
        opts = options or ExportOptions()
        parts: List[str] = []

        if opts.standalone:
            title_text = "Document"
            # Try to derive title from the first level-1 heading
            for b in doc_ast.blocks:
                if b.type == BlockType.HEADING and getattr(b, "level", 0) == 1:
                    title_text = getattr(b, "content", "Document")
                    break

            preamble = (
                f'#set document(title: "{escape_typst_string(title_text)}")\n'
                f'#set page(paper: "a4", margin: (x: 2cm, y: 2.5cm))\n'
                f'#set text(font: ("Liberation Sans", "DejaVu Sans"), size: 10.5pt)\n'
                f'#set par(justify: true, leading: 0.65em)\n\n'
            )
            parts.append(preamble)

        if opts.generate_toc and doc_ast.toc_items:
            parts.append('#outline(title: "Table of Contents", depth: 3, indent: auto)\n\n')

        current_page = 0
        for block in doc_ast.blocks:
            if block.page != current_page:
                current_page = block.page
                marker = format_typst_page_marker(current_page, opts.page_marker_mode)
                if marker:
                    parts.append(marker)

            rendered = self.render_block(block)
            if rendered:
                parts.append(rendered)
                parts.append("\n")

        return "".join(parts).strip() + "\n"

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
            raise DiskWriteException(f"Failed to write Typst output to {output_path}: {e}") from e


# Register default instance under both 'typ' and alias 'typst'
typst_exporter = TypstExporter()
register_exporter(typst_exporter)
