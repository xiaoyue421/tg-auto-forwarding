from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hashlib
import io
import logging

from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError
from telethon.tl import functions
from telethon.tl.custom.message import Message

from tg_forwarder.config import (
    FORWARD_STRATEGY_ACCOUNT_ONLY,
    FORWARD_STRATEGY_ACCOUNT_FIRST,
    FORWARD_STRATEGY_BOT_ONLY,
    FORWARD_STRATEGY_BOT_FIRST,
    FORWARD_STRATEGY_PARALLEL,
    ForwardTarget,
    normalize_forward_strategy,
)
from tg_forwarder.monitoring import ForwardLogContext, monitor_log


@dataclass(slots=True)
class ForwardDispatchResult:
    strategy: str
    account_success_count: int = 0
    bot_success_count: int = 0
    attempted_account: bool = False
    attempted_bot: bool = False
    account_results: list["TargetDispatchResult"] = field(default_factory=list)
    bot_results: list["TargetDispatchResult"] = field(default_factory=list)


@dataclass(slots=True)
class TargetDispatchResult:
    channel: str
    target_chat: str | int
    success: bool
    error_message: str | None = None
    via_label: str | None = None


@dataclass(slots=True)
class BotForwardContext:
    label: str
    client: TelegramClient
    source_entity: object | None
    target_entities: dict[str, object]


async def forward_with_strategy(
    *,
    strategy: str,
    user_client: TelegramClient,
    bot_contexts: list[BotForwardContext],
    message: Message,
    account_targets: list[tuple[ForwardTarget, object]],
    bot_targets: list[ForwardTarget],
    logger: logging.Logger,
    log_context: ForwardLogContext | None = None,
) -> ForwardDispatchResult:
    normalized_strategy = normalize_forward_strategy(strategy, "telegram.forward_strategy")
    result = ForwardDispatchResult(strategy=normalized_strategy)

    async def run_account() -> int:
        result.attempted_account = bool(account_targets)
        if not account_targets:
            return 0
        result.account_results = await forward_to_targets_detailed(
            user_client,
            message,
            account_targets,
            logger,
            log_context=log_context,
        )
        return sum(1 for item in result.account_results if item.success)

    async def run_bot() -> int:
        result.attempted_bot = bool(bot_targets)
        if not bot_targets:
            return 0
        result.bot_results = await forward_to_bot_targets_detailed(
            user_client,
            bot_contexts,
            message,
            bot_targets,
            logger,
            log_context=log_context,
        )
        return sum(1 for item in result.bot_results if item.success)

    if normalized_strategy == FORWARD_STRATEGY_PARALLEL:
        account_task = asyncio.create_task(run_account())
        bot_task = asyncio.create_task(run_bot())
        result.account_success_count, result.bot_success_count = await asyncio.gather(
            account_task,
            bot_task,
        )
        return result

    if normalized_strategy == FORWARD_STRATEGY_ACCOUNT_ONLY:
        result.account_success_count = await run_account()
        return result

    if normalized_strategy == FORWARD_STRATEGY_BOT_ONLY:
        result.bot_success_count = await run_bot()
        return result

    if normalized_strategy == FORWARD_STRATEGY_BOT_FIRST:
        result.bot_success_count = await run_bot()
        if result.bot_success_count > 0 or not account_targets:
            return result
        logger.info(
            "bot-first strategy fallback to account targets, message_id=%s",
            message.id,
            extra={"monitor": True},
        )
        result.account_success_count = await run_account()
        return result

    result.account_success_count = await run_account()
    if result.account_success_count > 0 or not bot_targets:
        return result
    logger.info(
        "account-first strategy fallback to bot targets, message_id=%s",
        message.id,
        extra={"monitor": True},
    )
    result.bot_success_count = await run_bot()
    return result


async def forward_to_targets(
    client: TelegramClient,
    message: Message,
    targets: list[tuple[ForwardTarget, object]],
    logger: logging.Logger,
    log_context: ForwardLogContext | None = None,
    idempotency_keys: dict[str, str] | None = None,
) -> int:
    results = await forward_to_targets_detailed(
        client,
        message,
        targets,
        logger,
        log_context=log_context,
        idempotency_keys=idempotency_keys,
    )
    return sum(1 for item in results if item.success)


async def forward_to_targets_detailed(
    client: TelegramClient,
    message: Message,
    targets: list[tuple[ForwardTarget, object]],
    logger: logging.Logger,
    log_context: ForwardLogContext | None = None,
    idempotency_keys: dict[str, str] | None = None,
) -> list[TargetDispatchResult]:
    tasks = [
        asyncio.create_task(
            _forward_to_single_target(
                client,
                message,
                target,
                entity,
                logger,
                log_context,
                (idempotency_keys or {}).get(str(target.chat)),
            )
        )
        for target, entity in targets
    ]
    if not tasks:
        return []
    return await asyncio.gather(*tasks)


async def forward_to_bot_targets(
    user_client: TelegramClient,
    bot_contexts: list[BotForwardContext],
    message: Message,
    targets: list[ForwardTarget],
    logger: logging.Logger,
    log_context: ForwardLogContext | None = None,
    idempotency_keys: dict[str, str] | None = None,
) -> int:
    results = await forward_to_bot_targets_detailed(
        user_client,
        bot_contexts,
        message,
        targets,
        logger,
        log_context=log_context,
        idempotency_keys=idempotency_keys,
    )
    return sum(1 for item in results if item.success)


async def forward_to_bot_targets_detailed(
    user_client: TelegramClient,
    bot_contexts: list[BotForwardContext],
    message: Message,
    targets: list[ForwardTarget],
    logger: logging.Logger,
    log_context: ForwardLogContext | None = None,
    idempotency_keys: dict[str, str] | None = None,
) -> list[TargetDispatchResult]:
    if not bot_contexts or not targets:
        return []
    tasks = [
        asyncio.create_task(
            _forward_to_single_bot_target_chain(
                user_client=user_client,
                bot_contexts=bot_contexts,
                message=message,
                target=target,
                logger=logger,
                log_context=log_context,
                idempotency_key=(idempotency_keys or {}).get(str(target.chat)),
            )
        )
        for target in targets
    ]
    if not tasks:
        return []
    return await asyncio.gather(*tasks)


async def _forward_to_single_target(
    client: TelegramClient,
    message: Message,
    target: ForwardTarget,
    entity: object,
    logger: logging.Logger,
    log_context: ForwardLogContext | None,
    idempotency_key: str | None = None,
) -> TargetDispatchResult:
    try:
        await _forward_with_retry(
            client=client,
            entity=entity,
            message=message,
            target=target,
            logger=logger,
            log_context=log_context,
            idempotency_key=idempotency_key,
        )
        return TargetDispatchResult(channel="account", target_chat=target.chat, success=True)
    except RPCError as exc:
        monitor_log(
            logger,
            logging.ERROR,
            "账号转发失败",
            message=message,
            target=target.chat,
            context=log_context,
        )
        logger.exception("forward failed message_id=%s -> %s", message.id, target.chat)
        return TargetDispatchResult(
            channel="account",
            target_chat=target.chat,
            success=False,
            error_message=_format_dispatch_exception(exc),
        )
    except Exception as exc:
        monitor_log(
            logger,
            logging.ERROR,
            "账号转发异常",
            message=message,
            target=target.chat,
            context=log_context,
        )
        logger.exception(
            "unexpected forward failure message_id=%s -> %s",
            message.id,
            target.chat,
        )
        return TargetDispatchResult(
            channel="account",
            target_chat=target.chat,
            success=False,
            error_message=_format_dispatch_exception(exc),
        )


async def _forward_to_single_bot_target(
    user_client: TelegramClient,
    bot_client: TelegramClient,
    message: Message,
    bot_source_entity: object | None,
    target: ForwardTarget,
    entity: object,
    logger: logging.Logger,
    log_context: ForwardLogContext | None,
    idempotency_key: str | None = None,
) -> tuple[bool, str | None]:
    last_error: str | None = None
    if bot_source_entity is not None:
        try:
            await _bot_forward_with_retry(
                bot_client=bot_client,
                entity=entity,
                source_entity=bot_source_entity,
                message_id=message.id,
                target=target,
                logger=logger,
                log_context=log_context,
                message=message,
                idempotency_key=idempotency_key,
            )
            return True, None
        except RPCError as exc:
            monitor_log(
                logger,
                logging.WARNING,
                "Bot 直转失败，改用复制发送",
                message=message,
                target=target.chat,
                context=log_context,
            )
            logger.warning(
                "bot forward failed for message_id=%s -> %s, fallback to bot copy",
                message.id,
                target.chat,
            )
            last_error = _format_dispatch_exception(exc)
        except Exception as exc:
            monitor_log(
                logger,
                logging.WARNING,
                "Bot 直转异常，改用复制发送",
                message=message,
                target=target.chat,
                context=log_context,
            )
            logger.warning(
                "bot forward unexpected failure for message_id=%s -> %s, fallback to bot copy",
                message.id,
                target.chat,
            )
            last_error = _format_dispatch_exception(exc)

    try:
        await _copy_message_via_bot(
            user_client=user_client,
            bot_client=bot_client,
            message=message,
            entity=entity,
            target=target,
            logger=logger,
            log_context=log_context,
            idempotency_key=idempotency_key,
        )
        return True, None
    except RPCError as exc:
        monitor_log(
            logger,
            logging.ERROR,
            "Bot 复制发送失败",
            message=message,
            target=target.chat,
            context=log_context,
        )
        logger.exception("bot copy failed message_id=%s -> %s", message.id, target.chat)
        return False, _format_dispatch_exception(exc)
    except Exception as exc:
        monitor_log(
            logger,
            logging.ERROR,
            "Bot 复制发送异常",
            message=message,
            target=target.chat,
            context=log_context,
        )
        logger.exception(
            "unexpected bot copy failure message_id=%s -> %s",
            message.id,
            target.chat,
        )
        return False, _format_dispatch_exception(exc) or last_error


async def _forward_to_single_bot_target_chain(
    user_client: TelegramClient,
    bot_contexts: list[BotForwardContext],
    message: Message,
    target: ForwardTarget,
    logger: logging.Logger,
    log_context: ForwardLogContext | None,
    idempotency_key: str | None = None,
) -> TargetDispatchResult:
    last_index = len(bot_contexts) - 1
    target_key = str(target.chat)
    attempted = False
    last_error: str | None = None
    last_label: str | None = None

    for index, context in enumerate(bot_contexts):
        entity = context.target_entities.get(target_key)
        if entity is None:
            continue
        attempted = True
        success, error_message = await _forward_to_single_bot_target(
            user_client=user_client,
            bot_client=context.client,
            message=message,
            bot_source_entity=context.source_entity,
            target=target,
            entity=entity,
            logger=logger,
            log_context=log_context,
            idempotency_key=idempotency_key,
        )
        if success:
            if index > 0:
                logger.info(
                    "bot fallback succeeded via %s for message_id=%s -> %s",
                    context.label,
                    message.id,
                    target.chat,
                    extra={"monitor": True},
                )
            return TargetDispatchResult(
                channel="bot",
                target_chat=target.chat,
                success=True,
                via_label=context.label,
            )
        last_error = error_message or last_error
        last_label = context.label
        if index < last_index:
            logger.warning(
                "bot forward failed via %s for message_id=%s -> %s, trying next bot",
                context.label,
                message.id,
                target.chat,
                extra={"monitor": True},
            )

    if attempted:
        return TargetDispatchResult(
            channel="bot",
            target_chat=target.chat,
            success=False,
            error_message=last_error or "bot forward failed",
            via_label=last_label,
        )

    monitor_log(
        logger,
        logging.ERROR,
        "Bot 鐩爣涓嶅彲鐢?",
        message=message,
        target=target.chat,
        context=log_context,
    )
    logger.warning(
        "no available bot context for target=%s message_id=%s",
        target.chat,
        message.id,
    )
    return TargetDispatchResult(
        channel="bot",
        target_chat=target.chat,
        success=False,
        error_message="no available bot context",
    )


async def _forward_with_retry(
    client: TelegramClient,
    entity: object,
    message: Message,
    target: ForwardTarget,
    logger: logging.Logger,
    log_context: ForwardLogContext | None,
    idempotency_key: str | None = None,
) -> None:
    random_id = _build_stable_random_id("account-forward", idempotency_key) if idempotency_key else None
    try:
        if random_id is None:
            await client.forward_messages(
                entity=entity,
                messages=message,
                silent=target.silent,
                drop_author=target.drop_author,
                drop_media_captions=target.drop_media_captions,
            )
        else:
            await _forward_messages_idempotent(
                client=client,
                entity=entity,
                source_entity=await _get_message_source_entity(message),
                message_id=int(message.id),
                target=target,
                random_id=random_id,
            )
        monitor_log(
            logger,
            logging.INFO,
            "账号转发成功",
            message=message,
            target=target.chat,
            context=log_context,
        )
    except FloodWaitError as exc:
        logger.warning(
            "FloodWait for %s seconds before retry, target=%s",
            exc.seconds,
            target.chat,
            extra={"monitor": True},
        )
        await asyncio.sleep(exc.seconds)
        if random_id is None:
            await client.forward_messages(
                entity=entity,
                messages=message,
                silent=target.silent,
                drop_author=target.drop_author,
                drop_media_captions=target.drop_media_captions,
            )
        else:
            await _forward_messages_idempotent(
                client=client,
                entity=entity,
                source_entity=await _get_message_source_entity(message),
                message_id=int(message.id),
                target=target,
                random_id=random_id,
            )
        monitor_log(
            logger,
            logging.INFO,
            "账号重试后转发成功",
            message=message,
            target=target.chat,
            context=log_context,
        )


async def _bot_forward_with_retry(
    bot_client: TelegramClient,
    entity: object,
    source_entity: object,
    message_id: int,
    target: ForwardTarget,
    logger: logging.Logger,
    log_context: ForwardLogContext | None,
    message: Message,
    idempotency_key: str | None = None,
) -> None:
    random_id = _build_stable_random_id("bot-forward", idempotency_key) if idempotency_key else None
    try:
        if random_id is None:
            await bot_client.forward_messages(
                entity=entity,
                messages=message_id,
                from_peer=source_entity,
                silent=target.silent,
                drop_author=target.drop_author,
                drop_media_captions=target.drop_media_captions,
            )
        else:
            await _forward_messages_idempotent(
                client=bot_client,
                entity=entity,
                source_entity=source_entity,
                message_id=message_id,
                target=target,
                random_id=random_id,
            )
        monitor_log(
            logger,
            logging.INFO,
            "Bot 直转成功",
            message=message,
            target=target.chat,
            context=log_context,
        )
    except FloodWaitError as exc:
        logger.warning(
            "Bot FloodWait for %s seconds before retry, target=%s",
            exc.seconds,
            target.chat,
            extra={"monitor": True},
        )
        await asyncio.sleep(exc.seconds)
        if random_id is None:
            await bot_client.forward_messages(
                entity=entity,
                messages=message_id,
                from_peer=source_entity,
                silent=target.silent,
                drop_author=target.drop_author,
                drop_media_captions=target.drop_media_captions,
            )
        else:
            await _forward_messages_idempotent(
                client=bot_client,
                entity=entity,
                source_entity=source_entity,
                message_id=message_id,
                target=target,
                random_id=random_id,
            )
        monitor_log(
            logger,
            logging.INFO,
            "Bot 重试后直转成功",
            message=message,
            target=target.chat,
            context=log_context,
        )


async def _copy_message_via_bot(
    user_client: TelegramClient,
    bot_client: TelegramClient,
    message: Message,
    entity: object,
    target: ForwardTarget,
    logger: logging.Logger,
    log_context: ForwardLogContext | None,
    idempotency_key: str | None = None,
) -> None:
    text = (message.raw_text or "").strip()
    buttons = build_copy_buttons_markup(message)
    message_entities = getattr(message, "entities", None)
    caption = None if target.drop_media_captions else (text or None)
    copied_message_text = text or " "
    caption_entities = None if target.drop_media_captions else message_entities

    if message.media:
        media_bytes = await user_client.download_media(message, file=bytes)
        if media_bytes:
            file_obj = io.BytesIO(media_bytes)
            file_obj.name = build_media_filename(message)
            await _send_file_via_bot_with_markup_fallback(
                bot_client=bot_client,
                entity=entity,
                file_obj=file_obj,
                caption=caption,
                silent=target.silent,
                buttons=buttons,
                entities=caption_entities,
                logger=logger,
                message=message,
                target=target,
                idempotency_key=idempotency_key,
            )
            monitor_log(
                logger,
                logging.INFO,
                "Bot 复制媒体成功",
                message=message,
                target=target.chat,
                context=log_context,
            )
            return

    await _send_message_via_bot_with_markup_fallback(
        bot_client=bot_client,
        entity=entity,
        text=copied_message_text,
        silent=target.silent,
        link_preview=bool(getattr(message, "web_preview", None)),
        buttons=buttons,
        entities=message_entities,
        logger=logger,
        message=message,
        target=target,
        idempotency_key=idempotency_key,
    )
    monitor_log(
        logger,
        logging.INFO,
        "Bot 复制文本成功",
        message=message,
        target=target.chat,
        context=log_context,
    )


async def _send_file_via_bot_with_markup_fallback(
    *,
    bot_client: TelegramClient,
    entity: object,
    file_obj: io.BytesIO,
    caption: str | None,
    silent: bool,
    buttons: object | None,
    entities: list[object] | None,
    logger: logging.Logger,
    message: Message,
    target: ForwardTarget,
    idempotency_key: str | None = None,
) -> None:
    random_id = _build_stable_random_id("bot-copy", idempotency_key) if idempotency_key else None
    try:
        if random_id is None:
            await bot_client.send_file(
                entity=entity,
                file=file_obj,
                caption=caption,
                silent=silent,
                buttons=buttons,
            )
        else:
            await _send_media_request_idempotent(
                bot_client=bot_client,
                entity=entity,
                file_obj=file_obj,
                caption=caption,
                silent=silent,
                buttons=buttons,
                entities=entities,
                random_id=random_id,
            )
    except Exception:
        if buttons is None:
            raise
        logger.warning(
            "bot copy media with buttons failed for message_id=%s -> %s, retrying without buttons",
            message.id,
            target.chat,
            extra={"monitor": True},
        )
        file_obj.seek(0)
        if random_id is None:
            await bot_client.send_file(
                entity=entity,
                file=file_obj,
                caption=caption,
                silent=silent,
            )
        else:
            await _send_media_request_idempotent(
                bot_client=bot_client,
                entity=entity,
                file_obj=file_obj,
                caption=caption,
                silent=silent,
                buttons=None,
                entities=entities,
                random_id=random_id,
            )


async def _send_message_via_bot_with_markup_fallback(
    *,
    bot_client: TelegramClient,
    entity: object,
    text: str,
    silent: bool,
    link_preview: bool,
    buttons: object | None,
    entities: list[object] | None,
    logger: logging.Logger,
    message: Message,
    target: ForwardTarget,
    idempotency_key: str | None = None,
) -> None:
    random_id = _build_stable_random_id("bot-copy", idempotency_key) if idempotency_key else None
    try:
        if random_id is None:
            await bot_client.send_message(
                entity=entity,
                message=text,
                silent=silent,
                link_preview=link_preview,
                buttons=buttons,
            )
        else:
            await _send_message_request_idempotent(
                client=bot_client,
                entity=entity,
                text=text,
                silent=silent,
                link_preview=link_preview,
                buttons=buttons,
                entities=entities,
                random_id=random_id,
            )
    except Exception:
        if buttons is None:
            raise
        logger.warning(
            "bot copy text with buttons failed for message_id=%s -> %s, retrying without buttons",
            message.id,
            target.chat,
            extra={"monitor": True},
        )
        if random_id is None:
            await bot_client.send_message(
                entity=entity,
                message=text,
                silent=silent,
                link_preview=link_preview,
            )
        else:
            await _send_message_request_idempotent(
                client=bot_client,
                entity=entity,
                text=text,
                silent=silent,
                link_preview=link_preview,
                buttons=None,
                entities=entities,
                random_id=random_id,
            )


def build_copy_buttons_markup(message: Message) -> object | None:
    return getattr(message, "reply_markup", None)


def build_media_filename(message: Message) -> str:
    document = getattr(message, "document", None)
    if document and getattr(document, "mime_type", None):
        mime_type = document.mime_type
        if "/" in mime_type:
            extension = mime_type.split("/", 1)[1]
            if extension:
                extension = extension.replace("jpeg", "jpg")
                return f"message_{message.id}.{extension}"
    if getattr(message, "photo", None):
        return f"message_{message.id}.jpg"
    if getattr(message, "video", None):
        return f"message_{message.id}.mp4"
    return f"message_{message.id}.bin"


def build_message_link(message: Message) -> str | None:
    chat = getattr(message, "chat", None)
    username = getattr(chat, "username", None)
    if username:
        return f"https://t.me/{username}/{message.id}"

    peer_id = getattr(message, "peer_id", None)
    channel_id = getattr(peer_id, "channel_id", None)
    if channel_id:
        return f"https://t.me/c/{channel_id}/{message.id}"
    return None


def _format_dispatch_exception(exc: Exception) -> str:
    message = str(exc).strip()
    if message:
        return message
    return exc.__class__.__name__


def _normalize_random_id_part(value: object) -> str:
    if value is None:
        return "<none>"
    if isinstance(value, bytes):
        return value.hex()
    text = str(value).strip()
    return text or "<empty>"


def _build_stable_random_id(*parts: object) -> int:
    payload = "|".join(_normalize_random_id_part(part) for part in parts).encode("utf-8", "surrogatepass")
    value = int.from_bytes(hashlib.blake2b(payload, digest_size=8).digest(), "big", signed=False)
    value &= (1 << 63) - 1
    return value or 1


async def _get_message_source_entity(message: Message) -> object:
    source_entity = getattr(message, "input_chat", None)
    if source_entity is None and hasattr(message, "get_input_chat"):
        source_entity = await message.get_input_chat()
    if source_entity is None:
        raise ValueError("source chat unavailable for idempotent forward")
    return source_entity


async def _forward_messages_idempotent(
    *,
    client: TelegramClient,
    entity: object,
    source_entity: object,
    message_id: int,
    target: ForwardTarget,
    random_id: int,
) -> None:
    await client(
        functions.messages.ForwardMessagesRequest(
            from_peer=source_entity,
            id=[int(message_id)],
            to_peer=entity,
            silent=target.silent,
            drop_author=target.drop_author,
            drop_media_captions=target.drop_media_captions,
            random_id=[int(random_id)],
        )
    )


async def _send_message_request_idempotent(
    *,
    client: TelegramClient,
    entity: object,
    text: str,
    silent: bool,
    link_preview: bool,
    buttons: object | None,
    entities: list[object] | None,
    random_id: int,
) -> None:
    await client(
        functions.messages.SendMessageRequest(
            peer=entity,
            message=text,
            no_webpage=not link_preview,
            silent=silent,
            random_id=int(random_id),
            reply_markup=client.build_reply_markup(buttons),
            entities=entities,
        )
    )


async def _send_media_request_idempotent(
    *,
    bot_client: TelegramClient,
    entity: object,
    file_obj: io.BytesIO,
    caption: str | None,
    silent: bool,
    buttons: object | None,
    entities: list[object] | None,
    random_id: int,
) -> None:
    file_obj.seek(0)
    _, media, _ = await bot_client._file_to_media(file_obj)
    if not media:
        raise TypeError(f"Cannot use {file_obj!r} as file")
    await bot_client(
        functions.messages.SendMediaRequest(
            peer=entity,
            media=media,
            message=caption or "",
            silent=silent,
            random_id=int(random_id),
            reply_markup=bot_client.build_reply_markup(buttons),
            entities=entities,
        )
    )
