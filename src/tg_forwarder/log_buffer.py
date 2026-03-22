from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
import logging
import threading
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
                )
                self._records.append(item)
        except Exception:
            self.handleError(record)

    def list_records(self, limit: int = 200) -> list[dict]:
        with self._lock:
            items = list(self._records)[-limit:]
        items.reverse()
        return [item.as_dict() for item in items]
