from __future__ import annotations

import asyncio
import logging
import os
from contextlib import suppress
from typing import Callable

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.errors.rpcerrorlist import UserMigrateError

from tg_forwarder.config import ProxyConfig, TelegramSettings

def build_telegram_client(
    *,
    session: object,
    api_id: int,
    api_hash: str,
    device_model: str,
    app_version: str,
    receive_updates: bool,
    proxy: ProxyConfig | dict | None = None,
) -> TelegramClient:
    client_kwargs: dict[str, object] = {}
    if proxy is not None:
        if isinstance(proxy, ProxyConfig):
            client_kwargs["proxy"] = proxy.to_telethon_proxy()
        else:
            client_kwargs["proxy"] = proxy
    return TelegramClient(
        session=session,
        api_id=api_id,
        api_hash=api_hash,
        device_model=device_model,
        app_version=app_version,
        receive_updates=receive_updates,
        **client_kwargs,
    )


def build_proxy_pool_from_settings(
    settings: TelegramSettings,
) -> list[ProxyConfig | None]:
    proxies = settings.build_proxy_pool()
    if not proxies:
        return [None]
    return [proxies[0]]


async def connect_client_with_proxy_pool(
    *,
    settings: TelegramSettings,
    client_builder: Callable[[ProxyConfig | None], TelegramClient],
    logger: logging.Logger | None = None,
    scope: str = "telegram client",
) -> TelegramClient:
    last_error: Exception | None = None
    proxy_pool = build_proxy_pool_from_settings(settings)
    total = len(proxy_pool)

    for index, proxy in enumerate(proxy_pool, start=1):
        client = client_builder(proxy)
        try:
            await client.connect()
            if logger is not None and total > 1:
                logger.info(
                    "%s connected via proxy #%s/%s",
                    scope,
                    index,
                    total,
                    extra={"monitor": True},
                )
            return client
        except Exception as exc:
            last_error = exc
            if logger is not None and total > 1:
                logger.warning(
                    "%s failed via proxy #%s/%s: %s",
                    scope,
                    index,
                    total,
                    exc.__class__.__name__,
                    extra={"monitor": True},
                )
            with suppress(Exception):
                await client.disconnect()

    if last_error is not None:
        raise last_error
    raise RuntimeError(f"{scope} has no available proxy candidates")


def _bot_floodwait_max_sleep_seconds() -> float:
    raw = (os.getenv("TG_BOT_FLOODWAIT_MAX_SLEEP_SECONDS") or "0").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 0.0


def _bot_start_retries_per_proxy() -> int:
    raw = (os.getenv("TG_BOT_START_RETRIES_PER_PROXY") or "3").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 3


async def start_bot_client_with_proxy_pool(
    *,
    settings: TelegramSettings,
    bot_token: str,
    client_builder: Callable[[ProxyConfig | None], TelegramClient],
    logger: logging.Logger | None = None,
    scope: str = "telegram bot client",
) -> TelegramClient:
    last_error: Exception | None = None
    proxy_pool = build_proxy_pool_from_settings(settings)
    total = len(proxy_pool)
    max_flood_sleep = _bot_floodwait_max_sleep_seconds()
    start_retries = _bot_start_retries_per_proxy()

    async def _try_start_once(cli: TelegramClient) -> None:
        await cli.start(bot_token=bot_token)

    for index, proxy in enumerate(proxy_pool, start=1):
        for attempt in range(1, start_retries + 1):
            client = client_builder(proxy)
            try:
                await _try_start_once(client)
                if logger is not None and (total > 1 or start_retries > 1):
                    logger.info(
                        "%s started via proxy #%s/%s (attempt %s/%s)",
                        scope,
                        index,
                        total,
                        attempt,
                        start_retries,
                        extra={"monitor": True},
                    )
                return client
            except FloodWaitError as exc:
                wait_s = int(getattr(exc, "seconds", 0) or 0)
                last_error = exc
                with suppress(Exception):
                    await client.disconnect()
                if max_flood_sleep > 0 and wait_s > 0:
                    sleep_for = min(float(wait_s) + 1.5, max_flood_sleep)
                    if logger is not None:
                        logger.warning(
                            "%s FloodWait %ss on proxy #%s/%s, sleep %.1fs then retry (%s/%s)",
                            scope,
                            wait_s,
                            index,
                            total,
                            sleep_for,
                            attempt,
                            start_retries,
                            extra={"monitor": True},
                        )
                    await asyncio.sleep(sleep_for)
                    continue
                if logger is not None:
                    logger.warning(
                        "%s FloodWait %ss on proxy #%s/%s and no auto-wait configured",
                        scope,
                        wait_s,
                        index,
                        total,
                        extra={"monitor": True},
                    )
                break
            except (UserMigrateError, asyncio.IncompleteReadError, ConnectionError, OSError) as exc:
                last_error = exc
                with suppress(Exception):
                    await client.disconnect()
                if logger is not None:
                    logger.warning(
                        "%s transient start failure via proxy #%s/%s (%s), retry %s/%s",
                        scope,
                        index,
                        total,
                        exc.__class__.__name__,
                        attempt,
                        start_retries,
                        extra={"monitor": True},
                    )
                if attempt < start_retries:
                    await asyncio.sleep(min(1.2 * attempt, 3.0))
                    continue
            except Exception as exc:
                last_error = exc
                with suppress(Exception):
                    await client.disconnect()
                if logger is not None and (total > 1 or start_retries > 1):
                    logger.warning(
                        "%s failed via proxy #%s/%s (%s), attempt %s/%s",
                        scope,
                        index,
                        total,
                        exc.__class__.__name__,
                        attempt,
                        start_retries,
                        extra={"monitor": True},
                    )
            break

    if last_error is not None:
        if isinstance(last_error, FloodWaitError) and logger is not None:
            wait_s = int(getattr(last_error, "seconds", 0) or 0)
            if max_flood_sleep <= 0 and wait_s > 0:
                logger.error(
                    "%s: Telegram requires ~%ss before bot login (FloodWait). "
                    "Set TG_BOT_FLOODWAIT_MAX_SLEEP_SECONDS to at least %s to auto-wait and retry, "
                    "or increase TG_BOT_POOL_START_STAGGER_SECONDS / avoid frequent restarts.",
                    scope,
                    wait_s,
                    wait_s + 5,
                    extra={"monitor": True},
                )
            elif max_flood_sleep > 0 and wait_s > max_flood_sleep:
                logger.error(
                    "%s: FloodWait %ss is longer than TG_BOT_FLOODWAIT_MAX_SLEEP_SECONDS=%s; "
                    "raise the cap (e.g. %s) or Telegram will keep rate-limiting.",
                    scope,
                    wait_s,
                    max_flood_sleep,
                    wait_s + 5,
                    extra={"monitor": True},
                )
        raise last_error
    raise RuntimeError(f"{scope} has no available proxy candidates")
