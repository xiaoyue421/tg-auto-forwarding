from __future__ import annotations

import asyncio
import base64
import io
from contextlib import suppress
from dataclasses import dataclass
import logging
from time import monotonic
from typing import Any
from uuid import uuid4

import segno
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

from tg_forwarder import __version__ as TG_FORWARDER_VERSION
from tg_forwarder.config import ConfigError, ProxyConfig, TelegramSettings
from tg_forwarder.telegram_clients import (
    build_telegram_client,
    connect_client_with_proxy_pool,
    disconnect_telegram_client,
)

DEFAULT_LOGIN_TTL_SECONDS = 15 * 60


def encode_qr_url_to_png_base64(url: str) -> str:
    buf = io.BytesIO()
    segno.make(url).save(buf, kind="png", scale=5, border=2)
    return base64.b64encode(buf.getvalue()).decode("ascii")


@dataclass(slots=True)
class PendingWebLogin:
    login_id: str
    client: TelegramClient
    phone: str
    expires_at: float
    password_required: bool = False
    mode: str = "phone"
    qr_handle: Any = None
    qr_wait_task: asyncio.Task | None = None
    qr_outcome: str | None = None
    qr_session_string: str | None = None
    qr_error_message: str | None = None


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
            await disconnect_telegram_client(
                client,
                logger=logging.getLogger("tg_forwarder.web_login"),
                scope="web login client (after send_code_request)",
            )
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

    async def request_qr(
        self,
        api_id: str | int,
        api_hash: str,
        proxy_pool: list[ProxyConfig] | None = None,
    ) -> dict[str, str]:
        """Start Telegram QR login (scan with mobile Telegram: Settings → Devices → Link Desktop Device)."""
        await self._cleanup_expired()

        normalized_api_id = parse_api_id(api_id)
        normalized_api_hash = normalize_required_text(api_hash, "web login requires api_hash")

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
            qr = await client.qr_login()
        except Exception:
            await disconnect_telegram_client(
                client,
                logger=logging.getLogger("tg_forwarder.web_login"),
                scope="web login client (after qr_login)",
            )
            raise

        login_id = uuid4().hex
        png_b64 = encode_qr_url_to_png_base64(qr.url)
        session = PendingWebLogin(
            login_id=login_id,
            client=client,
            phone="",
            expires_at=self._expires_at(),
            mode="qr",
            qr_handle=qr,
            qr_outcome="pending",
        )
        session.qr_wait_task = asyncio.create_task(self._qr_wait_worker(login_id))
        async with self._lock:
            self._sessions[login_id] = session
        return {
            "status": "qr_ready",
            "login_id": login_id,
            "qr_url": qr.url,
            "qr_png_base64": png_b64,
            "expires_at": qr.expires.isoformat(),
        }

    async def refresh_qr(self, login_id: str) -> dict[str, str]:
        await self._cleanup_expired()
        session = await self._get_session(login_id)
        if session.mode != "qr" or not session.qr_handle:
            raise ConfigError("web login session is not a qr flow")
        if session.password_required:
            raise ConfigError("web login qr cannot refresh while password is required")

        if session.qr_wait_task and not session.qr_wait_task.done():
            session.qr_wait_task.cancel()
            with suppress(asyncio.CancelledError):
                await session.qr_wait_task

        await session.qr_handle.recreate()
        session.qr_outcome = "pending"
        session.qr_session_string = None
        session.qr_error_message = None
        session.expires_at = self._expires_at()
        session.qr_wait_task = asyncio.create_task(self._qr_wait_worker(login_id))
        png_b64 = encode_qr_url_to_png_base64(session.qr_handle.url)
        return {
            "status": "qr_ready",
            "login_id": session.login_id,
            "qr_url": session.qr_handle.url,
            "qr_png_base64": png_b64,
            "expires_at": session.qr_handle.expires.isoformat(),
        }

    async def qr_status(self, login_id: str) -> dict[str, Any]:
        await self._cleanup_expired()
        session = await self._get_session(login_id)
        if session.mode != "qr":
            raise ConfigError("web login session is not a qr flow")

        if session.password_required or session.qr_outcome == "password_required":
            return {
                "status": "password_required",
                "login_id": session.login_id,
            }
        if session.qr_outcome == "completed" and session.qr_session_string:
            session_string = session.qr_session_string
            await self._disconnect_session(session.login_id)
            return {
                "status": "completed",
                "login_id": login_id,
                "session_string": session_string,
            }
        if session.qr_outcome == "expired":
            return {"status": "expired", "login_id": session.login_id}
        if session.qr_outcome == "error":
            return {
                "status": "error",
                "login_id": session.login_id,
                "message": session.qr_error_message or "unknown error",
            }
        return {"status": "waiting", "login_id": session.login_id}

    async def _qr_wait_worker(self, login_id: str) -> None:
        async with self._lock:
            session = self._sessions.get(login_id)
            qr = session.qr_handle if session else None
        if not session or not qr:
            return
        try:
            await qr.wait()
        except asyncio.CancelledError:
            raise
        except SessionPasswordNeededError:
            async with self._lock:
                s = self._sessions.get(login_id)
                if s:
                    s.password_required = True
                    s.qr_outcome = "password_required"
            return
        except asyncio.TimeoutError:
            async with self._lock:
                s = self._sessions.get(login_id)
                if s:
                    s.qr_outcome = "expired"
            return
        except Exception as exc:
            async with self._lock:
                s = self._sessions.get(login_id)
                if s:
                    s.qr_outcome = "error"
                    s.qr_error_message = str(exc)
            return

        async with self._lock:
            s = self._sessions.get(login_id)
        if not s:
            return
        if not await s.client.is_user_authorized():
            async with self._lock:
                s2 = self._sessions.get(login_id)
                if s2:
                    s2.qr_outcome = "error"
                    s2.qr_error_message = "telegram login did not complete"
            return
        session_string = s.client.session.save()
        async with self._lock:
            s3 = self._sessions.get(login_id)
            if s3:
                s3.qr_session_string = session_string
                s3.qr_outcome = "completed"

    async def complete(
        self,
        login_id: str,
        code: str = "",
        password: str = "",
    ) -> dict[str, str]:
        await self._cleanup_expired()
        session = await self._get_session(login_id)

        if session.mode == "qr" and not session.password_required:
            raise ConfigError("web login qr does not accept phone codes")

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
        log = logging.getLogger("tg_forwarder.web_login")
        for session in sessions:
            if session.qr_wait_task and not session.qr_wait_task.done():
                session.qr_wait_task.cancel()
                with suppress(asyncio.CancelledError):
                    await session.qr_wait_task
            await disconnect_telegram_client(session.client, logger=log, scope="web login session (shutdown)")

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
        if session is None:
            return
        if session.qr_wait_task and not session.qr_wait_task.done():
            session.qr_wait_task.cancel()
            with suppress(asyncio.CancelledError):
                await session.qr_wait_task
        await disconnect_telegram_client(
            session.client,
            logger=logging.getLogger("tg_forwarder.web_login"),
            scope="web login session",
        )

    async def _cleanup_expired(self) -> None:
        expired_ids: list[str] = []
        now = monotonic()
        async with self._lock:
            for lid, session in self._sessions.items():
                if session.expires_at <= now:
                    expired_ids.append(lid)

        for lid in expired_ids:
            await self._disconnect_session(lid)

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
