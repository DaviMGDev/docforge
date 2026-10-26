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
    "sinh", "cosh", "tanh", "coth", "ln", "log", "exp", "det", "gcd", "lcm", "min", "max",
    "lim", "mod", "deg", "dim", "ker", "hom", "arg", "Pr", "inf", "sup", "sqrt", "root",
    "frac", "binom", "abs", "norm", "floor", "ceil", "round", "bold", "italic",
    "mono", "sans", "serif", "cal", "frak", "bb", "dot", "times", "plus", "minus",
    "infinity", "integral", "diff", "sum", "product", "forall", "exists", "nabla",
    "partial", "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta",
    "iota", "kappa", "lambda", "mu", "nu", "xi", "pi", "rho", "sigma", "tau",
    "upsilon", "phi", "chi", "psi", "omega", "Gamma", "Delta", "Theta", "Lambda",
    "Xi", "Pi", "Sigma", "Phi", "Psi", "Omega", "degree", "arrow", "hat", "macron",
    "tilde", "overline", "underline", "approx", "equiv", "prop", "dots", "h.bar", "ell",
    "double", "triple", "cont", "bar", "acute", "grave", "caron", "breve"
}

LATEX_MATH_FUNCTIONS = {
    "sin", "cos", "tan", "cot", "sec", "csc",
    "arcsin", "arccos", "arctan",
    "sinh", "cosh", "tanh", "coth",
    "ln", "log", "exp",
    "det", "gcd", "lcm", "min", "max",
    "lim", "mod", "deg", "dim", "ker", "hom", "arg", "Pr", "inf", "sup",
}

_MATH_FN_PATTERN = re.compile(
    rf"\\({'|'.join(sorted(LATEX_MATH_FUNCTIONS, key=len, reverse=True))})(?![a-zA-Z])([\s(\^_]*)"
)


def sanitize_currency_expressions(text: str) -> str:
    """Normalize monetary expressions (e.g. $1400, $500,000, $10^9, $\\$100$) so they don't toggle Typst math mode."""
    if not text:
        return ""

    # 1. Math-wrapped currency with backslash: $\\$1400$, $\\$10^9$, $\\$500,000$
    s = re.sub(
        r"\$\s*\\\$([0-9][0-9,]*(?:\.[0-9]+)?(?:\s*\^\s*\{?[0-9]+\}?)?)\s*\$",
        r"\\$\1",
        text,
    )

    # 2. Purely numeric/exponent spans between $: $100$, $1400$, $500,000$, $10^9$
    s = re.sub(
        r"(?<!\\)\$([0-9][0-9,]*(?:\.[0-9]+)?(?:\s*\^\s*\{?[0-9]+\}?)?)\$",
        r"\\$\1",
        s,
    )

    # 3. Currency amounts in prose: $1400, $500,000, $10^9, $0.50
    # Preceded by start of string, whitespace, or open punctuation
    s = re.sub(
        r"(^|[\s([{\"'`])(?<!\\)\$([0-9][0-9,]*(?:\.[0-9]+)?(?:\s*\^\s*\{?[0-9]+\}?)?)\b",
        r"\1\\$\2",
        s,
    )

    return s


def convert_latex_to_typst_math(tex: str) -> str:
    """Comprehensive normalization of LaTeX math expressions to valid Typst math mode."""
    s = tex.strip()
    if not s:
        return ""

    # Preserve and protect string literals like \text{...}
    texts: List[str] = []

    def save_text(m: re.Match) -> str:
        t = m.group(1).rstrip("\\").replace('"', "'")
        texts.append(t)
        return f"__TEXT_PH_{len(texts)-1}__"

    s = re.sub(r"\\+(?:text|mathrm|mathbf|mathit|textbf)\{([^{}]+)\}", save_text, s)

    # Fractions
    while re.search(r"\\frac\{([^{}]+)\}\{([^{}]+)\}", s):
        s = re.sub(r"\\frac\{([^{}]+)\}\{([^{}]+)\}", r"(\1) / (\2)", s)

    # Scaled delimiters: \left( \right) etc.
    s = re.sub(r"\\left\s*([(\[{|])", r"\1", s)
    s = re.sub(r"\\right\s*([)\]}|])", r"\1", s)
    s = re.sub(r"\\left\s*\\\{", r"\{", s)
    s = re.sub(r"\\right\s*\\\}", r"\}", s)
    s = re.sub(r"\\left\.", "", s)
    s = re.sub(r"\\right\.", "", s)

    # Spacing commands
    s = re.sub(r"\\(?:quad|qquad)", "  ", s)
    s = re.sub(r"\\[,;: ]", " ", s)

    # Operators & calculus
    s = re.sub(r"\\int(?![a-zA-Z])", "integral", s)
    s = re.sub(r"\\iint(?![a-zA-Z])", "integral.double", s)
    s = re.sub(r"\\iiint(?![a-zA-Z])", "integral.triple", s)
    s = re.sub(r"\\oint(?![a-zA-Z])", "integral.cont", s)
    s = re.sub(r"\\sum(?![a-zA-Z])", "sum", s)
    s = re.sub(r"\\prod(?![a-zA-Z])", "product", s)
    s = re.sub(r"\\partial(?![a-zA-Z])", "diff", s)
    s = re.sub(r"\\nabla(?![a-zA-Z])", "nabla", s)
    s = re.sub(r"\\hbar(?![a-zA-Z])", "h.bar", s)
    s = re.sub(r"\\ell(?![a-zA-Z])", "ell", s)
    s = re.sub(r"\\circ(?![a-zA-Z])", "degree", s)

    # Math symbols & relations
    s = re.sub(r"\\cdot(?![a-zA-Z])", "dot", s)
    s = re.sub(r"\\times(?![a-zA-Z])", "times", s)
    s = re.sub(r"\\div(?![a-zA-Z])", "div", s)
    s = re.sub(r"\\pm(?![a-zA-Z])", "plus.minus", s)
    s = re.sub(r"\\mp(?![a-zA-Z])", "minus.plus", s)
    s = re.sub(r"\\infty(?![a-zA-Z])", "infinity", s)
    s = re.sub(r"\\approx(?![a-zA-Z])", "approx", s)
    s = re.sub(r"\\sim(?![a-zA-Z])", "tilde", s)
    s = re.sub(r"\\equiv(?![a-zA-Z])", "equiv", s)
    s = re.sub(r"\\propto(?![a-zA-Z])", "prop", s)
    s = re.sub(r"\\le(q)?(?![a-zA-Z])", "<=", s)
    s = re.sub(r"\\ge(q)?(?![a-zA-Z])", ">=", s)
    s = re.sub(r"\\ne(q)?(?![a-zA-Z])", "!=", s)
    s = re.sub(r"\\to(?![a-zA-Z])|\\rightarrow(?![a-zA-Z])", "->", s)
    s = re.sub(r"\\leftarrow(?![a-zA-Z])", "<-", s)
    s = re.sub(r"\\Rightarrow(?![a-zA-Z])", "=>", s)
    s = re.sub(r"\\Leftarrow(?![a-zA-Z])", "<=", s)
    s = re.sub(r"\\leftrightarrow(?![a-zA-Z])", "<->", s)
    s = re.sub(r"\\Leftrightarrow(?![a-zA-Z])", "<=>", s)
    s = re.sub(r"\\(?:dots|cdots|ldots)(?![a-zA-Z])", "dots", s)

    # Accents & vectors
    s = re.sub(r"\\vec\{([^{}]+)\}", r"arrow(\1)", s)
    s = re.sub(r"\\vec\s+([a-zA-Z])", r"arrow(\1)", s)
    s = re.sub(r"\\hat\{([^{}]+)\}", r"hat(\1)", s)
    s = re.sub(r"\\hat\s+([a-zA-Z])", r"hat(\1)", s)
    s = re.sub(r"\\dot\{([^{}]+)\}", r"dot(\1)", s)
    s = re.sub(r"\\dot\s+([a-zA-Z])", r"dot(\1)", s)
    s = re.sub(r"\\ddot\{([^{}]+)\}", r"dot.double(\1)", s)
    s = re.sub(r"\\ddot\s+([a-zA-Z])", r"dot.double(\1)", s)
    s = re.sub(r"\\bar\{([^{}]+)\}", r"macron(\1)", s)
    s = re.sub(r"\\bar\s+([a-zA-Z])", r"macron(\1)", s)
    s = re.sub(r"\\tilde\{([^{}]+)\}", r"tilde(\1)", s)
    s = re.sub(r"\\tilde\s+([a-zA-Z])", r"tilde(\1)", s)
    s = re.sub(r"\\sqrt\[([^{}\]]+)\]\{([^{}]+)\}", r"root(\1, \2)", s)
    s = re.sub(r"\\sqrt\{([^{}]+)\}", r"sqrt(\1)", s)
    s = re.sub(r"\\sqrt(?![a-zA-Z])", "sqrt", s)

    # Standard LaTeX math functions (sin, cos, tan, log, ln, etc.)
    s = re.sub(r"\\operatorname\{([^{}]+)\}", r"\1 ", s)

    def _replace_math_fn(m: re.Match) -> str:
        name = m.group(1)
        tail = m.group(2)
        if not tail:
            return f"{name} "
        return f"{name}{tail}"

    s = _MATH_FN_PATTERN.sub(_replace_math_fn, s)

    # Greek letters
    greek = [
        "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta",
        "iota", "kappa", "lambda", "mu", "nu", "xi", "pi", "rho", "sigma",
        "tau", "upsilon", "phi", "chi", "psi", "omega",
        "Gamma", "Delta", "Theta", "Lambda", "Xi", "Pi", "Sigma", "Phi", "Psi", "Omega"
    ]
    for g in greek:
        s = re.sub(rf"\\{g}(?![a-zA-Z])", g, s)

    # Subscripts & superscripts with curly braces: _{...} -> _(...) and ^{...} -> ^(...)
    while re.search(r"(_|\^)\{([^{}]+)\}", s):
        s = re.sub(r"(_|\^)\{([^{}]+)\}", r"\1(\2)", s)

    # General grouping braces {...} -> (...)
    while re.search(r"\{([^{}]+)\}", s):
        s = re.sub(r"\{([^{}]+)\}", r"(\1)", s)

    # Floating superscripts: ^ without preceding base (at start, or after space/punctuation)
    s = re.sub(r"(^|[\s(\[,=+\-*/])\^", r'\1""^', s)

    # Separate adjacent latin letters not in known words, protecting placeholders
    def _split_letters(m: re.Match) -> str:
        if m.group(1):
            return m.group(1)
        w = m.group(2)
        if w in KNOWN_TYPST_MATH_WORDS:
            return w
        return " ".join(list(w))

    s = re.sub(
        r"(__TEXT_PH_\d+__)|(?<![a-zA-Z])([a-zA-Z]{2,})(?![a-zA-Z])",
        _split_letters,
        s,
    )

    # Strip any backslashes immediately preceding placeholder
    s = re.sub(r"\\+(__TEXT_PH_\d+__)", r"\1", s)

    # Restore texts as string literals
    for i, t in enumerate(texts):
        s = s.replace(f"__TEXT_PH_{i}__", f'"{t}"')

    # Double backslash in math -> line break
    s = s.replace(r"\\", r"\ ")

    # Normalize any escaped quotes in math string literals: \"word" -> "word"
    s = re.sub(r'(?<!\\)\\"([^"]*)"', r'"\1"', s)
    return s


def format_typst_content(text: str, in_table: bool = False) -> str:
    """Format and escape text content, translating inline/display math and markdown formatting."""
    if not text:
        return ""

    text = sanitize_currency_expressions(text)

    # Split text into math and non-math segments:
    # Display math: $$...$$ or \[...\]
    # Inline math: $...$ or \(...\)
    pattern = re.compile(r"(\$\$.*?\$\$|(?<!\\)\$[^$\n]+?\$|\\\[.*?\\\]|\\\(.*?\\\))", re.DOTALL)
    parts = pattern.split(text)

    output: List[str] = []
    for part in parts:
        if not part:
            continue
        if part.startswith("$$") and part.endswith("$$"):
            inner = part[2:-2]
            converted = convert_latex_to_typst_math(inner)
            output.append(f"$ {converted} $")
        elif part.startswith("\\[") and part.endswith("\\]"):
            inner = part[2:-2]
            converted = convert_latex_to_typst_math(inner)
            output.append(f"$ {converted} $")
        elif part.startswith("\\(") and part.endswith("\\)"):
            inner = part[2:-2]
            converted = convert_latex_to_typst_math(inner)
            output.append(f"${converted}$")
        elif part.startswith("$") and part.endswith("$") and len(part) > 1:
            inner = part[1:-1]
            converted = convert_latex_to_typst_math(inner)
            output.append(f"${converted}$")
        else:
            # Plain text part
            segment = part
            # 1. Heading conversion at start of lines: ### Heading -> === Heading
            lines = segment.split("\n")
            proc_lines = []
            for line in lines:
                m = re.match(r"^(\s*)(#{1,6})\s+(.*)$", line)
                if m:
                    indent = m.group(1)
                    level = len(m.group(2))
                    title = m.group(3)
                    equals = "=" * min(level, 6)
                    proc_lines.append(f"{indent}{equals} {title}")
                else:
                    proc_lines.append(line)
            segment = "\n".join(proc_lines)

            # 2. Escape # not part of Typst keywords
            segment = re.sub(
                r"(?<!\\)#(?!table|align|figure|image|rect|line|set|show|let|import|include|outline)",
                r"\#",
                segment,
            )

            # 3. Markdown bold: **text** -> *text*
            bolds: List[str] = []

            def save_b(m: re.Match) -> str:
                bolds.append(m.group(1))
                return f"__BOLD_PH_{len(bolds)-1}__"

            segment = re.sub(r"\*\*([^\*\n]+?)\*\*", save_b, segment)

            # 4. Asterisks escaping in text to prevent unclosed delimiter or star collision
            segment = re.sub(r"(?<!\\)\*", r"\*", segment)

            # 5. Escape any remaining unescaped dollar signs in plain text
            segment = re.sub(r"(?<!\\)\$", r"\$", segment)

            # 6. Normalize escaped quotes in plain text / captions
            segment = segment.replace(r'\"', '"')

            # Restore bold
            for i, b in enumerate(bolds):
                segment = segment.replace(f"__BOLD_PH_{i}__", f"*{b}*")

            output.append(segment)

    return "".join(output)


class TypstExporter(BaseExporter):
    """Exports DocumentAST to native Typst (.typ) source documents."""

    format_name = "typ"
    file_extension = ".typ"

    def render_block(self, block: Block) -> str:
        """Serialize individual AST block to Typst syntax."""
        if block.type == BlockType.HEADING:
            equals = "=" * min(block.level, 6)
            label = f" <{block.slug}>" if block.slug else ""
            return f"{equals} {format_typst_content(block.content)}{label}\n"

        elif block.type == BlockType.PARAGRAPH:
            return f"{format_typst_content(block.content)}\n"

        elif block.type == BlockType.LIST:
            lines: List[str] = []
            for i, item in enumerate(block.items):
                pfx = "+ " if block.ordered else "- "
                lines.append(f"{pfx}{format_typst_content(item.content)}")
                for sub in item.sub_items:
                    lines.append(f"  - {format_typst_content(sub.content)}")
            return "\n".join(lines) + "\n"

        elif block.type == BlockType.CODE:
            lang = block.language or ""
            fenced = f"```{lang}\n{block.content.rstrip()}\n```"
            if block.caption:
                cap_formatted = format_typst_content(block.caption)
                return f"#figure(\n  [{fenced}],\n  caption: [{cap_formatted}]\n)\n"
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
                    formatted_h = format_typst_content(h.strip(), in_table=True)
                    if formatted_h.startswith("*") and formatted_h.endswith("*"):
                        cells.append(f"  [{formatted_h}],")
                    else:
                        cells.append(f"  [* {formatted_h} *],")
            if block.rows:
                for row in block.rows:
                    for cell in row:
                        formatted_cell = format_typst_content(cell.strip(), in_table=True)
                        cells.append(f"  [{formatted_cell}],")

            cells_joined = "\n".join(cells)
            if block.caption:
                cap_formatted = format_typst_content(block.caption)
                return (
                    f"#figure(\n"
                    f"  table(\n"
                    f"    columns: {col_count},\n"
                    f"{cells_joined}\n"
                    f"  ),\n"
                    f"  caption: [{cap_formatted}]\n"
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

            # Verify actual path existence on disk
            path_obj = Path(actual_path)
            if not path_obj.exists():
                # Attempt fallback to page asset (e.g. asset_p{block.page}_00.png) in same directory
                parent_dir = path_obj.parent
                fallback_found = False
                if parent_dir.exists():
                    candidates = [
                        parent_dir / f"asset_p{block.page}_00.png",
                        parent_dir / f"asset_p{block.page}_00.jpg",
                    ]
                    for p in parent_dir.glob(f"asset_p{block.page}_*.*"):
                        if p.suffix.lower() in typst_supported_exts and p not in candidates:
                            candidates.append(p)
                    for cand in candidates:
                        if cand.exists():
                            actual_path = str(cand)
                            path_obj = cand
                            fallback_found = True
                            break

                if not fallback_found and not path_obj.exists():
                    clean_name = escape_typst_text(path_obj.name)
                    caption_text = block.caption or block.alt_text or ""
                    if caption_text:
                        cap_formatted = format_typst_content(caption_text)
                        return (
                            f"#figure(\n"
                            f"  rect(width: 100%, height: 60pt, stroke: 0.5pt + gray, fill: rgb(\"f8fafc\"), radius: 4pt)[\n"
                            f"    #align(center + horizon)[#text(fill: gray.darken(30%), size: 9pt)[🖼️ Missing image: {clean_name}]]\n"
                            f"  ],\n"
                            f"  caption: [{cap_formatted}]\n"
                            f")\n"
                        )
                    return (
                        f"#rect(\n"
                        f"  width: 100%,\n"
                        f"  height: 60pt,\n"
                        f"  stroke: 0.5pt + gray,\n"
                        f'  fill: rgb("f8fafc"),\n'
                        f"  radius: 4pt,\n"
                        f")[\n"
                        f"  #align(center + horizon)[#text(fill: gray.darken(30%), size: 9pt)[🖼️ Missing image: {clean_name}]]\n"
                        f"]\n"
                    )

            path_esc = escape_typst_string(actual_path)
            if block.caption or block.alt_text:
                caption_text = block.caption or block.alt_text or ""
                cap_formatted = format_typst_content(caption_text)
                return f'#figure(\n  image("{path_esc}"),\n  caption: [{cap_formatted}]\n)\n'
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
            title_line = f"*{format_typst_content(title)}* \\ \n" if title else ""
            body_text = format_typst_content(block.content)

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
