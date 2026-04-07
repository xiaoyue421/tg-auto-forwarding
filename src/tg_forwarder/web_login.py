from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from time import monotonic
from uuid import uuid4

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

from tg_forwarder import __version__ as TG_FORWARDER_VERSION
from tg_forwarder.config import ConfigError, ProxyConfig, TelegramSettings
from tg_forwarder.telegram_clients import build_telegram_client, connect_client_with_proxy_pool

DEFAULT_LOGIN_TTL_SECONDS = 15 * 60


@dataclass(slots=True)
class PendingWebLogin:
    login_id: str
    client: TelegramClient
    phone: str
    expires_at: float
    password_required: bool = False


class TelegramWebLoginManager:
    def __init__(self, ttl_seconds: int = DEFAULT_LOGIN_TTL_SECONDS) -> None:
        self._ttl_seconds = ttl_seconds
        self._sessions: dict[str, PendingWebLogin] = {}
        self._lock = asyncio.Lock()

    async def request_code(
        self,
        api_id: str | int,
        api_hash: str,
        phone: str,
        proxy_pool: list[ProxyConfig] | None = None,
    ) -> dict[str, str]:
        await self._cleanup_expired()

        normalized_api_id = parse_api_id(api_id)
        normalized_api_hash = normalize_required_text(api_hash, "web login requires api_hash")
        normalized_phone = normalize_required_text(phone, "web login requires phone")

        settings = TelegramSettings(
            api_id=normalized_api_id,
            api_hash=normalized_api_hash,
            proxies=list(proxy_pool or []),
        )
        client = await connect_client_with_proxy_pool(
            settings=settings,
            client_builder=lambda proxy: build_telegram_client(
                session=StringSession(),
                api_id=normalized_api_id,
                api_hash=normalized_api_hash,
                proxy=proxy,
                receive_updates=False,
                device_model="TGForwarderWebLogin",
                app_version=TG_FORWARDER_VERSION,
            ),
            scope="web login client",
        )
        try:
            await client.send_code_request(normalized_phone)
        except Exception:
            with suppress(Exception):
                await client.disconnect()
            raise

        login_id = uuid4().hex
        session = PendingWebLogin(
            login_id=login_id,
            client=client,
            phone=normalized_phone,
            expires_at=self._expires_at(),
        )
        async with self._lock:
            self._sessions[login_id] = session
        return {
            "status": "code_sent",
            "login_id": login_id,
            "phone": normalized_phone,
        }

    async def complete(
        self,
        login_id: str,
        code: str = "",
        password: str = "",
    ) -> dict[str, str]:
        await self._cleanup_expired()
        session = await self._get_session(login_id)

        if session.password_required:
            normalized_password = normalize_required_text(password, "web login requires password")
            await session.client.sign_in(password=normalized_password)
        else:
            normalized_code = normalize_login_code(code)
            try:
                await session.client.sign_in(phone=session.phone, code=normalized_code)
            except SessionPasswordNeededError:
                session.password_required = True
                session.expires_at = self._expires_at()
                return {
                    "status": "password_required",
                    "login_id": session.login_id,
                    "phone": session.phone,
                }

        if not await session.client.is_user_authorized():
            raise ConfigError("telegram login did not complete")

        session_string = session.client.session.save()
        await self._disconnect_session(session.login_id)
        return {
            "status": "completed",
            "login_id": session.login_id,
            "phone": session.phone,
            "session_string": session_string,
        }

    async def cancel(self, login_id: str) -> dict[str, str]:
        normalized_login_id = str(login_id or "").strip()
        if not normalized_login_id:
            return {"status": "cancelled"}
        await self._disconnect_session(normalized_login_id)
        return {"status": "cancelled", "login_id": normalized_login_id}

    async def close(self) -> None:
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            with suppress(Exception):
                await session.client.disconnect()

    async def _get_session(self, login_id: str) -> PendingWebLogin:
        normalized_login_id = str(login_id or "").strip()
        if not normalized_login_id:
            raise ConfigError("web login session not found")

        async with self._lock:
            session = self._sessions.get(normalized_login_id)
            if session is None:
                raise ConfigError("web login session not found")
            session.expires_at = self._expires_at()
            return session

    async def _disconnect_session(self, login_id: str) -> None:
        async with self._lock:
            session = self._sessions.pop(login_id, None)
        if session is not None:
            with suppress(Exception):
                await session.client.disconnect()

    async def _cleanup_expired(self) -> None:
        expired_ids: list[str] = []
        now = monotonic()
        async with self._lock:
            for login_id, session in self._sessions.items():
                if session.expires_at <= now:
                    expired_ids.append(login_id)

        for login_id in expired_ids:
            await self._disconnect_session(login_id)

    def _expires_at(self) -> float:
        return monotonic() + self._ttl_seconds


def parse_api_id(value: str | int) -> int:
    if isinstance(value, int):
        api_id = value
    else:
        normalized = str(value or "").strip()
        if not normalized:
            raise ConfigError("web login requires api_id")
        try:
            api_id = int(normalized)
        except ValueError as exc:
            raise ConfigError("web login api_id must be an integer") from exc
    if api_id <= 0:
        raise ConfigError("web login api_id must be an integer")
    return api_id


def normalize_required_text(value: str, error_message: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ConfigError(error_message)
    return normalized


def normalize_login_code(value: str) -> str:
    normalized = "".join(str(value or "").split())
    if not normalized:
        raise ConfigError("web login requires code")
    return normalized
