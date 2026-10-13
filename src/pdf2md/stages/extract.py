"""Stage 2: Deterministic Local Extraction & Asset Deduplication.

Conforms to specs/contracts/extractor.contract and specs/algorithms/asset_extraction_and_dedup.pseudo.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import pymupdf as fitz

from pdf2md.exceptions import CorruptedPdfException, InvalidPageRangeException
from pdf2md.logging import get_logger
from pdf2md.models.pipeline import (
    AssetRecord,
    DocumentClassification,
    ExtractionResult,
    PageImageRecord,
    PageInfo,
    PageTextRecord,
    PageTextSpan,
)
from pdf2md.models.toc import OutlineNode

logger = get_logger("extract")


def compute_file_sha256(file_path: str) -> str:
    """Compute 64-character SHA-256 hex digest of file."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def open_pdf_document(pdf_path: str) -> fitz.Document:
    """Open and validate PDF document with auto-repair attempt if damaged."""
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")

    try:
        doc = fitz.open(pdf_path)
        if doc.is_encrypted:
            # Check if decryptable with empty password
            if not doc.authenticate(""):
                raise CorruptedPdfException(f"Encrypted PDF requires password: {pdf_path}")
        if len(doc) == 0:
            raise CorruptedPdfException("Error: Corrupted PDF file (zero pages)")
        return doc
    except Exception as e:
        if isinstance(e, (CorruptedPdfException, FileNotFoundError)):
            raise
        # Attempt repair by opening with fitz flags if possible
        try:
            doc = fitz.open(pdf_path)
            if len(doc) > 0:
                return doc
        except Exception:
            pass
        raise CorruptedPdfException(f"Error: Corrupted PDF file: {e}") from e


def extract_native_outline(doc: fitz.Document) -> List[OutlineNode]:
    """Extract native PDF bookmarks via doc.get_toc()."""
    toc_raw = doc.get_toc(simple=True)
    outline_nodes: List[OutlineNode] = []
    for item in toc_raw:
        # item: [lvl, title, page, dest_y]
        level = item[0]
        title = item[1].strip()
        page = item[2]
        dest_y = item[3] if len(item) > 3 and isinstance(item[3], (int, float)) else None
        if level >= 1 and page >= 1 and title:
            outline_nodes.append(OutlineNode(level=level, title=title, page_number=page, dest_y=dest_y))
    return outline_nodes


def inspect_page(doc: fitz.Document, page_number: int) -> PageInfo:
    """Inspect page dimensions and object counts (1-based index)."""
    if page_number < 1 or page_number > len(doc):
        raise InvalidPageRangeException(f"Page number {page_number} out of bounds")

    page = doc.load_page(page_number - 1)
    rect = page.rect
    images = page.get_images()
    drawings = page.get_drawings()
    text = page.get_text()

    return PageInfo(
        page_number=page_number,
        width_pts=rect.width,
        height_pts=rect.height,
        rotation=page.rotation,
        has_text=bool(text.strip()),
        image_count=len(images),
        drawing_count=len(drawings),
    )


def render_page_image(
    doc: fitz.Document,
    page_number: int,
    dpi: int = 150,
    output_path: Optional[str] = None,
) -> PageImageRecord:
    """Render page to high-res PNG image byte buffer and save to disk."""
    if page_number < 1 or page_number > len(doc):
        raise InvalidPageRangeException(f"Page number {page_number} out of bounds")

    page = doc.load_page(page_number - 1)
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)

    width = pix.width
    height = pix.height

    if output_path is None:
        raise ValueError("output_path must be provided to persist rendered image")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    pix.save(output_path)
    byte_size = os.path.getsize(output_path)

    # Immediately release pixmap
    pix = None

    return PageImageRecord(
        page_number=page_number,
        image_path=output_path,
        dpi=dpi,
        width_px=width,
        height_px=height,
        byte_size=byte_size,
    )


def extract_raw_page_text(doc: fitz.Document, page_number: int) -> PageTextRecord:
    """Extract selectable digital text spans and metadata without OCR."""
    if page_number < 1 or page_number > len(doc):
        raise InvalidPageRangeException(f"Page number {page_number} out of bounds")

    page = doc.load_page(page_number - 1)
    text_page_dict = page.get_text("dict")
    full_text = page.get_text("text")

    spans: List[PageTextSpan] = []
    line_count = 0

    for block in text_page_dict.get("blocks", []):
        if block.get("type") == 0:  # text block
            for line in block.get("lines", []):
                line_count += 1
                for s in line.get("spans", []):
                    span_text = s.get("text", "")
                    if not span_text:
                        continue
                    font_name = s.get("font", "")
                    font_size = float(s.get("size", 0.0))
                    flags = int(s.get("flags", 0))
                    bbox = tuple(float(x) for x in s.get("bbox", (0, 0, 0, 0)))

                    is_bold = bool(flags & (1 << 4)) or ("bold" in font_name.lower()) or ("black" in font_name.lower())
                    is_italic = bool(flags & (1 << 1)) or ("italic" in font_name.lower()) or ("oblique" in font_name.lower())
                    is_mono = bool(flags & (1 << 3)) or any(m in font_name.lower() for m in ["mono", "courier", "console"])

                    spans.append(
                        PageTextSpan(
                            text=span_text,
                            bbox=bbox,
                            font_name=font_name,
                            font_size=font_size,
                            flags=flags,
                            is_bold=is_bold,
                            is_italic=is_italic,
                            is_monospace=is_mono,
                        )
                    )

    words = page.get_text("words")
    word_count = len(words)

    return PageTextRecord(
        page_number=page_number,
        full_text=full_text,
        spans=spans,
        word_count=word_count,
        line_count=line_count,
    )


def extract_and_dedupe_assets(
    doc: fitz.Document,
    page_number: int,
    assets_dir: str,
    dedup_table: Dict[str, AssetRecord],
) -> List[AssetRecord]:
    """Extract embedded images, normalize color-space, deduplicate by SHA-256, and save."""
    if page_number < 1 or page_number > len(doc):
        raise InvalidPageRangeException(f"Page number {page_number} out of bounds")

    page = doc.load_page(page_number - 1)
    image_list = page.get_images(full=True)
    page_asset_records: List[AssetRecord] = []
    seq = 0

    os.makedirs(assets_dir, exist_ok=True)

    for img_info in image_list:
        xref = img_info[0]
        try:
            base_image = doc.extract_image(xref)
        except Exception:
            continue

        if not base_image:
            continue

        image_bytes = base_image["image"]
        image_ext = base_image["ext"]
        width = base_image["width"]
        height = base_image["height"]
        raw_cs = base_image.get("colorspace", "RGB")
        if isinstance(raw_cs, int):
            if raw_cs == 1:
                colorspace = "Gray"
            elif raw_cs == 4:
                colorspace = "DeviceCMYK"
            else:
                colorspace = "RGB"
        else:
            colorspace = str(raw_cs)

        # Filter out negligible graphical artifacts (icons, bullets, thin lines)
        if width < 24 or height < 24:
            continue

        # Handle format / color-space normalization: Convert CMYK or JPX/JBIG2 to standard PNG
        if colorspace in ("DeviceCMYK", "CMYK") or image_ext in ("jpx", "jp2", "j2k", "jpc", "jb2", "pam"):
            try:
                pixmap = fitz.Pixmap(doc, xref)
                if pixmap.colorspace and pixmap.colorspace.n >= 4:
                    rgb_pixmap = fitz.Pixmap(fitz.csRGB, pixmap)
                    image_bytes = rgb_pixmap.tobytes(output="png")
                else:
                    image_bytes = pixmap.tobytes(output="png")
                image_ext = "png"
                colorspace = "RGB"
                pixmap = None
                rgb_pixmap = None
            except Exception:
                pass

        sha256_hash = hashlib.sha256(image_bytes).hexdigest()
        asset_id = f"asset_p{page_number}_{seq:02d}"

        if sha256_hash in dedup_table:
            existing_record = dedup_table[sha256_hash]
            ref_record = AssetRecord(
                asset_id=asset_id,
                page_number=page_number,
                xref=xref,
                sha256=sha256_hash,
                extension=existing_record.extension,
                width_px=width,
                height_px=height,
                color_space=colorspace,
                file_path=existing_record.file_path,
                byte_size=len(image_bytes),
                is_deduplicated=True,
            )
            page_asset_records.append(ref_record)
        else:
            filename = f"asset_p{page_number}_{seq:02d}.{image_ext}"
            disk_path = os.path.join(assets_dir, filename)
            with open(disk_path, "wb") as f:
                f.write(image_bytes)

            new_record = AssetRecord(
                asset_id=asset_id,
                page_number=page_number,
                xref=xref,
                sha256=sha256_hash,
                extension=image_ext,
                width_px=width,
                height_px=height,
                color_space=colorspace,
                file_path=disk_path,
                byte_size=len(image_bytes),
                is_deduplicated=False,
            )
            dedup_table[sha256_hash] = new_record
            page_asset_records.append(new_record)

        seq += 1

    return page_asset_records


def extract_chunk(
    doc: fitz.Document,
    chunk_index: int,
    page_numbers: List[int],
    cache_chunk_dir: str,
    assets_dir: str,
    dedup_table: Dict[str, AssetRecord],
    dpi: int = 150,
) -> ExtractionResult:
    """Extract rendered images, raw texts, and embedded assets for all pages in a chunk."""
    rendered_images: List[PageImageRecord] = []
    raw_texts: List[PageTextRecord] = []
    extracted_assets: List[AssetRecord] = []

    os.makedirs(cache_chunk_dir, exist_ok=True)

    for p in page_numbers:
        img_path = os.path.join(cache_chunk_dir, f"page_{p:04d}.png")
        img_rec = render_page_image(doc, p, dpi=dpi, output_path=img_path)
        rendered_images.append(img_rec)

        text_rec = extract_raw_page_text(doc, p)
        raw_texts.append(text_rec)

        assets = extract_and_dedupe_assets(doc, p, assets_dir, dedup_table)
        extracted_assets.extend(assets)

    total_words = sum(len(t.full_text.split()) for t in raw_texts)
    dedup_count = sum(1 for a in extracted_assets if a.is_deduplicated)
    logger.info(
        f"[Stage 2] Chunk {chunk_index} (pages {min(page_numbers)}-{max(page_numbers)}): "
        f"{len(rendered_images)} page renders, {total_words} words, "
        f"{len(extracted_assets)} assets ({dedup_count} deduplicated)"
    )

    return ExtractionResult(
        chunk_index=chunk_index,
        page_numbers=page_numbers,
        rendered_images=rendered_images,
        raw_texts=raw_texts,
        extracted_assets=extracted_assets,
        classification=DocumentClassification.COMPLEX,
    )
