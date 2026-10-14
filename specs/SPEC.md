# System Specification: Multimodal PDF-to-Markdown Converter (`docforge`)

**Version:** 1.0.0  
**Status:** Approved / Canonical Specification  
**Target Runtime:** Python >= 3.11 with `uv` package management  
**Primary Engine:** PyMuPDF (`fitz`) + Multimodal LLMs (`litellm`)  

---

## 1. System Overview & Philosophy

### 1.1 Purpose
`docforge` is a command-line utility and Python library that transforms arbitrary, complex PDF documents into semantic, lint-clean CommonMark/GitHub Flavored Markdown (GFM). The system bypasses traditional, fragile OCR engines (e.g., Tesseract, PaddleOCR, OCRopus) in favor of a hybrid architecture:
1. **Deterministic Native Extraction**: High-fidelity character stream extraction, font metadata, vector outline analysis, and raw asset extraction directly from PDF byte streams via native PyMuPDF bindings.
2. **Multimodal LLM Structural Parsing**: Vision-language models (e.g., GPT-4o, Claude 3.5 Sonnet, Gemini 1.5 Pro) receive page bitmap renderings alongside native text ground truth and an extracted asset manifest to classify document blocks, transcribe formulas into standard LaTeX, build clean Markdown/HTML tables, and place assets accurately.
3. **Cross-Chunk AST Synthesis**: Chunked ASTs are reconciled across page boundaries to heal split sentences, continuing tables, and recurring running headers/footers.

### 1.2 Core Architectural Principles
- **Zero External System Binaries**: The pipeline relies strictly on self-contained Python wheels (`pymupdf`, `pillow`). No external system binaries (`tesseract-ocr`, `poppler-utils`, `ghostscript`) may be required.
- **Hardware-Bounded Concurrency**: CPU-bound tasks (rendering, text stream decoding, image hashing) scale with local CPU cores (`min(os.cpu_count(), 4)`). Expensive network I/O to LLM vision endpoints is strictly throttled to an asynchronous concurrency limit of **3**.
- **Memory Safety at Scale (1,000+ Pages)**: Documents are processed using a bounded sliding window. The system never eagerly renders or buffers an entire book into RAM. Bitmap buffers are evicted immediately after LLM chunk submission.
- **Deterministic Checkpointing & Resumption**: All intermediate chunk ASTs and asset manifests are persisted to a SHA-256 content-keyed cache directory (`.cache/<pdf-hash>/`). Interrupted jobs cleanly resume from the last completed chunk without duplicate LLM API spend.
- **Dual-Source Functional Navigation**: Full document navigation is preserved via deterministic HTML page anchors (`<a id="page-N"></a>`) and an interactive Table of Contents synthesized from both native PDF outline bookmarks (`doc.get_toc()`) and visual AST headings.
- **Graceful Fallback & Hybrid Fast-Path**: Pure single-column prose pages can bypass vision LLMs entirely (`--auto-bypass`). When LLM calls fail repeatedly (after 3 exponential retries), the pipeline deterministically falls back to formatted native text.

---

## 2. Architectural Pipeline

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                               Input PDF Document                                │
└────────────────────────────────────────┬────────────────────────────────────────┘
                                         │
                                         ▼
                    ┌─────────────────────────────────────────┐
                    │ Stage 1: Partitioning & Page Selection  │
                    │ - Parse range syntax (e.g. 1-20, [4:8]) │
                    │ - Partition into chunks ≤ 4p (adaptive) │
                    └────────────────────┬────────────────────┘
                                         │
                                         ▼
                    ┌─────────────────────────────────────────┐
                    │ Stage 2: Hardware-Bounded Prep          │
                    │ - Concurrency: min(cpu_count, 4)        │
                    │ - Render page PNGs (150-200 DPI)        │
                    │ - Extract raw text spans & bounding box │
                    │ - Extract & SHA-256 dedupe image streams│
                    └────────────────────┬────────────────────┘
                                         │
                        ┌────────────────┴────────────────┐
                        │ [Stage 2.5: Fast-Path Check]     │
                        │ Is chunk trivial prose?         │
                        └───┬─────────────────────────┬───┘
                       YES  │                         │ NO
                            ▼                         ▼
              ┌──────────────────────────┐  ┌───────────────────────────────────┐
              │ Direct Markdown Layout   │  │ Stage 3: Bounded Multimodal LLM   │
              │ (Bypass LLM Vision)      │  │ - Concurrency = 3 (async pool)   │
              └─────────────┬────────────┘  │ - Input: Images + Text + Assets   │
                            │               │ - Structured JSON Schema output   │
                            │               │ - Fallback to raw text on error   │
                            │               └─────────────────┬─────────────────┘
                            │                                 │
                            └────────────────┬────────────────┘
                                             │
                                             ▼
                    ┌─────────────────────────────────────────┐
                    │ Stage 4: Cross-Chunk AST Stitching      │
                    │ - Heal cross-boundary split paragraphs  │
                    │ - Reconcile split tables & code blocks  │
                    │ - Suppress running headers/footers      │
                    │ - Normalize heading levels              │
                    └────────────────────┬────────────────────┘
                                         │
                                         ▼
                    ┌─────────────────────────────────────────┐
                    │ Stage 5: Markdown Synthesis             │
                    │ - Synthesize interactive TOC            │
                    │ - Inject page markers (anchor/badge)    │
                    │ - Emit CommonMark / GFM document        │
                    └────────────────────┬────────────────────┘
                                         │
                         ┌───────────────┴───────────────┐
                         ▼                               ▼
                 output_document.md                   assets/
                                                       ├── asset_p1_01.png
                                                       └── asset_p5_02.jpg
```

---

## 3. Stage Specifications

### 3.1 Stage 1: Partitioning & Page Selection (`partition`)

#### 3.1.1 Inputs and Range Formats
The partitioner accepts an input file path and an optional range string specified via `--pages` / `-p`. The range string supports two paradigms:
1. **Print Range Notation**: Comma-separated 1-based page numbers and ranges, e.g., `"1-200, 401-500"`, `"1, 3, 5, 10-15"`.
2. **Python Slice Notation**: Comma-separated or concatenated brackets, e.g., `"[0:200]"`, `"[0:200] + [400:500]"`, `"[::2]"`.

#### 3.1.2 Normalization Rules
1. Python slices are evaluated against a 0-based index `range(total_pages)` and shifted to 1-based indices.
2. Negative indices are resolved relative to total page count (e.g., `[-10:]` represents the last 10 pages).
3. Out-of-bounds indices are clamped to `[1, total_pages]`. If an entire specified range lies outside document bounds, an `InvalidPageRangeException` is raised.
4. Duplicate pages across overlapping ranges are deduplicated and sorted in strictly ascending natural order.

#### 3.1.3 Chunk Slicing & Adaptive Sizing Rules
1. Default maximum chunk size is **4 pages** (`chunk_size <= 4`), configurable via `--chunk-size` / `-c` (1..16).
2. **Adaptive Sizing Heuristic**: When `--adaptive-chunking` is enabled (default: true), the partitioner inspects page density (word count and embedded image count). If a chunk's pages average > 800 words/page or > 3 images/page, the chunk size is dynamically throttled down to 1–2 pages to prevent LLM context and vision token blow-up.
3. Slicing maintains contiguity: If normalized pages contain non-contiguous gaps (e.g., `1..4` and `20..23`), chunk boundaries are forcibly severed at the discontinuity, yielding disjoint chunks.
4. Every chunk metadata record preserves:
   - `chunk_index`: 0-based contiguous integer.
   - `global_page_indices`: Array of 1-based page numbers from the original PDF (e.g., `[1, 2, 3, 4]`).
   - `page_count`: Number of pages in the chunk (`1 <= page_count <= chunk_size`).

---

### 3.2 Stage 2: Hardware-Bounded Deterministic Prep (`extract_local`)

#### 3.2.1 Execution Model
- Local preprocessing runs inside a bounded multiprocessing or asynchronous process worker pool constrained by:
  $$\text{workers}_{\text{cpu}} = \min(\text{os.cpu\_count}(), 4)$$
- Employs streaming iteration: chunks are extracted just-in-time relative to LLM worker consumption to enforce the sliding memory window.

#### 3.2.2 Page Image Rendering
- **Tool**: `fitz.Page.get_pixmap()`
- **Resolution**: Configurable via `--dpi` (default: `150` DPI; high-fidelity: `200` DPI; zoom factor: `DPI / 72.0`).
- **Color Space**: RGB 24-bit.
- **Output Format**: PNG compressed with Deflate level 6.
- **Memory Optimization**: The raw pixmap buffer is written directly to disk cache or wrapped in an in-memory byte buffer and immediately unreferenced in Python to release C-heap memory.

#### 3.2.3 Raw Text & Layout Extraction
- **Tool**: `fitz.Page.get_text("words")` and `fitz.Page.get_text("blocks")`.
- **Extracted Metadata**:
  - Raw un-hyphenated text string.
  - Bounding boxes `(x0, y0, x1, y1)` per text block.
  - Font flags (bold, italic, serif, monospace) and font sizes.
  - Estimated line breaks and paragraph separations.
- **Purpose**: Ground-truth text stream provided to the multimodal LLM to eliminate spelling hallucinations, ensure exact variable/code naming, and enable fast-path bypass.

#### 3.2.4 Embedded Asset Extraction & Deduplication
- **Tool**: `fitz.Page.get_images(full=True)`
- **Extraction Protocol**:
  1. For each image XObject on the page, extract the raw image stream using `fitz.Pixmap(doc, xref)`.
  2. Handle CMYK or color-spaced masks by converting pixmaps to sRGB via `fitz.Pixmap(fitz.csRGB, pixmap)`.
  3. Calculate the SHA-256 digest of the raw image bytes:
     $$\text{hash} = \text{SHA256}(\text{bytes})$$
  4. If `hash` already exists in the global `AssetDeduplicationTable`, map the occurrence to the existing asset path.
  5. If new:
     - Determine extension (`png`, `jpeg`, `webp`).
     - Save to `<output_dir>/assets/asset_p{page}_{seq:02d}.{ext}`.
     - Record asset metadata: `page`, `bbox`, `width`, `height`, `sha256`, `relative_path`.
  6. Filter out noise: embedded icons, bullet points, or hairline raster lines smaller than $24 \times 24$ pixels are discarded.

---

### 3.3 Stage 2.5: Fast-Path Hybrid Bypass Classifier (`auto_bypass`)

When `--auto-bypass` is enabled, each chunk's pages are evaluated by a deterministic heuristic classifier before scheduling multimodal LLM inference.

#### 3.3.1 Fast-Path Evaluation Criteria
A page is classified as **Trivial Prose** if all of the following conditions hold:
1. **Vector Drawing Paths**: Zero vector curves, rects, or drawing commands (`len(page.get_drawings()) == 0` or only standard horizontal rules).
2. **Table Grid Absence**: No vertical gridlines, tab-separated tabular bounding boxes, or alternating background rects.
3. **No Embedded Images**: `len(page.get_images()) == 0`.
4. **No Mathematical Symbols**: Absence of common Unicode math symbols ($\sum, \int, \in, \forall, \sqrt{}$, Greek letters in math contexts).
5. **Single-Column Reading Flow**: All text blocks follow a monotonic downward vertical progression ($y_{0, k+1} \ge y_{0, k}$) with overlapping horizontal boundaries ($x_{0}$ consistent within $\pm 20\text{pt}$).

#### 3.3.2 Routing Action
- If **all** pages in a chunk qualify as Trivial Prose, the chunk bypasses Stage 3 entirely.
- The deterministic layout engine directly parses font size differentials into Markdown headings (`#`, `##`), line wraps into paragraphs, and indentation into code/lists.
- Emits a valid `DocumentChunkAST` marked with `bypass_used: true`, bypassing LLM token consumption and latency.

---

### 3.4 Stage 3: Bounded Multimodal LLM Structural Parsing (`llm_parse`)

#### 3.4.1 Concurrency Model
- LLM worker pool is managed via `asyncio.Semaphore(3)`.
- Exactly 3 concurrent network requests may be inflight simultaneously.
- Worker slots are displayed dynamically in the Rich CLI terminal interface:
  `[Worker 1: Chunk 04] [Worker 2: Chunk 05] [Worker 3: Chunk 06]`.

#### 3.4.2 LLM Request Payload Composition
Each request presents the multimodal model with:
1. **System Prompt**: Enforces strict JSON Schema compliance, CommonMark conventions, LaTeX formatting rules, and asset referencing instructions.
2. **User Message Content**:
   - High-resolution page images as base64 data URLs or binary parts (ordered sequentially).
   - Verbatim native text extraction tagged per page: `<page_text page="N">...</page_text>`.
   - Asset inventory manifest: `<available_assets page="N">[{"path": "assets/...", "dimensions": "..."}]</available_assets>`.

#### 3.4.3 Structural Target Transformations
- **Tables**:
  - Simple rectangular tables $\to$ Standard GFM Markdown pipe tables (`| Col1 | Col2 |`).
  - Complex tables with merged cells (`colspan` or `rowspan`) $\to$ Clean, valid HTML `<table>` blocks.
- **Mathematics**:
  - Inline expressions $\to$ Single dollar delimiters: `$...$`.
  - Display / equation blocks $\to$ Double dollar delimiters: `$$...$$`.
  - Subscripts, superscripts, matrices, fractions converted to standard LaTeX notation.
- **Code Blocks**:
  - Extracted as fenced code blocks (```` ```<language> ````).
  - Programming language identifier inferred from syntax and text context (e.g., `python`, `cpp`, `json`).
- **Headings & Structure**:
  - Assigned levels 1 through 6 (`#` through `######`).
  - Bulleted and numbered lists preserved with proper nesting.
  - Blockquotes and admonitions mapped to blockquote notation (`> [!NOTE]`).
- **Visual Asset Placement**:
  - Figures and charts matching page images are mapped to entries in the asset manifest.
  - Emitted as `ImageBlock` referencing the exact disk asset path: `![Figure caption](assets/asset_p1_01.png)`.

#### 3.4.4 Resilience & Retry Protocol
- Wrapped with `tenacity.retry`:
  - Retries on HTTP 429 (Rate Limit), 500, 502, 503, 504, and network timeouts.
  - Exponential backoff with jitter: $t_{\text{wait}} = \min(60, 2^{\text{attempt}} + \text{uniform}(0, 1))$.
  - Maximum retry attempts: **3**.
- **Deterministic Raw Text Fallback**:
  - If an LLM call fails after 3 retries, or if JSON parsing/validation fails repeatedly, the chunk worker does not abort the entire document.
  - The worker switches to Fallback Mode: constructs a `DocumentChunkAST` from the raw deterministic text blocks of Stage 2.
  - Prepends an admonition block: `> [!WARNING] Page N rendered via deterministic raw-text fallback.`

---

### 3.5 Stage 4: Cross-Chunk AST Stitching (`stitch`)

Sequential chunk ASTs are combined into a unified `DocumentAST` through an AST stitching pipeline using **Smart Conservative Healing** (can be disabled via `--no-stitch`).

#### 3.5.1 Split Sentence & Paragraph Reconciliation
When a chunk ends on page $P_{\text{end}}$ and the next chunk begins on page $P_{\text{next}} = P_{\text{end}} + 1$:
1. Check the trailing block of chunk $K$ and the leading block of chunk $K+1$.
2. If both are `ParagraphBlock`, and the trailing text does not end with terminal punctuation (`.`, `!`, `?`, `:"`, `\n\n`), and the leading text begins with a lowercase letter or continuation character:
   - Merge the two blocks into a single continuous `ParagraphBlock`.
   - Reconnect hyphenated word wraps across pages (e.g. `transfor-` + `mation` $\to$ `transformation`).
   - Preserve page transition metadata within the block so page anchors remain exact.

#### 3.5.2 Broken Table & List Reconciliation
- If a table at the end of chunk $K$ has matching column counts with an un-headed table at the start of chunk $K+1$, the row elements are merged into the parent table.
- Ordered lists continuing with incremental numbers across chunk boundaries are merged into a single `ListBlock`.

#### 3.5.3 Running Header & Footer Suppression
1. Calculate the Levenshtein distance and token similarity of the first and last non-heading lines across pages.
2. If a repetitive string (e.g., `"Chapter 4: Microservices | 129"`, `"Confidential Draft"`) appears consistently at the top or bottom margin across $\ge 3$ pages, mark and suppress those blocks as running chrome.

#### 3.5.4 Heading Hierarchy Normalization
- Enforce heading hierarchy constraints: document must not jump from `#` directly to `###` without an intervening `##`.
- Adjust relative heading levels across chunks so chapter/section hierarchy remains monotonic.

---

### 3.6 Stage 5: Markdown Synthesis (`render_md`)

#### 3.6.1 Page Traceability & Navigation Markers
To guarantee reader navigation in large documents (200+ pages), the renderer injects page transition markers governed by `--page-markers`:
- `anchor` (default):
  ```markdown
  <a id="page-42"></a>
  <!-- [Page 42] -->
  ```
- `badge`:
  ```markdown
  <small class="pdf-page-indicator">📄 Page 42</small>
  ---
  ```
- `comment`:
  ```markdown
  <!-- [Page 42] -->
  ```
- `none`: Omits all page boundary markers.

Every heading block is also assigned a deterministic anchor slug (e.g., `<h2 id="neural-architectures">` or `# Neural Architectures {#neural-architectures}`).

#### 3.6.2 Functional Table of Contents (TOC) Generation
The synthesizer builds a top-level interactive Table of Contents when `--toc` is specified (or by default):
1. **Extraction Source 1 (Native Bookmarks)**: Extracted via PyMuPDF `doc.get_toc(simple=True)`. Contains `[level, title, page_number]`.
2. **Extraction Source 2 (AST Headings)**: Extracted from `HeadingBlock` instances in the stitched AST.
3. **Alignment & Linking**:
   - Matches bookmark titles to generated AST heading slugs using normalized string matching.
   - If a bookmark matches an AST heading, it links directly to that heading's slug:
     `- [1. Introduction](#1-introduction) *(p. 3)*`
   - If a bookmark has no matching heading on its target page, it falls back to linking the page anchor:
     `- [Appendix B](#page-85) *(p. 85)*`
4. **Printed TOC Transcription**: If the document contains printed TOC pages, static dotted leader lines (`Section 1 ........... 15`) are detected and converted to active markdown links.

---

## 4. Intermediate Data Models & Schemas

### 4.1 Document Block Types
Every block in the AST conforms to a discriminated union:
- `heading`: `level` (1..6), `content`, `id` (slug).
- `paragraph`: `content`.
- `list`: `ordered` (bool), `items` (list of strings or nested lists), `start` (optional int).
- `table`: `header` (list of strings), `rows` (list of list of strings), `raw_html` (optional string for complex colspans), `caption` (optional).
- `code`: `content`, `language` (e.g. `python`, `typescript`, `sh`).
- `math`: `content` (LaTeX), `math_mode` (`inline` | `block`).
- `image`: `asset_path`, `alt_text`, `caption`, `width`, `height`.
- `callout`: `callout_type` (`NOTE`, `TIP`, `WARNING`, `CAUTION`, `IMPORTANT`), `content`.
- `thematic_break`: horizontal dividing line.

### 4.2 Intermediate JSON Schema (`material.json` / `chunk_<id>.json`)
Each chunk produces an intermediate JSON checkpoint matching the formal schema:
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "DocumentChunkAST",
  "type": "object",
  "properties": {
    "chunk_index": { "type": "integer", "minimum": 0 },
    "page_range": {
      "type": "array",
      "items": { "type": "integer", "minimum": 1 },
      "minItems": 2,
      "maxItems": 2
    },
    "bypass_used": { "type": "boolean", "default": false },
    "fallback_used": { "type": "boolean", "default": false },
    "blocks": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["type", "page", "content"],
        "properties": {
          "type": {
            "type": "string",
            "enum": ["heading", "paragraph", "list", "table", "code", "math", "image", "callout", "thematic_break"]
          },
          "page": { "type": "integer", "minimum": 1 },
          "id": { "type": "string" },
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

## 5. Concurrency, Memory & Hardware Bounds

| Pipeline Stage | Nature | Max Concurrency | Resource Bound / Management Policy |
| :--- | :--- | :--- | :--- |
| **Stage 1: Partitioning** | CPU / In-memory | 1 (synchronous) | Reads PDF metadata/xref only; minimal memory (< 10MB) |
| **Stage 2: Deterministic Prep** | CPU & Disk I/O | `min(os.cpu_count(), 4)` | Sliding-window chunk rendering. Bitmaps flushed immediately to disk or dropped |
| **Stage 2.5: Auto-Bypass Classifier** | CPU | `min(os.cpu_count(), 4)` | Analyzes vector & text structures in milliseconds per page |
| **Stage 3: Multimodal LLM Vision** | Network I/O & Rate-limit | **Strictly 3** | Managed via `asyncio.Semaphore(3)`. Memory bounded to active payload buffers |
| **Stage 4: AST Stitching** | CPU | 1 (synchronous) | In-memory AST list concatenation and boundary resolution |
| **Stage 5: Markdown Synthesis** | CPU / Disk write | 1 (synchronous) | Single-pass CommonMark generator writing to target output file |

### 5.1 Sliding Window Memory Safety (1,000+ Pages)
- For large documents, the pipeline maintains a window size of at most:
  $$W_{\text{chunks}} = \text{LLM\_CONCURRENCY} \times 2 = 6 \text{ chunks}$$
- Chunks are prepared on demand. Rendered images for chunk $K$ are deleted from the working memory/cache as soon as chunk $K$ finishes Stage 3 parsing, keeping peak RAM usage below 500 MB regardless of page count.

---

## 6. State, Caching & Idempotency

### 6.1 Cache Directory Structure
Cache is keyed by the SHA-256 hash of the input PDF file bytes:
```
.cache/
└── 7f83b1657ff1fc53b92dc18148a1d65dfc2d4b1fa3d677284addd200126d9069/
    ├── manifest.json
    ├── chunks/
    │   ├── chunk_0000.json
    │   ├── chunk_0001.json
    │   └── chunk_0002.json
    ├── raw_text/
    │   ├── page_0001.txt
    │   └── page_0002.txt
    └── rendered/
        ├── page_0001.png
        └── page_0002.png
```

### 6.2 Cache Invalidation Rules
The cache is invalidated and rebuilt if:
1. The PDF SHA-256 checksum does not match `manifest.json`.
2. The user passes `--no-cache` / `--force`.
3. The LLM model name or prompt version in `manifest.json` differs from current execution parameters.

### 6.3 Resumption Semantics
Upon launching a conversion:
1. `docforge` checks for `.cache/<pdf_hash>/manifest.json`.
2. Any chunk whose `chunk_{i}.json` exists and validates against the schema is loaded directly from disk.
3. The progress bar marks those chunks as already completed and only executes remaining chunks.
4. If interrupted via `SIGINT` (Ctrl+C), all completed chunks remain intact on disk.

---

## 7. CLI & User Experience Specification

### 7.1 Command Line Interface
```bash
# Basic conversion (emits Markdown by default)
uv run docforge document.pdf -o output.md

# Flexible output format: emit both Markdown and unified JSON AST
uv run docforge document.pdf --format both -o output.md

# Emit single unified JSON AST only (skipping MD rendering)
uv run docforge document.pdf --format json -o output.json

# Targeted page extraction with badge markers and custom chunk size
uv run docforge document.pdf \
    --pages "1-40" \
    --page-markers badge \
    --chunk-size 4 \
    -o excerpt.md

# High-fidelity conversion with opt-in auto-bypass
uv run docforge document.pdf \
    -o ./dist/paper.md \
    --format both \
    --toc \
    --page-markers anchor \
    --assets-dir ./dist/assets \
    --model gpt-4o \
    --dpi 200 \
    --auto-bypass
```

### 7.2 CLI Options & Flags
| Option / Flag | Short | Default | Description |
| :--- | :--- | :--- | :--- |
| `INPUT_PATH` | (arg) | *Required* | Path to input PDF file |
| `--output` | `-o` | `<basename>.md` | Path to destination output file |
| `--format` | `-f` | `md` | Output format: `md`, `json`, `both` |
| `--pages` | `-p` | `all` | Page selection range (e.g. `"1-20, 55"`, `"[0:10]"`) |
| `--page-markers` | `-m` | `anchor` | Page delimiter format: `anchor`, `badge`, `comment`, `none` |
| `--toc / --no-toc` | | `True` | Generate interactive Table of Contents at top |
| `--assets-dir` | `-a` | `./assets` | Destination folder for extracted image assets |
| `--chunk-size` | `-c` | `4` | Maximum pages per processing chunk (1..16, default: 4) |
| `--adaptive-chunking / --no-adaptive` | | `True` | Dynamically downscale chunk size on dense pages |
| `--no-stitch` | | `False` | Disable heuristic paragraph/table cross-chunk stitching |
| `--llm-concurrency` | | `3` | Maximum concurrent LLM requests (fixed semaphore) |
| `--model` | | `gpt-4o` | Multimodal model name (supports OpenAI, Anthropic, Gemini) |
| `--dpi` | | `150` | Rendering resolution for page bitmaps |
| `--auto-bypass` | | `False` | Opt-in heuristic fast-path bypass for simple prose pages |
| `--cache-dir` | | `.cache` | Base directory for caching intermediate results |
| `--no-cache` | | `False` | Ignore and overwrite existing cache |
| `--verbose` | `-v` | `False` | Enable debug logging |

### 7.3 Visual Terminal Dashboard (Rich)
- **Live Progress Panels**:
  - `[1/4] Partitioning & Local Extraction` (Pages processed / total)
  - `[2/4] Multimodal Parsing` (Chunks processed / total, active worker tags)
  - `[3/4] Cross-Chunk AST Stitching` (Healing paragraphs & tables)
  - `[4/4] Markdown & TOC Synthesis` (Writing output and assets)
- **Signal Handling (`SIGINT`)**: Intercepts Ctrl+C, signals workers to finish current chunk or cleanly exit, saves checkpoint to `.cache`, and prints resume instructions.

---

## 8. Failure Modes & Resilience Matrix

| Failure Mode | Trigger / Context | Recovery & Fallback Strategy |
| :--- | :--- | :--- |
| **Corrupted PDF / Syntax Error** | PDF header invalid or xref broken | PyMuPDF repair attempt (`doc.save(garbage=3)`). If unrecoverable, abort with `CorruptPdfException` and exit code 2. |
| **Password Protected PDF** | Document is encrypted with user/owner password | Prompt user for `--password` or abort with `EncryptedPdfException` (exit code 3). |
| **Image Extraction Anomaly** | Corrupt image stream or unsupported mask | Log warning, render image area from page pixmap crop as fallback PNG. |
| **LLM Rate Limit (HTTP 429)** | Exceeded provider RPM / TPM | Exponential backoff retry with jitter up to 3 attempts. Rich dashboard shows `[Worker N: Paused (429 Backoff)]`. |
| **LLM Context Length Exceeded** | Chunk text/images exceed token limit | Dynamically split chunk in half (e.g., 8 pages $\to$ two 4-page chunks) and retry each sub-chunk. |
| **LLM Invalid JSON Output** | Model outputs markdown chatter or broken JSON | Run JSON repair parser (`json_repair`). If still invalid, retry with strict json-mode prompt. After 3 retries $\to$ Fallback Mode. |
| **LLM Outage / Network Offline** | Provider 5xx error or connection timeout | Retry 3 times. Fall back to Deterministic Raw-Text AST generation with warning admonition. Conversion completes without crashing. |
| **User Interruption (`SIGINT`)** | User hits Ctrl+C | Workers stop accepting new chunks. In-flight completed chunks flushed to disk. Manifest marked partial. Exit code 130. |

---

## 9. Verification & Acceptance Criteria
1. **Fidelity**: Complex mathematical formulas in PDF are transcribed into syntactically valid LaTeX enclosed in `$...$` or `$$...$$`.
2. **Tabular Precision**: Tables with multi-line cells and borders are rendered cleanly as GFM tables or valid HTML tables without truncated columns.
3. **No External Binaries**: Execution passes cleanly in a minimal Alpine/Debian container lacking `tesseract`, `poppler`, or `pdftotext`.
4. **Memory Constraint**: Resident Memory Size (RSS) during 1,000-page conversion remains $\le 512\text{ MB}$.
5. **Idempotent Resume**: Killing a 100-page job at page 50 and restarting it resumes immediately from page 51 without reprocessing pages 1–50.
