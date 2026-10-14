"""Runtime logging configuration for docforge.

Conforms to Issue #3 (structured runtime logging system for verbose mode).
"""

from __future__ import annotations

import logging
from typing import Optional, Union
from rich.console import Console
from rich.logging import RichHandler

from docforge.models.cli_options import LogLevel

LOGGER_NAME = "docforge"
logger = logging.getLogger(LOGGER_NAME)


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Get the project root logger or a sub-stage logger."""
    if name:
        return logging.getLogger(f"{LOGGER_NAME}.{name}")
    return logger


def resolve_log_level(
    log_level: Optional[Union[LogLevel, str]] = None,
    verbose: int = 0,
    debug: bool = False,
) -> int:
    """Resolve standard Python logging level integer from flags/arguments."""
    if log_level is not None:
        lvl_str = log_level.value if isinstance(log_level, LogLevel) else str(log_level).upper()
        if lvl_str == "DEBUG":
            return logging.DEBUG
        elif lvl_str == "INFO":
            return logging.INFO
        elif lvl_str in ("WARN", "WARNING"):
            return logging.WARNING
        elif lvl_str == "ERROR":
            return logging.ERROR

    if debug or verbose >= 2:
        return logging.DEBUG
    elif verbose == 1:
        return logging.INFO
    return logging.WARNING


def setup_logging(
    log_level: Optional[Union[LogLevel, str]] = None,
    verbose: int = 0,
    debug: bool = False,
    console: Optional[Console] = None,
) -> logging.Logger:
    """Configure structured logging utilizing RichHandler."""
    level = resolve_log_level(log_level=log_level, verbose=verbose, debug=debug)

    log_console = console or Console(stderr=True)
    handler = RichHandler(
        console=log_console,
        show_time=True,
        show_path=False,
        markup=True,
        rich_tracebacks=True,
    )
    handler.setLevel(level)

    logger.setLevel(level)
    # Remove existing RichHandlers to prevent duplicate logs on repeated setup
    logger.handlers = [h for h in logger.handlers if not isinstance(h, RichHandler)]
    logger.addHandler(handler)
    logger.propagate = False

    return logger
