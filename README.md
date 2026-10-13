# pdf2md: Multimodal PDF-to-Markdown Converter

[![Python Version](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://python.org)
[![Package Manager](https://img.shields.io/badge/uv-fast-blueviolet.svg)](https://docs.astral.sh/uv/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

A high-fidelity CLI utility and Python library that transforms arbitrary, complex PDF documents into semantic, lint-clean CommonMark / GitHub Flavored Markdown (GFM).

Traditional OCR solutions (Tesseract, PaddleOCR) often discard layout geometry, corrupt mathematical notation, and scramble multi-column text. `pdf2md` uses a hybrid architecture combining **deterministic native PDF extraction** (PyMuPDF) with **multimodal Large Language Models** (via LiteLLM) to reconstruct document structure, tables, LaTeX formulas, code listings, and visual assets faithfully.

---

## Key Features

- **Zero External System Binaries**: Operates strictly using self-contained Python wheels (`pymupdf`, `pillow`). No external dependencies such as `tesseract-ocr`, `poppler-utils`, or `ghostscript` are required.
- **Modular Multi-Format Export Architecture**: Native downstream serializers project the structured AST into CommonMark/GFM Markdown (`.md`), standalone HTML5 (`.html`) with KaTeX/CSS, native Typst (`.typ`), or structured JSON AST (`.json`).
- **Structural Multi-File Partitioning (`--split-by`)**: Partition monolithic outputs into structured chapters, sections, or individual pages with auto-generated navigation tables of contents (`index.md`, `SUMMARY.md`, `index.html`).
- **Consistent Multimodal Vision Parsing**: All document pages are parsed using multimodal models (GPT-4o, Claude 3.5 Sonnet, Gemini 1.5 Pro, local Ollama) paired with native text ground truth to transcribe tables, convert math into standard LaTeX/Typst, infer code syntax, and extract embedded visual assets.
- **Cross-Chunk Boundary Stitching**: Automatically heals split paragraphs, rejoins line-wrapped hyphenated words, merges continuing tables, and prunes repetitive running headers/footers across pages.
- **Hardware-Aware Concurrency**: CPU-bound tasks (rendering, text stream decoding, asset extraction) scale with local CPU cores (`min(os.cpu_count(), 4)`). Network requests to multimodal vision APIs are strictly throttled to an asynchronous concurrency limit of **3**.
- **Deterministic Checkpointing & Resumption**: Intermediate chunk ASTs and asset manifests are persisted to a SHA-256 content-keyed cache directory (`.cache/<pdf-hash>/`). Interrupted jobs (or `SIGINT` / Ctrl+C) resume from the last completed chunk without duplicate API spend.
- **Interactive Table of Contents & Navigation**: Dual-source TOC synthesized from native PDF bookmarks (`doc.get_toc()`) and visual headings, with deep linking and customizable page markers (`anchor`, `badge`, `comment`, `none`).
- **Resilient Fallback**: Exponential backoff retry with jitter on network/rate-limit errors. If repeated LLM calls fail, the pipeline falls back gracefully to formatted native text without aborting the job.

---

## Architectural Pipeline

```
                                Input PDF Document
                                        │
                                        ▼
                     Stage 1: Partitioning & Page Selection
                     - Print range ("1-20") or Python slices ("[0:10]")
                     - Adaptive chunk partitioning (≤ 4 pages)
                                        │
                                        ▼
                     Stage 2: Deterministic Local Extraction
                     - Render page PNGs at configurable DPI (150-200)
                     - Extract raw text spans with font metadata
                     - Extract & SHA-256 deduplicate embedded assets
                                        │
                                        ▼
                     Stage 3: Multimodal Vision LLM Parsing
                     - Concurrency bounded to 3 workers
                     - Enforce strict JSON AST Schema
                     - Exponential retry with jitter & raw text fallback
                                        │
                                        ▼
                     Stage 4: Cross-Chunk AST Stitching
                     - Heal split paragraphs & hyphenated words
                     - Reconcile severed tables across boundaries
                     - Suppress repetitive running chrome (headers/footers)
                     - Synthesize dual-source interactive Table of Contents
                                        │
                                        ▼
                           Validated DocumentAST (IR)
                                        │
                        ┌───────────────┴───────────────┐
                        │ Structural Multi-File Split?  │
                        └───┬───────────────────────┬───┘
                       YES  │                       │ NO
                            ▼                       ▼
                   partition_ast()          Single Monolithic AST
                 (page, chapter, etc.)              │
                            │                       │
                            └───────────────┬───────┘
                                            │
                                            ▼
                           Modular Exporter Dispatcher
                                (pdf2md.exporters)
             ┌───────────────┬───────────────┬───────────────┐
             ▼               ▼               ▼               ▼
        Markdown           HTML5           Typst         JSON AST
        (.md)             (.html)          (.typ)         (.json)
     - GFM tables      - Standalone/    - Native math   - Structured
     - LaTeX math        Fragment       - #table()        Pydantic
     - GitHub alerts   - KaTeX CDN      - #figure()       model dump
     - SUMMARY.md      - Inlined data:  - Preamble
```

---

## Installation

Ensure you have [uv](https://docs.astral.sh/uv/) and Python >= 3.11 installed:

### Global CLI Tool (Run anywhere on your machine)

To install `pdf2md` globally into your system path (e.g., `~/.local/bin/pdf2md`) so you can run `pdf2md` directly from any folder without `uv run`:

```bash
# Clone the repository
git clone https://github.com/DaviMGDev/pdf2md.git
cd pdf2md

# Install globally as a standalone CLI tool
uv tool install .

# Or install in editable mode (so any local changes are immediately live):
uv tool install --editable .
```

### Local Development Setup

```bash
# Clone and install dependencies into project virtual environment
git clone https://github.com/DaviMGDev/pdf2md.git
cd pdf2md
uv sync
```

---

## Usage

### Basic Conversion

Convert a PDF document using the default model (`gpt-4o`):

```bash
export OPENAI_API_KEY="your-api-key"
uv run pdf2md paper.pdf
```

This outputs `paper.md` in the current directory and extracts embedded images to `./assets/`.

### Specify Output Destination & Formats

`pdf2md` supports a modular multi-format export engine. You can emit single formats, bundle presets (`both`, `all`), or specify multiple comma-separated formats simultaneously from a single AST run:

```bash
# Custom output file path
uv run pdf2md paper.pdf -o ./output/document.md

# Native Typst export (.typ)
uv run pdf2md paper.pdf --format typ

# Standalone HTML5 with KaTeX math rendering and responsive styles
uv run pdf2md paper.pdf --format html

# Structured DocumentAST JSON only
uv run pdf2md paper.pdf --format json

# Export Markdown and JSON simultaneously
uv run pdf2md paper.pdf --format both

# Export all supported formats (md, json, html, typ) in one run
uv run pdf2md paper.pdf --format all

# Custom comma-separated multi-format export
uv run pdf2md paper.pdf --format md,html,typ
```

#### Standalone Documents & Asset Embedding

When exporting to HTML or Typst, you can control document wrapping and asset portability:

```bash
# Standalone HTML5 with responsive styles and KaTeX/MathJax CDN (default)
uv run pdf2md paper.pdf --format html --standalone

# HTML fragment without <html>/<head> wrapping (ideal for CMS or blogs)
uv run pdf2md paper.pdf --format html --no-standalone

# Zero-dependency single-file HTML with embedded base64 data URIs
uv run pdf2md paper.pdf --format html --embed-assets
```

### Multi-File Partitioning (`--split-by`)

For large documents such as textbooks, manuals, or dissertations, `pdf2md` can partition output across multiple structured files rather than writing a single monolithic document.

Supported partitioning strategies:
- **`page`**: One document per page (`page-001.md`, `page-002.md`, ...)
- **`chapter`**: One document per top-level heading (`01-introduction.md`, `02-methods.md`, ...)
- **`section`**: Slices on both H1 and H2 section boundaries
- **`chapter/page`**: Hierarchical folder structure (`01-introduction/page-001.md`, ...)

```bash
# Partition a book by chapter into Markdown (generates index.md & mdBook-compatible SUMMARY.md)
uv run pdf2md textbook.pdf --split-by chapter -o ./dist/textbook/

# Partition by page into HTML pages with a portal index.html
uv run pdf2md slides.pdf --split-by page --format html -o ./dist/slides/

# Hierarchical directory structure (chapters with per-page files)
uv run pdf2md dissertation.pdf --split-by chapter/page
```

When partitioning is active, an index table of contents (`index.md` / `index.html` / `index.json` / `SUMMARY.md`) with relative navigation links is automatically generated in the destination directory.

### Page Selection & Slicing

Select specific pages using print notation or Python slice syntax:

```bash
# Print-style ranges
uv run pdf2md paper.pdf -p "1-10, 25, 40-50"

# Python slice notation
uv run pdf2md paper.pdf -p "[0:10]"
uv run pdf2md paper.pdf -p "[:5] + [20:30]"
uv run pdf2md paper.pdf -p "[-10:]"
```

### Multimodal Model Selection

`pdf2md` uses LiteLLM under the hood, supporting OpenAI, Anthropic, Google Gemini, and custom endpoints:

```bash
# Claude 3.5 Sonnet
export ANTHROPIC_API_KEY="your-api-key"
uv run pdf2md paper.pdf --model claude-3-5-sonnet

# Gemini 1.5 Pro
export GEMINI_API_KEY="your-api-key"
uv run pdf2md paper.pdf --model gemini/gemini-1.5-pro
```

### Local Models & Custom Endpoints

`pdf2md` works with local Ollama models or OpenAI-compatible gateways:

```bash
# Local Ollama model
uv run pdf2md paper.pdf --model ollama/gemma4:cloud

# Custom context window and timeout
uv run pdf2md paper.pdf --model ollama/llama3 --context-window 32768 --timeout 180
```

### Page Navigation Markers & TOC

Customize page boundary markers and Table of Contents generation:

```bash
# HTML anchor markers (default): <a id="page-N"></a>
uv run pdf2md paper.pdf --page-markers anchor

# Visual badge markers: <small class="pdf-page-indicator">📄 Page N</small>
uv run pdf2md paper.pdf --page-markers badge

# HTML comment markers: <!-- [Page N] -->
uv run pdf2md paper.pdf --page-markers comment

# Disable page markers
uv run pdf2md paper.pdf --page-markers none

# Disable Table of Contents generation
uv run pdf2md paper.pdf --no-toc
```

---

## CLI Options

| Option | Short | Default | Description |
| :--- | :--- | :--- | :--- |
| `--output` | `-o` | `<stem>.<ext>` | Destination output file path or directory |
| `--format` | `-f` | `md` | Output format(s): `md`, `json`, `html`, `typ`, `both`, `all`, or comma-separated list |
| `--standalone` / `--no-standalone` | | `standalone` | Emit standalone document with styles/scripts (HTML/Typst) |
| `--embed-assets`, `--single-file` | | `False` | Inline assets/images as base64 data URLs |
| `--split-by` | | `None` | Partition output by boundary: `page`, `chapter`, `section`, `chapter/page` |
| `--split-pattern` | | `None` | Custom output filename template for partitioned files |
| `--pages` | `-p` | `all` | Page range e.g. `"1-10"` or `"[0:10]"` |
| `--model` | | `gpt-4o` | Multimodal LLM identifier |
| `--max-tokens` | | `8192` | Maximum completion tokens for LLM generation |
| `--context-window`, `--num-ctx` | | `None` | Context window token size for Ollama / local models |
| `--timeout` | | `120.0` | LLM request timeout in seconds |
| `--page-markers`| `-m` | `anchor` | Boundary marker style: `anchor`, `badge`, `comment`, `none` |
| `--toc` / `--no-toc` | | `toc` | Toggle interactive Table of Contents |
| `--chunk-size` | `-c` | `4` | Maximum pages per chunk (1–16) |
| `--adaptive-chunking` | | `True` | Adaptively reduce chunk size for dense pages |
| `--no-stitch` | | `False` | Disable cross-chunk boundary paragraph/table stitching |
| `--llm-concurrency` | | `3` | Concurrent LLM requests bound |
| `--assets-dir` | `-a` | `./assets` | Destination folder for extracted images |
| `--cache-dir` | | `.cache` | Directory for intermediate checkpoint cache |
| `--no-cache` | | `False` | Invalidate and bypass existing disk cache |
| `--dpi` | | `150` | Rendering resolution DPI (72–400) |
| `--verbose` | `-v` | `0` | Enable verbose logging (`-v` for INFO, `-vv` for DEBUG) |
| `--debug` | | `False` | Enable debug logging (alias for `-vv`) |
| `--log-level` | | `None` | Explicit logging level: `DEBUG`, `INFO`, `WARN`, `ERROR` |
| `--auto-bypass` | | `False` | *[Deprecated]* Opt-in heuristic fast-path bypass |

### Exit Codes

| Code | Status | Meaning |
| :--- | :--- | :--- |
| `0` | `SUCCESS` | Document converted completely |
| `1` | `GENERAL_ERROR` | Internal unhandled error |
| `2` | `CORRUPT_PDF` | PDF is corrupted and unrepairable |
| `4` | `INVALID_ARGUMENTS` | Invalid page range or CLI arguments |
| `5` | `AUTHENTICATION_ERROR` | Missing or invalid provider API key |
| `130` | `USER_INTERRUPT` | Process terminated cleanly via SIGINT (`Ctrl+C`) |

---

## Running Tests

Run the full unit and acceptance test suite with pytest:

```bash
uv run pytest
```

---

## License

MIT License. See [LICENSE](LICENSE) for details.
