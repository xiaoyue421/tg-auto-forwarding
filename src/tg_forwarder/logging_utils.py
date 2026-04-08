from __future__ import annotations

import logging
import os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


def _parse_bool(raw: str | None) -> bool:
    return str(raw or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _build_file_handler() -> TimedRotatingFileHandler | None:
    if not _parse_bool(os.getenv("TG_FILE_LOG_ENABLED", "false")):
        return None
    raw_path = (os.getenv("TG_FILE_LOG_PATH") or "").strip()
    path = Path(raw_path) if raw_path else Path("logs") / "tg_forwarder.log"
    if not path.is_absolute():
        path = Path.cwd() / path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    backup_count_raw = (os.getenv("TG_FILE_LOG_RETENTION_DAYS") or "").strip()
    try:
        backup_count = max(1, int(backup_count_raw or "7"))
    except ValueError:
        backup_count = 7
    handler = TimedRotatingFileHandler(
        filename=str(path),
        when="midnight",
        backupCount=backup_count,
        encoding="utf-8",
    )
    return handler


def configure_logging(level: str | None = None) -> None:
    resolved_level = (level or os.getenv("TG_FORWARDER_LOG_LEVEL", "INFO")).upper()
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    file_handler = _build_file_handler()
    if file_handler is not None:
        handlers.append(file_handler)
    logging.basicConfig(
        level=getattr(logging, resolved_level, logging.INFO),
        format="%(asctime)s | %(processName)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,
    )
