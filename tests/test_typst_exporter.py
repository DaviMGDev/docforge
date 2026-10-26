"""Unit tests for TypstExporter math normalization, header escaping, and formatting.

Covers Issue #18 and ensures .typ files compile cleanly via typst compile.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
from pathlib import Path
import pytest

from docforge.exporters.typst import (
    TypstExporter,
    convert_latex_to_typst_math,
    format_typst_content,
    sanitize_currency_expressions,
)
from docforge.models.ast import (
    CalloutBlock,
    CalloutType,
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


def make_ast(blocks) -> DocumentAST:
    return DocumentAST(
        source_pdf_hash="sample_hash",
        total_pages=1,
        selected_pages=[1],
        chunks_count=1,
        stitched_at=datetime.now().isoformat(),
        blocks=blocks,
    )


def test_convert_latex_to_typst_math():
    # Fractions
    assert convert_latex_to_typst_math(r"\frac{a}{b}") == "(a) / (b)"
    # Subscripts with braces
    assert "v_(0y)" in convert_latex_to_typst_math(r"v_y = v_{0y} + a_y t")
    # Integrals and limits
    assert "integral_(0)^(t)" in convert_latex_to_typst_math(r"\int_{0}^{t} v dt")
    # Floating superscripts without base
    assert '""^(133)"Cs"' in convert_latex_to_typst_math(r"^{133}\text{Cs}")
    assert '""^2' in convert_latex_to_typst_math(r"^2")
    # Math accents and vectors
    assert "arrow(v)" in convert_latex_to_typst_math(r"\vec{v}")
    assert "hat(i)" in convert_latex_to_typst_math(r"\hat{i}")
    assert "dot.double(y)" in convert_latex_to_typst_math(r"\ddot{y}")
    # Text commands inside math
    assert '"kinetic energy "' in convert_latex_to_typst_math(r"\text{kinetic energy }")


def test_format_typst_content_inline_and_display_math():
    text = (
        "Inline formula $v_{0y}$ and display formula:\n"
        r"$$\Delta K + \Delta U_{\text{int}} = 0$$"
    )
    formatted = format_typst_content(text)
    assert "$v_(0y)$" in formatted
    assert '$ Delta K + Delta U_("int") = 0 $' in formatted


def test_heading_normalization_and_hash_escaping():
    text = "### SOLUTION\nThis addresses issue #18 regarding C# code mode syntax."
    formatted = format_typst_content(text)
    assert "=== SOLUTION" in formatted
    assert r"\#18" in formatted
    assert r"C\#" in formatted


def test_markdown_bold_and_table_asterisk_sanitization():
    text = "This is **bold text** with footnote *1."
    formatted = format_typst_content(text)
    assert "*bold text*" in formatted
    assert r"\*1" in formatted

    ast = make_ast([
        TableBlock(
            page=1,
            header=["Density (kg/m³)*", "**Mass**"],
            rows=[["*1.29*", "100 kg*"], ["**2.5**", "200 kg"]],
        )
    ])
    exporter = TypstExporter()
    rendered = exporter.render(ast)
    assert r"Density (kg/m³)\*" in rendered
    assert "[*Mass*]" in rendered or "[* *Mass* *]" in rendered


def test_typst_exporter_render_full_stem_document(tmp_path: Path):
    """Verify that a full STEM document with all problematic elements compiles cleanly."""
    blocks = [
        HeadingBlock(page=1, level=1, slug="physics-chap1", content="University Physics: Chapter 1"),
        ParagraphBlock(
            page=1,
            content=r"Consider a cesium atom ($^{133}\text{Cs}$) undergoing acceleration $a = 9.8\text{ m/s}^2$.",
        ),
        ParagraphBlock(
            page=1,
            content=(
                r"Position: $v_y = v_{0y} + a_y t = v_{0y} + (-g)t$ and velocity:"
                "\n"
                r"$ v_x = 10\text{ m/s} + \int_{0}^{t} (2.0\text{ m/s}^2 - (0.10\text{ m/s}^3)t) d t $"
            ),
        ),
        ParagraphBlock(
            page=1,
            content="### SOLUTION\nWe compute the balance:",
        ),
        ParagraphBlock(
            page=1,
            content=r"$$\Delta K + \Delta U + \Delta U_{\text{int}} = +450\text{ J} + (-735\text{ J}) + 285\text{ J} = 0$$",
        ),
        ListBlock(
            page=1,
            items=[
                ListItem(content=r"Initial temperature $T_0 = 0^{\circ}\text{C}$ and angle $\theta = 45^{\circ}$"),
                ListItem(content=r"Acceleration unit $[\text{m/s}^2]$ and vector $\vec{F} = m\vec{a}$"),
            ],
        ),
        TableBlock(
            page=1,
            caption="Physical Densities",
            header=["Material*", "Density (kg/m³)*", "Specific Heat [J/kg*K]"],
            rows=[
                ["Water*", "1000*", r"$4.184 \times 10^3$"],
                [r"Air at 0$^{\circ}$C", "1.29", "1005"],
            ],
        ),
        CalloutBlock(
            page=1,
            callout_type=CalloutType.IMPORTANT,
            title="Principle",
            content=r"Notice that $\vec{v} = \dot{x}\hat{i} + \ddot{y}\hat{j} + \bar{z}\hat{k}$.",
        ),
        MathBlock(
            page=1,
            math_mode=MathMode.BLOCK,
            equation_number="1.1",
            content=r"\oint \vec{B} \cdot d\vec{A} = 0",
        ),
    ]

    ast = make_ast(blocks)
    exporter = TypstExporter()
    rendered = exporter.render(ast)

    typ_file = tmp_path / "document.typ"
    pdf_file = tmp_path / "document.pdf"
    typ_file.write_text(rendered, encoding="utf-8")

    if shutil.which("typst"):
        res = subprocess.run(["typst", "compile", str(typ_file), str(pdf_file)], capture_output=True, text=True)
        assert res.returncode == 0, f"typst compile failed:\n{res.stderr}"
        assert pdf_file.exists() and pdf_file.stat().st_size > 0


def test_issue_19_currency_symbols_escaping(tmp_path: Path):
    """Verify Issue #19 (1): currency symbols do not toggle math mode or cause split letters."""
    text = (
        r"Gold costs $1400/oz while diamonds cost $500,000. "
        r"Market capitalization is $\$10^9$. "
        r"Another item costs $\$100$ or \$500,000."
    )
    formatted = format_typst_content(text)
    assert r"\$1400" in formatted
    assert r"\$500,000" in formatted
    assert r"\$10^9" in formatted
    assert r"\$100" in formatted

    # Verify Typst compilation
    ast = make_ast([ParagraphBlock(page=1, content=text)])
    exporter = TypstExporter()
    rendered = exporter.render(ast)
    typ_file = tmp_path / "currency.typ"
    pdf_file = tmp_path / "currency.pdf"
    typ_file.write_text(rendered, encoding="utf-8")

    if shutil.which("typst"):
        res = subprocess.run(["typst", "compile", str(typ_file), str(pdf_file)], capture_output=True, text=True)
        assert res.returncode == 0, f"typst compile failed:\n{res.stderr}"


def test_issue_19_missing_image_asset_fallback_and_placeholder(tmp_path: Path):
    """Verify Issue #19 (2): missing image assets fall back or emit placeholder without aborting compile."""
    # Subtest A: Fallback to existing asset_p31_00.png on same page
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir(parents=True)
    fallback_png = assets_dir / "asset_p31_00.png"
    # Create valid 1x1 png
    fallback_png.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
        b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    ast_fallback = make_ast([
        ImageBlock(
            page=31,
            asset_path=str(assets_dir / "asset_p31_01.png"),  # Missing, should fallback to _00.png
            caption="Fallback diagram",
        )
    ])
    exporter = TypstExporter()
    rendered_fallback = exporter.render(ast_fallback)
    assert "asset_p31_00.png" in rendered_fallback

    # Subtest B: Completely missing image emits placeholder #rect
    ast_placeholder = make_ast([
        ImageBlock(
            page=99,
            asset_path=str(assets_dir / "asset_p99_05.png"),  # No fallback exists
            caption="Non-existent diagram",
        ),
        ImageBlock(
            page=99,
            asset_path=str(assets_dir / "asset_p99_06.png"),  # Standalone without caption
        ),
    ])
    rendered_placeholder = exporter.render(ast_placeholder)
    assert "#rect" in rendered_placeholder or "rect(" in rendered_placeholder
    assert "Missing image: asset_p99_05.png" in rendered_placeholder
    assert "Missing image: asset_p99_06.png" in rendered_placeholder

    typ_file = tmp_path / "images.typ"
    pdf_file = tmp_path / "images.pdf"
    typ_file.write_text(rendered_placeholder, encoding="utf-8")

    if shutil.which("typst"):
        res = subprocess.run(["typst", "compile", str(typ_file), str(pdf_file)], capture_output=True, text=True)
        assert res.returncode == 0, f"typst compile failed on placeholder:\n{res.stderr}"


def test_issue_19_latex_math_functions_backslash_stripping(tmp_path: Path):
    """Verify Issue #19 (3): LaTeX math functions strip backslashes (tan, sin, cos, ln, log, sqrt)."""
    tex = r"\tan\theta + \sin(x) + \cos\phi + \ln(y) + \log_{10}(z) + \arctan(x) + \sqrt[3]{8} = 2"
    converted = convert_latex_to_typst_math(tex)
    assert "tan" in converted and r"\tan" not in converted
    assert "sin" in converted and r"\sin" not in converted
    assert "cos" in converted and r"\cos" not in converted
    assert "ln" in converted and r"\ln" not in converted
    assert "log" in converted and r"\log" not in converted
    assert "arctan" in converted and r"\arctan" not in converted
    assert "root(3, 8)" in converted

    ast = make_ast([
        MathBlock(
            page=1,
            math_mode=MathMode.BLOCK,
            content=tex,
        )
    ])
    exporter = TypstExporter()
    rendered = exporter.render(ast)

    typ_file = tmp_path / "math_fn.typ"
    pdf_file = tmp_path / "math_fn.pdf"
    typ_file.write_text(rendered, encoding="utf-8")

    if shutil.which("typst"):
        res = subprocess.run(["typst", "compile", str(typ_file), str(pdf_file)], capture_output=True, text=True)
        assert res.returncode == 0, f"typst compile failed:\n{res.stderr}"


def test_issue_19_quote_and_caption_sanitization(tmp_path: Path):
    """Verify Issue #19 (4): \\text{...} in captions/tables does not emit unclosed string delimiters (\\\")."""
    tex = r"F_{\text{net}} = m a_{\text{cm}}"
    converted = convert_latex_to_typst_math(tex)
    assert r'\"' not in converted

    ast = make_ast([
        TableBlock(
            page=1,
            caption=r"Densities in \text{SI units} with \"quoted\" values",
            header=["Material", r"Density ($\text{kg/m}^3$)"],
            rows=[["Steel", r"$7.85 \times 10^3$"]],
        )
    ])
    exporter = TypstExporter()
    rendered = exporter.render(ast)
    assert r'\"' not in rendered

    typ_file = tmp_path / "quotes.typ"
    pdf_file = tmp_path / "quotes.pdf"
    typ_file.write_text(rendered, encoding="utf-8")

    if shutil.which("typst"):
        res = subprocess.run(["typst", "compile", str(typ_file), str(pdf_file)], capture_output=True, text=True)
        assert res.returncode == 0, f"typst compile failed on quote sanitization:\n{res.stderr}"


def test_issue_19_adjacent_variable_separation(tmp_path: Path):
    """Verify Issue #19 (5): multi-letter variables with subscripts (cA_x, AB cos phi) insert whitespace."""
    tex = r"cA_x + cA_y + AB \cos\phi = v_{net}"
    converted = convert_latex_to_typst_math(tex)
    assert "c A_x" in converted
    assert "c A_y" in converted
    assert "A B" in converted
    assert "cos phi" in converted
    assert "v_(n e t)" in converted

    ast = make_ast([
        MathBlock(
            page=1,
            math_mode=MathMode.BLOCK,
            content=tex,
        )
    ])
    exporter = TypstExporter()
    rendered = exporter.render(ast)

    typ_file = tmp_path / "variables.typ"
    pdf_file = tmp_path / "variables.pdf"
    typ_file.write_text(rendered, encoding="utf-8")

    if shutil.which("typst"):
        res = subprocess.run(["typst", "compile", str(typ_file), str(pdf_file)], capture_output=True, text=True)
        assert res.returncode == 0, f"typst compile failed on adjacent variables:\n{res.stderr}"
