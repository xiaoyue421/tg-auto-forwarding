from __future__ import annotations

import logging
import os


def configure_logging(level: str | None = None) -> None:
    resolved_level = (level or os.getenv("TG_FORWARDER_LOG_LEVEL", "INFO")).upper()
    logging.basicConfig(
        level=getattr(logging, resolved_level, logging.INFO),
        format="%(asctime)s | %(processName)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )
