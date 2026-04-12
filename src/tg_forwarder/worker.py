from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import deque
from contextlib import suppress
from multiprocessing.synchronize import Event as ProcessEvent
from pathlib import Path

from telethon import TelegramClient, events
from telethon.sessions import StringSession

from tg_forwarder import __version__ as TG_FORWARDER_VERSION
from tg_forwarder.config import (
    WorkerRuntimeConfig,
    filter_targets_by_forward_strategy,
    resolve_forward_strategy,
    worker_runtime_from_payload,
)
from tg_forwarder.dispatch_queue import (
    DELIVERY_CHANNEL_ACCOUNT,
    DELIVERY_CHANNEL_BOT,
    DispatchQueueDeliveryInsert,
    DispatchQueueJobInsert,
    enqueue_dispatch_job,
    get_worker_offset,
    set_worker_offset,
)
from tg_forwarder.env_utils import read_env_file
from tg_forwarder.filters import (
    MessageMatchResult,
    build_match_note,
    build_mismatch_note,
    explain_message_match,
)
from tg_forwarder.log_buffer import install_queue_forwarding_handler
from tg_forwarder.logging_utils import configure_logging
from tg_forwarder.modules.loader import MessageHookSet, load_message_hooks
from tg_forwarder.monitoring import ForwardLogContext, build_targets_note, monitor_log
from tg_forwarder.telegram_clients import (
    build_telegram_client,
    connect_client_with_proxy_pool,
    disconnect_telegram_client,
)

CATCH_UP_INTERVAL_SECONDS = 2.0
CATCH_UP_BATCH_SIZE = 100


def _recent_seen_cache_limit() -> int:
    raw = (os.getenv("TG_WORKER_RECENT_SEEN_CACHE_LIMIT") or "4096").strip()
    try:
        return max(128, int(raw))
    except ValueError:
        return 4096


RECENT_SEEN_CACHE_LIMIT = _recent_seen_cache_limit()


class ChannelWorker:
    def __init__(
        self,
        runtime: WorkerRuntimeConfig,
        stop_event: ProcessEvent,
        queue_db_path: str | None = None,
        config_path: str | None = None,
    ):
        self.runtime = runtime
        self.stop_event = stop_event
        self.queue_db_path = queue_db_path
        self.logger = logging.getLogger(f"tg_forwarder.worker.{runtime.name}")
        self._env_config_path: Path | None = Path(config_path).resolve() if config_path else None
        self._env_values: dict[str, str] = {}
        if self._env_config_path is not None and self._env_config_path.is_file():
            self._env_values = read_env_file(self._env_config_path)
        self._message_hooks: MessageHookSet = (
            load_message_hooks(self._env_config_path)
            if self._env_config_path is not None and self._env_config_path.is_file()
            else MessageHookSet()
        )
        self._active_tasks: set[asyncio.Task[None]] = set()
        self._persisted_message_cursors: dict[str, int] = {}
        self._recent_new_message_ids: dict[str, set[int]] = {}
        self._recent_new_message_orders: dict[str, deque[int]] = {}
        self._completed_message_ids: dict[str, set[int]] = {}
        self._cursor_locks: dict[str, asyncio.Lock] = {}

    async def run(self) -> None:
        client: TelegramClient | None = None
        try:
            client = await connect_client_with_proxy_pool(
                settings=self.runtime.telegram,
                client_builder=self._build_user_client_with_proxy,
                logger=self.logger,
                scope=f"worker `{self.runtime.name}`",
            )
            if not await client.is_user_authorized():
                raise RuntimeError(
                    f"worker `{self.runtime.name}` session is not authorized, run login first"
                )

            source_entities: dict[str, object] = {}
            for source in self.runtime.sources:
                source_key = str(source)
                self._ensure_source_state(source_key)
                source_entities[source_key] = await client.get_input_entity(source)
                await self._initialize_message_cursor(client, source_key, source_entities[source_key])

            self.logger.info(
                "listening sources=%s, include_edits=%s, forward_own_messages=%s, "
                "queue_db_path=%s, cursors=%s, catch_up_interval=%.1fs, catch_up_batch=%s",
                self._format_source_list(),
                self.runtime.include_edits,
                self.runtime.forward_own_messages,
                self.queue_db_path or "-",
                self._format_cursors(),
                CATCH_UP_INTERVAL_SECONDS,
                CATCH_UP_BATCH_SIZE,
            )

            for source_key, source_entity in source_entities.items():
                client.add_event_handler(
                    self._build_event_handler(source_key=source_key, is_edit=False),
                    events.NewMessage(chats=source_entity),
                )
                if self.runtime.include_edits:
                    client.add_event_handler(
                        self._build_event_handler(source_key=source_key, is_edit=True),
                        events.MessageEdited(chats=source_entity),
                    )

            stop_task = asyncio.create_task(self._watch_stop_signal(client))
            catch_up_task = asyncio.create_task(
                self._catch_up_loop(client=client, source_entities=source_entities)
            )
            try:
                await client.run_until_disconnected()
            finally:
                stop_task.cancel()
                catch_up_task.cancel()
                with suppress(asyncio.CancelledError):
                    await stop_task
                with suppress(asyncio.CancelledError):
                    await catch_up_task
                await self._drain_tasks()
        finally:
            await disconnect_telegram_client(
                client,
                logger=self.logger,
                scope=f"worker `{self.runtime.name}`",
            )

    def _build_event_handler(self, *, source_key: str, is_edit: bool):
        async def handle_event(event: object) -> None:
            await self._schedule_message(
                message=getattr(event, "message", None),
                source_key=source_key,
                is_edit=is_edit,
            )

        return handle_event

    def _build_user_client_with_proxy(self, proxy: object | None) -> TelegramClient:
        if self.runtime.telegram.session_string:
            session = StringSession(self.runtime.telegram.session_string)
        elif self.runtime.telegram.session_file:
            session = self.runtime.telegram.session_file
        else:
            raise RuntimeError("missing session_string or session_file")
        return build_telegram_client(
            session=session,
            api_id=self.runtime.telegram.api_id,
            api_hash=self.runtime.telegram.api_hash,
            device_model="TGForwarder",
            app_version=TG_FORWARDER_VERSION,
            receive_updates=True,
            proxy=proxy,
        )

    def _ensure_source_state(self, source_key: str) -> None:
        self._persisted_message_cursors.setdefault(source_key, 0)
        self._recent_new_message_ids.setdefault(source_key, set())
        self._recent_new_message_orders.setdefault(source_key, deque())
        self._completed_message_ids.setdefault(source_key, set())
        self._cursor_locks.setdefault(source_key, asyncio.Lock())

    def _format_source_list(self) -> str:
        return ", ".join(str(source) for source in self.runtime.sources) or "-"

    def _format_cursors(self) -> str:
        parts = [
            f"{source_key}={self._persisted_message_cursors.get(source_key, 0)}"
            for source_key in (str(source) for source in self.runtime.sources)
        ]
        return ", ".join(parts) or "-"

    async def _initialize_message_cursor(
        self,
        client: TelegramClient,
        source_key: str,
        source_entity: object,
    ) -> None:
        if self.queue_db_path:
            stored_offset = await asyncio.to_thread(
                get_worker_offset,
                self.queue_db_path,
                self.runtime.name,
                source_key,
            )
            if stored_offset is not None:
                self._persisted_message_cursors[source_key] = max(0, int(stored_offset))
                return

        latest_messages = await client.get_messages(source_entity, limit=1)
        latest_message = latest_messages[0] if latest_messages else None
        self._persisted_message_cursors[source_key] = int(getattr(latest_message, "id", 0) or 0)
        await self._persist_cursor(source_key)

    async def _schedule_message(self, *, message: object, source_key: str, is_edit: bool) -> None:
        try:
            if message is None:
                return
            message_id = int(getattr(message, "id", 0) or 0)
            if message_id <= 0:
                return

            if not is_edit and not self._remember_new_message_id(source_key, message_id):
                return

            task = asyncio.create_task(
                self._process_message(message=message, source_key=source_key, is_edit=is_edit)
            )
            self._track_task(task)
        except Exception:
            self.logger.exception("unexpected error while scheduling message")

    async def _catch_up_loop(
        self,
        *,
        client: TelegramClient,
        source_entities: dict[str, object],
    ) -> None:
        while not self.stop_event.is_set():
            recovered_total = 0
            recovered_notes: list[str] = []
            try:
                for source_key, source_entity in source_entities.items():
                    recovered_count = await self._catch_up_messages(
                        client=client,
                        source_key=source_key,
                        source_entity=source_entity,
                    )
                    if recovered_count > 0:
                        recovered_total += recovered_count
                        recovered_notes.append(f"{source_key}={recovered_count}")
                if recovered_total > 0:
                    self.logger.info(
                        "补抓到 %s 条未处理消息，正在按规则检查并准备转发，rule=%s，sources=%s，cursors=%s",
                        recovered_total,
                        self.runtime.name,
                        ", ".join(recovered_notes),
                        self._format_cursors(),
                        extra={"monitor": True, "detect": True},
                    )
            except Exception:
                self.logger.exception("补抓未处理消息失败，rule=%s", self.runtime.name)

            sleep_seconds = 0.1 if recovered_total > 0 else CATCH_UP_INTERVAL_SECONDS
            await asyncio.sleep(sleep_seconds)

    async def _catch_up_messages(
        self,
        *,
        client: TelegramClient,
        source_key: str,
        source_entity: object,
    ) -> int:
        recovered_count = 0
        async for message in client.iter_messages(
            source_entity,
            min_id=self._persisted_message_cursors.get(source_key, 0),
            reverse=True,
            limit=CATCH_UP_BATCH_SIZE,
        ):
            if self.stop_event.is_set():
                break
            message_id = int(getattr(message, "id", 0) or 0)
            if message_id <= 0:
                continue
            if not self._remember_new_message_id(source_key, message_id):
                continue
            recovered_count += 1
            task = asyncio.create_task(
                self._process_message(message=message, source_key=source_key, is_edit=False)
            )
            self._track_task(task)
        return recovered_count

    async def _process_message(self, *, message: object, source_key: str, is_edit: bool) -> None:
        message_id = int(getattr(message, "id", 0) or 0)
        handled_successfully = False
        try:
            handled_successfully = await self._handle_message(
                message=message,
                source_key=source_key,
                is_edit=is_edit,
            )
            if handled_successfully and not is_edit:
                await self._mark_message_handled(source_key, message_id)
        except Exception:
            self.logger.exception("unexpected error while handling message")
        finally:
            if not is_edit and not handled_successfully and message_id > 0:
                self._forget_new_message_id(source_key, message_id)

    async def _handle_message(self, *, message: object, source_key: str, is_edit: bool) -> bool:
        if message is None:
            return True
        if getattr(message, "action", None) is not None:
            return True

        if getattr(message, "out", False) and not self.runtime.forward_own_messages:
            monitor_log(
                self.logger,
                logging.INFO,
                "忽略自己发送的消息",
                message=message,
                context=ForwardLogContext(
                    mode="自动",
                    rule_name=self.runtime.name,
                    source=source_key,
                ),
                note="可开启“转发自己发送的消息（测试用）”后再测试。",
                detect=True,
            )
            return True

        match_result = await explain_message_match(
            message,
            self.runtime.filters,
            env_values=self._env_values or None,
            env_config_path=self._env_config_path,
        )
        match_result = await self._apply_module_after_match_hooks(message, source_key, match_result)
        if not match_result.matched:
            monitor_log(
                self.logger,
                logging.INFO,
                "未命中规则，跳过转发",
                message=message,
                context=ForwardLogContext(
                    mode="自动",
                    rule_name=self.runtime.name,
                    source=source_key,
                ),
                note=build_mismatch_note(match_result, self.runtime.filters),
                detect=True,
            )
            return True

        return await self._enqueue_message_for_dispatch(
            message,
            source_key=source_key,
            is_edit=is_edit,
            match_note=build_match_note(match_result),
            text_override=match_result.dispatch_text_override,
        )

    async def _apply_module_after_match_hooks(
        self,
        message: object,
        source_key: str,
        match_result: MessageMatchResult,
    ) -> MessageMatchResult:
        if not self._message_hooks.after_match:
            return match_result
        current = match_result
        for fn in self._message_hooks.after_match:
            try:
                nxt = await fn(
                    message,
                    source_key=source_key,
                    rule_name=self.runtime.name,
                    match_result=current,
                )
            except Exception:
                self.logger.exception("extension module after_match hook failed")
                break
            if not isinstance(nxt, MessageMatchResult):
                self.logger.warning("extension after_match must return MessageMatchResult, got %s", type(nxt))
                break
            current = nxt
        return current

    async def _enqueue_message_for_dispatch(
        self,
        message: object,
        *,
        source_key: str,
        is_edit: bool,
        match_note: str | None = None,
        text_override: str | None = None,
    ) -> bool:
        if not self.queue_db_path:
            self.logger.warning(
                "persistent queue is unavailable for rule=%s, cannot enqueue matched message",
                self.runtime.name,
            )
            return False

        log_context = ForwardLogContext(
            mode="自动队列",
            rule_name=self.runtime.name,
            source=source_key,
        )
        effective_strategy = resolve_forward_strategy(
            self.runtime.forward_strategy,
            self.runtime.telegram.forward_strategy,
            f"worker `{self.runtime.name}`.forward_strategy",
        )
        account_targets, bot_targets = filter_targets_by_forward_strategy(
            effective_strategy,
            self.runtime.targets,
            self.runtime.bot_targets,
            f"worker `{self.runtime.name}`.forward_strategy",
        )
        unique_key = self._build_queue_unique_key(source_key, message)
        enqueue_result = await asyncio.to_thread(
            enqueue_dispatch_job,
            self.queue_db_path,
            DispatchQueueJobInsert(
                unique_key=unique_key,
                source_chat=source_key,
                message_id=int(getattr(message, "id")),
                rule_name=self.runtime.name,
                runtime_payload_json=json.dumps(
                    self.runtime.as_payload(),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                preview=(str(getattr(message, "raw_text", "") or "").strip() or None),
                enqueued_by=self.runtime.name,
                text_override=text_override,
                deliveries=[
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
                ],
            ),
        )
        if enqueue_result.inserted:
            action_text = "已加入本地队列" if not is_edit else "编辑消息已重新加入队列"
            note_parts = [
                f"queue_id={enqueue_result.job_id}",
                f"queue_depth={enqueue_result.active_count}",
                build_targets_note(account_targets, bot_targets),
                f"strategy={effective_strategy}",
            ]
            if match_note:
                note_parts.append(match_note)
            monitor_log(
                self.logger,
                logging.INFO,
                action_text,
                message=message,
                context=log_context,
                note=" | ".join(note_parts),
                detect=True,
            )
            return True

        if enqueue_result.already_completed:
            note_parts = [f"queue_depth={enqueue_result.active_count}"]
            if match_note:
                note_parts.append(match_note)
            monitor_log(
                self.logger,
                logging.INFO,
                "消息此前已成功转发，跳过重复入队",
                message=message,
                context=log_context,
                note=" | ".join(note_parts),
                detect=True,
            )
            return True

        note_parts = [
            f"queue_id={enqueue_result.existing_job_id}",
            f"queue_depth={enqueue_result.active_count}",
        ]
        if match_note:
            note_parts.append(match_note)
        monitor_log(
            self.logger,
            logging.INFO,
            "消息已在队列中，跳过重复入队",
            message=message,
            context=log_context,
            note=" | ".join(note_parts),
            detect=True,
        )
        return True

    async def _mark_message_handled(self, source_key: str, message_id: int) -> None:
        if message_id <= 0:
            return
        lock = self._cursor_locks[source_key]
        async with lock:
            current_cursor = self._persisted_message_cursors.get(source_key, 0)
            completed_ids = self._completed_message_ids[source_key]
            if message_id <= current_cursor:
                completed_ids.discard(message_id)
                return

            completed_ids.add(message_id)
            next_cursor = current_cursor + 1
            advanced_cursor = current_cursor
            while next_cursor in completed_ids:
                completed_ids.remove(next_cursor)
                advanced_cursor = next_cursor
                next_cursor += 1

            if advanced_cursor > current_cursor:
                self._persisted_message_cursors[source_key] = advanced_cursor
                await self._persist_cursor(source_key)

    async def _persist_cursor(self, source_key: str) -> None:
        if not self.queue_db_path:
            return
        try:
            await asyncio.to_thread(
                set_worker_offset,
                self.queue_db_path,
                self.runtime.name,
                source_key,
                self._persisted_message_cursors.get(source_key, 0),
            )
        except Exception as exc:
            # disk I/O / permission / filesystem issues should not crash the whole worker loop.
            # Trade-off: message offsets may not persist across restarts.
            self.logger.warning(
                "failed to persist cursor (queue db I/O), continue without persistence: %s",
                exc,
                extra={"monitor": True},
            )

    def _build_queue_unique_key(self, source_key: str, message: object) -> str:
        return f"{self.runtime.name}|{source_key}|{int(getattr(message, 'id'))}"

    def _remember_new_message_id(self, source_key: str, message_id: int) -> bool:
        recent_ids = self._recent_new_message_ids[source_key]
        if message_id in recent_ids:
            return False
        recent_ids.add(message_id)
        recent_order = self._recent_new_message_orders[source_key]
        recent_order.append(message_id)
        while len(recent_order) > RECENT_SEEN_CACHE_LIMIT:
            expired_id = recent_order.popleft()
            recent_ids.discard(expired_id)
        return True

    def _forget_new_message_id(self, source_key: str, message_id: int) -> None:
        self._recent_new_message_ids[source_key].discard(message_id)

    def _track_task(self, task: asyncio.Task[None]) -> None:
        self._active_tasks.add(task)
        task.add_done_callback(self._active_tasks.discard)

    async def _drain_tasks(self) -> None:
        if not self._active_tasks:
            return
        pending = set(self._active_tasks)
        done, pending = await asyncio.wait(pending, timeout=10)
        for task in done:
            with suppress(asyncio.CancelledError):
                task.result()
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def _watch_stop_signal(self, client: TelegramClient) -> None:
        while not self.stop_event.is_set():
            await asyncio.sleep(1)
        self.logger.info("stop signal received, disconnecting worker")
        await disconnect_telegram_client(
            client,
            logger=self.logger,
            scope=f"worker `{self.runtime.name}` (stop signal)",
        )


def run_worker_process(
    payload: dict,
    stop_event: ProcessEvent,
    queue_db_path: str | None = None,
    log_queue: object | None = None,
) -> None:
    configure_logging()
    if log_queue is not None:
        install_queue_forwarding_handler(log_queue)
    env_config_path = str(payload.get("_config_path") or "").strip() or None
    runtime = worker_runtime_from_payload(payload)
    logger = logging.getLogger(f"tg_forwarder.worker.{runtime.name}")
    try:
        asyncio.run(
            ChannelWorker(
                runtime,
                stop_event,
                queue_db_path=queue_db_path,
                config_path=env_config_path,
            ).run()
        )
    except KeyboardInterrupt:
        logger.info("worker interrupted")
    except Exception:
        logger.exception("worker crashed")
        raise
