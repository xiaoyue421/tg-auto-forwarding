"""后台定时刷新 .env 中的 HDHIVE_COOKIE（从 Set-Cookie 合并新 token）。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.error
import urllib.request
from pathlib import Path
from time import sleep
from typing import Any, NamedTuple

from tg_forwarder.env_utils import read_env_file, update_env_file
from tg_forwarder.hdhive_checkin import (
    _build_proxy_opener,
    _env_bool,
    _should_retry_urlopen_os_error,
    resolve_hdhive_proxy,
)

HDHIVE_HOME_URL = "https://hdhive.com/"
DEFAULT_REFRESH_INTERVAL_SEC = 1800
MIN_REFRESH_INTERVAL_SEC = 60
MAX_REFRESH_INTERVAL_SEC = 86400
_FETCH_HOME_MAX_ATTEMPTS = 3


class CookieRefreshResult(NamedTuple):
    """``written`` 为真表示已把新 token 写回 .env；``kind`` 供 API/CLI 区分网络错误与「无新 token」。"""

    written: bool
    kind: str
    message: str = ""


def _network_hint_for_message(msg: str) -> str:
    low = (msg or "").lower()
    if "ssl" in low or "eof" in low:
        return (
            " 若经 SOCKS 仍失败，可在 .env 设置 HDHIVE_CHECKIN_PROXY_URL 为 HTTP 代理（如 http://127.0.0.1:7890），"
            "或与签到 CLI 相同使用 --checkin-proxy-url。"
        )
    return ""


def _quote_env_value(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _env_int_interval(values: dict[str, str], key: str, default: int) -> int:
    raw = (values.get(key) or "").strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return max(MIN_REFRESH_INTERVAL_SEC, min(MAX_REFRESH_INTERVAL_SEC, parsed))


def _token_from_cookie_header(cookie_header: str) -> str | None:
    m = re.search(r"(?i)\btoken=([^;]+)", cookie_header.strip())
    if not m:
        return None
    return m.group(1).strip().strip('"')


def _replace_or_append_token(cookie_header: str, new_token: str) -> str:
    ch = cookie_header.strip()
    if re.search(r"(?i)\btoken=", ch):
        return re.sub(r"(?i)\btoken=[^;]+", f"token={new_token}", ch, count=1)
    sep = "; " if ch else ""
    return f"{ch}{sep}token={new_token}".strip()


def _tokens_from_set_cookie_headers(headers: Any) -> list[str]:
    """从响应头收集 Set-Cookie 中的 token= 值。"""
    raw_list: list[str] = []
    h: Any = getattr(headers, "headers", headers)
    if hasattr(h, "get_all"):
        try:
            raw_list = list(h.get_all("Set-Cookie") or [])
        except (TypeError, AttributeError):
            raw_list = []
    if not raw_list and hasattr(h, "get"):
        one = h.get("Set-Cookie")
        if one:
            raw_list = [one]
    out: list[str] = []
    for raw in raw_list:
        first = raw.split(";", 1)[0].strip()
        if first.lower().startswith("token="):
            val = first[6:].strip().strip('"')
            if val:
                out.append(val)
    return out


def persist_hdhive_cookie_from_response_headers(
    env_path: Path,
    cookie_header: str,
    headers: object,
    log: logging.Logger,
) -> bool:
    """
    若响应 Set-Cookie 中含新的 token=，则合并进当前 Cookie 并写回 .env。
    用于 Cookie 模式签到后刷新 JWT（GET 首页往往不会下发新 token）。
    """
    ch = (cookie_header or "").strip()
    if not ch:
        return False
    old_tok = _token_from_cookie_header(ch)
    if not old_tok:
        return False
    new_tokens = _tokens_from_set_cookie_headers(headers)
    new_tok = new_tokens[-1] if new_tokens else None
    if not new_tok or new_tok == old_tok:
        return False
    merged = _replace_or_append_token(ch, new_tok)
    update_env_file(env_path, {"HDHIVE_COOKIE": _quote_env_value(merged)})
    log.info("HDHive Cookie 已从 HTTP 响应 Set-Cookie 合并新 token 并写回配置。")
    return True


def _fetch_home_with_cookie(
    cookie_header: str,
    proxy,
    *,
    max_attempts: int = _FETCH_HOME_MAX_ATTEMPTS,
) -> tuple[int, object]:
    """GET 首页，返回 (status_code, headers)。proxy 为 ProxyConfig | None。

    对 TLS/连接类 ``OSError`` 做短暂重试（与签到 urllib 逻辑一致）。
    """
    req = urllib.request.Request(
        HDHIVE_HOME_URL,
        method="GET",
        headers={
            "Cookie": cookie_header.strip(),
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Referer": "https://hdhive.com/",
        },
    )
    opener = _build_proxy_opener(proxy, follow_redirects=True)
    last_exc: OSError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            with opener.open(req, timeout=45) as resp:
                code = resp.getcode()
                hdrs = resp.headers
                resp.read()
                return code, hdrs
        except urllib.error.HTTPError as exc:
            try:
                exc.read()
            except OSError:
                pass
            return exc.code, exc.headers if exc.headers else {}
        except OSError as exc:
            last_exc = exc
            if attempt < max_attempts and _should_retry_urlopen_os_error(exc):
                sleep(min(0.7 * attempt, 2.5))
                continue
            raise
    raise last_exc or OSError("unknown fetch home error")


def maybe_refresh_hdhive_cookie(
    env_path: Path,
    log: logging.Logger,
    *,
    force: bool = False,
    env_values: dict[str, str] | None = None,
) -> CookieRefreshResult:
    """
    若开启 HDHIVE_COOKIE_REFRESH_ENABLED 且已配置 HDHIVE_COOKIE，则请求首页并写回新 token。
    force=True 时忽略开关（供控制台「立即刷新」接口使用）。
    使用与签到相同的 ``resolve_hdhive_proxy``；GET 带 TLS/连接重试。

    ``env_values`` 若传入则不再从磁盘读 .env（供 CLI 合并 ``--tg-proxy`` 等临时覆盖）。
    """
    values = env_values if env_values is not None else read_env_file(env_path)
    if not force and not _env_bool(values, "HDHIVE_COOKIE_REFRESH_ENABLED"):
        return CookieRefreshResult(False, "skipped_disabled", "")
    cookie_header = (values.get("HDHIVE_COOKIE") or "").strip()
    if not cookie_header:
        log.debug("HDHive Cookie 自动刷新已开启但未填写 HDHIVE_COOKIE，跳过。")
        return CookieRefreshResult(False, "skipped_no_cookie", "")

    proxy, proxy_err = resolve_hdhive_proxy(values)
    if proxy_err:
        log.warning("HDHive Cookie 自动刷新跳过（代理无效）：%s", proxy_err)
        return CookieRefreshResult(False, "proxy_error", proxy_err)

    old_tok = _token_from_cookie_header(cookie_header)
    if not old_tok:
        log.warning(
            "HDHive Cookie 自动刷新：HDHIVE_COOKIE 中未找到 token= 字段，无法合并刷新；"
            "请从浏览器复制含 token 的 Cookie。",
        )
        return CookieRefreshResult(False, "skipped_bad_cookie", "")

    try:
        code, hdrs = _fetch_home_with_cookie(cookie_header, proxy)
    except OSError as exc:
        err_s = str(exc)
        log.warning("HDHive Cookie 自动刷新请求失败：%s", err_s)
        return CookieRefreshResult(False, "network_error", err_s)
    except Exception:
        log.exception("HDHive Cookie 自动刷新异常")
        return CookieRefreshResult(False, "exception", "内部异常，详见日志。")

    if code >= 400:
        msg = f"GET 首页 HTTP {code}（若持续失败请检查 token 是否过期）"
        log.warning("HDHive Cookie 自动刷新：%s", msg)
        return CookieRefreshResult(False, "http_error", msg)

    new_tokens = _tokens_from_set_cookie_headers(hdrs)
    new_tok = new_tokens[-1] if new_tokens else None
    if new_tok and new_tok != old_tok:
        merged = _replace_or_append_token(cookie_header, new_tok)
        update_env_file(env_path, {"HDHIVE_COOKIE": _quote_env_value(merged)})
        log.info("HDHive Cookie 已自动刷新（token 已更新并写回配置）。")
        return CookieRefreshResult(True, "updated", "")
    log.debug(
        "HDHive Cookie 定时刷新：HTTP %s，响应未含新的 token=（多数站点 GET 首页不会轮换 JWT）。",
        code,
    )
    return CookieRefreshResult(
        False,
        "no_new_token",
        "GET 首页未在 Set-Cookie 中返回与当前不同的 token=（多数站点属正常，可改用签到响应合并 token）。",
    )


async def hdhive_cookie_refresh_loop(stop: asyncio.Event, env_path: Path, log: logging.Logger) -> None:
    """按 HDHIVE_COOKIE_REFRESH_INTERVAL_SEC 周期执行 maybe_refresh_hdhive_cookie。"""
    while not stop.is_set():
        try:
            r = await asyncio.to_thread(maybe_refresh_hdhive_cookie, env_path, log)
            if r.kind == "network_error" and r.message:
                log.warning("HDHive Cookie 定时刷新网络失败：%s", r.message)
        except Exception:
            log.exception("HDHive Cookie 自动刷新任务异常")
        values = read_env_file(env_path)
        interval = _env_int_interval(
            values,
            "HDHIVE_COOKIE_REFRESH_INTERVAL_SEC",
            DEFAULT_REFRESH_INTERVAL_SEC,
        )
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            break
        except asyncio.TimeoutError:
            continue


def _cli_main() -> int:
    """命令行：等同控制台「立即 GET 首页尝试刷新 token」（``POST /api/hdhive/refresh-cookie``）。"""
    import argparse
    import sys

    from tg_forwarder.hdhive_checkin import _merge_cli_checkin_proxy

    parser = argparse.ArgumentParser(
        description=(
            "HDHive：GET 首页，若响应 Set-Cookie 含新 token= 则写回 .env 的 HDHIVE_COOKIE。"
            " 代理规则与签到模块相同（resolve_hdhive_proxy）："
            " 优先 HDHIVE_CHECKIN_PROXY_URL，其次 HDHIVE_CHECKIN_USE_PROXY+TG_PROXY_*；"
            " 亦可传 --tg-proxy / --checkin-proxy-url，与 python -m tg_forwarder.hdhive_checkin 一致。"
        ),
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
        help="强制 HDHIVE_CHECKIN_USE_PROXY=true，使用 .env 中 TG_PROXY_*",
    )
    parser.add_argument("--tg-proxy", metavar="URL", default=None, help="本次覆盖单代理（同签到 CLI）")
    parser.add_argument(
        "--checkin-proxy-url",
        metavar="URL",
        default=None,
        dest="checkin_proxy_url",
        help="本次仅 HDHive 请求使用的代理 URL",
    )
    args = parser.parse_args()
    if not args.env_file.is_file():
        print(f"找不到文件: {args.env_file.resolve()}", file=sys.stderr)
        return 2

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    log = logging.getLogger("tg_forwarder.hdhive")

    values = _merge_cli_checkin_proxy(
        read_env_file(args.env_file),
        use_proxy=args.use_proxy,
        tg_proxy=args.tg_proxy,
        checkin_proxy_url=args.checkin_proxy_url,
    )
    cookie = (values.get("HDHIVE_COOKIE") or "").strip()
    if not cookie:
        print("未配置 HDHIVE_COOKIE。", file=sys.stderr)
        return 2
    if not re.search(r"(?i)\btoken=", cookie):
        print("HDHIVE_COOKIE 中需含 token=。", file=sys.stderr)
        return 2
    proxy, proxy_err = resolve_hdhive_proxy(values)
    if proxy_err:
        print(proxy_err, file=sys.stderr)
        return 2

    result = maybe_refresh_hdhive_cookie(
        args.env_file, log, force=True, env_values=values
    )
    if result.written:
        print("已写回 HDHIVE_COOKIE（GET 首页响应中含新 token）。")
        return 0
    if result.kind == "network_error":
        print(result.message, file=sys.stderr)
        extra = _network_hint_for_message(result.message)
        if extra:
            print(extra.strip(), file=sys.stderr)
        return 1
    if result.kind == "http_error":
        print(result.message, file=sys.stderr)
        return 1
    if result.kind == "proxy_error":
        print(result.message, file=sys.stderr)
        return 2
    if result.message:
        print(result.message, file=sys.stderr)
    else:
        print(
            "未写入：GET 首页未返回新 token=（多数站点属正常）。"
            "可改用 Cookie 模式「测试签到」合并 Set-Cookie。",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli_main())
