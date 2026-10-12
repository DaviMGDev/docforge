# pdf2md: Multimodal PDF-to-Markdown Converter

[![Python Version](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://python.org)
[![Package Manager](https://img.shields.io/badge/uv-fast-blueviolet.svg)](https://docs.astral.sh/uv/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

A high-fidelity CLI utility and Python library that transforms arbitrary, complex PDF documents into semantic, lint-clean CommonMark / GitHub Flavored Markdown (GFM).

Traditional OCR solutions (Tesseract, PaddleOCR) often discard layout geometry, corrupt mathematical notation, and scramble multi-column text. `pdf2md` uses a hybrid architecture combining **deterministic native PDF extraction** (PyMuPDF) with **multimodal Large Language Models** (via LiteLLM) to reconstruct document structure, tables, LaTeX formulas, code listings, and visual assets faithfully.

---

## Key Features

- **Zero External System Binaries**: Operates strictly using self-contained Python wheels (`pymupdf`, `pillow`). No external dependencies such as `tesseract-ocr`, `poppler-utils`, or `ghostscript` are required.
- **Semantic Multimodal Vision Parsing**: Page renderings paired with native text ground truth allow multimodal models (e.g., GPT-4o, Claude 3.5 Sonnet, Gemini 1.5 Pro) to transcribe tables (GFM/HTML), convert math into standard LaTeX (`$...$` and `$$...$$`), infer code block languages, and associate embedded figures.
- **Cross-Chunk Boundary Stitching**: Automatically heals split paragraphs, rejoins line-wrapped hyphenated words, merges continuing tables, and prunes repetitive running headers/footers across pages.
- **Hardware-Aware Concurrency**: CPU-bound tasks (rendering, text stream decoding, asset extraction) scale with local CPU cores (`min(os.cpu_count(), 4)`). Network requests to multimodal vision APIs are strictly throttled to an asynchronous concurrency limit of **3**.
- **Fast-Path Hybrid Bypass (`--auto-bypass`)**: Automatically detects trivial, single-column prose pages and bypasses vision LLMs entirely using deterministic layout analysis, saving API spend and conversion time.
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
                        ┌───────────────┴───────────────┐
                        │   Fast-Path Bypass Check      │
                        │   Is chunk trivial prose?     │
                        └───┬───────────────────────┬───┘
                       YES  │                       │ NO
                            ▼                       ▼
              Direct Markdown Layout      Stage 3: Multimodal LLM Parsing
              (Zero LLM spend)            - Concurrency bounded to 3
                                          - Structured JSON schema output
                                          - Exponential retry & raw fallback
                            │                       │
                            └───────────────┬───────┘
                                            │
                                            ▼
                     Stage 4: Cross-Chunk AST Stitching
                     - Heal split paragraphs & hyphenated words
                     - Reconcile severed tables across boundaries
                     - Suppress repetitive running chrome (headers/footers)
                                            │
                                            ▼
                     Stage 5: Markdown Synthesis & Navigation
                     - Synthesize interactive Table of Contents
                     - Inject page navigation markers
                     - Emit CommonMark / GFM Markdown
                                            │
                            ┌───────────────┴───────────────┐
                            ▼                               ▼
                    output_document.md                   assets/
                                                         ├── asset_p1_00.png
                                                         └── asset_p5_01.jpg
```

---

## Installation

Ensure you have [uv](https://docs.astral.sh/uv/) and Python >= 3.11 installed:

```bash
# Clone the repository
git clone https://github.com/davimgomes/pdf2md.git
cd pdf2md

# Install dependencies and setup virtual environment
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

### Specify Output Destination & Format

```bash
# Custom output file path
uv run pdf2md paper.pdf -o ./output/document.md

# Output both Markdown and AST JSON
uv run pdf2md paper.pdf --format both -o ./output/document.md

# Output structured DocumentAST JSON only
uv run pdf2md paper.pdf --format json -o ./output/document.json
```

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

### Fast-Path Optimization

Enable automatic bypass for simple single-column prose pages to eliminate LLM spend and speed up processing:

```bash
uv run pdf2md paper.pdf --auto-bypass
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
| `--output` | `-o` | `<stem>.md` | Destination output file path |
| `--format` | `-f` | `md` | Output format: `md`, `json`, or `both` |
| `--pages` | `-p` | `all` | Page range e.g. `"1-10"` or `"[0:10]"` |
| `--model` | | `gpt-4o` | Multimodal LLM identifier |
| `--page-markers`| `-m` | `anchor` | Boundary marker style: `anchor`, `badge`, `comment`, `none` |
| `--toc` / `--no-toc` | | `toc` | Toggle interactive Table of Contents |
| `--auto-bypass` | | `False` | Opt-in heuristic fast-path bypass for trivial prose |
| `--chunk-size` | `-c` | `4` | Maximum pages per chunk (1–16) |
| `--adaptive-chunking` | | `True` | Adaptively reduce chunk size for dense pages |
| `--no-stitch` | | `False` | Disable cross-chunk boundary paragraph/table stitching |
| `--assets-dir` | `-a` | `./assets` | Destination folder for extracted images |
| `--cache-dir` | | `.cache` | Directory for intermediate checkpoint cache |
| `--no-cache` | | `False` | Invalidate and bypass existing disk cache |
| `--dpi` | | `150` | Rendering resolution DPI (72–400) |
| `--verbose` | `-v` | `False` | Enable verbose debug logging and tracebacks |

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
