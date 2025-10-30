"""Logging helpers for the premarket alert bot."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

DEFAULT_LOG_PATH = Path("logs/bot.log")
DEFAULT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


def setup_logging(log_level: int = logging.INFO, log_file: Optional[Path] = None) -> None:
    """Configure console and rotating file handlers.

    Args:
        log_level: Numeric logging level (e.g., ``logging.INFO``).
        log_file: Optional path to a log file. Defaults to ``logs/bot.log``.
    """

    logger = logging.getLogger()
    if logger.handlers:
        # Avoid adding duplicate handlers when called multiple times.
        return

    logger.setLevel(log_level)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    file_path = log_file or DEFAULT_LOG_PATH
    file_handler = RotatingFileHandler(file_path, maxBytes=2_000_000, backupCount=5)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    logger.debug("Logging configured", extra={"log_file": str(file_path)})


if __name__ == "__main__":
    setup_logging()
    logging.getLogger(__name__).info("Logging system initialised. Ready for future phases.")
