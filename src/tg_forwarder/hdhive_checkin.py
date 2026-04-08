"""HDHive (https://hdhive.com/) check-in: Premium API Key 或网页 Cookie（Next Server Action）。"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import random
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from time import time
from urllib.parse import quote

import socks
from sockshandler import SocksiPyHandler

from tg_forwarder.config import ConfigError, ProxyConfig, parse_proxy_from_env
from tg_forwarder.env_utils import read_env_file

CHECKIN_URL_API = "https://hdhive.com/api/open/checkin"
CHECKIN_URL_SITE = "https://hdhive.com/"
# 轮询间隔：到点后每天最多尝试一次；缩短间隔可更快在「新的一天」触发
CHECKIN_POLL_INTERVAL_SEC = 300
DEFAULT_RETRY_MAX_ATTEMPTS = 4
DEFAULT_RETRY_BASE_DELAY_SEC = 60
DEFAULT_RETRY_MAX_DELAY_SEC = 1800
DEFAULT_RETRY_JITTER_SEC = 15

HDHIVE_METHOD_API_KEY = "api_key"
HDHIVE_METHOD_COOKIE = "cookie"


def _env_bool(values: dict[str, str], key: str) -> bool:
    raw = values.get(key)
    if raw is None:
        return False
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def state_path_for_env(env_path: Path) -> Path:
    return env_path.resolve().parent / ".hdhive_checkin_state.json"


def _load_state(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(path: Path, data: dict[str, object]) -> None:
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def load_checkin_state_for_env(env_path: Path) -> dict[str, object]:
    """Expose current check-in state for health/status endpoints."""
    return _load_state(state_path_for_env(env_path))


def resolve_hdhive_proxy(values: dict[str, str]) -> tuple[ProxyConfig | None, str | None]:
    """When HDHIVE_CHECKIN_USE_PROXY is set, return Telegram simple proxy or an error message."""
    if not _env_bool(values, "HDHIVE_CHECKIN_USE_PROXY"):
        return None, None
    try:
        proxy = parse_proxy_from_env(values)
    except ConfigError as exc:
        return None, str(exc)
    if proxy is None:
        return None, (
            "已开启通过代理访问 HDHive，但未填写代理地址或端口，"
            "请在「系统与连接」的代理设置中填写并保存。"
        )
    ptype = proxy.proxy_type.strip().lower()
    if ptype == "mtproto":
        return None, "HDHive 签到不支持 MTProto 代理，请改用 SOCKS5 或 HTTP 代理。"
    return proxy, None


def normalize_hdhive_checkin_method(raw: str | None) -> str:
    m = (raw or HDHIVE_METHOD_API_KEY).strip().lower()
    if m in (HDHIVE_METHOD_COOKIE, "site", "web"):
        return HDHIVE_METHOD_COOKIE
    return HDHIVE_METHOD_API_KEY


class _NoRedirectHTTPRedirectHandler(urllib.request.HTTPRedirectHandler):
    """与 PHP curl 的 CURLOPT_FOLLOWLOCATION=false 一致：不跟随 3xx，保留首包响应体。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _urllib_proxy_url(proxy: ProxyConfig) -> str:
    """URL for urllib ProxyHandler (HTTPS 经代理时仍用 http:// 指向本地 HTTP 代理端口，除非类型为 https 代理)."""
    user = (proxy.username or "").strip()
    password = proxy.password or ""
    auth = ""
    if user:
        auth = f"{quote(user, safe='')}:{quote(password, safe='')}@"
    ptype = proxy.proxy_type.strip().lower()
    scheme = "https" if ptype == "https" else "http"
    return f"{scheme}://{auth}{proxy.host}:{proxy.port}"


def _decode_response_body(data: bytes, _content_encoding: str = "") -> str:
    if data.startswith(b"\x1f\x8b"):
        try:
            data = gzip.decompress(data)
        except OSError:
            pass
    return data.decode("utf-8", errors="replace")


def _urlopen_checkin(
    req: urllib.request.Request,
    opener: urllib.request.OpenerDirector | None = None,
) -> tuple[int, str]:
    open_fn = opener.open if opener is not None else urllib.request.urlopen
    try:
        with open_fn(req, timeout=45) as resp:
            raw = resp.read()
            ce = ""
            if hasattr(resp, "headers"):
                ce = resp.headers.get("Content-Encoding", "") or ""
            text = _decode_response_body(raw, ce)
            return resp.getcode(), text
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read()
            ce = exc.headers.get("Content-Encoding", "") if exc.headers else ""
            text = _decode_response_body(raw, ce)
        except OSError:
            text = ""
        return exc.code, text
    except OSError as exc:
        return -1, str(exc)


def _build_proxy_opener(
    proxy: ProxyConfig | None,
    *,
    follow_redirects: bool = True,
) -> urllib.request.OpenerDirector:
    """与「系统与连接」单代理一致：HTTP(S) 用 ProxyHandler，SOCKS 用 PySocks（rdns≈socks5h）。"""
    handlers: list[urllib.request.BaseHandler] = []
    if not follow_redirects:
        handlers.append(_NoRedirectHTTPRedirectHandler())
    if proxy is None:
        return urllib.request.build_opener(*handlers)

    ptype = proxy.proxy_type.strip().lower()
    if ptype in {"http", "https"}:
        proxy_url = _urllib_proxy_url(proxy)
        handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
        return urllib.request.build_opener(*handlers, handler)

    if ptype in {"socks5", "socks4"}:
        sock_type = socks.SOCKS5 if ptype == "socks5" else socks.SOCKS4
        handler = SocksiPyHandler(
            sock_type,
            proxy.host,
            proxy.port,
            rdns=proxy.rdns,
            username=proxy.username or None,
            password=proxy.password or None,
        )
        return urllib.request.build_opener(*handlers, handler)

    raise ValueError(f"不支持的代理类型：{proxy.proxy_type}")


def _urlopen_with_proxy(req: urllib.request.Request, proxy: ProxyConfig | None) -> tuple[int, str]:
    if proxy is None:
        return _urlopen_checkin(req, None)
    try:
        opener = _build_proxy_opener(proxy)
    except ValueError as exc:
        return -1, str(exc)
    return _urlopen_checkin(req, opener)


def post_hdhive_checkin_api(
    api_key: str,
    is_gambler: bool,
    proxy: ProxyConfig | None = None,
) -> tuple[int, str]:
    """Premium：POST /api/open/checkin，请求头 X-API-Key。"""
    headers = {"X-API-Key": api_key.strip()}
    body: bytes = b""
    if is_gambler:
        body = json.dumps({"is_gambler": True}).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(CHECKIN_URL_API, data=body if body else None, headers=headers, method="POST")
    return _urlopen_with_proxy(req, proxy)


def post_hdhive_checkin_cookie(
    cookie_header: str,
    next_action: str,
    next_router_state_tree: str,
    is_gambler: bool,
    proxy: ProxyConfig | None = None,
) -> tuple[int, str]:
    """非 Premium：模拟站点 Next Server Action，POST 首页，正文为 [true]/[false]。"""
    body = b"[true]" if is_gambler else b"[false]"
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Cookie": cookie_header.strip(),
        "Next-Action": next_action.strip(),
        "Next-Router-State-Tree": next_router_state_tree.strip(),
        "Origin": "https://hdhive.com",
        "Referer": "https://hdhive.com/",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
    }
    req = urllib.request.Request(CHECKIN_URL_SITE, data=body, headers=headers, method="POST")
    return _urlopen_with_proxy(req, proxy)


def parse_hdhive_site_rsc_message(text: str) -> tuple[str, str]:
    """从 text/x-component 响应体中解析以 ``1:`` 开头的行的 JSON，得到 (message, description)。"""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("1:"):
            continue
        colon = line.find(":")
        json_str = line[colon + 1 :].strip() if colon >= 0 else ""
        if not json_str:
            continue
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        err = data.get("error")
        if isinstance(err, dict):
            return (
                str(err.get("message") or "").strip(),
                str(err.get("description") or "").strip(),
            )
        msg = str(data.get("message") or "").strip()
        desc = str(data.get("description") or "").strip()
        if msg or desc:
            return (msg, desc)
    return ("", "")


def parse_api_checkin_message(text: str) -> tuple[str, str]:
    """开放 API JSON 响应中尝试取出 message / description。"""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return ("", "")
    if not isinstance(data, dict):
        return ("", "")
    err = data.get("error")
    if isinstance(err, dict):
        return (
            str(err.get("message") or "").strip(),
            str(err.get("description") or "").strip(),
        )
    msg = str(data.get("message") or "").strip()
    desc = str(data.get("description") or "").strip()
    return (msg, desc)


def run_hdhive_checkin(
    *,
    method: str,
    api_key: str,
    cookie_header: str,
    next_action: str,
    next_router_state_tree: str,
    is_gambler: bool,
    proxy: ProxyConfig | None,
) -> tuple[int, str, str, str]:
    """
    执行签到。返回 (http_status, raw_body, display_message, display_description)。
    display_* 用于前端标题与正文；可能为空则前端回退 raw。
    """
    m = normalize_hdhive_checkin_method(method)
    if m == HDHIVE_METHOD_COOKIE:
        status, raw = post_hdhive_checkin_cookie(
            cookie_header,
            next_action,
            next_router_state_tree,
            is_gambler,
            proxy,
        )
        msg, desc = parse_hdhive_site_rsc_message(raw) if status > 0 else ("", "")
        return status, raw, msg, desc

    status, raw = post_hdhive_checkin_api(api_key, is_gambler, proxy)
    msg, desc = parse_api_checkin_message(raw) if status > 0 else ("", "")
    return status, raw, msg, desc


def _env_int(values: dict[str, str], key: str, default: int, *, minimum: int = 0, maximum: int = 10_000) -> int:
    raw = (values.get(key) or "").strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, parsed))


def _next_retry_delay_seconds(values: dict[str, str], attempt_index: int) -> int:
    base = _env_int(values, "HDHIVE_CHECKIN_RETRY_BASE_SECONDS", DEFAULT_RETRY_BASE_DELAY_SEC, minimum=5, maximum=3600)
    max_delay = _env_int(values, "HDHIVE_CHECKIN_RETRY_MAX_SECONDS", DEFAULT_RETRY_MAX_DELAY_SEC, minimum=10, maximum=24 * 3600)
    jitter = _env_int(values, "HDHIVE_CHECKIN_RETRY_JITTER_SECONDS", DEFAULT_RETRY_JITTER_SEC, minimum=0, maximum=300)
    exp_delay = base * (2 ** max(0, attempt_index - 1))
    capped = min(exp_delay, max_delay)
    if jitter <= 0:
        return int(capped)
    low = max(1, int(capped - jitter))
    high = int(capped + jitter)
    return random.randint(low, high)


def _should_retry_status(status: int) -> bool:
    if status < 0:
        return True
    if status in {408, 425, 429}:
        return True
    return status >= 500


def maybe_run_scheduled_checkin(env_path: Path, log: logging.Logger) -> None:
    """If enabled in .env, attempt check-in with bounded retry/backoff."""
    values = read_env_file(env_path)
    if not _env_bool(values, "HDHIVE_CHECKIN_ENABLED"):
        return

    method = normalize_hdhive_checkin_method(values.get("HDHIVE_CHECKIN_METHOD"))
    api_key = (values.get("HDHIVE_API_KEY") or "").strip()
    cookie_header = (values.get("HDHIVE_COOKIE") or "").strip()
    next_action = (values.get("HDHIVE_NEXT_ACTION") or "").strip()
    next_router = (values.get("HDHIVE_NEXT_ROUTER_STATE_TREE") or "").strip()

    if method == HDHIVE_METHOD_COOKIE:
        if not cookie_header or not next_action or not next_router:
            log.warning(
                "HDHive 自动签到未执行：Cookie 模式需填写 HDHIVE_COOKIE、HDHIVE_NEXT_ACTION、"
                "HDHIVE_NEXT_ROUTER_STATE_TREE 并保存。",
            )
            return
    elif not api_key:
        return

    proxy, proxy_err = resolve_hdhive_proxy(values)
    if proxy_err:
        log.warning("HDHive 自动签到未执行（代理配置无效）：%s", proxy_err)
        return

    is_gambler = _env_bool(values, "HDHIVE_CHECKIN_GAMBLER")
    today = date.today().isoformat()
    spath = state_path_for_env(env_path)
    state = _load_state(spath)
    last_success_date = str(state.get("last_success_date") or "").strip()
    if last_success_date == today:
        return

    if str(state.get("retry_exhausted_date") or "").strip() == today:
        return

    attempt_date = str(state.get("attempt_date") or "").strip()
    attempts_today = int(state.get("attempt_count_today") or 0) if attempt_date == today else 0
    max_attempts = _env_int(values, "HDHIVE_CHECKIN_MAX_RETRIES", DEFAULT_RETRY_MAX_ATTEMPTS, minimum=1, maximum=30)
    if attempts_today >= max_attempts:
        state["retry_exhausted_date"] = today
        _save_state(spath, state)
        return

    now_epoch = int(time())
    next_retry_epoch = int(state.get("next_retry_epoch") or 0) if attempt_date == today else 0
    if next_retry_epoch > now_epoch:
        return

    status, raw, msg, desc = run_hdhive_checkin(
        method=method,
        api_key=api_key,
        cookie_header=cookie_header,
        next_action=next_action,
        next_router_state_tree=next_router,
        is_gambler=is_gambler,
        proxy=proxy,
    )
    preview = (raw[:800] if raw else "") or f"{msg}: {desc}".strip()
    if msg or desc:
        line = "HDHive 自动签到 | HTTP %s | message=%s | description=%s"
        args = (status, (msg or "-")[:500], (desc or "-")[:500])
        if status == 200:
            log.info(line, *args)
        else:
            log.warning(line, *args)
    elif status == 200:
        log.info("HDHive 自动签到 HTTP %s: %s", status, preview[:500])
    elif status > 0:
        log.warning("HDHive 自动签到 HTTP %s: %s", status, preview[:500])
    else:
        log.warning("HDHive 自动签到失败: %s", preview[:500])
    attempts_today += 1
    state["attempt_date"] = today
    state["attempt_count_today"] = attempts_today
    state["last_http_status"] = status
    state["last_body_preview"] = preview
    state["last_attempt_epoch"] = now_epoch

    if status == 200:
        state["last_success_date"] = today
        state["next_retry_epoch"] = 0
        state["retry_exhausted_date"] = ""
    elif _should_retry_status(status):
        if attempts_today >= max_attempts:
            state["retry_exhausted_date"] = today
            state["next_retry_epoch"] = 0
            log.warning("HDHive 自动签到重试次数已达上限：%s/%s", attempts_today, max_attempts)
        else:
            delay = _next_retry_delay_seconds(values, attempts_today)
            state["next_retry_epoch"] = now_epoch + delay
            log.warning("HDHive 自动签到将在约 %s 秒后重试（%s/%s）", delay, attempts_today, max_attempts)
    else:
        state["retry_exhausted_date"] = today
        state["next_retry_epoch"] = 0
    _save_state(spath, state)


async def hdhive_checkin_loop(stop: asyncio.Event, env_path: Path, log: logging.Logger) -> None:
    while not stop.is_set():
        try:
            await asyncio.to_thread(maybe_run_scheduled_checkin, env_path, log)
        except Exception:
            log.exception("HDHive 签到轮询异常")
        try:
            await asyncio.wait_for(stop.wait(), timeout=CHECKIN_POLL_INTERVAL_SEC)
            break
        except asyncio.TimeoutError:
            continue
