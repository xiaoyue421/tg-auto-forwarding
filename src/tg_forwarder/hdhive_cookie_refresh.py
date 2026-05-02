"""从 HTTP 响应 Set-Cookie 合并新 ``token=`` 并写回 ``HDHIVE_COOKIE``（用于 Cookie 模式签到等）。

不包含定时 GET 首页或控制台「刷新 Cookie」；换 token 请依赖浏览器复制 Cookie 或签到成功时的 Set-Cookie。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any


def _quote_env_value(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


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
    用于 Cookie 模式签到后刷新 JWT。
    """
    from tg_forwarder.env_utils import update_env_file

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
