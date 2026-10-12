"""CLI Entry Point & Rich Terminal Dashboard.

Conforms to specs/contracts/cli.contract and specs/features/cli_and_error_handling.feature.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from typing import Optional
from dotenv import load_dotenv
import typer
from rich.console import Console

load_dotenv()
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeRemainingColumn

from pdf2md.exceptions import (
    CorruptedPdfException,
    InvalidPageRangeException,
    LlmInferenceException,
    MissingApiKeyException,
    Pdf2MdException,
)
from pdf2md.logging import get_logger, setup_logging
from pdf2md.models.cli_options import (
    CliConfig,
    ConcurrencyConfig,
    LlmConfig,
    LogLevel,
    OutputFormat,
    PageMarkerMode,
    RangeSpec,
)
from pdf2md.pipeline import PipelineRunner

app = typer.Typer(
    name="pdf2md",
    help="High-fidelity Multimodal PDF-to-Markdown Converter",
    add_completion=False,
)
console = Console()
err_console = Console(stderr=True)


@app.command()
def convert(
    input_pdf: str = typer.Argument(..., help="Path to input PDF file"),
    output: Optional[str] = typer.Option(None, "-o", "--output", help="Destination output file path"),
    format: str = typer.Option("md", "-f", "--format", help="Output format: md, json, both"),
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

    # Determine default output path
    stem = os.path.splitext(os.path.basename(input_pdf))[0]
    out_format = OutputFormat(format.lower())

    if output is None:
        if out_format == OutputFormat.JSON:
            dest_output = f"{stem}.json"
        else:
            dest_output = f"{stem}.md"
    else:
        dest_output = output

    range_spec = RangeSpec(raw_input=pages, normalized_pages=[]) if pages else None

    selected_model = model or os.environ.get("PDF2MD_MODEL", "gemma4:cloud")
    api_base = os.environ.get("PDF2MD_API_BASE") or os.environ.get("OLLAMA_API_BASE")
    api_key = os.environ.get("PDF2MD_API_KEY") or os.environ.get("OLLAMA_API_KEY")
    resolved_max_tokens = max_tokens if max_tokens is not None else int(os.environ.get("PDF2MD_MAX_TOKENS", "8192"))

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
        assets_dir_path=assets_dir,
        cache_dir_path=os.environ.get("PDF2MD_CACHE_DIR", cache_dir),
        pages_spec=range_spec,
        page_marker_mode=PageMarkerMode(page_markers.lower()),
        generate_toc=toc,
        chunk_size=chunk_size,
        adaptive_chunking=adaptive_chunking,
        no_stitch=no_stitch,
        dpi=int(os.environ.get("PDF2MD_DPI", dpi)),
        auto_bypass=auto_bypass,
        no_cache=no_cache,
        verbose=bool(verbose or debug),
        log_level=parsed_log_level,
        concurrency=ConcurrencyConfig(llm_concurrency=llm_concurrency),
        llm=LlmConfig(
            model_name=selected_model,
            api_base=api_base,
            api_key=api_key,
            max_tokens=resolved_max_tokens,
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

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    ) as progress:
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

        try:
            doc_ast, out_md, out_json = loop.run_until_complete(
                runner.run(progress_callback=progress_cb, worker_callback=worker_cb)
            )
            progress.update(stage1_task, completed=100)
            progress.update(stage2_task, completed=100)
            progress.update(stage3_task, completed=100)
            progress.update(stage4_task, completed=100)

            console.print(f"\n[green]✓ Conversion completed successfully![/green]")
            if out_md:
                console.print(f"  Markdown: [bold]{out_md}[/bold]")
            if out_json:
                console.print(f"  AST JSON: [bold]{out_json}[/bold]")

            loop.close()
            sys.exit(0)

        except MissingApiKeyException as e:
            err_console.print(f"[red]{e}[/red]")
            sys.exit(5)
        except LlmInferenceException as e:
            err_console.print(f"[red]Error: {e}[/red]")
            sys.exit(1)
        except CorruptedPdfException as e:
            err_console.print(f"[red]{e}[/red]")
            sys.exit(2)
        except InvalidPageRangeException as e:
            err_console.print(f"[red]Error: Invalid page range: {e}[/red]")
            sys.exit(4)
        except KeyboardInterrupt:
            err_console.print(f"\n[yellow]Conversion interrupted. Intermediate chunks saved. Run the command again to resume.[/yellow]")
            sys.exit(130)
        except Exception as e:
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
