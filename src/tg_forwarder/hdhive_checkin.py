"""HDHive (https://hdhive.com/) check-in: Premium API Key 或网页账号密码。

- **API Key 模式**（``HDHIVE_CHECKIN_METHOD`` 非 cookie）：直连 ``/api/open/checkin``。
- **Cookie / 网页账号**：Web、定时与 ``run_hdhive_checkin`` 均通过子进程执行
  ``hdhive/hdhive_site_login_checkin.py``（``run_cookie_checkin_via_site_login_script``），与 CLI 脚本一致。
- **命令行** ``python -m tg_forwarder.hdhive_checkin`` 在 cookie 时同样走该子进程。
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import random
import re
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import date
from http.cookiejar import CookieJar
from pathlib import Path
from time import sleep, time
from urllib.parse import quote

import socks
from sockshandler import SocksiPyHandler

from tg_forwarder.config import ConfigError, ProxyConfig, parse_proxy_from_env, parse_proxy_value
from tg_forwarder.env_utils import read_env_file, update_env_file

CHECKIN_URL_API = "https://hdhive.com/api/open/checkin"
# 轮询间隔：到点后每天最多尝试一次；缩短间隔可更快在「新的一天」触发
CHECKIN_POLL_INTERVAL_SEC = 300
DEFAULT_RETRY_MAX_ATTEMPTS = 4
DEFAULT_RETRY_BASE_DELAY_SEC = 60
DEFAULT_RETRY_MAX_DELAY_SEC = 1800
DEFAULT_RETRY_JITTER_SEC = 15

HDHIVE_METHOD_API_KEY = "api_key"
HDHIVE_METHOD_COOKIE = "cookie"

def cookie_checkin_next_meta_from_env(values: dict[str, str] | None) -> tuple[str, str]:
    """网页签到用的 ``Next-Action`` / ``Next-Router-State-Tree``：仅 .env **显式填写**时作为覆盖。

    未填写时返回 ``("", "")``，由 ``hdhive_site_login_checkin.run_site_login_checkin`` 在**账号密码登录成功后**
    拉取首页 HTML/分块 JS，再 ``discover_checkin_next_action`` 解析（与「先登录再签到」一致）。
    若解析仍失败，脚本内部再回退到 ``hdhive_site_login_checkin`` 内置常量。

    仍兼容旧键名 ``HDHIVE_NEXT_ACTION`` / ``HDHIVE_NEXT_ROUTER_STATE_TREE``。

    注意：若此处填写的哈希已过期，首包可能像「登录页 RSC」；``run_site_login_checkin`` 会检测并重试 discover，
    与直接运行 ``python hdhive/hdhive_site_login_checkin.py``（通常不传覆盖）对齐。长期仍建议删掉过时
    ``HDHIVE_CHECKIN_NEXT_ACTION`` / ``HDHIVE_CHECKIN_NEXT_ROUTER_STATE_TREE``。
    """
    if not values:
        return "", ""

    def pick_existing(*keys: str) -> str:
        for k in keys:
            v = (values.get(k) or "").strip()
            if v:
                return v
        return ""

    return (
        pick_existing("HDHIVE_CHECKIN_NEXT_ACTION", "HDHIVE_NEXT_ACTION"),
        pick_existing("HDHIVE_CHECKIN_NEXT_ROUTER_STATE_TREE", "HDHIVE_NEXT_ROUTER_STATE_TREE"),
    )


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
    """解析 HDHive 出站使用的代理（与 ``hdhive_site_login_checkin`` 中 ``requests`` 一致）。

    规则顺序：

    1. ``HDHIVE_CHECKIN_DIRECT`` 为真 → 强制直连（不使用任何代理）。
    2. ``HDHIVE_CHECKIN_PROXY_URL`` / ``HDHIVE_CHECKIN_HTTP_PROXY`` 非空则优先。
    3. 否则复用控制台「系统与连接」的 ``TG_PROXY_*``（``parse_proxy_from_env``，含进程环境变量）。
    4. 未配置 Telegram 代理则直连（``None``）。

    说明：不再依赖 ``HDHIVE_CHECKIN_USE_PROXY=true`` 才走 ``TG_PROXY_*``；只要填写了主机代理，HDHive 签到默认与其一致。
    若 Telegram 走代理而希望 HDHive 单独直连，请在 ``.env`` 设置 ``HDHIVE_CHECKIN_DIRECT=true``。
    """
    direct_raw = (values.get("HDHIVE_CHECKIN_DIRECT") or os.getenv("HDHIVE_CHECKIN_DIRECT") or "").strip().lower()
    if direct_raw in {"1", "true", "yes", "y", "on"}:
        return None, None

    override = (
        (values.get("HDHIVE_CHECKIN_PROXY_URL") or values.get("HDHIVE_CHECKIN_HTTP_PROXY") or "").strip()
        or (os.getenv("HDHIVE_CHECKIN_PROXY_URL") or os.getenv("HDHIVE_CHECKIN_HTTP_PROXY") or "").strip()
    )
    if override:
        try:
            proxy = parse_proxy_value(override, "HDHIVE_CHECKIN_PROXY_URL")
        except ConfigError as exc:
            return None, str(exc)
        ptype = proxy.proxy_type.strip().lower()
        if ptype == "mtproto":
            return None, "HDHive 签到不支持 MTProto 代理，请改用 SOCKS5 或 HTTP 代理。"
        return proxy, None

    try:
        proxy = parse_proxy_from_env(values)
    except ConfigError as exc:
        return None, str(exc)
    if proxy is None:
        return None, None
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


def _hdhive_site_login_script_path() -> Path:
    """定位 ``hdhive_site_login_checkin.py``。

    1. 环境变量 ``HDHIVE_SITE_LOGIN_SCRIPT``：指向该 ``.py`` 文件的绝对或相对路径（相对当前工作目录）。
    2. 自本包文件所在目录逐级向上（最多 12 层），查找 ``<目录>/hdhive/hdhive_site_login_checkin.py``。
    3. 当前工作目录下的 ``hdhive/hdhive_site_login_checkin.py``。

    Docker 镜像应将仓库 ``hdhive/`` 复制到 ``/workspace/hdhive``（见根目录 ``Dockerfile``）。
    """
    script_name = "hdhive_site_login_checkin.py"
    rel = Path("hdhive") / script_name

    env_raw = (os.environ.get("HDHIVE_SITE_LOGIN_SCRIPT") or "").strip()
    if env_raw:
        env_path = Path(env_raw).expanduser()
        if not env_path.is_absolute():
            env_path = Path.cwd() / env_path
        try:
            env_path = env_path.resolve()
        except OSError:
            pass
        if env_path.is_file():
            return env_path

    here = Path(__file__).resolve()
    max_up = min(len(here.parents), 12)
    for i in range(max_up):
        cand = here.parents[i] / rel
        if cand.is_file():
            return cand

    cwd_cand = Path.cwd() / rel
    if cwd_cand.is_file():
        return cwd_cand

    if len(here.parents) > 2:
        return here.parents[2] / rel
    return cwd_cand


def _build_hdhive_site_login_opener(proxy: ProxyConfig | None) -> urllib.request.OpenerDirector:
    """与签到代理一致，附带 CookieJar（网页登录 + 签到共用会话）。"""
    handlers: list[urllib.request.BaseHandler] = []
    if proxy is not None:
        ptype = proxy.proxy_type.strip().lower()
        if ptype in {"http", "https"}:
            proxy_url = _urllib_proxy_url(proxy)
            handlers.append(urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
        elif ptype in {"socks5", "socks4"}:
            sock_type = socks.SOCKS5 if ptype == "socks5" else socks.SOCKS4
            handlers.append(
                SocksiPyHandler(
                    sock_type,
                    proxy.host,
                    proxy.port,
                    rdns=proxy.rdns,
                    username=proxy.username or None,
                    password=proxy.password or None,
                )
            )
        else:
            raise ValueError(f"不支持的代理类型：{proxy.proxy_type}")
    jar = CookieJar()
    handlers.append(urllib.request.HTTPCookieProcessor(jar))
    ctx = ssl.create_default_context()
    handlers.append(urllib.request.HTTPSHandler(context=ctx))
    return urllib.request.build_opener(*handlers)


def hdhive_site_login_script_present() -> bool:
    """供健康检查等判断仓库内 ``hdhive/hdhive_site_login_checkin.py`` 是否可用。"""
    return _hdhive_site_login_script_path().is_file()


def _persist_hdhive_cookie_after_site_login(env_path: Path, cookie_header: str, log: logging.Logger) -> None:
    ch = (cookie_header or "").strip()
    if not ch:
        return
    try:
        update_env_file(env_path, {"HDHIVE_COOKIE": json.dumps(ch, ensure_ascii=False)})
        log.info("HDHive 已将网页登录会话 Cookie 写回 HDHIVE_COOKIE。")
    except OSError:
        log.warning("HDHive 写回 HDHIVE_COOKIE 失败。", exc_info=True)


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
        elif merged and re.search(r'"success"\s*:\s*false', merged, flags=re.I):
            em = _pick_rsc_quoted_string_field(merged, "message").strip()
            ed = _pick_rsc_quoted_string_field(merged, "description").strip()
            if em or ed:
                yield {"error": {"success": False, "message": em, "description": ed}}
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


def _pick_rsc_quoted_string_field(blob: str, field: str) -> str:
    """在 RSC 片段中按 ``"field":"..."`` 取字符串（用于 flight 非标准、结构化解析未命中时的回退）。"""
    m = re.search(rf'"{re.escape(field)}"\s*:\s*"((?:[^"\\\\]|\\\\.)*)"', blob)
    if not m:
        return ""
    inner = m.group(1)
    try:
        return str(json.loads(f'"{inner}"'))
    except json.JSONDecodeError:
        return inner.replace(r"\"", '"').replace(r"\\", "\\")


def _parse_hdhive_site_rsc_message_loose_window(raw: str) -> tuple[str, str]:
    """在正文里首次 ``"success":false`` 附近截取窗口，再抠 ``message`` / ``description``。"""
    blob = raw or ""
    rel = re.search(r'"success"\s*:\s*false', blob, flags=re.I)
    if not rel:
        return "", ""
    start = max(0, rel.start() - 600)
    end = min(len(blob), rel.end() + 2000)
    window = blob[start:end]
    return _pick_rsc_quoted_string_field(window, "message").strip(), _pick_rsc_quoted_string_field(
        window, "description"
    ).strip()


def format_hdhive_cookie_checkin_display(
    status: int, raw: str, msg: str, desc: str
) -> tuple[str, str]:
    """在无法解析到业务文案时，避免把整段 RSC flight 当作「提醒」展示给用户。"""
    if (msg or desc).strip():
        return msg, desc
    if status > 0 and _looks_like_nextjs_rsc_flight(raw):
        return (
            "签到请求已返回，但正文是 Next.js 页面数据流（RSC），未包含可识别的签到结果。",
            "可检查账号密码与代理；若登录后仍无法从首页解析签到元数据，再在 .env 显式填写 "
            "HDHIVE_CHECKIN_NEXT_ACTION / HDHIVE_CHECKIN_NEXT_ROUTER_STATE_TREE（F12 与浏览器一致）；"
            "或改用 API Key 签到。",
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


def _refine_cookie_checkin_message_fields(raw_w: str, msg0: str, desc0: str) -> tuple[str, str]:
    """与 ``run_hdhive_checkin`` cookie 分支内 RSC 纠正逻辑一致（供 in-process 与子进程 JSON 结果共用）。"""
    msg0 = (msg0 or "").strip()
    desc0 = (desc0 or "").strip()
    pm, pd = parse_hdhive_site_rsc_message(raw_w)
    if raw_w and re.search(r'"success"\s*:\s*false', raw_w) and not (pm or pd):
        pm, pd = _parse_hdhive_site_rsc_message_loose_window(raw_w)
    if raw_w and re.search(r'"success"\s*:\s*false', raw_w) and (pm or pd):
        msg0 = pm or msg0
        desc0 = pd or desc0
    elif (pm or pd) and msg0 == "签到完成" and (not desc0 or desc0 == "签到成功"):
        msg0 = pm or msg0
        desc0 = pd or desc0
    blob = raw_w or ""
    wrong_generic = msg0 == "签到完成" and (not desc0 or desc0 == "签到成功")
    if wrong_generic and (
        re.search(r'"success"\s*:\s*false', blob)
        or any(
            n in blob
            for n in ("已经签到", "签到过了", "明天再来", "重复签到", "无需重复", "签到失败")
        )
    ):
        pm3, pd3 = parse_hdhive_site_rsc_message(raw_w)
        if not (pm3 or pd3):
            pm3, pd3 = _parse_hdhive_site_rsc_message_loose_window(raw_w)
        if pm3 or pd3:
            msg0 = pm3 or msg0
            desc0 = pd3 or desc0
    return msg0, desc0


def run_cookie_checkin_via_site_login_script(
    env: dict[str, str],
    *,
    is_gambler: bool,
) -> tuple[int, str, str, str, object, str, bool]:
    """控制台「测试签到 / 立即签到」cookie 路径：子进程执行 ``hdhive_site_login_checkin.py --emit-json-result``。

    返回元组与 ``run_hdhive_checkin`` 的 cookie 模式相同，便于 Web 层复用 ``_enrich_*`` / 写回 Cookie。
    """
    user = (env.get("HDHIVE_LOGIN_USERNAME") or env.get("HDHIVE_LOGIN_EMAIL") or "").strip()
    pwd = (env.get("HDHIVE_LOGIN_PASSWORD") or "").strip()
    if not user or not pwd:
        return (
            400,
            "",
            "配置不完整",
            "请填写 HDHIVE_LOGIN_USERNAME（或 HDHIVE_LOGIN_EMAIL）与 HDHIVE_LOGIN_PASSWORD。",
            {},
            "",
            False,
        )
    path = _hdhive_site_login_script_path()
    if not path.is_file():
        detail = f"未找到网页登录脚本：{path}"
        return -2, detail, "模块缺失", detail, {}, "", False
    cmd: list[str] = [sys.executable, str(path), "--username", user, "--password", pwd, "--emit-json-result"]
    if is_gambler:
        cmd.append("--gambler")
    child_env = os.environ.copy()
    for k, v in env.items():
        if v is None:
            continue
        child_env[str(k)] = str(v)
    child_env["HDHIVE_LOGIN_USERNAME"] = user
    child_env["HDHIVE_LOGIN_PASSWORD"] = pwd
    try:
        proc = subprocess.run(
            cmd,
            env=child_env,
            cwd=str(Path.cwd()),
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return -2, "", "执行超时", "hdhive_site_login_checkin 子进程超时", {}, "", False
    except OSError as exc:
        return -2, "", "无法启动子进程", str(exc), {}, "", False
    out = (proc.stdout or "").strip()
    if not out:
        err = (proc.stderr or "").strip()
        return -2, err or "(empty stdout)", "子进程无输出", err or "stdout 为空", {}, "", False
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        merged = (out + "\n" + (proc.stderr or "")).strip()[:8000]
        return -2, merged, "解析失败", "子进程 stdout 非合法 JSON", {}, "", False
    raw_w = str(data.get("raw") or "")
    http_status = int(data.get("http_status") or 0)
    exit_code = int(data.get("exit_code") if data.get("exit_code") is not None else 1)
    ck = str(data.get("cookie_header") or "")
    msg0 = str(data.get("message") or "").strip()
    desc0 = str(data.get("description") or "").strip()
    msg0, desc0 = _refine_cookie_checkin_message_fields(raw_w, msg0, desc0)
    desc_for_fmt = desc0
    if exit_code != 0 and not (desc_for_fmt or "").strip():
        desc_for_fmt = raw_w[:1200] if raw_w else ""
    msg, desc = format_hdhive_cookie_checkin_display(http_status, raw_w, msg0, desc_for_fmt)
    ok = exit_code == 0
    return http_status, raw_w, msg, desc, {}, ck, ok


def run_hdhive_checkin(
    *,
    method: str,
    api_key: str,
    cookie_header: str,
    is_gambler: bool,
    proxy: ProxyConfig | None,
    hdhive_env: dict[str, str] | None = None,
) -> tuple[int, str, str, str, object, str, bool]:
    """
    执行签到。返回 ``(http_status, raw_body, message, description, response_headers, new_cookie_header, ok)``。

    ``cookie`` 模式：子进程执行 ``hdhive/hdhive_site_login_checkin.py --emit-json-result``（见
    ``run_cookie_checkin_via_site_login_script``）；第六项为登录后会话 ``Cookie`` 整串；第七项 ``ok`` 为脚本
    ``exit_code == 0``（与 HTTP 状态可能不一致时以该项为准）。

    API Key 模式第五、六项分别为 ``{}``、``""``；第七项为 ``http_status == 200``。
    """
    m = normalize_hdhive_checkin_method(method)
    if m == HDHIVE_METHOD_COOKIE:
        env = hdhive_env or {}
        user = (env.get("HDHIVE_LOGIN_USERNAME") or env.get("HDHIVE_LOGIN_EMAIL") or "").strip()
        pwd = (env.get("HDHIVE_LOGIN_PASSWORD") or "").strip()
        if not user or not pwd:
            return (
                400,
                "",
                "配置不完整",
                "请填写 HDHIVE_LOGIN_USERNAME（或 HDHIVE_LOGIN_EMAIL）与 HDHIVE_LOGIN_PASSWORD。",
                {},
                "",
                False,
            )
        return run_cookie_checkin_via_site_login_script(env, is_gambler=is_gambler)

    status, raw = post_hdhive_checkin_api(api_key, is_gambler, proxy)
    msg, desc = parse_api_checkin_message(raw) if status > 0 else ("", "")
    return status, raw, msg, desc, {}, "", status == 200


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
    if method == HDHIVE_METHOD_COOKIE:
        login_u = (values.get("HDHIVE_LOGIN_USERNAME") or values.get("HDHIVE_LOGIN_EMAIL") or "").strip()
        login_p = (values.get("HDHIVE_LOGIN_PASSWORD") or "").strip()
        if not login_u or not login_p:
            log.warning(
                "HDHive 自动签到未执行：非 Premium 模式需填写 HDHIVE_LOGIN_USERNAME（或 HDHIVE_LOGIN_EMAIL）"
                "与 HDHIVE_LOGIN_PASSWORD（通过 hdhive_site_login_checkin 登录后签到）。",
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

    status, raw, msg, desc, resp_hdrs, new_site_cookie, ok = run_hdhive_checkin(
        method=method,
        api_key=api_key,
        cookie_header="",
        is_gambler=is_gambler,
        proxy=proxy,
        hdhive_env=values,
    )
    if method == HDHIVE_METHOD_COOKIE and new_site_cookie.strip():
        _persist_hdhive_cookie_after_site_login(env_path, new_site_cookie, log)
    preview = (raw[:800] if raw else "") or f"{msg}: {desc}".strip()
    if msg or desc:
        line = "HDHive 自动签到 | HTTP %s | message=%s | description=%s"
        args = (status, (msg or "-")[:500], (desc or "-")[:500])
        if ok:
            log.info(line, *args)
        else:
            log.warning(line, *args)
    elif ok:
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

    if ok:
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
        out["HDHIVE_CHECKIN_DIRECT"] = "false"
    if tg_proxy and tg_proxy.strip():
        p = parse_proxy_value(tg_proxy.strip(), "--tg-proxy")
        out["HDHIVE_CHECKIN_USE_PROXY"] = "true"
        out["HDHIVE_CHECKIN_DIRECT"] = "false"
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
        out["HDHIVE_CHECKIN_DIRECT"] = "false"
        out["HDHIVE_CHECKIN_PROXY_URL"] = checkin_proxy_url.strip()
    return out


def _truncate_for_cli(text: str, max_chars: int) -> tuple[str, bool]:
    if max_chars <= 0 or len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def _run_site_login_checkin_subprocess(values: dict[str, str], *, is_gambler: bool) -> int:
    """命令行 cookie 模式：子进程执行 ``hdhive_site_login_checkin.py``，与手动 ``python hdhive/...`` 输出一致。"""
    path = _hdhive_site_login_script_path()
    if not path.is_file():
        print(f"未找到网页登录脚本: {path}", file=sys.stderr)
        return 2
    login_u = (values.get("HDHIVE_LOGIN_USERNAME") or values.get("HDHIVE_LOGIN_EMAIL") or "").strip()
    login_p = (values.get("HDHIVE_LOGIN_PASSWORD") or "").strip()
    if not login_u or not login_p:
        print(
            "非 Premium 模式需要 HDHIVE_LOGIN_USERNAME（或 HDHIVE_LOGIN_EMAIL）与 HDHIVE_LOGIN_PASSWORD。",
            file=sys.stderr,
        )
        return 2
    cmd: list[str] = [sys.executable, str(path), "--username", login_u, "--password", login_p]
    if is_gambler:
        cmd.append("--gambler")
    child_env = os.environ.copy()
    for k, v in values.items():
        if v is None:
            continue
        child_env[str(k)] = str(v)
    child_env["HDHIVE_LOGIN_USERNAME"] = login_u
    child_env["HDHIVE_LOGIN_PASSWORD"] = login_p
    try:
        proc = subprocess.run(
            cmd,
            env=child_env,
            cwd=str(Path.cwd()),
            check=False,
        )
    except OSError as exc:
        print(f"无法启动子进程 {path}: {exc}", file=sys.stderr)
        return 2
    rc = int(proc.returncode if proc.returncode is not None else 1)
    if rc in (0, 1, 2):
        return rc
    return 1 if rc != 0 else 0


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
    cli_login_username: str = "",
    cli_login_password: str = "",
) -> int:
    """
    命令行单次签到：读 ``HDHIVE_CHECKIN_METHOD``、网页账号或 API Key，不读写签到状态文件。
    返回进程退出码：0=签到业务成功（``run_hdhive_checkin`` 的 ``ok``），1=失败，2=配置错误。

    ``use_proxy`` / ``tg_proxy`` / ``checkin_proxy_url`` 仅用于 CLI，合并进配置后再解析代理；
    与 ``resolve_hdhive_proxy`` 一致：未设 ``HDHIVE_CHECKIN_PROXY_URL`` 时使用 ``TG_PROXY_*``。

    **Cookie 模式**：直接 ``subprocess`` 执行 ``hdhive_site_login_checkin.py``，stdout/stderr/退出码与子进程一致；
    ``--show-raw`` 对子进程不生效（子脚本无该参数），需要全文时请直接运行 hdhive 脚本。

    ``cli_login_username`` / ``cli_login_password`` 非空时覆盖 ``.env`` 中对应项；**两者均传时强制**
    ``HDHIVE_CHECKIN_METHOD=cookie``，便于与 ``python hdhive/hdhive_site_login_checkin.py --username … --password …`` 对照。
    若 ``env_path`` 文件不存在，仅当两者均传时使用空配置（代理等依赖进程环境变量）。
    """
    env_p = Path(env_path)
    u_cli = (cli_login_username or "").strip()
    p_cli = (cli_login_password or "").strip()
    if env_p.is_file():
        base = read_env_file(env_p)
    else:
        if not (u_cli and p_cli):
            print(
                f"找不到 .env: {env_p.resolve()}。请创建该文件，或与 hdhive_site_login_checkin.py 一样同时指定 "
                "`--username` 与 `--password`（可不经 .env）。",
                file=sys.stderr,
            )
            return 2
        base = {}
    values = _merge_cli_checkin_proxy(
        base,
        use_proxy=use_proxy,
        tg_proxy=tg_proxy,
        checkin_proxy_url=checkin_proxy_url,
    )
    if u_cli:
        values["HDHIVE_LOGIN_USERNAME"] = u_cli
    if p_cli:
        values["HDHIVE_LOGIN_PASSWORD"] = p_cli
    if u_cli and p_cli:
        values["HDHIVE_CHECKIN_METHOD"] = "cookie"
    if next_action and next_action.strip():
        values["HDHIVE_CHECKIN_NEXT_ACTION"] = next_action.strip()
    if next_router_state_tree and next_router_state_tree.strip():
        values["HDHIVE_CHECKIN_NEXT_ROUTER_STATE_TREE"] = next_router_state_tree.strip()
    method = normalize_hdhive_checkin_method(values.get("HDHIVE_CHECKIN_METHOD"))
    api_key = (values.get("HDHIVE_API_KEY") or "").strip()
    is_gambler = _env_bool(values, "HDHIVE_CHECKIN_GAMBLER")
    proxy, proxy_err = resolve_hdhive_proxy(values)
    if proxy_err:
        print(proxy_err, file=sys.stderr)
        return 2
    if method == HDHIVE_METHOD_COOKIE:
        login_u = (values.get("HDHIVE_LOGIN_USERNAME") or values.get("HDHIVE_LOGIN_EMAIL") or "").strip()
        login_p = (values.get("HDHIVE_LOGIN_PASSWORD") or "").strip()
        if not login_u or not login_p:
            print(
                "非 Premium 模式需要 .env 中配置 HDHIVE_LOGIN_USERNAME（或 HDHIVE_LOGIN_EMAIL）与 HDHIVE_LOGIN_PASSWORD。",
                file=sys.stderr,
            )
            return 2
        if show_raw:
            print(
                "提示：cookie 模式由子进程执行 hdhive_site_login_checkin.py，输出与 ``python hdhive/hdhive_site_login_checkin.py`` 一致；"
                "本入口的 --show-raw 不作用于子进程。",
                file=sys.stderr,
            )
        return _run_site_login_checkin_subprocess(values, is_gambler=is_gambler)
    elif not api_key:
        print("API Key 模式需要 .env 中配置 HDHIVE_API_KEY。", file=sys.stderr)
        return 2

    status, raw, msg, desc, _hdrs, _new_ck, ok = run_hdhive_checkin(
        method=method,
        api_key=api_key,
        cookie_header="",
        is_gambler=is_gambler,
        proxy=proxy,
        hdhive_env=values,
    )
    raw_len = len(raw or "")
    print(f"HTTP {status}")
    line = " ".join(x for x in ((msg or "").strip(), (desc or "").strip()) if x).strip()
    if line:
        print(line)
    if show_raw and raw_len:
        body, cut = _truncate_for_cli(raw or "", show_raw_max)
        print(f"--- response body ({raw_len} chars{'，已截断' if cut else ''}) ---")
        print(body)
        print("--- end response body ---")
    elif status >= 0 and raw_len:
        print(f"response_bytes: {raw_len}（加 --show-raw 打印正文）", file=sys.stderr)
    if status < 0:
        print(raw, file=sys.stderr)
        low = (raw or "").lower()
        if "ssl" in low or "eof" in low or "certificate" in low:
            print(
                "提示：若已配置 TG_PROXY_*，HDHive 默认与其共用出口；若经 SOCKS 仍 SSL EOF，"
                "请增设 HDHIVE_CHECKIN_PROXY_URL 为 HTTP 代理（如 http://127.0.0.1:7890），"
                "仅覆盖签到请求，不必改 TG_PROXY_*。",
                file=sys.stderr,
            )
        return 1
    return 0 if ok else 1


def _cli_main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "HDHive 单次签到（读取 .env：HDHIVE_CHECKIN_METHOD、HDHIVE_LOGIN_* 或 HDHIVE_API_KEY）。"
            "cookie 模式直接子进程执行 ``hdhive/hdhive_site_login_checkin.py``（输出与手动运行该脚本一致）；"
            "``--username`` + ``--password`` 强制 cookie 并覆盖 .env 账号。"
        ),
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help=".env 路径（默认 ./.env）；若文件不存在，须同时传 --username 与 --password",
    )
    parser.add_argument(
        "--username",
        "--email",
        "--login-username",
        dest="cli_login_username",
        default="",
        metavar="USER",
        help="网页签到用户名或邮箱（覆盖 .env；与 --password 均传时强制 cookie，对齐 hdhive_site_login_checkin.py）",
    )
    parser.add_argument(
        "--password",
        "--login-password",
        dest="cli_login_password",
        default="",
        metavar="PASS",
        help="网页签到密码（覆盖 .env）",
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
        help=(
            "在「响应摘录」之后再打印更长 response body（cookie 模式便于与 hdhive 脚本全文对比；"
            "Premium 模式仅打印该段）。过长时由 --show-raw-max 截断。"
        ),
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
    return run_hdhive_checkin_once_from_env(
        args.env_file,
        use_proxy=args.use_proxy,
        tg_proxy=args.tg_proxy,
        checkin_proxy_url=args.checkin_proxy_url,
        show_raw=args.show_raw,
        show_raw_max=max(0, args.show_raw_max),
        next_action=args.next_action,
        next_router_state_tree=args.next_router_state_tree,
        cli_login_username=getattr(args, "cli_login_username", "") or "",
        cli_login_password=getattr(args, "cli_login_password", "") or "",
    )


if __name__ == "__main__":
    raise SystemExit(_cli_main())
