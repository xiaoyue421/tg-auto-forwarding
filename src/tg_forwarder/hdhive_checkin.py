"""HDHive (https://hdhive.com/) check-in: Premium API Key 或网页 Cookie（Next Server Action）。"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import random
import re
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from time import sleep, time
from urllib.parse import quote

import socks
from sockshandler import SocksiPyHandler

from tg_forwarder.config import ConfigError, ProxyConfig, parse_proxy_from_env, parse_proxy_value
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

# Cookie 模式 POST https://hdhive.com/ 时，请求头 ``Next-Action`` / ``Next-Router-State-Tree`` 的默认写死值
#（与浏览器 Network 里名称一致；若站点发版可改下面两常量，或用 .env 的 HDHIVE_CHECKIN_NEXT_* 覆盖）。
# 使用处：``post_hdhive_checkin_cookie`` ← ``cookie_checkin_next_meta_from_env`` ← ``run_hdhive_checkin``。
HDHIVE_DEFAULT_CHECKIN_NEXT_ACTION = "402b7e1f30165a6ded288e0043f2dbb11db4a998a1"
HDHIVE_DEFAULT_CHECKIN_NEXT_ROUTER_STATE_TREE = (
    "%5B%22%22%2C%7B%22children%22%3A%5B%22(app)%22%2C%7B%22children%22%3A%5B%22__PAGE__%22%2C%7B%7D%2Cnull%2Cnull%5D%7D%2Cnull%2Cnull%5D%7D%2Cnull%2Cnull%2Ctrue%5D"
)


def cookie_checkin_next_meta_from_env(values: dict[str, str] | None) -> tuple[str, str]:
    """Cookie 签到用的 Next Server Action 元数据：优先读 .env，缺省用内置默认值。

    站点发版后内置哈希可能与线上不一致，须在浏览器 Network 里复制本次签到请求的
    ``Next-Action``、``Next-Router-State-Tree`` 填到 .env（见 ``HDHIVE_CHECKIN_NEXT_*``）。
    仍兼容旧键名 ``HDHIVE_NEXT_ACTION`` / ``HDHIVE_NEXT_ROUTER_STATE_TREE``。
    """
    if not values:
        return HDHIVE_DEFAULT_CHECKIN_NEXT_ACTION, HDHIVE_DEFAULT_CHECKIN_NEXT_ROUTER_STATE_TREE

    def pick(*keys: str, default: str) -> str:
        for k in keys:
            v = (values.get(k) or "").strip()
            if v:
                return v
        return default

    return (
        pick("HDHIVE_CHECKIN_NEXT_ACTION", "HDHIVE_NEXT_ACTION", default=HDHIVE_DEFAULT_CHECKIN_NEXT_ACTION),
        pick(
            "HDHIVE_CHECKIN_NEXT_ROUTER_STATE_TREE",
            "HDHIVE_NEXT_ROUTER_STATE_TREE",
            default=HDHIVE_DEFAULT_CHECKIN_NEXT_ROUTER_STATE_TREE,
        ),
    )


def _normalize_hdhive_cookie_header_for_request(cookie_header: str) -> str:
    """去掉首尾空白；若误把 ``Cookie:`` 前缀写进 .env 则剥掉。"""
    ch = (cookie_header or "").strip()
    if ch.lower().startswith("cookie:"):
        ch = ch[7:].lstrip()
    return ch


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
    """解析 HDHive 出站 urllib 使用的代理（签到、Cookie 定时/立即刷新 GET 首页等共用本函数）。

    规则顺序：

    1. ``HDHIVE_CHECKIN_PROXY_URL`` / ``HDHIVE_CHECKIN_HTTP_PROXY`` 只要非空即使用（**不必**再开
       ``HDHIVE_CHECKIN_USE_PROXY``），便于只给 HDHive 配 HTTP 代理而 Telegram 仍走 SOCKS。
    2. 否则若 ``HDHIVE_CHECKIN_USE_PROXY`` 为真，则用 ``TG_PROXY_*`` 单代理（与控制台「系统与连接」一致）。
    3. 以上均未启用则直连（``None``）。

    经 SOCKS 访问 HTTPS 仍 ``SSL: UNEXPECTED_EOF`` 时，可用第 1 条填 ``http://127.0.0.1:7890`` 等。
    """
    override = (values.get("HDHIVE_CHECKIN_PROXY_URL") or values.get("HDHIVE_CHECKIN_HTTP_PROXY") or "").strip()
    if override:
        try:
            proxy = parse_proxy_value(override, "HDHIVE_CHECKIN_PROXY_URL")
        except ConfigError as exc:
            return None, str(exc)
        ptype = proxy.proxy_type.strip().lower()
        if ptype == "mtproto":
            return None, "HDHive 签到不支持 MTProto 代理，请改用 SOCKS5 或 HTTP 代理。"
        return proxy, None

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


def _should_retry_urlopen_os_error(exc: OSError) -> bool:
    """TLS 读一半被掐、超时、连接重置等可短暂重试（常见于网络抖动或需走代理）。"""
    msg = str(exc).lower()
    needles = (
        "ssl",
        "eof",
        "connection reset",
        "broken pipe",
        "timed out",
        "timeout",
        "temporarily unavailable",
        "network is unreachable",
    )
    return any(n in msg for n in needles)


def _urlopen_checkin_full(
    req: urllib.request.Request,
    opener: urllib.request.OpenerDirector | None = None,
    *,
    max_attempts: int = 3,
) -> tuple[int, str, object]:
    """返回 (http_code, body_text, response_headers)，供 Cookie 签到后从 Set-Cookie 合并 token。"""
    open_fn = opener.open if opener is not None else urllib.request.urlopen
    last_exc: OSError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            with open_fn(req, timeout=45) as resp:
                raw = resp.read()
                ce = ""
                if hasattr(resp, "headers"):
                    ce = resp.headers.get("Content-Encoding", "") or ""
                text = _decode_response_body(raw, ce)
                return resp.getcode(), text, resp.headers
        except urllib.error.HTTPError as exc:
            try:
                raw = exc.read()
                ce = exc.headers.get("Content-Encoding", "") if exc.headers else ""
                text = _decode_response_body(raw, ce)
            except OSError:
                text = ""
            hdrs = exc.headers if exc.headers else {}
            return exc.code, text, hdrs
        except OSError as exc:
            last_exc = exc
            if attempt < max_attempts and _should_retry_urlopen_os_error(exc):
                sleep(min(0.7 * attempt, 2.5))
                continue
            return -1, str(exc), {}
    return -1, str(last_exc or OSError("unknown")), {}


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


def _urlopen_with_proxy(req: urllib.request.Request, proxy: ProxyConfig | None) -> tuple[int, str, object]:
    if proxy is None:
        return _urlopen_checkin_full(req, None)
    try:
        opener = _build_proxy_opener(proxy)
    except ValueError as exc:
        return -1, str(exc), {}
    return _urlopen_checkin_full(req, opener)


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
    status, raw, _h = _urlopen_with_proxy(req, proxy)
    return status, raw


def post_hdhive_checkin_cookie(
    cookie_header: str,
    next_action: str,
    next_router_state_tree: str,
    is_gambler: bool,
    proxy: ProxyConfig | None = None,
) -> tuple[int, str, object]:
    """非 Premium：模拟站点 Next Server Action，POST 首页，正文为 [true]/[false]。"""
    body = b"[true]" if is_gambler else b"[false]"
    cookie_value = _normalize_hdhive_cookie_header_for_request(cookie_header)
    headers = {
        "Accept": "text/x-component, text/html, application/xhtml+xml, application/xml, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Content-Type": "text/plain;charset=UTF-8",
        "Cookie": cookie_value,
        # 默认即文件顶部 HDHIVE_DEFAULT_CHECKIN_NEXT_*；可由 .env / CLI 覆盖后传入
        "Next-Action": next_action.strip(),
        "Next-Router-State-Tree": next_router_state_tree.strip(),
        "Origin": "https://hdhive.com",
        "Referer": "https://hdhive.com/",
        # 不显式发送 RSC:1：与浏览器里该次 Server Action POST 不一致时，服务端易返回整页 RSC。
        "Next-Url": "/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Sec-CH-UA": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "Sec-CH-UA-Mobile": "?0",
        "Sec-CH-UA-Platform": '"Windows"',
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
    }
    req = urllib.request.Request(CHECKIN_URL_SITE, data=body, headers=headers, method="POST")
    return _urlopen_with_proxy(req, proxy)


def _looks_like_nextjs_rsc_flight(text: str) -> bool:
    """站点返回整页 RSC flight 时常含这些片段，而非单行 JSON 签到结果。"""
    if not text or len(text) < 80:
        return False
    markers = (
        "$Sreact",
        "OutletBoundary",
        "ViewportBoundary",
        "MetadataBoundary",
        '"children"',
        "__PAGE__",
    )
    return sum(1 for m in markers if m in text) >= 3


def _extract_message_from_rsc_dict(data: dict) -> tuple[str, str]:
    """解析单行 flight JSON。HDHive 签到常见形如::

        {"error": {"success": false, "message": "签到失败", "description": "你已经签到过了…", "code": "400"}}

    也可能为顶层 message/description（无 error 包裹）。
    """
    err = data.get("error")
    if isinstance(err, dict):
        msg = str(err.get("message") or "").strip()
        desc = str(err.get("description") or "").strip()
        code = err.get("code")
        if code is not None and str(code).strip():
            code_s = str(code).strip()
            if desc:
                desc = f"{desc}（code={code_s}）"
            elif msg:
                desc = f"code={code_s}"
        return msg, desc
    return (
        str(data.get("message") or "").strip(),
        str(data.get("description") or "").strip(),
    )


def _json_loads_rsc_payload(payload: str) -> object | None:
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


def _iter_digit_prefixed_json_dicts(text: str):
    """解析 RSC flight 里 ``数字:`` 开头的块；若 JSON 跨多行则合并后再 ``json.loads``。

    站点改版后可能出现 ``1:{"error":`` 与后续行拼成完整 JSON，原先按单行解析会失败并误判为「整页 RSC」。
    """
    lines = text.splitlines()
    i = 0
    n = len(lines)
    digit_line = re.compile(r"^\s*\d+\s*:")
    while i < n:
        stripped = lines[i].strip()
        if ":" not in stripped:
            i += 1
            continue
        colon = stripped.find(":")
        prefix = stripped[:colon].strip()
        if not prefix.isdigit():
            i += 1
            continue
        payload = stripped[colon + 1 :].lstrip()
        merged = payload
        j = i
        data = _json_loads_rsc_payload(merged)
        while data is None and j + 1 < n:
            nxt = lines[j + 1]
            if digit_line.match(nxt.strip()):
                break
            merged += "\n" + nxt
            j += 1
            data = _json_loads_rsc_payload(merged)
            if len(merged) > 400_000:
                break
        next_i = j + 1 if j > i else i + 1
        if isinstance(data, dict):
            yield data
        i = next_i


def parse_hdhive_site_rsc_message(text: str) -> tuple[str, str]:
    """从 text/x-component 响应体中解析 ``N:`` 前缀行的 JSON，得到 (message, description)。

    示例（多行 flight，业务通常在 ``1:`` 行）::

        0:{"a":"$@1","f":"","b":"CxQEnW-PurR3OXrwqibTd","q":"","i":false}
        1:{"error":{"success":false,"message":"签到失败","description":"你已经签到过了，明天再来吧","code":"400"}}
    """
    if not (text or "").strip():
        return "", ""

    for data in _iter_digit_prefixed_json_dicts(text):
        msg, desc = _extract_message_from_rsc_dict(data)
        if msg or desc:
            return msg, desc
        for key in ("result", "data", "actionResult"):
            inner = data.get(key)
            if isinstance(inner, dict):
                msg, desc = _extract_message_from_rsc_dict(inner)
                if msg or desc:
                    return msg, desc

    return "", ""


def format_hdhive_cookie_checkin_display(
    status: int, raw: str, msg: str, desc: str
) -> tuple[str, str]:
    """在无法解析到业务文案时，避免把整段 RSC flight 当作「提醒」展示给用户。"""
    if (msg or desc).strip():
        return msg, desc
    if status > 0 and _looks_like_nextjs_rsc_flight(raw):
        return (
            "签到请求已返回，但正文是 Next.js 页面数据流（RSC），未包含可识别的签到结果。",
            "请确认 HDHIVE_COOKIE（token）仍有效；若站点大改版导致内置 Next 元数据过期，需更新程序版本。",
        )
    if status > 0 and len(raw or "") > 400 and not (msg or desc):
        return (
            f"HTTP {status}，响应体未识别为签到 JSON（长度 {len(raw)} 字符）。",
            "请确认 Cookie 有效；若站点已升级，可能需要更新程序内置的签到元数据。",
        )
    return msg, desc


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
    is_gambler: bool,
    proxy: ProxyConfig | None,
    hdhive_env: dict[str, str] | None = None,
) -> tuple[int, str, str, str, object]:
    """
    执行签到。返回 (http_status, raw_body, display_message, display_description, response_headers)。
    Cookie 模式下 Next-Action / Next-Router-State-Tree 由 ``hdhive_env``（通常为 .env 键值）解析，
    未配置时使用内置默认值；cookie 字符串勿带 ``Cookie:`` 前缀。
    Cookie 模式下 response_headers 用于合并 Set-Cookie 中的 token；否则为空 dict。
    """
    m = normalize_hdhive_checkin_method(method)
    if m == HDHIVE_METHOD_COOKIE:
        next_action, next_router_tree = cookie_checkin_next_meta_from_env(hdhive_env)
        status, raw, hdrs = post_hdhive_checkin_cookie(
            cookie_header,
            next_action,
            next_router_tree,
            is_gambler,
            proxy,
        )
        msg, desc = parse_hdhive_site_rsc_message(raw) if status > 0 else ("", "")
        msg, desc = format_hdhive_cookie_checkin_display(status, raw, msg, desc)
        return status, raw, msg, desc, hdrs

    status, raw = post_hdhive_checkin_api(api_key, is_gambler, proxy)
    msg, desc = parse_api_checkin_message(raw) if status > 0 else ("", "")
    return status, raw, msg, desc, {}


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
    if method == HDHIVE_METHOD_COOKIE:
        if not cookie_header:
            log.warning(
                "HDHive 自动签到未执行：Cookie 模式需填写 HDHIVE_COOKIE（含 token=）并保存。",
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

    status, raw, msg, desc, resp_hdrs = run_hdhive_checkin(
        method=method,
        api_key=api_key,
        cookie_header=cookie_header,
        is_gambler=is_gambler,
        proxy=proxy,
        hdhive_env=values,
    )
    if method == HDHIVE_METHOD_COOKIE and resp_hdrs:
        from tg_forwarder.hdhive_cookie_refresh import persist_hdhive_cookie_from_response_headers

        if persist_hdhive_cookie_from_response_headers(env_path, cookie_header, resp_hdrs, log):
            log.info("HDHive Cookie 已从签到响应 Set-Cookie 更新 token 并写回配置。")
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


def _merge_cli_checkin_proxy(
    values: dict[str, str],
    *,
    use_proxy: bool = False,
    tg_proxy: str | None = None,
    checkin_proxy_url: str | None = None,
) -> dict[str, str]:
    """命令行覆盖：与 .env 合并，用于本地单次测试而不必手改文件。"""
    out = dict(values)
    if use_proxy:
        out["HDHIVE_CHECKIN_USE_PROXY"] = "true"
    if tg_proxy and tg_proxy.strip():
        p = parse_proxy_value(tg_proxy.strip(), "--tg-proxy")
        out["HDHIVE_CHECKIN_USE_PROXY"] = "true"
        out["TG_PROXY_TYPE"] = p.proxy_type
        out["TG_PROXY_HOST"] = p.host
        out["TG_PROXY_PORT"] = str(p.port)
        out["TG_PROXY_USER"] = (p.username or "").strip()
        out["TG_PROXY_PASSWORD"] = p.password if p.password is not None else ""
        out["TG_PROXY_RDNS"] = "true" if p.rdns else "false"
        out.pop("HDHIVE_CHECKIN_PROXY_URL", None)
        out.pop("HDHIVE_CHECKIN_HTTP_PROXY", None)
    if checkin_proxy_url and checkin_proxy_url.strip():
        out["HDHIVE_CHECKIN_USE_PROXY"] = "true"
        out["HDHIVE_CHECKIN_PROXY_URL"] = checkin_proxy_url.strip()
    return out


def _truncate_for_cli(text: str, max_chars: int) -> tuple[str, bool]:
    if max_chars <= 0 or len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def run_hdhive_checkin_once_from_env(
    env_path: Path,
    *,
    use_proxy: bool = False,
    tg_proxy: str | None = None,
    checkin_proxy_url: str | None = None,
    show_raw: bool = False,
    show_raw_max: int = 80_000,
    next_action: str | None = None,
    next_router_state_tree: str | None = None,
) -> int:
    """
    命令行单次签到：读 ``HDHIVE_CHECKIN_METHOD``、Cookie 或 API Key，不读写签到状态文件。
    返回进程退出码：0=HTTP 200，1=HTTP 其它或业务失败，2=配置错误。

    ``use_proxy`` / ``tg_proxy`` / ``checkin_proxy_url`` 仅用于 CLI，合并进配置后再解析代理；
    与 ``resolve_hdhive_proxy`` 一致：未设 ``HDHIVE_CHECKIN_PROXY_URL`` 时使用 ``TG_PROXY_*``。

    ``show_raw`` 为真时向 stdout 打印响应正文（便于调试 RSC / 解析失败）；默认不打印以免刷屏。
    """
    values = _merge_cli_checkin_proxy(
        read_env_file(env_path),
        use_proxy=use_proxy,
        tg_proxy=tg_proxy,
        checkin_proxy_url=checkin_proxy_url,
    )
    if next_action and next_action.strip():
        values["HDHIVE_CHECKIN_NEXT_ACTION"] = next_action.strip()
    if next_router_state_tree and next_router_state_tree.strip():
        values["HDHIVE_CHECKIN_NEXT_ROUTER_STATE_TREE"] = next_router_state_tree.strip()
    method = normalize_hdhive_checkin_method(values.get("HDHIVE_CHECKIN_METHOD"))
    api_key = (values.get("HDHIVE_API_KEY") or "").strip()
    cookie = (values.get("HDHIVE_COOKIE") or "").strip()
    is_gambler = _env_bool(values, "HDHIVE_CHECKIN_GAMBLER")
    proxy, proxy_err = resolve_hdhive_proxy(values)
    if proxy_err:
        print(proxy_err, file=sys.stderr)
        return 2
    if method == HDHIVE_METHOD_COOKIE:
        if not cookie:
            print("Cookie 模式需要 .env 中配置 HDHIVE_COOKIE（含 token=）。", file=sys.stderr)
            return 2
    elif not api_key:
        print("API Key 模式需要 .env 中配置 HDHIVE_API_KEY。", file=sys.stderr)
        return 2

    status, raw, msg, desc, _hdrs = run_hdhive_checkin(
        method=method,
        api_key=api_key,
        cookie_header=cookie,
        is_gambler=is_gambler,
        proxy=proxy,
        hdhive_env=values,
    )
    print(f"HTTP {status}")
    if msg:
        print(f"message: {msg}")
    if desc:
        print(f"description: {desc}")
    raw_len = len(raw or "")
    if show_raw:
        body, cut = _truncate_for_cli(raw or "", show_raw_max)
        print(f"--- response body ({raw_len} chars{'，已截断' if cut else ''}) ---")
        print(body)
        print("--- end response body ---")
    elif status >= 0 and raw_len and method == HDHIVE_METHOD_COOKIE:
        print(f"response_bytes: {raw_len}（加 --show-raw 打印正文）", file=sys.stderr)
    if status < 0:
        print(raw, file=sys.stderr)
        low = (raw or "").lower()
        if "ssl" in low or "eof" in low or "certificate" in low:
            print(
                "提示：可在 .env 设置 HDHIVE_CHECKIN_USE_PROXY=true；若经 SOCKS 仍 SSL EOF，"
                "请增设 HDHIVE_CHECKIN_PROXY_URL 为 HTTP 代理（如 http://127.0.0.1:7890），"
                "仅覆盖签到请求，不必改 TG_PROXY_*。",
                file=sys.stderr,
            )
        return 1
    return 0 if status == 200 else 1


def _cli_main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="HDHive 单次签到（读取 .env：HDHIVE_CHECKIN_METHOD、HDHIVE_COOKIE 或 HDHIVE_API_KEY）",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help=".env 路径（默认 ./.env）",
    )
    parser.add_argument(
        "--use-proxy",
        action="store_true",
        help="本次运行强制开启「签到走代理」（等同 HDHIVE_CHECKIN_USE_PROXY=true，仍使用 .env 里 TG_PROXY_*）",
    )
    parser.add_argument(
        "--tg-proxy",
        metavar="URL",
        default=None,
        help="本次运行覆盖单代理（与控制台「代理设置」相同语义）：如 socks5://127.0.0.1:7893 或 http://127.0.0.1:7890；"
        "会开启签到走代理并写入临时 TG_PROXY_*，且不使用 .env 里的 HDHIVE_CHECKIN_PROXY_URL",
    )
    parser.add_argument(
        "--checkin-proxy-url",
        metavar="URL",
        default=None,
        dest="checkin_proxy_url",
        help="仅本次签到使用的代理 URL（等同 .env 的 HDHIVE_CHECKIN_PROXY_URL）；若与 --tg-proxy 同时指定，以本项为准",
    )
    parser.add_argument(
        "--show-raw",
        action="store_true",
        help="将 HTTP 响应正文打印到 stdout（调试 RSC / 解析不到签到文案时使用；过长时由 --show-raw-max 截断）",
    )
    parser.add_argument(
        "--show-raw-max",
        type=int,
        default=80_000,
        metavar="N",
        help="--show-raw 时最多输出字符数，0 表示不限制（默认 80000）",
    )
    parser.add_argument(
        "--next-action",
        metavar="HASH",
        default=None,
        help="覆盖本次签到的 Next-Action 请求头（与浏览器 Network 里一致；也可写进 .env 的 HDHIVE_CHECKIN_NEXT_ACTION）",
    )
    parser.add_argument(
        "--next-router-state-tree",
        metavar="ENCODED",
        default=None,
        help="覆盖本次签到的 Next-Router-State-Tree（与浏览器一致；也可写 .env 的 HDHIVE_CHECKIN_NEXT_ROUTER_STATE_TREE）",
    )
    args = parser.parse_args()
    if not args.env_file.is_file():
        print(f"找不到文件: {args.env_file.resolve()}", file=sys.stderr)
        return 2
    return run_hdhive_checkin_once_from_env(
        args.env_file,
        use_proxy=args.use_proxy,
        tg_proxy=args.tg_proxy,
        checkin_proxy_url=args.checkin_proxy_url,
        show_raw=args.show_raw,
        show_raw_max=max(0, args.show_raw_max),
        next_action=args.next_action,
        next_router_state_tree=args.next_router_state_tree,
    )


if __name__ == "__main__":
    raise SystemExit(_cli_main())
