from __future__ import annotations

import asyncio
import errno
import inspect
import json
import logging
import os
import re
from dataclasses import asdict
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from telethon.errors import (
    ApiIdInvalidError,
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
)

from tg_forwarder.config import (
    CONTENT_MATCH_MODE_ALL,
    DEFAULT_RATE_LIMIT_DELAY_SECONDS,
    FORWARD_STRATEGY_PARALLEL,
    SEARCH_MODE_FAST,
    ConfigError,
    ProxyConfig,
    normalize_content_match_mode,
    normalize_forward_strategy,
    normalize_optional_forward_strategy,
    normalize_rate_limit_delay,
    normalize_regex_patterns,
    normalize_search_mode,
)
from tg_forwarder.control_service import SupervisorService
from tg_forwarder.dashboard_actions import (
    manual_forward_message_async,
    search_messages_async,
)
from tg_forwarder.dispatch_queue import (
    clear_dispatch_success_history,
    clear_failed_dispatch_jobs,
    count_dispatch_success_history,
    list_dispatch_success_history_rules,
    list_failed_dispatch_jobs,
    resolve_queue_db_path,
    retry_failed_dispatch_jobs_smart,
)
from tg_forwarder.env_utils import read_env_file, update_env_file
from tg_forwarder.hdhive_checkin import (
    hdhive_checkin_loop,
    load_checkin_state_for_env,
    normalize_hdhive_checkin_method,
    resolve_hdhive_proxy,
    run_hdhive_checkin,
)
from tg_forwarder.hdhive_cookie_refresh import (
    _network_hint_for_message,
    hdhive_cookie_refresh_loop,
    maybe_refresh_hdhive_cookie,
    persist_hdhive_cookie_from_response_headers,
)
from tg_forwarder.log_buffer import InMemoryLogHandler, create_dashboard_child_log_bridge
from tg_forwarder.modules.loader import load_hooks_module_file
from tg_forwarder.modules.registry import (
    MAX_MODULE_ZIP_BYTES,
    get_installed_module_directory,
    install_module_from_zip,
    list_installed_modules,
)
from tg_forwarder.modules.ui_runtime import (
    build_module_ui_file_response,
    enrich_modules_ui_metadata,
)
from tg_forwarder.startup_notifier import DEFAULT_STARTUP_NOTIFY_MESSAGE
from tg_forwarder.user_messages import translate_error
from tg_forwarder.web_auth import (
    SESSION_COOKIE_NAME,
    DashboardSessionStore,
    LoginRateLimiter,
)
from tg_forwarder.web_login import TelegramWebLoginManager

DEFAULT_DASHBOARD_PASSWORD = "admin"
LIST_SPLIT_PATTERN = re.compile(r"[,;\r\n]+")
MODULE_CONFIG_JSON_MAX_BYTES = 256 * 1024
_SENSITIVE_ENV_HINTS = ("PASSWORD", "TOKEN", "SECRET", "KEY", "COOKIE", "HASH", "SESSION")


def _log_hdhive_event(event_label: str, method: str, status: int, msg: str, desc: str) -> None:
    """写入内存日志（控制台「日志」页），不包含 Cookie / Key 原文。"""
    log = logging.getLogger("tg_forwarder.hdhive")
    m = (msg or "").strip()[:600]
    d = (desc or "").strip()[:600]
    text = (
        f"HDHive {event_label} | mode={method} | HTTP {status}"
        f" | message={m or '—'} | description={d or '—'}"
    )
    if status == 200:
        log.info(text)
    else:
        log.warning(text)


def _mask_sensitive_value(value: str) -> str:
    s = str(value or "")
    if not s:
        return ""
    if len(s) <= 8:
        return "***"
    return f"{s[:3]}***{s[-3:]}"


def _build_sanitized_env_snapshot(values: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in values.items():
        k = str(key or "").strip()
        v = str(value or "")
        if any(hint in k.upper() for hint in _SENSITIVE_ENV_HINTS):
            out[k] = _mask_sensitive_value(v)
        else:
            out[k] = v
    return out


class LoginPayload(BaseModel):
    password: str = ""


class HdhiveCheckinTestPayload(BaseModel):
    """测试签到：优先使用请求体中的字段；未填则回退 .env。Cookie 模式签到使用程序内置 Next 元数据。"""

    checkin_method: str = ""
    api_key: str = ""
    cookie: str = ""
    is_gambler: bool = False


class HdhiveResolveTestPayload(BaseModel):
    url: str = ""


class LogsClearPayload(BaseModel):
    source: str = ""
    kind: str = "all"


class SessionRequestCodePayload(BaseModel):
    api_id: str = ""
    api_hash: str = ""
    phone: str = ""
    proxy_type: str = "socks5"
    proxy_host: str = ""
    proxy_port: str = ""
    proxy_user: str = ""
    proxy_password: str = ""
    proxy_rdns: bool = True


class SessionRequestQrPayload(BaseModel):
    """Same proxy fields as request-code; no phone — login is completed by scanning the QR in Telegram."""

    api_id: str = ""
    api_hash: str = ""
    proxy_type: str = "socks5"
    proxy_host: str = ""
    proxy_port: str = ""
    proxy_user: str = ""
    proxy_password: str = ""
    proxy_rdns: bool = True


class SessionCompletePayload(BaseModel):
    login_id: str = ""
    code: str = ""
    password: str = ""


class SessionCancelPayload(BaseModel):
    login_id: str = ""


class RulePayload(BaseModel):
    name: str = ""
    enabled: bool = True
    group: str = ""
    priority: int = 100
    source_chat: str = ""
    target_chats: str = ""
    bot_target_chats: str = ""
    forward_strategy: str = "inherit"
    include_edits: bool = False
    forward_own_messages: bool = False
    keywords_any: str = ""
    keywords_all: str = ""
    block_keywords: str = ""
    regex_any: str = ""
    regex_all: str = ""
    regex_block: str = ""
    hdhive_resource_resolve_forward: bool = False
    hdhive_require_rule_match: bool = False
    media_only: bool = False
    text_only: bool = False
    content_match_mode: str = CONTENT_MATCH_MODE_ALL
    case_sensitive: bool = False

    @field_validator("content_match_mode")
    @classmethod
    def validate_content_match_mode(cls, value: str) -> str:
        return normalize_content_match_mode(value, "content_match_mode")

    @field_validator("forward_strategy")
    @classmethod
    def validate_forward_strategy(cls, value: str) -> str:
        return normalize_rule_forward_strategy_text(value)

    @field_validator(
        "regex_any",
        "regex_all",
        "regex_block",
    )
    @classmethod
    def validate_regex_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "regex")
        return normalize_regex_text(value, field_name)

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, value: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return 100
        return max(1, min(10_000, parsed))


class DashboardConfigPayload(BaseModel):
    api_id: str = ""
    api_hash: str = ""
    session_string: str = ""
    bot_token: str = ""
    forward_strategy: str = FORWARD_STRATEGY_PARALLEL
    rate_limit_protection: bool = False
    rate_limit_delay_seconds: float = Field(default=DEFAULT_RATE_LIMIT_DELAY_SECONDS, ge=0)
    startup_notify_enabled: bool = False
    startup_notify_message: str = ""
    proxy_type: str = "socks5"
    proxy_host: str = ""
    proxy_port: str = ""
    proxy_user: str = ""
    proxy_password: str = ""
    proxy_rdns: bool = True
    search_default_mode: str = SEARCH_MODE_FAST
    rules: list[RulePayload] = Field(default_factory=list)
    hdhive_checkin_method: str = "api_key"
    hdhive_api_key: str = ""
    hdhive_cookie: str = ""
    hdhive_checkin_enabled: bool = False
    hdhive_checkin_gambler: bool = False
    hdhive_checkin_use_proxy: bool = False
    hdhive_resource_unlock_enabled: bool = False
    hdhive_resource_unlock_max_points: int = Field(default=0, ge=0)
    hdhive_resource_unlock_threshold_inclusive: bool = True
    hdhive_resource_unlock_skip_unknown_points: bool = False
    hdhive_cookie_refresh_enabled: bool = False
    hdhive_cookie_refresh_interval_sec: int = Field(default=1800, ge=60, le=86400)

    @field_validator("hdhive_checkin_method")
    @classmethod
    def validate_hdhive_checkin_method(cls, value: str) -> str:
        return normalize_hdhive_checkin_method(value)

    @field_validator("forward_strategy")
    @classmethod
    def validate_forward_strategy(cls, value: str) -> str:
        return normalize_forward_strategy(value, "forward_strategy")

    @field_validator("rate_limit_delay_seconds")
    @classmethod
    def validate_rate_limit_delay_seconds(cls, value: float) -> float:
        return normalize_rate_limit_delay(value, "rate_limit_delay_seconds")

    @field_validator("search_default_mode")
    @classmethod
    def validate_search_default_mode(cls, value: str) -> str:
        return normalize_search_mode(value, "search_default_mode")


def _validate_hdhive_checkin_config(payload: DashboardConfigPayload) -> None:
    method = normalize_hdhive_checkin_method(payload.hdhive_checkin_method)
    if not payload.hdhive_checkin_enabled:
        return
    if method == "cookie":
        if not payload.hdhive_cookie.strip():
            raise HTTPException(
                status_code=400,
                detail="自动签到已开启，Cookie 模式需填写 HDHIVE_COOKIE（含 token=）。",
            )
        return
    if not payload.hdhive_api_key.strip():
        raise HTTPException(status_code=400, detail="自动签到已开启，API Key 模式需填写 HDHive API Key。")


class SearchPayload(BaseModel):
    query: str = ""
    limit: int = Field(default=30, ge=1, le=100)
    mode: str = ""

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("search query is required")
        return normalized

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            return ""
        return normalize_search_mode(normalized, "search.mode")


class ManualForwardPayload(BaseModel):
    source_chat: str = ""
    message_id: int
    target_chats: str = ""
    bot_target_chats: str = ""
    forward_strategy: str = ""
    # 来自消息搜索时传入「命中规则」列表；按顺序尝试，命中第一条规则后执行与自动转发相同的匹配与 HDHive 解析。
    rule_names: list[str] = Field(default_factory=list)

    @field_validator("forward_strategy")
    @classmethod
    def validate_forward_strategy(cls, value: str) -> str:
        normalized = normalize_optional_forward_strategy(value, "manual.forward_strategy")
        return normalized or ""


class SuccessHistoryClearPayload(BaseModel):
    rule_name: str = ""


class ModulePreviewPayload(BaseModel):
    text: str = Field(default="", max_length=32000)
    config: Any = None
    rule_name: str = Field(default="preview", max_length=128)
    run_http_probe: bool | None = None


class ApiResponse(BaseModel):
    ok: bool = True
    message: str = ""
    data: dict = Field(default_factory=dict)


def _dashboard_env_value(config_path: Path, key: str, default: str = "") -> str:
    values = read_env_file(config_path)
    return (values.get(key) or os.getenv(key) or default).strip()


def _dashboard_session_ttl_seconds(config_path: Path) -> int:
    raw = _dashboard_env_value(config_path, "TG_DASHBOARD_SESSION_TTL_SECONDS", "43200")
    try:
        return max(300, int(raw))
    except ValueError:
        return 43200


def _dashboard_cookie_secure(config_path: Path) -> bool:
    v = _dashboard_env_value(config_path, "TG_DASHBOARD_COOKIE_SECURE", "").lower()
    return v in ("1", "true", "yes", "on")


def _dashboard_login_rate_config(config_path: Path) -> tuple[int, int]:
    max_attempts_raw = _dashboard_env_value(config_path, "TG_DASHBOARD_LOGIN_MAX_ATTEMPTS", "12")
    window_raw = _dashboard_env_value(config_path, "TG_DASHBOARD_LOGIN_WINDOW_SECONDS", "300")
    try:
        max_attempts = max(3, int(max_attempts_raw))
    except ValueError:
        max_attempts = 12
    try:
        window = max(30, int(window_raw))
    except ValueError:
        window = 300
    return max_attempts, window


def _resolve_dashboard_preview_fn(module_id: str, *, config_path: Path):
    base = get_installed_module_directory(module_id, config_path=config_path)
    if base is None:
        return None
    hooks_path = base / "hooks.py"
    if not hooks_path.is_file():
        return None
    safe_name = module_id.replace(".", "_")
    mod = load_hooks_module_file(hooks_path, safe_name)
    if mod is None:
        return None
    fn = getattr(mod, "dashboard_preview", None)
    if fn is None or not callable(fn):
        return None
    return fn


def _maybe_cors_middleware(app: FastAPI, config_path: Path) -> None:
    raw = _dashboard_env_value(config_path, "TG_DASHBOARD_CORS_ORIGINS", "")
    if not raw:
        return
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        return
    if parts == ["*"]:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
            allow_credentials=False,
        )
        return
    app.add_middleware(
        CORSMiddleware,
        allow_origins=parts,
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
    )


def build_web_app(config_path: str | Path = ".env") -> FastAPI:
    resolved_config_path = Path(config_path).resolve()
    log_handler = build_log_handler()
    child_log_queue, child_log_listener = create_dashboard_child_log_bridge(log_handler)
    child_log_listener.start()
    service = SupervisorService(resolved_config_path, child_log_queue=child_log_queue)
    login_manager = TelegramWebLoginManager()
    session_ttl_seconds = _dashboard_session_ttl_seconds(resolved_config_path)
    session_store = DashboardSessionStore(session_ttl_seconds)
    login_max, login_window = _dashboard_login_rate_config(resolved_config_path)
    login_rate_limiter = LoginRateLimiter(login_max, login_window)
    cookie_secure = _dashboard_cookie_secure(resolved_config_path)

    def queue_db_path() -> Path:
        return resolve_queue_db_path(resolved_config_path)

    def client_host(request: Request) -> str:
        if request.client and request.client.host:
            return request.client.host
        return "unknown"

    def ensure_authenticated(request: Request) -> None:
        token = request.cookies.get(SESSION_COOKIE_NAME)
        if session_store.validate(token):
            return
        provided_password = request.headers.get("x-dashboard-password", "")
        expected_password = get_dashboard_password(resolved_config_path)
        if provided_password == expected_password:
            return
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="请先输入控制台密码。",
        )

    app = FastAPI(title="TG 转发控制台")
    _maybe_cors_middleware(app, resolved_config_path)

    static_dir = Path(__file__).resolve().parent / "web" / "static"

    checkin_holder: list[object] = []

    @app.on_event("startup")
    async def _startup_hdhive_checkin() -> None:
        stop = asyncio.Event()
        log = logging.getLogger("tg_forwarder.hdhive")
        task_checkin = asyncio.create_task(hdhive_checkin_loop(stop, resolved_config_path, log))
        task_cookie = asyncio.create_task(hdhive_cookie_refresh_loop(stop, resolved_config_path, log))
        checkin_holder.clear()
        checkin_holder.extend([stop, task_checkin, task_cookie])

    @app.on_event("shutdown")
    async def shutdown_event() -> None:
        if checkin_holder:
            stop = checkin_holder[0]
            if isinstance(stop, asyncio.Event):
                stop.set()
            for item in checkin_holder[1:]:
                if isinstance(item, asyncio.Task) and not item.done():
                    item.cancel()
                    try:
                        await item
                    except asyncio.CancelledError:
                        pass
        child_log_listener.stop()
        await login_manager.close()

    @app.post("/api/login")
    def login(request: Request, response: Response, payload: LoginPayload) -> ApiResponse:
        ip = client_host(request)
        if login_rate_limiter.is_blocked(ip):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="登录尝试过多，请稍后再试。",
            )
        expected_password = get_dashboard_password(resolved_config_path)
        if payload.password != expected_password:
            login_rate_limiter.record_failure(ip)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="控制台密码错误。",
            )
        login_rate_limiter.reset(ip)
        token = session_store.create()
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=token,
            httponly=True,
            max_age=session_ttl_seconds,
            samesite="lax",
            secure=cookie_secure,
            path="/",
        )
        return ApiResponse(message="登录成功。")

    @app.post("/api/logout")
    def logout(request: Request, response: Response) -> ApiResponse:
        session_store.revoke(request.cookies.get(SESSION_COOKIE_NAME))
        response.delete_cookie(SESSION_COOKIE_NAME, path="/")
        return ApiResponse(message="已退出登录。")

    @app.get("/api/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/api/session/request-code")
    async def request_session_code(request: Request, payload: SessionRequestCodePayload) -> ApiResponse:
        ensure_authenticated(request)
        try:
            result = await login_manager.request_code(
                api_id=payload.api_id,
                api_hash=payload.api_hash,
                phone=payload.phone,
                proxy_pool=build_login_proxy_pool(payload),
            )
            return ApiResponse(message="验证码已发送，请在网页里继续输入验证码。", data=result)
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=translate_error(str(exc))) from exc
        except PhoneNumberInvalidError as exc:
            raise HTTPException(
                status_code=400,
                detail="手机号格式不正确，请使用国际区号格式，例如 +8613800000000。",
            ) from exc
        except ApiIdInvalidError as exc:
            raise HTTPException(
                status_code=400,
                detail="API ID 或 API HASH 不正确，请检查后重试。",
            ) from exc
        except FloodWaitError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"请求过于频繁，请等待 {exc.seconds} 秒后再试。",
            ) from exc

    @app.post("/api/session/complete")
    async def complete_session_login(request: Request, payload: SessionCompletePayload) -> ApiResponse:
        ensure_authenticated(request)
        try:
            result = await login_manager.complete(
                login_id=payload.login_id,
                code=payload.code,
                password=payload.password,
            )
            if result.get("status") == "password_required":
                return ApiResponse(
                    message="该账号开启了两步验证，请继续输入二步验证密码。",
                    data=result,
                )
            return ApiResponse(
                message="Telegram 登录成功，session_string 已生成。",
                data=result,
            )
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=translate_error(str(exc))) from exc
        except PhoneCodeInvalidError as exc:
            raise HTTPException(status_code=400, detail="验证码错误，请重新输入。") from exc
        except PhoneCodeExpiredError as exc:
            raise HTTPException(status_code=400, detail="验证码已过期，请重新发送验证码。") from exc
        except PasswordHashInvalidError as exc:
            raise HTTPException(status_code=400, detail="二步验证密码错误，请重新输入。") from exc
        except FloodWaitError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"请求过于频繁，请等待 {exc.seconds} 秒后再试。",
            ) from exc

    @app.post("/api/session/cancel")
    async def cancel_session_login(request: Request, payload: SessionCancelPayload) -> ApiResponse:
        ensure_authenticated(request)
        await login_manager.cancel(payload.login_id)
        return ApiResponse(message="已取消当前网页登录流程。")

    @app.post("/api/session/request-qr")
    async def request_session_qr(request: Request, payload: SessionRequestQrPayload) -> ApiResponse:
        ensure_authenticated(request)
        try:
            result = await login_manager.request_qr(
                api_id=payload.api_id,
                api_hash=payload.api_hash,
                proxy_pool=build_login_proxy_pool(payload),
            )
            return ApiResponse(
                message="二维码已生成。请用手机 Telegram：设置 → 设备 → 链接桌面设备，扫描网页上的二维码。",
                data=result,
            )
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=translate_error(str(exc))) from exc
        except ApiIdInvalidError as exc:
            raise HTTPException(
                status_code=400,
                detail="API ID 或 API HASH 不正确，请检查后重试。",
            ) from exc
        except FloodWaitError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"请求过于频繁，请等待 {exc.seconds} 秒后再试。",
            ) from exc

    @app.get("/api/session/qr-status")
    async def session_qr_status(request: Request, login_id: str = Query("")) -> ApiResponse:
        ensure_authenticated(request)
        try:
            result = await login_manager.qr_status(login_id)
            return ApiResponse(data=result)
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=translate_error(str(exc))) from exc

    @app.post("/api/session/qr-refresh")
    async def refresh_session_qr(request: Request, payload: SessionCancelPayload) -> ApiResponse:
        ensure_authenticated(request)
        try:
            result = await login_manager.refresh_qr(payload.login_id)
            return ApiResponse(message="已刷新二维码，请在手机上重新扫描。", data=result)
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=translate_error(str(exc))) from exc

    @app.get("/api/modules")
    def list_modules(request: Request) -> ApiResponse:
        ensure_authenticated(request)
        raw = list_installed_modules(config_path=resolved_config_path)
        items = enrich_modules_ui_metadata(raw, config_path=resolved_config_path)
        return ApiResponse(data={"items": items})

    @app.get("/api/modules/config/{module_id}")
    def get_module_config(request: Request, module_id: str) -> ApiResponse:
        ensure_authenticated(request)
        base = get_installed_module_directory(module_id, config_path=resolved_config_path)
        if base is None:
            raise HTTPException(status_code=404, detail="模块不存在或未安装。")
        cfg_path = base / "config.json"
        if not cfg_path.is_file():
            raise HTTPException(status_code=404, detail="该模块没有 config.json。")
        try:
            text = cfg_path.read_text(encoding="utf-8")
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"config.json 不是合法 JSON：{exc}",
            ) from exc
        return ApiResponse(data={"config": data})

    @app.put("/api/modules/config/{module_id}")
    async def put_module_config(request: Request, module_id: str) -> ApiResponse:
        ensure_authenticated(request)
        base = get_installed_module_directory(module_id, config_path=resolved_config_path)
        if base is None:
            raise HTTPException(status_code=404, detail="模块不存在或未安装。")
        cfg_path = base / "config.json"
        if not cfg_path.is_file():
            raise HTTPException(status_code=404, detail="该模块没有 config.json。")
        try:
            payload = await request.json()
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"请求体须为合法 JSON：{exc}") from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail="请求体须为 JSON。") from exc
        try:
            raw = json.dumps(payload, ensure_ascii=False, indent=2)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"无法序列化配置：{exc}") from exc
        encoded = raw.encode("utf-8")
        if len(encoded) > MODULE_CONFIG_JSON_MAX_BYTES:
            raise HTTPException(
                status_code=400,
                detail=f"配置超过 {MODULE_CONFIG_JSON_MAX_BYTES // 1024} KB。",
            )
        try:
            cfg_path.write_text(raw + "\n", encoding="utf-8")
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"写入失败：{exc}") from exc
        return ApiResponse(message="已保存。", data={"config": payload})

    @app.post("/api/modules/preview/{module_id}")
    async def module_preview(
        request: Request,
        module_id: str,
        payload: ModulePreviewPayload,
    ) -> ApiResponse:
        ensure_authenticated(request)
        preview_fn = _resolve_dashboard_preview_fn(module_id, config_path=resolved_config_path)
        if preview_fn is None:
            raise HTTPException(
                status_code=501,
                detail="该模块未安装、缺少 hooks.py，或未导出 dashboard_preview（不支持控制台试运行）。",
            )
        if payload.config is not None:
            try:
                raw_sz = len(json.dumps(payload.config, ensure_ascii=False).encode("utf-8"))
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="config 无法序列化。") from exc
            if raw_sz > MODULE_CONFIG_JSON_MAX_BYTES:
                raise HTTPException(
                    status_code=400,
                    detail=f"config 超过 {MODULE_CONFIG_JSON_MAX_BYTES // 1024} KB。",
                )
        body = payload.model_dump()
        try:
            if inspect.iscoroutinefunction(preview_fn):
                result = await preview_fn(body)
            else:
                result = await asyncio.to_thread(preview_fn, body)
        except Exception as exc:
            logging.getLogger("tg_forwarder.webapp").exception("module preview failed")
            raise HTTPException(status_code=500, detail=f"试运行失败：{exc}") from exc
        if not isinstance(result, dict):
            raise HTTPException(status_code=500, detail="模块 dashboard_preview 返回值无效。")
        return ApiResponse(data={"preview": result})

    @app.get("/api/modules/ui/{module_id}/{file_path:path}")
    def serve_module_ui(request: Request, module_id: str, file_path: str):
        ensure_authenticated(request)
        return build_module_ui_file_response(
            module_id=module_id,
            file_path=file_path,
            config_path=resolved_config_path,
        )

    @app.post("/api/modules/import")
    async def import_module_zip(
        request: Request,
        file: UploadFile = File(...),
        overwrite: str = Form("false"),
    ) -> ApiResponse:
        ensure_authenticated(request)
        name = (file.filename or "").strip().lower()
        if not name.endswith(".zip"):
            raise HTTPException(status_code=400, detail="请上传 .zip 格式的模块压缩包。")
        raw = await file.read()
        if len(raw) > MAX_MODULE_ZIP_BYTES:
            raise HTTPException(
                status_code=400,
                detail=f"文件超过 {MAX_MODULE_ZIP_BYTES // (1024 * 1024)} MB。",
            )
        ow = str(overwrite or "").strip().lower() in ("1", "true", "yes", "on")
        try:
            result = install_module_from_zip(
                raw,
                overwrite=ow,
                config_path=resolved_config_path,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"写入失败：{exc}") from exc
        return ApiResponse(
            message=f"已导入模块「{result['directory']}」。导入后若含 hooks.py，请重启转发 Worker。",
            data=result,
        )

    @app.get("/api/config")
    def get_config(request: Request) -> ApiResponse:
        ensure_authenticated(request)
        ensure_env_config_path(resolved_config_path)
        values = read_env_file(resolved_config_path)
        try:
            payload = DashboardConfigPayload(
                api_id=values.get("TG_API_ID", ""),
                api_hash=values.get("TG_API_HASH", ""),
                session_string=values.get("TG_SESSION_STRING", ""),
                bot_token=(values.get("TG_BOT_TOKEN", "") or values.get("TG_BOT_TOKENS", "")),
                forward_strategy=normalize_forward_strategy(
                    values.get("TG_FORWARD_STRATEGY"),
                    "TG_FORWARD_STRATEGY",
                ),
                rate_limit_protection=parse_bool_string(
                    values.get("TG_RATE_LIMIT_PROTECTION"),
                    default=False,
                ),
                rate_limit_delay_seconds=parse_float_string(
                    values.get("TG_RATE_LIMIT_DELAY_SECONDS"),
                    default=DEFAULT_RATE_LIMIT_DELAY_SECONDS,
                ),
                startup_notify_enabled=parse_bool_string(
                    values.get("TG_STARTUP_NOTIFY_ENABLED"),
                    default=False,
                ),
                startup_notify_message=values.get("TG_STARTUP_NOTIFY_MESSAGE", ""),
                proxy_type=values.get("TG_PROXY_TYPE", "socks5"),
                proxy_host=values.get("TG_PROXY_HOST", ""),
                proxy_port=values.get("TG_PROXY_PORT", ""),
                proxy_user=values.get("TG_PROXY_USER", ""),
                proxy_password=values.get("TG_PROXY_PASSWORD", ""),
                proxy_rdns=parse_bool_string(values.get("TG_PROXY_RDNS"), default=True),
                search_default_mode=normalize_search_mode(
                    values.get("TG_SEARCH_DEFAULT_MODE"),
                    "TG_SEARCH_DEFAULT_MODE",
                ),
                rules=load_rule_payloads(values),
                hdhive_checkin_method=normalize_hdhive_checkin_method(values.get("HDHIVE_CHECKIN_METHOD")),
                hdhive_api_key=(values.get("HDHIVE_API_KEY") or "").strip(),
                hdhive_cookie=(values.get("HDHIVE_COOKIE") or "").strip(),
                hdhive_checkin_enabled=parse_bool_string(values.get("HDHIVE_CHECKIN_ENABLED")),
                hdhive_checkin_gambler=parse_bool_string(values.get("HDHIVE_CHECKIN_GAMBLER")),
                hdhive_checkin_use_proxy=parse_bool_string(values.get("HDHIVE_CHECKIN_USE_PROXY")),
                hdhive_resource_unlock_enabled=parse_bool_string(values.get("HDHIVE_RESOURCE_UNLOCK_ENABLED")),
                hdhive_resource_unlock_max_points=parse_non_negative_int_string(
                    values.get("HDHIVE_RESOURCE_UNLOCK_MAX_POINTS"),
                    default=0,
                ),
                hdhive_resource_unlock_threshold_inclusive=parse_bool_string(
                    values.get("HDHIVE_RESOURCE_UNLOCK_THRESHOLD_INCLUSIVE"),
                    default=True,
                ),
                hdhive_resource_unlock_skip_unknown_points=parse_bool_string(
                    values.get("HDHIVE_RESOURCE_UNLOCK_SKIP_UNKNOWN_POINTS"),
                    default=False,
                ),
                hdhive_cookie_refresh_enabled=parse_bool_string(
                    values.get("HDHIVE_COOKIE_REFRESH_ENABLED"),
                    default=False,
                ),
                hdhive_cookie_refresh_interval_sec=max(
                    60,
                    min(
                        86400,
                        parse_non_negative_int_string(
                            values.get("HDHIVE_COOKIE_REFRESH_INTERVAL_SEC"),
                            default=1800,
                        )
                        or 1800,
                    ),
                ),
            )
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=translate_error(str(exc))) from exc
        return ApiResponse(
            data={
                "configPath": str(resolved_config_path),
                "defaultStartupNotifyMessage": DEFAULT_STARTUP_NOTIFY_MESSAGE,
                "config": payload.model_dump(),
            }
        )

    def persist_dashboard_config(request: Request, payload: DashboardConfigPayload) -> ApiResponse:
        ensure_authenticated(request)
        ensure_env_config_path(resolved_config_path)
        _validate_hdhive_checkin_config(payload)
        normalized_rules = payload.rules or [build_default_rule(1)]
        first_rule = normalized_rules[0]
        rules_json = serialize_rules_to_json(normalized_rules)
        values = {
            "TG_API_ID": payload.api_id.strip(),
            "TG_API_HASH": payload.api_hash.strip(),
            "TG_SESSION_STRING": payload.session_string.strip(),
            "TG_BOT_TOKEN": payload.bot_token.strip(),
            "TG_BOT_TOKENS": "",
            "TG_FORWARD_STRATEGY": payload.forward_strategy,
            "TG_RATE_LIMIT_PROTECTION": format_bool(payload.rate_limit_protection),
            "TG_RATE_LIMIT_DELAY_SECONDS": format_float(payload.rate_limit_delay_seconds),
            "TG_STARTUP_NOTIFY_ENABLED": format_bool(payload.startup_notify_enabled),
            "TG_STARTUP_NOTIFY_MESSAGE": quote_env_value(payload.startup_notify_message),
            "TG_PROXY_URLS": "",
            "TG_PROXY_LIST": "",
            "TG_SOURCE_CHAT": ",".join(split_list_value(first_rule.source_chat)),
            "TG_TARGET_CHATS": first_rule.target_chats.strip(),
            "TG_BOT_TARGET_CHATS": first_rule.bot_target_chats.strip(),
            "TG_WORKER_NAME": first_rule.name.strip() or "rule_1",
            "TG_INCLUDE_EDITS": format_bool(first_rule.include_edits),
            "TG_FORWARD_OWN_MESSAGES": format_bool(first_rule.forward_own_messages),
            "TG_KEYWORDS_ANY": first_rule.keywords_any.strip(),
            "TG_KEYWORDS_ALL": first_rule.keywords_all.strip(),
            "TG_BLOCK_KEYWORDS": first_rule.block_keywords.strip(),
            "TG_REGEX_ANY": quote_env_value(first_rule.regex_any),
            "TG_REGEX_ALL": quote_env_value(first_rule.regex_all),
            "TG_REGEX_BLOCK": quote_env_value(first_rule.regex_block),
            "TG_HDHIVE_RESOURCE_RESOLVE_FORWARD": format_bool(first_rule.hdhive_resource_resolve_forward),
            "TG_RESOURCE_PRESETS": quote_env_value(""),
            "TG_MEDIA_ONLY": format_bool(first_rule.media_only),
            "TG_TEXT_ONLY": format_bool(first_rule.text_only),
            "TG_CONTENT_MATCH_MODE": first_rule.content_match_mode,
            "TG_CASE_SENSITIVE": format_bool(first_rule.case_sensitive),
            "TG_PROXY_TYPE": payload.proxy_type.strip() or "socks5",
            "TG_PROXY_HOST": payload.proxy_host.strip(),
            "TG_PROXY_PORT": payload.proxy_port.strip(),
            "TG_PROXY_USER": payload.proxy_user.strip(),
            "TG_PROXY_PASSWORD": payload.proxy_password.strip(),
            "TG_PROXY_RDNS": format_bool(payload.proxy_rdns),
            "TG_SEARCH_DEFAULT_MODE": payload.search_default_mode,
            "HDHIVE_CHECKIN_METHOD": normalize_hdhive_checkin_method(payload.hdhive_checkin_method),
            "HDHIVE_API_KEY": quote_env_value(payload.hdhive_api_key.strip()),
            "HDHIVE_COOKIE": quote_env_value(payload.hdhive_cookie.strip()),
            "HDHIVE_NEXT_ACTION": None,
            "HDHIVE_NEXT_ROUTER_STATE_TREE": None,
            "HDHIVE_CHECKIN_ENABLED": format_bool(payload.hdhive_checkin_enabled),
            "HDHIVE_CHECKIN_GAMBLER": format_bool(payload.hdhive_checkin_gambler),
            "HDHIVE_CHECKIN_USE_PROXY": format_bool(payload.hdhive_checkin_use_proxy),
            "HDHIVE_RESOURCE_UNLOCK_ENABLED": format_bool(payload.hdhive_resource_unlock_enabled),
            "HDHIVE_RESOURCE_UNLOCK_MAX_POINTS": str(int(payload.hdhive_resource_unlock_max_points)),
            "HDHIVE_RESOURCE_UNLOCK_THRESHOLD_INCLUSIVE": format_bool(
                payload.hdhive_resource_unlock_threshold_inclusive
            ),
            "HDHIVE_RESOURCE_UNLOCK_SKIP_UNKNOWN_POINTS": format_bool(
                payload.hdhive_resource_unlock_skip_unknown_points
            ),
            "HDHIVE_COOKIE_REFRESH_ENABLED": format_bool(payload.hdhive_cookie_refresh_enabled),
            "HDHIVE_COOKIE_REFRESH_INTERVAL_SEC": str(int(payload.hdhive_cookie_refresh_interval_sec)),
            "TG_LANDING_PAGE_ENABLED": None,
            "TG_LANDING_PAGE_MATCH_ENABLED": None,
            "TG_LANDING_PAGE_HOSTS": None,
            "TG_LANDING_PAGE_EXTRACT_MODE": None,
            "TG_LANDING_PAGE_SECTION_MARKERS": None,
            "TG_LANDING_PAGE_SECTION_STOP_MARKERS": None,
            "TG_LANDING_PAGE_SECTION_FALLBACK_TO_FULL": None,
            "TG_RULES_JSON": quote_env_value(rules_json),
        }
        try:
            update_env_file(resolved_config_path, values)
        except OSError as exc:
            err_no = getattr(exc, "errno", None)
            readonly_codes = {errno.EACCES, errno.EPERM}
            if hasattr(errno, "EROFS"):
                readonly_codes.add(errno.EROFS)
            readonly_like = isinstance(exc, PermissionError) or err_no in readonly_codes
            detail = (
                "无法保存配置：无法写入 .env 文件。"
                " 若使用 Docker Compose，请确认没有把 `./.env` 挂载为只读（不要使用卷后缀 :ro）。"
            )
            if readonly_like:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"写入配置文件失败：{exc}",
            ) from exc
        return ApiResponse(message="配置已保存。")

    @app.put("/api/config")
    def save_config_put(request: Request, payload: DashboardConfigPayload) -> ApiResponse:
        return persist_dashboard_config(request, payload)

    @app.post("/api/config")
    def save_config_post(request: Request, payload: DashboardConfigPayload) -> ApiResponse:
        """与 PUT 相同；部分反向代理对 PUT 支持差，前端默认使用 POST。"""
        return persist_dashboard_config(request, payload)

    @app.post("/api/validate")
    def validate_config(request: Request) -> ApiResponse:
        ensure_authenticated(request)
        try:
            result = service.validate()
            return ApiResponse(message="配置校验通过。", data=result)
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=translate_error(str(exc))) from exc

    def _hdhive_checkin_health(values: dict[str, str]) -> dict[str, Any]:
        method = normalize_hdhive_checkin_method(values.get("HDHIVE_CHECKIN_METHOD"))
        enabled = parse_bool_string(values.get("HDHIVE_CHECKIN_ENABLED"))
        state = load_checkin_state_for_env(resolved_config_path)
        proxy, proxy_err = resolve_hdhive_proxy(values)
        return {
            "enabled": enabled,
            "method": method,
            "using_proxy": proxy is not None,
            "proxy_error": proxy_err or "",
            "last_success_date": str(state.get("last_success_date") or ""),
            "last_http_status": state.get("last_http_status"),
            "last_attempt_epoch": state.get("last_attempt_epoch"),
            "next_retry_epoch": state.get("next_retry_epoch"),
            "attempt_count_today": int(state.get("attempt_count_today") or 0),
            "retry_exhausted_date": str(state.get("retry_exhausted_date") or ""),
        }

    @app.get("/api/health")
    def get_health(request: Request) -> ApiResponse:
        ensure_authenticated(request)
        values = read_env_file(resolved_config_path)
        runtime_state = service.get_state()
        failed_has_items = bool(list_failed_dispatch_jobs(queue_db_path(), limit=1))
        success_total = count_dispatch_success_history(queue_db_path())
        return ApiResponse(
            data={
                "service": runtime_state.as_dict(),
                "logs": {
                    "in_memory_total": log_handler.total_record_count(),
                    "capacity": getattr(log_handler, "capacity", None),
                },
                "queue": {
                    "failed_has_items": failed_has_items,
                    "success_history_total": success_total,
                },
                "hdhive_checkin": _hdhive_checkin_health(values),
            }
        )

    @app.get("/api/v1/health")
    def get_health_v1(request: Request) -> ApiResponse:
        return get_health(request)

    @app.get("/api/diagnostics/export")
    def export_diagnostics(request: Request) -> ApiResponse:
        ensure_authenticated(request)
        values = read_env_file(resolved_config_path)
        health_payload = get_health(request).data
        failed_items = [asdict(item) for item in list_failed_dispatch_jobs(queue_db_path(), limit=30)]
        logs = log_handler.list_records(limit=300)
        now = datetime.now(timezone.utc)
        diagnostics = {
            "schema": "tg-forwarder.diagnostics.v1",
            "generated_at": now.isoformat(),
            "config_path": str(resolved_config_path),
            "env_sanitized": _build_sanitized_env_snapshot(values),
            "health": health_payload,
            "failed_queue_samples": failed_items,
            "recent_logs": logs,
        }
        filename = f"tg-forwarder-diagnostics-{now.strftime('%Y%m%d-%H%M%S')}.json"
        return ApiResponse(message="诊断包已生成。", data={"filename": filename, "diagnostics": diagnostics})

    @app.get("/api/status")
    def get_status(request: Request) -> ApiResponse:
        ensure_authenticated(request)
        return ApiResponse(data=service.get_state().as_dict())

    @app.get("/api/queue/failed")
    def get_failed_queue_items(
        request: Request,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> ApiResponse:
        ensure_authenticated(request)
        items = list_failed_dispatch_jobs(queue_db_path(), limit=limit)
        return ApiResponse(data={"items": [asdict(item) for item in items]})

    @app.post("/api/queue/retry-failed")
    def retry_failed_queue_items(request: Request) -> ApiResponse:
        ensure_authenticated(request)
        retry_result = retry_failed_dispatch_jobs_smart(queue_db_path())
        retried_count = retry_result.retried_count
        skipped_non_retryable = retry_result.skipped_non_retryable
        skipped_cooldown = retry_result.skipped_cooldown
        message = f"已重新放回队列 {retried_count} 个失败目标。"
        if skipped_non_retryable or skipped_cooldown:
            message += (
                f"（跳过不可重试 {skipped_non_retryable} 条，"
                f"冷却中 {skipped_cooldown} 条）"
            )
        return ApiResponse(
            message=message,
            data={
                "retried_count": retried_count,
                "skipped_non_retryable": skipped_non_retryable,
                "skipped_cooldown": skipped_cooldown,
            },
        )

    @app.post("/api/queue/clear-failed")
    def clear_failed_queue_items(request: Request) -> ApiResponse:
        ensure_authenticated(request)
        cleared_count = clear_failed_dispatch_jobs(queue_db_path())
        return ApiResponse(
            message=f"已清空 {cleared_count} 条失败任务。",
            data={"cleared_count": cleared_count},
        )

    @app.get("/api/queue/success-history/summary")
    def get_success_history_summary(request: Request) -> ApiResponse:
        ensure_authenticated(request)
        db_path = queue_db_path()
        total_count = count_dispatch_success_history(db_path)
        rules = list_dispatch_success_history_rules(db_path, limit=300)
        return ApiResponse(
            data={
                "total_count": total_count,
                "rules": [asdict(item) for item in rules],
            }
        )

    @app.post("/api/queue/clear-success-history")
    def clear_success_history(
        request: Request,
        payload: SuccessHistoryClearPayload,
    ) -> ApiResponse:
        ensure_authenticated(request)
        db_path = queue_db_path()
        normalized_rule_name = str(payload.rule_name or "").strip()
        cleared_count = clear_dispatch_success_history(
            db_path,
            rule_name=normalized_rule_name or None,
        )
        remaining_count = count_dispatch_success_history(db_path)
        if normalized_rule_name:
            message = f"已清空规则 {normalized_rule_name} 的 {cleared_count} 条已转发历史。"
        else:
            message = f"已清空 {cleared_count} 条已转发历史。"
        return ApiResponse(
            message=message,
            data={
                "cleared_count": cleared_count,
                "remaining_count": remaining_count,
                "rule_name": normalized_rule_name,
            },
        )

    @app.post("/api/start")
    def start_service(request: Request) -> ApiResponse:
        ensure_authenticated(request)
        try:
            state = service.start()
            return ApiResponse(message="已发送启动指令。", data=state.as_dict())
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=translate_error(str(exc))) from exc

    @app.post("/api/stop")
    def stop_service(request: Request) -> ApiResponse:
        ensure_authenticated(request)
        state = service.stop()
        return ApiResponse(message="已发送停止指令。", data=state.as_dict())

    @app.post("/api/restart")
    def restart_service(request: Request) -> ApiResponse:
        ensure_authenticated(request)
        try:
            state = service.restart()
            return ApiResponse(message="已发送重启指令。", data=state.as_dict())
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=translate_error(str(exc))) from exc

    @app.get("/api/logs")
    def get_logs(
        request: Request,
        limit: int = Query(default=200, ge=10, le=1000),
        before_sequence: int | None = Query(default=None),
    ) -> ApiResponse:
        ensure_authenticated(request)
        total = log_handler.total_record_count()
        items = log_handler.list_records(limit, before_sequence=before_sequence)
        return ApiResponse(data={"items": items, "total": total})

    @app.post("/api/logs/clear")
    def clear_logs(request: Request, payload: LogsClearPayload) -> ApiResponse:
        ensure_authenticated(request)
        removed = log_handler.clear_records(
            source=(payload.source or "").strip() or None,
            kind=(payload.kind or "all").strip().lower() or "all",
        )
        return ApiResponse(message=f"已清空 {removed} 条日志。", data={"removed": removed})

    @app.post("/api/search")
    async def search(request: Request, payload: SearchPayload) -> ApiResponse:
        ensure_authenticated(request)
        try:
            result = await search_messages_async(
                config_path=resolved_config_path,
                query=payload.query,
                limit=payload.limit,
                search_mode=payload.mode or None,
            )
            return ApiResponse(message="快速搜索完成。", data=result)
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=translate_error(str(exc))) from exc

    def _hdhive_checkin_api_response(status: int, raw: str, title: str, desc: str) -> ApiResponse:
        safe_title = (title or "").strip() or (f"HTTP {status}" if status > 0 else "请求结束")
        return ApiResponse(
            message=safe_title,
            data={
                "http_status": status,
                "body": raw,
                "checkin_message": title,
                "checkin_description": desc,
            },
        )

    @app.post("/api/hdhive/checkin-test")
    def hdhive_checkin_test(request: Request, payload: HdhiveCheckinTestPayload) -> ApiResponse:
        ensure_authenticated(request)
        ensure_env_config_path(resolved_config_path)
        values = read_env_file(resolved_config_path)
        method = normalize_hdhive_checkin_method(
            (payload.checkin_method or "").strip() or values.get("HDHIVE_CHECKIN_METHOD"),
        )
        if method == "cookie":
            cookie = (payload.cookie or "").strip() or (values.get("HDHIVE_COOKIE") or "").strip()
            if not cookie:
                raise HTTPException(
                    status_code=400,
                    detail="Cookie 模式需填写 Cookie（或先在站点设置保存 HDHIVE_COOKIE）后再测。",
                )
            api_key = ""
        else:
            api_key = (payload.api_key or "").strip() or (values.get("HDHIVE_API_KEY") or "").strip()
            if not api_key:
                raise HTTPException(
                    status_code=400,
                    detail="Premium（API Key）模式需填写 API Key，或先保存配置后再测。",
                )
            cookie = ""

        proxy, proxy_err = resolve_hdhive_proxy(values)
        if proxy_err:
            raise HTTPException(status_code=400, detail=translate_error(proxy_err))
        status, raw, msg, desc, resp_hdrs = run_hdhive_checkin(
            method=method,
            api_key=api_key,
            cookie_header=cookie,
            is_gambler=bool(payload.is_gambler),
            proxy=proxy,
            hdhive_env=values,
        )
        if status < 0:
            logging.getLogger("tg_forwarder.hdhive").warning(
                "HDHive 测试签到失败（网络）| mode=%s | %s",
                method,
                (raw or "")[:800],
            )
            raise HTTPException(status_code=502, detail=f"网络错误：{raw}")
        _log_hdhive_event("测试签到", method, status, msg, desc)
        hdhive_log = logging.getLogger("tg_forwarder.hdhive")
        cookie_updated = bool(
            method == "cookie"
            and resp_hdrs
            and persist_hdhive_cookie_from_response_headers(
                resolved_config_path, cookie, resp_hdrs, hdhive_log
            )
        )
        resp = _hdhive_checkin_api_response(status, raw, msg, desc)
        if cookie_updated:
            resp = resp.model_copy(
                update={
                    "message": (resp.message or "").strip()
                    + " 已根据响应 Set-Cookie 更新 HDHIVE_COOKIE（请刷新页面加载最新配置）。"
                }
            )
        return resp

    @app.post("/api/hdhive/checkin-now")
    def hdhive_checkin_now(request: Request) -> ApiResponse:
        ensure_authenticated(request)
        ensure_env_config_path(resolved_config_path)
        values = read_env_file(resolved_config_path)
        method = normalize_hdhive_checkin_method(values.get("HDHIVE_CHECKIN_METHOD"))
        api_key = (values.get("HDHIVE_API_KEY") or "").strip()
        cookie = (values.get("HDHIVE_COOKIE") or "").strip()
        if method == "cookie":
            if not cookie:
                raise HTTPException(
                    status_code=400,
                    detail="请先在站点设置中填写 HDHIVE_COOKIE（含 token=）并保存。",
                )
            api_key = ""
        elif not api_key:
            raise HTTPException(status_code=400, detail="请先在站点设置中填写 HDHive API Key 并保存。")
        else:
            cookie = ""

        is_gambler = parse_bool_string(values.get("HDHIVE_CHECKIN_GAMBLER"))
        proxy, proxy_err = resolve_hdhive_proxy(values)
        if proxy_err:
            raise HTTPException(status_code=400, detail=translate_error(proxy_err))
        status, raw, msg, desc, resp_hdrs = run_hdhive_checkin(
            method=method,
            api_key=api_key,
            cookie_header=cookie,
            is_gambler=is_gambler,
            proxy=proxy,
            hdhive_env=values,
        )
        if status < 0:
            logging.getLogger("tg_forwarder.hdhive").warning(
                "HDHive 立即签到失败（网络）| mode=%s | %s",
                method,
                (raw or "")[:800],
            )
            raise HTTPException(status_code=502, detail=f"网络错误：{raw}")
        _log_hdhive_event("立即签到", method, status, msg, desc)
        hdhive_log = logging.getLogger("tg_forwarder.hdhive")
        cookie_updated = bool(
            method == "cookie"
            and resp_hdrs
            and persist_hdhive_cookie_from_response_headers(
                resolved_config_path, cookie, resp_hdrs, hdhive_log
            )
        )
        resp = _hdhive_checkin_api_response(status, raw, msg, desc)
        if cookie_updated:
            resp = resp.model_copy(
                update={
                    "message": (resp.message or "").strip()
                    + " 已根据响应 Set-Cookie 更新 HDHIVE_COOKIE（请刷新页面加载最新配置）。"
                }
            )
        return resp

    @app.post("/api/hdhive/refresh-cookie")
    async def hdhive_refresh_cookie_api(request: Request) -> ApiResponse:
        """立即用当前 HDHIVE_COOKIE 请求首页并写回新 token（不依赖 HDHIVE_COOKIE_REFRESH_ENABLED）。"""
        ensure_authenticated(request)
        ensure_env_config_path(resolved_config_path)
        log = logging.getLogger("tg_forwarder.hdhive")
        values = read_env_file(resolved_config_path)
        cookie = (values.get("HDHIVE_COOKIE") or "").strip()
        if not cookie:
            raise HTTPException(status_code=400, detail="请先在站点设置中填写 HDHIVE_COOKIE 并保存。")
        if not re.search(r"(?i)\btoken=", cookie):
            raise HTTPException(
                status_code=400,
                detail="HDHIVE_COOKIE 中需包含 token=…（请从浏览器复制完整 Cookie）。",
            )
        proxy, proxy_err = resolve_hdhive_proxy(values)
        if proxy_err:
            raise HTTPException(status_code=400, detail=translate_error(proxy_err))
        refresh = await asyncio.to_thread(
            partial(maybe_refresh_hdhive_cookie, resolved_config_path, log, force=True)
        )
        if refresh.written:
            return ApiResponse(
                ok=True,
                message="Cookie 已刷新（GET 首页响应中含新 token，已写回配置）。",
                data={"updated": True, "via": "get_home_set_cookie"},
            )
        if refresh.kind == "network_error":
            raise HTTPException(
                status_code=502,
                detail=(
                    f"GET 首页失败（网络/TLS）：{refresh.message}"
                    f"{_network_hint_for_message(refresh.message)}"
                ),
            )
        if refresh.kind == "http_error":
            raise HTTPException(status_code=502, detail=refresh.message or "GET 首页返回错误状态码。")
        return ApiResponse(
            ok=True,
            message=(
                "GET 首页未返回新的 token=（很常见：站点往往只在登录或签到响应里 Set-Cookie）。"
                " 可改用 Cookie 模式「测试签到」/「立即签到」；若签到成功且响应带 Set-Cookie，系统会自动合并 token。"
            ),
            data={"updated": False, "via": "get_home_set_cookie", "kind": refresh.kind},
        )

    @app.post("/api/hdhive/resolve-test")
    def hdhive_resolve_test(request: Request, payload: HdhiveResolveTestPayload) -> ApiResponse:
        """检测单条 HDHive /resource/ 链接的转发路径（直连 vs 积分解锁策略）；不写入配置、不调用解锁接口。"""
        ensure_authenticated(request)
        ensure_env_config_path(resolved_config_path)
        values = read_env_file(resolved_config_path)
        url = (payload.url or "").strip()
        if not url:
            raise HTTPException(status_code=400, detail="请填写 HDHive resource 链接")
        cookie = (values.get("HDHIVE_COOKIE") or "").strip()
        api_key = (values.get("HDHIVE_API_KEY") or "").strip()
        unlock_enabled = parse_bool_string(values.get("HDHIVE_RESOURCE_UNLOCK_ENABLED"))
        unlock_max_points = parse_non_negative_int_string(
            values.get("HDHIVE_RESOURCE_UNLOCK_MAX_POINTS"),
            default=0,
        )
        unlock_inclusive = parse_bool_string(
            values.get("HDHIVE_RESOURCE_UNLOCK_THRESHOLD_INCLUSIVE"),
            default=True,
        )
        unlock_skip_unknown = parse_bool_string(
            values.get("HDHIVE_RESOURCE_UNLOCK_SKIP_UNKNOWN_POINTS"),
            default=False,
        )
        proxy, proxy_err = resolve_hdhive_proxy(values)
        if proxy_err:
            raise HTTPException(status_code=400, detail=translate_error(proxy_err))
        from tg_forwarder.hdhive_resource_resolve import preview_hdhive_resource_forward_sync

        preview = preview_hdhive_resource_forward_sync(
            url,
            cookie=cookie,
            api_key=api_key,
            unlock_enabled=unlock_enabled,
            unlock_max_points=unlock_max_points,
            unlock_inclusive=unlock_inclusive,
            unlock_skip_unknown=unlock_skip_unknown,
            proxy=proxy,
            timeout_seconds=30.0,
        )
        redirect_url = ""
        if preview.get("direct") and isinstance(preview["direct"], dict):
            redirect_url = str(preview["direct"].get("redirect_url") or "").strip()
        success = preview.get("outcome") == "direct" and bool(redirect_url)
        msg = str(preview.get("summary") or "检测完成")
        return ApiResponse(
            message=msg,
            data={
                "success": success,
                "redirect_url": redirect_url,
                "preview": preview,
            },
        )

    @app.post("/api/forward/manual")
    async def manual_forward(request: Request, payload: ManualForwardPayload) -> ApiResponse:
        ensure_authenticated(request)
        try:
            result = await manual_forward_message_async(
                config_path=resolved_config_path,
                source_chat=payload.source_chat,
                message_id=payload.message_id,
                target_chats=payload.target_chats,
                bot_target_chats=payload.bot_target_chats,
                forward_strategy=payload.forward_strategy,
                rule_names=payload.rule_names,
            )
            return ApiResponse(message="指定转发已执行。", data=result)
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=translate_error(str(exc))) from exc

    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app


def build_log_handler() -> InMemoryLogHandler:
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        if isinstance(handler, InMemoryLogHandler):
            return handler
    handler = InMemoryLogHandler(capacity=1000)
    handler.setFormatter(logging.Formatter("%(asctime)s | %(name)s | %(levelname)s | %(message)s"))
    root_logger.addHandler(handler)
    return handler


def build_default_rule(index: int) -> RulePayload:
    return RulePayload(name=f"rule_{index}", priority=index)


def load_rule_payloads(values: dict[str, str]) -> list[RulePayload]:
    raw_rules = (values.get("TG_RULES_JSON") or "").strip()
    if raw_rules:
        try:
            stored_rules = json.loads(raw_rules)
        except json.JSONDecodeError as exc:
            raise ConfigError("TG_RULES_JSON must be valid JSON") from exc
        if not isinstance(stored_rules, list):
            raise ConfigError("TG_RULES_JSON must be a JSON array")
        if not stored_rules:
            return [build_default_rule(1)]
        parsed_rules = [rule_payload_from_dict(item, index) for index, item in enumerate(stored_rules, start=1)]
        return sort_rule_payloads(parsed_rules)
    return sort_rule_payloads([build_legacy_rule(values)])


def build_legacy_rule(values: dict[str, str]) -> RulePayload:
    return RulePayload(
        name=(values.get("TG_WORKER_NAME", "") or "").strip() or "rule_1",
        enabled=True,
        group="default",
        priority=100,
        source_chat=(values.get("TG_SOURCE_CHAT", "") or "").strip(),
        target_chats=((values.get("TG_TARGET_CHATS") or values.get("TG_TARGET_CHAT") or "").strip()),
        bot_target_chats=(values.get("TG_BOT_TARGET_CHATS", "") or "").strip(),
        forward_strategy="inherit",
        include_edits=parse_bool_string(values.get("TG_INCLUDE_EDITS")),
        forward_own_messages=parse_bool_string(values.get("TG_FORWARD_OWN_MESSAGES")),
        keywords_any=(values.get("TG_KEYWORDS_ANY", "") or "").strip(),
        keywords_all=(values.get("TG_KEYWORDS_ALL", "") or "").strip(),
        block_keywords=(values.get("TG_BLOCK_KEYWORDS", "") or "").strip(),
        regex_any=normalize_regex_text(values.get("TG_REGEX_ANY", ""), "TG_REGEX_ANY"),
        regex_all=normalize_regex_text(values.get("TG_REGEX_ALL", ""), "TG_REGEX_ALL"),
        regex_block=normalize_regex_text(values.get("TG_REGEX_BLOCK", ""), "TG_REGEX_BLOCK"),
        hdhive_resource_resolve_forward=parse_bool_string(values.get("TG_HDHIVE_RESOURCE_RESOLVE_FORWARD")),
        hdhive_require_rule_match=parse_bool_string(values.get("TG_HDHIVE_REQUIRE_RULE_MATCH")),
        media_only=parse_bool_string(values.get("TG_MEDIA_ONLY")),
        text_only=parse_bool_string(values.get("TG_TEXT_ONLY")),
        content_match_mode=normalize_content_match_mode(
            values.get("TG_CONTENT_MATCH_MODE"),
            "TG_CONTENT_MATCH_MODE",
        ),
        case_sensitive=parse_bool_string(values.get("TG_CASE_SENSITIVE")),
    )


def sort_rule_payloads(rules: list[RulePayload]) -> list[RulePayload]:
    return sorted(
        rules,
        key=lambda rule: (
            int(getattr(rule, "priority", 100) or 100),
            str(getattr(rule, "group", "") or "").strip().lower(),
            str(getattr(rule, "name", "") or "").strip().lower(),
        ),
    )


def rule_payload_from_dict(raw_rule: object, index: int) -> RulePayload:
    if not isinstance(raw_rule, dict):
        raise ConfigError(f"TG_RULES_JSON[{index}] must be an object")

    filters = raw_rule.get("filters")
    if not isinstance(filters, dict):
        filters = {}

    source_chat = sources_to_text(raw_rule.get("sources", raw_rule.get("source")))
    targets = targets_to_text(raw_rule.get("targets"))
    name = str(raw_rule.get("name") or f"rule_{index}").strip() or f"rule_{index}"
    try:
        priority = int(raw_rule.get("priority") or index)
    except (TypeError, ValueError):
        priority = index

    return RulePayload(
        name=name,
        enabled=bool(raw_rule.get("enabled", True)),
        group=str(raw_rule.get("group") or "").strip(),
        priority=priority,
        source_chat=source_chat,
        target_chats=targets,
        bot_target_chats=targets_to_text(raw_rule.get("bot_targets")),
        forward_strategy=normalize_rule_forward_strategy_text(raw_rule.get("forward_strategy")),
        include_edits=bool(raw_rule.get("include_edits", False)),
        forward_own_messages=bool(raw_rule.get("forward_own_messages", False)),
        keywords_any=keywords_to_text(filters.get("keywords_any")),
        keywords_all=keywords_to_text(filters.get("keywords_all")),
        block_keywords=keywords_to_text(filters.get("block_keywords")),
        regex_any=keywords_to_text(filters.get("regex_any")),
        regex_all=keywords_to_text(filters.get("regex_all")),
        regex_block=keywords_to_text(filters.get("regex_block")),
        hdhive_resource_resolve_forward=bool(filters.get("hdhive_resource_resolve_forward", False)),
        hdhive_require_rule_match=bool(filters.get("hdhive_require_rule_match", False)),
        media_only=bool(filters.get("media_only", False)),
        text_only=bool(filters.get("text_only", False)),
        content_match_mode=normalize_content_match_mode(
            filters.get("content_match_mode"),
            f"TG_RULES_JSON[{index}].filters.content_match_mode",
        ),
        case_sensitive=bool(filters.get("case_sensitive", False)),
    )


def sources_to_text(raw_sources: object) -> str:
    if raw_sources in (None, ""):
        return ""
    if isinstance(raw_sources, list):
        values = [
            str(item).strip()
            for item in raw_sources
            if item not in (None, "") and str(item).strip()
        ]
        return "\n".join(values)
    return str(raw_sources).strip()


def targets_to_text(raw_targets: object) -> str:
    if not isinstance(raw_targets, list):
        return ""
    targets: list[str] = []
    for raw_target in raw_targets:
        if isinstance(raw_target, (str, int)):
            text = str(raw_target).strip()
            if text:
                targets.append(text)
            continue
        if isinstance(raw_target, dict):
            chat = raw_target.get("chat")
            if chat in (None, ""):
                continue
            text = str(chat).strip()
            if text:
                targets.append(text)
    return ",".join(targets)


def _rule_plain_string_field(value: object) -> str:
    if value in (None, ""):
        return ""
    return str(value)


def _env_value_maybe_json_string(raw: str | None) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    if text.startswith('"') and text.endswith('"'):
        try:
            return str(json.loads(text))
        except json.JSONDecodeError:
            pass
    return text


def keywords_to_text(raw_keywords: object) -> str:
    if raw_keywords in (None, ""):
        return ""
    if isinstance(raw_keywords, list):
        return "\n".join(str(item).strip() for item in raw_keywords if str(item).strip())
    return str(raw_keywords).strip()


def normalize_regex_text(value: str, field_name: str) -> str:
    lines = split_regex_text(value)
    normalize_regex_patterns(lines, field_name)
    return "\n".join(lines)


def split_regex_text(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").replace("\r\n", "\n").split("\n") if item.strip()]


def serialize_rules_to_json(rules: list[RulePayload]) -> str:
    serialized_rules: list[dict[str, object]] = []
    for index, rule in enumerate(rules, start=1):
        source_values = split_list_value(rule.source_chat)
        item: dict[str, object] = {
            "name": rule.name.strip() or f"rule_{index}",
            "enabled": bool(rule.enabled),
            "group": str(rule.group or "").strip(),
            "priority": int(rule.priority or index),
            "sources": source_values,
            "targets": split_list_value(rule.target_chats),
            "bot_targets": split_list_value(rule.bot_target_chats),
            "include_edits": bool(rule.include_edits),
            "forward_own_messages": bool(rule.forward_own_messages),
            "filters": {
                "keywords_any": split_list_value(rule.keywords_any),
                "keywords_all": split_list_value(rule.keywords_all),
                "block_keywords": split_list_value(rule.block_keywords),
                "regex_any": split_regex_text(rule.regex_any),
                "regex_all": split_regex_text(rule.regex_all),
                "regex_block": split_regex_text(rule.regex_block),
                "hdhive_resource_resolve_forward": bool(rule.hdhive_resource_resolve_forward),
                "hdhive_require_rule_match": bool(rule.hdhive_require_rule_match),
                "media_only": bool(rule.media_only),
                "text_only": bool(rule.text_only),
                "content_match_mode": rule.content_match_mode,
                "case_sensitive": bool(rule.case_sensitive),
            },
        }
        normalized_forward_strategy = normalize_rule_forward_strategy_text(rule.forward_strategy)
        if normalized_forward_strategy != "inherit":
            item["forward_strategy"] = normalized_forward_strategy
        serialized_rules.append(item)
    return json.dumps(serialized_rules, ensure_ascii=False, separators=(",", ":"))


def normalize_rule_forward_strategy_text(value: object) -> str:
    normalized = normalize_optional_forward_strategy(value, "rule.forward_strategy")
    return normalized or "inherit"


def split_list_value(value: str) -> list[str]:
    return [item.strip() for item in LIST_SPLIT_PATTERN.split(value) if item.strip()]


def quote_env_value(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def parse_bool_string(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_non_negative_int_string(value: str | None, default: int = 0) -> int:
    if value is None or not str(value).strip():
        return default
    try:
        return max(0, int(str(value).strip(), 10))
    except ValueError:
        return default


def format_bool(value: bool) -> str:
    return "true" if value else "false"


def format_float(value: float) -> str:
    return format(float(value), "g")


def build_login_proxy_pool(
    payload: SessionRequestCodePayload | SessionRequestQrPayload,
) -> list[ProxyConfig]:
    proxy_host = payload.proxy_host.strip()
    proxy_port = payload.proxy_port.strip()
    if not proxy_host and not proxy_port:
        return []
    if not proxy_host or not proxy_port:
        raise ConfigError("web login proxy requires both proxy_host and proxy_port")
    try:
        port = int(proxy_port)
    except ValueError as exc:
        raise ConfigError("web login proxy port must be an integer") from exc
    proxy = ProxyConfig(
        proxy_type=payload.proxy_type.strip() or "socks5",
        host=proxy_host,
        port=port,
        username=payload.proxy_user.strip() or None,
        password=payload.proxy_password.strip() or None,
        rdns=bool(payload.proxy_rdns),
    )
    return [proxy]


def ensure_env_config_path(path: Path) -> None:
    if path.name.lower() != ".env" and path.suffix.lower() != ".env":
        raise HTTPException(
            status_code=400,
            detail="网页控制台只支持 .env 简化模式，请把控制台配置文件切换成 .env。",
        )


def get_dashboard_password(config_path: Path) -> str:
    file_values = read_env_file(config_path)
    return (
        file_values.get("TG_DASHBOARD_PASSWORD")
        or os.getenv("TG_DASHBOARD_PASSWORD")
        or DEFAULT_DASHBOARD_PASSWORD
    )


def parse_float_string(value: str | None, default: float) -> float:
    if value is None or not value.strip():
        return default
    return normalize_rate_limit_delay(value, "TG_RATE_LIMIT_DELAY_SECONDS")
