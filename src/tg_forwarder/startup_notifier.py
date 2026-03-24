from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
from pathlib import Path

from telethon import TelegramClient

from tg_forwarder import __version__
from tg_forwarder.config import (
    AppConfig,
    ConfigError,
    FORWARD_STRATEGY_ACCOUNT_ONLY,
    FORWARD_STRATEGY_ACCOUNT_FIRST,
    FORWARD_STRATEGY_BOT_ONLY,
    FORWARD_STRATEGY_BOT_FIRST,
    FORWARD_STRATEGY_PARALLEL,
    ForwardTarget,
    TelegramSettings,
    normalize_forward_strategy,
    load_config,
)
from tg_forwarder.dashboard_actions import build_bot_client, build_user_client


STARTUP_NOTIFY_BOT_INIT_TIMEOUT_SECONDS = 25


DEFAULT_STARTUP_NOTIFY_MESSAGE = f"""<b>TelegramForwarder 已成功启动</b>

<b>欢迎使用 TelegramForwarder！</b>

当前规则已经加载完成，系统开始实时监听并准备转发新消息。

如果这个项目对您有帮助，欢迎支持：
[Star] <a href="https://github.com/Heavrnl/TelegramForwarder">项目点个小小的 Star</a>
[Ko-fi] <a href="https://ko-fi.com/heavrnl">请我喝杯咖啡</a>

当前版本：v{__version__}
更新日志：<a href="https://github.com/Heavrnl/TelegramForwarder/releases">/changelog</a>

感谢您的支持！

GitHub:
https://github.com/Heavrnl/TelegramForwarder
"""


def _format_exception_brief(exc: BaseException) -> str:
    message = str(exc).strip()
    if message:
        return f"{exc.__class__.__name__}: {message}"
    return exc.__class__.__name__


def send_startup_notifications(
    config_path: str | Path,
    logger: logging.Logger | None = None,
) -> dict[str, int | bool]:
    resolved_logger = logger or logging.getLogger("tg_forwarder.startup_notifier")
    config = load_config(config_path)
    return asyncio.run(_send_startup_notifications(config, resolved_logger))


async def _send_startup_notifications(
    config: AppConfig,
    logger: logging.Logger,
) -> dict[str, int | bool]:
    if not config.telegram.startup_notify_enabled:
        logger.info("startup notification disabled")
        return {
            "enabled": False,
            "account_targets": 0,
            "bot_targets": 0,
            "account_sent": 0,
            "bot_sent": 0,
        }

    runtime_workers = config.build_runtime_workers()
    account_targets = collect_unique_targets(runtime_workers, use_bot_targets=False)
    bot_targets = collect_unique_targets(runtime_workers, use_bot_targets=True)
    if not account_targets and not bot_targets:
        logger.info("startup notification skipped: no target chats configured")
        return {
            "enabled": True,
            "account_targets": 0,
            "bot_targets": 0,
            "account_sent": 0,
            "bot_sent": 0,
        }

    message = (config.telegram.startup_notify_message or "").strip() or DEFAULT_STARTUP_NOTIFY_MESSAGE
    strategy = normalize_forward_strategy(
        config.telegram.forward_strategy,
        "telegram.forward_strategy",
    )
    account_sent, bot_sent = await send_startup_notifications_with_strategy(
        config=config,
        account_targets=account_targets,
        bot_targets=bot_targets,
        message=message,
        logger=logger,
        strategy=strategy,
    )
    logger.info(
        "startup notification finished, strategy=%s, account_sent=%s/%s, bot_sent=%s/%s",
        strategy,
        account_sent,
        len(account_targets),
        bot_sent,
        len(bot_targets),
    )
    return {
        "enabled": True,
        "strategy": strategy,
        "account_targets": len(account_targets),
        "bot_targets": len(bot_targets),
        "account_sent": account_sent,
        "bot_sent": bot_sent,
    }


async def send_startup_notifications_with_strategy(
    *,
    config: AppConfig,
    account_targets: list[ForwardTarget],
    bot_targets: list[ForwardTarget],
    message: str,
    logger: logging.Logger,
    strategy: str,
) -> tuple[int, int]:
    if strategy == FORWARD_STRATEGY_PARALLEL:
        account_task = asyncio.create_task(
            send_account_notifications(
                settings=config.telegram,
                targets=account_targets,
                message=message,
                logger=logger,
            )
        )
        bot_task = asyncio.create_task(
            send_bot_notifications(
                config=config,
                targets=bot_targets,
                message=message,
                logger=logger,
            )
        )
        return await asyncio.gather(account_task, bot_task)

    if strategy == FORWARD_STRATEGY_ACCOUNT_ONLY:
        account_sent = await send_account_notifications(
            settings=config.telegram,
            targets=account_targets,
            message=message,
            logger=logger,
        )
        return account_sent, 0

    if strategy == FORWARD_STRATEGY_BOT_ONLY:
        bot_sent = await send_bot_notifications(
            config=config,
            targets=bot_targets,
            message=message,
            logger=logger,
        )
        return 0, bot_sent

    if strategy == FORWARD_STRATEGY_BOT_FIRST:
        bot_sent = await send_bot_notifications(
            config=config,
            targets=bot_targets,
            message=message,
            logger=logger,
        )
        if bot_sent > 0 or not account_targets:
            return 0, bot_sent
        logger.info("startup notification bot-first fallback to account targets")
        account_sent = await send_account_notifications(
            settings=config.telegram,
            targets=account_targets,
            message=message,
            logger=logger,
        )
        return account_sent, bot_sent

    account_sent = await send_account_notifications(
        settings=config.telegram,
        targets=account_targets,
        message=message,
        logger=logger,
    )
    if account_sent > 0 or not bot_targets:
        return account_sent, 0
    logger.info("startup notification account-first fallback to bot targets")
    bot_sent = await send_bot_notifications(
        config=config,
        targets=bot_targets,
        message=message,
        logger=logger,
    )
    return account_sent, bot_sent


def collect_unique_targets(
    runtime_workers: list,
    *,
    use_bot_targets: bool,
) -> list[ForwardTarget]:
    unique_targets: list[ForwardTarget] = []
    seen: set[str] = set()
    for worker in runtime_workers:
        targets = worker.bot_targets if use_bot_targets else worker.targets
        for target in targets:
            key = str(target.chat)
            if key in seen:
                continue
            seen.add(key)
            unique_targets.append(target)
    return unique_targets


async def send_account_notifications(
    *,
    settings: TelegramSettings,
    targets: list[ForwardTarget],
    message: str,
    logger: logging.Logger,
) -> int:
    if not targets:
        return 0

    sent = 0
    client: TelegramClient | None = None
    try:
        client = await build_user_client(settings)
        if not await client.is_user_authorized():
            logger.warning("startup notification skipped for account targets: session is not authorized")
            return 0
        for target in targets:
            if await send_notification_message(client, target, message, logger, target_kind="账号"):
                sent += 1
    except ConfigError as exc:
        logger.warning("startup notification skipped for account targets: %s", exc)
    except Exception:
        logger.exception("failed while sending startup notifications to account targets")
    finally:
        if client is not None:
            with suppress(Exception):
                await client.disconnect()
    return sent


async def send_bot_notifications(
    *,
    config: AppConfig,
    targets: list[ForwardTarget],
    message: str,
    logger: logging.Logger,
) -> int:
    if not targets:
        return 0

    bot_tokens = config.telegram.bot_tokens
    if not bot_tokens:
        logger.warning("startup notification skipped for bot targets: TG_BOT_TOKEN is missing")
        return 0

    sent = 0
    bot_clients: list[tuple[str, TelegramClient]] = []
    try:
        for index, bot_token in enumerate(bot_tokens, start=1):
            label = f"bot#{index}"
            try:
                bot_client = await asyncio.wait_for(
                    build_bot_client(config.telegram, bot_token),
                    timeout=STARTUP_NOTIFY_BOT_INIT_TIMEOUT_SECONDS,
                )
                bot_clients.append((label, bot_client))
            except Exception as exc:
                logger.warning(
                    "startup notification skip %s: %s",
                    label,
                    _format_exception_brief(exc),
                )
        for target in targets:
            if await send_notification_message_via_bots(bot_clients, target, message, logger):
                sent += 1
    except Exception:
        logger.exception("failed while sending startup notifications to bot targets")
    finally:
        for _label, bot_client in bot_clients:
            with suppress(Exception):
                await bot_client.disconnect()
    return sent


async def send_notification_message_via_bots(
    bot_clients: list[tuple[str, TelegramClient]],
    target: ForwardTarget,
    message: str,
    logger: logging.Logger,
) -> bool:
    if not bot_clients:
        return False

    last_index = len(bot_clients) - 1
    for index, (label, bot_client) in enumerate(bot_clients):
        success = await send_notification_message(
            bot_client,
            target,
            message,
            logger,
            target_kind=f"Bot({label})",
        )
        if success:
            return True
        if index < last_index:
            logger.warning(
                "startup notification failed via %s target=%s, trying next bot",
                label,
                target.chat,
            )
    return False


async def send_notification_message(
    client: TelegramClient,
    target: ForwardTarget,
    message: str,
    logger: logging.Logger,
    *,
    target_kind: str,
) -> bool:
    try:
        entity = await client.get_input_entity(target.chat)
        await client.send_message(
            entity=entity,
            message=message,
            parse_mode="html",
            link_preview=True,
            silent=target.silent,
        )
        logger.info("startup notification sent via %s target=%s", target_kind, target.chat)
        return True
    except Exception as exc:
        logger.warning(
            "startup notification failed via %s target=%s: %s",
            target_kind,
            target.chat,
            exc,
        )
        return False
