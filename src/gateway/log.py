"""Logging for the gateway: human-readable lines to stderr, never to stdout."""

from __future__ import annotations

import logging
import sys

_PACKAGE = "gateway"
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def get_logger(suffix: str | None = None) -> logging.Logger:
    return logging.getLogger(_PACKAGE if not suffix else f"{_PACKAGE}.{suffix}")


def configure_logging(level: int = logging.INFO) -> None:
    """Idempotent stderr handler for the package logger (called from `main`)."""
    logger = logging.getLogger(_PACKAGE)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(_FORMAT))
        logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
