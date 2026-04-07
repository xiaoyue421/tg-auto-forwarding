from __future__ import annotations

import logging
import multiprocessing as mp
import threading
from collections import deque
from dataclasses import asdict, dataclass
from logging.handlers import QueueHandler, QueueListener
from time import time


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
                )
                self._records.append(item)
        except Exception:
            self.handleError(record)

    def list_records(self, limit: int = 200) -> list[dict]:
        with self._lock:
            items = list(self._records)[-limit:]
        items.reverse()
        return [item.as_dict() for item in items]


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
