import os
import fitz
import pytest
from PIL import Image
from pdf2md.exceptions import CorruptedPdfException
from pdf2md.stages.extract import (
    compute_file_sha256,
    open_pdf_document,
    render_page_image,
    extract_raw_page_text,
    extract_and_dedupe_assets,
    extract_chunk,
)


@pytest.fixture
def sample_pdf(tmp_path):
    pdf_path = str(tmp_path / "sample.pdf")
    doc = fitz.open()

    # Create dummy images for asset extraction test
    img1 = Image.new("RGB", (100, 100), color="red")
    img1_path = str(tmp_path / "test_img1.png")
    img1.save(img1_path)

    # Small 12x12 icon that should be filtered out (<24px)
    icon = Image.new("RGB", (12, 12), color="blue")
    icon_path = str(tmp_path / "icon.png")
    icon.save(icon_path)

    # Page 1: Text
    p1 = doc.new_page(width=595, height=842)
    p1.insert_text((50, 50), "Sample Heading", fontsize=20)
    p1.insert_text((50, 100), "This is plain body text.", fontsize=12)

    # Page 2: Text + Image1 + Icon
    p2 = doc.new_page(width=595, height=842)
    p2.insert_text((50, 50), "Page 2 content", fontsize=12)
    p2.insert_image(fitz.Rect(50, 100, 150, 200), filename=img1_path)
    p2.insert_image(fitz.Rect(50, 250, 62, 262), filename=icon_path)

    # Page 3: Duplicate of Image1
    p3 = doc.new_page(width=595, height=842)
    p3.insert_image(fitz.Rect(50, 100, 150, 200), filename=img1_path)

    doc.save(pdf_path)
    doc.close()
    return pdf_path


def test_sha256_computation(sample_pdf):
    digest = compute_file_sha256(sample_pdf)
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)


def test_render_page_image(sample_pdf, tmp_path):
    doc = open_pdf_document(sample_pdf)
    out_img = str(tmp_path / "page_1.png")
    rec = render_page_image(doc, page_number=1, dpi=150, output_path=out_img)
    assert os.path.exists(out_img)
    assert rec.dpi == 150
    # Expected width: 595 * 150 / 72 = 1239.58 -> 1240
    assert abs(rec.width_px - int(595 * 150 / 72)) <= 2
    assert rec.byte_size > 0
    doc.close()


def test_extract_raw_page_text(sample_pdf):
    doc = open_pdf_document(sample_pdf)
    txt_rec = extract_raw_page_text(doc, page_number=1)
    assert "Sample Heading" in txt_rec.full_text
    assert "This is plain body text." in txt_rec.full_text
    assert len(txt_rec.spans) >= 2
    assert txt_rec.word_count > 0
    doc.close()


def test_extract_and_dedupe_assets(sample_pdf, tmp_path):
    doc = open_pdf_document(sample_pdf)
    assets_dir = str(tmp_path / "assets")
    dedup_table = {}

    # Page 2 has image1 (100x100) and icon (12x12)
    assets_p2 = extract_and_dedupe_assets(doc, page_number=2, assets_dir=assets_dir, dedup_table=dedup_table)
    # The 12x12 icon must be filtered out (<24px)
    assert len(assets_p2) == 1
    assert assets_p2[0].is_deduplicated is False
    assert os.path.exists(assets_p2[0].file_path)

    # Page 3 has duplicate of image1
    assets_p3 = extract_and_dedupe_assets(doc, page_number=3, assets_dir=assets_dir, dedup_table=dedup_table)
    assert len(assets_p3) == 1
    assert assets_p3[0].is_deduplicated is True
    # Points to same file path as p2
    assert assets_p3[0].file_path == assets_p2[0].file_path

    # Only 1 file should be created on disk in assets directory
    saved_files = os.listdir(assets_dir)
    assert len(saved_files) == 1
    doc.close()


def test_corrupted_pdf_error(tmp_path):
    bad_pdf = str(tmp_path / "corrupted.pdf")
    with open(bad_pdf, "wb") as f:
        f.write(b"NOT A VALID PDF FILE")

    with pytest.raises(CorruptedPdfException):
        open_pdf_document(bad_pdf)
