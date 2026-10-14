# docforge: Multimodal Document Ingestion & Multi-Format Typesetting Engine

[![Python Version](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://python.org)
[![Package Manager](https://img.shields.io/badge/uv-fast-blueviolet.svg)](https://docs.astral.sh/uv/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

A high-fidelity CLI utility and Python library that transforms arbitrary, complex PDF documents into semantic, lint-clean CommonMark / GitHub Flavored Markdown (GFM), standalone HTML5 with KaTeX, native Typst, or structured DocumentAST JSON.

Traditional OCR solutions (Tesseract, PaddleOCR) often discard layout geometry, corrupt mathematical notation, and scramble multi-column text. `docforge` (formerly `pdf2md`) uses a hybrid architecture combining **deterministic native PDF extraction** (PyMuPDF) with **multimodal Large Language Models** (via LiteLLM) to reconstruct document structure, tables, LaTeX formulas, code listings, and visual assets faithfully.

> **Backwards Compatibility**: Both `docforge` and `pdf2md` terminal commands and Python imports (`import docforge`, `import pdf2md`) are fully supported.

---

## Key Features

- **Zero External System Binaries**: Operates strictly using self-contained Python wheels (`pymupdf`, `pillow`). No external dependencies such as `tesseract-ocr`, `poppler-utils`, or `ghostscript` are required.
- **Modular Multi-Format Export Architecture**: Native downstream serializers project the structured AST into CommonMark/GFM Markdown (`.md`), standalone HTML5 (`.html`) with KaTeX/CSS, native Typst (`.typ`), or structured JSON AST (`.json`).
- **Structural Multi-File Partitioning (`--split-by`)**: Partition monolithic outputs into structured chapters, sections, or individual pages with auto-generated navigation tables of contents (`index.md`, `SUMMARY.md`, `index.html`).
- **Custom System Prompts & Guidelines**: Tailor extraction visual behavior with `--instructions` (e.g. ignoring margin scribbles, headers, or watermarks) or load local guidelines via `--prompt-file SYSTEM.md`.
- **Persistent XDG Configuration**: Manage default LLM models, API keys, concurrency, and format preferences via standard `~/.config/docforge/settings.json` (or `$XDG_CONFIG_HOME/docforge/settings.json`).
- **Consistent Multimodal Vision Parsing**: All document pages are parsed using multimodal models (GPT-4o, Claude 3.5 Sonnet, Gemini 1.5 Pro, local Ollama) paired with native text ground truth to transcribe tables, convert math into standard LaTeX/Typst, infer code syntax, and extract embedded visual assets.
- **Cross-Chunk Boundary Stitching**: Automatically heals split paragraphs, rejoins line-wrapped hyphenated words, merges continuing tables, and prunes repetitive running headers/footers across pages.
- **Hardware-Aware Concurrency**: CPU-bound tasks (rendering, text stream decoding, asset extraction) scale with local CPU cores (`min(os.cpu_count(), 4)`). Network requests to multimodal vision APIs are strictly throttled to an asynchronous concurrency limit of **3**.
- **Deterministic Checkpointing & Resumption**: Intermediate chunk ASTs and asset manifests are persisted to a SHA-256 content-keyed cache directory (`.cache/<pdf-hash>/`). Interrupted jobs (or `SIGINT` / Ctrl+C) resume from the last completed chunk without duplicate API spend. Prompt instruction changes automatically invalidate the chunk cache.
- **Interactive Table of Contents & Navigation**: Dual-source TOC synthesized from native PDF bookmarks (`doc.get_toc()`) and visual headings, with deep linking and customizable page markers (`anchor`, `badge`, `comment`, `none`).

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
                     - Enforce strict JSON AST Schema & custom instructions
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
                               (docforge.exporters)
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

To install `docforge` globally into your system path (e.g., `~/.local/bin/docforge`) so you can run `docforge` directly from any folder without `uv run`:

```bash
# Clone the repository
git clone https://github.com/DaviMGDev/pdf2md.git docforge
cd docforge

# Install globally as a standalone CLI tool
uv tool install .

# Or install in editable mode (so any local changes are immediately live):
uv tool install --editable .
```

### Local Development Setup

```bash
# Clone and install dependencies into project virtual environment
git clone https://github.com/DaviMGDev/pdf2md.git docforge
cd docforge
uv sync
```

---

## Configuration

`docforge` resolves configuration defensively according to a strict **5-tier precedence hierarchy**:

1. **Explicit CLI flags** (highest priority)
2. **Environment variables** (`DOCFORGE_*`, `PDF2MD_*`, `OPENAI_API_KEY`, etc.)
3. **Current working directory `.env` file**
4. **Persistent user configuration file** (`~/.config/docforge/settings.json`)
5. **Built-in defaults** (lowest priority)

### Persistent User Settings (`~/.config/docforge/settings.json`)

```json
{
  "llm": {
    "model_name": "ollama/gemma4:cloud",
    "api_base": "http://localhost:11434",
    "api_key": null,
    "max_tokens": 8192,
    "context_window": 32768,
    "timeout_seconds": 120.0
  },
  "defaults": {
    "format": ["md"],
    "page_markers": "anchor",
    "generate_toc": true,
    "chunk_size": 4,
    "llm_concurrency": 3,
    "cache_dir": "~/.cache/docforge",
    "instructions": null,
    "prompt_file": "SYSTEM.md"
  }
}
```

---

## Usage

### Basic Conversion

Convert a PDF document using the default model (`gpt-4o`):

```bash
export OPENAI_API_KEY="your-api-key"
docforge paper.pdf
```

This outputs `paper.md` in the current directory and extracts embedded images to `./assets/`.

### Specify Output Destination & Formats

`docforge` supports a modular multi-format export engine. You can emit single formats, bundle presets (`both`, `all`), or specify multiple comma-separated formats simultaneously from a single AST run:

```bash
# Custom output file path
docforge paper.pdf -o ./output/document.md

# Native Typst export (.typ)
docforge paper.pdf --format typ

# Standalone HTML5 with KaTeX math rendering and responsive styles
docforge paper.pdf --format html

# Structured DocumentAST JSON only
docforge paper.pdf --format json

# Export Markdown and JSON simultaneously
docforge paper.pdf --format both

# Export all supported formats (md, json, html, typ) in one run
docforge paper.pdf --format all

# Custom comma-separated multi-format export
docforge paper.pdf --format md,html,typ
```

#### Standalone Documents & Asset Embedding

When exporting to HTML or Typst, you can control document wrapping and asset portability:

```bash
# Standalone HTML5 with responsive styles and KaTeX/MathJax CDN (default)
docforge paper.pdf --format html --standalone

# HTML fragment without <html>/<head> wrapping (ideal for CMS or blogs)
docforge paper.pdf --format html --no-standalone

# Zero-dependency single-file HTML with embedded base64 data URIs
docforge paper.pdf --format html --embed-assets
```

### Custom Prompts & Transcription Instructions

Tailor the vision LLM prompt for specialized documents without breaking JSON schema integrity:

```bash
# Filter out marginalia, reviewer comments, or handwritten scribbles
docforge lecture_notes.pdf --instructions "Ignore all handwritten pen notes and marginalia; transcribe only native printed text."

# Suppress proprietary watermarks or stamps
docforge internal_doc.pdf --prompt "Ignore 'CONFIDENTIAL' diagonal draft watermarks and header logos."

# Load detailed transcription rules from a local file
docforge textbook.pdf --prompt-file ./SYSTEM.md
```

### Multi-File Partitioning (`--split-by`)

For large documents such as textbooks, manuals, or dissertations, `docforge` can partition output across multiple structured files rather than writing a single monolithic document:

```bash
# Partition a book by chapter into Markdown (generates index.md & mdBook-compatible SUMMARY.md)
docforge textbook.pdf --split-by chapter -o ./dist/textbook/

# Partition by page into HTML pages with a portal index.html
docforge slides.pdf --split-by page --format html -o ./dist/slides/

# Hierarchical directory structure (chapters with per-page files)
docforge dissertation.pdf --split-by chapter/page
```

When partitioning is active, an index table of contents (`index.md` / `index.html` / `index.json` / `SUMMARY.md`) with relative navigation links is automatically generated in the destination directory.

### Page Selection & Slicing

Select specific pages using print notation or Python slice syntax:

```bash
# Print-style ranges
docforge paper.pdf -p "1-10, 25, 40-50"

# Python slice notation
docforge paper.pdf -p "[0:10]"
docforge paper.pdf -p "[:5] + [20:30]"
docforge paper.pdf -p "[-10:]"
```

### Multimodal Model Selection

`docforge` uses LiteLLM under the hood, supporting OpenAI, Anthropic, Google Gemini, and custom endpoints:

```bash
# Claude 3.5 Sonnet
export ANTHROPIC_API_KEY="your-api-key"
docforge paper.pdf --model claude-3-5-sonnet

# Gemini 1.5 Pro
export GEMINI_API_KEY="your-api-key"
docforge paper.pdf --model gemini/gemini-1.5-pro

# Local Ollama model
docforge paper.pdf --model ollama/gemma4:cloud

# Local model with custom context window and timeout
docforge paper.pdf --model ollama/llama3 --context-window 32768 --timeout 180
```

### Page Navigation Markers & TOC

Customize page boundary markers and Table of Contents generation:

```bash
# HTML anchor markers (default): <a id="page-N"></a>
docforge paper.pdf --page-markers anchor

# Visual badge markers: <small class="pdf-page-indicator">📄 Page N</small>
docforge paper.pdf --page-markers badge

# HTML comment markers: <!-- [Page N] -->
docforge paper.pdf --page-markers comment

# Disable page markers
docforge paper.pdf --page-markers none

# Disable Table of Contents generation
docforge paper.pdf --no-toc
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
| `--model` | | `gemma4:cloud` | Multimodal LLM identifier |
| `--max-tokens` | | `8192` | Maximum completion tokens for LLM generation |
| `--context-window`, `--num-ctx` | | `None` | Context window token size for Ollama / local models |
| `--timeout` | | `120.0` | LLM request timeout in seconds |
| `--instructions`, `--prompt` | | `None` | Custom transcription guidelines appended to vision system prompt |
| `--prompt-file` | | `None` | Path to text/markdown file containing custom guidelines |
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
