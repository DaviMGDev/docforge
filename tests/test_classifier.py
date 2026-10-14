import os
import fitz
import pytest
from docforge.models.pipeline import ChunkDefinition, DocumentClassification
from docforge.stages.classifier import classify_chunk, classify_page, build_bypass_chunk_ast


@pytest.fixture
def prose_pdf(tmp_path):
    pdf_path = str(tmp_path / "prose.pdf")
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    p.insert_text((50, 50), "Chapter 1: The Beginning", fontsize=18)
    p.insert_text((50, 100), "This is a simple single column story paragraph without complex elements.", fontsize=11)
    p.insert_text((50, 140), "Second paragraph continuing the narrative in a linear fashion.", fontsize=11)
    doc.save(pdf_path)
    doc.close()
    return pdf_path


@pytest.fixture
def math_pdf(tmp_path):
    pdf_path = str(tmp_path / "math.pdf")
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    if os.path.exists(font_path):
        p.insert_font(fontname="dejavu", fontfile=font_path)
        p.insert_text((50, 50), "Formula ∑ i=1 to n and √x", fontname="dejavu", fontsize=12)
    else:
        p.insert_text((50, 50), "Formula ∑ i=1 to n and √x", fontsize=12)
    doc.save(pdf_path)
    doc.close()
    return pdf_path


@pytest.fixture
def multicolumn_pdf(tmp_path):
    pdf_path = str(tmp_path / "multicolumn.pdf")
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    # Column 1
    p.insert_textbox(fitz.Rect(50, 50, 250, 300), "Column 1 text here that runs down the page.", fontsize=10)
    # Column 2
    p.insert_textbox(fitz.Rect(300, 50, 500, 300), "Column 2 text here running parallel to column 1.", fontsize=10)
    doc.save(pdf_path)
    doc.close()
    return pdf_path


def test_trivial_prose_bypasses_when_flag_enabled(prose_pdf):
    doc = fitz.open(prose_pdf)
    chunk = ChunkDefinition(chunk_index=0, start_page=1, end_page=1, page_numbers=[1], is_contiguous=True)
    res = classify_chunk(doc, chunk, auto_bypass_enabled=True)
    assert res == DocumentClassification.TRIVIAL_PROSE

    ast = build_bypass_chunk_ast(doc, chunk)
    assert ast.bypass_used is True
    assert ast.llm_model == "bypass"
    assert len(ast.blocks) >= 2
    doc.close()


def test_auto_bypass_disabled_by_default(prose_pdf):
    doc = fitz.open(prose_pdf)
    chunk = ChunkDefinition(chunk_index=0, start_page=1, end_page=1, page_numbers=[1], is_contiguous=True)
    res = classify_chunk(doc, chunk, auto_bypass_enabled=False)
    assert res == DocumentClassification.COMPLEX
    doc.close()


def test_math_symbols_force_complex(math_pdf):
    doc = fitz.open(math_pdf)
    chunk = ChunkDefinition(chunk_index=0, start_page=1, end_page=1, page_numbers=[1], is_contiguous=True)
    res = classify_chunk(doc, chunk, auto_bypass_enabled=True)
    assert res == DocumentClassification.COMPLEX
    doc.close()


def test_multicolumn_forces_complex(multicolumn_pdf):
    doc = fitz.open(multicolumn_pdf)
    chunk = ChunkDefinition(chunk_index=0, start_page=1, end_page=1, page_numbers=[1], is_contiguous=True)
    res = classify_chunk(doc, chunk, auto_bypass_enabled=True)
    assert res == DocumentClassification.COMPLEX
    doc.close()


@pytest.mark.asyncio
async def test_pipeline_routes_to_llm_even_with_auto_bypass(prose_pdf, tmp_path):
    from docforge.pipeline import PipelineRunner
    from docforge.models.cli_options import CliConfig, LlmConfig

    out_md = str(tmp_path / "prose_out.md")
    cfg = CliConfig(
        input_pdf_path=prose_pdf,
        output_path=out_md,
        auto_bypass=True,
        no_cache=True,
        llm=LlmConfig(model_name="gemma4:cloud"),
    )
    runner = PipelineRunner(cfg)
    ast, out_md_path, _ = await runner.run()
    assert os.path.exists(out_md_path)
    # Validate that none of the generated blocks have bypass provenance
    with open(out_md_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "Chapter 1: The Beginning" in content
