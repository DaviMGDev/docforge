# Feature Specification: Deterministic Local Extraction
# File: specs/features/deterministic_extraction.feature

@stage2 @extraction
Feature: Deterministic Local Extraction
  As a processing pipeline
  I want to extract raw text, render page images, and deduplicate embedded image streams natively
  So that high-fidelity ground truth is provided to the LLM without external OCR binaries

  Background:
    Given a valid input PDF "technical_paper.pdf"
    And PyMuPDF native bindings are available without Tesseract or Poppler installed

  Scenario: High-resolution page image rendering
    When page 1 is rendered at 150 DPI
    Then an RGB PNG image is produced
    And the image dimensions in pixels match 150/72 times the PDF page point dimensions
    And the temporary memory allocated for the bitmap is released immediately after writing

  Scenario: Raw text span extraction without OCR
    Given page 2 contains selectable digital text with bold and monospace spans
    When the raw text extractor runs on page 2
    Then plain text is extracted preserving natural reading order
    And each text span includes font size, bold flag, and bounding box coordinates
    And no OCR engine binary is executed

  Scenario: Embedded image stream extraction and deduplication
    Given page 3 contains an embedded figure with SHA-256 "abc123hash"
    And page 7 contains the exact same logo image with SHA-256 "abc123hash"
    When embedded assets are extracted for pages 3 and 7
    Then the image is saved to disk exactly once as "assets/asset_p3_00.png"
    And page 7's asset record references "assets/asset_p3_00.png" with is_deduplicated set to true
    And the total count of asset files on disk is 1

  Scenario: Filtering small decorative icons and hairline artifacts
    Given page 4 contains a 12x12 bullet icon and a 600x400 diagram
    When embedded assets are extracted for page 4
    Then the 12x12 bullet icon is filtered out
    And only the 600x400 diagram is saved into the assets folder

  Scenario: CMYK color space conversion to RGB
    Given page 5 contains an embedded print image formatted in DeviceCMYK
    When embedded assets are extracted for page 5
    Then the image stream is converted to standard sRGB PNG format
    And the saved asset displays correct colors in web and markdown viewers

  Scenario: Hardware-bounded CPU concurrency
    Given a system with 8 CPU cores
    When deterministic extraction runs across 32 pages
    Then the maximum concurrent worker processes is bounded by min(8, 4) = 4
