from __future__ import annotations

from dataclasses import asdict
import json
import logging
import os
from pathlib import Path
import re

from fastapi import FastAPI, HTTPException, Query, Request, status
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
    ConfigError,
    FORWARD_STRATEGY_PARALLEL,
    ProxyConfig,
    SEARCH_MODE_FAST,
    normalize_content_match_mode,
    normalize_forward_strategy,
    normalize_optional_forward_strategy,
    normalize_regex_patterns,
    normalize_rate_limit_delay,
    normalize_resource_presets,
    normalize_search_mode,
)
from tg_forwarder.control_service import SupervisorService
from tg_forwarder.dashboard_actions import manual_forward_message, search_messages
from tg_forwarder.dispatch_queue import (
    clear_dispatch_success_history,
    clear_failed_dispatch_jobs,
    count_dispatch_success_history,
    list_failed_dispatch_jobs,
    list_dispatch_success_history_rules,
    resolve_queue_db_path,
    retry_failed_dispatch_jobs,
)
from tg_forwarder.env_utils import read_env_file, update_env_file
from tg_forwarder.log_buffer import InMemoryLogHandler
from tg_forwarder.startup_notifier import DEFAULT_STARTUP_NOTIFY_MESSAGE
from tg_forwarder.user_messages import translate_error
from tg_forwarder.web_login import TelegramWebLoginManager


DEFAULT_DASHBOARD_PASSWORD = "admin"
LIST_SPLIT_PATTERN = re.compile(r"[,;\r\n]+")


class LoginPayload(BaseModel):
    password: str = ""


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


class SessionCompletePayload(BaseModel):
    login_id: str = ""
    code: str = ""
    password: str = ""


class SessionCancelPayload(BaseModel):
    login_id: str = ""


class RulePayload(BaseModel):
    name: str = ""
    enabled: bool = True
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
    resource_presets: list[str] = Field(default_factory=list)
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

    @field_validator("regex_any", "regex_all", "regex_block")
    @classmethod
    def validate_regex_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "regex")
        return normalize_regex_text(value, field_name)

    @field_validator("resource_presets")
    @classmethod
    def validate_resource_presets(cls, value: list[str]) -> list[str]:
        return normalize_resource_presets(value, "resource_presets")


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

    @field_validator("forward_strategy")
    @classmethod
    def validate_forward_strategy(cls, value: str) -> str:
        normalized = normalize_optional_forward_strategy(value, "manual.forward_strategy")
        return normalized or ""


class SuccessHistoryClearPayload(BaseModel):
    rule_name: str = ""


class ApiResponse(BaseModel):
    ok: bool = True
    message: str = ""
    data: dict = Field(default_factory=dict)


def build_web_app(config_path: str | Path = ".env") -> FastAPI:
    resolved_config_path = Path(config_path).resolve()
    service = SupervisorService(resolved_config_path)
    log_handler = build_log_handler()
    login_manager = TelegramWebLoginManager()

    def queue_db_path() -> Path:
        return resolve_queue_db_path(resolved_config_path)

    app = FastAPI(title="TG 转发控制台")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    static_dir = Path(__file__).resolve().parent / "web" / "static"

    @app.on_event("shutdown")
    async def shutdown_event() -> None:
        await login_manager.close()

    @app.post("/api/login")
    def login(payload: LoginPayload) -> ApiResponse:
        expected_password = get_dashboard_password(resolved_config_path)
        if payload.password != expected_password:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="控制台密码错误。",
            )
        return ApiResponse(message="登录成功。")

    @app.post("/api/session/request-code")
    async def request_session_code(request: Request, payload: SessionRequestCodePayload) -> ApiResponse:
        ensure_authenticated(request, resolved_config_path)
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
        ensure_authenticated(request, resolved_config_path)
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
        ensure_authenticated(request, resolved_config_path)
        await login_manager.cancel(payload.login_id)
        return ApiResponse(message="已取消当前网页登录流程。")

    @app.get("/api/config")
    def get_config(request: Request) -> ApiResponse:
        ensure_authenticated(request, resolved_config_path)
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

    @app.put("/api/config")
    def save_config(request: Request, payload: DashboardConfigPayload) -> ApiResponse:
        ensure_authenticated(request, resolved_config_path)
        ensure_env_config_path(resolved_config_path)
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
            "TG_SOURCE_CHAT": first_rule.source_chat.strip(),
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
            "TG_RESOURCE_PRESETS": ",".join(first_rule.resource_presets),
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
            "TG_LANDING_PAGE_ENABLED": None,
            "TG_LANDING_PAGE_MATCH_ENABLED": None,
            "TG_LANDING_PAGE_HOSTS": None,
            "TG_LANDING_PAGE_EXTRACT_MODE": None,
            "TG_LANDING_PAGE_SECTION_MARKERS": None,
            "TG_LANDING_PAGE_SECTION_STOP_MARKERS": None,
            "TG_LANDING_PAGE_SECTION_FALLBACK_TO_FULL": None,
            "TG_RULES_JSON": quote_env_value(rules_json),
        }
        update_env_file(resolved_config_path, values)
        return ApiResponse(message="配置已保存。")

    @app.post("/api/validate")
    def validate_config(request: Request) -> ApiResponse:
        ensure_authenticated(request, resolved_config_path)
        try:
            result = service.validate()
            return ApiResponse(message="配置校验通过。", data=result)
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=translate_error(str(exc))) from exc

    @app.get("/api/status")
    def get_status(request: Request) -> ApiResponse:
        ensure_authenticated(request, resolved_config_path)
        return ApiResponse(data=service.get_state().as_dict())

    @app.get("/api/queue/failed")
    def get_failed_queue_items(
        request: Request,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> ApiResponse:
        ensure_authenticated(request, resolved_config_path)
        items = list_failed_dispatch_jobs(queue_db_path(), limit=limit)
        return ApiResponse(data={"items": [asdict(item) for item in items]})

    @app.post("/api/queue/retry-failed")
    def retry_failed_queue_items(request: Request) -> ApiResponse:
        ensure_authenticated(request, resolved_config_path)
        retried_count = retry_failed_dispatch_jobs(queue_db_path())
        return ApiResponse(
            message=f"已重新放回队列 {retried_count} 个失败目标。",
            data={"retried_count": retried_count},
        )

    @app.post("/api/queue/clear-failed")
    def clear_failed_queue_items(request: Request) -> ApiResponse:
        ensure_authenticated(request, resolved_config_path)
        cleared_count = clear_failed_dispatch_jobs(queue_db_path())
        return ApiResponse(
            message=f"已清空 {cleared_count} 条失败任务。",
            data={"cleared_count": cleared_count},
        )

    @app.get("/api/queue/success-history/summary")
    def get_success_history_summary(request: Request) -> ApiResponse:
        ensure_authenticated(request, resolved_config_path)
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
        ensure_authenticated(request, resolved_config_path)
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
        ensure_authenticated(request, resolved_config_path)
        try:
            state = service.start()
            return ApiResponse(message="已发送启动指令。", data=state.as_dict())
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=translate_error(str(exc))) from exc

    @app.post("/api/stop")
    def stop_service(request: Request) -> ApiResponse:
        ensure_authenticated(request, resolved_config_path)
        state = service.stop()
        return ApiResponse(message="已发送停止指令。", data=state.as_dict())

    @app.post("/api/restart")
    def restart_service(request: Request) -> ApiResponse:
        ensure_authenticated(request, resolved_config_path)
        try:
            state = service.restart()
            return ApiResponse(message="已发送重启指令。", data=state.as_dict())
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=translate_error(str(exc))) from exc

    @app.get("/api/logs")
    def get_logs(
        request: Request,
        limit: int = Query(default=200, ge=10, le=1000),
    ) -> ApiResponse:
        ensure_authenticated(request, resolved_config_path)
        return ApiResponse(data={"items": log_handler.list_records(limit)})

    @app.post("/api/search")
    def search(request: Request, payload: SearchPayload) -> ApiResponse:
        ensure_authenticated(request, resolved_config_path)
        try:
            result = search_messages(
                config_path=resolved_config_path,
                query=payload.query,
                limit=payload.limit,
                search_mode=payload.mode or None,
            )
            return ApiResponse(message="快速搜索完成。", data=result)
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=translate_error(str(exc))) from exc

    @app.post("/api/forward/manual")
    def manual_forward(request: Request, payload: ManualForwardPayload) -> ApiResponse:
        ensure_authenticated(request, resolved_config_path)
        try:
            result = manual_forward_message(
                config_path=resolved_config_path,
                source_chat=payload.source_chat,
                message_id=payload.message_id,
                target_chats=payload.target_chats,
                bot_target_chats=payload.bot_target_chats,
                forward_strategy=payload.forward_strategy,
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
    return RulePayload(name=f"rule_{index}")


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
        return [rule_payload_from_dict(item, index) for index, item in enumerate(stored_rules, start=1)]
    return [build_legacy_rule(values)]


def build_legacy_rule(values: dict[str, str]) -> RulePayload:
    return RulePayload(
        name=(values.get("TG_WORKER_NAME", "") or "").strip() or "rule_1",
        enabled=True,
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
        resource_presets=normalize_resource_presets(
            split_list_value(values.get("TG_RESOURCE_PRESETS", "")),
            "TG_RESOURCE_PRESETS",
        ),
        media_only=parse_bool_string(values.get("TG_MEDIA_ONLY")),
        text_only=parse_bool_string(values.get("TG_TEXT_ONLY")),
        content_match_mode=normalize_content_match_mode(
            values.get("TG_CONTENT_MATCH_MODE"),
            "TG_CONTENT_MATCH_MODE",
        ),
        case_sensitive=parse_bool_string(values.get("TG_CASE_SENSITIVE")),
    )


def rule_payload_from_dict(raw_rule: object, index: int) -> RulePayload:
    if not isinstance(raw_rule, dict):
        raise ConfigError(f"TG_RULES_JSON[{index}] must be an object")

    filters = raw_rule.get("filters")
    if not isinstance(filters, dict):
        filters = {}

    source = raw_rule.get("source")
    source_chat = "" if source in (None, "") else str(source).strip()
    targets = targets_to_text(raw_rule.get("targets"))
    name = str(raw_rule.get("name") or f"rule_{index}").strip() or f"rule_{index}"

    return RulePayload(
        name=name,
        enabled=bool(raw_rule.get("enabled", True)),
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
        resource_presets=normalize_resource_presets(
            filters.get("resource_presets"),
            f"TG_RULES_JSON[{index}].filters.resource_presets",
        ),
        media_only=bool(filters.get("media_only", False)),
        text_only=bool(filters.get("text_only", False)),
        content_match_mode=normalize_content_match_mode(
            filters.get("content_match_mode"),
            f"TG_RULES_JSON[{index}].filters.content_match_mode",
        ),
        case_sensitive=bool(filters.get("case_sensitive", False)),
    )


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
        item: dict[str, object] = {
            "name": rule.name.strip() or f"rule_{index}",
            "enabled": bool(rule.enabled),
            "source": rule.source_chat.strip(),
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
                "resource_presets": list(rule.resource_presets),
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


def format_bool(value: bool) -> str:
    return "true" if value else "false"


def format_float(value: float) -> str:
    return format(float(value), "g")


def build_login_proxy_pool(payload: SessionRequestCodePayload) -> list[ProxyConfig]:
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


def ensure_authenticated(request: Request, config_path: Path) -> None:
    provided_password = request.headers.get("x-dashboard-password", "")
    expected_password = get_dashboard_password(config_path)
    if provided_password != expected_password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="请先输入控制台密码。",
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
