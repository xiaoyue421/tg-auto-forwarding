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
import http.client
import json
import os
import re
import time
from typing import Any
import urllib.error
import urllib.request

from telethon.tl.custom.message import Message

from tg_forwarder.config import ProxyConfig
from tg_forwarder.hdhive_checkin import _build_proxy_opener
from tg_forwarder.message_index import extract_message_keyword_values, extract_urls_from_text

NEXT_REDIRECT_RE = re.compile(r"NEXT_REDIRECT;[^;]+;(.+?);307;", re.DOTALL)
# Legacy single-segment /resource/{slug}; newer pan links use /resource/115/{slug}.
RESOURCE_SLUG_RE = re.compile(r"/resource/(?:115/)?([A-Za-z0-9]+)")
# Default site root (no trailing slash); unlock URL is {base}/api/open/resources/unlock — same as MediaSync HDHiveService._build_open_api_url.
_DEFAULT_HDHIVE_OPENAPI_BASE = "https://hdhive.com"
# Embedded JSON on /resource/* HTML + 页面中文提示（与常见 PHP 探针接口 extractPointsRequired 一致）。
# 顺序：优先结构化字段，再中文文案；未匹配返回 None（对应 PHP 未解析到积分时的分支）。
_UNLOCK_POINTS_PATTERNS = (
    re.compile(r'["\']unlock_points["\']\s*:\s*(\d+)', re.IGNORECASE),
    re.compile(r'["\']unlockPoints["\']\s*:\s*(\d+)', re.IGNORECASE),
    re.compile(r'["\']points_required["\']\s*:\s*(\d+)', re.IGNORECASE),
    re.compile(r'需要使用\s*(\d+)\s*积分解锁'),
    re.compile(r'需要\s*(\d+)\s*积分解锁'),
)
_MAX_HTML_SCAN_CHARS = 3_000_000


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


def load_hdhive_resource_page_text_sync(
    url: str,
    *,
    cookie_header: str,
    proxy: ProxyConfig | None,
    timeout_seconds: float = 30.0,
) -> tuple[str, str]:
    """
    GET HDHive /resource/* HTML (no redirect follow). Same headers as resolve.
    Returns (page_text, error_message). page_text empty on failure.
    Cookie may be empty to probe public HTML for unlock_points (best-effort).
    """
    if not url or "hdhive.com" not in url.lower():
        return "", "URL 不合法或非 hdhive.com"
    if "/resource/" not in url.lower():
        return "", "非 HDHive resource 链接（缺少 /resource/）"

    req = urllib.request.Request(url, headers=_request_headers(cookie_header or ""), method="GET")
    opener = _build_proxy_opener(proxy, follow_redirects=False)
    opener.add_handler(_NoRedirectHTTPRedirectHandler())
    last_err = ""
    for attempt in range(3):
        try:
            with opener.open(req, timeout=timeout_seconds) as resp:
                raw = resp.read()
                ce = ""
                if hasattr(resp, "headers"):
                    ce = resp.headers.get("Content-Encoding", "") or ""
                text = _decode_response_body(raw, ce)
                code = int(getattr(resp, "status", None) or resp.getcode() or 0)
                if code != 200:
                    return "", f"HTTP错误: {code}"
                return text, ""
        except urllib.error.HTTPError as exc:
            return "", f"HTTP错误: {exc.code}"
        except http.client.IncompleteRead as exc:
            last_err = f"响应体读取不完整（连接提前关闭）: {exc}"
            if attempt < 2:
                time.sleep(0.4 * (attempt + 1))
                continue
            return "", last_err
        except http.client.HTTPException as exc:
            return "", str(exc)
        except OSError as exc:
            return "", str(exc)

    return "", last_err or "未知网络错误"


def extract_unlock_points_from_hdhive_resource_html(html: str) -> int | None:
    """Parse required unlock points from resource page HTML.

    Aligns with typical PHP probes: ``unlock_points`` / ``points_required`` in JSON,
    or Chinese ``需要使用 N 积分解锁``. Returns ``None`` if unknown (contrast: free
    resource shows NEXT_REDIRECT and never needs this for gating).
    """
    if not (html or "").strip():
        return None
    chunk = html[:_MAX_HTML_SCAN_CHARS]
    for pattern in _UNLOCK_POINTS_PATTERNS:
        match = pattern.search(chunk)
        if match:
            try:
                n = int(match.group(1))
                if n >= 0:
                    return n
            except ValueError:
                return None
    return None


def should_attempt_hdhive_openapi_unlock(
    unlock_points: int | None,
    *,
    max_points_per_item: int,
    threshold_inclusive: bool,
    skip_when_points_unknown: bool,
) -> tuple[bool, str]:
    """
    Mirror MediaSync115 subscription_hdhive_unlock_max_points_per_item (+ inclusive).
    max_points_per_item <= 0: no cap (always attempt).
    Returns (allow, reason) where reason is '' or 'over_threshold' / 'unknown_points'.
    """
    if max_points_per_item <= 0:
        return True, ""
    if unlock_points is None:
        if skip_when_points_unknown:
            return False, "unknown_points"
        return True, ""
    if threshold_inclusive:
        allowed = unlock_points <= max_points_per_item
    else:
        allowed = unlock_points < max_points_per_item
    if not allowed:
        return False, "over_threshold"
    return True, ""


def resolve_hdhive_resource_redirect_sync(
    url: str,
    *,
    cookie_header: str,
    proxy: ProxyConfig | None,
    timeout_seconds: float = 30.0,
) -> tuple[bool, str, str, str]:
    """Return (success, redirect_url, error_message, page_text_on_http_200)."""
    if not cookie_header.strip():
        return False, "", "缺少 HDHIVE_COOKIE", ""

    text, err = load_hdhive_resource_page_text_sync(
        url,
        cookie_header=cookie_header,
        proxy=proxy,
        timeout_seconds=timeout_seconds,
    )
    if err:
        return False, "", err, ""

    redirect = extract_redirect_url(text)
    if not redirect:
        return False, "", "未找到重定向URL", text
    redirect = redirect.replace("\\u0026", "&")
    return True, redirect, "", text


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


def extract_hdhive_resource_slug(url: str) -> str:
    """Return OpenAPI resource slug from an HDHive /resource/* URL.

    Prefer the last path segment after ``/resource/`` so ``/resource/115/{id}`` yields ``{id}``,
    not the literal ``115`` pan prefix.
    """
    text = str(url or "").strip()
    if not text:
        return ""
    low = text.lower()
    marker = "/resource/"
    idx = low.find(marker)
    if idx < 0:
        return ""
    tail = text[idx + len(marker) :].strip()
    tail = tail.split("?", 1)[0].split("#", 1)[0].strip().strip("/")
    if not tail:
        return ""
    parts = [p for p in tail.split("/") if p.strip()]
    if not parts:
        return ""
    candidate = str(parts[-1] or "").strip()
    if not candidate:
        return ""
    normalized = normalize_hdhive_openapi_slug(candidate)
    if normalized:
        return normalized
    match = RESOURCE_SLUG_RE.search(text)
    if not match:
        return ""
    return str(match.group(1) or "").strip()


def normalize_hdhive_openapi_slug(slug: str) -> str:
    """Match MediaSync115 HDHiveService._normalize_slug (alphanumeric only)."""
    return re.sub(r"[^A-Za-z0-9]", "", str(slug or "").strip())


def effective_hdhive_openapi_base_url(values: dict[str, str] | None = None) -> str:
    """Match MediaSync settings.HDHIVE_BASE_URL (strip, no trailing slash)."""
    raw = ""
    if values:
        raw = str(values.get("HDHIVE_BASE_URL") or "").strip()
    if not raw:
        raw = str(os.environ.get("HDHIVE_BASE_URL") or "").strip()
    if not raw:
        raw = f"{_DEFAULT_HDHIVE_OPENAPI_BASE}/"
    return raw.rstrip("/")


def build_hdhive_openapi_unlock_url(openapi_base: str) -> str:
    base = str(openapi_base or "").strip().rstrip("/")
    if not base:
        base = _DEFAULT_HDHIVE_OPENAPI_BASE
    return f"{base}/api/open/resources/unlock"


def unlock_hdhive_resource_via_open_api_sync(
    *,
    slug: str,
    api_key: str,
    proxy: ProxyConfig | None,
    timeout_seconds: float = 30.0,
    openapi_base_url: str | None = None,
) -> tuple[bool, str, str]:
    """
    POST ``{HDHIVE_BASE_URL}/api/open/resources/unlock`` with JSON ``{"slug": "..."}``,
    header ``X-API-Key``（与 PHP curl、站内签到 OpenAPI 一致）。SOCKS5 经 ``_build_proxy_opener``
    且 ``rdns=True`` 时等价于 PHP 的 ``CURLPROXY_SOCKS5_HOSTNAME``。

    Returns (success, share_link, error_message).
    """
    normalized_slug = normalize_hdhive_openapi_slug(slug)
    normalized_api_key = str(api_key or "").strip()
    if not normalized_slug:
        return False, "", "缺少资源 slug"
    if not normalized_api_key:
        return False, "", "缺少 HDHIVE_API_KEY"

    base = (openapi_base_url or "").strip().rstrip("/") or _DEFAULT_HDHIVE_OPENAPI_BASE
    unlock_url = build_hdhive_openapi_unlock_url(base)

    payload = json.dumps({"slug": normalized_slug}, ensure_ascii=False).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-API-Key": normalized_api_key,
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        ),
    }
    req = urllib.request.Request(
        unlock_url,
        data=payload,
        headers=headers,
        method="POST",
    )
    opener = _build_proxy_opener(proxy, follow_redirects=True)
    try:
        with opener.open(req, timeout=timeout_seconds) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            status = int(getattr(resp, "status", None) or resp.getcode() or 0)
    except http.client.HTTPException as exc:
        return False, "", str(exc)
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except OSError:
            body = ""
        return False, "", f"HTTP错误: {exc.code} {body[:220]}"
    except OSError as exc:
        return False, "", str(exc)

    if status < 200 or status >= 300:
        return False, "", f"HTTP错误: {status}"

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return False, "", f"响应非 JSON: {body[:220]}"
    if not isinstance(data, dict):
        return False, "", "响应格式错误"

    if data.get("success") is False:
        msg = str(data.get("message") or "").strip()
        desc = str(data.get("description") or "").strip()
        return False, "", (desc or msg or "OpenAPI 解锁失败")

    payload_data = data.get("data")
    if not isinstance(payload_data, dict):
        payload_data = {}
    share_link = str(payload_data.get("full_url") or payload_data.get("url") or "").strip()
    access_code = str(payload_data.get("access_code") or "").strip()
    if not share_link and str(payload_data.get("url") or "").strip() and access_code:
        base = str(payload_data.get("url") or "").strip()
        joiner = "&" if "?" in base else "?"
        share_link = f"{base}{joiner}password={access_code}"
    if not share_link:
        msg = str(data.get("message") or "").strip()
        return False, "", (msg or "OpenAPI 解锁后未返回可用链接")
    return True, share_link, ""


def preview_hdhive_resource_forward_sync(
    url: str,
    *,
    cookie: str,
    api_key: str,
    unlock_enabled: bool,
    unlock_max_points: int,
    unlock_inclusive: bool,
    unlock_skip_unknown: bool,
    proxy: ProxyConfig | None,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """
    Simulate the forwarder path (Cookie NEXT_REDIRECT first, then unlock policy) without calling
    OpenAPI unlock — avoids spending points. Used by the dashboard “转发路径检测”.
    """
    url_norm = str(url or "").strip()
    low = url_norm.lower()
    lines: list[str] = []

    def _finish(
        *,
        outcome: str,
        summary: str,
        direct_ok: bool = False,
        redirect_url: str = "",
        direct_error: str = "",
        unlock_points: int | None = None,
        page_probe_error: str = "",
        openapi_would_attempt: bool = False,
        openapi_skip_reason: str = "",
    ) -> dict[str, Any]:
        return {
            "valid_url": True,
            "url": url_norm,
            "slug": normalize_hdhive_openapi_slug(extract_hdhive_resource_slug(url_norm)),
            "outcome": outcome,
            "summary": summary,
            "direct": {
                "attempted": bool((cookie or "").strip()),
                "ok": direct_ok,
                "error": direct_error or "",
                "redirect_url": redirect_url or "",
            },
            "unlock_points": unlock_points,
            "unlock_points_known": unlock_points is not None,
            "page_probe_error": page_probe_error or "",
            "openapi_preview": {
                "would_attempt": openapi_would_attempt,
                "skip_reason": openapi_skip_reason or "",
                "note": "预览未调用 OpenAPI 解锁接口，不会消耗积分。",
            },
            "detail_lines": lines,
        }

    if not url_norm or "hdhive.com" not in low or "/resource/" not in low:
        return {
            "valid_url": False,
            "url": url_norm,
            "slug": "",
            "outcome": "invalid_url",
            "summary": "请输入有效的 hdhive.com/resource/… 链接。",
            "direct": {"attempted": False, "ok": False, "error": "", "redirect_url": ""},
            "unlock_points": None,
            "unlock_points_known": False,
            "page_probe_error": "",
            "openapi_preview": {
                "would_attempt": False,
                "skip_reason": "",
                "note": "预览未调用 OpenAPI 解锁接口，不会消耗积分。",
            },
            "detail_lines": [],
        }

    cookie_s = (cookie or "").strip()
    api_s = (api_key or "").strip()
    slug_raw = extract_hdhive_resource_slug(url_norm)
    slug_norm = normalize_hdhive_openapi_slug(slug_raw)

    page_text = ""
    direct_ok = False
    redirect_url = ""
    direct_err = ""

    if cookie_s:
        direct_ok, redirect_url, direct_err, page_text = resolve_hdhive_resource_redirect_sync(
            url_norm,
            cookie_header=cookie_s,
            proxy=proxy,
            timeout_seconds=timeout_seconds,
        )
        if direct_ok and (redirect_url or "").strip():
            lines.append("Cookie 直链：已从页面解析到 NEXT_REDIRECT（免积分）。")
            return _finish(
                outcome="direct",
                summary="将走 Cookie 直连（免积分），转发内容应为解析后的直链。",
                direct_ok=True,
                redirect_url=(redirect_url or "").strip(),
                direct_error="",
                unlock_points=extract_unlock_points_from_hdhive_resource_html(page_text),
                openapi_would_attempt=False,
            )
        lines.append(f"Cookie 直链失败：{direct_err or '未知错误'}。")
    else:
        direct_err = "未配置 Cookie，未尝试 Cookie 直链（可在站点设置保存 HDHIVE_COOKIE 后重测）。"
        lines.append(direct_err)

    if not (page_text or "").strip():
        probe_text, probe_err = load_hdhive_resource_page_text_sync(
            url_norm,
            cookie_header=cookie_s,
            proxy=proxy,
            timeout_seconds=timeout_seconds,
        )
        page_text = probe_text
        if probe_err:
            lines.append(f"抓取资源页：{probe_err}")

    unlock_points = extract_unlock_points_from_hdhive_resource_html(page_text)
    if unlock_points is not None:
        lines.append(f"页面解析到的 unlock_points：{unlock_points}（仅供参考，以实际转发时为准）。")
    elif (page_text or "").strip():
        lines.append("未能从页面 HTML 解析 unlock_points（站点结构变化或需登录可见）。")
    else:
        lines.append("未能获取资源页正文，无法解析所需积分。")

    page_probe_error = ""
    if not (page_text or "").strip() and cookie_s:
        page_probe_error = direct_err or "无法读取资源页"

    openapi_skip_reason = ""
    openapi_would_attempt = False

    if not unlock_enabled:
        openapi_skip_reason = "unlock_disabled"
        lines.append("当前未开启「积分解锁回退」，直链失败时不会调用 OpenAPI。")
        summary = "直链不可用且未开启积分解锁；实际转发会得到固定失败提示（不会发原链接）。"
        return _finish(
            outcome="fail",
            summary=summary,
            direct_ok=False,
            direct_error=direct_err,
            unlock_points=unlock_points,
            page_probe_error=page_probe_error,
            openapi_would_attempt=False,
            openapi_skip_reason=openapi_skip_reason,
        )

    if not api_s:
        openapi_skip_reason = "no_api_key"
        lines.append("已开启积分解锁但未配置 API Key，无法走 OpenAPI。")
        summary = "直链失败且无法积分解锁（缺少 API Key）。"
        return _finish(
            outcome="fail",
            summary=summary,
            direct_ok=False,
            direct_error=direct_err,
            unlock_points=unlock_points,
            page_probe_error=page_probe_error,
            openapi_would_attempt=False,
            openapi_skip_reason=openapi_skip_reason,
        )

    if not slug_norm:
        openapi_skip_reason = "no_slug"
        lines.append("无法从链接提取资源 slug，OpenAPI 解锁无法调用。")
        summary = "直链失败且无法解析资源 slug。"
        return _finish(
            outcome="fail",
            summary=summary,
            direct_ok=False,
            direct_error=direct_err,
            unlock_points=unlock_points,
            page_probe_error=page_probe_error,
            openapi_would_attempt=False,
            openapi_skip_reason=openapi_skip_reason,
        )

    allow_unlock, skip_reason = should_attempt_hdhive_openapi_unlock(
        unlock_points,
        max_points_per_item=max(0, int(unlock_max_points)),
        threshold_inclusive=unlock_inclusive,
        skip_when_points_unknown=unlock_skip_unknown,
    )

    if allow_unlock:
        openapi_would_attempt = True
        openapi_skip_reason = ""
        if unlock_points is None:
            pts_hint = "所需积分未知（当前设置允许在未知时仍尝试解锁）"
        else:
            pts_hint = f"所需积分约为 {unlock_points}"
        lines.append(
            f"按当前上限规则：将尝试 OpenAPI 积分解锁（{pts_hint}）。"
            " 实际转发时先直链失败后才会调用；此处预览不调用接口。"
        )
        summary = (
            f"直链不可用；将回退 OpenAPI 积分解锁（{pts_hint}）。"
            " 请确认规则中已开启「HDHive 专用直链转发」。"
        )
        return _finish(
            outcome="openapi",
            summary=summary,
            direct_ok=False,
            direct_error=direct_err,
            unlock_points=unlock_points,
            page_probe_error=page_probe_error,
            openapi_would_attempt=True,
            openapi_skip_reason="",
        )

    openapi_skip_reason = skip_reason or "blocked"
    if skip_reason == "over_threshold":
        cap = max(0, int(unlock_max_points))
        bound = "≤" if unlock_inclusive else "<"
        lines.append(
            f"积分解锁已按上限跳过：资源约需 {unlock_points} 分，当前上限为 {bound} {cap}（0 表示不限制）。"
        )
        summary = f"直链失败；积分解锁因超过上限被跳过（约需 {unlock_points} 分）。"
    elif skip_reason == "unknown_points":
        lines.append("积分解锁已跳过：无法解析所需积分，且已勾选「未知积分时跳过」。")
        summary = "直链失败；因未知积分且保守策略，不会自动解锁。"
    else:
        lines.append("积分解锁不会执行（策略限制）。")
        summary = "直链失败且不会自动积分解锁。"

    return _finish(
        outcome="fail",
        summary=summary,
        direct_ok=False,
        direct_error=direct_err,
        unlock_points=unlock_points,
        page_probe_error=page_probe_error,
        openapi_would_attempt=False,
        openapi_skip_reason=openapi_skip_reason,
    )

