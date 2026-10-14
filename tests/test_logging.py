import logging
import os
import fitz
import pytest
from rich.console import Console
from rich.logging import RichHandler
from typer.testing import CliRunner

from docforge.cli import app
from docforge.logging import get_logger, resolve_log_level, setup_logging
from docforge.models.cli_options import LogLevel


def test_resolve_log_level_from_enum():
    assert resolve_log_level(log_level=LogLevel.DEBUG) == logging.DEBUG
    assert resolve_log_level(log_level=LogLevel.INFO) == logging.INFO
    assert resolve_log_level(log_level=LogLevel.WARN) == logging.WARNING
    assert resolve_log_level(log_level=LogLevel.ERROR) == logging.ERROR


def test_resolve_log_level_from_string():
    assert resolve_log_level(log_level="DEBUG") == logging.DEBUG
    assert resolve_log_level(log_level="info") == logging.INFO
    assert resolve_log_level(log_level="WARNING") == logging.WARNING
    assert resolve_log_level(log_level="warn") == logging.WARNING
    assert resolve_log_level(log_level="ERROR") == logging.ERROR


def test_resolve_log_level_from_verbose_flags():
    # Default (no flags)
    assert resolve_log_level() == logging.WARNING

    # -v (INFO)
    assert resolve_log_level(verbose=1) == logging.INFO

    # -vv (DEBUG)
    assert resolve_log_level(verbose=2) == logging.DEBUG

    # --debug
    assert resolve_log_level(debug=True) == logging.DEBUG


def test_setup_logging_configures_rich_handler():
    custom_console = Console(record=True)
    logger = setup_logging(log_level="DEBUG", console=custom_console)

    assert logger.level == logging.DEBUG
    assert any(isinstance(h, RichHandler) for h in logger.handlers)

    child_logger = get_logger("child")
    assert child_logger.name in ("docforge.child", "docforge.child")


def test_cli_verbose_logging_output(tmp_path):
    runner = CliRunner()
    pdf_path = str(tmp_path / "test.pdf")
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    p.insert_text((50, 50), "Logging Test Content", fontsize=14)
    doc.save(pdf_path)
    doc.close()

    out_md = str(tmp_path / "test_out.md")
    result = runner.invoke(app, [pdf_path, "-v", "-o", out_md])
    assert result.exit_code == 0
    assert os.path.exists(out_md)
    # Check that Stage 1 and Stage 2 telemetry appeared
    output = result.output + (result.stderr or "")
    assert "[Stage 1]" in output
    assert "[Stage 2]" in output


def test_cli_debug_logging_output(tmp_path):
    runner = CliRunner()
    pdf_path = str(tmp_path / "test.pdf")
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    p.insert_text((50, 50), "Debug Test Content", fontsize=14)
    doc.save(pdf_path)
    doc.close()

    out_md = str(tmp_path / "debug_out.md")
    result = runner.invoke(app, [pdf_path, "--debug", "-o", out_md])
    assert result.exit_code == 0
    assert os.path.exists(out_md)
    output = result.output + (result.stderr or "")
    assert "[Stage 1]" in output
    assert "boundary: pages" in output


def test_cli_explicit_log_level_flag(tmp_path):
    runner = CliRunner()
    pdf_path = str(tmp_path / "test.pdf")
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    p.insert_text((50, 50), "Explicit Log Level Test", fontsize=14)
    doc.save(pdf_path)
    doc.close()

    out_md = str(tmp_path / "level_out.md")
    result = runner.invoke(app, [pdf_path, "--log-level", "ERROR", "-o", out_md])
    assert result.exit_code == 0
    output = result.output + (result.stderr or "")
    # At ERROR level, info stage telemetry should be suppressed
    assert "[Stage 1]" not in output
