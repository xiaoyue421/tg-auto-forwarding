"""拉取 telegra.ph 文章 HTML（同步 HTTP，与 hdhive 资源页拉取方式一致）。"""

from __future__ import annotations

import gzip
import http.client
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urlsplit, urlunsplit

def _encode_telegra_request_url(url: str) -> str:
    """Percent-encode non-ASCII path so urllib can open Chinese slugs."""
    parts = urlsplit(url)
    path = parts.path or "/"
    if path == "/":
        return url
    encoded_path = "/" + "/".join(quote(segment, safe="") for segment in path.strip("/").split("/"))
    return urlunsplit((parts.scheme, parts.netloc, encoded_path, parts.query, parts.fragment))


_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)


def _decode_body(data: bytes, content_encoding: str = "") -> str:
    if data.startswith(b"\x1f\x8b") or "gzip" in (content_encoding or "").lower():
        try:
            data = gzip.decompress(data)
        except OSError:
            pass
    return data.decode("utf-8", errors="replace")


def load_telegra_page_html_sync(
    url: str,
    *,
    proxy: object | None = None,
    timeout_seconds: float = 30.0,
    max_retries: int = 3,
) -> tuple[str, str]:
    """
  GET Telegraph 文章页。

  Returns:
      (html, error_message) — html 为空时表示失败。
  """
    from tgph.extract import is_telegra_ph_url, normalize_telegra_ph_url

    page_url = normalize_telegra_ph_url(url)
    if not is_telegra_ph_url(page_url):
        return "", "非 telegra.ph 链接"

    page_url = _encode_telegra_request_url(page_url)

    req = urllib.request.Request(
        page_url,
        headers={
            "User-Agent": _DEFAULT_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        },
        method="GET",
    )
    try:
        from tg_forwarder.hdhive_checkin import _build_proxy_opener

        opener = _build_proxy_opener(proxy, follow_redirects=True)
    except Exception:
        opener = urllib.request.build_opener()

    last_err = ""
    for attempt in range(max(1, max_retries)):
        try:
            with opener.open(req, timeout=timeout_seconds) as resp:
                raw = resp.read()
                ce = resp.headers.get("Content-Encoding", "") if hasattr(resp, "headers") else ""
                text = _decode_body(raw, ce)
                code = int(getattr(resp, "status", None) or resp.getcode() or 0)
                if code != 200:
                    return "", f"HTTP错误: {code}"
                return text, ""
        except urllib.error.HTTPError as exc:
            return "", f"HTTP错误: {exc.code}"
        except http.client.IncompleteRead as exc:
            last_err = f"响应体读取不完整: {exc}"
            if attempt + 1 < max_retries:
                time.sleep(0.4 * (attempt + 1))
                continue
            return "", last_err
        except OSError as exc:
            return "", str(exc)
    return "", last_err or "未知网络错误"
