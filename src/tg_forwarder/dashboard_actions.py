from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime
import logging
from pathlib import Path
import re
from typing import Any

from telethon import TelegramClient
from telethon.sessions import MemorySession, StringSession
from telethon.tl.custom.message import Message
from telethon.utils import get_display_name

from tg_forwarder.config import (
    AppConfig,
    ConfigError,
    ForwardTarget,
    SEARCH_MODE_FAST,
    TelegramSettings,
    WorkerConfig,
    filter_targets_by_forward_strategy,
    normalize_optional_forward_strategy,
    resolve_forward_strategy,
    load_config,
    normalize_search_mode,
    parse_chat_reference,
    parse_list_value,
)
from tg_forwarder.forwarder import BotForwardContext, build_message_link, forward_with_strategy
from tg_forwarder.message_index import extract_message_keyword_values
from tg_forwarder.monitoring import ForwardLogContext, build_targets_note, monitor_log
from tg_forwarder.telegram_clients import (
    build_telegram_client,
    connect_client_with_proxy_pool,
    start_bot_client_with_proxy_pool,
)


LOGGER = logging.getLogger("tg_forwarder.dashboard_actions")
SEARCH_SOURCE_CONCURRENCY = 4
SEARCH_NATIVE_FETCH_CAP = 120
SEARCH_LOCAL_SCAN_CAP = 4000
SEARCH_TERM_SPLIT_PATTERN = re.compile(r"[\s,，;；、|/]+")


def search_messages(
    config_path: str | Path,
    query: str,
    limit: int,
    search_mode: str | None = None,
) -> dict[str, Any]:
    config = load_config(config_path)
    resolved_search_mode = (
        normalize_search_mode(search_mode, "search_mode")
        if search_mode is not None
        else config.telegram.search_default_mode or SEARCH_MODE_FAST
    )
    return asyncio.run(
        _search_messages(
            config,
            query=query.strip(),
            limit=limit,
            search_mode=resolved_search_mode,
        )
    )


def manual_forward_message(
    config_path: str | Path,
    source_chat: str,
    message_id: int,
    target_chats: str,
    bot_target_chats: str,
    forward_strategy: str | None = None,
) -> dict[str, Any]:
    config = load_config(config_path)
    return asyncio.run(
        _manual_forward_message(
            config=config,
            source_chat=source_chat,
            message_id=message_id,
            target_chats=target_chats,
            bot_target_chats=bot_target_chats,
            forward_strategy=forward_strategy,
        )
    )
async def _search_messages(
    config: AppConfig,
    query: str,
    limit: int,
    search_mode: str,
) -> dict[str, Any]:
    source_index = build_source_index(config)
    if not source_index:
        raise ConfigError("no searchable sources found")
    if not query.strip():
        raise ConfigError("search query is required")

    client = await build_user_client(config.telegram)
    try:
        if not await client.is_user_authorized():
            raise ConfigError("dashboard session is not authorized")

        source_count = max(1, len(source_index))
        per_source_limit = max(
            10,
            min(max((limit * 3) // min(source_count, 6), 10), 40),
        )
        results: list[dict[str, Any]] = []
        seen_keys: set[tuple[str, int]] = set()
        semaphore = asyncio.Semaphore(min(SEARCH_SOURCE_CONCURRENCY, source_count))

        async def search_single_source(
            source_key: str,
            meta: dict[str, Any],
        ) -> list[tuple[str, Message, str | int, str, dict[str, Any]]]:
            async with semaphore:
                source_value = meta["source"]
                try:
                    entity = await client.get_entity(source_value)
                    messages = await collect_messages_for_source(
                        client=client,
                        source=entity,
                        query=query,
                        limit=per_source_limit,
                    )
                except Exception:
                    LOGGER.exception("search failed for source=%s", source_value)
                    return []

                source_label = build_source_label(entity, fallback=str(source_value))
                return [
                    (source_key, message, source_value, source_label, meta)
                    for message in messages
                ]

        search_batches = await asyncio.gather(
            *(search_single_source(source_key, meta) for source_key, meta in source_index.items())
        )
        for batch in search_batches:
            for source_key, message, source_value, source_label, meta in batch:
                dedupe_key = (source_key, int(message.id))
                if dedupe_key in seen_keys:
                    continue
                seen_keys.add(dedupe_key)
                results.append(
                    serialize_search_result(
                        message=message,
                        source_value=source_value,
                        source_label=source_label,
                        rules=meta["rules"],
                        default_targets=meta["default_targets"],
                        default_bot_targets=meta["default_bot_targets"],
                        default_forward_strategy=meta.get("default_forward_strategy", ""),
                    )
                )

        results.sort(key=lambda item: item["timestamp"], reverse=True)
        trimmed = results[:limit]
        for item in trimmed:
            item.pop("timestamp", None)
        return {"items": trimmed, "mode": search_mode}
    finally:
        with suppress(Exception):
            await client.disconnect()


async def _manual_forward_message(
    config: AppConfig,
    source_chat: str,
    message_id: int,
    target_chats: str,
    bot_target_chats: str,
    forward_strategy: str | None = None,
) -> dict[str, Any]:
    account_targets = parse_manual_targets(target_chats, "target_chats")
    bot_targets = parse_manual_targets(bot_target_chats, "bot_target_chats")
    if not account_targets and not bot_targets:
        raise ConfigError("manual forward requires target_chats or bot_target_chats")
    effective_strategy = resolve_forward_strategy(
        normalize_optional_forward_strategy(forward_strategy, "manual.forward_strategy"),
        config.telegram.forward_strategy,
        "manual.forward_strategy",
    )
    account_targets, bot_targets = filter_targets_by_forward_strategy(
        effective_strategy,
        account_targets,
        bot_targets,
        "manual.forward_strategy",
    )
    if not account_targets and not bot_targets:
        raise ConfigError("manual forward has no available targets for the selected strategy")

    user_client = await build_user_client(config.telegram)
    bot_contexts: list[BotForwardContext] = []
    try:
        if not await user_client.is_user_authorized():
            raise ConfigError("dashboard session is not authorized")

        source_reference = parse_chat_reference(source_chat, "source_chat")
        source_entity = await user_client.get_input_entity(source_reference)
        message = await user_client.get_messages(source_entity, ids=message_id)
        if message is None:
            raise ConfigError("message not found")
        log_context = ForwardLogContext(mode="手动", source=str(source_reference))
        monitor_log(
            LOGGER,
            logging.INFO,
            "执行手动转发",
            message=message,
            context=log_context,
            note=(
                f"{build_targets_note(account_targets, bot_targets)} | "
                f"strategy={effective_strategy}"
            ),
        )

        resolved_targets: list[tuple[ForwardTarget, object]] = []
        if account_targets:
            resolved_targets = await resolve_targets(user_client, account_targets)

        if bot_targets:
            if not config.telegram.bot_tokens:
                if not account_targets:
                    raise ConfigError("TG_BOT_TOKEN is required for bot forwarding")
                LOGGER.warning("bot targets configured but TG_BOT_TOKEN is missing, bot forwarding disabled")
            else:
                bot_contexts = await build_bot_forward_contexts(
                    settings=config.telegram,
                    source_reference=source_reference,
                    targets=bot_targets,
                    logger=LOGGER,
                    log_scope="manual forward",
                )
                if not bot_contexts and not account_targets:
                    raise ConfigError("all configured bot tokens failed to initialize")

        dispatch_result = await forward_with_strategy(
            strategy=effective_strategy,
            user_client=user_client,
            bot_contexts=bot_contexts,
            message=message,
            account_targets=resolved_targets,
            bot_targets=bot_targets,
            logger=LOGGER,
            log_context=log_context,
        )

        return {
            "source_chat": str(source_reference),
            "message_id": message_id,
            "forward_strategy": dispatch_result.strategy,
            "account_targets": [target.chat for target in account_targets],
            "bot_targets": [target.chat for target in bot_targets],
            "account_sent": dispatch_result.account_success_count,
            "bot_sent": dispatch_result.bot_success_count,
            "attempted_account": dispatch_result.attempted_account,
            "attempted_bot": dispatch_result.attempted_bot,
        }
    finally:
        with suppress(Exception):
            await user_client.disconnect()
        await close_bot_forward_contexts(bot_contexts)


def build_source_index(config: AppConfig) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for worker in config.workers:
        for source in worker.sources:
            source_key = str(source)
            if source_key not in index:
                index[source_key] = {
                    "source": source,
                    "rules": [],
                    "default_targets": [],
                    "default_bot_targets": [],
                    "forward_strategies": [],
                }
            meta = index[source_key]
            meta["rules"].append(worker.name)
            append_unique_targets(meta["default_targets"], worker.targets)
            append_unique_targets(meta["default_bot_targets"], worker.bot_targets)
            effective_strategy = resolve_forward_strategy(
                worker.forward_strategy,
                config.telegram.forward_strategy,
                f"worker `{worker.name}`.forward_strategy",
            )
            if effective_strategy not in meta["forward_strategies"]:
                meta["forward_strategies"].append(effective_strategy)
    for meta in index.values():
        strategies = meta.pop("forward_strategies", [])
        meta["default_forward_strategy"] = strategies[0] if len(strategies) == 1 else ""
    return index


async def collect_messages_for_source(
    client: TelegramClient,
    source: object,
    query: str,
    limit: int,
) -> list[Message]:
    normalized_query = normalize_search_text(query)
    search_terms = build_search_terms(query)
    messages: list[Message] = []
    seen_ids: set[int] = set()

    native_fetch_limit = min(max(limit * 4, 30), SEARCH_NATIVE_FETCH_CAP)
    native_matches = await collect_native_search_matches(
        client=client,
        source=source,
        query=query,
        limit=native_fetch_limit,
        normalized_query=normalized_query,
        search_terms=search_terms,
    )
    append_unique_messages(messages, seen_ids, native_matches, limit)
    if len(messages) >= limit:
        return messages

    scan_limit = build_search_scan_limit(limit)
    async for message in client.iter_messages(source, limit=scan_limit):
        if getattr(message, "action", None) is not None:
            continue
        message_id = int(message.id)
        if message_id in seen_ids:
            continue
        if await message_matches_search_query(message, normalized_query, search_terms):
            seen_ids.add(message_id)
            messages.append(message)
            if len(messages) >= limit:
                return messages
    return messages


async def collect_native_search_matches(
    client: TelegramClient,
    source: object,
    query: str,
    limit: int,
    normalized_query: str,
    search_terms: list[str],
) -> list[Message]:
    if not query.strip():
        return []
    matches: list[Message] = []
    async for message in client.iter_messages(source, search=query, limit=limit):
        if getattr(message, "action", None) is not None:
            continue
        if not await message_matches_search_query(message, normalized_query, search_terms):
            continue
        matches.append(message)
    return matches


def append_unique_messages(
    target: list[Message],
    seen_ids: set[int],
    candidates: list[Message],
    limit: int,
) -> None:
    for message in candidates:
        message_id = int(message.id)
        if message_id in seen_ids:
            continue
        seen_ids.add(message_id)
        target.append(message)
        if len(target) >= limit:
            return


def build_search_scan_limit(limit: int) -> int:
    return min(max(limit * 60, 800), SEARCH_LOCAL_SCAN_CAP)


def serialize_search_result(
    message: Message,
    source_value: str | int,
    source_label: str,
    rules: list[str],
    default_targets: list[str | int],
    default_bot_targets: list[str | int],
    default_forward_strategy: str = "",
) -> dict[str, Any]:
    preview = (message.raw_text or "").strip()
    if not preview and message.media:
        preview = "[媒体消息]"
    if not preview:
        preview = "[无文本内容]"
    if len(preview) > 320:
        preview = preview[:317] + "..."

    dt = message.date or datetime.min
    timestamp = dt.timestamp() if message.date else 0.0
    return {
        "source_chat": str(source_value),
        "source_label": source_label,
        "message_id": int(message.id),
        "preview": preview,
        "date": dt.isoformat() if message.date else "",
        "timestamp": timestamp,
        "has_media": bool(message.media),
        "link": build_message_link(message),
        "rules": rules,
        "default_target_chats": ",".join(str(item) for item in default_targets),
        "default_bot_target_chats": ",".join(str(item) for item in default_bot_targets),
        "default_forward_strategy": str(default_forward_strategy or "").strip(),
    }


def append_unique_targets(target_list: list[str | int], targets: list[ForwardTarget]) -> None:
    seen = {str(item) for item in target_list}
    for target in targets:
        key = str(target.chat)
        if key in seen:
            continue
        seen.add(key)
        target_list.append(target.chat)


async def build_user_client(settings: TelegramSettings) -> TelegramClient:
    def client_builder(proxy: object | None) -> TelegramClient:
        return _build_user_client_instance(settings, proxy)

    return await connect_client_with_proxy_pool(
        settings=settings,
        client_builder=client_builder,
        logger=LOGGER,
        scope="dashboard user client",
    )


def _build_user_client_instance(settings: TelegramSettings, proxy: object | None) -> TelegramClient:
    if settings.session_string:
        session = StringSession(settings.session_string)
    elif settings.session_file:
        session = settings.session_file
    else:
        raise ConfigError("dashboard session is not configured")
    return build_telegram_client(
        session=session,
        api_id=settings.api_id,
        api_hash=settings.api_hash,
        device_model="TGForwarderDashboard",
        app_version="0.1.0",
        receive_updates=False,
        proxy=proxy,
    )


async def build_bot_client(settings: TelegramSettings, bot_token: str) -> TelegramClient:
    def client_builder(proxy: object | None) -> TelegramClient:
        return build_telegram_client(
            session=MemorySession(),
            api_id=settings.api_id,
            api_hash=settings.api_hash,
            device_model="TGForwarderDashboardBot",
            app_version="0.1.0",
            receive_updates=False,
            proxy=proxy,
        )

    return await start_bot_client_with_proxy_pool(
        settings=settings,
        bot_token=bot_token,
        client_builder=client_builder,
        logger=LOGGER,
        scope="dashboard bot client",
    )


async def resolve_targets_partial(
    client: TelegramClient,
    targets: list[ForwardTarget],
    logger: logging.Logger,
    bot_label: str,
    *,
    log_scope: str,
) -> dict[str, object]:
    resolved: dict[str, object] = {}
    for target in targets:
        try:
            resolved[str(target.chat)] = await client.get_input_entity(target.chat)
        except Exception:
            logger.warning(
                "%s cannot access target=%s in %s, skip this target for current bot",
                bot_label,
                target.chat,
                log_scope,
            )
    return resolved


async def build_bot_forward_contexts(
    settings: TelegramSettings,
    source_reference: str | int,
    targets: list[ForwardTarget],
    logger: logging.Logger,
    *,
    log_scope: str,
) -> list[BotForwardContext]:
    contexts: list[BotForwardContext] = []
    for index, bot_token in enumerate(settings.bot_tokens, start=1):
        label = f"bot#{index}"
        client: TelegramClient | None = None
        try:
            client = await build_bot_client(settings, bot_token)
            source_entity = await safe_get_entity(client, source_reference)
            target_entities = await resolve_targets_partial(
                client,
                targets,
                logger,
                label,
                log_scope=log_scope,
            )
            if not target_entities:
                logger.warning("%s initialized in %s but has no accessible targets, skip", label, log_scope)
                with suppress(Exception):
                    await client.disconnect()
                continue
            contexts.append(
                BotForwardContext(
                    label=label,
                    client=client,
                    source_entity=source_entity,
                    target_entities=target_entities,
                )
            )
        except Exception:
            logger.exception("failed to initialize %s in %s", label, log_scope)
            if client is not None:
                with suppress(Exception):
                    await client.disconnect()
    return contexts


async def close_bot_forward_contexts(contexts: list[BotForwardContext]) -> None:
    for context in contexts:
        with suppress(Exception):
            await context.client.disconnect()


async def resolve_targets(
    client: TelegramClient,
    targets: list[ForwardTarget],
) -> list[tuple[ForwardTarget, object]]:
    resolved: list[tuple[ForwardTarget, object]] = []
    for target in targets:
        resolved.append((target, await client.get_input_entity(target.chat)))
    return resolved


async def safe_get_entity(client: TelegramClient, source: str | int) -> object | None:
    try:
        return await client.get_input_entity(source)
    except Exception:
        return None


def parse_manual_targets(value: str, field_name: str) -> list[ForwardTarget]:
    if not value.strip():
        return []
    items = parse_list_value(value)
    if not items:
        return []
    return [ForwardTarget(chat=parse_chat_reference(item, field_name)) for item in items]


def build_source_label(entity: object, fallback: str) -> str:
    title = getattr(entity, "title", None)
    if title:
        return str(title)
    username = getattr(entity, "username", None)
    if username:
        return f"@{username}"
    try:
        display_name = get_display_name(entity)
    except Exception:
        display_name = ""
    return display_name or fallback


def normalize_search_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def build_search_terms(query: str) -> list[str]:
    normalized_query = normalize_search_text(query)
    terms: list[str] = []
    if normalized_query:
        terms.append(normalized_query)
    for part in SEARCH_TERM_SPLIT_PATTERN.split(str(query or "").strip()):
        normalized_part = normalize_search_text(part)
        if normalized_part and normalized_part not in terms:
            terms.append(normalized_part)
    return terms


async def message_matches_search_query(
    message: Message,
    normalized_query: str,
    search_terms: list[str],
) -> bool:
    haystacks = build_message_search_haystacks(extract_message_keyword_values(message))
    if not haystacks:
        return False

    if normalized_query and any(normalized_query in haystack for haystack in haystacks):
        return True

    partial_terms = [term for term in search_terms if term]
    if partial_terms and any(term in haystack for term in partial_terms for haystack in haystacks):
        return True

    if normalized_query and any(is_subsequence_match(normalized_query, haystack) for haystack in haystacks):
        return True

    return False


def build_message_search_haystacks(raw_values: list[str]) -> list[str]:
    haystacks: list[str] = []
    for raw_value in raw_values:
        normalized = normalize_search_text(str(raw_value or ""))
        if normalized and normalized not in haystacks:
            haystacks.append(normalized)
    return haystacks


def is_subsequence_match(query: str, text: str) -> bool:
    if not query:
        return False
    index = 0
    for char in text:
        if char == query[index]:
            index += 1
            if index == len(query):
                return True
    return False
