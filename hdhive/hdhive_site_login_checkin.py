#!/usr/bin/env python3
"""HDHive：用户名/邮箱 + 密码登录 hdhive.com/login，并可 POST 首页 Server Action 签到。

可被项目内 ``tg_forwarder.hdhive_checkin`` 以 ``run_site_login_checkin(opener, ...)`` 调用；
亦可直接命令行运行（见文末 ``main()``）。

环境变量见 ``main()`` 内 argparse 说明。
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import ssl
import sys
from pathlib import Path
import urllib.request
from dataclasses import dataclass
from http.cookiejar import CookieJar
from urllib.parse import quote

BASE_URL = "https://hdhive.com"
LOGIN_URL = f"{BASE_URL}/login"
CHECKIN_URL = f"{BASE_URL}/"

DEFAULT_CHECKIN_NEXT_ACTION = "40f6fa81b95f6ab53478231d5b36e8ac9b8722d28d"
DEFAULT_CHECKIN_NEXT_ROUTER_STATE_TREE = (
    "%5B%22%22%2C%7B%22children%22%3A%5B%22(app)%22%2C%7B%22children%22%3A%5B%22__PAGE__%22%2C%7B%7D%2Cnull%2Cnull%5D%7D%2Cnull%2Cnull%5D%7D%2Cnull%2Cnull%2Ctrue%5D"
)

_LOGIN_SERVER_ACTION_RE = re.compile(
    r'createServerReference\)\("([a-f0-9]{40,48})"[^)]*,"login"\)',
    re.I,
)
_CHECKIN_SERVER_ACTION_RE = re.compile(
    r'createServerReference\)\("([a-f0-9]{40,48})"[^)]*,"checkIn"\)',
)

# 打包形态变化时：逗号后可能是单引号，或与 "login" 间隔略大。
_LOGIN_SERVER_ACTION_RELAX_RE = re.compile(
    r'createServerReference\)\("([a-f0-9]{40,48})"[^)]*[,]\s*["\']login["\']',
    re.I,
)
_CHECKIN_SERVER_ACTION_RELAX_RE = re.compile(
    r'createServerReference\)\("([a-f0-9]{40,48})"[^)]*[,]\s*["\']checkIn["\']',
    re.I,
)
_CREATE_SERVER_REF_HASH_RE = re.compile(
    r'createServerReference\)\(\s*["\']([a-f0-9]{40,48})["\']',
    re.I,
)


def _login_next_action_from_text(text: str) -> str | None:
    """从 HTML 或 chunk JS 中提取 login 的 Next-Action 哈希（尽量不依赖手写 .env）。"""
    if not text:
        return None
    for rx in (_LOGIN_SERVER_ACTION_RE, _LOGIN_SERVER_ACTION_RELAX_RE):
        m = rx.search(text)
        if m:
            return m.group(1)
    for m in _CREATE_SERVER_REF_HASH_RE.finditer(text):
        h = m.group(1)
        tail = text[m.start() : m.start() + 420]
        if re.search(r'["\']login["\']', tail):
            return h
    return None


def _checkin_next_action_from_text(text: str) -> str | None:
    if not text:
        return None
    for rx in (_CHECKIN_SERVER_ACTION_RE, _CHECKIN_SERVER_ACTION_RELAX_RE):
        m = rx.search(text)
        if m:
            return m.group(1)
    for m in _CREATE_SERVER_REF_HASH_RE.finditer(text):
        h = m.group(1)
        tail = text[m.start() : m.start() + 420]
        if re.search(r'["\']checkIn["\']', tail):
            return h
    return None


def _chunk_js_urls_from_html(html: str) -> list[str]:
    """收集页面引用的 ``/_next/static/chunks/*.js``（兼容查询串）。"""
    if not html:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for rel in re.findall(r"/_next/static/chunks/[^\"\'\\s<>]+\.js(?:\?[^\"\'\\s<>]*)?", html):
        if rel in seen:
            continue
        seen.add(rel)
        out.append(BASE_URL + rel if rel.startswith("/") else f"{BASE_URL}/{rel}")
    return out

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class SiteLoginCheckinResult:
    """供宿主映射为 HTTP 状态与写回 Cookie。"""

    exit_code: int
    http_status: int
    raw: str
    message: str
    description: str
    cookie_header: str


def default_login_router_state_tree() -> str:
    payload = [
        "",
        {
            "children": [
                "(auth)",
                {
                    "children": [
                        "login",
                        {"children": ["__PAGE__", {}, None, None]},
                        None,
                        None,
                    ]
                },
                None,
                None,
            ]
        },
        None,
        None,
        True,
    ]
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return quote(raw, safe="")


def decode_body(raw: bytes, content_encoding: str = "") -> str:
    if raw.startswith(b"\x1f\x8b") or "gzip" in (content_encoding or "").lower():
        try:
            raw = gzip.decompress(raw)
        except OSError:
            pass
    return raw.decode("utf-8", errors="replace")


def _opener_cookiejar(opener: urllib.request.OpenerDirector) -> CookieJar | None:
    for h in opener.handlers:
        if h is None:
            continue
        cj = getattr(h, "cookiejar", None)
        if cj is not None:
            return cj  # type: ignore[return-value]
    return None


def _opener_requests_proxies(opener: urllib.request.OpenerDirector) -> dict[str, str] | None:
    """从 ``ProxyHandler`` 或 ``SocksiPyHandler`` 还原 ``requests`` 的 ``proxies`` 字典。"""
    import socks

    for h in opener.handlers:
        if h is None:
            continue
        proxies = getattr(h, "proxies", None)
        if isinstance(proxies, dict) and proxies:
            pu = (proxies.get("https") or proxies.get("http") or "").strip()
            if pu:
                return {"http": pu, "https": pu}
        if type(h).__name__ != "SocksiPyHandler":
            continue
        args = getattr(h, "args", ())
        kw = getattr(h, "kw", {}) or {}
        if len(args) < 3:
            continue
        ptype, host, port = args[0], args[1], args[2]
        rdns = bool(kw.get("rdns", True))
        user = (kw.get("username") or "").strip()
        pwd = kw.get("password") or ""
        if ptype == socks.PROXY_TYPE_SOCKS5:
            scheme = "socks5h" if rdns else "socks5"
        elif ptype == socks.PROXY_TYPE_SOCKS4:
            scheme = "socks4"
        else:
            continue
        auth = ""
        if user:
            auth = f"{quote(user, safe='')}:{quote(str(pwd), safe='')}@"
        url = f"{scheme}://{auth}{host}:{port}"
        return {"http": url, "https": url}
    return None


def _urlopen_read_requests(opener: urllib.request.OpenerDirector, req: urllib.request.Request, timeout: float) -> tuple[int, str]:
    """用 ``requests`` 发送与 ``urllib.Request`` 等效的 HTTPS 请求（共享 opener 的 CookieJar / 代理）。

    HDHive 脚本**不再**调用 ``opener.open().read()``，避免 ``urllib.response.addbase`` /
    ``TemporaryFileWrapper`` 在部分环境下抛出 ``NoneType`` / ``__dict__`` 等 ``AttributeError``。

    必须使用 **Session + 同一 CookieJar**：单次 ``requests.request(..., cookies=CookieJar)`` 在跟随重定向时
    不会把各跳 ``Set-Cookie`` 写回 ``http.cookiejar.CookieJar``，导致登录后 ``cookie_header_from_opener`` 为空、
    无法写回 ``HDHIVE_COOKIE``。
    """
    try:
        import requests
    except ImportError as e:
        raise OSError(
            "HDHive 网页签到依赖 requests；请重新构建镜像或执行 pip install -e .（pyproject 已声明 requests）。",
        ) from e

    url = req.get_full_url()
    method = req.get_method()
    headers = dict(req.header_items())
    body = req.data
    proxies = _opener_requests_proxies(opener) or {}
    cj = _opener_cookiejar(opener)
    sess_attr = "_hdhive_site_login_requests_session"
    sess = getattr(opener, sess_attr, None)
    if sess is None:
        sess = requests.Session()
        sess.proxies = proxies
        if cj is not None:
            sess.cookies = cj
        setattr(opener, sess_attr, sess)
    try:
        r = sess.request(method, url, headers=headers, data=body, timeout=timeout)
    except Exception as exc:
        raise OSError(f"requests 请求失败：{exc}") from exc
    ce = r.headers.get("Content-Encoding", "") or ""
    return r.status_code, decode_body(r.content, ce)


def urlopen_read(opener: urllib.request.OpenerDirector, req: urllib.request.Request, timeout: float) -> tuple[int, str]:
    """读完正文：一律经 ``requests``（见 ``_urlopen_read_requests``）。"""
    return _urlopen_read_requests(opener, req, timeout)


def build_opener(proxy_url: str | None = None) -> urllib.request.OpenerDirector:
    """仅用 HTTP(S) 代理 URL 构建 opener（无 tg_forwarder 时的回退）。"""
    ctx = ssl.create_default_context()
    jar = CookieJar()
    handlers: list[urllib.request.BaseHandler] = []
    p = (proxy_url or "").strip()
    if p:
        handlers.append(urllib.request.ProxyHandler({"http": p, "https": p}))
    handlers.append(urllib.request.HTTPCookieProcessor(jar))
    handlers.append(urllib.request.HTTPSHandler(context=ctx))
    return urllib.request.build_opener(*handlers)


def build_opener_from_dashboard_env() -> urllib.request.OpenerDirector:
    """与控制台 / ``tg_forwarder.hdhive_checkin`` 一致：读 ``.env`` 中 ``TG_PROXY_*``、``HDHIVE_CHECKIN_*`` 等并构建 opener。

    需在已安装本项目的 Python 环境中运行（可导入 ``tg_forwarder``）；否则回退为仅环境变量
    ``HDHIVE_CHECKIN_PROXY_URL`` / ``HDHIVE_CHECKIN_HTTP_PROXY`` 的 ``build_opener``。
    """
    try:
        from tg_forwarder.env_utils import read_env_file
        from tg_forwarder.hdhive_checkin import _build_hdhive_site_login_opener, resolve_hdhive_proxy
    except ImportError:
        pu = (
            os.environ.get("HDHIVE_CHECKIN_PROXY_URL") or os.environ.get("HDHIVE_CHECKIN_HTTP_PROXY") or ""
        ).strip()
        return build_opener(pu or None)

    script_dir = Path(__file__).resolve().parent
    candidates: list[Path] = [Path.cwd() / ".env"]
    for i in range(0, 8):
        try:
            candidates.append(script_dir.parents[i] / ".env")
        except IndexError:
            break

    seen: set[str] = set()
    for p in candidates:
        try:
            key = str(p.resolve())
        except OSError:
            key = str(p)
        if key in seen or not p.is_file():
            continue
        seen.add(key)
        values = read_env_file(p)
        proxy, err = resolve_hdhive_proxy(values)
        if err:
            print(f"警告（HDHive 代理）：{err}", file=sys.stderr)
        return _build_hdhive_site_login_opener(proxy)

    proxy, err = resolve_hdhive_proxy({})
    if err:
        print(f"警告（HDHive 代理）：{err}", file=sys.stderr)
    return _build_hdhive_site_login_opener(proxy)


def cookie_header_from_opener(opener: urllib.request.OpenerDirector) -> str:
    pairs: dict[str, str] = {}
    for h in opener.handlers:
        if h is None:
            continue
        cj = getattr(h, "cookiejar", None)
        if cj is None:
            continue
        try:
            for c in cj:
                if c is None:
                    continue
                dom = (getattr(c, "domain", None) or "").lstrip(".").lower()
                # 本 opener 仅用于 hdhive.com；空 domain 常见于未写 Domain 的 Set-Cookie，仍应写入会话串。
                if dom and not dom.endswith("hdhive.com"):
                    continue
                name = getattr(c, "name", None)
                if not name:
                    continue
                val = getattr(c, "value", None)
                pairs[str(name)] = "" if val is None else str(val)
        except Exception:
            continue
    return "; ".join(f"{k}={pairs[k]}" for k in sorted(pairs))


def fetch_login_page(opener: urllib.request.OpenerDirector) -> str:
    req = urllib.request.Request(
        LOGIN_URL,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "gzip, deflate",
        },
        method="GET",
    )
    _st, text = urlopen_read(opener, req, 30.0)
    return text


def fetch_home_page(opener: urllib.request.OpenerDirector) -> str:
    req = urllib.request.Request(
        CHECKIN_URL,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Referer": CHECKIN_URL,
        },
        method="GET",
    )
    _st, text = urlopen_read(opener, req, 30.0)
    return text


def discover_login_next_action(opener: urllib.request.OpenerDirector, login_html: str) -> str | None:
    hit = _login_next_action_from_text(login_html)
    if hit:
        return hit
    urls = _chunk_js_urls_from_html(login_html)
    for url in urls:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": UA,
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Accept-Encoding": "gzip, deflate",
                "Referer": LOGIN_URL,
            },
            method="GET",
        )
        try:
            _st, js = urlopen_read(opener, req, 45.0)
        except OSError:
            continue
        except Exception:
            continue
        hit = _login_next_action_from_text(js)
        if hit:
            return hit
    return None


def discover_checkin_next_action(opener: urllib.request.OpenerDirector, home_html: str) -> str | None:
    hit = _checkin_next_action_from_text(home_html)
    if hit:
        return hit
    urls = _chunk_js_urls_from_html(home_html)
    for url in urls:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": UA,
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Accept-Encoding": "gzip, deflate",
                "Referer": CHECKIN_URL,
            },
            method="GET",
        )
        try:
            _st, js = urlopen_read(opener, req, 45.0)
        except OSError:
            continue
        except Exception:
            continue
        hit = _checkin_next_action_from_text(js)
        if hit:
            return hit
    return None


def post_server_action(
    opener: urllib.request.OpenerDirector,
    *,
    next_action: str,
    router_tree: str,
    body: bytes,
) -> tuple[int, str]:
    headers = {
        "Accept": "text/x-component",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Content-Type": "text/plain;charset=UTF-8",
        "Next-Action": next_action.strip(),
        "Next-Router-State-Tree": router_tree.strip(),
        "Origin": BASE_URL,
        "Referer": LOGIN_URL,
        "Next-Url": "/login",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Sec-CH-UA": '"Google Chrome";v="147", "Chromium";v="147", "Not A(Brand";v="24"',
        "Sec-CH-UA-Mobile": "?0",
        "Sec-CH-UA-Platform": '"Windows"',
        "User-Agent": UA,
    }
    req = urllib.request.Request(LOGIN_URL, data=body, headers=headers, method="POST")
    return urlopen_read(opener, req, 45.0)


def post_checkin_server_action(
    opener: urllib.request.OpenerDirector,
    *,
    next_action: str,
    router_tree: str,
    is_gambler: bool,
) -> tuple[int, str]:
    body = b"[true]" if is_gambler else b"[false]"
    headers = {
        "Accept": "text/x-component",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Content-Type": "text/plain;charset=UTF-8",
        "Next-Action": next_action.strip(),
        "Next-Router-State-Tree": router_tree.strip(),
        "Origin": BASE_URL,
        "Referer": CHECKIN_URL,
        "Next-Url": "/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Sec-CH-UA": '"Google Chrome";v="147", "Chromium";v="147", "Not A(Brand";v="24"',
        "Sec-CH-UA-Mobile": "?0",
        "Sec-CH-UA-Platform": '"Windows"',
        "User-Agent": UA,
    }
    req = urllib.request.Request(CHECKIN_URL, data=body, headers=headers, method="POST")
    return urlopen_read(opener, req, 45.0)


def candidate_bodies(email: str, password: str, redirect: str) -> list[tuple[str, bytes]]:
    cred_u = {"username": email, "password": password}
    cred_e = {"email": email, "password": password}
    redir = redirect.strip() or "/"
    return [
        ("tuple-username-password+redirect", json.dumps([cred_u, redir], ensure_ascii=False).encode("utf-8")),
        ("tuple-email-password+redirect", json.dumps([cred_e, redir], ensure_ascii=False).encode("utf-8")),
        ("object-username-password", json.dumps(cred_u, ensure_ascii=False).encode("utf-8")),
        ("object-email-password", json.dumps(cred_e, ensure_ascii=False).encode("utf-8")),
        ("array-email-password", json.dumps([email, password], ensure_ascii=False).encode("utf-8")),
    ]


def server_action_body_indicates_failure(text: str) -> bool:
    if re.search(r'"success"\s*:\s*false', text or ""):
        return True
    if '"error"' in text and "LoginRequest" in text:
        return True
    return False


def _json_loads_rsc_payload(payload: str) -> object | None:
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


def _iter_digit_prefixed_json_dicts(text: str):
    """解析 RSC flight 里 ``数字:`` 前缀行；JSON 跨多行时合并后再 ``json.loads``（与 tg_forwarder.hdhive_checkin 一致）。"""
    lines = (text or "").splitlines()
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


def _success_field_is_false(val: object) -> bool:
    """站点 JSON 里 ``success`` 多为布尔 ``false``；偶见字符串或缺省与其它字段混排。"""
    if val is False:
        return True
    if isinstance(val, str) and val.strip().lower() in {"false", "0", "no"}:
        return True
    if isinstance(val, (int, float)) and val == 0:
        return True
    return False


def _flight_error_dict_candidates(data: dict) -> list[dict]:
    """与 ``tg_forwarder.hdhive_checkin.parse_hdhive_site_rsc_message`` 一致：顶层 ``error`` 及
    ``result`` / ``data`` / ``actionResult`` 内的嵌套 ``error``。"""
    out: list[dict] = []
    err = data.get("error")
    if isinstance(err, dict):
        out.append(err)
    for key in ("result", "data", "actionResult"):
        inner = data.get(key)
        if isinstance(inner, dict):
            err2 = inner.get("error")
            if isinstance(err2, dict):
                out.append(err2)
    return out


def _pick_json_string_field_near(blob: str, field: str) -> str:
    m = re.search(rf'"{re.escape(field)}"\s*:\s*"((?:[^"\\\\]|\\\\.)*)"', blob)
    if not m:
        return ""
    inner = m.group(1)
    try:
        return json.loads(f'"{inner}"')
    except json.JSONDecodeError:
        return inner.replace(r"\"", '"').replace(r"\\", "\\")


def rsc_checkin_error_success_false(text: str) -> tuple[bool, str, str]:
    """若 flight 中出现 ``success: false`` 的业务错误块，返回 (True, message, description)。"""
    blob = text or ""
    for data in _iter_digit_prefixed_json_dicts(blob):
        if not isinstance(data, dict):
            continue
        for err in _flight_error_dict_candidates(data):
            if not _success_field_is_false(err.get("success")):
                continue
            msg = str(err.get("message") or "").strip()
            desc = str(err.get("description") or "").strip()
            return True, msg, desc
    rel = re.search(r'"success"\s*:\s*false', blob, flags=re.I)
    if not rel:
        return False, "", ""
    start = max(0, rel.start() - 600)
    end = min(len(blob), rel.end() + 2000)
    window = blob[start:end]
    msg = _pick_json_string_field_near(window, "message")
    desc = _pick_json_string_field_near(window, "description")
    if msg or desc:
        return True, msg, desc
    return False, "", ""


def checkin_failure_is_benign_already_done(text: str) -> bool:
    biz = rsc_first_business_line(text)
    blob = biz + text
    needles = ("已经签到", "签到过了", "明天再来", "重复签到", "无需重复")
    return any(n in blob for n in needles)


def rsc_first_business_line(text: str) -> str:
    """取首条可读业务文案（兼容多行 JSON 块、嵌套 error）。"""
    for data in _iter_digit_prefixed_json_dicts(text):
        if not isinstance(data, dict):
            continue
        for err in _flight_error_dict_candidates(data):
            desc = str(err.get("description") or "").strip()
            msg = str(err.get("message") or "").strip()
            if desc:
                return desc
            if msg:
                return msg
        for k in ("description", "message"):
            v = data.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def print_cookie_summary(opener: urllib.request.OpenerDirector) -> None:
    print("\n--- CookieJar（节选，不含敏感完整值）---")
    for h in opener.handlers:
        if h is None:
            continue
        cj = getattr(h, "cookiejar", None)
        if cj is None:
            continue
        for cookie in cj:
            if cookie is None:
                continue
            val = getattr(cookie, "value", "") or ""
            masked = (val[:8] + "…" + val[-6:]) if len(val) > 20 else "***"
            print(f"  {getattr(cookie, 'name', '?')}: {masked} domain={getattr(cookie, 'domain', '')} path={getattr(cookie, 'path', '')}")
    try:
        header = cookie_header_from_opener(opener)
    except Exception:
        header = ""
    print("\n--- Cookie 请求头（分号+空格分隔，与写入 HDHIVE_COOKIE 格式一致）---")
    print(header if header else "（无）")


def run_site_login_checkin(
    opener: urllib.request.OpenerDirector,
    *,
    username: str,
    password: str,
    redirect: str = "/",
    do_checkin: bool = True,
    is_gambler: bool = False,
    login_next_action: str = "",
    login_router_tree: str = "",
    checkin_next_action: str = "",
    checkin_router_tree: str = "",
) -> SiteLoginCheckinResult:
    """使用已配置代理与 CookieJar 的 opener 执行登录（及可选签到）。

    ``checkin_next_action`` / ``checkin_router_tree`` 为空时：在**登录成功**后拉取首页，再从 HTML/分块 JS
    解析签到用的 ``Next-Action``；树为空则使用本模块内置的默认 ``Next-Router-State-Tree``。
    非空字符串表示调用方（如 .env 显式覆盖）提供的值，将**优先**于解析结果。
    """
    try:
        ck = cookie_header_from_opener(opener)
    except Exception:
        ck = ""

    email = (username or "").strip()
    if not email:
        return SiteLoginCheckinResult(2, 400, "", "配置错误", "用户名为空", ck)

    router_tree = (login_router_tree or "").strip() or default_login_router_state_tree()

    login_html = fetch_login_page(opener)
    next_action = (login_next_action or "").strip()
    if not next_action:
        next_action = discover_login_next_action(opener, login_html) or ""
    if not next_action:
        return SiteLoginCheckinResult(
            2,
            400,
            "",
            "登录失败",
            "未能解析 login 的 Next-Action，请在 .env 设置 HDHIVE_LOGIN_NEXT_ACTION",
            cookie_header_from_opener(opener),
        )

    last_status = 0
    last_text = ""
    for _label, body in candidate_bodies(email, password, redirect):
        status, text = post_server_action(
            opener,
            next_action=next_action,
            router_tree=router_tree,
            body=body,
        )
        last_status, last_text = status, text
        if status == 404 and "Server action not found" in text:
            continue
        if status >= 400:
            continue
        if server_action_body_indicates_failure(text):
            continue

        ck_ok = cookie_header_from_opener(opener)
        if not do_checkin:
            return SiteLoginCheckinResult(0, 200, text[:8000], "登录成功", "", ck_ok)

        home_html = fetch_home_page(opener)
        # 签到元数据在登录态首页/JS 中；调用方未覆盖时才解析，避免用陈旧内置哈希跳过 discover
        ck_na = (checkin_next_action or "").strip()
        if not ck_na:
            ck_na = discover_checkin_next_action(opener, home_html) or ""
        if not ck_na:
            ck_na = DEFAULT_CHECKIN_NEXT_ACTION
        ck_tree = (checkin_router_tree or "").strip() or DEFAULT_CHECKIN_NEXT_ROUTER_STATE_TREE
        st, ck_text = post_checkin_server_action(
            opener,
            next_action=ck_na,
            router_tree=ck_tree,
            is_gambler=is_gambler,
        )
        ck_final = cookie_header_from_opener(opener)
        biz = rsc_first_business_line(ck_text)
        if st >= 400:
            return SiteLoginCheckinResult(1, st, ck_text[:8000], "签到失败", biz or f"HTTP {st}", ck_final)
        err_hit, emsg, edesc = rsc_checkin_error_success_false(ck_text)
        if err_hit:
            em = (emsg or "签到失败").strip()
            ed = (edesc or "").strip()
            if checkin_failure_is_benign_already_done(ck_text):
                # 业务上视为「今日已处理」：仍用站点返回的 message/description，避免误报「签到完成」
                return SiteLoginCheckinResult(0, 200, ck_text[:8000], em, ed, ck_final)
            return SiteLoginCheckinResult(1, 400, ck_text[:8000], em, ed or biz or "", ck_final)
        # message → 弹窗标题，description → 弹窗正文；无 RSC 附句时正文默认「签到成功」
        return SiteLoginCheckinResult(
            0, 200, ck_text[:8000], "签到完成", (biz or "").strip() or "签到成功", ck_final
        )

    hint = rsc_first_business_line(last_text) or last_text[:500]
    return SiteLoginCheckinResult(
        1,
        last_status if last_status > 0 else 401,
        last_text[:8000],
        "登录失败",
        hint,
        cookie_header_from_opener(opener),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HDHive：账号密码登录并签到（Next Server Action）")
    parser.add_argument("--username", "--email", dest="email", metavar="USER", default="", help="用户名或邮箱")
    parser.add_argument("--password", default="", help="密码")
    parser.add_argument("--next-action", default="", help="登录 Next-Action 覆盖")
    parser.add_argument("--next-router-state-tree", default="", help="登录 Next-Router-State-Tree")
    parser.add_argument("--redirect", default="/", help="登录 redirect")
    parser.add_argument("--no-checkin", dest="checkin", action="store_false")
    parser.add_argument("--gambler", dest="is_gambler", action="store_true")
    parser.add_argument("--checkin-next-action", default="", help="签到 Next-Action")
    parser.add_argument("--checkin-next-router-state-tree", default="", help="签到 Next-Router-State-Tree")
    parser.set_defaults(checkin=True, is_gambler=False)
    args = parser.parse_args(argv)

    email = (args.email or "").strip() or os.environ.get("HDHIVE_LOGIN_USERNAME", "").strip()
    email = email or os.environ.get("HDHIVE_LOGIN_EMAIL", "").strip()
    password = (args.password or "").strip() or os.environ.get("HDHIVE_LOGIN_PASSWORD", "").strip()
    if not email or not password:
        print("错误：请提供 --username / --password 或环境变量 HDHIVE_LOGIN_USERNAME（或 HDHIVE_LOGIN_EMAIL）与 HDHIVE_LOGIN_PASSWORD。", file=sys.stderr)
        return 2

    opener = build_opener_from_dashboard_env()
    print("GET", LOGIN_URL, "…")
    res = run_site_login_checkin(
        opener,
        username=email,
        password=password,
        redirect=(args.redirect or "/").strip() or "/",
        do_checkin=args.checkin,
        is_gambler=args.is_gambler,
        login_next_action=(args.next_action or os.environ.get("HDHIVE_LOGIN_NEXT_ACTION", "")).strip(),
        login_router_tree=(args.next_router_state_tree or os.environ.get("HDHIVE_LOGIN_NEXT_ROUTER_STATE_TREE", "")).strip(),
        checkin_next_action=(args.checkin_next_action or os.environ.get("HDHIVE_CHECKIN_NEXT_ACTION", "")).strip(),
        checkin_router_tree=(
            args.checkin_next_router_state_tree or os.environ.get("HDHIVE_CHECKIN_NEXT_ROUTER_STATE_TREE", "")
        ).strip(),
    )
    print(res.message, res.description, sep=" " if res.description else "")
    if res.raw:
        print("\n--- 响应摘录 ---\n", res.raw[:2000])
    print_cookie_summary(opener)
    return res.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
