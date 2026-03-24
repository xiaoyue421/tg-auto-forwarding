from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import json
import os
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from dotenv import load_dotenv
import yaml

from tg_forwarder.env_utils import read_env_file


ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")
LIST_SPLIT_PATTERN = re.compile(r"[,;\r\n]+")

FORWARD_STRATEGY_PARALLEL = "parallel"
FORWARD_STRATEGY_ACCOUNT_FIRST = "account_first"
FORWARD_STRATEGY_BOT_FIRST = "bot_first"
FORWARD_STRATEGY_ACCOUNT_ONLY = "account_only"
FORWARD_STRATEGY_BOT_ONLY = "bot_only"
FORWARD_STRATEGY_VALUES = {
    FORWARD_STRATEGY_PARALLEL,
    FORWARD_STRATEGY_ACCOUNT_FIRST,
    FORWARD_STRATEGY_BOT_FIRST,
    FORWARD_STRATEGY_ACCOUNT_ONLY,
    FORWARD_STRATEGY_BOT_ONLY,
}
SEARCH_MODE_FAST = "fast"
SEARCH_MODE_VALUES = {
    SEARCH_MODE_FAST,
}
CONTENT_MATCH_MODE_ALL = "all"
CONTENT_MATCH_MODE_ANY = "any"
CONTENT_MATCH_MODE_VALUES = {
    CONTENT_MATCH_MODE_ALL,
    CONTENT_MATCH_MODE_ANY,
}
RESOURCE_PRESET_115CDN = "115cdn"
RESOURCE_PRESET_ED2K = "ed2k"
RESOURCE_PRESET_MAGNET = "magnet"
RESOURCE_PRESET_THUNDER = "thunder"
RESOURCE_PRESET_QUARK = "quark"
RESOURCE_PRESET_ALIYUN = "aliyun"
RESOURCE_PRESET_BAIDU = "baidu"
RESOURCE_PRESET_VALUES = {
    RESOURCE_PRESET_115CDN,
    RESOURCE_PRESET_ED2K,
    RESOURCE_PRESET_MAGNET,
    RESOURCE_PRESET_THUNDER,
    RESOURCE_PRESET_QUARK,
    RESOURCE_PRESET_ALIYUN,
    RESOURCE_PRESET_BAIDU,
}
RESOURCE_PRESET_ALIASES = {
    "115": RESOURCE_PRESET_115CDN,
    "115cdn": RESOURCE_PRESET_115CDN,
    "ed2k": RESOURCE_PRESET_ED2K,
    "magnet": RESOURCE_PRESET_MAGNET,
    "thunder": RESOURCE_PRESET_THUNDER,
    "quark": RESOURCE_PRESET_QUARK,
    "uc": RESOURCE_PRESET_QUARK,
    "aliyun": RESOURCE_PRESET_ALIYUN,
    "alipan": RESOURCE_PRESET_ALIYUN,
    "baidu": RESOURCE_PRESET_BAIDU,
}
DEFAULT_RATE_LIMIT_DELAY_SECONDS = 1.2
DEFAULT_WORKER_MAX_RESTART_FAILURES = 5
DEFAULT_WORKER_FAILURE_RESET_SECONDS = 60


class ConfigError(ValueError):
    """Raised when the configuration is invalid."""


@dataclass(slots=True)
class ProxyConfig:
    proxy_type: str
    host: str
    port: int
    username: str | None = None
    password: str | None = None
    rdns: bool = True

    def to_telethon_proxy(self) -> dict[str, Any]:
        proxy: dict[str, Any] = {
            "proxy_type": self.proxy_type,
            "addr": self.host,
            "port": self.port,
            "rdns": self.rdns,
        }
        if self.username:
            proxy["username"] = self.username
        if self.password:
            proxy["password"] = self.password
        return proxy


@dataclass(slots=True)
class TelegramSettings:
    api_id: int
    api_hash: str
    session_string: str | None = None
    session_file: str | None = None
    proxy: ProxyConfig | None = None
    proxies: list[ProxyConfig] = field(default_factory=list)
    bot_token: str | None = None
    forward_strategy: str = FORWARD_STRATEGY_PARALLEL
    rate_limit_protection: bool = False
    rate_limit_delay_seconds: float = DEFAULT_RATE_LIMIT_DELAY_SECONDS
    startup_notify_enabled: bool = False
    startup_notify_message: str | None = None
    search_default_mode: str = SEARCH_MODE_FAST

    def build_client_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if self.proxy is not None:
            kwargs["proxy"] = self.proxy.to_telethon_proxy()
        return kwargs

    def build_proxy_pool(self) -> list[ProxyConfig]:
        pool: list[ProxyConfig] = []
        seen: set[tuple[str, str, int, str | None, str | None, bool]] = set()
        for item in [self.proxy, *self.proxies]:
            if item is None:
                continue
            key = (
                item.proxy_type.strip().lower(),
                item.host.strip().lower(),
                int(item.port),
                (item.username or "").strip() or None,
                (item.password or "").strip() or None,
                bool(item.rdns),
            )
            if key in seen:
                continue
            seen.add(key)
            pool.append(item)
        return pool

    @property
    def bot_tokens(self) -> list[str]:
        return parse_bot_tokens(self.bot_token, "telegram.bot_token")


@dataclass(slots=True)
class ForwardTarget:
    chat: str | int
    silent: bool = False
    drop_author: bool = False
    drop_media_captions: bool = False


@dataclass(slots=True)
class FilterConfig:
    keywords_any: list[str] = field(default_factory=list)
    keywords_all: list[str] = field(default_factory=list)
    block_keywords: list[str] = field(default_factory=list)
    regex_any: list[str] = field(default_factory=list)
    regex_all: list[str] = field(default_factory=list)
    regex_block: list[str] = field(default_factory=list)
    resource_presets: list[str] = field(default_factory=list)
    media_only: bool = False
    text_only: bool = False
    content_match_mode: str = CONTENT_MATCH_MODE_ALL
    case_sensitive: bool = False


@dataclass(slots=True)
class WorkerConfig:
    name: str
    sources: list[str | int]
    targets: list[ForwardTarget]
    bot_targets: list[ForwardTarget] = field(default_factory=list)
    forward_strategy: str | None = None
    enabled: bool = True
    include_edits: bool = False
    forward_own_messages: bool = False
    session_string: str | None = None
    session_file: str | None = None
    filters: FilterConfig = field(default_factory=FilterConfig)

    @property
    def primary_source(self) -> str | int:
        return self.sources[0]

    @property
    def source(self) -> str | int:
        return self.primary_source


@dataclass(slots=True)
class WorkerRuntimeConfig:
    name: str
    sources: list[str | int]
    targets: list[ForwardTarget]
    bot_targets: list[ForwardTarget]
    forward_strategy: str | None
    include_edits: bool
    forward_own_messages: bool
    filters: FilterConfig
    telegram: TelegramSettings

    @property
    def primary_source(self) -> str | int:
        return self.sources[0]

    @property
    def source(self) -> str | int:
        return self.primary_source

    def as_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["source"] = self.primary_source
        return payload


@dataclass(slots=True)
class SupervisorConfig:
    check_interval_seconds: int = 5
    restart_delay_seconds: int = 3
    stop_timeout_seconds: int = 10
    worker_max_restart_failures: int = DEFAULT_WORKER_MAX_RESTART_FAILURES
    worker_failure_reset_seconds: int = DEFAULT_WORKER_FAILURE_RESET_SECONDS
    reload_on_change: bool = True


@dataclass(slots=True)
class AppConfig:
    config_path: Path
    telegram: TelegramSettings
    supervisor: SupervisorConfig
    workers: list[WorkerConfig]

    def build_runtime_workers(self) -> list[WorkerRuntimeConfig]:
        runtime_workers: list[WorkerRuntimeConfig] = []
        for worker in self.workers:
            if not worker.enabled:
                continue
            effective_strategy = resolve_forward_strategy(
                worker.forward_strategy,
                self.telegram.forward_strategy,
                f"worker `{worker.name}`.forward_strategy",
            )
            if effective_strategy == FORWARD_STRATEGY_ACCOUNT_ONLY and not worker.targets:
                raise ConfigError(
                    f"worker `{worker.name}` uses account_only but has no account targets"
                )
            if effective_strategy == FORWARD_STRATEGY_BOT_ONLY:
                if not worker.bot_targets:
                    raise ConfigError(
                        f"worker `{worker.name}` uses bot_only but has no bot targets"
                    )
                if not self.telegram.bot_tokens:
                    raise ConfigError(
                        f"worker `{worker.name}` uses bot_only but TG_BOT_TOKEN is missing"
                    )
            session_string = worker.session_string or self.telegram.session_string
            session_file = worker.session_file or self.telegram.session_file
            if not session_string and not session_file:
                raise ConfigError(
                    f"worker `{worker.name}` is missing session_string or session_file"
                )
            if session_string and session_file:
                raise ConfigError(
                    f"worker `{worker.name}` cannot set both session_string and session_file"
                )
            runtime_workers.append(
                WorkerRuntimeConfig(
                    name=worker.name,
                    sources=list(worker.sources),
                    targets=worker.targets,
                    bot_targets=worker.bot_targets,
                    forward_strategy=worker.forward_strategy,
                    include_edits=worker.include_edits,
                    forward_own_messages=worker.forward_own_messages,
                    filters=worker.filters,
                    telegram=TelegramSettings(
                        api_id=self.telegram.api_id,
                        api_hash=self.telegram.api_hash,
                        session_string=session_string,
                        session_file=session_file,
                        proxy=self.telegram.proxy,
                        proxies=list(self.telegram.proxies),
                        bot_token=self.telegram.bot_token,
                        forward_strategy=self.telegram.forward_strategy,
                        rate_limit_protection=self.telegram.rate_limit_protection,
                        rate_limit_delay_seconds=self.telegram.rate_limit_delay_seconds,
                        search_default_mode=self.telegram.search_default_mode,
                    ),
                )
            )
        if not runtime_workers:
            raise ConfigError("no runnable workers found, check workers[].enabled")
        return runtime_workers


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path).resolve()
    if config_path.exists() and not is_env_path(config_path):
        data = load_yaml_with_env(config_path)
        telegram = parse_telegram_settings(data.get("telegram") or {}, config_path.parent)
        supervisor = parse_supervisor_config(data.get("supervisor") or {})
        workers = parse_workers(data.get("workers"), config_path.parent)
        return AppConfig(
            config_path=config_path,
            telegram=telegram,
            supervisor=supervisor,
            workers=workers,
        )

    env_path = config_path if is_env_path(config_path) else config_path.parent / ".env"
    return load_simple_env_config(env_path)


def load_telegram_settings(path: str | Path) -> TelegramSettings:
    config_path = Path(path).resolve()
    if config_path.exists() and not is_env_path(config_path):
        load_dotenv_if_exists(config_path.parent / ".env")
        raw_text = config_path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw_text) or {}
        if not isinstance(data, dict):
            raise ConfigError("config root must be a mapping object")
        telegram_section = expand_env_object(data.get("telegram") or {})
        return parse_telegram_settings(
            telegram_section,
            base_dir=config_path.parent,
        )

    env_path = config_path if is_env_path(config_path) else config_path.parent / ".env"
    return load_simple_env_telegram_settings(env_path)


def load_simple_env_config(env_path: str | Path) -> AppConfig:
    resolved_env_path = Path(env_path).resolve()
    env_values = read_env_context(resolved_env_path)

    api_id = env_required_from(env_values, "TG_API_ID", "simple mode requires TG_API_ID")
    api_hash = env_required_from(env_values, "TG_API_HASH", "simple mode requires TG_API_HASH")
    session_string = env_optional_from(env_values, "TG_SESSION_STRING")
    session_file = resolve_optional_path(
        resolved_env_path.parent,
        env_optional_from(env_values, "TG_SESSION_FILE"),
    )
    if session_string and session_file:
        raise ConfigError("simple mode cannot set both TG_SESSION_STRING and TG_SESSION_FILE")

    worker_name = env_optional_from(env_values, "TG_WORKER_NAME") or "main"
    telegram = TelegramSettings(
        api_id=int(api_id),
        api_hash=api_hash,
        session_string=session_string,
        session_file=session_file,
        proxy=parse_proxy_from_env(env_values),
        proxies=parse_proxy_list_from_env(env_values),
        bot_token=serialize_string_list(
            merge_unique_strings(
                parse_bot_tokens(env_optional_from(env_values, "TG_BOT_TOKEN"), "TG_BOT_TOKEN"),
                parse_bot_tokens(env_optional_from(env_values, "TG_BOT_TOKENS"), "TG_BOT_TOKENS"),
            )
        ),
        forward_strategy=normalize_forward_strategy(
            env_optional_from(env_values, "TG_FORWARD_STRATEGY"),
            "TG_FORWARD_STRATEGY",
        ),
        rate_limit_protection=parse_bool(
            env_optional_from(env_values, "TG_RATE_LIMIT_PROTECTION"),
            False,
        ),
        rate_limit_delay_seconds=parse_float_env(
            env_values,
            "TG_RATE_LIMIT_DELAY_SECONDS",
            DEFAULT_RATE_LIMIT_DELAY_SECONDS,
        ),
        startup_notify_enabled=parse_bool(
            env_optional_from(env_values, "TG_STARTUP_NOTIFY_ENABLED"),
            False,
        ),
        startup_notify_message=env_optional_from(env_values, "TG_STARTUP_NOTIFY_MESSAGE"),
        search_default_mode=normalize_search_mode(
            env_optional_from(env_values, "TG_SEARCH_DEFAULT_MODE"),
            "TG_SEARCH_DEFAULT_MODE",
        ),
    )
    workers = parse_simple_env_workers(env_values, resolved_env_path.parent, worker_name)
    return AppConfig(
        config_path=resolved_env_path,
        telegram=telegram,
        supervisor=SupervisorConfig(
            check_interval_seconds=parse_int_env(env_values, "TG_CHECK_INTERVAL", 5),
            restart_delay_seconds=parse_int_env(env_values, "TG_RESTART_DELAY", 3),
            stop_timeout_seconds=parse_int_env(env_values, "TG_STOP_TIMEOUT", 10),
            worker_max_restart_failures=parse_int_env(
                env_values,
                "TG_WORKER_MAX_RESTARTS",
                DEFAULT_WORKER_MAX_RESTART_FAILURES,
            ),
            worker_failure_reset_seconds=parse_int_env(
                env_values,
                "TG_WORKER_FAILURE_RESET_SECONDS",
                DEFAULT_WORKER_FAILURE_RESET_SECONDS,
            ),
            reload_on_change=resolved_env_path.exists(),
        ),
        workers=workers,
    )


def load_simple_env_telegram_settings(env_path: str | Path) -> TelegramSettings:
    resolved_env_path = Path(env_path).resolve()
    env_values = read_env_context(resolved_env_path)
    session_string = env_optional_from(env_values, "TG_SESSION_STRING")
    session_file = resolve_optional_path(
        resolved_env_path.parent,
        env_optional_from(env_values, "TG_SESSION_FILE"),
    )
    if session_string and session_file:
        raise ConfigError("simple mode cannot set both TG_SESSION_STRING and TG_SESSION_FILE")
    return TelegramSettings(
        api_id=int(env_required_from(env_values, "TG_API_ID", "simple mode requires TG_API_ID")),
        api_hash=env_required_from(env_values, "TG_API_HASH", "simple mode requires TG_API_HASH"),
        session_string=session_string,
        session_file=session_file,
        proxy=parse_proxy_from_env(env_values),
        proxies=parse_proxy_list_from_env(env_values),
        bot_token=serialize_string_list(
            merge_unique_strings(
                parse_bot_tokens(env_optional_from(env_values, "TG_BOT_TOKEN"), "TG_BOT_TOKEN"),
                parse_bot_tokens(env_optional_from(env_values, "TG_BOT_TOKENS"), "TG_BOT_TOKENS"),
            )
        ),
        forward_strategy=normalize_forward_strategy(
            env_optional_from(env_values, "TG_FORWARD_STRATEGY"),
            "TG_FORWARD_STRATEGY",
        ),
        rate_limit_protection=parse_bool(
            env_optional_from(env_values, "TG_RATE_LIMIT_PROTECTION"),
            False,
        ),
        rate_limit_delay_seconds=parse_float_env(
            env_values,
            "TG_RATE_LIMIT_DELAY_SECONDS",
            DEFAULT_RATE_LIMIT_DELAY_SECONDS,
        ),
        startup_notify_enabled=parse_bool(
            env_optional_from(env_values, "TG_STARTUP_NOTIFY_ENABLED"),
            False,
        ),
        startup_notify_message=env_optional_from(env_values, "TG_STARTUP_NOTIFY_MESSAGE"),
        search_default_mode=normalize_search_mode(
            env_optional_from(env_values, "TG_SEARCH_DEFAULT_MODE"),
            "TG_SEARCH_DEFAULT_MODE",
        ),
    )


def parse_simple_env_workers(
    env_values: dict[str, str],
    base_dir: Path,
    default_worker_name: str,
) -> list[WorkerConfig]:
    rules_json = env_optional_from(env_values, "TG_RULES_JSON")
    if rules_json:
        try:
            raw_rules = json.loads(rules_json)
        except json.JSONDecodeError as exc:
            raise ConfigError("TG_RULES_JSON must be valid JSON") from exc
        return parse_workers(raw_rules, base_dir)
    return [
        build_legacy_env_worker(env_values, default_worker_name)
    ]


def build_legacy_env_worker(
    env_values: dict[str, str],
    default_worker_name: str,
) -> WorkerConfig:
    sources = parse_source_references(
        env_required_from(env_values, "TG_SOURCE_CHAT", "simple mode requires TG_SOURCE_CHAT"),
        "TG_SOURCE_CHAT",
    )
    targets = parse_simple_optional_targets(env_values, "TG_TARGET_CHATS")
    if not targets:
        targets = parse_simple_optional_targets(env_values, "TG_TARGET_CHAT")
    bot_targets = parse_simple_optional_targets(env_values, "TG_BOT_TARGET_CHATS")
    if not targets and not bot_targets:
        raise ConfigError("simple mode requires TG_TARGET_CHATS or TG_BOT_TARGET_CHATS")
    return WorkerConfig(
        name=default_worker_name,
        sources=sources,
        targets=targets,
        bot_targets=bot_targets,
        forward_strategy=normalize_optional_forward_strategy(
            env_optional_from(env_values, "TG_WORKER_FORWARD_STRATEGY"),
            "TG_WORKER_FORWARD_STRATEGY",
        ),
        enabled=True,
        include_edits=parse_bool(env_optional_from(env_values, "TG_INCLUDE_EDITS"), False),
        forward_own_messages=parse_bool(
            env_optional_from(env_values, "TG_FORWARD_OWN_MESSAGES"),
            False,
        ),
        filters=FilterConfig(
            keywords_any=parse_list_env(env_values, "TG_KEYWORDS_ANY"),
            keywords_all=parse_list_env(env_values, "TG_KEYWORDS_ALL"),
            block_keywords=parse_list_env(env_values, "TG_BLOCK_KEYWORDS"),
            regex_any=parse_regex_env(env_values, "TG_REGEX_ANY"),
            regex_all=parse_regex_env(env_values, "TG_REGEX_ALL"),
            regex_block=parse_regex_env(env_values, "TG_REGEX_BLOCK"),
            resource_presets=parse_resource_presets_env(env_values, "TG_RESOURCE_PRESETS"),
            media_only=parse_bool(env_optional_from(env_values, "TG_MEDIA_ONLY"), False),
            text_only=parse_bool(env_optional_from(env_values, "TG_TEXT_ONLY"), False),
            content_match_mode=normalize_content_match_mode(
                env_optional_from(env_values, "TG_CONTENT_MATCH_MODE"),
                "TG_CONTENT_MATCH_MODE",
            ),
            case_sensitive=parse_bool(env_optional_from(env_values, "TG_CASE_SENSITIVE"), False),
        ),
    )


def expand_env_in_text(raw_text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        default = match.group(2)
        value = os.getenv(var_name)
        if value is not None:
            return value
        if default is not None:
            return default
        raise ConfigError(f"missing environment variable `{var_name}`")

    return ENV_PATTERN.sub(replace, raw_text)


def load_yaml_with_env(path: str | Path) -> dict[str, Any]:
    config_path = Path(path).resolve()
    load_dotenv_if_exists(config_path.parent / ".env")
    raw_text = config_path.read_text(encoding="utf-8")
    expanded_text = expand_env_in_text(raw_text)
    data = yaml.safe_load(expanded_text) or {}
    if not isinstance(data, dict):
        raise ConfigError("config root must be a mapping object")
    return data


def parse_telegram_settings(data: dict[str, Any], base_dir: Path) -> TelegramSettings:
    if not isinstance(data, dict):
        raise ConfigError("telegram must be a mapping object")
    api_id = data.get("api_id")
    api_hash = clean_optional_string(data.get("api_hash"))
    if api_id in (None, ""):
        raise ConfigError("telegram.api_id is required")
    if not api_hash:
        raise ConfigError("telegram.api_hash is required")
    session_string = clean_optional_string(data.get("session_string"))
    session_file = resolve_optional_path(base_dir, data.get("session_file"))
    if session_string and session_file:
        raise ConfigError("telegram cannot set both session_string and session_file")
    proxy_data = data.get("proxy")
    proxy = parse_proxy_config(proxy_data) if proxy_data else None
    proxies = parse_proxy_config_list(data.get("proxies"), "telegram.proxies")
    bot_token = serialize_string_list(
        merge_unique_strings(
            parse_bot_tokens(data.get("bot_token"), "telegram.bot_token"),
            parse_bot_tokens(data.get("bot_tokens"), "telegram.bot_tokens"),
        )
    )
    return TelegramSettings(
        api_id=int(api_id),
        api_hash=api_hash,
        session_string=session_string,
        session_file=session_file,
        proxy=proxy,
        proxies=proxies,
        bot_token=bot_token,
        forward_strategy=normalize_forward_strategy(
            data.get("forward_strategy"),
            "telegram.forward_strategy",
        ),
        rate_limit_protection=bool(data.get("rate_limit_protection", False)),
        rate_limit_delay_seconds=normalize_rate_limit_delay(
            data.get("rate_limit_delay_seconds"),
            "telegram.rate_limit_delay_seconds",
        ),
        startup_notify_enabled=bool(data.get("startup_notify_enabled", False)),
        startup_notify_message=clean_optional_string(data.get("startup_notify_message")),
        search_default_mode=normalize_search_mode(
            data.get("search_default_mode"),
            "telegram.search_default_mode",
        ),
    )


def parse_proxy_config(data: dict[str, Any]) -> ProxyConfig:
    if not isinstance(data, dict):
        raise ConfigError("telegram.proxy must be a mapping object")
    proxy_type = clean_optional_string(data.get("type"))
    host = clean_optional_string(data.get("host"))
    port = data.get("port")
    if not proxy_type or not host or port in (None, ""):
        raise ConfigError("telegram.proxy requires type, host and port")
    return ProxyConfig(
        proxy_type=proxy_type,
        host=host,
        port=int(port),
        username=clean_optional_string(data.get("username")),
        password=clean_optional_string(data.get("password")),
        rdns=bool(data.get("rdns", True)),
    )


def parse_proxy_config_list(data: Any, field_name: str) -> list[ProxyConfig]:
    if data in (None, ""):
        return []
    if isinstance(data, str):
        return parse_proxy_value_list(data, field_name)
    if not isinstance(data, list):
        raise ConfigError(f"{field_name} must be a list")
    proxies: list[ProxyConfig] = []
    for index, item in enumerate(data, start=1):
        if isinstance(item, dict):
            proxies.append(parse_proxy_config(item))
            continue
        proxies.append(parse_proxy_value(item, f"{field_name}[{index}]"))
    return dedupe_proxy_list(proxies)


def parse_proxy_from_env(env_values: dict[str, str]) -> ProxyConfig | None:
    host = env_optional_from(env_values, "TG_PROXY_HOST")
    port = env_optional_from(env_values, "TG_PROXY_PORT")
    if not host and not port:
        return None
    if not host or not port:
        raise ConfigError("simple mode proxy requires both TG_PROXY_HOST and TG_PROXY_PORT")
    return ProxyConfig(
        proxy_type=env_optional_from(env_values, "TG_PROXY_TYPE") or "socks5",
        host=host,
        port=int(port),
        username=env_optional_from(env_values, "TG_PROXY_USER"),
        password=env_optional_from(env_values, "TG_PROXY_PASSWORD"),
        rdns=parse_bool(env_optional_from(env_values, "TG_PROXY_RDNS"), True),
    )


def parse_proxy_list_from_env(env_values: dict[str, str]) -> list[ProxyConfig]:
    raw_value = env_optional_from(env_values, "TG_PROXY_URLS") or env_optional_from(
        env_values,
        "TG_PROXY_LIST",
    )
    if not raw_value:
        return []
    return parse_proxy_value_list(raw_value, "TG_PROXY_URLS")


def parse_supervisor_config(data: dict[str, Any]) -> SupervisorConfig:
    if not isinstance(data, dict):
        raise ConfigError("supervisor must be a mapping object")
    return SupervisorConfig(
        check_interval_seconds=max(1, int(data.get("check_interval_seconds", 5))),
        restart_delay_seconds=max(1, int(data.get("restart_delay_seconds", 3))),
        stop_timeout_seconds=max(1, int(data.get("stop_timeout_seconds", 10))),
        worker_max_restart_failures=max(
            1,
            int(data.get("worker_max_restart_failures", DEFAULT_WORKER_MAX_RESTART_FAILURES)),
        ),
        worker_failure_reset_seconds=max(
            1,
            int(data.get("worker_failure_reset_seconds", DEFAULT_WORKER_FAILURE_RESET_SECONDS)),
        ),
        reload_on_change=bool(data.get("reload_on_change", True)),
    )


def parse_workers(
    data: Any,
    base_dir: Path,
) -> list[WorkerConfig]:
    if not isinstance(data, list) or not data:
        raise ConfigError("workers must be a non-empty list")
    workers: list[WorkerConfig] = []
    seen_names: set[str] = set()
    for index, raw_worker in enumerate(data, start=1):
        if not isinstance(raw_worker, dict):
            raise ConfigError(f"workers[{index}] must be an object")
        name = clean_optional_string(raw_worker.get("name"))
        if not name:
            raise ConfigError(f"workers[{index}].name is required")
        if name in seen_names:
            raise ConfigError(f"duplicate worker name `{name}`")
        seen_names.add(name)
        sources = parse_source_references(
            raw_worker.get("sources", raw_worker.get("source")),
            f"workers[{index}].sources",
        )
        targets = parse_optional_targets(raw_worker.get("targets"), f"workers[{index}].targets")
        bot_targets = parse_optional_targets(
            raw_worker.get("bot_targets"),
            f"workers[{index}].bot_targets",
        )
        if not targets and not bot_targets:
            raise ConfigError(
                f"workers[{index}] must set at least one target in targets or bot_targets"
            )
        filters = parse_filter_config(raw_worker.get("filters") or {})
        session_string = clean_optional_string(raw_worker.get("session_string"))
        session_file = resolve_optional_path(base_dir, raw_worker.get("session_file"))
        if session_string and session_file:
            raise ConfigError(
                f"workers[{index}] cannot set both session_string and session_file"
            )
        workers.append(
            WorkerConfig(
                name=name,
                sources=sources,
                targets=targets,
                bot_targets=bot_targets,
                forward_strategy=normalize_optional_forward_strategy(
                    raw_worker.get("forward_strategy"),
                    f"workers[{index}].forward_strategy",
                ),
                enabled=bool(raw_worker.get("enabled", True)),
                include_edits=bool(raw_worker.get("include_edits", False)),
                forward_own_messages=bool(raw_worker.get("forward_own_messages", False)),
                session_string=session_string,
                session_file=session_file,
                filters=filters,
            )
        )
    return workers


def parse_targets(data: Any, field_name: str) -> list[ForwardTarget]:
    if not isinstance(data, list) or not data:
        raise ConfigError(f"{field_name} must be a non-empty list")
    targets: list[ForwardTarget] = []
    for index, raw_target in enumerate(data, start=1):
        if isinstance(raw_target, (str, int)):
            targets.append(ForwardTarget(chat=raw_target))
            continue
        if not isinstance(raw_target, dict):
            raise ConfigError(f"{field_name}[{index}] must be a string, int or object")
        targets.append(
            ForwardTarget(
                chat=parse_chat_reference(raw_target.get("chat"), f"{field_name}[{index}].chat"),
                silent=bool(raw_target.get("silent", False)),
                drop_author=bool(raw_target.get("drop_author", False)),
                drop_media_captions=bool(raw_target.get("drop_media_captions", False)),
            )
        )
    return targets


def parse_optional_targets(data: Any, field_name: str) -> list[ForwardTarget]:
    if data in (None, ""):
        return []
    if not isinstance(data, list):
        raise ConfigError(f"{field_name} must be a list")
    if not data:
        return []
    return parse_targets(data, field_name)


def parse_simple_targets(env_values: dict[str, str]) -> list[ForwardTarget]:
    targets_raw = env_optional_from(env_values, "TG_TARGET_CHATS") or env_optional_from(
        env_values,
        "TG_TARGET_CHAT",
    )
    if not targets_raw:
        raise ConfigError("simple mode requires TG_TARGET_CHATS")
    targets = [item for item in parse_list_value(targets_raw) if item]
    if not targets:
        raise ConfigError("simple mode requires at least one target in TG_TARGET_CHATS")
    return [ForwardTarget(chat=parse_chat_reference(item, "TG_TARGET_CHATS")) for item in targets]


def parse_simple_optional_targets(env_values: dict[str, str], name: str) -> list[ForwardTarget]:
    targets_raw = env_optional_from(env_values, name)
    if not targets_raw:
        return []
    targets = [item for item in parse_list_value(targets_raw) if item]
    return [ForwardTarget(chat=parse_chat_reference(item, name)) for item in targets]


def parse_filter_config(
    data: Any,
) -> FilterConfig:
    if not isinstance(data, dict):
        raise ConfigError("filters must be a mapping object")
    media_only = bool(data.get("media_only", False))
    text_only = bool(data.get("text_only", False))
    return FilterConfig(
        keywords_any=normalize_keywords(data.get("keywords_any")),
        keywords_all=normalize_keywords(data.get("keywords_all")),
        block_keywords=normalize_keywords(data.get("block_keywords")),
        regex_any=normalize_regex_patterns(data.get("regex_any"), "filters.regex_any"),
        regex_all=normalize_regex_patterns(data.get("regex_all"), "filters.regex_all"),
        regex_block=normalize_regex_patterns(data.get("regex_block"), "filters.regex_block"),
        resource_presets=normalize_resource_presets(
            data.get("resource_presets"),
            "filters.resource_presets",
        ),
        media_only=media_only,
        text_only=text_only,
        content_match_mode=normalize_content_match_mode(
            data.get("content_match_mode"),
            "filters.content_match_mode",
        ),
        case_sensitive=bool(data.get("case_sensitive", False)),
    )


def normalize_keywords(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ConfigError("keyword filters must be arrays of strings")
    normalized: list[str] = []
    for item in value:
        keyword = clean_optional_string(item)
        if keyword:
            normalized.append(keyword)
    return normalized


def normalize_regex_patterns(value: Any, field_name: str = "regex filters") -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ConfigError("regex filters must be arrays of strings")
    normalized: list[str] = []
    for index, item in enumerate(value, start=1):
        pattern = clean_optional_string(item)
        if not pattern:
            continue
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ConfigError(f"{field_name}[{index}] is invalid regex") from exc
        normalized.append(pattern)
    return normalized


def normalize_resource_presets(value: Any, field_name: str = "resource_presets") -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ConfigError(f"{field_name} must be an array of strings")
    normalized: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value, start=1):
        raw_text = clean_optional_string(item)
        if not raw_text:
            continue
        preset = RESOURCE_PRESET_ALIASES.get(raw_text.strip().lower(), raw_text.strip().lower())
        if preset not in RESOURCE_PRESET_VALUES:
            raise ConfigError(f"{field_name}[{index}] is invalid")
        if preset in seen:
            continue
        seen.add(preset)
        normalized.append(preset)
    return normalized


def parse_chat_reference(value: Any, field_name: str) -> str | int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            raise ConfigError(f"{field_name} is required")
        if cleaned.startswith("-") and cleaned[1:].isdigit():
            return int(cleaned)
        return cleaned
    raise ConfigError(f"{field_name} must be a channel username or numeric id")


def parse_source_references(value: Any, field_name: str) -> list[str | int]:
    if value in (None, ""):
        raise ConfigError(f"{field_name} is required")

    raw_items: list[Any]
    if isinstance(value, list):
        raw_items = list(value)
    elif isinstance(value, int):
        raw_items = [value]
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ConfigError(f"{field_name} is required")
        raw_items = parse_list_value(text)
    else:
        raise ConfigError(f"{field_name} must be a channel username, numeric id or list")

    sources: list[str | int] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_items, start=1):
        source = parse_chat_reference(item, f"{field_name}[{index}]")
        source_key = str(source)
        if source_key in seen:
            continue
        seen.add(source_key)
        sources.append(source)
    if not sources:
        raise ConfigError(f"{field_name} is required")
    return sources


def parse_proxy_value_list(value: Any, field_name: str) -> list[ProxyConfig]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        proxies: list[ProxyConfig] = []
        for index, item in enumerate(value, start=1):
            proxies.append(parse_proxy_value(item, f"{field_name}[{index}]"))
        return dedupe_proxy_list(proxies)
    text = clean_optional_string(value)
    if not text:
        return []
    return dedupe_proxy_list(
        [parse_proxy_value(item, field_name) for item in parse_list_value(text)]
    )


def parse_proxy_value(value: Any, field_name: str = "proxy") -> ProxyConfig:
    text = clean_optional_string(value)
    if not text:
        raise ConfigError(f"{field_name} is invalid")

    raw_text = text
    if "://" not in raw_text:
        raw_text = f"socks5://{raw_text}"
    parsed = urlparse(raw_text)
    proxy_type = clean_optional_string(parsed.scheme)
    host = clean_optional_string(parsed.hostname)
    port = parsed.port
    if not proxy_type or not host or port in (None, ""):
        raise ConfigError(f"{field_name} is invalid")

    query = parse_qs(parsed.query or "", keep_blank_values=True)
    rdns = True
    rdns_values = query.get("rdns")
    if rdns_values:
        rdns = parse_bool(rdns_values[-1], True)

    return ProxyConfig(
        proxy_type=proxy_type.lower(),
        host=host,
        port=int(port),
        username=unquote(parsed.username) if parsed.username else None,
        password=unquote(parsed.password) if parsed.password else None,
        rdns=rdns,
    )


def dedupe_proxy_list(values: list[ProxyConfig]) -> list[ProxyConfig]:
    result: list[ProxyConfig] = []
    seen: set[tuple[str, str, int, str | None, str | None, bool]] = set()
    for item in values:
        key = (
            item.proxy_type.strip().lower(),
            item.host.strip().lower(),
            int(item.port),
            (item.username or "").strip() or None,
            (item.password or "").strip() or None,
            bool(item.rdns),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def clean_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def merge_unique_strings(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            key = item.strip()
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(key)
    return merged


def serialize_string_list(items: list[str]) -> str | None:
    if not items:
        return None
    return ",".join(items)


def parse_bot_tokens(value: Any, field_name: str = "bot_token") -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return merge_unique_strings(parse_list_value(value))
    if isinstance(value, list):
        normalized: list[str] = []
        for index, item in enumerate(value, start=1):
            token = clean_optional_string(item)
            if token:
                normalized.append(token)
            elif item not in (None, ""):
                raise ConfigError(f"{field_name}[{index}] must be a string")
        return merge_unique_strings(normalized)
    raise ConfigError(f"{field_name} must be a string or list")


def normalize_forward_strategy(
    value: Any,
    field_name: str = "forward_strategy",
) -> str:
    text = (clean_optional_string(value) or FORWARD_STRATEGY_PARALLEL).lower().replace("-", "_")
    aliases = {
        "parallel": FORWARD_STRATEGY_PARALLEL,
        "both": FORWARD_STRATEGY_PARALLEL,
        "all": FORWARD_STRATEGY_PARALLEL,
        "account_first": FORWARD_STRATEGY_ACCOUNT_FIRST,
        "account": FORWARD_STRATEGY_ACCOUNT_FIRST,
        "account_only": FORWARD_STRATEGY_ACCOUNT_ONLY,
        "user": FORWARD_STRATEGY_ACCOUNT_FIRST,
        "user_only": FORWARD_STRATEGY_ACCOUNT_ONLY,
        "user_first": FORWARD_STRATEGY_ACCOUNT_FIRST,
        "bot_first": FORWARD_STRATEGY_BOT_FIRST,
        "bot": FORWARD_STRATEGY_BOT_FIRST,
        "bot_only": FORWARD_STRATEGY_BOT_ONLY,
    }
    normalized = aliases.get(text, text)
    if normalized not in FORWARD_STRATEGY_VALUES:
        raise ConfigError(
            f"{field_name} must be one of: "
            f"{FORWARD_STRATEGY_PARALLEL}, "
            f"{FORWARD_STRATEGY_ACCOUNT_ONLY}, "
            f"{FORWARD_STRATEGY_ACCOUNT_FIRST}, "
            f"{FORWARD_STRATEGY_BOT_ONLY}, "
            f"{FORWARD_STRATEGY_BOT_FIRST}"
        )
    return normalized


def normalize_optional_forward_strategy(
    value: Any,
    field_name: str = "forward_strategy",
) -> str | None:
    text = clean_optional_string(value)
    if not text:
        return None
    normalized_text = text.lower().replace("-", "_")
    if normalized_text in {"inherit", "default", "global"}:
        return None
    return normalize_forward_strategy(text, field_name)


def resolve_forward_strategy(
    override_strategy: Any,
    default_strategy: Any,
    field_name: str = "forward_strategy",
) -> str:
    normalized_override = normalize_optional_forward_strategy(override_strategy, field_name)
    if normalized_override:
        return normalized_override
    return normalize_forward_strategy(default_strategy, field_name)


def filter_targets_by_forward_strategy(
    strategy: Any,
    account_targets: list[Any],
    bot_targets: list[Any],
    field_name: str = "forward_strategy",
) -> tuple[list[Any], list[Any]]:
    normalized = normalize_forward_strategy(strategy, field_name)
    if normalized == FORWARD_STRATEGY_ACCOUNT_ONLY:
        return list(account_targets), []
    if normalized == FORWARD_STRATEGY_BOT_ONLY:
        return [], list(bot_targets)
    return list(account_targets), list(bot_targets)


def normalize_search_mode(
    value: Any,
    field_name: str = "search_mode",
) -> str:
    text = (clean_optional_string(value) or SEARCH_MODE_FAST).lower().replace("-", "_")
    aliases = {
        SEARCH_MODE_FAST: SEARCH_MODE_FAST,
        "quick": SEARCH_MODE_FAST,
        "simple": SEARCH_MODE_FAST,
        "deep": SEARCH_MODE_FAST,
        "full": SEARCH_MODE_FAST,
        "landing_page": SEARCH_MODE_FAST,
    }
    normalized = aliases.get(text, text)
    if normalized not in SEARCH_MODE_VALUES:
        raise ConfigError(f"{field_name} must be: {SEARCH_MODE_FAST}")
    return normalized


def parse_regex_env(env_values: dict[str, str], name: str) -> list[str]:
    raw_value = env_optional_from(env_values, name)
    if not raw_value:
        return []
    return normalize_regex_patterns(split_multiline_value(raw_value), name)


def parse_resource_presets_env(env_values: dict[str, str], name: str) -> list[str]:
    raw_value = env_optional_from(env_values, name)
    if not raw_value:
        return []
    return normalize_resource_presets(parse_list_value(raw_value), name)
def normalize_content_match_mode(
    value: Any,
    field_name: str = "content_match_mode",
) -> str:
    text = (clean_optional_string(value) or CONTENT_MATCH_MODE_ALL).lower().replace("-", "_")
    aliases = {
        "all": CONTENT_MATCH_MODE_ALL,
        "both": CONTENT_MATCH_MODE_ALL,
        "require_all": CONTENT_MATCH_MODE_ALL,
        "any": CONTENT_MATCH_MODE_ANY,
        "either": CONTENT_MATCH_MODE_ANY,
        "one": CONTENT_MATCH_MODE_ANY,
        "or": CONTENT_MATCH_MODE_ANY,
    }
    normalized = aliases.get(text, text)
    if normalized not in CONTENT_MATCH_MODE_VALUES:
        raise ConfigError(
            f"{field_name} must be one of: "
            f"{CONTENT_MATCH_MODE_ALL}, "
            f"{CONTENT_MATCH_MODE_ANY}"
        )
    return normalized


def normalize_rate_limit_delay(
    value: Any,
    field_name: str = "rate_limit_delay_seconds",
) -> float:
    if value in (None, ""):
        return DEFAULT_RATE_LIMIT_DELAY_SECONDS
    try:
        delay = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field_name} must be a non-negative number") from exc
    if delay < 0:
        raise ConfigError(f"{field_name} must be a non-negative number")
    return delay


def expand_env_object(value: Any) -> Any:
    if isinstance(value, str):
        return expand_env_in_text(value)
    if isinstance(value, list):
        return [expand_env_object(item) for item in value]
    if isinstance(value, dict):
        return {key: expand_env_object(item) for key, item in value.items()}
    return value


def resolve_optional_path(base_dir: Path, value: Any) -> str | None:
    text = clean_optional_string(value)
    if not text:
        return None
    normalized_text = text.replace("\\", "/")
    path = Path(normalized_text)
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return str(path)


def is_env_path(path: Path) -> bool:
    return path.name.lower() == ".env" or path.suffix.lower() == ".env"


def load_dotenv_if_exists(path: Path) -> None:
    if path.exists():
        load_dotenv(path, override=False)


def env_optional(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def env_required(name: str, message: str) -> str:
    value = env_optional(name)
    if value is None:
        raise ConfigError(message)
    return value


def env_optional_from(env_values: dict[str, str], name: str) -> str | None:
    if name in os.environ:
        return env_optional(name)
    value = env_values.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def env_required_from(env_values: dict[str, str], name: str, message: str) -> str:
    value = env_optional_from(env_values, name)
    if value is None:
        raise ConfigError(message)
    return value


def parse_list_env(env_values: dict[str, str], name: str) -> list[str]:
    value = env_optional_from(env_values, name)
    if not value:
        return []
    return parse_list_value(value)


def split_multiline_value(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").replace("\r\n", "\n").split("\n") if item.strip()]


def parse_list_value(value: str) -> list[str]:
    return [item.strip() for item in LIST_SPLIT_PATTERN.split(value) if item.strip()]


def parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ConfigError(f"invalid boolean value `{value}`")


def parse_int_env(env_values: dict[str, str], name: str, default: int) -> int:
    value = env_optional_from(env_values, name)
    if value is None:
        return default
    return max(1, int(value))


def parse_float_env(env_values: dict[str, str], name: str, default: float) -> float:
    value = env_optional_from(env_values, name)
    if value is None:
        return default
    return normalize_rate_limit_delay(value, name)


def read_env_context(path: Path) -> dict[str, str]:
    file_values = read_env_file(path)
    merged = {
        key: value
        for key, value in os.environ.items()
        if (key.startswith("TG_") or key.startswith("SOURCE_")) and key not in file_values
    }
    merged.update(file_values)
    return merged


def worker_runtime_from_payload(payload: dict[str, Any]) -> WorkerRuntimeConfig:
    telegram_data = payload["telegram"]
    filters_data = payload["filters"]
    targets_data = payload["targets"]
    proxy_data = telegram_data.get("proxy")
    proxies_data = telegram_data.get("proxies") or []
    telegram = TelegramSettings(
        api_id=int(telegram_data["api_id"]),
        api_hash=str(telegram_data["api_hash"]),
        session_string=clean_optional_string(telegram_data.get("session_string")),
        session_file=clean_optional_string(telegram_data.get("session_file")),
        proxy=ProxyConfig(**proxy_data) if proxy_data else None,
        proxies=[ProxyConfig(**item) for item in proxies_data if isinstance(item, dict)],
        bot_token=serialize_string_list(
            parse_bot_tokens(telegram_data.get("bot_token"), "telegram.bot_token")
        ),
        forward_strategy=normalize_forward_strategy(
            telegram_data.get("forward_strategy"),
            "telegram.forward_strategy",
        ),
        rate_limit_protection=bool(telegram_data.get("rate_limit_protection", False)),
        rate_limit_delay_seconds=normalize_rate_limit_delay(
            telegram_data.get("rate_limit_delay_seconds"),
            "telegram.rate_limit_delay_seconds",
        ),
        startup_notify_enabled=bool(telegram_data.get("startup_notify_enabled", False)),
        startup_notify_message=clean_optional_string(telegram_data.get("startup_notify_message")),
        search_default_mode=normalize_search_mode(
            telegram_data.get("search_default_mode"),
            "telegram.search_default_mode",
        ),
    )
    return WorkerRuntimeConfig(
        name=str(payload["name"]),
        sources=parse_source_references(
            payload.get("sources", payload.get("source")),
            "worker.sources",
        ),
        targets=[ForwardTarget(**target) for target in targets_data],
        bot_targets=[ForwardTarget(**target) for target in payload.get("bot_targets", [])],
        forward_strategy=normalize_optional_forward_strategy(
            payload.get("forward_strategy"),
            "worker.forward_strategy",
        ),
        include_edits=bool(payload.get("include_edits", False)),
        forward_own_messages=bool(payload.get("forward_own_messages", False)),
        filters=parse_filter_config(filters_data),
        telegram=telegram,
    )


def worker_config_digest(runtime_worker: WorkerRuntimeConfig) -> str:
    return json.dumps(
        runtime_worker.as_payload(),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
