from __future__ import annotations

import logging
import multiprocessing as mp
import re
import threading
from collections import deque
from dataclasses import asdict, dataclass
from logging.handlers import QueueHandler, QueueListener
from time import time

_LOG_LINE_SOURCE_RE = re.compile(r"来源=([^|]+)")


@dataclass(slots=True)
class LogRecordItem:
    sequence: int
    created_at: float
    level: str
    logger: str
    message: str
    raw_message: str
    full_content: str | None
    monitor: bool
    detect: bool
    """Normalized source key (e.g. channel username without @) from monitor_log / extra."""
    source: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


class InMemoryLogHandler(logging.Handler):
    def __init__(self, capacity: int = 500):
        super().__init__()
        self.capacity = capacity
        self._lock = threading.Lock()
        self._sequence = 0
        self._records: deque[LogRecordItem] = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            with self._lock:
                self._sequence += 1
                src = getattr(record, "log_source", None)
                src_s = str(src).strip() if src not in (None, "") else None
                item = LogRecordItem(
                    sequence=self._sequence,
                    created_at=getattr(record, "created", time()),
                    level=record.levelname,
                    logger=record.name,
                    message=self.format(record),
                    raw_message=record.getMessage(),
                    full_content=getattr(record, "full_content", None),
                    monitor=bool(getattr(record, "monitor", False)),
                    detect=bool(getattr(record, "detect", False)),
                    source=src_s,
                )
                self._records.append(item)
        except Exception:
            self.handleError(record)

    def total_record_count(self) -> int:
        with self._lock:
            return len(self._records)

    def list_records(self, limit: int = 200, *, before_sequence: int | None = None) -> list[dict]:
        with self._lock:
            items = list(self._records)
        if before_sequence is not None:
            items = [i for i in items if i.sequence < int(before_sequence)]
        items = items[-limit:]
        items.reverse()
        return [item.as_dict() for item in items]

    def clear_records(self, *, source: str | None = None, kind: str = "all") -> int:
        """Remove in-memory log lines matching the dashboard filter (kind + optional source). Returns removed count."""

        kind_l = (kind or "all").strip().lower() or "all"
        src_want = (source or "").strip() or None
        if src_want:
            src_want = _normalize_source_key(src_want)

        with self._lock:
            old = list(self._records)
            kept: list[LogRecordItem] = []
            removed = 0
            for item in old:
                if _should_remove_log_item(item, kind_l=kind_l, source_key=src_want):
                    removed += 1
                else:
                    kept.append(item)
            self._records = deque(kept, maxlen=self.capacity)
        return removed


def _normalize_source_key(value: str) -> str:
    s = value.strip()
    return s[1:] if s.startswith("@") else s


def _extract_source_key_from_log_item(item: LogRecordItem) -> str | None:
    if item.source:
        return _normalize_source_key(item.source)
    text = f"{item.raw_message or ''}\n{item.message or ''}"
    m = _LOG_LINE_SOURCE_RE.search(text)
    if not m:
        return None
    return _normalize_source_key(m.group(1))


def _matches_kind_filter(item: LogRecordItem, kind: str) -> bool:
    if kind == "all":
        return True
    if kind == "monitor":
        return item.monitor
    if kind == "error":
        return item.level == "ERROR"
    if kind == "detect":
        return item.detect
    if kind == "hdhive":
        if "hdhive" in (item.logger or "").lower():
            return True
        blob = f"{item.raw_message or ''}\n{item.message or ''}"
        return "HDHive" in blob or "hdhive" in blob.lower()
    return True


def _should_remove_log_item(item: LogRecordItem, *, kind_l: str, source_key: str | None) -> bool:
    """True if this line should be deleted when clearing the current dashboard view."""

    if not _matches_kind_filter(item, kind_l):
        return False
    if not source_key:
        return True
    got = _extract_source_key_from_log_item(item)
    if got is None:
        return False
    return got == source_key


def install_queue_forwarding_handler(log_queue: object) -> None:
    """Attach a QueueHandler so child-process logs reach the dashboard listener in the parent."""
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, QueueHandler) and getattr(handler, "queue", None) is log_queue:
            return
    root.addHandler(QueueHandler(log_queue))


def create_dashboard_child_log_bridge(memory_handler: InMemoryLogHandler) -> tuple[mp.Queue, QueueListener]:
    """Queue + listener: start the listener in the web process before spawning workers."""
    queue: mp.Queue = mp.Queue(-1)
    listener = QueueListener(queue, memory_handler, respect_handler_level=True)
    return queue, listener
