"""CLI Entry Point & Rich Terminal Dashboard.

Conforms to specs/contracts/cli.contract and specs/features/cli_and_error_handling.feature.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import signal
import sys
from typing import Optional
from dotenv import dotenv_values, load_dotenv
import typer
from rich.console import Console

load_dotenv()
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeRemainingColumn

from docforge.config import load_user_settings, resolve_value_precedence

from docforge.exceptions import (
    CorruptedPdfException,
    InvalidPageRangeException,
    LlmInferenceException,
    MissingApiKeyException,
    Pdf2MdException,
)
from docforge.logging import get_logger, setup_logging
from docforge.models.cli_options import (
    CliConfig,
    ConcurrencyConfig,
    LlmConfig,
    LogLevel,
    OutputFormat,
    PageMarkerMode,
    RangeSpec,
    parse_output_formats,
)
from docforge.pipeline import PipelineRunner
from docforge.stages.extract import open_pdf_document
from docforge.stages.partition import parse_page_selection

app = typer.Typer(
    name="docforge",
    help="High-fidelity Multimodal PDF-to-Markdown Converter",
    add_completion=False,
)
console = Console()
err_console = Console(stderr=True)


@app.command()
def convert(
    input_pdf: str = typer.Argument(..., help="Path to input PDF file"),
    output: Optional[str] = typer.Option(None, "-o", "--output", help="Destination output file path"),
    format: str = typer.Option(
        "md",
        "-f",
        "--format",
        help="Output format(s): md, json, html, typ, both, all (or comma-separated e.g. md,json)",
    ),
    standalone: bool = typer.Option(
        True,
        "--standalone/--no-standalone",
        help="Emit standalone document with styles/scripts (for HTML/Typst)",
    ),
    embed_assets: bool = typer.Option(
        False,
        "--embed-assets",
        "--single-file",
        help="Inline assets/images into output document as base64 data URLs",
    ),
    split_by: Optional[str] = typer.Option(
        None,
        "--split-by",
        help="Partition output files by boundary: page, chapter, section, chapter/page",
    ),
    split_pattern: Optional[str] = typer.Option(
        None,
        "--split-pattern",
        help="Custom output filename template for partitioned files",
    ),
    pages: Optional[str] = typer.Option(None, "-p", "--pages", help="Page range specification e.g. '1-10' or '[0:10]'"),
    page_markers: str = typer.Option("anchor", "-m", "--page-markers", help="Page marker style: anchor, badge, comment, none"),
    toc: bool = typer.Option(True, "--toc/--no-toc", help="Generate Table of Contents"),
    assets_dir: str = typer.Option("./assets", "-a", "--assets-dir", help="Directory for extracted assets"),
    chunk_size: int = typer.Option(4, "-c", "--chunk-size", help="Maximum pages per chunk (1-16)"),
    adaptive_chunking: bool = typer.Option(True, "--adaptive-chunking/--no-adaptive", help="Dynamically adjust chunk size"),
    no_stitch: bool = typer.Option(False, "--no-stitch", help="Disable cross-chunk boundary stitching"),
    llm_concurrency: int = typer.Option(3, "--llm-concurrency", help="Concurrent LLM requests bound (default 3)"),
    model: Optional[str] = typer.Option(None, "--model", help="Multimodal LLM model slug (defaults to PDF2MD_MODEL or gemma4:cloud)"),
    max_tokens: Optional[int] = typer.Option(None, "--max-tokens", help="Maximum completion tokens for LLM generation (default 8192)"),
    context_window: Optional[int] = typer.Option(
        None,
        "--context-window",
        "--num-ctx",
        help="Context window token size for Ollama/local backends (num_ctx)",
    ),
    timeout: Optional[float] = typer.Option(
        None,
        "--timeout",
        help="LLM request timeout in seconds (default 120.0)",
    ),
    instructions: Optional[str] = typer.Option(
        None,
        "--instructions",
        "--prompt",
        help="Custom transcription guidelines/prompt appended to vision LLM system prompt",
    ),
    prompt_file: Optional[str] = typer.Option(
        None,
        "--prompt-file",
        help="Path to a text/markdown file containing custom transcription guidelines",
    ),
    dpi: int = typer.Option(150, "--dpi", help="Page rendering resolution DPI (default 150)"),
    cache_dir: str = typer.Option(".cache", "--cache-dir", help="Directory for cache checkpoints"),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass and ignore disk cache"),
    auto_bypass: bool = typer.Option(
        False,
        "--auto-bypass",
        help="[Deprecated] Opt-in heuristic fast-path bypass (deprecated; all pages use multimodal LLM parsing)",
    ),
    verbose: int = typer.Option(0, "-v", "--verbose", count=True, help="Enable verbose logging (-v for INFO, -vv for DEBUG)"),
    debug: bool = typer.Option(False, "--debug", help="Enable debug logging (alias for -vv)"),
    log_level: Optional[str] = typer.Option(None, "--log-level", help="Explicit logging level: DEBUG, INFO, WARN, ERROR"),
) -> None:
    """Transform PDF documents into semantic Markdown using native extraction and vision LLMs."""
    # Initialize structured logging
    setup_logging(log_level=log_level, verbose=verbose, debug=debug, console=console)

    if auto_bypass:
        err_console.print(
            "[yellow]Warning: --auto-bypass is deprecated and will be removed in a future release. "
            "All pages are parsed using multimodal LLM for consistent fidelity.[/yellow]"
        )

    if not os.path.exists(input_pdf):
        err_console.print(f"[red]Error: File not found: {input_pdf}[/red]")
        sys.exit(2)

    # Early validation of PDF and page range before expensive hashing or progress bars
    try:
        early_doc = open_pdf_document(input_pdf)
        total_source_pages = len(early_doc)
        early_doc.close()
    except CorruptedPdfException as e:
        err_console.print(f"[red]{e}[/red]")
        sys.exit(2)
    except Exception as e:
        err_console.print(f"[red]Error: Corrupted or unreadable PDF: {e}[/red]")
        sys.exit(2)

    if pages:
        try:
            parse_page_selection(pages, total_source_pages)
        except InvalidPageRangeException as e:
            err_console.print(f"[red]Error: Invalid page range: {e}[/red]")
            sys.exit(4)

    # Determine default output path
    stem = os.path.splitext(os.path.basename(input_pdf))[0]
    parsed_formats = parse_output_formats(format)
    try:
        out_format = OutputFormat(format.lower())
    except ValueError:
        out_format = OutputFormat.MD

    if output is None:
        if len(parsed_formats) == 1 and parsed_formats[0] == "json":
            dest_output = f"{stem}.json"
        elif len(parsed_formats) == 1 and parsed_formats[0] == "html":
            dest_output = f"{stem}.html"
        elif len(parsed_formats) == 1 and parsed_formats[0] in ("typ", "typst"):
            dest_output = f"{stem}.typ"
        else:
            dest_output = f"{stem}.md"
    else:
        dest_output = output

    range_spec = RangeSpec(raw_input=pages, normalized_pages=[]) if pages else None

    # Load 5-tier configuration sources
    user_settings = load_user_settings()
    local_dotenv = dotenv_values(".env") if os.path.exists(".env") else {}

    selected_model = resolve_value_precedence(
        cli_value=model,
        env_keys=["DOCFORGE_MODEL", "PDF2MD_MODEL"],
        settings_value=user_settings.llm.model_name,
        default_value="gemma4:cloud",
        dotenv_dict=local_dotenv,
    )
    api_base = resolve_value_precedence(
        cli_value=None,
        env_keys=["DOCFORGE_API_BASE", "PDF2MD_API_BASE", "OLLAMA_API_BASE"],
        settings_value=user_settings.llm.api_base,
        default_value=None,
        dotenv_dict=local_dotenv,
    )
    api_key = resolve_value_precedence(
        cli_value=None,
        env_keys=["DOCFORGE_API_KEY", "PDF2MD_API_KEY", "OLLAMA_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"],
        settings_value=user_settings.llm.api_key,
        default_value=None,
        dotenv_dict=local_dotenv,
    )
    max_tokens_val = resolve_value_precedence(
        cli_value=max_tokens,
        env_keys=["DOCFORGE_MAX_TOKENS", "PDF2MD_MAX_TOKENS"],
        settings_value=user_settings.llm.max_tokens,
        default_value=8192,
        dotenv_dict=local_dotenv,
    )
    resolved_max_tokens = int(max_tokens_val)

    context_window_val = resolve_value_precedence(
        cli_value=context_window,
        env_keys=["DOCFORGE_CONTEXT_WINDOW", "PDF2MD_CONTEXT_WINDOW", "DOCFORGE_NUM_CTX", "PDF2MD_NUM_CTX"],
        settings_value=user_settings.llm.context_window,
        default_value=None,
        dotenv_dict=local_dotenv,
    )
    resolved_context_window = int(context_window_val) if context_window_val is not None else None

    timeout_val = resolve_value_precedence(
        cli_value=timeout,
        env_keys=["DOCFORGE_TIMEOUT", "PDF2MD_TIMEOUT"],
        settings_value=user_settings.llm.timeout_seconds,
        default_value=None,
        dotenv_dict=local_dotenv,
    )
    if timeout_val is not None:
        resolved_timeout = float(timeout_val)
    else:
        resolved_timeout = max(120.0, (resolved_max_tokens / 8192.0) * 120.0)

    resolved_instructions = resolve_value_precedence(
        cli_value=instructions,
        env_keys=["DOCFORGE_INSTRUCTIONS", "PDF2MD_INSTRUCTIONS"],
        settings_value=user_settings.defaults.instructions,
        default_value=None,
        dotenv_dict=local_dotenv,
    )

    resolved_prompt_file = resolve_value_precedence(
        cli_value=prompt_file,
        env_keys=["DOCFORGE_PROMPT_FILE", "PDF2MD_PROMPT_FILE"],
        settings_value=user_settings.defaults.prompt_file,
        default_value=None,
        dotenv_dict=local_dotenv,
    )

    if resolved_prompt_file:
        prompt_path = Path(resolved_prompt_file)
        if not prompt_path.is_file():
            # If specified via explicit CLI or env, fail; if from settings.json and missing, only fail if not optional
            if prompt_file or any(k in os.environ for k in ["DOCFORGE_PROMPT_FILE", "PDF2MD_PROMPT_FILE"]):
                err_console.print(f"[red]Error: Prompt file not found: {resolved_prompt_file}[/red]")
                sys.exit(2)
        else:
            try:
                file_content = prompt_path.read_text(encoding="utf-8").strip()
                if file_content:
                    if resolved_instructions:
                        resolved_instructions = f"{resolved_instructions}\n\n{file_content}"
                    else:
                        resolved_instructions = file_content
            except Exception as e:
                err_console.print(f"[red]Error reading prompt file {resolved_prompt_file}: {e}[/red]")
                sys.exit(2)

    resolved_cache_dir = os.path.expanduser(
        resolve_value_precedence(
            cli_value=None if cache_dir == ".cache" else cache_dir,
            env_keys=["DOCFORGE_CACHE_DIR", "PDF2MD_CACHE_DIR"],
            settings_value=user_settings.defaults.cache_dir,
            default_value=cache_dir,
            dotenv_dict=local_dotenv,
        )
    )
    resolved_dpi = int(
        resolve_value_precedence(
            cli_value=None if dpi == 150 else dpi,
            env_keys=["DOCFORGE_DPI", "PDF2MD_DPI"],
            settings_value=user_settings.defaults.dpi,
            default_value=dpi,
            dotenv_dict=local_dotenv,
        )
    )
    resolved_page_markers = resolve_value_precedence(
        cli_value=None if page_markers == "anchor" else page_markers,
        env_keys=["DOCFORGE_PAGE_MARKERS", "PDF2MD_PAGE_MARKERS"],
        settings_value=user_settings.defaults.page_markers,
        default_value=page_markers,
        dotenv_dict=local_dotenv,
    )
    resolved_chunk_size = int(
        resolve_value_precedence(
            cli_value=None if chunk_size == 4 else chunk_size,
            env_keys=["DOCFORGE_CHUNK_SIZE", "PDF2MD_CHUNK_SIZE"],
            settings_value=user_settings.defaults.chunk_size,
            default_value=chunk_size,
            dotenv_dict=local_dotenv,
        )
    )

    parsed_log_level = None
    if log_level:
        try:
            parsed_log_level = LogLevel(log_level.upper())
        except ValueError:
            pass

    # Construct runtime config
    config = CliConfig(
        input_pdf_path=input_pdf,
        output_path=dest_output,
        output_format=out_format,
        output_formats=parsed_formats,
        standalone=standalone,
        embed_assets=embed_assets,
        split_by=split_by,
        split_pattern=split_pattern,
        assets_dir_path=assets_dir,
        cache_dir_path=resolved_cache_dir,
        pages_spec=range_spec,
        page_marker_mode=PageMarkerMode(resolved_page_markers.lower()),
        generate_toc=toc,
        chunk_size=resolved_chunk_size,
        adaptive_chunking=adaptive_chunking,
        no_stitch=no_stitch,
        dpi=resolved_dpi,
        auto_bypass=auto_bypass,
        no_cache=no_cache,
        verbose=bool(verbose or debug),
        log_level=parsed_log_level,
        concurrency=ConcurrencyConfig(llm_concurrency=llm_concurrency),
        instructions=resolved_instructions,
        prompt_file=resolved_prompt_file,
        llm=LlmConfig(
            model_name=selected_model,
            api_base=api_base,
            api_key=api_key,
            max_tokens=resolved_max_tokens,
            context_window=resolved_context_window,
            request_timeout_seconds=resolved_timeout,
            instructions=resolved_instructions,
            prompt_file=resolved_prompt_file,
        ),
    )

    runner = PipelineRunner(config)

    # Signal handling for Ctrl+C (SIGINT)
    def handle_sigint(signum, frame):
        runner.handle_sigint()
        err_console.print("\n[yellow]SIGINT received. Shutting down gracefully...[/yellow]")
        err_console.print(f"[dim]Conversion interrupted. Intermediate chunks saved to {config.cache_dir_path}/. Run the command again to resume.[/dim]")
        sys.exit(130)

    try:
        signal.signal(signal.SIGINT, handle_sigint)
    except (ValueError, AttributeError):
        pass

    worker_slots = {i: "Idle" for i in range(1, llm_concurrency + 1)}

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    )

    stage1_task = progress.add_task("[1/4] Partitioning & Local Extraction", total=100)
    stage2_task = progress.add_task("[2/4] Multimodal Parsing", total=100)
    stage3_task = progress.add_task("[3/4] Cross-Chunk Stitching", total=100)
    stage4_task = progress.add_task("[4/4] Markdown & TOC Synthesis", total=100)

    def progress_cb(stage: str, current: int, total: int):
        pct = (current / max(1, total)) * 100.0
        if stage == "partition":
            progress.update(stage1_task, completed=pct)
        elif stage == "chunks":
            progress.update(stage2_task, completed=pct)

    def worker_cb(chunk_idx: int, status: str):
        slot = (chunk_idx % llm_concurrency) + 1
        worker_slots[slot] = f"Chunk {chunk_idx}: {status}"
        slots_str = " ".join([f"[Worker {s}: {st}]" for s, st in worker_slots.items()])
        progress.update(stage2_task, description=f"[2/4] Multimodal Parsing {slots_str}")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    progress.start()
    try:
        doc_ast, out_md, out_json = loop.run_until_complete(
            runner.run(progress_callback=progress_cb, worker_callback=worker_cb)
        )
        progress.update(stage1_task, completed=100)
        progress.update(stage2_task, completed=100)
        progress.update(stage3_task, completed=100)
        progress.update(stage4_task, completed=100)
        progress.stop()

        console.print(f"\n[green]✓ Conversion completed successfully![/green]")
        if out_md:
            console.print(f"  Markdown: [bold]{out_md}[/bold]")
        if out_json:
            console.print(f"  AST JSON: [bold]{out_json}[/bold]")

        loop.close()
        sys.exit(0)

    except MissingApiKeyException as e:
        progress.stop()
        err_console.print(f"[red]{e}[/red]")
        sys.exit(5)
    except LlmInferenceException as e:
        progress.stop()
        err_console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)
    except CorruptedPdfException as e:
        progress.stop()
        err_console.print(f"[red]{e}[/red]")
        sys.exit(2)
    except InvalidPageRangeException as e:
        progress.stop()
        err_console.print(f"[red]Error: Invalid page range: {e}[/red]")
        sys.exit(4)
    except KeyboardInterrupt:
        progress.stop()
        err_console.print(f"\n[yellow]Conversion interrupted. Intermediate chunks saved. Run the command again to resume.[/yellow]")
        sys.exit(130)
    except Exception as e:
        progress.stop()
        if verbose:
            err_console.print_exception()
        else:
            err_console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


def main():
    """CLI script entrypoint."""
    app()


if __name__ == "__main__":
    main()
