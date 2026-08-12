"""Structured logging setup using the standard library."""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path


def setup_logging(
    level: str = "INFO",
    log_dir: str | Path | None = None,
    run_name: str | None = None,
) -> Path | None:
    """
    Configure root logger with a clean format for CLI use.

    Always attaches a console (stderr) handler. If log_dir is given, also
    attaches a file handler writing to a timestamped file under that
    directory (created if missing) — one file per run, so a long-running
    discover call leaves a durable trace to tail/inspect after the fact or
    while it's still running.

    Returns the path of the log file written, or None if log_dir was not given.
    """
    numeric = getattr(logging, level.upper(), logging.INFO)
    fmt = logging.Formatter(
        fmt="%(asctime)s  %(levelname)-8s  %(name)-30s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(numeric)

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(fmt)
    root.addHandler(console)

    log_path: Path | None = None
    if log_dir:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        stem = f"{run_name}_{stamp}" if run_name else stamp
        log_path = log_dir / f"{stem}.log"

        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)

    return log_path


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
