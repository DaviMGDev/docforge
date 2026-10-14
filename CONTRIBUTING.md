# Contributing to docforge

Thank you for your interest in contributing to `docforge`!

`docforge` is a high-fidelity multimodal document ingestion, transcription, and multi-format export engine built with Python >= 3.11, [uv](https://docs.astral.sh/uv/), PyMuPDF, and LiteLLM.

---

## Architectural Principles

Before contributing code, please ensure your design adheres to our core constraints:

1. **Zero External System Binaries**: The pipeline relies exclusively on self-contained Python wheels (`pymupdf`, `pillow`). No external OS packages like `tesseract-ocr`, `poppler-utils`, or `ghostscript` may be introduced.
2. **Hardware-Bounded Concurrency**: CPU-bound tasks (rendering, text extraction, hashing) scale with local CPU cores (`min(os.cpu_count(), 4)`). Outbound network calls to vision LLMs are strictly bounded via `asyncio.Semaphore(3)`.
3. **Memory Safety & Sliding Windows**: Never buffer entire large PDFs into memory. Page bitmaps are rendered per chunk and pruned immediately after LLM processing.
4. **Deterministic Checkpointing**: Cache intermediate AST representations by PDF SHA-256 and content keys to enable resumption without duplicate LLM spend.
5. **Lint-Clean CommonMark / GFM**: Output Markdown must adhere strictly to CommonMark/GFM specifications.

---

## Development Setup

We use `uv` for fast, reproducible dependency management.

```bash
# 1. Clone repository
git clone https://github.com/DaviMGDev/pdf2md.git
cd pdf2md

# 2. Sync virtual environment and dependencies
uv sync

# 3. Verify test suite
uv run pytest
```

---

## Project Structure

```
src/docforge/
├── cache/            # SHA-256 chunk checkpointing and cache manager
├── config.py         # Persistent XDG user configuration manager
├── models/           # Typed Pydantic v2 domain schemas (AST, pipeline, options, TOC)
├── stages/
│   ├── partition.py  # Stage 1: Page range parser and adaptive chunking
│   ├── extract.py    # Stage 2: Deterministic PyMuPDF extraction & asset deduplication
│   ├── classifier.py # Stage 2.5: Heuristic bypass classifier
│   ├── llm_worker.py # Stage 3: Bounded multimodal LLM parsing & retry loops
│   ├── stitcher.py   # Stage 4: Cross-chunk AST stitching & boundary healing
│   ├── synthesis.py  # Stage 5: CommonMark / GFM serializer
│   └── toc.py        # Stage 5: Dual-source TOC generator & deep linker
├── cli.py            # Typer CLI application & Rich progress dashboard
├── pipeline.py       # Async pipeline orchestrator
└── exceptions.py     # Domain exceptions
```

---

## Commit Guidelines (Conventional Commits)

We enforce strict **Conventional Commits** with atomic grouping:

### Format
```
<type>(<scope>): <description>

[optional body]

[optional footer(s)]
```

### Types
- `feat`: A new feature or capability
- `fix`: A bug fix
- `docs`: Documentation-only changes
- `style`: Formatting or code style changes (no logic changes)
- `refactor`: Code changes that neither fix a bug nor add a feature
- `perf`: Performance improvements
- `test`: Adding or correcting tests
- `build`: Build system, dependencies, or package metadata
- `ci`: CI configuration and workflow scripts
- `chore`: Tooling or repository maintenance

### Atomic Commit Rules
- **One concern per commit**: Keep commits small and self-contained.
- **Subject line**: 72 characters or fewer, imperative mood, lowercase, no trailing period.
- **Lines and files**: Avoid mega-commits. Split large modifications by scope or concern.

---

## Running Tests

All tests are located in `tests/` and run through `pytest`:

```bash
# Run all tests
uv run pytest

# Run specific test module
uv run pytest tests/test_cli.py

# Run with verbose output
uv run pytest -v
```

Before submitting a pull request, ensure **100% of tests pass**.

---

## Submitting Pull Requests & Issues

1. **Issues**: Use the provided issue templates for [Bug Reports](.github/ISSUE_TEMPLATE/bug_report.md) or [Feature Requests](.github/ISSUE_TEMPLATE/feature_request.md).
2. **Branches**: Branch from `main` using descriptive names (e.g., `feat/custom-prompts` or `fix/progress-calculation`).
3. **Pull Requests**: Reference related issues, describe the rationale, and ensure test coverage for all new functionality.
