# Product Requirement Document (PRD): Multimodal PDF-to-Markdown Converter (`pdf2md`)

## 1. Executive Summary

### 1.1 Objective
Build a high-fidelity, CLI-driven document converter that transforms complex PDF documents into clean, semantic Markdown (`.md`) without relying on traditional OCR engines (e.g., Tesseract, PaddleOCR). Instead, the system pairs **native PDF text/asset extraction** with **multimodal Large Language Models (LLMs)** to recognize document structure, mathematical expressions, multi-column layouts, tables, and visual context.

### 1.2 Core Value Proposition
- **High Semantic Accuracy**: Complex tables, formulas (converted to standard LaTeX), and code blocks are reconstructed faithfully.
- **Cost & Token Efficiency**: Raw digital text is fed alongside page images, reducing hallucination and minimizing token waste.
- **Hardware-Aware Concurrency**: Deterministic I/O and rendering scale with available CPU cores, while expensive LLM network calls are rate-controlled and bounded.
- **Reproducibility & Resume**: Intermediate structured JSON checkpoints allow inspectability, offline editing, and resumption of interrupted conversions.

---

## 2. System Architecture & Processing Pipeline

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                             Input PDF Document                              │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
                    [ Stage 1: Partitioning & Slicing ]
                   Splits PDF into chunks of ≤ 8 pages
                                       │
            ┌──────────────────────────┴──────────────────────────┐
            ▼                                                     ▼
     Chunk 1 (pp. 1-8)             ...                     Chunk N (pp. 9-14)
            │                                                     │
            ├─────────────────────────────────────────────────────┤
            │  [ Stage 2: Hardware-Bounded Deterministic Prep ]    │
            │  - Render pages to high-res images (e.g., 150-200DPI)│
            │  - Extract raw selectable text per page             │
            │  - Extract embedded vector/raster images to assets/ │
            └──────────────────────────┬──────────────────────────┘
                                       │
                                       ▼
                   [ Stage 3: Bounded LLM Processing ]
                   Concurrency = 3 (async worker pool)
                   Input: Raw text + Page images + Asset inventory
                   Output: Strongly typed JSON chunk AST
                                       │
                                       ▼
                   [ Stage 4: Cross-Chunk AST Stitching ]
                   - Reconciles split paragraphs & tables
                   - Re-indexes asset references
                   - Resolves heading hierarchy & footnotes
                                       │
                                       ▼
                      [ Stage 5: Markdown Generation ]
                      Deterministic AST -> Standard CommonMark
                                       │
            ┌──────────────────────────┴──────────────────────────┐
            ▼                                                     ▼
    output_document.md                                     assets/
                                                            ├── img_p1_01.png
                                                            └── img_p3_02.png
```

---

## 3. Pipeline Specifications

### 3.1 Stage 1: Partitioning & Page Selection (`partition`)
- **Input**: Path to arbitrary PDF file, plus optional page selection criteria.
- **Page Selection Syntax (`--pages` / `-p`)**:
  - Supports standard print-range formats: e.g., `"1-200, 401-500"`, `"15, 22, 50-75"`.
  - Supports Python-style slice notation: e.g., `"[0:200]"` or `"[0:200] + [400:500]"`.
  - **Normalization**: Automatically maps 0-based or 1-based notation into a deduplicated, sorted list of global 1-based page numbers.
  - **Memory Efficiency**: Unselected pages are never rendered or extracted.
- **Chunking Constraint**:
  - Maximum chunk size is configurable, defaulting to **≤ 8 pages per chunk** (`len(chunk) <= 8`).
  - Disjoint ranges generate independent contiguous sub-chunks (e.g., `1-200` yields 25 chunks of 8; `401-500` yields 13 chunks of ≤ 8).
  - Preserves original document page indices for cross-referencing and asset naming.

### 3.2 Stage 2: Deterministic Preprocessing (`extract_local`)
- **Execution Model**: CPU-bound task pool constrained by `min(os.cpu_count(), max_cpu_workers)`.
- **Outputs per Page/Chunk**:
  1. **Page Images**: Rendered via high-performance native bindings (`PyMuPDF` / `fitz` or `pypdfium2`) at configurable DPI (default: 150 DPI, RGB PNG).
  2. **Raw Text Extraction**: Native extraction of character strings, font spans, and basic reading order without OCR.
  3. **Embedded Image Extraction**: Extracts raw embedded images natively from the PDF streams, dedupes them via SHA-256 hash, and saves them to `<output_dir>/assets/asset_p{page}_{idx}.{ext}`.

### 3.3 Stage 3: Multimodal LLM Structural Parsing (`llm_parse`)
- **Execution Model**: Network I/O bounded task pool with a strict concurrency limit of **3 simultaneous requests** (managed via `asyncio.Semaphore(3)`).
- **Prompt Input Context**:
  - Sequence of page images for the chunk.
  - Raw deterministic text per page (acts as ground-truth anchor for spellings, code, and text streams).
  - List of extracted native image assets available for placement.
- **Parsing Targets**:
  - **Tables**: Converted to clean Markdown tables or HTML `<table>` for complex colspans/rowspans.
  - **Mathematics**: Inline ($...$) and block ($$...$$) mathematical notation converted to LaTeX syntax.
  - **Code Blocks**: Fenced blocks with inferred language identifier (e.g. `python`, `typescript`).
  - **Headings & Structure**: Heading level assignment (`#` through `######`), lists, blockquotes, callout boxes.
  - **Image Placement**: Matches page visual images with extracted asset files, emitting Markdown image tags (`![alt](assets/filename.png)`).
- **Output**: JSON payload validated against the Document AST Schema (see Section 4).

### 3.4 Stage 4: Aggregation & Cross-Chunk Stitching (`stitch`)
- Merges sequential JSON ASTs into a cohesive document.
- Resolves cross-boundary anomalies:
  - Sentences/paragraphs cut off across chunk boundaries (e.g., page 8 to page 9).
  - Headings or list numbers split over page transitions.
  - Deduplication of repeating running headers/footers.

### 3.5 Stage 5: Markdown Synthesis (`render_md`)
- Deterministic rendering from merged JSON AST directly to standard Markdown (CommonMark + GFM).
- Emits clean, lint-compliant Markdown without extraneous LLM conversational artifacts.

#### 3.5.1 Page Traceability & Navigation Markers
- **Problem**: When reading a 200+ page Markdown file, users must know which page of the original PDF a paragraph or figure came from.
- **Mechanism**:
  - Emits page transition anchors at every page boundary:
    ```markdown
    <a id="page-42"></a>
    <!-- [Page 42] -->
    ```
  - Configurable formats via `--page-markers` flag:
    - `anchor` (default): Invisible HTML anchor `<a id="page-N"></a>` with an HTML comment `<!-- [Page N] -->`. Clean rendering in Markdown viewers while enabling direct link navigation (`#page-42`).
    - `badge`: Visible subtle badge/divider: `<small class="pdf-page-indicator">📄 Page 42</small>\n---`.
    - `comment`: Pure HTML comment `<!-- [Page 42] -->`.
    - `none`: Omits page markers.
  - Heading tags also receive deterministic IDs/slugs to enable deep-linking (e.g. `<h2 id="neural-architectures">Neural Architectures</h2>` or `# Neural Architectures {#neural-architectures}`).

#### 3.5.2 Functional Table of Contents (TOC) with Real Internal Links
- **Dual Source of Truth**:
  1. **Native PDF Bookmarks/Outline**: Extracted instantly via PyMuPDF (`doc.get_toc()`), containing hierarchical titles and target page numbers.
  2. **AST Heading Tree**: Extracted by LLM from document headings (`#`, `##`, `###`).
- **Functional Linking Rules**:
  - The synthesized TOC is placed at the top of the Markdown document:
    ```markdown
    ## Table of Contents
    - [1. Introduction](#1-introduction) *(p. 3)*
      - [1.1 Problem Statement](#11-problem-statement) *(p. 4)*
      - [1.2 Prior Work](#12-prior-work) *(p. 7)*
    - [2. System Architecture](#2-system-architecture) *(p. 12)*
    ```
  - **Slug Matching**: Each TOC entry links directly to the generated markdown heading slug (`#slug-name`).
  - **Fallback to Page Anchors**: If a bookmark in the outline points to a page without an explicit heading, it links directly to `#page-{n}`.
  - **Transcribed Printed TOCs**: If the original PDF contains printed TOC pages, the synthesizer replaces static printed page numbers (e.g., `Chapter 1 ........... 15`) with interactive Markdown links (`[Chapter 1](#chapter-1-slug)` or `[Chapter 1](#page-15)`).

---

## 4. Intermediate Data Schema (`material.json`)

To ensure reliability, the LLM must emit structured data adhering to a strictly defined Pydantic schema using structured output (or function calling):

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "DocumentChunkAST",
  "type": "object",
  "properties": {
    "chunk_index": { "type": "integer" },
    "page_range": {
      "type": "array",
      "items": { "type": "integer" },
      "minItems": 2,
      "maxItems": 2
    },
    "blocks": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["type", "page"],
        "properties": {
          "type": {
            "type": "string",
            "enum": ["heading", "paragraph", "list", "table", "code", "math", "image", "callout", "thematic_break"]
          },
          "page": { "type": "integer" },
          "id": { "type": "string", "description": "Unique slug or anchor identifier" },
          "content": { "type": "string" },
          "level": { "type": "integer", "minimum": 1, "maximum": 6 },
          "language": { "type": "string" },
          "math_mode": { "type": "string", "enum": ["inline", "block"] },
          "asset_path": { "type": "string" },
          "caption": { "type": "string" }
        }
      }
    }
  },
  "required": ["chunk_index", "page_range", "blocks"]
}
```

---

## 5. Concurrency & Resource Constraints

| Component | Nature | Concurrency Limit | Management Strategy |
| :--- | :--- | :--- | :--- |
| **PDF Slicing & Rendering** | CPU / Memory | `min(os.cpu_count(), 4)` | Lazy streaming window: render on-demand to avoid OOM on 1,000+ page docs |
| **Native Text/Asset Extract** | CPU / File I/O | `min(os.cpu_count(), 4)` | PyMuPDF batch operations restricted to requested `--pages` |
| **LLM Vision Requests** | Network I/O / Rate limit | **Fixed: 3** | `asyncio.Semaphore(3)` with exponential backoff retry |
| **JSON Merge & MD Render** | CPU / Light | 1 (sequential) | Single-threaded synchronous |

---

## 6. CLI & UX Design

### 6.1 Tooling & Environment
- **Package Manager**: `uv` (fast dependency resolution, script running via `uv run`).
- **CLI Framework**: `typer` + `rich` (for modern command flags, auto-help, and terminal visuals).
- **Python Version**: `>= 3.11`.

### 6.2 Command Line Interface
```bash
# Basic conversion (entire document with TOC and page anchors)
uv run pdf2md input.pdf -o output.md

# Targeted page ranges with custom page markers
uv run pdf2md input.pdf --pages "1-200, 401-500" --page-markers badge -o excerpt.md

# Full document with interactive TOC and plain page comments
uv run pdf2md input.pdf \
    --output ./dist/output.md \
    --toc \
    --page-markers anchor \
    --pages "1-100" \
    --assets-dir ./dist/assets \
    --chunk-size 8 \
    --llm-concurrency 3 \
    --model gpt-4o \
    --dpi 150 \
    --cache-dir ./.cache \
    --auto-bypass \
    --keep-json
```

### 6.3 Visual Terminal Output (Rich)
- **Status Dashboard**:
  - Live progress bars indicating:
    - `[1/4] PDF Partitioning & Page Rendering`
    - `[2/4] Asset & Raw Text Extraction`
    - `[3/4] LLM Multimodal Analysis` (shows active slots `[Worker 1: Chunk 1] [Worker 2: Chunk 2] [Worker 3: Chunk 3]`)
    - `[4/4] AST Assembly & Markdown Output`
- **Logging**: Structured logs with timestamps, levels (`INFO`, `DEBUG`, `WARN`, `ERROR`), and clear error diagnosis.
- **Interruption Gracefulness**: Catches `SIGINT` (Ctrl+C), saves current chunk state to cache, and cleanly shuts down active worker threads.

---

## 7. Recommended Dependency Stack

```toml
[project]
name = "pdf2md"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "typer>=0.12.0",
    "rich>=13.7.0",
    "pymupdf>=1.24.0",          # High-speed PDF rendering & native text/asset extraction
    "pydantic>=2.7.0",          # Data validation & structured output schemas
    "litellm>=1.40.0",          # Unified multimodal interface (OpenAI, Anthropic, Gemini, Ollama)
    "tenacity>=8.3.0",          # Robust retry and backoff logic
    "pillow>=10.3.0",           # Image inspection and format conversion
    "aiofiles>=23.2.0",         # Non-blocking async file writes
]
```

---

## 8. Non-Functional Requirements & Resilience

1. **Deterministic Fallback**: If an LLM call fails repeatedly after 3 retries (due to context limit or outage), the chunk falls back to raw extracted text with a markdown banner: `> [!WARNING] Page rendered via fallback raw text`.
2. **Caching & Idempotency**: Storing intermediate page images, raw text, and chunk JSONs in a hidden `.cache/` folder keyed by PDF SHA-256 hash. If interrupted, re-running skips completed chunks.
3. **Memory Safety & Streaming (1,000+ Page Scale)**: Renders pages lazily in a bounded sliding window ahead of LLM workers (no eager rendering of entire documents). Temporary image bitmaps are purged from RAM/disk immediately after chunk analysis completes.
4. **Hybrid Fast-Path (`--auto-bypass`)**: Optional classifier that inspects page metadata (absence of vector tables, complex formulas, and graphics). Pure single-column prose pages bypass vision LLM calls directly to native text markdown formatting, saving cost and time.
5. **No External OCR Binary**: The pipeline must run without installing system binaries like `tesseract`, `poppler-utils`, or `libtesseract`. All rendering and extraction must rely on self-contained Python wheels (`pymupdf`).
6. **Security & Privacy**: API keys sourced from environment variables (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, etc.) or `.env` files via `python-dotenv`.

---

## 9. Next Steps & Implementation Roadmap

- [ ] **Milestone 1**: Set up project repository using `uv init`, add dependencies, create CLI skeleton with `typer` & `rich`.
- [ ] **Milestone 2**: Implement `partition.py` and `extractor.py` using `PyMuPDF` for page splitting, rendering, raw text extraction, and asset extraction.
- [ ] **Milestone 3**: Define Pydantic AST models and build the async multimodal worker pool with `Semaphore(3)` and structured prompt templates.
- [ ] **Milestone 4**: Implement chunk stitcher and Markdown serializer.
- [ ] **Milestone 5**: Add caching/resume layer, comprehensive error handling, and test against complex sample PDFs.
