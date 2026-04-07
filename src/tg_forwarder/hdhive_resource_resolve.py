"""Resolve HDHive /resource/* pages to share redirect URLs (e.g. 115cdn).

Python port of common PHP curl scripts:
- GET resource URL with Cookie header
- DO NOT follow 3xx redirects (CURLOPT_FOLLOWLOCATION=false)
- optional SOCKS5 proxy via project's ProxyConfig (rdns=True ~= socks5h)
- parse NEXT_REDIRECT segment and decode \\uXXXX (BMP) escapes

Return value is the decoded URL string (often ends with &), caller may strip as needed.
"""

from __future__ import annotations

import gzip
import re
import urllib.error
import urllib.request

from telethon.tl.custom.message import Message

from tg_forwarder.config import ProxyConfig
from tg_forwarder.hdhive_checkin import _build_proxy_opener
from tg_forwarder.message_index import extract_message_keyword_values, extract_urls_from_text

NEXT_REDIRECT_RE = re.compile(r"NEXT_REDIRECT;[^;]+;(.+?);307;", re.DOTALL)


class _NoRedirectHTTPRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _decode_response_body(data: bytes, content_encoding: str = "") -> str:
    # keep consistent with other modules; gzip may appear even if header missing
    if data.startswith(b"\x1f\x8b") or "gzip" in (content_encoding or "").lower():
        try:
            data = gzip.decompress(data)
        except OSError:
            pass
    return data.decode("utf-8", errors="replace")


def decode_unicode_escapes_bmp(text: str) -> str:
    def _u4(match: re.Match[str]) -> str:
        return chr(int(match.group(1), 16))

    return re.sub(r"\\u([0-9a-fA-F]{4})", _u4, text)


def extract_redirect_url(response_text: str) -> str | None:
    m = NEXT_REDIRECT_RE.search(response_text or "")
    if not m:
        return None
    raw = m.group(1)
    decoded = decode_unicode_escapes_bmp(raw)
    decoded = decoded.replace("\\u0026", "&").strip()
    return decoded or None


def _request_headers(cookie_header: str) -> dict[str, str]:
    return {
        "accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,"
            "image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
        ),
        "accept-language": "zh-CN,zh;q=0.9",
        "priority": "u=0, i",
        "sec-ch-ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "none",
        "sec-fetch-user": "?1",
        "upgrade-insecure-requests": "1",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
        ),
        "cookie": cookie_header.strip(),
    }


def resolve_hdhive_resource_redirect_sync(
    url: str,
    *,
    cookie_header: str,
    proxy: ProxyConfig | None,
    timeout_seconds: float = 30.0,
) -> tuple[bool, str, str]:
    """Return (success, redirect_url, error_message)."""
    if not url or "hdhive.com" not in url.lower():
        return False, "", "URL 不合法或非 hdhive.com"
    if "/resource/" not in url.lower():
        return False, "", "非 HDHive resource 链接（缺少 /resource/）"
    if not cookie_header.strip():
        return False, "", "缺少 HDHIVE_COOKIE"

    req = urllib.request.Request(url, headers=_request_headers(cookie_header), method="GET")
    opener = _build_proxy_opener(proxy, follow_redirects=False)
    # ensure no-redirect even when no proxy
    opener.add_handler(_NoRedirectHTTPRedirectHandler())
    try:
        with opener.open(req, timeout=timeout_seconds) as resp:
            raw = resp.read()
            ce = ""
            if hasattr(resp, "headers"):
                ce = resp.headers.get("Content-Encoding", "") or ""
            text = _decode_response_body(raw, ce)
            code = int(getattr(resp, "status", None) or resp.getcode() or 0)
            if code != 200:
                return False, "", f"HTTP错误: {code}"
    except urllib.error.HTTPError as exc:
        return False, "", f"HTTP错误: {exc.code}"
    except OSError as exc:
        return False, "", str(exc)

    redirect = extract_redirect_url(text)
    if not redirect:
        return False, "", "未找到重定向URL"
    # keep PHP behavior
    redirect = redirect.replace("\\u0026", "&")
    return True, redirect, ""


def collect_hdhive_resource_urls_from_message(message: Message, *, max_urls: int = 3) -> list[str]:
    """Extract HDHive /resource URLs from message text + buttons."""
    if max_urls <= 0:
        return []
    seen: set[str] = set()
    ordered: list[str] = []
    for text in extract_message_keyword_values(message):
        for url in extract_urls_from_text(str(text or "")):
            low = url.lower()
            if "hdhive.com" not in low or "/resource/" not in low:
                continue
            if url in seen:
                continue
            seen.add(url)
            ordered.append(url)
            if len(ordered) >= max_urls:
                break
    return ordered

