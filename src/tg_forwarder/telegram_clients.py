from __future__ import annotations

from contextlib import suppress
import logging
from typing import Callable

from telethon import TelegramClient

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

    for index, proxy in enumerate(proxy_pool, start=1):
        client = client_builder(proxy)
        try:
            await client.start(bot_token=bot_token)
            if logger is not None and total > 1:
                logger.info(
                    "%s started via proxy #%s/%s",
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
