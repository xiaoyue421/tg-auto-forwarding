from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from contextlib import suppress
from dataclasses import dataclass
from multiprocessing.synchronize import Event as ProcessEvent
from pathlib import Path

from tg_forwarder.config import (
    FORWARD_STRATEGY_ACCOUNT_ONLY,
    FORWARD_STRATEGY_BOT_ONLY,
    FORWARD_STRATEGY_PARALLEL,
    ConfigError,
    ForwardTarget,
    WorkerRuntimeConfig,
    resolve_forward_strategy,
    worker_config_digest,
    worker_runtime_from_payload,
)
from tg_forwarder.dashboard_actions import (
    build_bot_forward_contexts,
    build_user_client,
    close_bot_forward_contexts,
    resolve_targets,
    safe_get_entity,
)
from tg_forwarder.dispatch_queue import (
    DELIVERY_CHANNEL_ACCOUNT,
    DELIVERY_CHANNEL_BOT,
    DELIVERY_STATUS_FAILED,
    DELIVERY_STATUS_PENDING,
    DispatchQueueDelivery,
    DispatchQueueJob,
    claim_next_dispatch_job,
    ensure_dispatch_queue,
    get_dispatch_queue_stats,
    list_dispatch_job_deliveries,
    mark_dispatch_deliveries_processing,
    mark_dispatch_deliveries_skipped,
    mark_dispatch_delivery_failed,
    mark_dispatch_delivery_succeeded,
    mark_dispatch_job_done,
    mark_dispatch_job_failed,
    recover_processing_jobs,
)
from tg_forwarder.forwarder import (
    BotForwardContext,
    TargetDispatchResult,
    forward_to_bot_targets_detailed,
    forward_to_targets_detailed,
    send_text_to_bot_targets_detailed,
    send_text_to_targets_detailed,
)
from tg_forwarder.log_buffer import install_queue_forwarding_handler
from tg_forwarder.logging_utils import configure_logging
from tg_forwarder.monitoring import ForwardLogContext, build_targets_note, monitor_log


def _job_poll_interval_seconds() -> float:
    raw = (os.getenv("TG_DISPATCH_POLL_INTERVAL_SECONDS") or "0.25").strip()
    try:
        value = float(raw)
    except ValueError:
        return 0.25
    return min(max(value, 0.05), 60.0)


JOB_POLL_INTERVAL_SECONDS = _job_poll_interval_seconds()


@dataclass(slots=True)
class DispatchRuntimeContext:
    runtime: WorkerRuntimeConfig
    user_client: object
    source_entity_map: dict[str, object]
    account_target_map: dict[str, tuple[ForwardTarget, object]]
    bot_target_map: dict[str, ForwardTarget]
    bot_contexts: list[BotForwardContext]
    bot_source_key: str | None = None


@dataclass(slots=True)
class DeliveryGroupState:
    channel: str
    all_items: list[DispatchQueueDelivery]
    pending: list[DispatchQueueDelivery]
    processing: list[DispatchQueueDelivery]
    failed: list[DispatchQueueDelivery]
    succeeded: list[DispatchQueueDelivery]
    skipped: list[DispatchQueueDelivery]

    @property
    def success_count(self) -> int:
        return len(self.succeeded)

    @property
    def total_count(self) -> int:
        return len(self.all_items)


@dataclass(slots=True)
class DispatchSelection:
    batch: list[DispatchQueueDelivery]
    skip_ids: list[int]
    skip_reason: str | None = None


class PersistentQueueDispatcher:
    def __init__(self, config_path: str | Path, queue_db_path: str | Path, stop_event: ProcessEvent):
        self.config_path = Path(config_path).resolve()
        self.queue_db_path = Path(queue_db_path).resolve()
        self.stop_event = stop_event
        self.logger = logging.getLogger("tg_forwarder.dispatcher")
        self._runtime_contexts: dict[str, DispatchRuntimeContext] = {}

    async def run(self) -> None:
        await asyncio.to_thread(ensure_dispatch_queue, self.queue_db_path)
        recovered_count = await asyncio.to_thread(recover_processing_jobs, self.queue_db_path)
        if recovered_count:
            self.logger.warning(
                "recovered %s interrupted queue jobs back to pending state",
                recovered_count,
                extra={"monitor": True},
            )

        try:
            while not self.stop_event.is_set():
                try:
                    job = await asyncio.to_thread(claim_next_dispatch_job, self.queue_db_path)
                except sqlite3.DatabaseError as exc:
                    self._fatal_queue_database_error(exc, job_id=None)
                    break
                if job is None:
                    await asyncio.sleep(JOB_POLL_INTERVAL_SECONDS)
                    continue
                delay_seconds = await self._handle_job(job)
                if delay_seconds > 0 and not self.stop_event.is_set():
                    await asyncio.sleep(delay_seconds)
        finally:
            await self._close_runtime_contexts()

    async def _handle_job(self, job: DispatchQueueJob) -> float:
        runtime: WorkerRuntimeConfig | None = None
        attempted_dispatch = False
        try:
            runtime = worker_runtime_from_payload(json.loads(job.runtime_payload_json))
            runtime_key = worker_config_digest(runtime)
            context = await self._get_runtime_context(runtime_key, runtime)
            source_key = str(job.source_chat)
            source_entity = await self._get_source_entity(context, source_key)
            message = await context.user_client.get_messages(source_entity, ids=job.message_id)
            log_context = ForwardLogContext(
                mode="自动队列",
                rule_name=runtime.name,
                source=source_key,
            )
            if message is None:
                await self._fail_remaining_deliveries(job.id, "message not found")
                await asyncio.to_thread(mark_dispatch_job_failed, self.queue_db_path, job.id, "message not found")
                return 0.0

            monitor_log(
                self.logger,
                logging.INFO,
                "队列开始发送",
                message=message,
                context=log_context,
                note=(
                    f"queue_id={job.id} | {build_targets_note(runtime.targets, runtime.bot_targets)} | "
                    f"strategy={self._get_runtime_strategy(runtime)}"
                ),
            )

            while not self.stop_event.is_set():
                deliveries = await asyncio.to_thread(list_dispatch_job_deliveries, self.queue_db_path, job.id)
                if not deliveries:
                    await asyncio.to_thread(mark_dispatch_job_done, self.queue_db_path, job.id)
                    self._log_job_completion(job.id, runtime.name)
                    return self._get_inter_job_delay(runtime, attempted_dispatch)

                selection = self._select_deliveries_for_strategy(
                    self._get_runtime_strategy(runtime),
                    deliveries,
                )
                if selection.skip_ids:
                    await asyncio.to_thread(
                        mark_dispatch_deliveries_skipped,
                        self.queue_db_path,
                        selection.skip_ids,
                        selection.skip_reason or "strategy skip",
                    )
                    if selection.skip_reason:
                        monitor_log(
                            self.logger,
                            logging.INFO,
                            "队列策略跳过部分目标",
                            message=message,
                            context=log_context,
                            note=f"queue_id={job.id} | {selection.skip_reason}",
                        )
                    if not selection.batch:
                        continue

                if not selection.batch:
                    await self._finalize_job(job.id, runtime.name)
                    return self._get_inter_job_delay(runtime, attempted_dispatch)

                claimed_count = await asyncio.to_thread(
                    mark_dispatch_deliveries_processing,
                    self.queue_db_path,
                    job.id,
                    [item.id for item in selection.batch],
                )
                if claimed_count <= 0:
                    await asyncio.sleep(0)
                    continue

                current_deliveries = await asyncio.to_thread(
                    list_dispatch_job_deliveries,
                    self.queue_db_path,
                    job.id,
                )
                batch_map = {item.id: item for item in current_deliveries}
                active_batch = [batch_map[item.id] for item in selection.batch if item.id in batch_map]
                if not active_batch:
                    continue

                attempted_dispatch = True
                await self._dispatch_batch(
                    context=context,
                    message=message,
                    batch=active_batch,
                    log_context=log_context,
                    source_key=source_key,
                    text_override=job.text_override,
                )

            return self._get_inter_job_delay(runtime, attempted_dispatch)
        except sqlite3.DatabaseError as exc:
            self._fatal_queue_database_error(exc, job_id=job.id)
            return 0.0
        except Exception as exc:
            try:
                await self._fail_remaining_deliveries(job.id, str(exc))
                await asyncio.to_thread(mark_dispatch_job_failed, self.queue_db_path, job.id, str(exc))
            except sqlite3.DatabaseError as db_exc:
                self._fatal_queue_database_error(db_exc, job_id=job.id)
                return 0.0
            if runtime is not None:
                await self._drop_runtime_context(worker_config_digest(runtime))
            self.logger.exception("queue job failed and kept in database, id=%s, rule=%s", job.id, job.rule_name)
            return 0.0

    async def _dispatch_batch(
        self,
        *,
        context: DispatchRuntimeContext,
        message: object,
        batch: list[DispatchQueueDelivery],
        log_context: ForwardLogContext,
        source_key: str,
        text_override: str | None = None,
    ) -> None:
        if any(delivery.channel == DELIVERY_CHANNEL_BOT for delivery in batch):
            await self._prepare_bot_contexts_for_source(context, source_key)
        for delivery in batch:
            result = await self._dispatch_single_delivery(
                context=context,
                message=message,
                delivery=delivery,
                log_context=log_context,
                text_override=text_override,
            )
            await self._persist_batch_results([delivery], [result])

    async def _dispatch_single_delivery(
        self,
        *,
        context: DispatchRuntimeContext,
        message: object,
        delivery: DispatchQueueDelivery,
        log_context: ForwardLogContext,
        text_override: str | None = None,
    ) -> TargetDispatchResult:
        idempotency_key = f"queue-delivery:{delivery.channel}:{delivery.id}"
        if delivery.channel == DELIVERY_CHANNEL_ACCOUNT:
            resolved = context.account_target_map.get(delivery.target_chat)
            if resolved is None:
                return TargetDispatchResult(
                    channel=DELIVERY_CHANNEL_ACCOUNT,
                    target_chat=delivery.target_chat,
                    success=False,
                    error_message="account target is unavailable",
                )
            if text_override:
                results = await send_text_to_targets_detailed(
                    context.user_client,
                    text_override,
                    [resolved],
                    self.logger,
                    log_context=log_context,
                    idempotency_keys={str(delivery.target_chat): idempotency_key},
                    message=message,
                )
            else:
                results = await forward_to_targets_detailed(
                    context.user_client,
                    message,
                    [resolved],
                    self.logger,
                    log_context=log_context,
                    idempotency_keys={str(delivery.target_chat): idempotency_key},
                )
            return results[0]

        if delivery.channel == DELIVERY_CHANNEL_BOT:
            target = context.bot_target_map.get(delivery.target_chat)
            if target is None:
                return TargetDispatchResult(
                    channel=DELIVERY_CHANNEL_BOT,
                    target_chat=delivery.target_chat,
                    success=False,
                    error_message="bot target is unavailable",
                )
            if not context.bot_contexts:
                return TargetDispatchResult(
                    channel=DELIVERY_CHANNEL_BOT,
                    target_chat=delivery.target_chat,
                    success=False,
                    error_message="no available bot context",
                )
            if text_override:
                results = await send_text_to_bot_targets_detailed(
                    context.user_client,
                    context.bot_contexts,
                    text_override,
                    [target],
                    self.logger,
                    log_context=log_context,
                    idempotency_keys={str(delivery.target_chat): idempotency_key},
                    message=message,
                )
            else:
                results = await forward_to_bot_targets_detailed(
                    context.user_client,
                    context.bot_contexts,
                    message,
                    [target],
                    self.logger,
                    log_context=log_context,
                    idempotency_keys={str(delivery.target_chat): idempotency_key},
                )
            return results[0]

        return TargetDispatchResult(
            channel=str(delivery.channel),
            target_chat=delivery.target_chat,
            success=False,
            error_message="unsupported delivery channel",
        )

    async def _persist_batch_results(
        self,
        batch: list[DispatchQueueDelivery],
        results: list[TargetDispatchResult],
    ) -> None:
        result_map: dict[tuple[str, str], TargetDispatchResult] = {}
        for result in results:
            result_map[(str(result.channel), str(result.target_chat))] = result

        for delivery in batch:
            result = result_map.get((str(delivery.channel), str(delivery.target_chat)))
            if result is None:
                await asyncio.to_thread(
                    mark_dispatch_delivery_failed,
                    self.queue_db_path,
                    delivery.id,
                    "dispatcher result missing",
                )
                continue
            if result.success:
                await asyncio.to_thread(mark_dispatch_delivery_succeeded, self.queue_db_path, delivery.id)
                continue
            await asyncio.to_thread(
                mark_dispatch_delivery_failed,
                self.queue_db_path,
                delivery.id,
                result.error_message or "dispatch failed",
            )

    async def _finalize_job(self, job_id: int, rule_name: str) -> None:
        deliveries = await asyncio.to_thread(list_dispatch_job_deliveries, self.queue_db_path, job_id)
        if not deliveries:
            await asyncio.to_thread(mark_dispatch_job_done, self.queue_db_path, job_id)
            self._log_job_completion(job_id, rule_name)
            return

        failed_deliveries = [item for item in deliveries if item.status == DELIVERY_STATUS_FAILED]
        unresolved = [item for item in deliveries if item.status not in {DELIVERY_STATUS_FAILED, *self._completed_statuses()}]
        if unresolved:
            return
        if failed_deliveries:
            error_message = self._build_job_error_message(failed_deliveries)
            await asyncio.to_thread(mark_dispatch_job_failed, self.queue_db_path, job_id, error_message)
            queue_stats = await asyncio.to_thread(get_dispatch_queue_stats, self.queue_db_path)
            self.logger.warning(
                "queue job retained for retry, id=%s, rule=%s, failed_targets=%s, remaining=%s",
                job_id,
                rule_name,
                len(failed_deliveries),
                queue_stats.active_count,
                extra={"monitor": True},
            )
            return

        await asyncio.to_thread(mark_dispatch_job_done, self.queue_db_path, job_id)
        self._log_job_completion(job_id, rule_name)

    async def _fail_remaining_deliveries(self, job_id: int, error_message: str) -> None:
        deliveries = await asyncio.to_thread(list_dispatch_job_deliveries, self.queue_db_path, job_id)
        for delivery in deliveries:
            if delivery.status in self._completed_statuses():
                continue
            await asyncio.to_thread(
                mark_dispatch_delivery_failed,
                self.queue_db_path,
                delivery.id,
                error_message,
            )

    def _select_deliveries_for_strategy(
        self,
        strategy: str,
        deliveries: list[DispatchQueueDelivery],
    ) -> DispatchSelection:
        groups = self._group_deliveries(deliveries)
        account = groups[DELIVERY_CHANNEL_ACCOUNT]
        bot = groups[DELIVERY_CHANNEL_BOT]

        if strategy == FORWARD_STRATEGY_PARALLEL:
            return DispatchSelection(batch=[*account.pending, *bot.pending], skip_ids=[])
        if strategy == FORWARD_STRATEGY_ACCOUNT_ONLY:
            return self._select_single_channel(
                selected=account,
                skipped=bot,
                selected_label="账号",
                skipped_label="Bot",
            )
        if strategy == FORWARD_STRATEGY_BOT_ONLY:
            return self._select_single_channel(
                selected=bot,
                skipped=account,
                selected_label="Bot",
                skipped_label="账号",
            )
        if strategy == "bot_first":
            return self._select_primary_secondary(
                primary=bot,
                secondary=account,
                primary_label="Bot",
                secondary_label="账号",
            )
        return self._select_primary_secondary(
            primary=account,
            secondary=bot,
            primary_label="账号",
            secondary_label="Bot",
        )

    def _select_single_channel(
        self,
        *,
        selected: DeliveryGroupState,
        skipped: DeliveryGroupState,
        selected_label: str,
        skipped_label: str,
    ) -> DispatchSelection:
        skip_ids = [
            item.id
            for item in skipped.all_items
            if item.status in {DELIVERY_STATUS_PENDING, DELIVERY_STATUS_FAILED}
        ]
        skip_reason = None
        if skip_ids:
            skip_reason = f"当前规则只允许 {selected_label} 发送，跳过 {skipped_label} 目标"
        if selected.pending:
            return DispatchSelection(
                batch=list(selected.pending),
                skip_ids=skip_ids,
                skip_reason=skip_reason,
            )
        return DispatchSelection(batch=[], skip_ids=skip_ids, skip_reason=skip_reason)

    def _select_primary_secondary(
        self,
        *,
        primary: DeliveryGroupState,
        secondary: DeliveryGroupState,
        primary_label: str,
        secondary_label: str,
    ) -> DispatchSelection:
        if primary.pending:
            return DispatchSelection(batch=list(primary.pending), skip_ids=[])

        if primary.success_count > 0:
            skip_ids = [item.id for item in secondary.all_items if item.status in {DELIVERY_STATUS_PENDING, DELIVERY_STATUS_FAILED}]
            return DispatchSelection(
                batch=[],
                skip_ids=skip_ids,
                skip_reason=f"{primary_label}组已有成功投递，跳过 {secondary_label} 组",
            )

        if primary.total_count == 0 and secondary.pending:
            return DispatchSelection(batch=list(secondary.pending), skip_ids=[])

        if secondary.pending:
            skip_ids = [item.id for item in primary.failed]
            return DispatchSelection(
                batch=list(secondary.pending),
                skip_ids=skip_ids,
                skip_reason=f"{primary_label}组全部失败，切换到 {secondary_label} 组",
            )

        if secondary.success_count > 0:
            skip_ids = [item.id for item in primary.failed]
            return DispatchSelection(
                batch=[],
                skip_ids=skip_ids,
                skip_reason=f"{secondary_label}组已接管发送，关闭 {primary_label} 组失败项",
            )

        return DispatchSelection(batch=[], skip_ids=[])

    def _group_deliveries(self, deliveries: list[DispatchQueueDelivery]) -> dict[str, DeliveryGroupState]:
        groups: dict[str, DeliveryGroupState] = {}
        for channel in (DELIVERY_CHANNEL_ACCOUNT, DELIVERY_CHANNEL_BOT):
            items = [item for item in deliveries if item.channel == channel]
            groups[channel] = DeliveryGroupState(
                channel=channel,
                all_items=items,
                pending=[item for item in items if item.status == DELIVERY_STATUS_PENDING],
                processing=[item for item in items if item.status == "processing"],
                failed=[item for item in items if item.status == DELIVERY_STATUS_FAILED],
                succeeded=[item for item in items if item.status == "succeeded"],
                skipped=[item for item in items if item.status == "skipped"],
            )
        return groups

    def _build_job_error_message(self, failed_deliveries: list[DispatchQueueDelivery]) -> str:
        previews: list[str] = []
        for delivery in failed_deliveries[:3]:
            error_text = str(delivery.last_error or "dispatch failed").strip()
            previews.append(f"{delivery.channel}:{delivery.target_chat} => {error_text}")
        if len(failed_deliveries) > 3:
            previews.append(f"... 其余 {len(failed_deliveries) - 3} 个目标仍失败")
        return " | ".join(previews) if previews else "dispatch failed"

    def _completed_statuses(self) -> set[str]:
        return {"succeeded", "skipped"}

    def _get_inter_job_delay(self, runtime: WorkerRuntimeConfig, attempted_dispatch: bool) -> float:
        if not attempted_dispatch:
            return 0.0
        if not runtime.telegram.rate_limit_protection:
            return 0.0
        return max(0.0, float(runtime.telegram.rate_limit_delay_seconds))

    def _get_runtime_strategy(self, runtime: WorkerRuntimeConfig) -> str:
        return resolve_forward_strategy(
            runtime.forward_strategy,
            runtime.telegram.forward_strategy,
            f"worker `{runtime.name}`.forward_strategy",
        )

    def _log_job_completion(self, job_id: int, rule_name: str) -> None:
        queue_stats = get_dispatch_queue_stats(self.queue_db_path)
        self.logger.info(
            "queue job completed and removed, id=%s, rule=%s, remaining=%s",
            job_id,
            rule_name,
            queue_stats.active_count,
            extra={"monitor": True},
        )

    async def _get_runtime_context(
        self,
        runtime_key: str,
        runtime: WorkerRuntimeConfig,
    ) -> DispatchRuntimeContext:
        existing = self._runtime_contexts.get(runtime_key)
        if existing is not None and self._context_is_ready(existing):
            return existing
        if existing is not None:
            await self._drop_runtime_context(runtime_key)

        user_client = await build_user_client(runtime.telegram)
        if not await user_client.is_user_authorized():
            raise ConfigError(f"worker `{runtime.name}` session is not authorized, run login first")

        source_entity_map = {
            str(source): await user_client.get_input_entity(source)
            for source in runtime.sources
        }
        resolved_targets = await resolve_targets(user_client, runtime.targets)
        account_target_map = {
            str(target.chat): (target, entity)
            for target, entity in resolved_targets
        }
        bot_target_map = {str(target.chat): target for target in runtime.bot_targets}

        bot_contexts: list[BotForwardContext] = []
        if runtime.bot_targets and runtime.telegram.bot_tokens:
            bot_contexts = await build_bot_forward_contexts(
                settings=runtime.telegram,
                source_reference=runtime.primary_source,
                targets=runtime.bot_targets,
                logger=self.logger,
                log_scope=f"queue dispatcher `{runtime.name}`",
            )

        context = DispatchRuntimeContext(
            runtime=runtime,
            user_client=user_client,
            source_entity_map=source_entity_map,
            account_target_map=account_target_map,
            bot_target_map=bot_target_map,
            bot_contexts=bot_contexts,
        )
        self._runtime_contexts[runtime_key] = context
        return context

    async def _get_source_entity(self, context: DispatchRuntimeContext, source_key: str) -> object:
        source_entity = context.source_entity_map.get(source_key)
        if source_entity is not None:
            return source_entity
        source_entity = await context.user_client.get_input_entity(source_key)
        context.source_entity_map[source_key] = source_entity
        return source_entity

    async def _prepare_bot_contexts_for_source(
        self,
        context: DispatchRuntimeContext,
        source_key: str,
    ) -> None:
        if context.bot_source_key == source_key:
            return
        for bot_context in context.bot_contexts:
            bot_context.source_entity = await safe_get_entity(bot_context.client, source_key)
        context.bot_source_key = source_key

    def _context_is_ready(self, context: DispatchRuntimeContext) -> bool:
        user_client = context.user_client
        try:
            if not user_client.is_connected():
                return False
        except Exception:
            return False
        for bot_context in context.bot_contexts:
            try:
                if not bot_context.client.is_connected():
                    return False
            except Exception:
                return False
        return True

    async def _drop_runtime_context(self, runtime_key: str) -> None:
        context = self._runtime_contexts.pop(runtime_key, None)
        if context is None:
            return
        with suppress(Exception):
            await context.user_client.disconnect()
        await close_bot_forward_contexts(context.bot_contexts)

    async def _close_runtime_contexts(self) -> None:
        keys = list(self._runtime_contexts)
        for runtime_key in keys:
            await self._drop_runtime_context(runtime_key)

    def _fatal_queue_database_error(self, exc: sqlite3.DatabaseError, job_id: int | None) -> None:
        job_part = f" job_id={job_id}" if job_id is not None else ""
        self.logger.critical(
            "queue sqlite database is unreadable or corrupt%s: %s | path=%s | "
            "back up the file if needed, then delete it to start a fresh queue, "
            "or try recovering with: sqlite3 %s \".recover\" | sqlite3 new_queue.sqlite3",
            job_part,
            exc,
            self.queue_db_path,
            self.queue_db_path,
            extra={"monitor": True},
        )
        try:
            self.stop_event.set()
        except Exception:
            pass


def run_dispatcher_process(
    config_path: str,
    queue_db_path: str,
    stop_event: ProcessEvent,
    log_queue: object | None = None,
) -> None:
    configure_logging()
    if log_queue is not None:
        install_queue_forwarding_handler(log_queue)
    logger = logging.getLogger("tg_forwarder.dispatcher")
    try:
        asyncio.run(
            PersistentQueueDispatcher(
                config_path=config_path,
                queue_db_path=queue_db_path,
                stop_event=stop_event,
            ).run()
        )
    except KeyboardInterrupt:
        logger.info("dispatcher interrupted")
    except Exception:
        logger.exception("dispatcher crashed")
        raise
