"""Logging setup for the application."""

from __future__ import annotations

import logging
import sys


def configure_logging(level: str = "INFO") -> None:
    """Configure a single readable stdout handler.

    Uvicorn also configures its own loggers. We only touch the root logger and
    avoid ``force=True`` so an embedding process keeps control if it wants to.
    """

    root = logging.getLogger()
    if not any(getattr(h, "_link_audio_handler", False) for h in root.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        handler._link_audio_handler = True  # type: ignore[attr-defined]
        root.addHandler(handler)

    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # httpx logs every request at INFO; keep useful failures but not noise.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
