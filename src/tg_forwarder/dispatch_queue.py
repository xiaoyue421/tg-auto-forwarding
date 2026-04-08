from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Iterable

from tg_forwarder.config import (
    filter_targets_by_forward_strategy,
    resolve_forward_strategy,
    worker_runtime_from_payload,
)
from tg_forwarder.env_utils import read_env_file


JOB_STATUS_PENDING = "pending"
JOB_STATUS_PROCESSING = "processing"
JOB_STATUS_FAILED = "failed"

DELIVERY_STATUS_PENDING = "pending"
DELIVERY_STATUS_PROCESSING = "processing"
DELIVERY_STATUS_SUCCEEDED = "succeeded"
DELIVERY_STATUS_FAILED = "failed"
DELIVERY_STATUS_SKIPPED = "skipped"

DELIVERY_CHANNEL_ACCOUNT = "account"
DELIVERY_CHANNEL_BOT = "bot"

ACTIVE_JOB_STATUSES = (JOB_STATUS_PENDING, JOB_STATUS_PROCESSING)
TERMINAL_DELIVERY_STATUSES = (
    DELIVERY_STATUS_SUCCEEDED,
    DELIVERY_STATUS_FAILED,
    DELIVERY_STATUS_SKIPPED,
)


@dataclass(slots=True)
class DispatchQueueStats:
    pending_count: int = 0
    processing_count: int = 0
    failed_count: int = 0
    pending_delivery_count: int = 0
    processing_delivery_count: int = 0
    failed_delivery_count: int = 0

    @property
    def active_count(self) -> int:
        return self.pending_count + self.processing_count

    @property
    def active_delivery_count(self) -> int:
        return self.pending_delivery_count + self.processing_delivery_count


@dataclass(slots=True)
class DispatchQueueDeliveryInsert:
    channel: str
    target_chat: str | int

    @property
    def delivery_key(self) -> str:
        return f"{self.channel}:{self.target_chat}"


@dataclass(slots=True)
class DispatchQueueJobInsert:
    unique_key: str
    source_chat: str
    message_id: int
    rule_name: str
    runtime_payload_json: str
    preview: str | None = None
    enqueued_by: str | None = None
    text_override: str | None = None
    deliveries: list[DispatchQueueDeliveryInsert] = field(default_factory=list)


@dataclass(slots=True)
class DispatchQueueEnqueueResult:
    inserted: bool
    active_count: int
    job_id: int | None = None
    existing_job_id: int | None = None
    already_completed: bool = False


@dataclass(slots=True)
class DispatchQueueJob:
    id: int
    unique_key: str
    source_chat: str
    message_id: int
    rule_name: str
    runtime_payload_json: str
    preview: str | None
    enqueued_by: str | None
    status: str
    attempts: int
    created_at: float
    updated_at: float
    claimed_at: float | None
    last_error: str | None
    text_override: str | None


@dataclass(slots=True)
class DispatchQueueDelivery:
    id: int
    job_id: int
    delivery_key: str
    channel: str
    target_chat: str
    status: str
    attempts: int
    created_at: float
    updated_at: float
    claimed_at: float | None
    last_error: str | None


@dataclass(slots=True)
class FailedDispatchJobRecord:
    id: int
    rule_name: str
    source_chat: str
    message_id: int
    preview: str | None
    last_error: str | None
    failed_delivery_count: int
    updated_at: float


@dataclass(slots=True)
class DispatchSuccessHistoryRuleRecord:
    rule_name: str
    count: int
    last_completed_at: float


@dataclass(slots=True)
class RetryFailedDispatchResult:
    retried_count: int
    skipped_non_retryable: int = 0
    skipped_cooldown: int = 0


def default_queue_db_path(config_path: str | Path) -> Path:
    resolved = Path(config_path).resolve()
    return resolved.parent / "tg_forwarder_queue.sqlite3"


def resolve_queue_db_path(config_path: str | Path) -> Path:
    resolved_config_path = Path(config_path).resolve()
    env_values = read_env_file(resolved_config_path)
    raw_path = (env_values.get("TG_QUEUE_DB_PATH") or os.getenv("TG_QUEUE_DB_PATH") or "").strip()
    if not raw_path:
        return default_queue_db_path(resolved_config_path)

    queue_path = Path(raw_path)
    if not queue_path.is_absolute():
        queue_path = (resolved_config_path.parent / queue_path).resolve()
    return queue_path


def ensure_dispatch_queue(path: str | Path) -> Path:
    db_path = Path(path).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS dispatch_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                legacy_row_id INTEGER UNIQUE,
                unique_key TEXT NOT NULL UNIQUE,
                source_chat TEXT NOT NULL,
                message_id INTEGER NOT NULL,
                rule_name TEXT NOT NULL,
                runtime_payload_json TEXT NOT NULL,
                preview TEXT,
                enqueued_by TEXT,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                claimed_at REAL,
                last_error TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_dispatch_jobs_status_id
            ON dispatch_jobs(status, id);

            CREATE TABLE IF NOT EXISTS dispatch_deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                delivery_key TEXT NOT NULL,
                channel TEXT NOT NULL,
                target_chat TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                claimed_at REAL,
                last_error TEXT,
                UNIQUE(job_id, delivery_key),
                FOREIGN KEY(job_id) REFERENCES dispatch_jobs(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_dispatch_deliveries_job_status
            ON dispatch_deliveries(job_id, status, id);

            CREATE INDEX IF NOT EXISTS idx_dispatch_deliveries_status_id
            ON dispatch_deliveries(status, id);

            CREATE TABLE IF NOT EXISTS worker_offsets (
                worker_name TEXT NOT NULL,
                source_chat TEXT NOT NULL,
                last_seen_message_id INTEGER NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY(worker_name, source_chat)
            );

            CREATE TABLE IF NOT EXISTS dispatch_success_history (
                unique_key TEXT PRIMARY KEY,
                source_chat TEXT NOT NULL,
                message_id INTEGER NOT NULL,
                rule_name TEXT NOT NULL,
                completed_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_dispatch_success_history_rule_message
            ON dispatch_success_history(rule_name, source_chat, message_id);
            """
        )
        _migrate_dispatch_jobs_columns(conn)
        _migrate_legacy_queue_rows(conn)
    return db_path


def enqueue_dispatch_job(
    path: str | Path,
    job: DispatchQueueJobInsert,
) -> DispatchQueueEnqueueResult:
    db_path = ensure_dispatch_queue(path)
    deliveries = _normalize_delivery_inserts(job.deliveries)
    now = time.time()
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            """
            SELECT id
            FROM dispatch_jobs
            WHERE unique_key = ?
            ORDER BY id
            LIMIT 1
            """,
            (job.unique_key,),
        ).fetchone()
        if existing is not None:
            active_count = _select_active_count(conn)
            conn.commit()
            return DispatchQueueEnqueueResult(
                inserted=False,
                active_count=active_count,
                existing_job_id=int(existing["id"]),
            )

        history_row = conn.execute(
            """
            SELECT unique_key
            FROM dispatch_success_history
            WHERE unique_key = ?
            LIMIT 1
            """,
            (job.unique_key,),
        ).fetchone()
        if history_row is not None:
            active_count = _select_active_count(conn)
            conn.commit()
            return DispatchQueueEnqueueResult(
                inserted=False,
                active_count=active_count,
                already_completed=True,
            )

        cursor = conn.execute(
            """
            INSERT INTO dispatch_jobs (
                legacy_row_id,
                unique_key,
                source_chat,
                message_id,
                rule_name,
                runtime_payload_json,
                preview,
                enqueued_by,
                status,
                attempts,
                created_at,
                updated_at,
                claimed_at,
                last_error,
                text_override
            )
            VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, NULL, NULL, ?)
            """,
            (
                job.unique_key,
                job.source_chat,
                int(job.message_id),
                job.rule_name,
                job.runtime_payload_json,
                job.preview,
                job.enqueued_by,
                JOB_STATUS_PENDING,
                now,
                now,
                job.text_override,
            ),
        )
        job_id = int(cursor.lastrowid)
        if deliveries:
            conn.executemany(
                """
                INSERT INTO dispatch_deliveries (
                    job_id,
                    delivery_key,
                    channel,
                    target_chat,
                    status,
                    attempts,
                    created_at,
                    updated_at,
                    claimed_at,
                    last_error
                )
                VALUES (?, ?, ?, ?, ?, 0, ?, ?, NULL, NULL)
                """,
                [
                    (
                        job_id,
                        delivery.delivery_key,
                        delivery.channel,
                        str(delivery.target_chat),
                        DELIVERY_STATUS_PENDING,
                        now,
                        now,
                    )
                    for delivery in deliveries
                ],
            )
        active_count = _select_active_count(conn)
        conn.commit()
        return DispatchQueueEnqueueResult(inserted=True, active_count=active_count, job_id=job_id)


def claim_next_dispatch_job(path: str | Path) -> DispatchQueueJob | None:
    db_path = ensure_dispatch_queue(path)
    now = time.time()
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT *
            FROM dispatch_jobs
            WHERE status = ?
            ORDER BY id
            LIMIT 1
            """,
            (JOB_STATUS_PENDING,),
        ).fetchone()
        if row is None:
            conn.commit()
            return None

        cursor = conn.execute(
            """
            UPDATE dispatch_jobs
            SET status = ?,
                attempts = attempts + 1,
                claimed_at = ?,
                updated_at = ?,
                last_error = NULL
            WHERE id = ?
              AND status = ?
            """,
            (
                JOB_STATUS_PROCESSING,
                now,
                now,
                int(row["id"]),
                JOB_STATUS_PENDING,
            ),
        )
        if cursor.rowcount <= 0:
            conn.rollback()
            return None

        updated_row = conn.execute(
            "SELECT * FROM dispatch_jobs WHERE id = ?",
            (int(row["id"]),),
        ).fetchone()
        conn.commit()
        if updated_row is None:
            return None
        return _row_to_job(updated_row)


def get_dispatch_job(path: str | Path, job_id: int) -> DispatchQueueJob | None:
    db_path = ensure_dispatch_queue(path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM dispatch_jobs WHERE id = ?",
            (int(job_id),),
        ).fetchone()
    if row is None:
        return None
    return _row_to_job(row)


def list_dispatch_job_deliveries(path: str | Path, job_id: int) -> list[DispatchQueueDelivery]:
    db_path = ensure_dispatch_queue(path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM dispatch_deliveries
            WHERE job_id = ?
            ORDER BY id
            """,
            (int(job_id),),
        ).fetchall()
    return [_row_to_delivery(row) for row in rows]


def mark_dispatch_deliveries_processing(
    path: str | Path,
    job_id: int,
    delivery_ids: Iterable[int],
) -> int:
    delivery_id_list = sorted({int(item) for item in delivery_ids})
    if not delivery_id_list:
        return 0
    db_path = ensure_dispatch_queue(path)
    now = time.time()
    placeholders = ",".join("?" for _ in delivery_id_list)
    params = [now, now, int(job_id), *delivery_id_list, DELIVERY_STATUS_PENDING]
    with _connect(db_path) as conn:
        cursor = conn.execute(
            f"""
            UPDATE dispatch_deliveries
            SET status = ?,
                attempts = attempts + 1,
                claimed_at = ?,
                updated_at = ?,
                last_error = NULL
            WHERE job_id = ?
              AND id IN ({placeholders})
              AND status = ?
            """,
            (
                DELIVERY_STATUS_PROCESSING,
                *params,
            ),
        )
        return int(cursor.rowcount or 0)


def mark_dispatch_delivery_succeeded(path: str | Path, delivery_id: int) -> None:
    _update_delivery_status(
        path,
        delivery_id,
        status=DELIVERY_STATUS_SUCCEEDED,
        error_message=None,
    )


def mark_dispatch_delivery_failed(path: str | Path, delivery_id: int, error_message: str) -> None:
    _update_delivery_status(
        path,
        delivery_id,
        status=DELIVERY_STATUS_FAILED,
        error_message=error_message,
    )


def mark_dispatch_deliveries_skipped(
    path: str | Path,
    delivery_ids: Iterable[int],
    reason: str,
) -> int:
    delivery_id_list = sorted({int(item) for item in delivery_ids})
    if not delivery_id_list:
        return 0
    db_path = ensure_dispatch_queue(path)
    now = time.time()
    placeholders = ",".join("?" for _ in delivery_id_list)
    with _connect(db_path) as conn:
        cursor = conn.execute(
            f"""
            UPDATE dispatch_deliveries
            SET status = ?,
                claimed_at = NULL,
                updated_at = ?,
                last_error = ?
            WHERE id IN ({placeholders})
              AND status IN (?, ?, ?)
            """,
            (
                DELIVERY_STATUS_SKIPPED,
                now,
                str(reason or "").strip() or "skipped",
                *delivery_id_list,
                DELIVERY_STATUS_PENDING,
                DELIVERY_STATUS_PROCESSING,
                DELIVERY_STATUS_FAILED,
            ),
        )
        return int(cursor.rowcount or 0)


def set_dispatch_job_pending(path: str | Path, job_id: int, error_message: str | None = None) -> None:
    _update_job_status(path, job_id, status=JOB_STATUS_PENDING, error_message=error_message)


def mark_dispatch_job_failed(path: str | Path, job_id: int, error_message: str) -> None:
    _update_job_status(path, job_id, status=JOB_STATUS_FAILED, error_message=error_message)


def mark_dispatch_job_done(path: str | Path, job_id: int) -> None:
    db_path = ensure_dispatch_queue(path)
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT unique_key, source_chat, message_id, rule_name
            FROM dispatch_jobs
            WHERE id = ?
            LIMIT 1
            """,
            (int(job_id),),
        ).fetchone()
        if row is not None:
            conn.execute(
                """
                INSERT INTO dispatch_success_history (
                    unique_key,
                    source_chat,
                    message_id,
                    rule_name,
                    completed_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(unique_key) DO UPDATE
                SET completed_at = excluded.completed_at
                """,
                (
                    str(row["unique_key"]),
                    str(row["source_chat"]),
                    int(row["message_id"]),
                    str(row["rule_name"]),
                    time.time(),
                ),
            )
        conn.execute("DELETE FROM dispatch_jobs WHERE id = ?", (int(job_id),))
        conn.commit()


def recover_processing_jobs(path: str | Path) -> int:
    db_path = ensure_dispatch_queue(path)
    now = time.time()
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        job_cursor = conn.execute(
            """
            UPDATE dispatch_jobs
            SET status = ?,
                claimed_at = NULL,
                updated_at = ?,
                last_error = COALESCE(last_error, 'recovered after restart')
            WHERE status = ?
            """,
            (
                JOB_STATUS_PENDING,
                now,
                JOB_STATUS_PROCESSING,
            ),
        )
        conn.execute(
            """
            UPDATE dispatch_deliveries
            SET status = ?,
                claimed_at = NULL,
                updated_at = ?,
                last_error = COALESCE(last_error, 'recovered after restart')
            WHERE status = ?
            """,
            (
                DELIVERY_STATUS_PENDING,
                now,
                DELIVERY_STATUS_PROCESSING,
            ),
        )
        conn.commit()
        return int(job_cursor.rowcount or 0)


def get_dispatch_queue_stats(path: str | Path) -> DispatchQueueStats:
    db_path = ensure_dispatch_queue(path)
    with _connect(db_path) as conn:
        job_row = conn.execute(
            """
            SELECT
                SUM(CASE WHEN status = ? THEN 1 ELSE 0 END) AS pending_count,
                SUM(CASE WHEN status = ? THEN 1 ELSE 0 END) AS processing_count,
                SUM(CASE WHEN status = ? THEN 1 ELSE 0 END) AS failed_count
            FROM dispatch_jobs
            """,
            (
                JOB_STATUS_PENDING,
                JOB_STATUS_PROCESSING,
                JOB_STATUS_FAILED,
            ),
        ).fetchone()
        delivery_row = conn.execute(
            """
            SELECT
                SUM(CASE WHEN status = ? THEN 1 ELSE 0 END) AS pending_delivery_count,
                SUM(CASE WHEN status = ? THEN 1 ELSE 0 END) AS processing_delivery_count,
                SUM(CASE WHEN status = ? THEN 1 ELSE 0 END) AS failed_delivery_count
            FROM dispatch_deliveries
            """,
            (
                DELIVERY_STATUS_PENDING,
                DELIVERY_STATUS_PROCESSING,
                DELIVERY_STATUS_FAILED,
            ),
        ).fetchone()
    if job_row is None or delivery_row is None:
        return DispatchQueueStats()
    return DispatchQueueStats(
        pending_count=int(job_row["pending_count"] or 0),
        processing_count=int(job_row["processing_count"] or 0),
        failed_count=int(job_row["failed_count"] or 0),
        pending_delivery_count=int(delivery_row["pending_delivery_count"] or 0),
        processing_delivery_count=int(delivery_row["processing_delivery_count"] or 0),
        failed_delivery_count=int(delivery_row["failed_delivery_count"] or 0),
    )


def list_failed_dispatch_jobs(path: str | Path, limit: int = 100) -> list[FailedDispatchJobRecord]:
    db_path = ensure_dispatch_queue(path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                jobs.id,
                jobs.rule_name,
                jobs.source_chat,
                jobs.message_id,
                jobs.preview,
                jobs.last_error,
                jobs.updated_at,
                SUM(CASE WHEN deliveries.status = ? THEN 1 ELSE 0 END) AS failed_delivery_count
            FROM dispatch_jobs AS jobs
            LEFT JOIN dispatch_deliveries AS deliveries
              ON deliveries.job_id = jobs.id
            WHERE jobs.status = ?
            GROUP BY jobs.id
            ORDER BY jobs.updated_at DESC, jobs.id DESC
            LIMIT ?
            """,
            (
                DELIVERY_STATUS_FAILED,
                JOB_STATUS_FAILED,
                max(1, int(limit)),
            ),
        ).fetchall()
    return [
        FailedDispatchJobRecord(
            id=int(row["id"]),
            rule_name=str(row["rule_name"]),
            source_chat=str(row["source_chat"]),
            message_id=int(row["message_id"]),
            preview=row["preview"],
            last_error=row["last_error"],
            failed_delivery_count=int(row["failed_delivery_count"] or 0),
            updated_at=float(row["updated_at"] or 0.0),
        )
        for row in rows
    ]


_NON_RETRYABLE_HINTS = (
    "chat_write_forbidden",
    "chat admin required",
    "forbidden",
    "user is blocked",
    "bot was blocked",
    "bot was kicked",
    "channel private",
    "peer id invalid",
    "chat not found",
    "username invalid",
)

_RETRYABLE_HINTS = (
    "floodwait",
    "timeout",
    "temporarily",
    "connection reset",
    "connection aborted",
    "connection refused",
    "network",
    "server error",
    "too many requests",
    "retry later",
)

_FLOODWAIT_SECONDS_RE = re.compile(r"floodwait[^0-9]*([0-9]{1,6})", re.IGNORECASE)


def _estimate_retry_after_seconds(error_text: str) -> int:
    text = (error_text or "").strip().lower()
    m = _FLOODWAIT_SECONDS_RE.search(text)
    if not m:
        return 0
    try:
        return max(0, min(24 * 3600, int(m.group(1))))
    except ValueError:
        return 0


def _is_non_retryable_error(error_text: str) -> bool:
    text = (error_text or "").strip().lower()
    if not text:
        return False
    return any(token in text for token in _NON_RETRYABLE_HINTS)


def _is_retryable_error(error_text: str) -> bool:
    text = (error_text or "").strip().lower()
    if not text:
        return True
    if _is_non_retryable_error(text):
        return False
    return any(token in text for token in _RETRYABLE_HINTS)


def retry_failed_dispatch_jobs(path: str | Path, job_ids: Iterable[int] | None = None) -> int:
    db_path = ensure_dispatch_queue(path)
    now = time.time()
    job_id_list = sorted({int(item) for item in (job_ids or [])})
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if job_id_list:
            placeholders = ",".join("?" for _ in job_id_list)
            scope_sql = f"AND job_id IN ({placeholders})"
            scope_params: tuple[object, ...] = tuple(job_id_list)
            job_scope_sql = f"AND id IN ({placeholders})"
        else:
            scope_sql = ""
            scope_params = ()
            job_scope_sql = ""

        delivery_cursor = conn.execute(
            f"""
            UPDATE dispatch_deliveries
            SET status = ?,
                claimed_at = NULL,
                updated_at = ?,
                last_error = NULL
            WHERE status = ?
              {scope_sql}
            """,
            (
                DELIVERY_STATUS_PENDING,
                now,
                DELIVERY_STATUS_FAILED,
                *scope_params,
            ),
        )
        conn.execute(
            f"""
            UPDATE dispatch_jobs
            SET status = ?,
                claimed_at = NULL,
                updated_at = ?,
                last_error = NULL
            WHERE id IN (
                SELECT DISTINCT job_id
                FROM dispatch_deliveries
                WHERE status = ?
            )
            {job_scope_sql}
            """,
            (
                JOB_STATUS_PENDING,
                now,
                DELIVERY_STATUS_PENDING,
                *scope_params,
            ),
        )
        conn.commit()
        return int(delivery_cursor.rowcount or 0)


def retry_failed_dispatch_jobs_smart(path: str | Path, job_ids: Iterable[int] | None = None) -> RetryFailedDispatchResult:
    """Retry only likely-transient failures and obey FloodWait-like cooldown hints."""
    db_path = ensure_dispatch_queue(path)
    now = time.time()
    job_id_list = sorted({int(item) for item in (job_ids or [])})
    retried_ids: list[int] = []
    skipped_non_retryable = 0
    skipped_cooldown = 0
    with _connect(db_path) as conn:
        if job_id_list:
            placeholders = ",".join("?" for _ in job_id_list)
            rows = conn.execute(
                f"""
                SELECT id, updated_at, last_error
                FROM dispatch_jobs
                WHERE status = ?
                  AND id IN ({placeholders})
                """,
                (JOB_STATUS_FAILED, *job_id_list),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, updated_at, last_error
                FROM dispatch_jobs
                WHERE status = ?
                """,
                (JOB_STATUS_FAILED,),
            ).fetchall()

    for row in rows:
        err = str(row["last_error"] or "")
        if not _is_retryable_error(err):
            skipped_non_retryable += 1
            continue
        retry_after = _estimate_retry_after_seconds(err)
        updated_at = float(row["updated_at"] or 0.0)
        if retry_after > 0 and (updated_at + retry_after) > now:
            skipped_cooldown += 1
            continue
        retried_ids.append(int(row["id"]))

    if not retried_ids:
        return RetryFailedDispatchResult(
            retried_count=0,
            skipped_non_retryable=skipped_non_retryable,
            skipped_cooldown=skipped_cooldown,
        )

    retried_count = retry_failed_dispatch_jobs(db_path, job_ids=retried_ids)
    return RetryFailedDispatchResult(
        retried_count=retried_count,
        skipped_non_retryable=skipped_non_retryable,
        skipped_cooldown=skipped_cooldown,
    )


def clear_failed_dispatch_jobs(path: str | Path, job_ids: Iterable[int] | None = None) -> int:
    db_path = ensure_dispatch_queue(path)
    job_id_list = sorted({int(item) for item in (job_ids or [])})
    with _connect(db_path) as conn:
        if job_id_list:
            placeholders = ",".join("?" for _ in job_id_list)
            cursor = conn.execute(
                f"""
                DELETE FROM dispatch_jobs
                WHERE status = ?
                  AND id IN ({placeholders})
                """,
                (
                    JOB_STATUS_FAILED,
                    *job_id_list,
                ),
            )
        else:
            cursor = conn.execute(
                "DELETE FROM dispatch_jobs WHERE status = ?",
                (JOB_STATUS_FAILED,),
            )
        return int(cursor.rowcount or 0)


def count_dispatch_success_history(path: str | Path, rule_name: str | None = None) -> int:
    db_path = ensure_dispatch_queue(path)
    normalized_rule_name = (str(rule_name or "").strip() or None)
    with _connect(db_path) as conn:
        if normalized_rule_name is None:
            row = conn.execute(
                "SELECT COUNT(*) AS total_count FROM dispatch_success_history"
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT COUNT(*) AS total_count
                FROM dispatch_success_history
                WHERE rule_name = ?
                """,
                (normalized_rule_name,),
            ).fetchone()
    if row is None:
        return 0
    return int(row["total_count"] or 0)


def list_dispatch_success_history_rules(
    path: str | Path,
    limit: int = 200,
) -> list[DispatchSuccessHistoryRuleRecord]:
    db_path = ensure_dispatch_queue(path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                rule_name,
                COUNT(*) AS item_count,
                MAX(completed_at) AS last_completed_at
            FROM dispatch_success_history
            GROUP BY rule_name
            ORDER BY last_completed_at DESC, rule_name ASC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
    return [
        DispatchSuccessHistoryRuleRecord(
            rule_name=str(row["rule_name"] or ""),
            count=int(row["item_count"] or 0),
            last_completed_at=float(row["last_completed_at"] or 0.0),
        )
        for row in rows
        if str(row["rule_name"] or "").strip()
    ]


def clear_dispatch_success_history(path: str | Path, rule_name: str | None = None) -> int:
    db_path = ensure_dispatch_queue(path)
    normalized_rule_name = (str(rule_name or "").strip() or None)
    with _connect(db_path) as conn:
        if normalized_rule_name is None:
            cursor = conn.execute("DELETE FROM dispatch_success_history")
        else:
            cursor = conn.execute(
                """
                DELETE FROM dispatch_success_history
                WHERE rule_name = ?
                """,
                (normalized_rule_name,),
            )
    return int(cursor.rowcount or 0)


def get_worker_offset(path: str | Path, worker_name: str, source_chat: str | int) -> int | None:
    db_path = ensure_dispatch_queue(path)
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT last_seen_message_id
            FROM worker_offsets
            WHERE worker_name = ?
              AND source_chat = ?
            """,
            (
                str(worker_name),
                str(source_chat),
            ),
        ).fetchone()
    if row is None:
        return None
    return int(row["last_seen_message_id"] or 0)


def set_worker_offset(path: str | Path, worker_name: str, source_chat: str | int, message_id: int) -> None:
    db_path = ensure_dispatch_queue(path)
    now = time.time()
    normalized_message_id = max(0, int(message_id))
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO worker_offsets (
                worker_name,
                source_chat,
                last_seen_message_id,
                updated_at
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(worker_name, source_chat) DO UPDATE
            SET last_seen_message_id = CASE
                    WHEN excluded.last_seen_message_id > worker_offsets.last_seen_message_id
                    THEN excluded.last_seen_message_id
                    ELSE worker_offsets.last_seen_message_id
                END,
                updated_at = excluded.updated_at
            """,
            (
                str(worker_name),
                str(source_chat),
                normalized_message_id,
                now,
            ),
        )


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    # WAL may fail on some filesystems / mounts (e.g. permission issues, certain network volumes).
    # Fallback to DELETE to keep the app running instead of crashing the worker.
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def _normalize_delivery_inserts(
    deliveries: Iterable[DispatchQueueDeliveryInsert],
) -> list[DispatchQueueDeliveryInsert]:
    normalized: list[DispatchQueueDeliveryInsert] = []
    seen_keys: set[str] = set()
    for delivery in deliveries:
        channel = str(delivery.channel).strip().lower()
        if channel not in {DELIVERY_CHANNEL_ACCOUNT, DELIVERY_CHANNEL_BOT}:
            continue
        target_chat = str(delivery.target_chat).strip()
        if not target_chat:
            continue
        normalized_delivery = DispatchQueueDeliveryInsert(channel=channel, target_chat=target_chat)
        if normalized_delivery.delivery_key in seen_keys:
            continue
        seen_keys.add(normalized_delivery.delivery_key)
        normalized.append(normalized_delivery)
    return normalized


def _update_delivery_status(
    path: str | Path,
    delivery_id: int,
    *,
    status: str,
    error_message: str | None,
) -> None:
    db_path = ensure_dispatch_queue(path)
    now = time.time()
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE dispatch_deliveries
            SET status = ?,
                claimed_at = NULL,
                updated_at = ?,
                last_error = ?
            WHERE id = ?
            """,
            (
                status,
                now,
                (str(error_message or "").strip() or None),
                int(delivery_id),
            ),
        )


def _update_job_status(
    path: str | Path,
    job_id: int,
    *,
    status: str,
    error_message: str | None,
) -> None:
    db_path = ensure_dispatch_queue(path)
    now = time.time()
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE dispatch_jobs
            SET status = ?,
                claimed_at = NULL,
                updated_at = ?,
                last_error = ?
            WHERE id = ?
            """,
            (
                status,
                now,
                (str(error_message or "").strip() or None),
                int(job_id),
            ),
        )


def _select_active_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS active_count
        FROM dispatch_jobs
        WHERE status IN (?, ?)
        """,
        ACTIVE_JOB_STATUSES,
    ).fetchone()
    if row is None:
        return 0
    return int(row["active_count"] or 0)


def _row_to_job(row: sqlite3.Row) -> DispatchQueueJob:
    raw_override = row["text_override"]
    text_override = str(raw_override).strip() if raw_override else None
    return DispatchQueueJob(
        id=int(row["id"]),
        unique_key=str(row["unique_key"]),
        source_chat=str(row["source_chat"]),
        message_id=int(row["message_id"]),
        rule_name=str(row["rule_name"]),
        runtime_payload_json=str(row["runtime_payload_json"]),
        preview=row["preview"],
        enqueued_by=row["enqueued_by"],
        status=str(row["status"]),
        attempts=int(row["attempts"] or 0),
        created_at=float(row["created_at"] or 0.0),
        updated_at=float(row["updated_at"] or 0.0),
        claimed_at=float(row["claimed_at"]) if row["claimed_at"] is not None else None,
        last_error=row["last_error"],
        text_override=text_override,
    )


def _migrate_dispatch_jobs_columns(conn: sqlite3.Connection) -> None:
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(dispatch_jobs)")}
    if "text_override" not in columns:
        conn.execute("ALTER TABLE dispatch_jobs ADD COLUMN text_override TEXT")


def _row_to_delivery(row: sqlite3.Row) -> DispatchQueueDelivery:
    return DispatchQueueDelivery(
        id=int(row["id"]),
        job_id=int(row["job_id"]),
        delivery_key=str(row["delivery_key"]),
        channel=str(row["channel"]),
        target_chat=str(row["target_chat"]),
        status=str(row["status"]),
        attempts=int(row["attempts"] or 0),
        created_at=float(row["created_at"] or 0.0),
        updated_at=float(row["updated_at"] or 0.0),
        claimed_at=float(row["claimed_at"]) if row["claimed_at"] is not None else None,
        last_error=row["last_error"],
    )


def _migrate_legacy_queue_rows(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "dispatch_queue"):
        return

    rows = conn.execute(
        """
        SELECT legacy.*
        FROM dispatch_queue AS legacy
        LEFT JOIN dispatch_jobs AS jobs
          ON jobs.legacy_row_id = legacy.id
        WHERE jobs.id IS NULL
        ORDER BY legacy.id
        """
    ).fetchall()
    if not rows:
        return

    now = time.time()
    for row in rows:
        runtime = worker_runtime_from_payload(_safe_json_loads(str(row["runtime_payload_json"])))
        effective_strategy = resolve_forward_strategy(
            runtime.forward_strategy,
            runtime.telegram.forward_strategy,
            f"worker `{runtime.name}`.forward_strategy",
        )
        account_targets, bot_targets = filter_targets_by_forward_strategy(
            effective_strategy,
            runtime.targets,
            runtime.bot_targets,
            f"worker `{runtime.name}`.forward_strategy",
        )
        deliveries = _normalize_delivery_inserts(
            [
                *[
                    DispatchQueueDeliveryInsert(
                        channel=DELIVERY_CHANNEL_ACCOUNT,
                        target_chat=target.chat,
                    )
                    for target in account_targets
                ],
                *[
                    DispatchQueueDeliveryInsert(
                        channel=DELIVERY_CHANNEL_BOT,
                        target_chat=target.chat,
                    )
                    for target in bot_targets
                ],
            ]
        )
        legacy_status = str(row["status"])
        job_status = JOB_STATUS_PENDING if legacy_status in ACTIVE_JOB_STATUSES else JOB_STATUS_FAILED
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO dispatch_jobs (
                legacy_row_id,
                unique_key,
                source_chat,
                message_id,
                rule_name,
                runtime_payload_json,
                preview,
                enqueued_by,
                status,
                attempts,
                created_at,
                updated_at,
                claimed_at,
                last_error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                int(row["id"]),
                str(row["unique_key"]),
                str(row["source_chat"]),
                int(row["message_id"]),
                str(row["rule_name"]),
                str(row["runtime_payload_json"]),
                row["preview"],
                row["enqueued_by"],
                job_status,
                int(row["attempts"] or 0),
                float(row["created_at"] or now),
                float(row["updated_at"] or now),
                row["last_error"],
            ),
        )
        if cursor.rowcount <= 0:
            continue
        job_id = int(cursor.lastrowid)
        delivery_status = (
            DELIVERY_STATUS_PENDING if job_status == JOB_STATUS_PENDING else DELIVERY_STATUS_FAILED
        )
        conn.executemany(
            """
            INSERT OR IGNORE INTO dispatch_deliveries (
                job_id,
                delivery_key,
                channel,
                target_chat,
                status,
                attempts,
                created_at,
                updated_at,
                claimed_at,
                last_error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            [
                (
                    job_id,
                    delivery.delivery_key,
                    delivery.channel,
                    str(delivery.target_chat),
                    delivery_status,
                    int(row["attempts"] or 0),
                    float(row["created_at"] or now),
                    float(row["updated_at"] or now),
                    row["last_error"],
                )
                for delivery in deliveries
            ],
        )


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = ?
        LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def _safe_json_loads(raw_value: str) -> dict:
    import json

    loaded = json.loads(raw_value)
    if not isinstance(loaded, dict):
        raise ValueError("legacy runtime payload must be an object")
    return loaded
